package recommend

import "testing"

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
