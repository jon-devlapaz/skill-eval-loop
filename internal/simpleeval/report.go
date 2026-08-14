package simpleeval

import (
	"encoding/json"
	"fmt"
	"html"
	"os"
	"path/filepath"
	"strings"
)

type pairReport struct {
	RunnerValid    bool              `json:"runner_valid"`
	Task           reportTask        `json:"task"`
	Trial          int               `json:"trial"`
	ExecutionOrder []string          `json:"execution_order"`
	Comparison     string            `json:"deterministic_comparison"`
	ReviewStatus   string            `json:"review_status"`
	RubricStatus   string            `json:"rubric_status"`
	Skill          reportSkill       `json:"skill"`
	Isolation      reportIsolation   `json:"isolation"`
	ToolPosture    string            `json:"tool_posture"`
	Cost           *float64          `json:"cost"`
	CostStatus     string            `json:"cost_status"`
	Conditions     []reportCondition `json:"conditions"`
}

type reportTask struct {
	ID      string            `json:"id"`
	Prompt  string            `json:"prompt"`
	Graders []json.RawMessage `json:"graders"`
}

type reportSkill struct {
	Name   string `json:"name"`
	SHA256 string `json:"sha256"`
}

type reportIsolation struct {
	ControlSkillAbsent    bool `json:"control_skill_absent"`
	TreatmentSkillPresent bool `json:"treatment_skill_present"`
	TreatmentHashMatches  bool `json:"treatment_installed_source_hash_match"`
}

type reportCondition struct {
	Name                string              `json:"name"`
	Response            string              `json:"response"`
	DeterministicStatus DeterministicStatus `json:"deterministic_status"`
	PendingRubrics      int                 `json:"pending_rubrics"`
	Graders             []reportGrader      `json:"graders"`
	Execution           reportExecution     `json:"execution"`
	Artifacts           reportArtifacts     `json:"artifacts"`
}

type reportGrader struct {
	Type     string `json:"type"`
	Passed   bool   `json:"passed"`
	Evidence string `json:"evidence"`
}

type reportExecution struct {
	Status                string `json:"status"`
	ExitCode              int    `json:"exit_code"`
	DurationMS            int64  `json:"duration_ms"`
	RequestedModel        string `json:"requested_model"`
	TraceReportedModel    string `json:"trace_reported_model"`
	ModelIdentitySource   string `json:"model_identity_source"`
	ModelMatchesRequested *bool  `json:"model_matches_requested"`
	ModelRequirementMet   bool   `json:"model_requirement_satisfied"`
	InputTokens           *int   `json:"input_tokens"`
	OutputTokens          *int   `json:"output_tokens"`
	TotalTokens           *int   `json:"total_tokens"`
}

type reportArtifacts struct {
	Response string `json:"response"`
	Trace    string `json:"trace"`
	Stderr   string `json:"stderr"`
}

func writePairReport(outputDir string, pair PairResult) (string, string, error) {
	report, err := buildPairReport(outputDir, pair)
	if err != nil {
		return "", "", err
	}
	jsonPath := filepath.Join(outputDir, "report.json")
	markdownPath := filepath.Join(outputDir, "report.md")
	data, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		return "", "", err
	}
	if err := os.WriteFile(jsonPath, append(data, '\n'), 0o644); err != nil {
		return "", "", err
	}
	if err := os.WriteFile(markdownPath, []byte(renderPairMarkdown(report)), 0o644); err != nil {
		return "", "", err
	}
	return jsonPath, markdownPath, nil
}

func buildPairReport(outputDir string, pair PairResult) (pairReport, error) {
	graders, err := graderDefinitions(pair.Task.Graders)
	if err != nil {
		return pairReport{}, err
	}
	control, err := conditionForReport(outputDir, pair.Control)
	if err != nil {
		return pairReport{}, err
	}
	treatment, err := conditionForReport(outputDir, pair.Treatment)
	if err != nil {
		return pairReport{}, err
	}
	pending := control.PendingRubrics + treatment.PendingRubrics
	rubricStatus := "not_required"
	if pending > 0 {
		rubricStatus = "pending_human_review"
	}
	return pairReport{
		RunnerValid: conditionValid(control) && conditionValid(treatment) && pair.ControlSkillAbsent && pair.TreatmentSkillPresent && pair.TreatmentHashMatches,
		Task:        reportTask{ID: pair.TaskID, Prompt: pair.Task.Prompt, Graders: graders}, Trial: pair.Trial,
		ExecutionOrder: append([]string(nil), pair.ExecutionOrder...),
		Comparison:     deterministicComparison(control.DeterministicStatus, treatment.DeterministicStatus),
		ReviewStatus:   "human_transcript_review_required", RubricStatus: rubricStatus,
		Skill: reportSkill{Name: pair.SkillName, SHA256: pair.SkillSHA256},
		Isolation: reportIsolation{
			ControlSkillAbsent: pair.ControlSkillAbsent, TreatmentSkillPresent: pair.TreatmentSkillPresent,
			TreatmentHashMatches: pair.TreatmentHashMatches,
		},
		ToolPosture: pair.ToolPosture, Cost: nil, CostStatus: "unknown",
		Conditions: []reportCondition{control, treatment},
	}, nil
}

