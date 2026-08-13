package evalspec

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

var (
	graderTypes           = stringSet("response_contains", "response_not_contains", "response_regex", "markdown_table_column_regex", "file_exists", "json_exact", "model_rubric")
	responseSensitive     = stringSet("response_contains", "response_not_contains", "response_regex", "markdown_table_column_regex", "model_rubric")
	deterministicResponse = stringSet("response_contains", "response_not_contains", "response_regex", "markdown_table_column_regex")
	skillLoadingPolicies  = stringSet("required", "optional", "forbidden")
	suiteTypes            = stringSet("capability", "regression")
	datasetOrigins        = stringSet("author_derived", "held_out", "production_regression")
	provenanceSourceTypes = stringSet("author_scenario", "independent_task", "production_trace", "user_correction", "incident")
	behaviorClasses       = stringSet("positive", "edge", "negative")
	routingClasses        = stringSet("should_trigger", "should_not_trigger", "ambiguous")
	toolProfiles          = stringSet("no_tools", "read_only", "read_write", "coding")
	activationModes       = stringSet("forced", "autonomous")
	discriminationClaims  = stringSet("none", "case_contrast")
	caseIDPattern         = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,63}$`)
	sha256Pattern         = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

type Suite struct {
	Raw                  map[string]any
	SchemaVersion        any
	SchemaVersionNumber  int
	SkillName            string
	SuiteType            string
	DatasetOrigin        string
	ToolProfile          string
	ActivationMode       string
	GraderDiscrimination string
	SourcePath           string
	SuiteRoot            string
	ProvenanceRecords    map[string]map[string]any
	ProvenanceSHA256     string
	Cases                []Case
}

type Case struct {
	Raw                  map[string]any
	ID                   string
	Prompt               string
	BehaviorClass        string
	RoutingClass         string
	ExpectedSkillLoading string
	Graders              []map[string]any
	Reference            map[string]any
	CounterReference     map[string]any
	HasCounterReference  bool
	Discrimination       Discrimination
}

type Discrimination struct {
	ResponseSensitiveGraders    int
	DeterministicGradersChecked int
	ModelGradersPendingRuntime  int
}

type GradeResult struct {
	Grader       GradeOwner    `json:"grader"`
	Expectations []Expectation `json:"expectations"`
	Summary      GradeSummary  `json:"summary"`
}

type GradeOwner struct {
	Kind          string `json:"kind"`
	SchemaVersion int    `json:"schema_version"`
}

type Expectation struct {
	Text     string `json:"text"`
	Passed   bool   `json:"passed"`
	Evidence string `json:"evidence"`
	Grader   string `json:"grader"`
}

type GradeSummary struct {
	Passed   int     `json:"passed"`
	Failed   int     `json:"failed"`
	Total    int     `json:"total"`
	PassRate float64 `json:"pass_rate"`
}

func GradeCase(workspace, response string, graders []map[string]any, external map[string]map[string]any) (GradeResult, error) {
	result := GradeResult{Grader: GradeOwner{Kind: "deterministic_mixed", SchemaVersion: 2}}
	for _, grader := range graders {
		typeName := grader["type"].(string)
		name := grader["name"].(string)
		expectation := Expectation{Text: name, Grader: typeName}
		switch typeName {
		case "response_contains", "response_not_contains", "response_regex", "markdown_table_column_regex":
			passed, err := GradeResponse(response, grader)
			if err != nil {
				return GradeResult{}, err
			}
			expectation.Passed = passed
			expectation.Evidence = responseEvidence(response, grader, passed)
		case "model_rubric":
			grade, ok := external[name]
			if !ok {
				return GradeResult{}, fmt.Errorf("missing external model grade for %q", name)
			}
			passed, ok := grade["passed"].(bool)
			if !ok {
				return GradeResult{}, fmt.Errorf("external model grade %q needs boolean passed", name)
			}
			evidence := strings.TrimSpace(stringValue(grade["evidence"]))
			if evidence == "" {
				return GradeResult{}, fmt.Errorf("external model grade %q needs evidence", name)
			}
			expectation.Passed, expectation.Evidence = passed, evidence
		case "file_exists":
			target, err := SafeRelativePath(workspace, grader["path"], name)
			if err != nil {
				return GradeResult{}, err
			}
			info, err := os.Stat(target)
			expectation.Passed = err == nil && info.Mode().IsRegular()
			if expectation.Passed {
				expectation.Evidence = grader["path"].(string) + " exists"
			} else {
				expectation.Evidence = grader["path"].(string) + " is absent"
			}
		case "json_exact":
			target, err := SafeRelativePath(workspace, grader["path"], name)
			if err != nil {
				return GradeResult{}, err
			}
			observed, readErr := readJSON(target)
			expectation.Passed = readErr == nil && jsonEqual(observed, grader["expected"])
			if expectation.Passed {
				expectation.Evidence = grader["path"].(string) + " exactly matches expected JSON"
			} else {
				errorText := "none"
				if readErr != nil {
					errorText = readErr.Error()
				}
				expectation.Evidence = fmt.Sprintf("observed=%s; error=%s", pythonRepr(observed), errorText)
			}
		}
		result.Expectations = append(result.Expectations, expectation)
		if expectation.Passed {
			result.Summary.Passed++
		}
	}
	result.Summary.Total = len(result.Expectations)
	result.Summary.Failed = result.Summary.Total - result.Summary.Passed
	result.Summary.PassRate = float64(result.Summary.Passed) / float64(result.Summary.Total)
	return result, nil
}

func responseEvidence(response string, grader map[string]any, passed bool) string {
	switch grader["type"] {
	case "response_contains":
		needle := grader["value"].(string)
		state := "not found"
		if passed {
			state = "found"
		}
		return fmt.Sprintf("%q %s in response", needle, state)
	case "response_not_contains":
		needle := grader["value"].(string)
		state := "present"
		if passed {
			state = "absent"
		}
		return fmt.Sprintf("%q %s in response", needle, state)
	case "response_regex":
		pattern := grader["pattern"].(string)
		match := regexp.MustCompile(pattern).FindString(response)
		if passed {
			return fmt.Sprintf("matched %q", match)
		}
		return fmt.Sprintf("pattern %q did not match", pattern)
	case "markdown_table_column_regex":
		pattern := grader["pattern"].(string)
		column := grader["column"].(string)
		text := markdownColumn(response, column)
		match := regexp.MustCompile(pattern).FindString(text)
		if passed {
			return fmt.Sprintf("column %q matched %q", column, match)
		}
		return fmt.Sprintf("pattern %q did not match column %q; observed=%q", pattern, column, text)
	}
	return ""
}

func jsonEqual(left, right any) bool {
	leftBytes, _ := canonicalJSON(left)
	rightBytes, _ := canonicalJSON(right)
	return bytes.Equal(leftBytes, rightBytes)
}
func pythonRepr(value any) string {
	if value == nil {
		return "None"
	}
	data, err := json.Marshal(value)
	if err != nil {
		return fmt.Sprintf("%v", value)
	}
	return string(data)
}

func Load(skillPath, evalsPath string) (*Suite, error) {
	resolvedSkill, err := filepath.Abs(skillPath)
	if err != nil {
		return nil, err
	}
	source := evalsPath
	if source == "" {
		source = filepath.Join(resolvedSkill, "evals", "evals.json")
	}
	source, err = filepath.Abs(source)
	if err != nil {
		return nil, err
	}
	data, err := readJSON(source)
	if err != nil {
		return nil, err
	}
	root, ok := data.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s must contain a JSON object", source)
	}
	schemaValue := root["schema_version"]
	schema, ok := numericMember(schemaValue, 2, 3)
	if !ok {
		return nil, fmt.Errorf("%s must use schema_version 2 or 3", source)
	}
	suiteType, _ := root["suite_type"].(string)
	if !suiteTypes[suiteType] {
		return nil, fmt.Errorf("%s.suite_type must be one of %s", source, pythonSet(suiteTypes))
	}
	datasetOrigin, _ := root["dataset_origin"].(string)
	if !datasetOrigins[datasetOrigin] {
		return nil, fmt.Errorf("%s.dataset_origin must be one of %s", source, pythonSet(datasetOrigins))
	}
	toolProfile, _ := root["tool_profile"].(string)
	if !toolProfiles[toolProfile] {
		return nil, fmt.Errorf("%s.tool_profile must be one of %s", source, pythonSet(toolProfiles))
	}
	activationMode := stringDefault(root["activation_mode"], "forced")
	if !activationModes[activationMode] {
		return nil, fmt.Errorf("%s.activation_mode must be one of %s", source, pythonSet(activationModes))
	}
	if activationMode == "autonomous" && schema != 3 {
		return nil, fmt.Errorf("%s.activation_mode=autonomous requires schema_version 3", source)
	}
	discrimination := stringDefault(root["grader_discrimination"], "none")
	if !discriminationClaims[discrimination] {
		return nil, fmt.Errorf("%s.grader_discrimination must be one of %s", source, pythonSet(discriminationClaims))
	}
	if discrimination != "none" && schema != 3 {
		return nil, fmt.Errorf("%s.grader_discrimination=%s requires schema_version 3", source, discrimination)
	}
	skillName, _ := root["skill_name"].(string)
	if skillName != filepath.Base(resolvedSkill) {
		return nil, fmt.Errorf("%s.skill_name must match directory %q", source, filepath.Base(resolvedSkill))
	}
	rawCases, ok := root["evals"].([]any)
	if !ok || len(rawCases) == 0 {
		return nil, fmt.Errorf("%s.evals must be a non-empty list", source)
	}

	suite := &Suite{
		Raw: root, SchemaVersion: schemaValue, SchemaVersionNumber: schema,
		SkillName: skillName, SuiteType: suiteType, DatasetOrigin: datasetOrigin,
		ToolProfile: toolProfile, ActivationMode: activationMode,
		GraderDiscrimination: discrimination, SourcePath: source,
		ProvenanceRecords: map[string]map[string]any{},
	}
	seenIDs := map[string]bool{}
	caseHashes := map[string]string{}
	for index, raw := range rawCases {
		label := fmt.Sprintf("evals[%d]", index+1)
		caseMap, ok := raw.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s must be an object", label)
		}
		parsed, err := validateCase(caseMap, label, schema, discrimination)
		if err != nil {
			return nil, err
		}
		if seenIDs[parsed.ID] {
			return nil, fmt.Errorf("duplicate eval id: %s", parsed.ID)
		}
		seenIDs[parsed.ID] = true
		hash, err := CanonicalSHA256(caseMap)
		if err != nil {
			return nil, err
		}
		caseHashes[parsed.ID] = hash
		suite.Cases = append(suite.Cases, parsed)
	}
	if discrimination == "case_contrast" {
		found := false
		for _, current := range suite.Cases {
			found = found || current.Discrimination.ResponseSensitiveGraders > 0
		}
		if !found {
			return nil, fmt.Errorf("%s grader contrast requires at least one response-sensitive grader", source)
		}
	}
	if schema == 3 {
		if _, exists := root["distribution_policy"]; exists {
			return nil, fmt.Errorf("%s.distribution_policy is obsolete and must be removed; the evaluator never applied these thresholds", source)
		}
		suite.SuiteRoot = filepath.Dir(source)
		suiteHash, err := CanonicalSHA256(root)
		if err != nil {
			return nil, err
		}
		records, provenanceHash, err := loadProvenance(suite.SuiteRoot, root["provenance_manifest"], source, seenIDs, caseHashes, suiteHash, datasetOrigin)
		if err != nil {
			return nil, err
		}
		suite.ProvenanceRecords = records
		suite.ProvenanceSHA256 = provenanceHash
	} else {
		suite.SuiteRoot = resolvedSkill
	}
	return suite, nil
}

func validateCase(value map[string]any, label string, schema int, discrimination string) (Case, error) {
	id := strings.TrimSpace(stringValue(value["id"]))
	if !caseIDPattern.MatchString(id) {
		return Case{}, fmt.Errorf("%s.id must be lowercase kebab-case", label)
	}
	prompt, ok := value["prompt"].(string)
	if !ok || strings.TrimSpace(prompt) == "" {
		return Case{}, fmt.Errorf("%s.prompt must be non-empty", label)
	}
	loading := stringDefault(value["expected_skill_loading"], "required")
	if !skillLoadingPolicies[loading] {
		return Case{}, fmt.Errorf("%s.expected_skill_loading must be one of %s", label, pythonSet(skillLoadingPolicies))
	}
	behavior, _ := value["behavior_class"].(string)
	if !behaviorClasses[behavior] {
		return Case{}, fmt.Errorf("%s.behavior_class must be one of %s", label, pythonSet(behaviorClasses))
	}
	routing, _ := value["routing_class"].(string)
	if schema == 3 && !routingClasses[routing] {
		return Case{}, fmt.Errorf("%s.routing_class must be one of %s", label, pythonSet(routingClasses))
	}
	if schema == 3 {
		if routing == "should_trigger" && loading != "required" {
			return Case{}, fmt.Errorf("%s.should_trigger requires expected_skill_loading=required", label)
		}
		if routing == "should_not_trigger" && loading != "forbidden" {
			return Case{}, fmt.Errorf("%s.should_not_trigger requires expected_skill_loading=forbidden", label)
		}
		if routing == "ambiguous" && loading == "optional" {
			return Case{}, fmt.Errorf("%s.ambiguous routing must declare required or forbidden", label)
		}
	}
	rawGraders, ok := value["graders"].([]any)
	if !ok || len(rawGraders) == 0 {
		return Case{}, fmt.Errorf("%s.graders must be a non-empty list", label)
	}
	reference, ok := value["reference"].(map[string]any)
	if !ok {
		return Case{}, fmt.Errorf("%s.reference must be an object", label)
	}
	if response, exists := reference["response"]; exists {
		if _, ok := response.(string); !ok {
			return Case{}, fmt.Errorf("%s.reference.response must be a string", label)
		}
	}
	counter, hasCounter := value["counter_reference"]
	var counterMap map[string]any
	if hasCounter && counter != nil {
		counterMap, ok = counter.(map[string]any)
		if !ok {
			return Case{}, fmt.Errorf("%s.counter_reference must be an object", label)
		}
		response, exists := counterMap["response"]
		if !exists {
			return Case{}, fmt.Errorf("%s.counter_reference.response is required (empty objects are not allowed)", label)
		}
		if _, ok := response.(string); !ok {
			return Case{}, fmt.Errorf("%s.counter_reference.response must be a string", label)
		}
	} else {
		hasCounter = false
	}
	graders := make([]map[string]any, 0, len(rawGraders))
	seenNames := map[string]bool{}
	for index, raw := range rawGraders {
		grader, err := validateGrader(raw, label, index+1, schema, prompt)
		if err != nil {
			return Case{}, err
		}
		name := grader["name"].(string)
		if seenNames[name] {
			return Case{}, fmt.Errorf("%s has a duplicate grader name", label)
		}
		seenNames[name] = true
		graders = append(graders, grader)
	}
	responseCount := 0
	for _, grader := range graders {
		if responseSensitive[grader["type"].(string)] {
			responseCount++
		}
	}
	if discrimination == "case_contrast" && responseCount > 0 && !hasCounter {
		return Case{}, fmt.Errorf("%s.counter_reference is required when grader_discrimination=case_contrast", label)
	}
	if hasCounter && responseCount == 0 {
		return Case{}, fmt.Errorf("%s.counter_reference requires at least one response-sensitive grader (%s); file_exists/json_exact alone cannot discriminate a wrong response on the gold reference workspace", label, strings.Join(sortedKeys(responseSensitive), ", "))
	}
	contrast := Discrimination{ResponseSensitiveGraders: responseCount}
	if hasCounter && discrimination == "case_contrast" {
		var err error
		contrast, err = validateResponseContrast(label, reference, counterMap, graders)
		if err != nil {
			return Case{}, err
		}
	}
	return Case{
		Raw: value, ID: id, Prompt: strings.TrimSpace(prompt), BehaviorClass: behavior,
		RoutingClass: routing, ExpectedSkillLoading: loading, Graders: graders,
		Reference: reference, CounterReference: counterMap,
		HasCounterReference: hasCounter, Discrimination: contrast,
	}, nil
}

func validateGrader(raw any, caseLabel string, index, schema int, prompt string) (map[string]any, error) {
	label := fmt.Sprintf("%s.graders[%d]", caseLabel, index)
	grader, ok := raw.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s must be an object", label)
	}
	typeName, _ := grader["type"].(string)
	if !graderTypes[typeName] {
		return nil, fmt.Errorf("%s.type must be one of %s", label, pythonSet(graderTypes))
	}
	name, ok := grader["name"].(string)
	if !ok || strings.TrimSpace(name) == "" {
		return nil, fmt.Errorf("%s.name must be non-empty", label)
	}
	normalized := cloneMap(grader)
	normalized["name"] = strings.TrimSpace(name)
	switch typeName {
	case "response_contains", "response_not_contains", "response_regex", "markdown_table_column_regex":
		key := "value"
		if strings.Contains(typeName, "regex") {
			key = "pattern"
		}
		text, ok := grader[key].(string)
		if !ok || text == "" {
			return nil, fmt.Errorf("%s.%s must be non-empty", label, key)
		}
		if strings.Contains(typeName, "regex") {
			if _, err := regexp.Compile(text); err != nil {
				return nil, fmt.Errorf("%s.pattern is invalid: %v", label, err)
			}
		}
		if typeName == "markdown_table_column_regex" {
			column, ok := grader["column"].(string)
			if !ok || column == "" {
				return nil, fmt.Errorf("%s.column must be non-empty", label)
			}
		}
	case "file_exists", "json_exact":
		if _, ok := grader["path"].(string); !ok {
			return nil, fmt.Errorf("%s.path must be a string", label)
		}
		if typeName == "json_exact" {
			if _, exists := grader["expected"]; !exists {
				return nil, fmt.Errorf("%s.expected is required", label)
			}
		}
	case "model_rubric":
		rubric, hasRubric := grader["rubric"].(string)
		criteria, hasCriteria := grader["criteria"].([]any)
		if (!hasRubric || strings.TrimSpace(rubric) == "") && (!hasCriteria || len(criteria) == 0) {
			return nil, fmt.Errorf("%s needs a rubric or criteria", label)
		}
		if schema == 2 && (!hasRubric || strings.TrimSpace(rubric) == "") {
			return nil, fmt.Errorf("%s.rubric must be non-empty", label)
		}
		if schema == 3 {
			if !hasCriteria || len(criteria) == 0 {
				return nil, fmt.Errorf("%s.criteria must be a non-empty list", label)
			}
			requirements := []string{}
			for criterionIndex, rawCriterion := range criteria {
				criterionLabel := fmt.Sprintf("%s.criteria[%d]", label, criterionIndex+1)
				criterion, ok := rawCriterion.(map[string]any)
				if !ok {
					return nil, fmt.Errorf("%s must be an object", criterionLabel)
				}
				requirement, requirementOK := criterion["requirement"].(string)
				quote, quoteOK := criterion["prompt_quote"].(string)
				if !requirementOK || strings.TrimSpace(requirement) == "" {
					return nil, fmt.Errorf("%s.requirement must be non-empty", criterionLabel)
				}
				if !quoteOK || strings.TrimSpace(quote) == "" {
					return nil, fmt.Errorf("%s.prompt_quote must be non-empty", criterionLabel)
				}
				if !strings.Contains(strings.ToLower(prompt), strings.ToLower(strings.TrimSpace(quote))) {
					return nil, fmt.Errorf("%s.prompt_quote must appear in the prompt", criterionLabel)
				}
				requirements = append(requirements, strings.TrimSpace(requirement))
			}
			normalized["rubric"] = "Pass only if every requirement is met:\n- " + strings.Join(requirements, "\n- ")
		}
	}
	return normalized, nil
}

func validateResponseContrast(label string, reference, counter map[string]any, graders []map[string]any) (Discrimination, error) {
	referenceResponse, _ := reference["response"].(string)
	counterResponse, _ := counter["response"].(string)
	if strings.TrimSpace(referenceResponse) == "" {
		return Discrimination{}, fmt.Errorf("%s.reference.response must be non-empty for a grader contrast", label)
	}
	if strings.TrimSpace(counterResponse) == "" {
		return Discrimination{}, fmt.Errorf("%s.counter_reference.response must be non-empty for a grader contrast", label)
	}
	if strings.TrimSpace(referenceResponse) == strings.TrimSpace(counterResponse) {
		return Discrimination{}, fmt.Errorf("%s.counter_reference.response must differ from reference.response for a grader contrast", label)
	}
	result := Discrimination{}
	for _, grader := range graders {
		typeName := grader["type"].(string)
		if !responseSensitive[typeName] {
			continue
		}
		result.ResponseSensitiveGraders++
		if typeName == "model_rubric" {
			result.ModelGradersPendingRuntime++
			continue
		}
		if deterministicResponse[typeName] {
			result.DeterministicGradersChecked++
			passed, err := GradeResponse(referenceResponse, grader)
			if err != nil {
				return Discrimination{}, err
			}
			if !passed {
				return Discrimination{}, fmt.Errorf("%s grader contrast reference does not pass grader %q", label, grader["name"])
			}
			passed, err = GradeResponse(counterResponse, grader)
			if err != nil {
				return Discrimination{}, err
			}
			if passed {
				return Discrimination{}, fmt.Errorf("%s grader contrast counter_reference does not fail grader %q", label, grader["name"])
			}
		}
	}
	return result, nil
}

func GradeResponse(response string, grader map[string]any) (bool, error) {
	switch grader["type"] {
	case "response_contains":
		return strings.Contains(response, grader["value"].(string)), nil
	case "response_not_contains":
		return !strings.Contains(response, grader["value"].(string)), nil
	case "response_regex":
		return regexp.MatchString(grader["pattern"].(string), response)
	case "markdown_table_column_regex":
		column := markdownColumn(response, grader["column"].(string))
		return regexp.MatchString(grader["pattern"].(string), column)
	default:
		return false, fmt.Errorf("grader %v is not deterministic response grading", grader["type"])
	}
}

func markdownColumn(markdown, column string) string {
	lines := []string{}
	for _, line := range strings.Split(markdown, "\n") {
		line = strings.TrimSpace(line)
		if strings.Contains(line, "|") {
			lines = append(lines, line)
		}
	}
	for index, line := range lines {
		cells := tableCells(line)
		columnIndex := -1
		for current, cell := range cells {
			if cell == column {
				columnIndex = current
				break
			}
		}
		if columnIndex < 0 {
			continue
		}
		values := []string{}
		for _, row := range lines[index+2:] {
			rowCells := tableCells(row)
			if len(rowCells) <= columnIndex {
				break
			}
			values = append(values, strings.Trim(rowCells[columnIndex], `"“”`))
		}
		return strings.Join(values, "\n")
	}
	return ""
}

