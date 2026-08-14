package simpleeval

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
)

type DeterministicGrade struct {
	Status         DeterministicStatus
	AllPassed      bool
	PendingRubrics int
	Results        []GraderResult
}

type DeterministicStatus string

const (
	DeterministicNotScored DeterministicStatus = "not_scored"
	DeterministicPass      DeterministicStatus = "pass"
	DeterministicFail      DeterministicStatus = "fail"
)

type GraderResult struct {
	Type     string
	Passed   bool
	Evidence string
}

func GradeDeterministic(task Task, workspace, response string) (DeterministicGrade, error) {
	report := DeterministicGrade{Status: DeterministicNotScored}
	for _, grader := range task.Graders {
		if grader.Type == "rubric" {
			report.PendingRubrics++
			continue
		}
		result, err := gradeOne(workspace, response, grader)
		if err != nil {
			return DeterministicGrade{}, fmt.Errorf("task %q grader %s: %w", task.ID, grader.Type, err)
		}
		report.Results = append(report.Results, result)
		if len(report.Results) == 1 {
			report.Status = DeterministicPass
			report.AllPassed = true
		}
		if !result.Passed {
			report.Status = DeterministicFail
			report.AllPassed = false
		}
	}
	return report, nil
}

func gradeOne(workspace, response string, grader Grader) (GraderResult, error) {
	result := GraderResult{Type: grader.Type}
	switch grader.Type {
	case "regex", "not_regex":
		pattern, err := regexp.Compile(grader.Pattern)
		if err != nil {
			return GraderResult{}, fmt.Errorf("invalid pattern: %w", err)
		}
		match := pattern.FindString(response)
		if grader.Type == "regex" {
			result.Passed = match != ""
			if result.Passed {
				result.Evidence = fmt.Sprintf("response matched %q", match)
			} else {
				result.Evidence = fmt.Sprintf("response did not match pattern %q", grader.Pattern)
			}
		} else {
			result.Passed = match == ""
			if result.Passed {
				result.Evidence = fmt.Sprintf("response did not match forbidden pattern %q", grader.Pattern)
			} else {
				result.Evidence = fmt.Sprintf("response matched forbidden text %q", match)
			}
		}
	case "file_exists":
		target, err := pathInside(workspace, grader.Path)
		if err != nil {
			return GraderResult{}, err
		}
		info, err := os.Stat(target)
		result.Passed = err == nil && info.Mode().IsRegular()
		if result.Passed {
			result.Evidence = fmt.Sprintf("%s exists as a regular file", grader.Path)
		} else if errors.Is(err, os.ErrNotExist) {
			result.Evidence = fmt.Sprintf("%s is absent", grader.Path)
		} else if err != nil {
			result.Evidence = fmt.Sprintf("%s could not be inspected: %v", grader.Path, err)
		} else {
			result.Evidence = fmt.Sprintf("%s exists but is not a regular file", grader.Path)
		}
	case "json_equal":
		target, err := pathInside(workspace, grader.Path)
		if err != nil {
			return GraderResult{}, err
		}
		observed, err := readJSONValue(target)
		if err != nil {
			result.Evidence = fmt.Sprintf("%s could not be read as JSON: %v", grader.Path, err)
			return result, nil
		}
		result.Passed = reflect.DeepEqual(observed, grader.Expected)
		if result.Passed {
			result.Evidence = fmt.Sprintf("%s equals expected JSON", grader.Path)
		} else {
			encoded, _ := json.Marshal(observed)
			result.Evidence = fmt.Sprintf("%s differs; observed=%s", grader.Path, encoded)
		}
	default:
		return GraderResult{}, fmt.Errorf("unsupported deterministic grader %q", grader.Type)
	}
	return result, nil
}

func readJSONValue(path string) (any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		if err != nil {
			return nil, err
		}
		return nil, fmt.Errorf("multiple JSON values")
	}
	return value, nil
}

func pathInside(root, relative string) (string, error) {
	if strings.TrimSpace(relative) == "" || filepath.IsAbs(relative) {
		return "", fmt.Errorf("path must be non-empty and relative")
	}
	rootPath, err := filepath.Abs(root)
	if err != nil {
		return "", err
	}
	rootPath, err = filepath.EvalSymlinks(rootPath)
	if err != nil {
		return "", fmt.Errorf("resolve workspace: %w", err)
	}
	target := filepath.Join(rootPath, relative)
	resolved, err := resolveExistingPrefix(target)
	if err != nil {
		return "", err
	}
	within, err := filepath.Rel(rootPath, resolved)
	if err != nil || within == ".." || strings.HasPrefix(within, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("path %q escapes the trial workspace", relative)
	}
	return resolved, nil
}

func resolveExistingPrefix(path string) (string, error) {
	var missing []string
	current := path
	for {
		_, err := os.Lstat(current)
		if err == nil {
			resolved, err := filepath.EvalSymlinks(current)
			if err != nil {
				return "", err
			}
			for index := len(missing) - 1; index >= 0; index-- {
				resolved = filepath.Join(resolved, missing[index])
			}
			return resolved, nil
		}
		if !errors.Is(err, os.ErrNotExist) {
			return "", err
		}
		parent := filepath.Dir(current)
		if parent == current {
			return "", err
		}
		missing = append(missing, filepath.Base(current))
		current = parent
	}
}
