package main

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"testing"
)

func TestRunDryRunUsesMinimumContract(t *testing.T) {
	root := t.TempDir()
	skill := filepath.Join(root, "skill")
	if err := os.Mkdir(skill, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "SKILL.md"), []byte("# Skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	tasks := filepath.Join(root, "tasks.jsonl")
	data := "{\"id\":\"one\",\"prompt\":\"One\",\"graders\":[{\"type\":\"regex\",\"pattern\":\"Blue\"},{\"type\":\"rubric\",\"text\":\"Be safe.\"}]}\n" +
		"{\"id\":\"two\",\"prompt\":\"Two\",\"graders\":[{\"type\":\"not_regex\",\"pattern\":\"Red\"}]}\n"
	if err := os.WriteFile(tasks, []byte(data), 0o644); err != nil {
		t.Fatal(err)
	}
	harness := filepath.Join(root, "fake-codex")
	if err := os.WriteFile(harness, []byte("#!/bin/sh\n[ \"$#\" -eq 1 ] && [ \"$1\" = \"--version\" ] || exit 9\nprintf 'fake-codex 1.0\\n'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(root, "output")
	stdout, restore := captureStdout(t)
	code := runRun([]string{
		"--dry-run", "--skill", skill, "--tasks", tasks, "--output", output,
		"--harness", "codex", "--harness-bin", harness, "--model", "gpt-5.6-sol",
		"--judge-model", "gpt-5.6-sol", "--trials", "3", "--timeout-seconds", "120",
	})
	restore()
	if code != 0 {
		t.Fatalf("exit code=%d", code)
	}
	var report struct {
		Valid            bool `json:"valid"`
		CreatedArtifacts bool `json:"created_artifacts"`
		ProviderCalls    int  `json:"provider_calls"`
		Counts           struct {
			TaskCount         int `json:"task_count"`
			PairedTrials      int `json:"paired_trials"`
			TargetInvocations int `json:"target_invocations"`
			JudgeInvocations  int `json:"judge_invocations"`
			TotalInvocations  int `json:"total_invocations"`
		} `json:"counts"`
	}
	if err := json.Unmarshal(stdout(), &report); err != nil {
		t.Fatal(err)
	}
	if !report.Valid || report.CreatedArtifacts || report.ProviderCalls != 0 {
		t.Fatalf("unexpected dry-run: %+v", report)
	}
	if report.Counts.TaskCount != 2 || report.Counts.PairedTrials != 6 || report.Counts.TargetInvocations != 12 || report.Counts.JudgeInvocations != 6 || report.Counts.TotalInvocations != 18 {
		t.Fatalf("unexpected counts: %+v", report.Counts)
	}
	if _, err := os.Stat(output); !os.IsNotExist(err) {
		t.Fatalf("CLI dry-run created output: %v", err)
	}
}

func TestRunLiveUsesMinimumContract(t *testing.T) {
	root := t.TempDir()
	skill := filepath.Join(root, "skill")
	if err := os.Mkdir(skill, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skill, "SKILL.md"), []byte("# Skill\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	tasks := filepath.Join(root, "tasks.jsonl")
	data := "{\"id\":\"one\",\"prompt\":\"One\",\"graders\":[{\"type\":\"regex\",\"pattern\":\"Blue\"}]}\n" +
		"{\"id\":\"two\",\"prompt\":\"Two\",\"graders\":[{\"type\":\"not_regex\",\"pattern\":\"Green\"}]}\n"
	if err := os.WriteFile(tasks, []byte(data), 0o644); err != nil {
		t.Fatal(err)
	}
	harness := filepath.Join(root, "fake-codex")
	source, err := os.ReadFile(filepath.Join("..", "..", "conformance", "scenarios", "fixtures", "simple-fake-codex"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(harness, source, 0o755); err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(root, "output")
	stdout, restore := captureStdout(t)
	code := runRun([]string{
		"--skill", skill, "--tasks", tasks, "--output", output,
		"--harness", "codex", "--harness-bin", harness, "--model", "gpt-5.6-sol",
		"--trials", "2", "--timeout-seconds", "10",
	})
	restore()
	if code != 0 {
		t.Fatalf("exit code=%d", code)
	}
	var report struct {
		Valid  bool `json:"valid"`
		Counts struct {
			TargetInvocations int `json:"target_invocations"`
		} `json:"counts"`
		Pairs []struct {
			Trial          int      `json:"trial"`
			RunnerValid    bool     `json:"runner_valid"`
			ExecutionOrder []string `json:"execution_order"`
		} `json:"pairs"`
	}
	if err := json.Unmarshal(stdout(), &report); err != nil {
		t.Fatal(err)
	}
	if !report.Valid || report.Counts.TargetInvocations != 8 || len(report.Pairs) != 4 {
		t.Fatalf("unexpected live report: %+v", report)
	}
	for _, pair := range report.Pairs {
		if !pair.RunnerValid || len(pair.ExecutionOrder) != 2 {
			t.Errorf("unexpected pair: %+v", pair)
		}
	}
	traces, err := filepath.Glob(filepath.Join(output, "task-*", "trial-*", "*", "trace.jsonl"))
	if err != nil || len(traces) != 8 {
		t.Fatalf("target invocation traces=%d err=%v", len(traces), err)
	}
}

func captureStdout(t *testing.T) (func() []byte, func()) {
	t.Helper()
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	original := os.Stdout
	os.Stdout = writer
	return func() []byte {
			data, readErr := io.ReadAll(reader)
			if readErr != nil {
				t.Fatal(readErr)
			}
			return data
		}, func() {
			if err := writer.Close(); err != nil {
				t.Fatal(err)
			}
			os.Stdout = original
		}
}
