package simpleeval

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

const maxTaskBytes = 4 * 1024 * 1024

type Task struct {
	ID      string
	Prompt  string
	Graders []Grader
	Raw     json.RawMessage
}

type Grader struct {
	Type     string
	Pattern  string
	Text     string
	Path     string
	Expected any
	Raw      json.RawMessage
}

func LoadTasks(path string) ([]Task, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	var tasks []Task
	seen := map[string]bool{}
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64*1024), maxTaskBytes)
	for line := 1; scanner.Scan(); line++ {
		raw := bytes.TrimSpace(scanner.Bytes())
		if len(raw) == 0 {
			continue
		}
		task, err := parseTask(raw, line)
		if err != nil {
			return nil, err
		}
		if seen[task.ID] {
			return nil, fmt.Errorf("task %q field id: duplicate value", task.ID)
		}
		seen[task.ID] = true
		tasks = append(tasks, task)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read tasks: %w", err)
	}
	if len(tasks) == 0 {
		return nil, fmt.Errorf("tasks: at least one task is required")
	}
	return tasks, nil
}

func parseTask(data []byte, line int) (Task, error) {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(data, &object); err != nil {
		return Task{}, fmt.Errorf("line %d: invalid JSON: %w", line, err)
	}
	label := fmt.Sprintf("line %d", line)
	id, err := requiredString(object, "id", label)
	if err != nil {
		return Task{}, err
	}
	label = fmt.Sprintf("task %q", id)
	prompt, err := requiredString(object, "prompt", label)
	if err != nil {
		return Task{}, err
	}

	var rawGraders []json.RawMessage
	if err := json.Unmarshal(object["graders"], &rawGraders); err != nil || len(rawGraders) == 0 {
		return Task{}, fmt.Errorf("%s field graders: must be a non-empty array", label)
	}
	graders := make([]Grader, 0, len(rawGraders))
	for index, raw := range rawGraders {
		grader, err := parseGrader(raw, fmt.Sprintf("%s field graders[%d]", label, index))
		if err != nil {
			return Task{}, err
		}
		graders = append(graders, grader)
	}
	return Task{ID: id, Prompt: prompt, Graders: graders, Raw: append(json.RawMessage(nil), data...)}, nil
}

func parseGrader(data []byte, label string) (Grader, error) {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(data, &object); err != nil {
		return Grader{}, fmt.Errorf("%s: must be an object", label)
	}
	typeName, err := requiredString(object, "type", label)
	if err != nil {
		return Grader{}, err
	}
	grader := Grader{Type: typeName, Raw: append(json.RawMessage(nil), data...)}
	switch typeName {
	case "regex", "not_regex":
		grader.Pattern, err = requiredString(object, "pattern", label)
		if err == nil {
			_, err = regexp.Compile(grader.Pattern)
			if err != nil {
				err = fmt.Errorf("%s field pattern: invalid regular expression: %w", label, err)
			}
		}
	case "file_exists":
		grader.Path, err = requiredRelativePath(object, label)
	case "json_equal":
		grader.Path, err = requiredRelativePath(object, label)
		if err == nil {
			rawExpected, exists := object["expected"]
			if !exists {
				err = fmt.Errorf("%s field expected: is required", label)
			} else if decodeErr := json.Unmarshal(rawExpected, &grader.Expected); decodeErr != nil {
				err = fmt.Errorf("%s field expected: invalid JSON: %w", label, decodeErr)
			}
		}
	case "rubric":
		grader.Text, err = requiredString(object, "text", label)
	default:
		err = fmt.Errorf("%s field type: unsupported value %q", label, typeName)
	}
	if err != nil {
		return Grader{}, err
	}
	return grader, nil
}

func requiredString(object map[string]json.RawMessage, field, label string) (string, error) {
	var value string
	raw, exists := object[field]
	if !exists || json.Unmarshal(raw, &value) != nil || strings.TrimSpace(value) == "" {
		return "", fmt.Errorf("%s field %s: must be a non-empty string", label, field)
	}
	return value, nil
}

func requiredRelativePath(object map[string]json.RawMessage, label string) (string, error) {
	value, err := requiredString(object, "path", label)
	if err != nil {
		return "", err
	}
	if filepath.IsAbs(value) {
		return "", fmt.Errorf("%s field path: must be relative", label)
	}
	for _, part := range strings.FieldsFunc(value, func(current rune) bool { return current == '/' || current == '\\' }) {
		if part == ".." {
			return "", fmt.Errorf("%s field path: must stay inside the trial workspace", label)
		}
	}
	return value, nil
}
