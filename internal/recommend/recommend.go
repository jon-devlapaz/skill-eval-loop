package recommend

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"time"
)

type Model struct {
	ID          string `json:"id"`
	Tier        string `json:"tier"`
	Source      string `json:"source"`
	Description string `json:"description"`
}
type Input struct {
	Harness           string
	Models            []Model
	TaskProfile       string
	CaseCount         int
	ModelRubricCounts []int
	CounterReferences []bool
	Trials            int
}

var tokenPattern = regexp.MustCompile(`[a-z]+`)
var budgetMarkers = wordSet("luna", "mini", "haiku", "flash", "spark", "small")
var qualityMarkers = wordSet("sol", "opus", "ultra", "max", "pro")

func InferTier(modelID, description string) string {
	leaf := modelID
	if index := strings.LastIndex(leaf, "/"); index >= 0 {
		leaf = leaf[index+1:]
	}
	tokens := tokenPattern.FindAllString(strings.ToLower(leaf+" "+description), -1)
	for _, token := range tokens {
		if budgetMarkers[token] {
			return "budget"
		}
	}
	for _, token := range tokens {
		if qualityMarkers[token] {
			return "quality"
		}
	}
	return "balanced"
}
func ParseExplicit(value string) []Model {
	models := []Model{}
	for _, item := range strings.Split(value, ",") {
		id := strings.TrimSpace(item)
		if id != "" {
			models = append(models, Model{ID: id, Tier: InferTier(id, ""), Source: "user-supplied inventory"})
		}
	}
	return uniqueSorted(models)
}

func ParsePiModels(output string) []Model {
	models := []Model{}
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 || (fields[0] == "provider" && fields[1] == "model") {
			continue
		}
		id := fields[0] + "/" + fields[1]
		models = append(models, Model{ID: id, Tier: InferTier(id, ""), Source: "pi --list-models"})
	}
	return models
}

func DiscoverPi(executable string) ([]Model, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	output, err := exec.CommandContext(ctx, executable, "--list-models").Output()
	if err != nil {
		return nil, err
	}
	return uniqueSorted(ParsePiModels(string(output))), nil
}

func uniqueSorted(models []Model) []Model {
	unique := map[string]Model{}
	for _, model := range models {
		unique[model.ID] = model
	}
	result := make([]Model, 0, len(unique))
	for _, model := range unique {
		result = append(result, model)
	}
	sort.Slice(result, func(i, j int) bool {
		left, right := tierIndex(result[i].Tier), tierIndex(result[j].Tier)
		if left != right {
			return left < right
		}
		return result[i].ID < result[j].ID
	})
	return result
}

func Build(input Input) (map[string]any, error) {
	if len(input.Models) == 0 {
		return nil, fmt.Errorf("model inventory is empty")
	}
	if !wordSet("simple", "standard", "complex", "portability")[input.TaskProfile] {
		return nil, fmt.Errorf("unsupported task profile")
	}
	if input.CaseCount < 0 || len(input.ModelRubricCounts) != input.CaseCount || len(input.CounterReferences) != input.CaseCount {
		return nil, fmt.Errorf("per-case vectors must match case count")
	}
	if input.Trials < 1 {
		return nil, fmt.Errorf("trials must be a positive integer")
	}
	tiered := map[string][]Model{"budget": {}, "balanced": {}, "quality": {}}
	for _, model := range input.Models {
		tiered[model.Tier] = append(tiered[model.Tier], model)
	}
	for tier := range tiered {
		sort.Slice(tiered[tier], func(i, j int) bool { return tiered[tier][i].ID < tiered[tier][j].ID })
	}
	budget := pick(tiered, "budget", "balanced", "quality")
	balanced := pick(tiered, "balanced", "quality", "budget")
	quality := pick(tiered, "quality", "balanced", "budget")
	frontier := uniqueStrings([]string{budget.ID, balanced.ID, quality.ID})
	preference := map[string][]string{"simple": {"budget", "balanced", "quality"}, "standard": {"balanced", "quality", "budget"}, "complex": {"quality", "balanced", "budget"}, "portability": {"balanced", "quality", "budget"}}[input.TaskProfile]
	target := any(nil)
	targets := []string{}
	if input.TaskProfile == "portability" {
		targets = frontier
	} else {
		target = pick(tiered, preference...).ID
	}
	modelRubrics := 0
	counterCount := 0
	for index, count := range input.ModelRubricCounts {
		if count < 0 {
			return nil, fmt.Errorf("model_rubric_counts must contain non-negative integers")
		}
		modelRubrics += count
		if input.CounterReferences[index] {
			counterCount += count
		}
	}
	judge := any(nil)
	if modelRubrics > 0 {
		judge = quality.ID
	}
	targetCalls := 2 * input.Trials * input.CaseCount
	conditionJudges := 2 * input.Trials * modelRubrics
	references := modelRubrics
	total := targetCalls + conditionJudges + references + counterCount
	counts := map[string]any{"target": targetCalls, "condition_judges": conditionJudges, "references": references, "counter_references": counterCount, "judge": conditionJudges + references + counterCount, "total": total}
	inventory := make([]any, len(input.Models))
	for index, model := range input.Models {
		inventory[index] = model
	}
	independence := "not_needed"
	if judge != nil {
		if target != nil && judge == target {
			independence = "same_model"
		} else {
			independence = "different_model"
		}
	}
	return map[string]any{
		"harness": input.Harness, "task_profile": input.TaskProfile,
		"inventory":          inventory,
		"frontier":           map[string]any{"budget": budget.ID, "balanced": balanced.ID, "quality": quality.ID},
		"frontier_fallbacks": map[string]any{"budget": budget.Tier != "budget", "balanced": balanced.Tier != "balanced", "quality": quality.Tier != "quality"},
		"recommended_target": target, "recommended_targets": stringsAny(targets),
		"recommended_judge": judge, "judge_independence": independence,
		"pilot_trials": input.Trials, "pilot_harness_invocations": total,
		"pilot_harness_invocation_counts": counts,
		"full_run_harness_invocations":    nil, "provider_model_calls": "unknown",
		"cost":                  "unknown unless the selected harness reports pricing",
		"confirmation_required": true,
		"limits": []any{
			"Tier labels are transparent name/description heuristics, not measured quality.",
			"Availability does not prove sufficient quota for the planned run.",
			"Use the intended deployment model for release claims.",
		},
	}, nil
}

