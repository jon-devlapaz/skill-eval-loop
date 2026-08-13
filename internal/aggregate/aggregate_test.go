package aggregate

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/jon-devlapaz/skill-eval-loop/internal/skillpayload"
)

func TestAggregateSmallestValidRetainedPythonRun(t *testing.T) {
	run := retainedRun(t)
	report, err := Run(run)
	if err != nil {
		t.Fatal(err)
	}
	if report["valid"] != true || report["verdict"] != "improved" || report["pair_count"] != 1 {
		t.Fatalf("report = %#v", report)
	}
	task := report["task_success"].(map[string]any)
	if task["delta"] != 1.0 {
		t.Fatalf("task_success = %#v", task)
	}
}

func TestAggregateReportsPerGraderMovementWithoutChangingCaseVerdict(t *testing.T) {
	run := retainedRun(t)
	setConditionGrading(t, run, "without_skill", []graderExpectation{
		{name: "always passes", passed: true},
		{name: "treatment improvement", passed: false},
		{name: "always fails", passed: false},
	})
	setConditionGrading(t, run, "with_skill", []graderExpectation{
		{name: "always fails", passed: false},
		{name: "treatment improvement", passed: true},
		{name: "always passes", passed: true},
	})

	report, err := Run(run)
	if err != nil {
		t.Fatal(err)
	}
	if report["outcome_verdict"] != "no_difference" {
		t.Fatalf("outcome_verdict = %v", report["outcome_verdict"])
	}
	outcomes, ok := report["grader_outcomes"].([]any)
	if !ok || len(outcomes) != 3 {
		t.Fatalf("grader_outcomes = %#v", report["grader_outcomes"])
	}
	expected := []struct {
		name, pattern string
		without, with int
	}{
		{name: "always passes", pattern: "both_pass", without: 1, with: 1},
		{name: "treatment improvement", pattern: "treatment_only", without: 0, with: 1},
		{name: "always fails", pattern: "both_fail", without: 0, with: 0},
	}
	for index, want := range expected {
		outcome, ok := outcomes[index].(map[string]any)
		if !ok {
			t.Fatalf("grader_outcomes[%d] = %#v", index, outcomes[index])
		}
		without := outcome["without_skill"].(map[string]any)
		with := outcome["with_skill"].(map[string]any)
		if outcome["case_id"] != "case-one" || outcome["grader"] != want.name || outcome["pattern"] != want.pattern || without["passed"] != want.without || with["passed"] != want.with {
			t.Fatalf("grader_outcomes[%d] = %#v", index, outcome)
		}
	}
}

func TestAggregateReportsVariablePerGraderOutcomeAcrossTrials(t *testing.T) {
	run := retainedRun(t)
	addTrial(t, run, 2, true, false)

	report, err := Run(run)
	if err != nil {
		t.Fatal(err)
	}
	outcomes := report["grader_outcomes"].([]any)
	outcome := outcomes[0].(map[string]any)
	without := outcome["without_skill"].(map[string]any)
	with := outcome["with_skill"].(map[string]any)
	if outcome["pattern"] != "variable" || outcome["delta"] != 0.0 || without["passed"] != 1 || without["total"] != 2 || without["rate"] != 0.5 || with["passed"] != 1 || with["total"] != 2 || with["rate"] != 0.5 {
		t.Fatalf("grader_outcomes[0] = %#v", outcome)
	}
}

func TestRound3HandlesNegativeValues(t *testing.T) {
	tests := []struct {
		value, expected float64
	}{
		{value: -1, expected: -1},
		{value: -1.0 / 3.0, expected: -0.333},
		{value: 1.0 / 3.0, expected: 0.333},
	}
	for _, current := range tests {
		if actual := round3(current.value); actual != current.expected {
			t.Errorf("round3(%v) = %v, want %v", current.value, actual, current.expected)
		}
	}
}

func TestAggregateReportsControlOnlyGraderOutcome(t *testing.T) {
	run := retainedRun(t)
	setConditionGrading(t, run, "without_skill", []graderExpectation{{name: "control advantage", passed: true}})
	setConditionGrading(t, run, "with_skill", []graderExpectation{{name: "control advantage", passed: false}})

	report, err := Run(run)
	if err != nil {
		t.Fatal(err)
	}
	outcome := report["grader_outcomes"].([]any)[0].(map[string]any)
	if outcome["pattern"] != "control_only" || outcome["delta"] != -1.0 {
		t.Fatalf("grader_outcomes[0] = %#v", outcome)
	}
}

