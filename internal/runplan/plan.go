package runplan

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/jon-devlapaz/skill-eval-loop/internal/evalspec"
)

type Input struct {
	SkillPath  string
	EvalsPath  string
	OutputDir  string
	Model      string
	Trials     int
	Harness    string
	HarnessBin string
	PiBin      string
	JudgeModel string
	Observer   string
}

type InvocationCounts struct {
	Target            int `json:"target"`
	ConditionJudges   int `json:"condition_judges"`
	References        int `json:"references"`
	CounterReferences int `json:"counter_references"`
	Judge             int `json:"judge"`
	Total             int `json:"total"`
}

type ExecutionOrder struct {
	Policy     string   `json:"policy"`
	OddTrials  []string `json:"odd_trials"`
	EvenTrials []string `json:"even_trials"`
}

type Observer struct {
	Kind                string `json:"kind"`
	RequiredEnvironment any    `json:"required_environment"`
	ArtifactsObservable bool   `json:"artifacts_observable"`
}

type Plan struct {
	SkillPath          string           `json:"skill_path"`
	EvalsPath          string           `json:"evals_path"`
	OutputDir          string           `json:"output_dir"`
	Harness            string           `json:"harness"`
	HarnessPath        string           `json:"harness_path"`
	HarnessVersion     string           `json:"harness_version"`
	Model              string           `json:"model"`
	JudgeModel         any              `json:"judge_model"`
	ActivationMode     string           `json:"activation_mode"`
	TrialsPerCase      int              `json:"trials_per_case"`
	CaseCount          int              `json:"case_count"`
	PairCount          int              `json:"pair_count"`
	HarnessInvocations InvocationCounts `json:"harness_invocations"`
	ProviderModelCalls string           `json:"provider_model_calls"`
	ExecutionOrder     ExecutionOrder   `json:"execution_order"`
	Observer           Observer         `json:"observer"`
}

func Build(input Input) (Plan, error) {
	if input.Trials < 1 {
		return Plan{}, errors.New("trials must be at least 1")
	}
	if err := validatePinnedModel(input.Model); err != nil {
		return Plan{}, err
	}
	if input.JudgeModel != "" {
		if err := validatePinnedModel(input.JudgeModel); err != nil {
			return Plan{}, err
		}
	}
	skillPath, err := resolvedPath(input.SkillPath)
	if err != nil {
		return Plan{}, err
	}
	info, err := os.Stat(filepath.Join(skillPath, "SKILL.md"))
	if err != nil || !info.Mode().IsRegular() {
		return Plan{}, fmt.Errorf("skill has no SKILL.md: %s", skillPath)
	}
	suite, err := evalspec.Load(skillPath, input.EvalsPath)
	if err != nil {
		return Plan{}, err
	}
	if !stringSet("hermes", "claude-code", "codex", "pi")[input.Harness] {
		return Plan{}, fmt.Errorf("harness must be one of ['hermes', 'claude-code', 'codex', 'pi']")
	}
	if input.PiBin != "" && input.Harness != "pi" {
		return Plan{}, errors.New("--pi-bin can only be used with --harness pi")
	}
	if input.Observer != "headless" && input.Observer != "herdr" {
		return Plan{}, errors.New("observer must be one of ['headless', 'herdr']")
	}
	if err := assertFixtureIsolation(suite, skillPath); err != nil {
		return Plan{}, err
	}
	outputDir := input.OutputDir
	if outputDir == "" {
		outputDir, err = defaultOutput(skillPath)
		if err != nil {
			return Plan{}, err
		}
	}
	outputDir, err = resolvedPath(outputDir)
	if err != nil {
		return Plan{}, err
	}
	if pathWithin(outputDir, skillPath) {
		return Plan{}, fmt.Errorf("evaluation output cannot live inside evaluated skill %s", skillPath)
	}

	counts := InvocationCounts{Target: 2 * input.Trials * len(suite.Cases)}
	for _, current := range suite.Cases {
		modelRubrics := 0
		for _, grader := range current.Graders {
			if grader["type"] == "model_rubric" {
				modelRubrics++
			}
		}
		if modelRubrics > 0 && input.JudgeModel == "" {
			return Plan{}, errors.New("model_rubric graders require --judge-model")
		}
		counts.ConditionJudges += modelRubrics * 2 * input.Trials
		counts.References += modelRubrics
		if current.HasCounterReference {
			counts.CounterReferences += modelRubrics
		}
	}
	counts.Judge = counts.ConditionJudges + counts.References + counts.CounterReferences
	counts.Total = counts.Target + counts.Judge

	executable := input.HarnessBin
	if executable == "" {
		executable = input.PiBin
	}
	if executable == "" {
		executable = map[string]string{"hermes": "hermes", "claude-code": "claude", "codex": "codex", "pi": "pi"}[input.Harness]
	}
	resolvedExecutable, err := exec.LookPath(executable)
	if err != nil {
		return Plan{}, fmt.Errorf("%s executable not found: %s", input.Harness, executable)
	}
	versionBytes, err := exec.Command(resolvedExecutable, "--version").Output()
	if err != nil {
		return Plan{}, err
	}
	version := strings.TrimSpace(string(versionBytes))
	if version == "" {
		return Plan{}, fmt.Errorf("%s returned an empty version", input.Harness)
	}
	judgeModel := any(nil)
	if input.JudgeModel != "" {
		judgeModel = input.JudgeModel
	}
	return Plan{
		SkillPath: skillPath, EvalsPath: suite.SourcePath, OutputDir: outputDir,
		Harness: input.Harness, HarnessPath: resolvedExecutable, HarnessVersion: version,
		Model: input.Model, JudgeModel: judgeModel, ActivationMode: suite.ActivationMode,
		TrialsPerCase: input.Trials, CaseCount: len(suite.Cases), PairCount: len(suite.Cases) * input.Trials,
		HarnessInvocations: counts, ProviderModelCalls: "unknown",
		ExecutionOrder: ExecutionOrder{Policy: "counterbalanced_by_trial", OddTrials: []string{"without_skill", "with_skill"}, EvenTrials: []string{"with_skill", "without_skill"}},
		Observer:       Observer{Kind: "headless", RequiredEnvironment: nil, ArtifactsObservable: true},
	}, nil
}

