package aggregate

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
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

func TestAggregateMatchesFrozenPythonReport(t *testing.T) {
	run := retainedRun(t)
	assertPythonParity(t, run)
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
	assertPythonParity(t, run)
}

func TestAggregateRejectsIncompleteCaseTrialMatrix(t *testing.T) {
	run := retainedRun(t)
	mutateObject(t, filepath.Join(run, "run_manifest.json"), func(value map[string]any) { value["trials"] = []any{} })
	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "trials must be a non-empty list") {
		t.Fatalf("error=%v", err)
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
	assertPythonParity(t, run)
	writeFile(t, artifact, []byte("tampered\n"))
	_, err := Run(run)
	if err == nil || !strings.Contains(err.Error(), "retained artifact hash does not match") {
		t.Fatalf("error=%v", err)
	}
}

func assertPythonParity(t *testing.T, run string) {
	t.Helper()
	goReport, err := Run(run)
	if err != nil {
		t.Fatal(err)
	}
	script, err := filepath.Abs(filepath.Join("..", "..", "skills", "skill-eval-loop", "scripts", "aggregate_benchmark.py"))
	if err != nil {
		t.Fatal(err)
	}
	command := exec.Command("python3", script, "--run-dir", run)
	output, err := command.Output()
	if err != nil {
		t.Fatalf("python aggregate: %v", err)
	}
	var pythonReport map[string]any
	if err := json.Unmarshal(output, &pythonReport); err != nil {
		t.Fatal(err)
	}
	goJSON, _ := json.MarshalIndent(goReport, "", "  ")
	pythonJSON, _ := json.MarshalIndent(pythonReport, "", "  ")
	if string(goJSON) != string(pythonJSON) {
		t.Fatalf("reports differ\npython:\n%s\ngo:\n%s", pythonJSON, goJSON)
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
	skillDir := filepath.Join(root, "eval-case-one", "trial-001", "with_skill", "installed-skill", "fixture-skill")
	if err := os.MkdirAll(skillDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skillDir, "SKILL.md"), []byte("# Fixture\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	conditions := map[string]any{}
	conditions["without_skill"] = condition(t, root, "without_skill", false, false)
	conditions["with_skill"] = condition(t, root, "with_skill", true, true)
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

func condition(t *testing.T, root, name string, treatment, passed bool) map[string]any {
	t.Helper()
	dir := filepath.Join(root, "eval-case-one", "trial-001", name)
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
	record := map[string]any{"case_id": "case-one", "trial": 1, "condition": name, "exit_code": 0, "timed_out": false, "requested_model": "provider/model-fixed", "actual_model": "provider/model-fixed", "judge_records": []any{}, "trace_path": rel(root, filepath.Join(outputs, "trace.jsonl")), "trace_sha256": testFileHash(t, filepath.Join(outputs, "trace.jsonl")), "response_path": rel(root, filepath.Join(outputs, "response.md")), "response_sha256": testFileHash(t, filepath.Join(outputs, "response.md")), "grading_path": rel(root, filepath.Join(dir, "grading.json")), "grading_sha256": testFileHash(t, filepath.Join(dir, "grading.json")), "duration_seconds": 0.1, "total_tokens": nil, "cost": nil, "available_skills": []any{}, "skill_activation": "none", "expected_skill_loading": "forbidden", "installed_skill_path": ""}
	if treatment {
		record["available_skills"] = []any{"fixture-skill"}
		record["skill_activation"] = "forced_command"
		record["expected_skill_loading"] = "required"
		record["installed_skill_path"] = rel(root, filepath.Join(dir, "installed-skill", "fixture-skill"))
	}
	return record
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
	data, err := os.ReadFile(filepath.Join(root, "SKILL.md"))
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.New()
	hash.Write([]byte("SKILL.md\x00-\x00"))
	hash.Write(data)
	hash.Write([]byte{0})
	return hex.EncodeToString(hash.Sum(nil))
}
func rel(root, path string) string {
	value, _ := filepath.Rel(root, path)
	return filepath.ToSlash(value)
}
