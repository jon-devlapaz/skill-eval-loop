package audit

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/jon-devlapaz/skill-eval-loop/internal/evalspec"
)

type Report struct {
	Valid                bool
	Errors               []string
	Details              []string
	SchemaVersion        any
	SkillName            string
	SuiteType            string
	DatasetOrigin        string
	ActivationMode       string
	CaseCount            int
	RoutingClasses       []string
	GraderDiscrimination *DiscriminationSummary
	ProvenanceCaseCount  int
}

type DiscriminationSummary struct {
	Claim                       string `json:"claim"`
	ContrastCaseCount           int    `json:"contrast_case_count"`
	ResponseSensitiveGraders    int    `json:"response_sensitive_grader_count"`
	DeterministicGradersChecked int    `json:"deterministic_graders_checked"`
	ModelGradersPendingRuntime  int    `json:"model_graders_pending_runtime"`
}

func Run(skillPath, evalsPath string) Report {
	suite, err := evalspec.Load(skillPath, evalsPath)
	if err != nil {
		message := err.Error()
		return Report{Valid: false, Errors: []string{errorCode(message)}, Details: []string{message}}
	}
	routingSet := map[string]bool{}
	summary := &DiscriminationSummary{Claim: suite.GraderDiscrimination}
	for _, current := range suite.Cases {
		if current.RoutingClass != "" {
			routingSet[current.RoutingClass] = true
		}
		if suite.GraderDiscrimination == "case_contrast" && current.HasCounterReference && current.Discrimination.ResponseSensitiveGraders > 0 {
			summary.ContrastCaseCount++
		}
		summary.ResponseSensitiveGraders += current.Discrimination.ResponseSensitiveGraders
		summary.DeterministicGradersChecked += current.Discrimination.DeterministicGradersChecked
		summary.ModelGradersPendingRuntime += current.Discrimination.ModelGradersPendingRuntime
	}
	routing := make([]string, 0, len(routingSet))
	for value := range routingSet {
		routing = append(routing, value)
	}
	sortStrings(routing)
	return Report{
		Valid: true, Errors: []string{}, SchemaVersion: suite.SchemaVersion,
		SkillName: suite.SkillName, SuiteType: suite.SuiteType,
		DatasetOrigin: suite.DatasetOrigin, ActivationMode: suite.ActivationMode,
		CaseCount: len(suite.Cases), RoutingClasses: routing,
		GraderDiscrimination: summary, ProvenanceCaseCount: len(suite.ProvenanceRecords),
	}
}

func Write(report Report, output string) error {
	data, err := Bytes(report)
	if err != nil {
		return err
	}
	if output == "" {
		_, err = os.Stdout.Write(data)
		return err
	}
	return os.WriteFile(output, data, 0o666)
}

func Bytes(report Report) ([]byte, error) {
	var value any
	if !report.Valid {
		value = struct {
			Valid   bool     `json:"valid"`
			Errors  []string `json:"errors"`
			Details []string `json:"details"`
		}{report.Valid, report.Errors, report.Details}
	} else {
		value = struct {
			Valid                bool                   `json:"valid"`
			Errors               []string               `json:"errors"`
			SchemaVersion        any                    `json:"schema_version"`
			SkillName            string                 `json:"skill_name"`
			SuiteType            string                 `json:"suite_type"`
			DatasetOrigin        string                 `json:"dataset_origin"`
			ActivationMode       string                 `json:"activation_mode"`
			CaseCount            int                    `json:"case_count"`
			RoutingClasses       []string               `json:"routing_classes"`
			GraderDiscrimination *DiscriminationSummary `json:"grader_discrimination"`
			ProvenanceCaseCount  int                    `json:"provenance_case_count"`
		}{report.Valid, report.Errors, report.SchemaVersion, report.SkillName,
			report.SuiteType, report.DatasetOrigin, report.ActivationMode,
			report.CaseCount, report.RoutingClasses, report.GraderDiscrimination,
			report.ProvenanceCaseCount}
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(data, '\n'), nil
}

func errorCode(message string) string {
	rules := []struct{ needle, code string }{
		{"counter_reference is required", "missing_grader_contrast"},
		{"grader contrast", "non_discriminating_grader_contrast"},
		{"counter_reference", "invalid_grader_contrast"},
		{"artifact_sha256 does not match artifact", "provenance_hash_mismatch"},
		{"case_sha256 does not match eval case", "provenance_case_mismatch"},
		{"suite_sha256 does not match eval suite", "provenance_suite_mismatch"},
		{"should_trigger requires", "routing_loading_policy_conflict"},
		{"should_not_trigger requires", "routing_loading_policy_conflict"},
		{"ambiguous routing must declare", "routing_loading_policy_conflict"},
		{"does not cover every eval case", "provenance_coverage_mismatch"},
		{"distribution_policy", "invalid_legacy_policy"},
		{"provenance_manifest", "invalid_provenance_manifest"},
	}
	for _, rule := range rules {
		if strings.Contains(message, rule.needle) {
			return rule.code
		}
	}
	return "invalid_eval_suite"
}

func sortStrings(values []string) {
	for index := 1; index < len(values); index++ {
		for current := index; current > 0 && values[current] < values[current-1]; current-- {
			values[current], values[current-1] = values[current-1], values[current]
		}
	}
}

func ExitCode(report Report) int {
	if report.Valid {
		return 0
	}
	return 1
}

func FormatError(err error) string { return fmt.Sprintf("ERROR: %v", err) }