func TestAggregateRoundsNegativeRepeatingGraderDelta(t *testing.T) {
	run := retainedRun(t)
	addTrial(t, run, 2, true, false)
	addTrial(t, run, 3, true, false)

	report, err := Run(run)
	if err != nil {
		t.Fatal(err)
	}
	outcome := report["grader_outcomes"].([]any)[0].(map[string]any)
	if outcome["pattern"] != "variable" || outcome["delta"] != -0.333 {
		t.Fatalf("grader_outcomes[0] = %#v", outcome)
	}
}

func TestAggregateRejectsMismatchedGraderSet(t *testing.T) {
	run := retainedRun(t)
	setConditionGrading(t, run, "with_skill", []graderExpectation{{name: "renamed", passed: true}})

	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "grader set does not match other target conditions") {
		t.Fatalf("error = %v", err)
	}
}

func TestAggregateRejectsDuplicateGraderName(t *testing.T) {
	run := retainedRun(t)
	setConditionGrading(t, run, "with_skill", []graderExpectation{
		{name: "duplicate", passed: true},
		{name: "duplicate", passed: false},
	})

	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "expectation name missing or duplicate") {
		t.Fatalf("error = %v", err)
	}
}

func TestAggregateRejectsMissingOrExtraGrader(t *testing.T) {
	tests := []struct {
		name      string
		treatment []graderExpectation
	}{
		{name: "missing", treatment: []graderExpectation{{name: "first", passed: true}}},
		{name: "extra", treatment: []graderExpectation{{name: "first", passed: true}, {name: "second", passed: true}, {name: "third", passed: true}}},
	}
	for _, current := range tests {
		t.Run(current.name, func(t *testing.T) {
			run := retainedRun(t)
			setConditionGrading(t, run, "without_skill", []graderExpectation{{name: "first", passed: true}, {name: "second", passed: false}})
			setConditionGrading(t, run, "with_skill", current.treatment)

			_, err := Run(run)
			if err == nil || !strings.Contains(err.Error(), "grader set does not match other target conditions") {
				t.Fatalf("error = %v", err)
			}
		})
	}
}

func TestAggregateRejectsResponseHashMutation(t *testing.T) {
	run := retainedRun(t)
	path := filepath.Join(run, "eval-case-one", "trial-001", "with_skill", "outputs", "response.md")
	if err := os.WriteFile(path, []byte("tampered\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "response_sha256 does not match") {
		t.Fatalf("error = %v", err)
	}
}

func TestAggregateRejectsArtifactSymlinkEscape(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("migration target is macOS/Linux")
	}
	run := retainedRun(t)
	outside := filepath.Join(t.TempDir(), "response.md")
	if err := os.WriteFile(outside, []byte("ok\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(run, "eval-case-one", "trial-001", "with_skill", "outputs", "response.md")
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, path); err != nil {
		t.Fatal(err)
	}
	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "escapes the run") {
		t.Fatalf("error = %v", err)
	}
}

func TestAggregateMatchesFrozenContract(t *testing.T) {
	run := retainedRun(t)
	report, err := Run(run)
	if err != nil {
		t.Fatal(err)
	}
	data, err := Bytes(report)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(data), "{\n  \"schema_version\": 2,\n  \"skill_name\": \"fixture-skill\",") || !strings.HasSuffix(string(data), "\n}\n") {
		t.Fatalf("unexpected benchmark rendering:\n%s", data)
	}
	taskIndex := strings.Index(string(data), `"task_success"`)
	graderIndex := strings.Index(string(data), `"grader_outcomes"`)
	routingIndex := strings.Index(string(data), `"routing"`)
	if taskIndex < 0 || graderIndex < taskIndex || routingIndex < graderIndex {
		t.Fatalf("grader outcomes are not rendered in contract order:\n%s", data)
	}
}

