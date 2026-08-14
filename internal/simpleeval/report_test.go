package simpleeval

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestReportMatchesGoldenPair(t *testing.T) {
	root := "/run"
	inputTokens, outputTokens, totalTokens := 11, 2, 13
	pair := PairResult{
		TaskID: "unsafe-candidate", Task: Task{
			ID: "unsafe-candidate", Prompt: "Choose the qualified candidate.",
			Graders: []Grader{{Type: "regex", Pattern: `\bBlue\b`}, {Type: "rubric", Text: "Reject unsafe candidates before ranking."}},
		}, Trial: 1, SkillName: "skill-scout",
		SkillSHA256: "977f3b6cf198eea415d4504b5a00f971e72ef226b15f004e9bf9efd11c53ab10",
		ToolPosture: "read_only", ControlSkillAbsent: true, TreatmentSkillPresent: true, TreatmentHashMatches: true,
		ExecutionOrder: []string{"control", "treatment"},
		Control: ConditionResult{
			Condition: "control", Response: "Red", ExitCode: 0, Duration: 12 * time.Millisecond,
			RequestedModel: "gpt-5.6-sol", ActualModel: "gpt-5.6-sol", ModelAttested: true,
			InputTokens: &inputTokens, OutputTokens: &outputTokens, TotalTokens: &totalTokens,
			ResponsePath: filepath.Join(root, "control", "response.md"), TracePath: filepath.Join(root, "control", "trace.jsonl"), StderrPath: filepath.Join(root, "control", "stderr.txt"),
			Grade: DeterministicGrade{Status: DeterministicFail, AllPassed: false, PendingRubrics: 1, Results: []GraderResult{{Type: "regex", Passed: false, Evidence: `response did not match pattern "\\bBlue\\b"`}}},
		},
		Treatment: ConditionResult{
			Condition: "treatment", Response: "Blue", ExitCode: 0, Duration: 15 * time.Millisecond,
			RequestedModel: "gpt-5.6-sol", ActualModel: "gpt-5.6-sol", ModelAttested: true,
			InputTokens: &inputTokens, OutputTokens: &outputTokens, TotalTokens: &totalTokens,
			ResponsePath: filepath.Join(root, "treatment", "response.md"), TracePath: filepath.Join(root, "treatment", "trace.jsonl"), StderrPath: filepath.Join(root, "treatment", "stderr.txt"),
			Grade: DeterministicGrade{Status: DeterministicPass, AllPassed: true, PendingRubrics: 1, Results: []GraderResult{{Type: "regex", Passed: true, Evidence: `response matched "Blue"`}}},
		},
	}
	report, err := buildPairReport(root, pair)
	if err != nil {
		t.Fatal(err)
	}
	if !report.RunnerValid || report.Comparison != "treatment_only" || report.RubricStatus != "pending_human_review" || report.Cost != nil {
		t.Fatalf("unexpected report: %+v", report)
	}
	if got := strings.Join(report.ExecutionOrder, " -> "); got != "control -> treatment" {
		t.Fatalf("unexpected execution order: %s", got)
	}
	want, err := os.ReadFile("testdata/report.md")
	if err != nil {
		t.Fatal(err)
	}
	if got := renderPairMarkdown(report); got != string(want) {
		t.Fatalf("markdown differs from golden\n--- got ---\n%s\n--- want ---\n%s", got, want)
	}
}