type frontierOutput struct {
	Budget   string `json:"budget"`
	Balanced string `json:"balanced"`
	Quality  string `json:"quality"`
}
type fallbackOutput struct {
	Budget   bool `json:"budget"`
	Balanced bool `json:"balanced"`
	Quality  bool `json:"quality"`
}
type countOutput struct {
	Target            int `json:"target"`
	ConditionJudges   int `json:"condition_judges"`
	References        int `json:"references"`
	CounterReferences int `json:"counter_references"`
	Judge             int `json:"judge"`
	Total             int `json:"total"`
}
type output struct {
	Harness            string         `json:"harness"`
	TaskProfile        string         `json:"task_profile"`
	Inventory          []Model        `json:"inventory"`
	Frontier           frontierOutput `json:"frontier"`
	Fallbacks          fallbackOutput `json:"frontier_fallbacks"`
	RecommendedTarget  any            `json:"recommended_target"`
	RecommendedTargets []string       `json:"recommended_targets"`
	RecommendedJudge   any            `json:"recommended_judge"`
	JudgeIndependence  string         `json:"judge_independence"`
	PilotTrials        int            `json:"pilot_trials"`
	PilotInvocations   int            `json:"pilot_harness_invocations"`
	Counts             countOutput    `json:"pilot_harness_invocation_counts"`
	Full               any            `json:"full_run_harness_invocations"`
	ProviderCalls      string         `json:"provider_model_calls"`
	Cost               string         `json:"cost"`
	Confirmation       bool           `json:"confirmation_required"`
	Limits             []string       `json:"limits"`
	HarnessVersion     string         `json:"harness_version"`
	SkillName          string         `json:"skill_name"`
	CaseCount          int            `json:"case_count"`
	ModelRubricCount   int            `json:"model_rubric_count"`
}

func Bytes(report map[string]any, harnessVersion, skillName string, caseCount, modelRubricCount int) ([]byte, error) {
	frontier := report["frontier"].(map[string]any)
	fallbacks := report["frontier_fallbacks"].(map[string]any)
	counts := report["pilot_harness_invocation_counts"].(map[string]any)
	models := []Model{}
	for _, raw := range report["inventory"].([]any) {
		models = append(models, raw.(Model))
	}
	targets := []string{}
	for _, raw := range report["recommended_targets"].([]any) {
		targets = append(targets, raw.(string))
	}
	limits := []string{}
	for _, raw := range report["limits"].([]any) {
		limits = append(limits, raw.(string))
	}
	value := output{
		Harness: report["harness"].(string), TaskProfile: report["task_profile"].(string),
		Inventory: models,
		Frontier: frontierOutput{
			Budget: frontier["budget"].(string), Balanced: frontier["balanced"].(string),
			Quality: frontier["quality"].(string),
		},
		Fallbacks: fallbackOutput{
			Budget: fallbacks["budget"].(bool), Balanced: fallbacks["balanced"].(bool),
			Quality: fallbacks["quality"].(bool),
		},
		RecommendedTarget: report["recommended_target"], RecommendedTargets: targets,
		RecommendedJudge:  report["recommended_judge"],
		JudgeIndependence: report["judge_independence"].(string),
		PilotTrials:       report["pilot_trials"].(int),
		PilotInvocations:  report["pilot_harness_invocations"].(int),
		Counts: countOutput{
			Target: counts["target"].(int), ConditionJudges: counts["condition_judges"].(int),
			References: counts["references"].(int), CounterReferences: counts["counter_references"].(int),
			Judge: counts["judge"].(int), Total: counts["total"].(int),
		},
		Full: nil, ProviderCalls: "unknown",
		Cost:         "unknown unless the selected harness reports pricing",
		Confirmation: true, Limits: limits, HarnessVersion: harnessVersion,
		SkillName: skillName, CaseCount: caseCount, ModelRubricCount: modelRubricCount,
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(data, '\n'), nil
}

func pick(tiered map[string][]Model, preference ...string) Model {
	for _, tier := range preference {
		items := tiered[tier]
		if len(items) > 0 {
			return items[len(items)-1]
		}
	}
	panic("empty inventory")
}
func tierIndex(value string) int {
	switch value {
	case "budget":
		return 0
	case "balanced":
		return 1
	default:
		return 2
	}
}
func uniqueStrings(values []string) []string {
	seen := map[string]bool{}
	result := []string{}
	for _, value := range values {
		if !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	return result
}
func stringsAny(values []string) []any {
	result := make([]any, len(values))
	for index, value := range values {
		result[index] = value
	}
	return result
}
func wordSet(values ...string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		result[value] = true
	}
	return result
}
