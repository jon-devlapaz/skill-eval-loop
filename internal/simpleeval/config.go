package simpleeval

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/jon-devlapaz/skill-eval-loop/internal/skillpayload"
)

type DryRunInput struct {
	SkillPath      string
	TasksPath      string
	Harness        string
	HarnessBin     string
	Model          string
	JudgeModel     string
	Trials         int
	TimeoutSeconds int
	OutputDir      string
}

type DryRunPlan struct {
	Valid            bool                `json:"valid"`
	Mode             string              `json:"mode"`
	CreatedArtifacts bool                `json:"created_artifacts"`
	ProviderCalls    int                 `json:"provider_calls"`
	Configuration    DryRunConfiguration `json:"configuration"`
	Counts           DryRunCounts        `json:"counts"`
	Usage            DryRunUsage         `json:"usage"`
}

type DryRunConfiguration struct {
	SkillPath         string `json:"skill_path"`
	SkillSHA256       string `json:"skill_sha256"`
	TasksPath         string `json:"tasks_path"`
	TasksSHA256       string `json:"tasks_sha256"`
	Harness           string `json:"harness"`
	HarnessExecutable string `json:"harness_executable"`
	HarnessVersion    string `json:"harness_version"`
	Model             string `json:"model"`
	JudgeModel        string `json:"judge_model"`
	Trials            int    `json:"trials"`
	TimeoutSeconds    int    `json:"timeout_seconds"`
	OutputDir         string `json:"output_dir"`
	Execution         string `json:"execution"`
	ConditionOrder    string `json:"condition_order"`
	ToolPosture       string `json:"tool_posture"`
}

type DryRunCounts struct {
	TaskCount         int `json:"task_count"`
	PairedTrials      int `json:"paired_trials"`
	TargetInvocations int `json:"target_invocations"`
	RubricGraderCount int `json:"rubric_grader_count"`
	JudgeInvocations  int `json:"judge_invocations"`
	TotalInvocations  int `json:"total_invocations"`
}

type DryRunUsage struct {
	Tokens *int     `json:"tokens"`
	Cost   *float64 `json:"cost"`
	Status string   `json:"status"`
}

func BuildDryRun(input DryRunInput) (DryRunPlan, error) {
	if input.Harness != "codex" {
		return DryRunPlan{}, fmt.Errorf("harness must be codex")
	}
	if strings.TrimSpace(input.Model) == "" || input.Trials < 1 || input.TimeoutSeconds < 1 {
		return DryRunPlan{}, fmt.Errorf("model, positive trials, and positive timeout-seconds are required")
	}
	for name, path := range map[string]string{"skill": input.SkillPath, "tasks": input.TasksPath, "output": input.OutputDir} {
		if path == "" || !filepath.IsAbs(path) {
			return DryRunPlan{}, fmt.Errorf("%s path must be absolute", name)
		}
	}
	if info, err := os.Stat(filepath.Join(input.SkillPath, "SKILL.md")); err != nil || !info.Mode().IsRegular() {
		return DryRunPlan{}, fmt.Errorf("skill path must contain SKILL.md")
	}
	tasks, err := LoadTasks(input.TasksPath)
	if err != nil {
		return DryRunPlan{}, err
	}
	rubrics := 0
	for _, task := range tasks {
		for _, grader := range task.Graders {
			if grader.Type == "rubric" {
				rubrics++
			}
		}
	}
	if rubrics > 0 && strings.TrimSpace(input.JudgeModel) == "" {
		return DryRunPlan{}, fmt.Errorf("judge-model is required when rubric graders are present")
	}
	executable := input.HarnessBin
	if executable == "" {
		executable = "codex"
	}
	resolved, err := exec.LookPath(executable)
	if err != nil {
		return DryRunPlan{}, fmt.Errorf("codex executable not found: %s", executable)
	}
	versionOutput, err := exec.Command(resolved, "--version").Output()
	if err != nil {
		return DryRunPlan{}, fmt.Errorf("read codex version: %w", err)
	}
	version := strings.TrimSpace(string(versionOutput))
	if version == "" {
		return DryRunPlan{}, fmt.Errorf("codex returned an empty version")
	}
	skillHash, err := skillpayload.Hash(input.SkillPath)
	if err != nil {
		return DryRunPlan{}, err
	}
	tasksHash, err := hashFile(input.TasksPath)
	if err != nil {
		return DryRunPlan{}, err
	}
	paired := len(tasks) * input.Trials
	targets := paired * 2
	judges := rubrics * input.Trials * 2
	return DryRunPlan{
		Valid: true, Mode: "dry_run", CreatedArtifacts: false, ProviderCalls: 0,
		Configuration: DryRunConfiguration{
			SkillPath: input.SkillPath, SkillSHA256: skillHash, TasksPath: input.TasksPath, TasksSHA256: tasksHash,
			Harness: "codex", HarnessExecutable: resolved, HarnessVersion: version, Model: input.Model,
			JudgeModel: input.JudgeModel, Trials: input.Trials, TimeoutSeconds: input.TimeoutSeconds, OutputDir: input.OutputDir,
			Execution: "sequential", ConditionOrder: "alternating_control_first", ToolPosture: "read_only",
		},
		Counts: DryRunCounts{
			TaskCount: len(tasks), PairedTrials: paired, TargetInvocations: targets,
			RubricGraderCount: rubrics, JudgeInvocations: judges, TotalInvocations: targets + judges,
		},
		Usage: DryRunUsage{Status: "unknown_until_live_run"},
	}, nil
}

func DryRunBytes(plan DryRunPlan) ([]byte, error) {
	data, err := json.MarshalIndent(plan, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(data, '\n'), nil
}

func hashFile(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:]), nil
}