func TestReportDoesNotPassUnscoredConditions(t *testing.T) {
	root := "/run"
	artifacts := func(condition string) ConditionResult {
		return ConditionResult{
			Condition: condition, ExitCode: 0, ModelAttested: true,
			ResponsePath: filepath.Join(root, condition, "response.md"),
			TracePath:    filepath.Join(root, condition, "trace.jsonl"),
			StderrPath:   filepath.Join(root, condition, "stderr.txt"),
			Grade:        DeterministicGrade{Status: DeterministicNotScored, PendingRubrics: 1},
		}
	}
	report, err := buildPairReport(root, PairResult{ControlSkillAbsent: true, TreatmentSkillPresent: true, TreatmentHashMatches: true, Control: artifacts("control"), Treatment: artifacts("treatment")})
	if err != nil {
		t.Fatal(err)
	}
	if report.Comparison != "not_scored" {
		t.Fatalf("unexpected comparison: %s", report.Comparison)
	}
	for _, condition := range report.Conditions {
		if condition.DeterministicStatus != DeterministicNotScored {
			t.Fatalf("unexpected condition: %+v", condition)
		}
	}
}

func TestReportAcceptsCLIConfiguredModelWhenResolvedIdentityIsUnavailable(t *testing.T) {
	root := "/run"
	condition := func(name string) ConditionResult {
		return ConditionResult{
			Condition: name, ExitCode: 0, RequestedModel: "gpt-5.6-sol",
			ResponsePath: filepath.Join(root, name, "response.md"),
			TracePath:    filepath.Join(root, name, "trace.jsonl"),
			StderrPath:   filepath.Join(root, name, "stderr.txt"),
		}
	}
	report, err := buildPairReport(root, PairResult{
		ControlSkillAbsent: true, TreatmentSkillPresent: true, TreatmentHashMatches: true,
		Control: condition("control"), Treatment: condition("treatment"),
	})
	if err != nil {
		t.Fatal(err)
	}
	if !report.RunnerValid {
		t.Fatalf("CLI-configured run should be valid: %+v", report)
	}
	for _, condition := range report.Conditions {
		if condition.Execution.ModelIdentitySource != "cli_configured" || condition.Execution.ModelMatchesRequested != nil || !condition.Execution.ModelRequirementMet {
			t.Fatalf("configured model evidence is misleading: %+v", condition.Execution)
		}
	}
}

func TestReportRejectsTraceReportedModelMismatch(t *testing.T) {
	root := "/run"
	condition := func(name string) ConditionResult {
		return ConditionResult{
			Condition: name, ExitCode: 0, RequestedModel: "gpt-5.6-sol", ActualModel: "different-model",
			ResponsePath: filepath.Join(root, name, "response.md"),
			TracePath:    filepath.Join(root, name, "trace.jsonl"),
			StderrPath:   filepath.Join(root, name, "stderr.txt"),
		}
	}
	report, err := buildPairReport(root, PairResult{
		ControlSkillAbsent: true, TreatmentSkillPresent: true, TreatmentHashMatches: true,
		Control: condition("control"), Treatment: condition("treatment"),
	})
	if err != nil {
		t.Fatal(err)
	}
	if report.RunnerValid {
		t.Fatal("trace-reported model mismatch passed runner validation")
	}
	for _, condition := range report.Conditions {
		if condition.Execution.ModelIdentitySource != "trace_reported" || condition.Execution.ModelMatchesRequested == nil || *condition.Execution.ModelMatchesRequested || condition.Execution.ModelRequirementMet {
			t.Fatalf("model mismatch evidence is misleading: %+v", condition.Execution)
		}
	}
}

func TestReportRejectsOutsideArtifact(t *testing.T) {
	root := t.TempDir()
	pair := PairResult{TaskID: "bad", Control: ConditionResult{
		ResponsePath: filepath.Join(root, "control", "response.md"), TracePath: filepath.Join(root, "control", "trace.jsonl"), StderrPath: filepath.Join(root, "control", "stderr.txt"),
	}, Treatment: ConditionResult{
		ResponsePath: filepath.Join(root, "treatment", "response.md"), TracePath: filepath.Join(root, "treatment", "trace.jsonl"), StderrPath: filepath.Join(root, "..", "outside.txt"),
	}}
	if _, err := buildPairReport(root, pair); err == nil {
		t.Fatal("report accepted an artifact outside the run")
	}
}
