package runplan

import (
	"os"
	"path/filepath"
	"testing"
)

func TestBuildDryPlanCountsInvocationsWithoutCreatingOutput(t *testing.T) {
	root := t.TempDir()
	skill := filepath.Join(root, "fixture-skill")
	if err := os.MkdirAll(filepath.Join(skill, "evals"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "SKILL.md"), []byte("# Fixture\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	suite := `{
  "schema_version": 2,
  "skill_name": "fixture-skill",
  "suite_type": "capability",
  "dataset_origin": "author_derived",
  "tool_profile": "no_tools",
  "evals": [{
    "id": "case-one",
    "prompt": "Return ok",
    "behavior_class": "positive",
    "graders": [{"name": "contains", "type": "response_contains", "value": "ok"}],
    "reference": {"response": "ok"}
  }]
}`
	if err := os.WriteFile(filepath.Join(skill, "evals", "evals.json"), []byte(suite), 0o644); err != nil {
		t.Fatal(err)
	}
	harness := filepath.Join(root, "fake-pi")
	if err := os.WriteFile(harness, []byte("#!/bin/sh\nprintf 'fake-pi 1.0\\n'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(root, "planned-output")
	plan, err := Build(Input{
		SkillPath: skill, OutputDir: output, Model: "provider/model-1",
		Trials: 2, Harness: "pi", HarnessBin: harness, Observer: "headless",
	})
	if err != nil {
		t.Fatal(err)
	}
	if plan.HarnessInvocations.Target != 4 || plan.HarnessInvocations.Total != 4 {
		t.Fatalf("counts=%#v", plan.HarnessInvocations)
	}
	if _, err := os.Stat(output); !os.IsNotExist(err) {
		t.Fatalf("dry plan created output: %v", err)
	}
}

func TestPinnedModelRejectsMovingAliases(t *testing.T) {
	for _, model := range []string{"", "auto", "default", "provider/latest", "PROVIDER/LATEST-1"} {
		if err := validatePinnedModel(model); err == nil {
			t.Fatalf("model %q was accepted", model)
		}
	}
}
