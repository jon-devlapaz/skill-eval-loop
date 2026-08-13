package audit

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jon-devlapaz/skill-eval-loop/internal/evalspec"
)

func TestSchemaTwoAuditMatchesPythonReportBytes(t *testing.T) {
	skill := writeSchemaTwoSkill(t, json.Number("2"))
	data, err := Bytes(Run(skill, ""))
	if err != nil {
		t.Fatal(err)
	}
	want := `{
  "valid": true,
  "errors": [],
  "schema_version": 2,
  "skill_name": "skill",
  "suite_type": "capability",
  "dataset_origin": "author_derived",
  "activation_mode": "forced",
  "case_count": 1,
  "routing_classes": [],
  "grader_discrimination": {
    "claim": "none",
    "contrast_case_count": 0,
    "response_sensitive_grader_count": 1,
    "deterministic_graders_checked": 0,
    "model_graders_pending_runtime": 0
  },
  "provenance_case_count": 0
}
`
	if string(data) != want {
		t.Fatalf("report bytes differ\nwant:\n%s\ngot:\n%s", want, data)
	}
}

func TestLoaderPreservesFrozenDuplicateUnknownAndNumericBehavior(t *testing.T) {
	directory := t.TempDir()
	skill := filepath.Join(directory, "skill")
	if err := os.MkdirAll(filepath.Join(skill, "evals"), 0o755); err != nil {
		t.Fatal(err)
	}
	data := `{
  "schema_version": 9,
  "schema_version": 2.0,
  "unknown_root": true,
  "skill_name": "skill",
  "suite_type": "capability",
  "dataset_origin": "author_derived",
  "tool_profile": "no_tools",
  "evals": [{
    "id": "case-one",
    "prompt": "Return ok",
    "behavior_class": "positive",
    "unknown_case": 1,
    "graders": [{"name":"contains","type":"response_contains","value":"ok","unknown_grader":true}],
    "reference": {"response":"ok"}
  }]
}`
	if err := os.WriteFile(filepath.Join(skill, "evals", "evals.json"), []byte(data), 0o644); err != nil {
		t.Fatal(err)
	}
	report := Run(skill, "")
	if !report.Valid || report.SchemaVersion.(json.Number).String() != "2.0" {
		t.Fatalf("report = %#v", report)
	}
}

func TestInvalidUTF8FailsAudit(t *testing.T) {
	directory := t.TempDir()
	skill := filepath.Join(directory, "skill")
	if err := os.MkdirAll(filepath.Join(skill, "evals"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "evals", "evals.json"), []byte{0xff}, 0o644); err != nil {
		t.Fatal(err)
	}
	report := Run(skill, "")
	if report.Valid || report.Errors[0] != "invalid_eval_suite" || !strings.Contains(report.Details[0], "valid UTF-8") {
		t.Fatalf("report = %#v", report)
	}
}

func TestValidSchemaThreeProvenanceAndContrast(t *testing.T) {
	directory := t.TempDir()
	skill := filepath.Join(directory, "skill")
	evals := filepath.Join(skill, "evals")
	if err := os.MkdirAll(filepath.Join(evals, "provenance"), 0o755); err != nil {
		t.Fatal(err)
	}
	artifact := filepath.Join(evals, "provenance", "case.json")
	if err := os.WriteFile(artifact, []byte("{}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	caseValue := map[string]any{
		"id": "case-one", "prompt": "Return ok", "behavior_class": "positive",
		"routing_class": "should_trigger", "expected_skill_loading": "required",
		"graders":           []any{map[string]any{"name": "contains", "type": "response_contains", "value": "ok"}},
		"reference":         map[string]any{"response": "ok"},
		"counter_reference": map[string]any{"response": "wrong"},
	}
	suiteValue := map[string]any{
		"schema_version": json.Number("3"), "skill_name": "skill",
		"suite_type": "capability", "dataset_origin": "author_derived",
		"tool_profile": "no_tools", "activation_mode": "forced",
		"grader_discrimination": "case_contrast", "provenance_manifest": "provenance.json",
		"evals": []any{caseValue},
	}
	caseHash, _ := evalspec.CanonicalSHA256(caseValue)
	suiteHash, _ := evalspec.CanonicalSHA256(suiteValue)
	artifactHash, _ := evalspec.FileSHA256(artifact)
	provenance := map[string]any{
		"schema_version": json.Number("1"), "suite_sha256": suiteHash,
		"cases": []any{map[string]any{
			"case_id": "case-one", "origin": "author_derived", "source_id": "source-1",
			"source_type": "author_scenario", "observed_at": "2026-08-13", "task_author": "test",
			"artifact": "provenance/case.json", "artifact_sha256": artifactHash, "case_sha256": caseHash,
		}},
	}
	writeJSON(t, filepath.Join(evals, "evals.json"), suiteValue)
	writeJSON(t, filepath.Join(evals, "provenance.json"), provenance)
	report := Run(skill, "")
	if !report.Valid || report.GraderDiscrimination.ContrastCaseCount != 1 || report.ProvenanceCaseCount != 1 {
		t.Fatalf("report = %#v", report)
	}
}

func writeSchemaTwoSkill(t *testing.T, schema json.Number) string {
	t.Helper()
	directory := t.TempDir()
	skill := filepath.Join(directory, "skill")
	value := map[string]any{
		"schema_version": schema, "skill_name": "skill", "suite_type": "capability",
		"dataset_origin": "author_derived", "tool_profile": "no_tools",
		"evals": []any{map[string]any{
			"id": "case-one", "prompt": "Return ok", "behavior_class": "positive",
			"graders":   []any{map[string]any{"name": "contains", "type": "response_contains", "value": "ok"}},
			"reference": map[string]any{"response": "ok"},
		}},
	}
	writeJSON(t, filepath.Join(skill, "evals", "evals.json"), value)
	return skill
}

func writeJSON(t *testing.T, path string, value any) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(data, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
}
