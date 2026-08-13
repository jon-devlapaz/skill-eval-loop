package aggregate

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func Run(runDir string) (map[string]any, error) {
	root, err := filepath.Abs(runDir)
	if err != nil {
		return nil, err
	}
	manifest, err := readObject(filepath.Join(root, "run_manifest.json"))
	if err != nil {
		return nil, err
	}
	if manifest["schema_version"] != float64(1) {
		return nil, fmt.Errorf("run_manifest.json must use schema_version 1")
	}
	harness, _ := manifest["harness"].(string)
	if harness != "pi" && harness != "claude-code" && harness != "hermes" && harness != "codex" {
		return nil, fmt.Errorf("aggregate currently supports retained Pi, Claude Code, Codex, and Hermes runs")
	}
	skillName, _ := manifest["target_skill_name"].(string)
	requested, _ := manifest["requested_model"].(string)
	skillHash, _ := manifest["skill_sha256"].(string)
	if skillName == "" {
		return nil, fmt.Errorf("manifest.target_skill_name is missing")
	}
	if requested == "" {
		return nil, fmt.Errorf("manifest.requested_model is missing")
	}
	if !isHash(skillHash) {
		return nil, fmt.Errorf("manifest.skill_sha256 is not a sha256")
	}
	activation := stringDefault(manifest["activation_mode"], "forced")
	if activation != "forced" && activation != "autonomous" {
		return nil, fmt.Errorf("manifest.activation_mode is invalid")
	}
	suitePath, err := artifactPath(root, manifest["suite_path"], "manifest.suite_path")
	if err != nil {
		return nil, err
	}
	if err = requireFileHash(suitePath, manifest["suite_sha256"], "manifest.suite_sha256 does not match suite snapshot"); err != nil {
		return nil, err
	}
	suite, err := readObject(suitePath)
	if err != nil {
		return nil, err
	}
	cases, ok := suite["cases"].([]any)
	if !ok || len(cases) == 0 {
		return nil, fmt.Errorf("suite snapshot must contain cases")
	}
	if provenanceValue, ok := manifest["provenance_path"].(string); ok && provenanceValue != "" {
		provenancePath, err := artifactPath(root, provenanceValue, "manifest.provenance_path")
		if err != nil {
			return nil, err
		}
		if err = requireFileHash(provenancePath, manifest["provenance_sha256"], "manifest.provenance_sha256 does not match snapshot"); err != nil {
			return nil, err
		}
		snapshot, err := readObject(provenancePath)
		if err != nil {
			return nil, err
		}
		records, _ := snapshot["cases"].([]any)
		for index, raw := range records {
			record, ok := raw.(map[string]any)
			if !ok {
				return nil, fmt.Errorf("provenance.cases[%d] must be an object", index+1)
			}
			label := fmt.Sprintf("provenance.cases[%d]", index+1)
			retained, err := artifactPath(root, record["retained_artifact_path"], label+".retained_artifact_path")
			if err != nil {
				return nil, err
			}
			actual, err := fileHash(retained)
			if err != nil {
				return nil, err
			}
			expected, _ := record["retained_artifact_sha256"].(string)
			if actual != expected {
				return nil, fmt.Errorf("%s retained artifact hash does not match", label)
			}
		}
	}
	caseIDs := []string{}
	caseModelCounts := map[string]int{}
	counterDeclared := map[string]bool{}
	accountingAvailable := true
	modelRubricTotal := 0
	counterModelRubricTotal := 0
	for _, raw := range cases {
		item, ok := raw.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("suite snapshot cases must have ids")
		}
		id, _ := item["id"].(string)
		if id == "" {
			return nil, fmt.Errorf("suite snapshot cases must have ids")
		}
		caseIDs = append(caseIDs, id)
		count, countOK := intValue(item["model_rubric_count"])
		declared, declaredOK := item["counter_reference_declared"].(bool)
		if !countOK || count < 0 || !declaredOK {
			accountingAvailable = false
		} else {
			caseModelCounts[id] = count
			counterDeclared[id] = declared
			modelRubricTotal += count
			if declared {
				counterModelRubricTotal += count
			}
		}
	}
	judgeModel, _ := manifest["judge_model"].(string)
	referenceJudges := []map[string]any{}
	counterJudges := []map[string]any{}
	if accountingAvailable {
		references, ok := manifest["reference_validation"].([]any)
		if !ok || len(references) != len(caseIDs) {
			return nil, fmt.Errorf("manifest.reference_validation does not match cases")
		}
		seenReferences := map[string]bool{}
		for index, raw := range references {
			reference, ok := raw.(map[string]any)
			if !ok {
				return nil, fmt.Errorf("manifest.reference_validation[%d] must be an object", index+1)
			}
			caseID, _ := reference["case_id"].(string)
			if _, exists := caseModelCounts[caseID]; !exists || seenReferences[caseID] {
				return nil, fmt.Errorf("manifest.reference_validation[%d].case_id is unknown or duplicate", index+1)
			}
			seenReferences[caseID] = true
			judges, err := validateJudgeRecords(root, reference["judge_records"], caseModelCounts[caseID], fmt.Sprintf("manifest.reference_validation[%d].judge_records", index+1), judgeModel)
			if err != nil {
				return nil, err
			}
			referenceJudges = append(referenceJudges, judges...)
			counter, present := reference["counter_reference"]
			if present != counterDeclared[caseID] {
				return nil, fmt.Errorf("manifest.reference_validation[%d].counter_reference does not match suite snapshot", index+1)
			}
			if present {
				counterValue, ok := counter.(map[string]any)
				if !ok {
					return nil, fmt.Errorf("manifest.reference_validation[%d].counter_reference must be an object", index+1)
				}
				judges, err := validateJudgeRecords(root, counterValue["judge_records"], caseModelCounts[caseID], fmt.Sprintf("manifest.reference_validation[%d].counter_reference.judge_records", index+1), judgeModel)
				if err != nil {
					return nil, err
				}
				counterJudges = append(counterJudges, judges...)
			}
		}
	}
	trialsPerCase, ok := intValue(manifest["trials_per_case"])
	if !ok || trialsPerCase < 1 {
		return nil, fmt.Errorf("manifest.trials_per_case must be a positive integer")
	}
	trials, ok := manifest["trials"].([]any)
	if !ok || len(trials) == 0 {
		return nil, fmt.Errorf("manifest.trials must be a non-empty list")
	}
	expected := len(caseIDs) * trialsPerCase
	if count, _ := intValue(manifest["pair_count"]); count != expected || len(trials) != expected {
		return nil, fmt.Errorf("manifest does not contain the complete case/trial matrix")
	}
	totals := map[string]int{"without_skill": 0, "with_skill": 0}
	pairOutcomes := map[string]int{"improved": 0, "regressed": 0, "tied_pass": 0, "tied_fail": 0}
	seen := map[string]bool{}
	mechanismGaps := []string{}
	runtimeGaps := []string{}
	routing := map[string]int{"expected_injections": 0, "available": 0, "injection_attested": 0, "explicit_accesses": 0, "control_exposures": 0, "decisions_scored": 0, "decisions_correct": 0, "false_positives": 0, "false_negatives": 0}
	conditionRecords := map[string][]map[string]any{"without_skill": {}, "with_skill": {}}
	conditionJudges := []map[string]any{}
	for _, rawPair := range trials {
		pair, ok := rawPair.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("manifest trial must be an object")
		}
		caseID, _ := pair["case_id"].(string)
		trial, ok := intValue(pair["trial"])
		if !ok {
			return nil, fmt.Errorf("manifest trial needs case_id and integer trial")
		}
		key := fmt.Sprintf("%s/%d", caseID, trial)
		if seen[key] {
			return nil, fmt.Errorf("duplicate pair: %s/%d", caseID, trial)
		}
		seen[key] = true
		label := fmt.Sprintf("%s/trial-%03d", caseID, trial)
		conditions, ok := pair["conditions"].(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s must contain exactly conditions", label)
		}
		observed := map[string]conditionResult{}
		for _, condition := range []string{"without_skill", "with_skill"} {
			record, ok := conditions[condition].(map[string]any)
			if !ok {
				return nil, fmt.Errorf("%s.%s is missing", label, condition)
			}
			result, err := validateCondition(root, record, condition, label, caseID, trial, requested, harness, skillName, skillHash)
			if err != nil {
				return nil, err
			}
			observed[condition] = result
			conditionRecords[condition] = append(conditionRecords[condition], record)
			if accountingAvailable {
				judges, err := validateJudgeRecords(root, record["judge_records"], caseModelCounts[caseID], label+"."+condition+".judge_records", judgeModel)
				if err != nil {
					return nil, err
				}
				conditionJudges = append(conditionJudges, judges...)
			}
			if result.success {
				totals[condition]++
			}
		}
		without, with := observed["without_skill"].success, observed["with_skill"].success
		switch {
		case with && !without:
			pairOutcomes["improved"]++
		case without && !with:
			pairOutcomes["regressed"]++
		case with:
			pairOutcomes["tied_pass"]++
		default:
			pairOutcomes["tied_fail"]++
		}
		treatment := observed["with_skill"]
		control := observed["without_skill"]
		routing["expected_injections"]++
		if treatment.available {
			routing["available"]++
		}
		if treatment.injected {
			routing["injection_attested"]++
		}
		if treatment.accessed {
			routing["explicit_accesses"]++
		}
		controlExposed := control.available || control.activation != "none" || control.injected || control.accessed
		if controlExposed {
			routing["control_exposures"]++
			mechanismGaps = append(mechanismGaps, label+": control_skill_exposure")
		}
		if !treatment.available {
			mechanismGaps = append(mechanismGaps, label+": treatment_skill_unavailable")
		}
		expectedActivation := "forced_command"
		if activation == "autonomous" {
			expectedActivation = "available_for_autonomous_selection"
		}
		if treatment.activation != expectedActivation {
			gap := "treatment_skill_not_forced"
			if activation == "autonomous" {
				gap = "treatment_activation_mismatch"
			}
			mechanismGaps = append(mechanismGaps, label+": "+gap)
		}
		if activation == "forced" && !treatment.injected && !treatment.accessed {
			runtimeGaps = append(runtimeGaps, label+": skill_injection_not_visible_in_trace")
		}
		if activation == "autonomous" {
			loading, _ := recordString(conditions["with_skill"], "expected_skill_loading")
			if loading == "required" || loading == "forbidden" {
				routing["decisions_scored"]++
				correct := (loading == "required" && treatment.accessed) || (loading == "forbidden" && !treatment.accessed)
				if correct {
					routing["decisions_correct"]++
				}
				if loading == "required" && !treatment.accessed {
					routing["false_negatives"]++
					runtimeGaps = append(runtimeGaps, label+": expected_skill_access_not_visible_in_trace")
				}
				if loading == "forbidden" && treatment.accessed {
					routing["false_positives"]++
				}
			}
		}
	}
	pairCount := len(trials)
	controlRate := float64(totals["without_skill"]) / float64(pairCount)
	treatmentRate := float64(totals["with_skill"]) / float64(pairCount)
	delta := treatmentRate - controlRate
	outcome := "no_difference"
	if delta > 0 {
		outcome = "improved"
	} else if delta < 0 {
		outcome = "regressed"
	}
	verdict := outcome
	if len(mechanismGaps) > 0 {
		verdict = "mechanism_unconfirmed"
	}
	operations := map[string]any{"without_skill": usage(conditionRecords["without_skill"], pairCount), "with_skill": usage(conditionRecords["with_skill"], pairCount), "condition_judges": unknownUsage(), "references": unknownUsage(), "counter_references": unknownUsage(), "full": unknownUsage()}
	if accountingAvailable {
		conditionJudgesExpected := modelRubricTotal * 2 * trialsPerCase
		references := modelRubricTotal
		counters := counterModelRubricTotal
		full := pairCount*2 + conditionJudgesExpected + references + counters
		operations["condition_judges"] = usageBucket(conditionJudges, conditionJudgesExpected)
		operations["references"] = usageBucket(referenceJudges, references)
		operations["counter_references"] = usageBucket(counterJudges, counters)
		allRecords := append(append([]map[string]any{}, conditionRecords["without_skill"]...), conditionRecords["with_skill"]...)
		allRecords = append(allRecords, conditionJudges...)
		allRecords = append(allRecords, referenceJudges...)
		allRecords = append(allRecords, counterJudges...)
		operations["full"] = usageBucket(allRecords, full)
	}
	accuracy := any(nil)
	selection := "not_measured"
	if routing["decisions_scored"] > 0 {
		value := round3(float64(routing["decisions_correct"]) / float64(routing["decisions_scored"]))
		accuracy = value
		selection = "failed"
		if value == 1 {
			selection = "passed"
		}
	}
	claim := stringDefault(suite["grader_discrimination"], "none")
	limits := []any{"This is a local paired diagnostic, not a distribution or significance claim."}
	if claim == "none" {
		limits = append(limits, "The suite did not declare grader_discrimination=case_contrast; optional counters do not prove every response-sensitive grader distinguishes a known good/bad pair.")
	}
	limits = append(limits, harness+" skill exposure is configured by the selected adapter; runtime attestation and tool-profile precision vary by harness.", "Condition order is counterbalanced by trial; temporal drift remains possible.")
	return map[string]any{"schema_version": 2, "skill_name": skillName, "verdict": verdict, "outcome_verdict": outcome, "valid": true, "artifact_valid": true, "mechanism_valid": len(mechanismGaps) == 0, "runtime_attestation_complete": len(runtimeGaps) == 0, "activation_mode": activation, "grader_discrimination": map[string]any{"claim": claim, "validated": claim == "case_contrast"}, "selection_verdict": selection, "invalid_reasons": []any{}, "mechanism_gaps": stringsAny(sortedUnique(mechanismGaps)), "runtime_attestation_gaps": stringsAny(sortedUnique(runtimeGaps)), "pair_count": pairCount, "task_success": map[string]any{"without_skill": map[string]any{"passed": totals["without_skill"], "rate": round3(controlRate)}, "with_skill": map[string]any{"passed": totals["with_skill"], "rate": round3(treatmentRate)}, "delta": round3(delta), "pair_outcomes": pairOutcomes}, "routing": map[string]any{"expected_injections": routing["expected_injections"], "available": routing["available"], "injection_attested": routing["injection_attested"], "explicit_accesses": routing["explicit_accesses"], "control_exposures": routing["control_exposures"], "decisions_scored": routing["decisions_scored"], "decisions_correct": routing["decisions_correct"], "false_positives": routing["false_positives"], "false_negatives": routing["false_negatives"], "accuracy": accuracy}, "operations": operations, "limits": limits}, nil
}