func tableCells(line string) []string {
	parts := strings.Split(strings.Trim(line, "|"), "|")
	for index := range parts {
		parts[index] = strings.TrimSpace(parts[index])
	}
	return parts
}

func loadProvenance(suiteRoot string, raw any, source string, caseIDs map[string]bool, caseHashes map[string]string, suiteHash, datasetOrigin string) (map[string]map[string]any, string, error) {
	value, _ := raw.(string)
	path, err := SafeRelativePath(suiteRoot, value, source+".provenance_manifest")
	if err != nil {
		return nil, "", err
	}
	data, err := readJSON(path)
	if err != nil {
		return nil, "", err
	}
	root, ok := data.(map[string]any)
	if !ok {
		return nil, "", fmt.Errorf("%s must be an object", path)
	}
	if schema, ok := numericMember(root["schema_version"], 1); !ok || schema != 1 {
		return nil, "", fmt.Errorf("%s must use schema_version 1", path)
	}
	records, ok := root["cases"].([]any)
	if !ok {
		return nil, "", fmt.Errorf("%s.cases must be a list", path)
	}
	byCase := map[string]map[string]any{}
	seenSource := map[string]bool{}
	for index, rawRecord := range records {
		label := fmt.Sprintf("%s.cases[%d]", path, index+1)
		record, ok := rawRecord.(map[string]any)
		if !ok {
			return nil, "", fmt.Errorf("%s must be an object", label)
		}
		caseID, _ := record["case_id"].(string)
		if !caseIDs[caseID] || byCase[caseID] != nil {
			return nil, "", fmt.Errorf("%s.case_id is unknown or duplicate", label)
		}
		origin, _ := record["origin"].(string)
		if origin != datasetOrigin {
			return nil, "", fmt.Errorf("%s.origin must match suite dataset_origin %q", label, datasetOrigin)
		}
		sourceID, _ := record["source_id"].(string)
		if strings.TrimSpace(sourceID) == "" {
			return nil, "", fmt.Errorf("%s.source_id must be non-empty", label)
		}
		if seenSource[sourceID] {
			return nil, "", fmt.Errorf("%s.source_id must be unique", label)
		}
		seenSource[sourceID] = true
		sourceType, _ := record["source_type"].(string)
		if !provenanceSourceTypes[sourceType] {
			return nil, "", fmt.Errorf("%s.source_type must be one of %s", label, pythonSet(provenanceSourceTypes))
		}
		allowed := map[string]map[string]bool{
			"author_derived":        stringSet("author_scenario"),
			"held_out":              stringSet("independent_task"),
			"production_regression": stringSet("production_trace", "user_correction", "incident"),
		}
		if !allowed[origin][sourceType] {
			return nil, "", fmt.Errorf("%s.source_type is inconsistent with origin %q", label, origin)
		}
		for _, key := range []string{"observed_at", "task_author"} {
			text, ok := record[key].(string)
			if !ok || strings.TrimSpace(text) == "" {
				return nil, "", fmt.Errorf("%s.%s must be non-empty", label, key)
			}
		}
		artifact, err := SafeRelativePath(suiteRoot, record["artifact"], label+".artifact")
		if err != nil {
			return nil, "", err
		}
		expectedArtifact, _ := record["artifact_sha256"].(string)
		if !sha256Pattern.MatchString(expectedArtifact) {
			return nil, "", fmt.Errorf("%s.artifact_sha256 must be a sha256", label)
		}
		if info, err := os.Stat(artifact); err != nil || !info.Mode().IsRegular() {
			return nil, "", fmt.Errorf("%s.artifact does not exist", label)
		}
		actualArtifact, err := FileSHA256(artifact)
		if err != nil {
			return nil, "", err
		}
		if actualArtifact != expectedArtifact {
			return nil, "", fmt.Errorf("%s.artifact_sha256 does not match artifact", label)
		}
		expectedCase, _ := record["case_sha256"].(string)
		if !sha256Pattern.MatchString(expectedCase) {
			return nil, "", fmt.Errorf("%s.case_sha256 must be a sha256", label)
		}
		if expectedCase != caseHashes[caseID] {
			return nil, "", fmt.Errorf("%s.case_sha256 does not match eval case", label)
		}
		normalized := cloneMap(record)
		relative, _ := filepath.Rel(suiteRoot, artifact)
		normalized["artifact"] = filepath.ToSlash(relative)
		byCase[caseID] = normalized
	}
	if len(byCase) != len(caseIDs) {
		missing := []string{}
		for caseID := range caseIDs {
			if byCase[caseID] == nil {
				missing = append(missing, caseID)
			}
		}
		sort.Strings(missing)
		return nil, "", fmt.Errorf("%s does not cover every eval case: missing %s", path, pythonStringList(missing))
	}
	expectedSuite, _ := root["suite_sha256"].(string)
	if !sha256Pattern.MatchString(expectedSuite) {
		return nil, "", fmt.Errorf("%s.suite_sha256 must be a sha256", path)
	}
	if expectedSuite != suiteHash {
		return nil, "", fmt.Errorf("%s.suite_sha256 does not match eval suite", path)
	}
	provenanceHash, err := FileSHA256(path)
	return byCase, provenanceHash, err
}