func Bytes(plan Plan) ([]byte, error) {
	data, err := json.MarshalIndent(plan, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(data, '\n'), nil
}

func validatePinnedModel(model string) error {
	normalized := strings.ToLower(strings.TrimSpace(model))
	if normalized == "" || normalized == "auto" || normalized == "default" || strings.Contains(normalized, "latest") {
		return errors.New("use an exact pinned model id, not a moving alias")
	}
	return nil
}

func assertFixtureIsolation(suite *evalspec.Suite, skillPath string) error {
	for _, current := range suite.Cases {
		fixture, _ := current.Raw["fixture"].(string)
		if fixture == "" {
			continue
		}
		path, err := evalspec.SafeRelativePath(suite.SuiteRoot, fixture, current.ID+".fixture")
		if err != nil {
			return err
		}
		for _, nativeRoot := range []string{filepath.Join(".agents", "skills"), filepath.Join(".claude", "skills")} {
			contaminated := filepath.Join(path, nativeRoot, filepath.Base(skillPath))
			if _, err := os.Lstat(contaminated); err == nil {
				return fmt.Errorf("control fixture for %s contains target skill at %s", current.ID, contaminated)
			}
		}
	}
	return nil
}

func resolvedPath(path string) (string, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	current := absolute
	tail := []string{}
	for {
		resolved, resolveErr := filepath.EvalSymlinks(current)
		if resolveErr == nil {
			for index := len(tail) - 1; index >= 0; index-- {
				resolved = filepath.Join(resolved, tail[index])
			}
			return resolved, nil
		}
		parent := filepath.Dir(current)
		if parent == current {
			return absolute, nil
		}
		tail = append(tail, filepath.Base(current))
		current = parent
	}
}

func pathWithin(path, root string) bool {
	relative, err := filepath.Rel(root, path)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

func defaultOutput(skillPath string) (string, error) {
	runBytes := make([]byte, 3)
	if _, err := rand.Read(runBytes); err != nil {
		return "", err
	}
	root := filepath.Join(filepath.Dir(filepath.Dir(skillPath)), ".eval-runs")
	runID := "run-" + time.Now().UTC().Format("20060102T150405000000Z") + "-" + hex.EncodeToString(runBytes)
	return filepath.Join(root, filepath.Base(skillPath), runID), nil
}

func stringSet(values ...string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		result[value] = true
	}
	return result
}
