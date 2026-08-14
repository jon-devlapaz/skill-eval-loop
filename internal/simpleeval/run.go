package simpleeval

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/jon-devlapaz/skill-eval-loop/internal/skillpayload"
)

type PairInput struct {
	Task        Task
	Trial       int
	SkillPath   string
	FixturePath string
	OutputDir   string
	CodexHome   string
	Executable  string
	Model       string
	Timeout     time.Duration
	Environment map[string]string
}

type PairResult struct {
	TaskID                string
	Task                  Task
	Trial                 int
	SkillName             string
	SkillSHA256           string
	ToolPosture           string
	ControlSkillAbsent    bool
	TreatmentSkillPresent bool
	TreatmentHashMatches  bool
	ExecutionOrder        []string
	Control               ConditionResult
	Treatment             ConditionResult
	ReportJSONPath        string
	ReportMarkdownPath    string
}

func RunPair(ctx context.Context, input PairInput) (PairResult, error) {
	if input.Task.ID == "" || input.Task.Prompt == "" || len(input.Task.Graders) == 0 {
		return PairResult{}, fmt.Errorf("task must have id, prompt, and graders")
	}
	if input.Trial < 1 || input.Executable == "" || input.Model == "" || input.Timeout <= 0 {
		return PairResult{}, fmt.Errorf("trial, executable, model, and timeout are required")
	}
	if _, err := os.Stat(input.OutputDir); err == nil {
		return PairResult{}, fmt.Errorf("output directory already exists: %s", input.OutputDir)
	} else if !os.IsNotExist(err) {
		return PairResult{}, err
	}
	skillPath, err := filepath.Abs(input.SkillPath)
	if err != nil {
		return PairResult{}, err
	}
	if info, err := os.Stat(filepath.Join(skillPath, "SKILL.md")); err != nil || !info.Mode().IsRegular() {
		return PairResult{}, fmt.Errorf("skill path must contain SKILL.md")
	}
	skillName := filepath.Base(skillPath)
	codexHome, err := resolveCodexHome(input.CodexHome)
	if err != nil {
		return PairResult{}, err
	}
	globalSkill := filepath.Join(codexHome, "skills", skillName)
	if _, err := os.Lstat(globalSkill); err == nil {
		return PairResult{}, fmt.Errorf("target skill already exists in authenticated Codex home: %s", globalSkill)
	} else if !os.IsNotExist(err) {
		return PairResult{}, err
	}
	skillHash, err := skillpayload.Hash(skillPath)
	if err != nil {
		return PairResult{}, err
	}

	conditionResults := map[string]ConditionResult{}
	controlSkillAbsent := false
	treatmentSkillPresent := false
	treatmentHashMatches := false
	conditions := []string{"control", "treatment"}
	if input.Trial%2 == 0 {
		conditions[0], conditions[1] = conditions[1], conditions[0]
	}
	executionOrder := make([]string, 0, len(conditions))
	for _, condition := range conditions {
		conditionDir := filepath.Join(input.OutputDir, condition)
		workspace := filepath.Join(conditionDir, "workspace")
		if err := prepareWorkspace(input.FixturePath, workspace); err != nil {
			return PairResult{}, err
		}
		installed := filepath.Join(workspace, ".agents", "skills", skillName)
		if _, err := os.Lstat(installed); err == nil {
			return PairResult{}, fmt.Errorf("fixture exposes target skill in %s", condition)
		} else if !os.IsNotExist(err) {
			return PairResult{}, err
		}
		if condition == "control" {
			controlSkillAbsent = true
		}
		if condition == "treatment" {
			if err := copySkillPayload(skillPath, installed); err != nil {
				return PairResult{}, err
			}
			installedHash, err := skillpayload.Hash(installed)
			if err != nil {
				return PairResult{}, err
			}
			if installedHash != skillHash {
				return PairResult{}, fmt.Errorf("installed skill hash does not match source")
			}
			treatmentSkillPresent = true
			treatmentHashMatches = true
		}

		result, err := runCodex(ctx, codexInput{
			ConditionDir: conditionDir, Workspace: workspace, CodexHome: codexHome, Executable: input.Executable,
			Model: input.Model, Prompt: input.Task.Prompt, SkillName: skillName,
			Timeout: input.Timeout, Environment: input.Environment,
		})
		if err != nil {
			return PairResult{}, err
		}
		executionOrder = append(executionOrder, condition)
		result.Condition = condition
		if condition == "treatment" {
			result.SkillPath = installed
			result.SkillSHA256 = skillHash
		}
		result.Grade, err = GradeDeterministic(input.Task, workspace, result.Response)
		if err != nil {
			return PairResult{}, err
		}
		conditionResults[condition] = result
	}
	pair := PairResult{
		TaskID: input.Task.ID, Task: input.Task, Trial: input.Trial, SkillName: skillName,
		SkillSHA256: skillHash, ToolPosture: "read_only",
		ControlSkillAbsent: controlSkillAbsent, TreatmentSkillPresent: treatmentSkillPresent,
		TreatmentHashMatches: treatmentHashMatches,
		ExecutionOrder:       executionOrder,
		Control:              conditionResults["control"], Treatment: conditionResults["treatment"],
	}
	pair.ReportJSONPath, pair.ReportMarkdownPath, err = writePairReport(input.OutputDir, pair)
	if err != nil {
		return PairResult{}, err
	}
	return pair, nil
}

func resolveCodexHome(explicit string) (string, error) {
	path := explicit
	if path == "" {
		path = os.Getenv("CODEX_HOME")
	}
	if path == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("resolve user home: %w", err)
		}
		path = filepath.Join(home, ".codex")
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve Codex home: %w", err)
	}
	info, err := os.Stat(absolute)
	if err != nil {
		return "", fmt.Errorf("authenticated Codex home is unavailable: %w", err)
	}
	if !info.IsDir() {
		return "", fmt.Errorf("authenticated Codex home is not a directory: %s", absolute)
	}
	return absolute, nil
}

func prepareWorkspace(fixture, workspace string) error {
	if err := os.MkdirAll(workspace, 0o755); err != nil {
		return err
	}
	if fixture == "" {
		return nil
	}
	return copyTree(fixture, workspace)
}

func copySkillPayload(source, destination string) error {
	files, err := skillpayload.Files(source)
	if err != nil {
		return err
	}
	for _, path := range files {
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		if err := copyRegularFile(path, filepath.Join(destination, relative)); err != nil {
			return err
		}
	}
	return nil
}

func copyTree(source, destination string) error {
	return filepath.Walk(source, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("fixture contains symlink: %s", path)
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		target := filepath.Join(destination, relative)
		if info.IsDir() {
			return os.MkdirAll(target, info.Mode().Perm())
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("fixture contains unsupported entry: %s", path)
		}
		return copyRegularFile(path, target)
	})
}

func copyRegularFile(source, destination string) error {
	info, err := os.Stat(source)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, info.Mode().Perm())
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(output, input)
	return errorsJoin(copyErr, output.Close())
}

func errorsJoin(values ...error) error {
	for _, value := range values {
		if value != nil {
			return value
		}
	}
	return nil
}