func SafeRelativePath(root string, value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || strings.TrimSpace(text) == "" {
		return "", fmt.Errorf("%s must be a non-empty relative path", label)
	}
	if filepath.IsAbs(text) {
		return "", fmt.Errorf("%s must stay below %s", label, root)
	}
	for _, part := range strings.FieldsFunc(text, func(r rune) bool { return r == '/' || r == '\\' }) {
		if part == ".." {
			return "", fmt.Errorf("%s must stay below %s", label, root)
		}
	}
	resolved, err := filepath.Abs(filepath.Join(root, text))
	if err != nil {
		return "", err
	}
	resolved, err = evalSymlinksAllowMissing(resolved)
	if err != nil {
		return "", err
	}
	rootResolved, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	relative, err := filepath.Rel(rootResolved, resolved)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("%s escapes %s", label, root)
	}
	return resolved, nil
}

func evalSymlinksAllowMissing(path string) (string, error) {
	missing := []string{}
	current := path
	for {
		_, err := os.Lstat(current)
		if err == nil {
			resolved, err := filepath.EvalSymlinks(current)
			if err != nil {
				return "", err
			}
			for index := len(missing) - 1; index >= 0; index-- {
				resolved = filepath.Join(resolved, missing[index])
			}
			return resolved, nil
		}
		if !errors.Is(err, os.ErrNotExist) {
			return "", err
		}
		parent := filepath.Dir(current)
		if parent == current {
			return "", err
		}
		missing = append(missing, filepath.Base(current))
		current = parent
	}
}

