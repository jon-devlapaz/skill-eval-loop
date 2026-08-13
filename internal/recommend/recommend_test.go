package recommend

import (
	"reflect"
	"testing"
)

func TestStandardExplicitInventoryRecommendation(t *testing.T) {
	models := ParseExplicit("provider/model-luna,provider/model-balanced,provider/model-sol")
	report, err := Build(Input{
		Harness: "pi", Models: models, TaskProfile: "standard",
		CaseCount: 1, ModelRubricCounts: []int{0}, CounterReferences: []bool{false}, Trials: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	if report["recommended_target"] != "provider/model-balanced" || report["recommended_judge"] != nil {
		t.Fatalf("report = %#v", report)
	}
	counts := report["pilot_harness_invocation_counts"].(map[string]any)
	if counts["target"] != 2 || counts["total"] != 2 {
		t.Fatalf("counts = %#v", counts)
	}
}

func TestPortabilityAndFallbackRemainExplicit(t *testing.T) {
	report, err := Build(Input{Harness: "pi", Models: ParseExplicit("provider/model-balanced"), TaskProfile: "portability", CaseCount: 1, ModelRubricCounts: []int{1}, CounterReferences: []bool{true}, Trials: 1})
	if err != nil {
		t.Fatal(err)
	}
	if report["recommended_target"] != nil || report["recommended_judge"] != "provider/model-balanced" {
		t.Fatalf("report=%#v", report)
	}
	fallbacks := report["frontier_fallbacks"].(map[string]any)
	if fallbacks["budget"] != true || fallbacks["quality"] != true {
		t.Fatalf("fallbacks=%#v", fallbacks)
	}
	if report["pilot_harness_invocations"] != 6 {
		t.Fatalf("report=%#v", report)
	}
}

func TestInferTierUsesLeafTokensNotProviderOrSubstrings(t *testing.T) {
	if got := InferTier("quality-provider/ordinary", ""); got != "balanced" {
		t.Fatalf("tier=%s", got)
	}
	if got := InferTier("provider/prototype", ""); got != "balanced" {
		t.Fatalf("tier=%s", got)
	}
}

func TestParsePiModelsUsesExactProviderModelIDs(t *testing.T) {
	models := ParsePiModels(`provider model context max-out thinking images
openai-codex gpt-5.6-luna 272K 128K yes yes
openai-codex gpt-5.6-terra 272K 128K yes yes
openai-codex gpt-5.6-sol 272K 128K yes yes
`)
	want := []string{
		"openai-codex/gpt-5.6-luna",
		"openai-codex/gpt-5.6-terra",
		"openai-codex/gpt-5.6-sol",
	}
	if len(models) != len(want) {
		t.Fatalf("models=%#v", models)
	}
	for index := range want {
		if models[index].ID != want[index] || models[index].Source != "pi --list-models" {
			t.Fatalf("models[%d]=%#v", index, models[index])
		}
	}
}

func TestParseCodexCacheFiltersVisibilityAndUsesDescription(t *testing.T) {
	models, err := ParseCodexCache([]byte(`{
  "fetched_at": "2026-08-13T12:00:00Z",
  "models": [
    {"slug": "gpt-main", "description": "pro reasoning", "visibility": "list"},
    {"slug": "gpt-hidden", "description": "", "visibility": "hide"},
    {"slug": "gpt-default", "description": ""},
    {"slug": 7, "description": "ignored"}
  ]
}`))
	if err != nil {
		t.Fatal(err)
	}
	want := []Model{
		{ID: "gpt-main", Tier: "quality", Source: "Codex authenticated cache (2026-08-13T12:00:00Z)", Description: "pro reasoning"},
		{ID: "gpt-default", Tier: "balanced", Source: "Codex authenticated cache (2026-08-13T12:00:00Z)"},
	}
	if !reflect.DeepEqual(models, want) {
		t.Fatalf("models=%#v", models)
	}
}
