package simpleeval

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type codexInput struct {
	ConditionDir string
	Workspace    string
	CodexHome    string
	Executable   string
	Model        string
	Prompt       string
	SkillName    string
	Timeout      time.Duration
	Environment  map[string]string
}

type ConditionResult struct {
	Condition      string
	Response       string
	ResponsePath   string
	TracePath      string
	StderrPath     string
	ExitCode       int
	TimedOut       bool
	Duration       time.Duration
	RequestedModel string
	ActualModel    string
	ModelAttested  bool
	SessionID      string
	InputTokens    *int
	OutputTokens   *int
	TotalTokens    *int
	SkillPath      string
	SkillSHA256    string
	Grade          DeterministicGrade
}

func (result ConditionResult) modelRequirementSatisfied() bool {
	if result.RequestedModel == "" {
		return false
	}
	return result.ActualModel == "" || result.ModelAttested
}

func runCodex(ctx context.Context, input codexInput) (ConditionResult, error) {
	if input.Timeout <= 0 {
		return ConditionResult{}, fmt.Errorf("timeout must be positive")
	}
	if !filepath.IsAbs(input.CodexHome) {
		return ConditionResult{}, fmt.Errorf("codex home must be an absolute path")
	}
	if err := os.MkdirAll(input.ConditionDir, 0o755); err != nil {
		return ConditionResult{}, err
	}
	for _, path := range []string{input.Workspace, filepath.Join(input.ConditionDir, "home")} {
		if err := os.MkdirAll(path, 0o755); err != nil {
			return ConditionResult{}, err
		}
	}

	tracePath := filepath.Join(input.ConditionDir, "trace.jsonl")
	stderrPath := filepath.Join(input.ConditionDir, "stderr.txt")
	trace, err := os.Create(tracePath)
	if err != nil {
		return ConditionResult{}, err
	}
	stderr, err := os.Create(stderrPath)
	if err != nil {
		trace.Close()
		return ConditionResult{}, err
	}

	runContext, cancel := context.WithTimeout(ctx, input.Timeout)
	defer cancel()
	arguments := []string{
		"exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
		"--ignore-rules", "--sandbox", "read-only", "--model", input.Model,
		input.Prompt,
	}
	command := exec.CommandContext(runContext, input.Executable, arguments...)
	command.Dir = input.Workspace
	command.Env = environmentWith(input.Environment, map[string]string{
		"HOME":                  filepath.Join(input.ConditionDir, "home"),
		"CODEX_HOME":            input.CodexHome,
		"SKILL_EVAL_SKILL_NAME": input.SkillName,
	})
	command.Stdout = trace
	command.Stderr = stderr
	started := time.Now()
	runErr := command.Run()
	duration := time.Since(started)
	closeErr := errors.Join(trace.Close(), stderr.Close())
	if closeErr != nil {
		return ConditionResult{}, closeErr
	}

	result := ConditionResult{
		TracePath: tracePath, StderrPath: stderrPath, Duration: duration,
		RequestedModel: input.Model, ExitCode: exitCode(runErr),
		TimedOut: errors.Is(runContext.Err(), context.DeadlineExceeded),
	}
	if runErr != nil {
		var exitErr *exec.ExitError
		if !errors.As(runErr, &exitErr) && !result.TimedOut {
			return ConditionResult{}, runErr
		}
	}
	if err := applyCodexTrace(tracePath, &result); err != nil {
		return ConditionResult{}, err
	}
	result.ModelAttested = result.ActualModel == result.RequestedModel && result.ActualModel != ""
	responsePath := filepath.Join(input.ConditionDir, "response.md")
	if err := os.WriteFile(responsePath, []byte(result.Response), 0o644); err != nil {
		return ConditionResult{}, err
	}
	result.ResponsePath = responsePath
	return result, nil
}

func applyCodexTrace(path string, result *ConditionResult) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var event map[string]any
		if json.Unmarshal(scanner.Bytes(), &event) != nil {
			continue
		}
		if event["type"] == "system" && event["subtype"] == "init" {
			result.ActualModel, _ = event["model"].(string)
		}
		if event["type"] == "thread.started" {
			result.SessionID, _ = event["thread_id"].(string)
		}
		if event["type"] == "item.completed" {
			item, _ := event["item"].(map[string]any)
			if item["type"] == "agent_message" {
				result.Response = strings.TrimSpace(stringValue(item["text"]))
			}
		}
		if event["type"] == "turn.completed" {
			usage, _ := event["usage"].(map[string]any)
			result.InputTokens = integerPointer(usage["input_tokens"])
			result.OutputTokens = integerPointer(usage["output_tokens"])
			if result.InputTokens != nil && result.OutputTokens != nil {
				total := *result.InputTokens + *result.OutputTokens
				result.TotalTokens = &total
			}
		}
	}
	return scanner.Err()
}

func environmentWith(base, overrides map[string]string) []string {
	values := append([]string(nil), os.Environ()...)
	merged := make(map[string]string, len(base)+len(overrides))
	for key, value := range base {
		merged[key] = value
	}
	for key, value := range overrides {
		merged[key] = value
	}
	for key, value := range merged {
		prefix := key + "="
		found := false
		for index, current := range values {
			if strings.HasPrefix(current, prefix) {
				values[index] = prefix + value
				found = true
				break
			}
		}
		if !found {
			values = append(values, prefix+value)
		}
	}
	return values
}

func integerPointer(value any) *int {
	number, ok := value.(float64)
	if !ok || number < 0 || number != float64(int(number)) {
		return nil
	}
	integer := int(number)
	return &integer
}

func stringValue(value any) string {
	text, _ := value.(string)
	return text
}

func exitCode(err error) int {
	if err == nil {
		return 0
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		return exitErr.ExitCode()
	}
	return -1
}
