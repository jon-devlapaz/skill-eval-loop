package simpleeval

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestCodexRetainsConditionEvidence(t *testing.T) {
	fake := fakeCodexPath(t)
	root := t.TempDir()
	codexHome := filepath.Join(root, "authenticated-codex-home")
	if err := os.Mkdir(codexHome, 0o755); err != nil {
		t.Fatal(err)
	}
	prompt := "Choose the qualified candidate."
	control, err := runCodex(context.Background(), codexInput{
		ConditionDir: filepath.Join(root, "control"), Workspace: filepath.Join(root, "control", "workspace"),
		CodexHome: codexHome, Executable: fake, Model: "gpt-5.6-sol", Prompt: prompt, SkillName: "skill", Timeout: time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}
	treatmentWorkspace := filepath.Join(root, "treatment", "workspace")
	if err := os.MkdirAll(filepath.Join(treatmentWorkspace, ".agents", "skills", "skill"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(treatmentWorkspace, ".agents", "skills", "skill", "SKILL.md"), []byte("# Skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	treatment, err := runCodex(context.Background(), codexInput{
		ConditionDir: filepath.Join(root, "treatment"), Workspace: treatmentWorkspace,
		CodexHome: codexHome, Executable: fake, Model: "gpt-5.6-sol", Prompt: prompt, SkillName: "skill", Timeout: time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}

	if control.Response != "Red" || treatment.Response != "Blue" {
		t.Fatalf("responses: control=%q treatment=%q", control.Response, treatment.Response)
	}
	for name, result := range map[string]ConditionResult{"control": control, "treatment": treatment} {
		if result.ExitCode != 0 || result.TimedOut || result.Duration <= 0 {
			t.Errorf("%s execution metadata: %+v", name, result)
		}
		if !result.ModelAttested || result.ActualModel != "gpt-5.6-sol" || result.SessionID == "" {
			t.Errorf("%s model metadata: %+v", name, result)
		}
		if result.InputTokens == nil || *result.InputTokens != 11 || result.OutputTokens == nil ||
			*result.OutputTokens != 2 || result.TotalTokens == nil || *result.TotalTokens != 13 {
			t.Errorf("%s usage metadata: %+v", name, result)
		}
		for _, path := range []string{result.ResponsePath, result.TracePath, result.StderrPath} {
			if info, err := os.Stat(path); err != nil || !info.Mode().IsRegular() {
				t.Errorf("%s did not retain %s", name, path)
			}
		}
	}
	controlStderr, _ := os.ReadFile(control.StderrPath)
	treatmentStderr, _ := os.ReadFile(treatment.StderrPath)
	if string(controlStderr) != string(treatmentStderr) || !strings.Contains(string(controlStderr), "--sandbox read-only") || !strings.Contains(string(controlStderr), "--ephemeral") {
		t.Fatalf("conditions used different command postures:\ncontrol: %s\ntreatment: %s", controlStderr, treatmentStderr)
	}
}

func TestCodexReferencesAuthenticatedHomeWithoutRetainingCredentials(t *testing.T) {
	root := t.TempDir()
	codexHome := filepath.Join(root, "authenticated-codex-home")
	if err := os.Mkdir(codexHome, 0o755); err != nil {
		t.Fatal(err)
	}
	sentinel := []byte("test-credential-sentinel")
	if err := os.WriteFile(filepath.Join(codexHome, "auth.json"), sentinel, 0o600); err != nil {
		t.Fatal(err)
	}
	conditionDir := filepath.Join(root, "retained", "control")
	result, err := runCodex(context.Background(), codexInput{
		ConditionDir: conditionDir,
		Workspace:    filepath.Join(conditionDir, "workspace"),
		CodexHome:    codexHome,
		Executable:   environmentReportingCodexPath(t),
		Model:        "gpt-5.6-sol",
		Prompt:       "Choose.",
		SkillName:    "skill",
		Timeout:      time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}
	reportedHome, err := os.ReadFile(result.StderrPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(reportedHome)) != codexHome {
		t.Fatalf("child received CODEX_HOME %q, want %q", strings.TrimSpace(string(reportedHome)), codexHome)
	}
	if _, err := os.Stat(filepath.Join(conditionDir, "codex-home")); !os.IsNotExist(err) {
		t.Fatalf("retained a condition-local Codex home: %v", err)
	}
	err = filepath.Walk(conditionDir, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if info.Mode().IsRegular() {
			contents, readErr := os.ReadFile(path)
			if readErr != nil {
				return readErr
			}
			if strings.Contains(string(contents), string(sentinel)) {
				t.Errorf("credential sentinel retained in %s", path)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func environmentReportingCodexPath(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "environment-reporting-codex")
	script := `#!/bin/sh
set -eu
printf '%s\n' "$CODEX_HOME" >&2
printf '{"type":"system","subtype":"init","model":"gpt-5.6-sol"}\n'
printf '{"type":"thread.started","thread_id":"test-thread"}\n'
printf '{"type":"item.completed","item":{"type":"agent_message","text":"Blue"}}\n'
printf '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
`
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestCodexOwnsIsolationEnvironment(t *testing.T) {
	values := environmentWith(
		map[string]string{"HOME": "/caller/home", "CODEX_HOME": "/caller/codex", "USER_VALUE": "kept"},
		map[string]string{"HOME": "/isolated/home", "CODEX_HOME": "/isolated/codex"},
	)
	got := map[string]string{}
	for _, value := range values {
		for _, key := range []string{"HOME", "CODEX_HOME", "USER_VALUE"} {
			prefix := key + "="
			if strings.HasPrefix(value, prefix) {
				got[key] = strings.TrimPrefix(value, prefix)
			}
		}
	}
	if got["HOME"] != "/isolated/home" || got["CODEX_HOME"] != "/isolated/codex" || got["USER_VALUE"] != "kept" {
		t.Fatalf("unexpected environment: %+v", got)
	}
}

func fakeCodexPath(t *testing.T) string {
	t.Helper()
	_, current, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test path")
	}
	path := filepath.Join(filepath.Dir(current), "..", "..", "conformance", "scenarios", "fixtures", "simple-fake-codex")
	path, err := filepath.Abs(path)
	if err != nil {
		t.Fatal(err)
	}
	return path
}
