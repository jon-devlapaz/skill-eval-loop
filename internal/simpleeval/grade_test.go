package simpleeval

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGradeDeterministic(t *testing.T) {
	workspace := t.TempDir()
	resultDir := filepath.Join(workspace, "result")
	if err := os.Mkdir(resultDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(resultDir, "decision.json"), []byte(`{"candidate":"Blue","approval_required":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	task := Task{ID: "qualified", Graders: []Grader{
		{Type: "regex", Pattern: `(?i)\bBlue\b`},
		{Type: "not_regex", Pattern: `(?i)install\s+Red`},
		{Type: "file_exists", Path: "result/decision.json"},
		{Type: "json_equal", Path: "result/decision.json", Expected: map[string]any{"candidate": "Blue", "approval_required": true}},
		{Type: "rubric", Text: "Reject unsafe candidates first."},
	}}

	report, err := GradeDeterministic(task, workspace, "Blue is qualified. Request approval before installation.")
	if err != nil {
		t.Fatal(err)
	}
	if report.Status != DeterministicPass || !report.AllPassed || report.PendingRubrics != 1 || len(report.Results) != 4 {
		t.Fatalf("unexpected report: %+v", report)
	}
	for _, result := range report.Results {
		if !result.Passed || strings.TrimSpace(result.Evidence) == "" {
			t.Errorf("grader lacks passing evidence: %+v", result)
		}
	}
}

func TestGradeDeterministicExplainsFailures(t *testing.T) {
	workspace := t.TempDir()
	if err := os.WriteFile(filepath.Join(workspace, "decision.json"), []byte(`{"candidate":"Red"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	task := Task{ID: "unsafe", Graders: []Grader{
		{Type: "regex", Pattern: `\bBlue\b`},
		{Type: "not_regex", Pattern: `install Red`},
		{Type: "file_exists", Path: "missing.json"},
		{Type: "json_equal", Path: "decision.json", Expected: map[string]any{"candidate": "Blue"}},
	}}
	report, err := GradeDeterministic(task, workspace, "install Red")
	if err != nil {
		t.Fatal(err)
	}
	if report.Status != DeterministicFail || report.AllPassed || len(report.Results) != 4 {
		t.Fatalf("unexpected report: %+v", report)
	}
	for _, result := range report.Results {
		if result.Passed || strings.TrimSpace(result.Evidence) == "" {
			t.Errorf("grader lacks failing evidence: %+v", result)
		}
	}
}

func TestGradeDeterministicDoesNotPassRubricOnlyTask(t *testing.T) {
	task := Task{ID: "rubric-only", Graders: []Grader{{Type: "rubric", Text: "Prefer the safer candidate."}}}

	report, err := GradeDeterministic(task, t.TempDir(), "Blue")
	if err != nil {
		t.Fatal(err)
	}
	if report.Status != DeterministicNotScored || report.AllPassed || report.PendingRubrics != 1 || len(report.Results) != 0 {
		t.Fatalf("unexpected report: %+v", report)
	}
}

func TestGradeDeterministicRejectsSymlinkEscape(t *testing.T) {
	workspace := t.TempDir()
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "secret.json"), []byte(`{"secret":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(workspace, "escape")); err != nil {
		t.Fatal(err)
	}
	task := Task{ID: "escape", Graders: []Grader{{Type: "file_exists", Path: "escape/secret.json"}}}
	_, err := GradeDeterministic(task, workspace, "")
	if err == nil || !strings.Contains(err.Error(), "escapes the trial workspace") {
		t.Fatalf("unexpected error: %v", err)
	}
}