func recordString(value any, key string) (string, bool) {
	record, ok := value.(map[string]any)
	if !ok {
		return "", false
	}
	text, ok := record[key].(string)
	return text, ok
}

type conditionResult struct {
	success, available, injected, accessed bool
	activation                             string
}

func validateJudgeRecords(root string, value any, expected int, label, requested string) ([]map[string]any, error) {
	raw, ok := value.([]any)
	if !ok {
		return nil, fmt.Errorf("%s must be a list", label)
	}
	if len(raw) != expected {
		return nil, fmt.Errorf("%s does not match the case model_rubric_count", label)
	}
	records := make([]map[string]any, 0, len(raw))
	for index, item := range raw {
		record, ok := item.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s[%d] must be an object", label, index+1)
		}
		itemLabel := fmt.Sprintf("%s[%d]", label, index+1)
		trace, err := requireHash(root, record, "trace", itemLabel)
		if err != nil {
			return nil, err
		}
		traces := []string{trace}
		if value, _ := record["attestation_trace_path"].(string); value != "" {
			attestation, err := requireHash(root, record, "attestation_trace", itemLabel)
			if err != nil {
				return nil, err
			}
			traces = append(traces, attestation)
		}
		observed, _, _, err := traceEvidence(traces, "")
		if err != nil {
			return nil, err
		}
		actual, _ := record["actual_model"].(string)
		if requested == "" || !strings.EqualFold(observed, requested) || !strings.EqualFold(actual, observed) {
			return nil, fmt.Errorf("%s judge model mismatch", itemLabel)
		}
		records = append(records, record)
	}
	return records, nil
}