func CanonicalSHA256(value any) (string, error) {
	encoded, err := canonicalJSON(value)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}

func FileSHA256(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:]), nil
}

func canonicalJSON(value any) ([]byte, error) {
	var output bytes.Buffer
	if err := writeCanonical(&output, value); err != nil {
		return nil, err
	}
	return output.Bytes(), nil
}

func writeCanonical(output *bytes.Buffer, value any) error {
	switch typed := value.(type) {
	case nil:
		output.WriteString("null")
	case bool:
		if typed {
			output.WriteString("true")
		} else {
			output.WriteString("false")
		}
	case string:
		data, _ := json.Marshal(typed)
		data = bytes.ReplaceAll(data, []byte(`\u003c`), []byte("<"))
		data = bytes.ReplaceAll(data, []byte(`\u003e`), []byte(">"))
		data = bytes.ReplaceAll(data, []byte(`\u0026`), []byte("&"))
		output.Write(data)
	case json.Number:
		number, err := pythonNumber(typed.String())
		if err != nil {
			return err
		}
		output.WriteString(number)
	case []any:
		output.WriteByte('[')
		for index, item := range typed {
			if index > 0 {
				output.WriteByte(',')
			}
			if err := writeCanonical(output, item); err != nil {
				return err
			}
		}
		output.WriteByte(']')
	case map[string]any:
		output.WriteByte('{')
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for index, key := range keys {
			if index > 0 {
				output.WriteByte(',')
			}
			if err := writeCanonical(output, key); err != nil {
				return err
			}
			output.WriteByte(':')
			if err := writeCanonical(output, typed[key]); err != nil {
				return err
			}
		}
		output.WriteByte('}')
	default:
		return fmt.Errorf("unsupported canonical JSON value %T", value)
	}
	return nil
}