func TestAggregateAccountingMetadataMatchesFrozenPython(t *testing.T) {
	run := retainedRun(t)
	suitePath := filepath.Join(run, "suite_snapshot.json")
	var suite map[string]any
	data, err := os.ReadFile(suitePath)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(data, &suite); err != nil {
		t.Fatal(err)
	}
	caseValue := suite["cases"].([]any)[0].(map[string]any)
	caseValue["model_rubric_count"] = 0
	caseValue["counter_reference_declared"] = false
	writeJSON(t, suitePath, suite)
	manifestPath := filepath.Join(run, "run_manifest.json")
	data, err = os.ReadFile(manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	var manifest map[string]any
	if err = json.Unmarshal(data, &manifest); err != nil {
		t.Fatal(err)
	}
	manifest["suite_sha256"] = testFileHash(t, suitePath)
	manifest["reference_validation"] = []any{map[string]any{
		"case_id": "case-one", "valid": true, "judge_records": []any{},
		"grading": map[string]any{"expectations": []any{map[string]any{"text": "contains ok", "passed": true, "grader": "response_contains"}}, "summary": map[string]any{"passed": 1, "failed": 0, "total": 1, "pass_rate": 1.0}},
	}}
	writeJSON(t, manifestPath, manifest)
	if _, err := Run(run); err != nil {
		t.Fatal(err)
	}
}

func TestAggregateRejectsIncompleteCaseTrialMatrix(t *testing.T) {
	run := retainedRun(t)
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) { value["trials"] = []any{} })
	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "trials must be a non-empty list") {
		t.Fatalf("error=%v", err)
	}
}

func TestAggregateRejectsUnknownCaseInTrialMatrix(t *testing.T) {
	run := retainedRun(t)
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) {
		pair := value["trials"].([]any)[0].(map[string]any)
		pair["case_id"] = "unknown-case"
		for _, raw := range pair["conditions"].(map[string]any) {
			raw.(map[string]any)["case_id"] = "unknown-case"
		}
	})

	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "complete case/trial matrix") {
		t.Fatalf("error = %v", err)
	}
}

func TestAggregateRejectsOutOfRangeTrialInMatrix(t *testing.T) {
	run := retainedRun(t)
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) {
		pair := value["trials"].([]any)[0].(map[string]any)
		pair["trial"] = 2
		for _, raw := range pair["conditions"].(map[string]any) {
			raw.(map[string]any)["trial"] = 2
		}
	})

	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "complete case/trial matrix") {
		t.Fatalf("error = %v", err)
	}
}

func TestAggregateRejectsSkewedTwoCaseTrialMatrix(t *testing.T) {
	run := retainedRun(t)
	addTrial(t, run, 2, false, true)
	mutateObject(t, filepath.Join(run, "suite_snapshot.json"), func(value map[string]any) {
		value["cases"] = append(value["cases"].([]any), map[string]any{"id": "case-two"})
	})
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) {
		value["suite_sha256"] = testFileHash(t, filepath.Join(run, "suite_snapshot.json"))
		trials := value["trials"].([]any)
		caseOneTrialThree := cloneObject(t, trials[1].(map[string]any))
		caseOneTrialThree["trial"] = 3
		for _, raw := range caseOneTrialThree["conditions"].(map[string]any) {
			raw.(map[string]any)["trial"] = 3
		}
		caseTwoTrialOne := cloneObject(t, trials[0].(map[string]any))
		caseTwoTrialOne["case_id"] = "case-two"
		for _, raw := range caseTwoTrialOne["conditions"].(map[string]any) {
			raw.(map[string]any)["case_id"] = "case-two"
		}
		value["pair_count"] = 4
		value["trials"] = []any{trials[0], trials[1], caseOneTrialThree, caseTwoTrialOne}
	})

	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "complete case/trial matrix") {
		t.Fatalf("error = %v", err)
	}
}

func TestAggregateRejectsConditionIdentityMismatch(t *testing.T) {
	run := retainedRun(t)
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) {
		pair := value["trials"].([]any)[0].(map[string]any)
		conditions := pair["conditions"].(map[string]any)
		conditions["with_skill"].(map[string]any)["case_id"] = "different"
	})
	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "does not match its enclosing pair") {
		t.Fatalf("error=%v", err)
	}
}

func TestAggregateRejectsInconsistentGradingSummaryAfterRehash(t *testing.T) {
	run := retainedRun(t)
	gradingPath := filepath.Join(run, "eval-case-one", "trial-001", "with_skill", "grading.json")
	mutateObject(t, gradingPath, func(value map[string]any) { value["summary"].(map[string]any)["passed"] = 0 })
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) {
		pair := value["trials"].([]any)[0].(map[string]any)
		conditions := pair["conditions"].(map[string]any)
		conditions["with_skill"].(map[string]any)["grading_sha256"] = testFileHash(t, gradingPath)
	})
	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "summary is inconsistent") {
		t.Fatalf("error=%v", err)
	}
}

