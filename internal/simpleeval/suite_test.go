package simpleeval

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRunSuiteRetainsEveryPairOnce(t *testing.T) {
	root := t.TempDir()
	plan := suitePlan(t, root, "{\"id\":\"one\",\"prompt\":\"One\",\"graders\":[{\"type\":\"regex\",\"pattern\":\"Blue\"}]}\n"+
		"{\"id\":\"two\",\"prompt\":\"Two\",\"graders\":[{\"type\":\"not_regex\",\"pattern\":\"Green\"}]}\n", 2, "")

	result, err := RunSuite(context.Background(), plan)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Valid || len(result.Pairs) != 4 || result.Counts.TargetInvocations != 8 || result.Counts.TotalInvocations != 8 {
		t.Fatalf("unexpected result: %+v", result)
	}
	for _, pair := range result.Pairs {
		wantOrder := "control -> treatment"
		if pair.Trial%2 == 0 {
			wantOrder = "treatment -> control"
		}
		if got := strings.Join(pair.ExecutionOrder, " -> "); got != wantOrder {
			t.Errorf("%s trial %d order=%s want=%s", pair.TaskID, pair.Trial, got, wantOrder)
		}
		for _, path := range []string{pair.ReportJSON, pair.ReportMarkdown} {
			if info, err := os.Stat(filepath.Join(result.OutputDir, filepath.FromSlash(path))); err != nil || !info.Mode().IsRegular() {
				t.Errorf("missing pair artifact %s: %v", path, err)
			}
		}
	}
	for _, path := range []string{"config.json", "tasks.jsonl", "run.json"} {
		if info, err := os.Stat(filepath.Join(result.OutputDir, path)); err != nil || !info.Mode().IsRegular() {
			t.Errorf("missing suite artifact %s: %v", path, err)
		}
	}
	traces, err := filepath.Glob(filepath.Join(result.OutputDir, "task-*", "trial-*", "*", "trace.jsonl"))
	if err != nil || len(traces) != 8 {
		t.Fatalf("target invocation traces=%d err=%v", len(traces), err)
	}
}

func TestRunSuiteRejectsUnsafeInputsBeforeOutput(t *testing.T) {
	for _, test := range []struct {
		name  string
		tasks string
		judge string
		want  string
	}{
		{name: "unsafe id", tasks: "{\"id\":\"../escape\",\"prompt\":\"One\",\"graders\":[{\"type\":\"regex\",\"pattern\":\"Blue\"}]}\n", want: "path-safe"},
		{name: "rubric", tasks: "{\"id\":\"one\",\"prompt\":\"One\",\"graders\":[{\"type\":\"rubric\",\"text\":\"Be safe.\"}]}\n", judge: "gpt-5.6-sol", want: "rubric graders are not supported"},
	} {
		t.Run(test.name, func(t *testing.T) {
			root := t.TempDir()
			plan := suitePlan(t, root, test.tasks, 1, test.judge)
			_, err := RunSuite(context.Background(), plan)
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("unexpected error: %v", err)
			}
			if _, err := os.Stat(plan.Configuration.OutputDir); !os.IsNotExist(err) {
				t.Fatalf("invalid suite created output: %v", err)
			}
		})
	}
}

func TestPairConditionModelRequirement(t *testing.T) {
	for _, test := range []struct {
		name      string
		condition ConditionResult
		want      bool
	}{
		{name: "CLI configured and unresolved", condition: ConditionResult{ExitCode: 0, RequestedModel: "gpt-5.6-sol"}, want: true},
		{name: "trace match", condition: ConditionResult{ExitCode: 0, RequestedModel: "gpt-5.6-sol", ActualModel: "gpt-5.6-sol", ModelAttested: true}, want: true},
		{name: "trace mismatch", condition: ConditionResult{ExitCode: 0, RequestedModel: "gpt-5.6-sol", ActualModel: "different-model"}, want: false},
	} {
		t.Run(test.name, func(t *testing.T) {
			if got := pairConditionValid(test.condition); got != test.want {
				t.Fatalf("pairConditionValid()=%t want=%t", got, test.want)
			}
		})
	}
}

func suitePlan(t *testing.T, root, taskData string, trials int, judge string) DryRunPlan {
	t.Helper()
	skill := filepath.Join(root, "skill")
	if err := os.Mkdir(skill, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "SKILL.md"), []byte("# Skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	tasks := filepath.Join(root, "tasks.jsonl")
	if err := os.WriteFile(tasks, []byte(taskData), 0o644); err != nil {
		t.Fatal(err)
	}
	plan, err := BuildDryRun(DryRunInput{
		SkillPath: skill, TasksPath: tasks, Harness: "codex", HarnessBin: fakeCodexPath(t),
		Model: "gpt-5.6-sol", JudgeModel: judge, Trials: trials, TimeoutSeconds: 10,
		OutputDir: filepath.Join(root, "run"),
	})
	if err != nil {
		t.Fatal(err)
	}
	return plan
}
