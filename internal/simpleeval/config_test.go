package simpleeval

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBuildDryRunCountsWithoutCreatingOutput(t *testing.T) {
	root := t.TempDir()
	skill := filepath.Join(root, "skill")
	if err := os.Mkdir(skill, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "SKILL.md"), []byte("# Skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	tasks := filepath.Join(root, "tasks.jsonl")
	data := "{\"id\":\"one\",\"prompt\":\"One\",\"graders\":[{\"type\":\"regex\",\"pattern\":\"Blue\"},{\"type\":\"rubric\",\"text\":\"Be safe.\"}]}\n" +
		"{\"id\":\"two\",\"prompt\":\"Two\",\"graders\":[{\"type\":\"not_regex\",\"pattern\":\"Red\"}]}\n"
	if err := os.WriteFile(tasks, []byte(data), 0o644); err != nil {
		t.Fatal(err)
	}
	harness := fakeVersionExecutable(t, root)
	output := filepath.Join(root, "output")
	plan, err := BuildDryRun(DryRunInput{
		SkillPath: skill, TasksPath: tasks, Harness: "codex", HarnessBin: harness,
		Model: "gpt-5.6-sol", JudgeModel: "gpt-5.6-sol", Trials: 3, TimeoutSeconds: 120, OutputDir: output,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !plan.Valid || plan.CreatedArtifacts || plan.ProviderCalls != 0 {
		t.Fatalf("unexpected plan validity: %+v", plan)
	}
	want := DryRunCounts{TaskCount: 2, PairedTrials: 6, TargetInvocations: 12, RubricGraderCount: 1, JudgeInvocations: 6, TotalInvocations: 18}
	if plan.Counts != want {
		t.Fatalf("counts=%+v want=%+v", plan.Counts, want)
	}
	if plan.Usage.Tokens != nil || plan.Usage.Cost != nil || plan.Usage.Status != "unknown_until_live_run" {
		t.Fatalf("unexpected usage: %+v", plan.Usage)
	}
	if _, err := os.Stat(output); !os.IsNotExist(err) {
		t.Fatalf("dry-run created output: %v", err)
	}
}

func TestBuildDryRunRequiresJudgeForRubric(t *testing.T) {
	root := t.TempDir()
	skill := filepath.Join(root, "skill")
	if err := os.Mkdir(skill, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "SKILL.md"), []byte("# Skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	tasks := filepath.Join(root, "tasks.jsonl")
	if err := os.WriteFile(tasks, []byte("{\"id\":\"one\",\"prompt\":\"One\",\"graders\":[{\"type\":\"rubric\",\"text\":\"Be safe.\"}]}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := BuildDryRun(DryRunInput{
		SkillPath: skill, TasksPath: tasks, Harness: "codex", HarnessBin: fakeVersionExecutable(t, root),
		Model: "gpt-5.6-sol", Trials: 1, TimeoutSeconds: 120, OutputDir: filepath.Join(root, "output"),
	})
	if err == nil || !strings.Contains(err.Error(), "judge-model is required") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func fakeVersionExecutable(t *testing.T, root string) string {
	t.Helper()
	path := filepath.Join(root, "fake-codex")
	if err := os.WriteFile(path, []byte("#!/bin/sh\n[ \"$#\" -eq 1 ] && [ \"$1\" = \"--version\" ] || exit 9\nprintf 'fake-codex 1.0\\n'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}