func TestAggregateRetainedProvenanceMatchesPythonAndRejectsMutation(t *testing.T) {
	run := retainedRun(t)
	artifact := filepath.Join(run, "provenance", "case-one.json")
	writeFile(t, artifact, []byte("{\"source\":true}\n"))
	snapshotPath := filepath.Join(run, "provenance_snapshot.json")
	writeJSON(t, snapshotPath, map[string]any{"schema_version": 1, "source_manifest_sha256": strings.Repeat("1", 64), "cases": []any{map[string]any{"case_id": "case-one", "retained_artifact_path": "provenance/case-one.json", "retained_artifact_sha256": testFileHash(t, artifact)}}})
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) {
		value["provenance_path"] = "provenance_snapshot.json"
		value["provenance_sha256"] = testFileHash(t, snapshotPath)
	})
	if _, err := Run(run); err != nil {
		t.Fatal(err)
	}
	writeFile(t, artifact, []byte("tampered\n"))
	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "retained artifact hash does not match") {
		t.Fatalf("error=%v", err)
	}
}

func retainedRun(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	suite := map[string]any{
		"schema_version": 2, "skill_name": "fixture-skill", "suite_type": "capability",
		"dataset_origin": "author_derived", "tool_profile": "no_tools", "activation_mode": "forced",
		"grader_discrimination": "none", "source_sha256": strings.Repeat("0", 64),
		"cases": []any{map[string]any{"id": "case-one"}},
	}
	writeJSON(t, filepath.Join(root, "suite_snapshot.json"), suite)
	skillDir := writeInstalledSkill(t, root, 1)
	conditions := map[string]any{}
	conditions["without_skill"] = condition(t, root, 1, "without_skill", false, false)
	conditions["with_skill"] = condition(t, root, 1, "with_skill", true, true)
	manifest := map[string]any{
		"schema_version": 1, "target_skill_name": "fixture-skill",
		"skill_sha256": testPayloadHash(t, skillDir), "suite_path": "suite_snapshot.json",
		"suite_sha256":    testFileHash(t, filepath.Join(root, "suite_snapshot.json")),
		"requested_model": "provider/model-fixed", "judge_model": nil,
		"harness": "pi", "activation_mode": "forced", "case_count": 1,
		"trials_per_case": 1, "pair_count": 1, "reference_validation": []any{},
		"execution_order": "counterbalanced_by_trial",
		"trials":          []any{map[string]any{"case_id": "case-one", "trial": 1, "execution_order": []any{"without_skill", "with_skill"}, "conditions": conditions}},
	}
	writeJSON(t, filepath.Join(root, "run_manifest.json"), manifest)
	return root
}

func condition(t *testing.T, root string, trial int, name string, treatment, passed bool) map[string]any {
	t.Helper()
	dir := filepath.Join(root, "eval-case-one", fmt.Sprintf("trial-%03d", trial), name)
	outputs := filepath.Join(dir, "outputs")
	if err := os.MkdirAll(outputs, 0o755); err != nil {
		t.Fatal(err)
	}
	trace := `{"type":"system","subtype":"init","provider":"provider","model":"model-fixed","session_id":"session-1","skills":[]}` + "\n" +
		`{"role":"assistant","provider":"provider","model":"model-fixed","content":"` + map[bool]string{true: "ok", false: "bad"}[passed] + `"}` + "\n"
	if treatment {
		trace = strings.Replace(trace, `"skills":[]`, `"skills":["fixture-skill"]`, 1)
	}
	writeFile(t, filepath.Join(outputs, "trace.jsonl"), []byte(trace))
	writeFile(t, filepath.Join(outputs, "response.md"), []byte(map[bool]string{true: "ok\n", false: "bad\n"}[passed]))
	grading := map[string]any{"grader": map[string]any{"kind": "deterministic_mixed", "schema_version": 2}, "expectations": []any{map[string]any{"text": "contains ok", "passed": passed, "evidence": "fixture", "grader": "response_contains"}}, "summary": map[string]any{"passed": map[bool]int{true: 1, false: 0}[passed], "failed": map[bool]int{true: 0, false: 1}[passed], "total": 1, "pass_rate": map[bool]float64{true: 1, false: 0}[passed]}}
	writeJSON(t, filepath.Join(dir, "grading.json"), grading)
	record := map[string]any{"case_id": "case-one", "trial": trial, "condition": name, "exit_code": 0, "timed_out": false, "requested_model": "provider/model-fixed", "actual_model": "provider/model-fixed", "judge_records": []any{}, "trace_path": rel(root, filepath.Join(outputs, "trace.jsonl")), "trace_sha256": testFileHash(t, filepath.Join(outputs, "trace.jsonl")), "response_path": rel(root, filepath.Join(outputs, "response.md")), "response_sha256": testFileHash(t, filepath.Join(outputs, "response.md")), "grading_path": rel(root, filepath.Join(dir, "grading.json")), "grading_sha256": testFileHash(t, filepath.Join(dir, "grading.json")), "duration_seconds": 0.1, "total_tokens": nil, "cost": nil, "available_skills": []any{}, "skill_activation": "none", "expected_skill_loading": "forbidden", "installed_skill_path": ""}
	if treatment {
		record["available_skills"] = []any{"fixture-skill"}
		record["skill_activation"] = "forced_command"
		record["expected_skill_loading"] = "required"
		record["installed_skill_path"] = rel(root, filepath.Join(dir, "installed-skill", "fixture-skill"))
	}
	return record
}

