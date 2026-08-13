package aggregate

import (
	"encoding/json"

	"github.com/jon-devlapaz/skill-eval-loop/internal/evalspec"
)

type benchmarkOutput struct {
	SchemaVersion              int                  `json:"schema_version"`
	SkillName                  string               `json:"skill_name"`
	Verdict                    string               `json:"verdict"`
	OutcomeVerdict             string               `json:"outcome_verdict"`
	Valid                      bool                 `json:"valid"`
	ArtifactValid              bool                 `json:"artifact_valid"`
	MechanismValid             bool                 `json:"mechanism_valid"`
	RuntimeAttestationComplete bool                 `json:"runtime_attestation_complete"`
	ActivationMode             string               `json:"activation_mode"`
	GraderDiscrimination       discriminationOutput `json:"grader_discrimination"`
	SelectionVerdict           string               `json:"selection_verdict"`
	InvalidReasons             []string             `json:"invalid_reasons"`
	MechanismGaps              []string             `json:"mechanism_gaps"`
	RuntimeAttestationGaps     []string             `json:"runtime_attestation_gaps"`
	PairCount                  int                  `json:"pair_count"`
	TaskSuccess                taskSuccessOutput    `json:"task_success"`
	GraderOutcomes             []graderOutcome      `json:"grader_outcomes"`
	Routing                    routingOutput        `json:"routing"`
	Operations                 operationsOutput     `json:"operations"`
	Limits                     []string             `json:"limits"`
}
type discriminationOutput struct {
	Claim     string `json:"claim"`
	Validated bool   `json:"validated"`
}
type conditionSuccess struct {
	Passed int                  `json:"passed"`
	Rate   evalspec.PythonFloat `json:"rate"`
}
type pairOutcomesOutput struct {
	Improved  int `json:"improved"`
	Regressed int `json:"regressed"`
	TiedPass  int `json:"tied_pass"`
	TiedFail  int `json:"tied_fail"`
}
type taskSuccessOutput struct {
	WithoutSkill conditionSuccess     `json:"without_skill"`
	WithSkill    conditionSuccess     `json:"with_skill"`
	Delta        evalspec.PythonFloat `json:"delta"`
	PairOutcomes pairOutcomesOutput   `json:"pair_outcomes"`
}
type graderOutcome struct {
	CaseID       string               `json:"case_id"`
	Grader       string               `json:"grader"`
	WithoutSkill graderSuccessOutput  `json:"without_skill"`
	WithSkill    graderSuccessOutput  `json:"with_skill"`
	Delta        evalspec.PythonFloat `json:"delta"`
	Pattern      string               `json:"pattern"`
}
type graderSuccessOutput struct {
	Passed int                  `json:"passed"`
	Total  int                  `json:"total"`
	Rate   evalspec.PythonFloat `json:"rate"`
}
type routingOutput struct {
	ExpectedInjections int                   `json:"expected_injections"`
	Available          int                   `json:"available"`
	InjectionAttested  int                   `json:"injection_attested"`
	ExplicitAccesses   int                   `json:"explicit_accesses"`
	ControlExposures   int                   `json:"control_exposures"`
	DecisionsScored    int                   `json:"decisions_scored"`
	DecisionsCorrect   int                   `json:"decisions_correct"`
	FalsePositives     int                   `json:"false_positives"`
	FalseNegatives     int                   `json:"false_negatives"`
	Accuracy           *evalspec.PythonFloat `json:"accuracy"`
}
type coverageOutput struct {
	Reported *int `json:"reported"`
	Expected *int `json:"expected"`
}
type operationOutput struct {
	Errors         int                   `json:"errors"`
	Timeouts       int                   `json:"timeouts"`
	Tokens         *int                  `json:"tokens"`
	Cost           *evalspec.PythonFloat `json:"cost"`
	TokensCoverage coverageOutput        `json:"tokens_coverage"`
	CostCoverage   coverageOutput        `json:"cost_coverage"`
}
type usageOutput struct {
	Tokens         *int                  `json:"tokens"`
	Cost           *evalspec.PythonFloat `json:"cost"`
	TokensCoverage coverageOutput        `json:"tokens_coverage"`
	CostCoverage   coverageOutput        `json:"cost_coverage"`
}
type operationsOutput struct {
	WithoutSkill      operationOutput `json:"without_skill"`
	WithSkill         operationOutput `json:"with_skill"`
	ConditionJudges   usageOutput     `json:"condition_judges"`
	References        usageOutput     `json:"references"`
	CounterReferences usageOutput     `json:"counter_references"`
	Full              usageOutput     `json:"full"`
}

func Bytes(report map[string]any) ([]byte, error) {
	raw, err := json.Marshal(report)
	if err != nil {
		return nil, err
	}
	var ordered benchmarkOutput
	if err := json.Unmarshal(raw, &ordered); err != nil {
		return nil, err
	}
	data, err := json.MarshalIndent(ordered, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(data, '\n'), nil
}