func validateCondition(root string, record map[string]any, condition, label, caseID string, trial int, requested, harness, skillName, skillHash string) (conditionResult, error) {
	prefix := label + "." + condition
	if record["condition"] != condition {
		return conditionResult{}, fmt.Errorf("%s.condition does not match its manifest key", prefix)
	}
	if record["case_id"] != caseID {
		return conditionResult{}, fmt.Errorf("%s does not match its enclosing pair", prefix)
	}
	observedTrial, _ := intValue(record["trial"])
	if observedTrial != trial {
		return conditionResult{}, fmt.Errorf("%s does not match its enclosing pair", prefix)
	}
	trace, err := requireHash(root, record, "trace", prefix)
	if err != nil {
		return conditionResult{}, err
	}
	if _, err = requireHash(root, record, "response", prefix); err != nil {
		return conditionResult{}, err
	}
	gradingPath, err := requireHash(root, record, "grading", prefix)
	if err != nil {
		return conditionResult{}, err
	}
	passed, err := validateGrading(gradingPath, prefix)
	if err != nil {
		return conditionResult{}, err
	}
	exit, _ := intValue(record["exit_code"])
	success := passed && exit == 0 && record["timed_out"] != true
	available := stringSlice(record["available_skills"])
	installed, _ := record["installed_skill_path"].(string)
	if condition == "without_skill" {
		if installed != "" || len(available) > 0 {
			return conditionResult{}, fmt.Errorf("%s exposes the target skill in the control", prefix)
		}
	} else {
		path, err := artifactPath(root, installed, prefix+".installed_skill_path")
		if err != nil {
			return conditionResult{}, err
		}
		if hash, err := payloadHash(path); err != nil || hash != skillHash {
			return conditionResult{}, fmt.Errorf("%s installed payload differs from evaluated skill", prefix)
		}
		if len(available) != 1 || available[0] != skillName {
			return conditionResult{}, fmt.Errorf("%s.available_skills must contain only %s", prefix, skillName)
		}
	}
	traces := []string{trace}
	if value, _ := record["attestation_trace_path"].(string); value != "" {
		attestation, err := requireHash(root, record, "attestation_trace", prefix)
		if err != nil {
			return conditionResult{}, err
		}
		traces = append(traces, attestation)
	}
	model, injected, accessed, err := traceEvidence(traces, skillName)
	if err != nil {
		return conditionResult{}, err
	}
	if !strings.EqualFold(model, requested) {
		return conditionResult{}, fmt.Errorf("%s model mismatch", prefix)
	}
	activation, _ := record["skill_activation"].(string)
	return conditionResult{success: success, available: len(available) == 1, injected: injected, accessed: accessed, activation: activation}, nil
}
func requireHash(root string, record map[string]any, stem, label string) (string, error) {
	path, err := artifactPath(root, record[stem+"_path"], label+"."+stem+"_path")
	if err != nil {
		return "", err
	}
	expected, _ := record[stem+"_sha256"].(string)
	if !isHash(expected) {
		return "", fmt.Errorf("%s.%s_sha256 is not a sha256", label, stem)
	}
	actual, err := fileHash(path)
	if err != nil {
		return "", err
	}
	if actual != expected {
		return "", fmt.Errorf("%s.%s_sha256 does not match %s", label, stem, path)
	}
	return path, nil
}
func validateGrading(path, label string) (bool, error) {
	value, err := readObject(path)
	if err != nil {
		return false, err
	}
	items, ok := value["expectations"].([]any)
	if !ok || len(items) == 0 {
		return false, fmt.Errorf("%s.expectations must be a non-empty list", label)
	}
	summary, ok := value["summary"].(map[string]any)
	if !ok {
		return false, fmt.Errorf("%s.summary must be an object", label)
	}
	passed := 0
	seen := map[string]bool{}
	for _, raw := range items {
		item, ok := raw.(map[string]any)
		if !ok {
			return false, fmt.Errorf("%s expectation invalid", label)
		}
		name, _ := item["text"].(string)
		if name == "" || seen[name] {
			return false, fmt.Errorf("%s expectation name missing or duplicate", label)
		}
		seen[name] = true
		value, ok := item["passed"].(bool)
		if !ok {
			return false, fmt.Errorf("%s expectation passed must be boolean", label)
		}
		if value {
			passed++
		}
	}
	total := len(items)
	sp, _ := intValue(summary["passed"])
	sf, _ := intValue(summary["failed"])
	st, _ := intValue(summary["total"])
	rate, _ := summary["pass_rate"].(float64)
	if sp != passed || sf != total-passed || st != total || rate != float64(passed)/float64(total) {
		return false, fmt.Errorf("%s.summary is inconsistent with expectations", label)
	}
	return passed == total, nil
}
func traceEvidence(paths []string, skill string) (string, bool, bool, error) {
	model := ""
	injected := false
	accessed := false
	for _, path := range paths {
		file, err := os.Open(path)
		if err != nil {
			return "", false, false, err
		}
		scanner := bufio.NewScanner(file)
		for scanner.Scan() {
			var event map[string]any
			if json.Unmarshal(scanner.Bytes(), &event) != nil {
				continue
			}
			provider, _ := event["provider"].(string)
			observed, _ := event["model"].(string)
			if payload, ok := event["payload"].(map[string]any); ok && event["type"] == "turn_context" {
				observed, _ = payload["model"].(string)
			}
			if observed != "" {
				if provider != "" && !strings.Contains(observed, "/") {
					observed = provider + "/" + observed
				}
				model = observed
			}
			for _, name := range stringSlice(event["skills"]) {
				if strings.EqualFold(name, skill) {
					injected = true
				}
			}
			lower := strings.ToLower(string(scanner.Bytes()))
			if strings.Contains(lower, "/"+strings.ToLower(skill)+"/skill.md") {
				if event["type"] == "world_state" {
					injected = true
				}
				if event["type"] != "world_state" {
					accessed = true
				}
			}
		}
		err = scanner.Err()
		closeErr := file.Close()
		if err != nil {
			return "", false, false, err
		}
		if closeErr != nil {
			return "", false, false, closeErr
		}
	}
	return model, injected, accessed, nil
}
func payloadHash(root string) (string, error) {
	data, err := os.ReadFile(filepath.Join(root, "SKILL.md"))
	if err != nil {
		return "", err
	}
	hash := sha256.New()
	hash.Write([]byte("SKILL.md\x00-\x00"))
	hash.Write(data)
	hash.Write([]byte{0})
	return hex.EncodeToString(hash.Sum(nil)), nil
}
func artifactPath(root string, value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || text == "" {
		return "", fmt.Errorf("%s is missing", label)
	}
	if filepath.IsAbs(text) || strings.Contains(filepath.ToSlash(text), "../") {
		return "", fmt.Errorf("%s must be relative to the run", label)
	}
	path := filepath.Join(root, text)
	resolvedRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return "", err
	}
	relative, err := filepath.Rel(resolvedRoot, resolved)
	if err != nil || strings.HasPrefix(relative, "..") {
		return "", fmt.Errorf("%s escapes the run", label)
	}
	return resolved, nil
}
func readObject(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	var value map[string]any
	if err = decoder.Decode(&value); err != nil {
		return nil, err
	}
	return value, nil
}
func requireFileHash(path string, expected any, message string) error {
	text, _ := expected.(string)
	actual, err := fileHash(path)
	if err != nil {
		return err
	}
	if actual != text {
		return fmt.Errorf("%s", message)
	}
	return nil
}
func fileHash(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}
func isHash(value string) bool {
	if len(value) != 64 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil && value == strings.ToLower(value)
}
func intValue(value any) (int, bool) {
	number, ok := value.(float64)
	return int(number), ok && number == float64(int(number))
}
func stringDefault(value any, fallback string) string {
	text, ok := value.(string)
	if !ok {
		return fallback
	}
	return text
}
func stringSlice(value any) []string {
	raw, ok := value.([]any)
	if !ok {
		return nil
	}
	result := []string{}
	for _, item := range raw {
		if text, ok := item.(string); ok {
			result = append(result, text)
		}
	}
	return result
}
func usage(records []map[string]any, expected int) map[string]any {
	errors, timeouts, tokens, tokenReports := 0, 0, 0, 0
	cost, costReports := 0.0, 0
	for _, record := range records {
		exit, _ := intValue(record["exit_code"])
		if exit != 0 {
			errors++
		}
		if record["timed_out"] == true {
			timeouts++
		}
		if value, ok := intValue(record["total_tokens"]); ok {
			tokens += value
			tokenReports++
		}
		if value, ok := record["cost"].(float64); ok {
			cost += value
			costReports++
		}
	}
	var tokenValue, costValue any
	if tokenReports > 0 {
		tokenValue = tokens
	}
	if costReports > 0 {
		costValue = cost
	}
	return map[string]any{"errors": errors, "timeouts": timeouts, "tokens": tokenValue, "cost": costValue, "tokens_coverage": map[string]any{"reported": tokenReports, "expected": expected}, "cost_coverage": map[string]any{"reported": costReports, "expected": expected}}
}
func usageBucket(records []map[string]any, expected int) map[string]any {
	if expected == 0 && len(records) == 0 {
		return map[string]any{"tokens": 0, "cost": 0.0, "tokens_coverage": map[string]any{"reported": 0, "expected": 0}, "cost_coverage": map[string]any{"reported": 0, "expected": 0}}
	}
	base := usage(records, expected)
	return map[string]any{"tokens": base["tokens"], "cost": base["cost"], "tokens_coverage": base["tokens_coverage"], "cost_coverage": base["cost_coverage"]}
}
func unknownUsage() map[string]any {
	return map[string]any{"tokens": nil, "cost": nil, "tokens_coverage": map[string]any{"reported": nil, "expected": nil}, "cost_coverage": map[string]any{"reported": nil, "expected": nil}}
}
func sortedUnique(values []string) []string {
	set := map[string]bool{}
	for _, value := range values {
		set[value] = true
	}
	result := []string{}
	for value := range set {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}
func stringsAny(values []string) []any {
	result := make([]any, len(values))
	for index, value := range values {
		result[index] = value
	}
	return result
}
func round3(value float64) float64 { return float64(int(value*1000+0.5)) / 1000 }