func writeInstalledSkill(t *testing.T, root string, trial int) string {
	t.Helper()
	skillDir := filepath.Join(root, "eval-case-one", fmt.Sprintf("trial-%03d", trial), "with_skill", "installed-skill", "fixture-skill")
	writeFile(t, filepath.Join(skillDir, "SKILL.md"), []byte("# Fixture\n"))
	writeFile(t, filepath.Join(skillDir, "references", "guide.md"), []byte("supporting guidance\n"))
	return skillDir
}

func addTrial(t *testing.T, run string, trial int, withoutPassed, withPassed bool) {
	t.Helper()
	writeInstalledSkill(t, run, trial)
	conditions := map[string]any{
		"without_skill": condition(t, run, trial, "without_skill", false, withoutPassed),
		"with_skill":    condition(t, run, trial, "with_skill", true, withPassed),
	}
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) {
		value["trials_per_case"] = trial
		value["pair_count"] = trial
		value["trials"] = append(value["trials"].([]any), map[string]any{"case_id": "case-one", "trial": trial, "execution_order": []any{"with_skill", "without_skill"}, "conditions": conditions})
	})
}

type graderExpectation struct {
	name   string
	passed bool
}

func setConditionGrading(t *testing.T, run, condition string, expectations []graderExpectation) {
	t.Helper()
	gradingPath := filepath.Join(run, "eval-case-one", "trial-001", condition, "grading.json")
	items := make([]any, 0, len(expectations))
	passed := 0
	for _, expectation := range expectations {
		items = append(items, map[string]any{"text": expectation.name, "passed": expectation.passed, "evidence": "fixture", "grader": "response_contains"})
		if expectation.passed {
			passed++
		}
	}
	writeJSON(t, gradingPath, map[string]any{
		"grader":       map[string]any{"kind": "deterministic_mixed", "schema_version": 2},
		"expectations": items,
		"summary":      map[string]any{"passed": passed, "failed": len(items) - passed, "total": len(items), "pass_rate": float64(passed) / float64(len(items))},
	})
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) {
		pair := value["trials"].([]any)[0].(map[string]any)
		conditions := pair["conditions"].(map[string]any)
		conditions[condition].(map[string]any)["grading_sha256"] = testFileHash(t, gradingPath)
	})
}

func writeJSON(t *testing.T, path string, value any) {
	t.Helper()
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	writeFile(t, path, append(data, '\n'))
}
func mutateObject(t *testing.T, path string, mutation func(map[string]any)) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err = json.Unmarshal(data, &value); err != nil {
		t.Fatal(err)
	}
	mutation(value)
	writeJSON(t, path, value)
}
func cloneObject(t *testing.T, value map[string]any) map[string]any {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var cloned map[string]any
	if err = json.Unmarshal(data, &cloned); err != nil {
		t.Fatal(err)
	}
	return cloned
}
func writeFile(t *testing.T, path string, data []byte) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
}
func testFileHash(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}
func testPayloadHash(t *testing.T, root string) string {
	t.Helper()
	hash, err := skillpayload.Hash(root)
	if err != nil {
		t.Fatal(err)
	}
	return hash
}
func rel(root, path string) string {
	value, _ := filepath.Rel(root, path)
	return filepath.ToSlash(value)
}
