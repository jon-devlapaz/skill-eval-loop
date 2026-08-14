package simpleeval

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jon-devlapaz/skill-eval-loop/internal/skillpayload"
)

func TestPairedRunIsolatesSkillAndGradesConditions(t *testing.T) {
	root := t.TempDir()
	skill := filepath.Join(root, "skill")
	if err := os.MkdirAll(filepath.Join(skill, "references"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "SKILL.md"), []byte("# Skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "references", "guide.md"), []byte("guide\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	fixture := filepath.Join(root, "fixture")
	if err := os.Mkdir(fixture, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(fixture, "shared.txt"), []byte("same\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	task := Task{ID: "qualified", Prompt: "Choose the qualified candidate.", Graders: []Grader{{Type: "regex", Pattern: `\bBlue\b`}}}
	input := PairInput{
		Task: task, Trial: 1, SkillPath: skill, FixturePath: fixture,
		OutputDir: filepath.Join(root, "run-one"), Executable: fakeCodexPath(t),
		CodexHome: emptyCodexHome(t, root), Model: "gpt-5.6-sol", Timeout: time.Second,
	}
	pair, err := RunPair(context.Background(), input)
	if err != nil {
		t.Fatal(err)
	}
	if pair.Control.Grade.AllPassed || !pair.Treatment.Grade.AllPassed {
		t.Fatalf("unexpected grades: control=%+v treatment=%+v", pair.Control.Grade, pair.Treatment.Grade)
	}
	if !pair.ControlSkillAbsent || !pair.TreatmentSkillPresent || !pair.TreatmentHashMatches {
		t.Fatalf("isolation evidence missing: %+v", pair)
	}
	if got := strings.Join(pair.ExecutionOrder, " -> "); got != "control -> treatment" {
		t.Fatalf("unexpected trial 1 execution order: %s", got)
	}
	for _, path := range []string{pair.ReportJSONPath, pair.ReportMarkdownPath} {
		if info, err := os.Stat(path); err != nil || !info.Mode().IsRegular() {
			t.Fatalf("paired run did not retain report %s", path)
		}
	}
	report, err := os.ReadFile(pair.ReportMarkdownPath)
	if err != nil {
		t.Fatal(err)
	}
	for _, required := range []string{
		"Deterministic comparison: **treatment_only**",
		"Choose the qualified candidate.",
		`&#34;pattern&#34;: &#34;\\bBlue\\b&#34;`,
		"Control target skill absent: **true**",
		"Treatment installed/source hash match: **true**",
		"Trial: **1**",
		"Execution order: **control → treatment**",
		"trace_reported; matches requested",
		"[response](control/response.md)",
		"[response](treatment/response.md)",
		"Cost: **unknown**",
	} {
		if !strings.Contains(string(report), required) {
			t.Errorf("report does not contain %q", required)
		}
	}
	jsonReport, err := os.ReadFile(pair.ReportJSONPath)
	if err != nil {
		t.Fatal(err)
	}
	var retained struct {
		RunnerValid bool `json:"runner_valid"`
		Task        struct {
			Prompt  string            `json:"prompt"`
			Graders []json.RawMessage `json:"graders"`
		} `json:"task"`
		Isolation struct {
			ControlSkillAbsent    bool `json:"control_skill_absent"`
			TreatmentSkillPresent bool `json:"treatment_skill_present"`
			TreatmentHashMatches  bool `json:"treatment_installed_source_hash_match"`
		} `json:"isolation"`
		Cost       *float64          `json:"cost"`
		Conditions []json.RawMessage `json:"conditions"`
	}
	if err := json.Unmarshal(jsonReport, &retained); err != nil {
		t.Fatal(err)
	}
	if !retained.RunnerValid || retained.Cost != nil || len(retained.Conditions) != 2 {
		t.Fatalf("unexpected retained report: %+v", retained)
	}
	if retained.Task.Prompt != task.Prompt || len(retained.Task.Graders) != 1 {
		t.Fatalf("task contract missing from report: %+v", retained.Task)
	}
	if !retained.Isolation.ControlSkillAbsent || !retained.Isolation.TreatmentSkillPresent || !retained.Isolation.TreatmentHashMatches {
		t.Fatalf("isolation contract missing from report: %+v", retained.Isolation)
	}
	if strings.Contains(string(jsonReport), `"model_attested"`) || !strings.Contains(string(jsonReport), `"model_identity_source": "trace_reported"`) {
		t.Fatalf("model identity provenance is misleading: %s", jsonReport)
	}
	controlSkill := filepath.Join(input.OutputDir, "control", "workspace", ".agents", "skills", "skill")
	if _, err := os.Stat(controlSkill); !os.IsNotExist(err) {
		t.Fatalf("control can see target skill: %v", err)
	}
	installed := filepath.Join(input.OutputDir, "treatment", "workspace", ".agents", "skills", "skill")
	sourceHash, err := skillpayload.Hash(skill)
	if err != nil {
		t.Fatal(err)
	}
	installedHash, err := skillpayload.Hash(installed)
	if err != nil {
		t.Fatal(err)
	}
	if pair.SkillSHA256 != sourceHash || pair.Treatment.SkillSHA256 != sourceHash || installedHash != sourceHash {
		t.Fatalf("payload hashes differ: pair=%s treatment=%s installed=%s source=%s", pair.SkillSHA256, pair.Treatment.SkillSHA256, installedHash, sourceHash)
	}
	for _, condition := range []string{"control", "treatment"} {
		shared, err := os.ReadFile(filepath.Join(input.OutputDir, condition, "workspace", "shared.txt"))
		if err != nil || string(shared) != "same\n" {
			t.Fatalf("%s fixture differs: %q, %v", condition, shared, err)
		}
	}
}

func TestPairedRunTreatmentOutputChangesOnlyTreatmentGrade(t *testing.T) {
	root := t.TempDir()
	skill := filepath.Join(root, "skill")
	if err := os.Mkdir(skill, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "SKILL.md"), []byte("# Skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	task := Task{ID: "qualified", Prompt: "Choose.", Graders: []Grader{{Type: "regex", Pattern: `\bBlue\b`}}}
	base := PairInput{Task: task, Trial: 1, SkillPath: skill, CodexHome: emptyCodexHome(t, root), Executable: fakeCodexPath(t), Model: "gpt-5.6-sol", Timeout: time.Second}
	base.OutputDir = filepath.Join(root, "passing-treatment")
	passing, err := RunPair(context.Background(), base)
	if err != nil {
		t.Fatal(err)
	}
	base.OutputDir = filepath.Join(root, "failing-treatment")
	base.Trial = 2
	base.Environment = map[string]string{"SIMPLE_FAKE_TREATMENT_RESPONSE": "Red"}
	failing, err := RunPair(context.Background(), base)
	if err != nil {
		t.Fatal(err)
	}
	if passing.Control.Response != failing.Control.Response || passing.Control.Grade.AllPassed != failing.Control.Grade.AllPassed {
		t.Fatal("changing treatment output changed control")
	}
	if !passing.Treatment.Grade.AllPassed || failing.Treatment.Grade.AllPassed {
		t.Fatalf("treatment grade did not follow treatment response: passing=%+v failing=%+v", passing.Treatment.Grade, failing.Treatment.Grade)
	}
	if got := strings.Join(passing.ExecutionOrder, " -> "); got != "control -> treatment" {
		t.Fatalf("unexpected trial 1 execution order: %s", got)
	}
	if got := strings.Join(failing.ExecutionOrder, " -> "); got != "treatment -> control" {
		t.Fatalf("unexpected trial 2 execution order: %s", got)
	}
	trialTwoReport, err := os.ReadFile(failing.ReportMarkdownPath)
	if err != nil {
		t.Fatal(err)
	}
	for _, required := range []string{"Trial: **2**", "Execution order: **treatment → control**"} {
		if !strings.Contains(string(trialTwoReport), required) {
			t.Errorf("trial 2 report does not contain %q", required)
		}
	}
}

func TestPairedRunRejectsTargetSkillInAuthenticatedHomeBeforeWritingOutput(t *testing.T) {
	root := t.TempDir()
	skill := filepath.Join(root, "skill")
	if err := os.Mkdir(skill, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "SKILL.md"), []byte("# Skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	codexHome := emptyCodexHome(t, root)
	globalSkill := filepath.Join(codexHome, "skills", "skill")
	if err := os.MkdirAll(globalSkill, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(globalSkill, "SKILL.md"), []byte("# Global skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(root, "must-not-exist")
	_, err := RunPair(context.Background(), PairInput{
		Task:  Task{ID: "collision", Prompt: "Choose.", Graders: []Grader{{Type: "regex", Pattern: "Blue"}}},
		Trial: 1, SkillPath: skill, OutputDir: output, CodexHome: codexHome,
		Executable: fakeCodexPath(t), Model: "gpt-5.6-sol", Timeout: time.Second,
	})
	if err == nil || !strings.Contains(err.Error(), "target skill already exists") {
		t.Fatalf("expected authenticated-home collision, got %v", err)
	}
	if _, statErr := os.Stat(output); !os.IsNotExist(statErr) {
		t.Fatalf("output was written before collision failure: %v", statErr)
	}
}

func emptyCodexHome(t *testing.T, root string) string {
	t.Helper()
	path := filepath.Join(root, "codex-home")
	if err := os.Mkdir(path, 0o755); err != nil && !os.IsExist(err) {
		t.Fatal(err)
	}
	return path
}