func pythonNumber(text string) (string, error) {
	if !strings.ContainsAny(text, ".eE") {
		return text, nil
	}
	value, err := strconv.ParseFloat(text, 64)
	if err != nil || math.IsInf(value, 0) || math.IsNaN(value) {
		return "", fmt.Errorf("invalid JSON number %q", text)
	}
	rendered := strconv.FormatFloat(value, 'g', -1, 64)
	if !strings.ContainsAny(rendered, ".eE") {
		rendered += ".0"
	}
	return rendered, nil
}

func readJSON(path string) (any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if !utf8.Valid(data) {
		return nil, fmt.Errorf("%s is not valid UTF-8", path)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		if err == nil {
			return nil, errors.New("multiple JSON values")
		}
		return nil, err
	}
	return value, nil
}

func numericMember(value any, members ...int) (int, bool) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	floatValue, err := strconv.ParseFloat(number.String(), 64)
	if err != nil {
		return 0, false
	}
	for _, member := range members {
		if floatValue == float64(member) {
			return member, true
		}
	}
	return 0, false
}

func stringDefault(value any, fallback string) string {
	if value == nil {
		return fallback
	}
	text, _ := value.(string)
	return text
}

func stringValue(value any) string { text, _ := value.(string); return text }
func cloneMap(value map[string]any) map[string]any {
	result := map[string]any{}
	for key, item := range value {
		result[key] = item
	}
	return result
}
func stringSet(values ...string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		result[value] = true
	}
	return result
}
func sortedKeys(values map[string]bool) []string {
	result := make([]string, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}
func pythonSet(values map[string]bool) string {
	keys := sortedKeys(values)
	quoted := make([]string, len(keys))
	for index, key := range keys {
		quoted[index] = "'" + key + "'"
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}
func pythonStringList(values []string) string {
	quoted := make([]string, len(values))
	for index, value := range values {
		quoted[index] = "'" + value + "'"
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}
