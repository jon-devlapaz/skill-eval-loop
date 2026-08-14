package simpleeval

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type contractTask struct {
	ID      string           `json:"id"`
	Prompt  string           `json:"prompt"`
	Graders []contractGrader `json:"graders"`
}

type contractGrader struct {
	Type     string          `json:"type"`
	Pattern  string          `json:"pattern"`
	Text     string          `json:"text"`
	Path     string          `json:"path"`
	Expected json.RawMessage `json:"expected"`
}

type contractDryRun struct {
	Valid            bool   `json:"valid"`
	Mode             string `json:"mode"`
	CreatedArtifacts bool   `json:"created_artifacts"`
	ProviderCalls    int    `json:"provider_calls"`
	Configuration    struct {
		SkillPath      string `json:"skill_path"`
		SkillSHA256    string `json:"skill_sha256"`
		TasksPath      string `json:"tasks_path"`
		TasksSHA256    string `json:"tasks_sha256"`
		Harness        string `json:"harness"`
		HarnessBin     string `json:"harness_executable"`
		HarnessVersion string `json:"harness_version"`
		Model          string `json:"model"`
		JudgeModel     string `json:"judge_model"`
		Trials         int    `json:"trials"`
		TimeoutSeconds int    `json:"timeout_seconds"`
		OutputDir      string `json:"output_dir"`
		Execution      string `json:"execution"`
		ConditionOrder string `json:"condition_order"`
		ToolPosture    string `json:"tool_posture"`
	} `json:"configuration"`
	Counts struct {
		TaskCount         int `json:"task_count"`
		PairedTrials      int `json:"paired_trials"`
		TargetInvocations int `json:"target_invocations"`
		RubricGraderCount int `json:"rubric_grader_count"`
		JudgeInvocations  int `json:"judge_invocations"`
		TotalInvocations  int `json:"total_invocations"`
	} `json:"counts"`
	Usage struct {
		Tokens *int     `json:"tokens"`
		Cost   *float64 `json:"cost"`
		Status string   `json:"status"`
	} `json:"usage"`
}

func TestContract(t *testing.T) {
	t.Run("tasks are versionless JSONL", func(t *testing.T) {
		file, err := os.Open("testdata/tasks.jsonl")
		if err != nil {
			t.Fatal(err)
		}
		defer file.Close()

		supported := map[string]bool{
			"regex": true, "not_regex": true, "file_exists": true,
			"json_equal": true, "rubric": true,
		}
		seenIDs := map[string]bool{}
		seenGraders := map[string]bool{}
		scanner := bufio.NewScanner(file)
		count := 0
		for scanner.Scan() {
			count++
			var raw map[string]json.RawMessage
			if err := json.Unmarshal(scanner.Bytes(), &raw); err != nil {
				t.Fatalf("line %d: %v", count, err)
			}
			if _, exists := raw["schema_version"]; exists {
				t.Fatalf("line %d declares a schema version", count)
			}
			if _, exists := raw["provenance_manifest"]; exists {
				t.Fatalf("line %d declares provenance machinery", count)
			}

			var task contractTask
			if err := json.Unmarshal(scanner.Bytes(), &task); err != nil {
				t.Fatalf("line %d: %v", count, err)
			}
			if task.ID == "" || task.Prompt == "" || len(task.Graders) == 0 {
				t.Fatalf("line %d is not a complete task", count)
			}
			if seenIDs[task.ID] {
				t.Fatalf("duplicate task id %q", task.ID)
			}
			seenIDs[task.ID] = true
			for _, grader := range task.Graders {
				if !supported[grader.Type] {
					t.Fatalf("task %q has unsupported grader %q", task.ID, grader.Type)
				}
				seenGraders[grader.Type] = true
			}
		}
		if err := scanner.Err(); err != nil {
			t.Fatal(err)
		}
		if count != 2 {
			t.Fatalf("got %d tasks, want 2", count)
		}
		for _, grader := range []string{"regex", "not_regex", "file_exists", "json_equal", "rubric"} {
			if !seenGraders[grader] {
				t.Errorf("fixture does not demonstrate %s", grader)
			}
		}
	})

	t.Run("dry run makes variables and calls explicit", func(t *testing.T) {
		data, err := os.ReadFile("testdata/dry-run.json")
		if err != nil {
			t.Fatal(err)
		}
		var plan contractDryRun
		if err := json.Unmarshal(data, &plan); err != nil {
			t.Fatal(err)
		}
		if !plan.Valid || plan.Mode != "dry_run" || plan.CreatedArtifacts || plan.ProviderCalls != 0 {
			t.Fatal("dry run must be valid, no-write, and no-call")
		}
		config := plan.Configuration
		for label, path := range map[string]string{
			"skill": config.SkillPath, "tasks": config.TasksPath,
			"harness": config.HarnessBin, "output": config.OutputDir,
		} {
			if !filepath.IsAbs(path) {
				t.Errorf("%s path is not absolute: %q", label, path)
			}
		}
		for label, hash := range map[string]string{"skill": config.SkillSHA256, "tasks": config.TasksSHA256} {
			decoded, err := hex.DecodeString(hash)
			if err != nil || len(decoded) != 32 {
				t.Errorf("%s hash is not SHA-256: %q", label, hash)
			}
		}
		tasks, err := os.ReadFile("testdata/tasks.jsonl")
		if err != nil {
			t.Fatal(err)
		}
		tasksHash := sha256.Sum256(tasks)
		if got := hex.EncodeToString(tasksHash[:]); got != config.TasksSHA256 {
			t.Fatalf("task hash = %s, want %s", config.TasksSHA256, got)
		}
		if config.Harness == "" || config.HarnessVersion == "" || config.Model == "" ||
			config.JudgeModel == "" || config.Trials < 1 || config.TimeoutSeconds < 1 ||
			config.Execution != "sequential" || config.ConditionOrder == "" || config.ToolPosture == "" {
			t.Fatal("dry run omits an interpretation-changing variable")
		}
		counts := plan.Counts
		wantPairs := counts.TaskCount * config.Trials
		wantTargets := 2 * wantPairs
		wantJudges := 2 * counts.RubricGraderCount * config.Trials
		if counts.PairedTrials != wantPairs || counts.TargetInvocations != wantTargets ||
			counts.JudgeInvocations != wantJudges || counts.TotalInvocations != wantTargets+wantJudges {
			t.Fatalf("inconsistent invocation counts: %+v", counts)
		}
		if plan.Usage.Tokens != nil || plan.Usage.Cost != nil || plan.Usage.Status != "unknown_until_live_run" {
			t.Fatal("dry run must not invent usage or cost")
		}
	})

	t.Run("documentation limits the claim", func(t *testing.T) {
		data, err := os.ReadFile("../../docs/minimum-eval-contract.md")
		if err != nil {
			t.Fatal(err)
		}
		contract := string(data)
		for _, required := range []string{
			"Runner acceptance", "skill-quality claim", "no difference",
			"Raw evidence is authoritative", "There is no schema version",
		} {
			if !strings.Contains(contract, required) {
				t.Errorf("contract does not state %q", required)
			}
		}
	})
}