func graderDefinitions(graders []Grader) ([]json.RawMessage, error) {
	definitions := make([]json.RawMessage, 0, len(graders))
	for _, grader := range graders {
		if len(grader.Raw) > 0 {
			if !json.Valid(grader.Raw) {
				return nil, fmt.Errorf("grader definition is invalid JSON")
			}
			definitions = append(definitions, append(json.RawMessage(nil), grader.Raw...))
			continue
		}
		definition := map[string]any{"type": grader.Type}
		switch grader.Type {
		case "regex", "not_regex":
			definition["pattern"] = grader.Pattern
		case "file_exists":
			definition["path"] = grader.Path
		case "json_equal":
			definition["path"] = grader.Path
			definition["expected"] = grader.Expected
		case "rubric":
			definition["text"] = grader.Text
		}
		encoded, err := json.Marshal(definition)
		if err != nil {
			return nil, err
		}
		definitions = append(definitions, encoded)
	}
	return definitions, nil
}

func conditionForReport(outputDir string, result ConditionResult) (reportCondition, error) {
	artifacts := reportArtifacts{}
	for path, destination := range map[string]*string{
		result.ResponsePath: &artifacts.Response,
		result.TracePath:    &artifacts.Trace,
		result.StderrPath:   &artifacts.Stderr,
	} {
		relative, err := filepath.Rel(outputDir, path)
		if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			return reportCondition{}, fmt.Errorf("artifact is outside report directory: %s", path)
		}
		*destination = filepath.ToSlash(relative)
	}
	graders := make([]reportGrader, 0, len(result.Grade.Results))
	for _, grader := range result.Grade.Results {
		graders = append(graders, reportGrader{Type: grader.Type, Passed: grader.Passed, Evidence: grader.Evidence})
	}
	identitySource := "cli_configured"
	var modelMatchesRequested *bool
	if result.ActualModel != "" {
		identitySource = "trace_reported"
		matches := result.ModelAttested
		modelMatchesRequested = &matches
	}
	return reportCondition{
		Name: result.Condition, Response: result.Response,
		DeterministicStatus: result.Grade.Status, PendingRubrics: result.Grade.PendingRubrics,
		Graders: graders,
		Execution: reportExecution{
			Status: executionStatus(result), ExitCode: result.ExitCode,
			DurationMS: result.Duration.Milliseconds(), RequestedModel: result.RequestedModel,
			TraceReportedModel: result.ActualModel, ModelIdentitySource: identitySource,
			ModelMatchesRequested: modelMatchesRequested, ModelRequirementMet: result.modelRequirementSatisfied(),
			InputTokens: result.InputTokens, OutputTokens: result.OutputTokens, TotalTokens: result.TotalTokens,
		},
		Artifacts: artifacts,
	}, nil
}

func conditionValid(condition reportCondition) bool {
	return condition.Execution.Status == "completed" && condition.Execution.ModelRequirementMet
}

func executionStatus(result ConditionResult) string {
	if result.TimedOut {
		return "timed_out"
	}
	if result.ExitCode != 0 {
		return "failed"
	}
	return "completed"
}

func deterministicComparison(control, treatment DeterministicStatus) string {
	if control == DeterministicNotScored || treatment == DeterministicNotScored {
		return "not_scored"
	}
	switch {
	case control == DeterministicPass && treatment == DeterministicPass:
		return "both_pass"
	case control == DeterministicFail && treatment == DeterministicPass:
		return "treatment_only"
	case control == DeterministicPass && treatment == DeterministicFail:
		return "control_only"
	default:
		return "both_fail"
	}
}

