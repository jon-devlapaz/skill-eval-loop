package simpleeval

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"time"

	"github.com/jon-devlapaz/skill-eval-loop/internal/skillpayload"
)

var safeTaskID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)

type SuiteResult struct {
	Valid         bool                `json:"valid"`
	Mode          string              `json:"mode"`
	OutputDir     string              `json:"output_dir"`
	Configuration DryRunConfiguration `json:"configuration"`
	Counts        DryRunCounts        `json:"counts"`
	Pairs         []SuitePair         `json:"pairs"`
}

type SuitePair struct {
	TaskID         string   `json:"task_id"`
	Trial          int      `json:"trial"`
	RunnerValid    bool     `json:"runner_valid"`
	ExecutionOrder []string `json:"execution_order"`
	ReportJSON     string   `json:"report_json"`
	ReportMarkdown string   `json:"report_markdown"`
}

func RunSuite(ctx context.Context, plan DryRunPlan) (SuiteResult, error) {
	if !plan.Valid || plan.Configuration.Harness != "codex" {
		return SuiteResult{}, fmt.Errorf("valid codex dry-run plan is required")
	}
	tasks, err := LoadTasks(plan.Configuration.TasksPath)
	if err != nil {
		return SuiteResult{}, err
	}
	for _, task := range tasks {
		if !safeTaskID.MatchString(task.ID) {
			return SuiteResult{}, fmt.Errorf("task %q field id: must be path-safe", task.ID)
		}
		for _, grader := range task.Graders {
			if grader.Type == "rubric" {
				return SuiteResult{}, fmt.Errorf("task %q: rubric graders are not supported by live minimum runs yet", task.ID)
			}
		}
	}
	if hash, err := hashFile(plan.Configuration.TasksPath); err != nil || hash != plan.Configuration.TasksSHA256 {
		return SuiteResult{}, fmt.Errorf("tasks changed after dry-run planning")
	}
	if hash, err := skillpayload.Hash(plan.Configuration.SkillPath); err != nil || hash != plan.Configuration.SkillSHA256 {
		return SuiteResult{}, fmt.Errorf("skill changed after dry-run planning")
	}
	if _, err := os.Stat(plan.Configuration.OutputDir); err == nil {
		return SuiteResult{}, fmt.Errorf("output directory already exists: %s", plan.Configuration.OutputDir)
	} else if !os.IsNotExist(err) {
		return SuiteResult{}, err
	}
	if err := os.MkdirAll(plan.Configuration.OutputDir, 0o755); err != nil {
		return SuiteResult{}, err
	}
	if err := writeSuiteSnapshots(plan); err != nil {
		return SuiteResult{}, err
	}

	result := SuiteResult{
		Valid: true, Mode: "live", OutputDir: plan.Configuration.OutputDir,
		Configuration: plan.Configuration,
		Counts:        plan.Counts,
		Pairs:         make([]SuitePair, 0, len(tasks)*plan.Configuration.Trials),
	}
	for _, task := range tasks {
		for trial := 1; trial <= plan.Configuration.Trials; trial++ {
			pairDir := filepath.Join(plan.Configuration.OutputDir, "task-"+task.ID, fmt.Sprintf("trial-%03d", trial))
			pair, err := RunPair(ctx, PairInput{
				Task: task, Trial: trial, SkillPath: plan.Configuration.SkillPath,
				OutputDir: pairDir, Executable: plan.Configuration.HarnessExecutable,
				Model: plan.Configuration.Model, Timeout: time.Duration(plan.Configuration.TimeoutSeconds) * time.Second,
			})
			if err != nil {
				return SuiteResult{}, err
			}
			runnerValid := pairConditionValid(pair.Control) && pairConditionValid(pair.Treatment) && pair.ControlSkillAbsent && pair.TreatmentSkillPresent && pair.TreatmentHashMatches
			if !runnerValid {
				result.Valid = false
			}
			result.Pairs = append(result.Pairs, SuitePair{
				TaskID: task.ID, Trial: trial, RunnerValid: runnerValid,
				ExecutionOrder: append([]string(nil), pair.ExecutionOrder...),
				ReportJSON:     relativePath(plan.Configuration.OutputDir, pair.ReportJSONPath),
				ReportMarkdown: relativePath(plan.Configuration.OutputDir, pair.ReportMarkdownPath),
			})
		}
	}
	data, err := SuiteBytes(result)
	if err != nil {
		return SuiteResult{}, err
	}
	if err := os.WriteFile(filepath.Join(plan.Configuration.OutputDir, "run.json"), data, 0o644); err != nil {
		return SuiteResult{}, err
	}
	return result, nil
}

func SuiteBytes(result SuiteResult) ([]byte, error) {
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(data, '\n'), nil
}

func writeSuiteSnapshots(plan DryRunPlan) error {
	config := struct {
		Mode          string              `json:"mode"`
		Configuration DryRunConfiguration `json:"configuration"`
		Counts        DryRunCounts        `json:"counts"`
	}{Mode: "live", Configuration: plan.Configuration, Counts: plan.Counts}
	data, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(plan.Configuration.OutputDir, "config.json"), append(data, '\n'), 0o644); err != nil {
		return err
	}
	tasks, err := os.ReadFile(plan.Configuration.TasksPath)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(plan.Configuration.OutputDir, "tasks.jsonl"), tasks, 0o644)
}

func pairConditionValid(condition ConditionResult) bool {
	return condition.ExitCode == 0 && !condition.TimedOut && condition.modelRequirementSatisfied()
}

func relativePath(root, path string) string {
	relative, _ := filepath.Rel(root, path)
	return filepath.ToSlash(relative)
}