func renderPairMarkdown(report pairReport) string {
	var output strings.Builder
	fmt.Fprintf(&output, "# Skill evaluation: %s\n\n", report.Task.ID)
	fmt.Fprintf(&output, "- Runner valid: **%t**\n", report.RunnerValid)
	fmt.Fprintf(&output, "- Trial: **%d**\n", report.Trial)
	fmt.Fprintf(&output, "- Execution order: **%s**\n", strings.Join(report.ExecutionOrder, " → "))
	fmt.Fprintf(&output, "- Deterministic comparison: **%s**\n", report.Comparison)
	fmt.Fprintf(&output, "- Review status: **%s**\n", report.ReviewStatus)
	fmt.Fprintf(&output, "- Rubric status: **%s**\n", report.RubricStatus)
	fmt.Fprintf(&output, "- Skill: `%s` (`%s`)\n", report.Skill.Name, report.Skill.SHA256)
	fmt.Fprintf(&output, "- Control target skill absent: **%t**\n", report.Isolation.ControlSkillAbsent)
	fmt.Fprintf(&output, "- Treatment target skill present: **%t**\n", report.Isolation.TreatmentSkillPresent)
	fmt.Fprintf(&output, "- Treatment installed/source hash match: **%t**\n", report.Isolation.TreatmentHashMatches)
	fmt.Fprintf(&output, "- Tool posture: `%s`\n", report.ToolPosture)
	fmt.Fprintf(&output, "- Cost: **unknown**\n\n")
	output.WriteString("A valid runner result is not a general skill-quality claim. Read both transcripts before interpreting the comparison.\n\n")
	fmt.Fprintf(&output, "## Task prompt\n\n<pre>%s</pre>\n\n", html.EscapeString(report.Task.Prompt))
	graderJSON, _ := json.MarshalIndent(report.Task.Graders, "", "  ")
	fmt.Fprintf(&output, "## Grader definitions\n\n<pre>%s</pre>\n\n", html.EscapeString(string(graderJSON)))
	output.WriteString("| Condition | Deterministic | Rubrics | Execution | Model | Tokens | Duration | Evidence |\n")
	output.WriteString("|---|---:|---:|---|---|---:|---:|---|\n")
	for _, condition := range report.Conditions {
		model := condition.Execution.TraceReportedModel
		match := "resolved identity unavailable"
		if condition.Execution.ModelMatchesRequested != nil {
			match = "does not match requested"
			if *condition.Execution.ModelMatchesRequested {
				match = "matches requested"
			}
		}
		if model == "" {
			model = condition.Execution.RequestedModel
		}
		fmt.Fprintf(&output, "| %s | %s | %d pending | %s (exit %d) | %s (%s; %s) | %s | %d ms | [response](%s) · [trace](%s) · [stderr](%s) |\n",
			condition.Name, condition.DeterministicStatus, condition.PendingRubrics,
			condition.Execution.Status, condition.Execution.ExitCode, model,
			condition.Execution.ModelIdentitySource, match, displayInt(condition.Execution.TotalTokens),
			condition.Execution.DurationMS, condition.Artifacts.Response, condition.Artifacts.Trace, condition.Artifacts.Stderr)
	}
	for _, condition := range report.Conditions {
		fmt.Fprintf(&output, "\n## %s response\n\n[Open raw response](%s)\n\n<pre>%s</pre>\n", title(condition.Name), condition.Artifacts.Response, html.EscapeString(condition.Response))
		output.WriteString("\n### Deterministic graders\n\n")
		output.WriteString("| Grader | Passed | Evidence |\n|---|---:|---|\n")
		for _, grader := range condition.Graders {
			fmt.Fprintf(&output, "| %s | %t | %s |\n", markdownCell(grader.Type), grader.Passed, markdownCell(grader.Evidence))
		}
		if len(condition.Graders) == 0 {
			output.WriteString("| none | — | No deterministic graders declared; status is not_scored |\n")
		}
		if condition.PendingRubrics > 0 {
			fmt.Fprintf(&output, "\n%d rubric grader(s) require human review; no judge model was called.\n", condition.PendingRubrics)
		}
	}
	return output.String()
}

func title(value string) string {
	if value == "" {
		return value
	}
	return strings.ToUpper(value[:1]) + value[1:]
}

func displayInt(value *int) string {
	if value == nil {
		return "unknown"
	}
	return fmt.Sprintf("%d", *value)
}

func markdownCell(value string) string {
	value = strings.ReplaceAll(value, "|", "\\|")
	return strings.ReplaceAll(value, "\n", "<br>")
}
