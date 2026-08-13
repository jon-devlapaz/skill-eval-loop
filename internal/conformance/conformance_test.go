package conformance

import (
	"context"
	"encoding/base64"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestCompareCapturesEquivalentRawEvidence(t *testing.T) {
	requireUnix(t)
	directory := t.TempDir()
	oracle := writeDriver(t, directory, "oracle", driverScript("hello"))
	candidate := writeDriver(t, directory, "candidate", driverScript("hello"))
	scenario := writeScenario(t, directory, `{
  "name": "capture",
  "command": "audit",
  "args": ["--flag", "value"],
  "stdin_base64": "aW5wdXQK",
  "environment": {"VISIBLE": "yes"},
  "unset_environment": ["SHOULD_BE_UNSET"]
}`)

	report, err := Compare(context.Background(), Options{
		Oracle: oracle, Candidate: candidate, ScenarioPath: scenario,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !report.Equivalent {
		t.Fatalf("expected equivalent snapshots: %s", report.Difference)
	}
	if report.Oracle.Invocation.Executable == report.Candidate.Invocation.Executable {
		t.Fatal("raw evidence must retain distinct implementation paths")
	}
	if report.Oracle.Invocation.Argv[0] != report.Oracle.Invocation.Executable ||
		strings.Contains(report.Oracle.Invocation.SelectedEnvironment[subprocessLogEnv], "$RUN_ROOT") {
		t.Fatalf("raw evidence was normalized: %#v", report.Oracle.Invocation)
	}
	if report.Oracle.Invocation.CWD != report.Candidate.Invocation.CWD {
		t.Fatalf("implementations did not replay at one cwd: %q != %q", report.Oracle.Invocation.CWD, report.Candidate.Invocation.CWD)
	}
	if got := string(decode(t, report.Oracle.StdoutBase64)); got != "hello\n" {
		t.Fatalf("stdout = %q", got)
	}
	if len(report.Oracle.Subprocesses) != 1 {
		t.Fatalf("subprocesses = %#v", report.Oracle.Subprocesses)
	}
	assertEntry(t, report.Oracle.Filesystem, "result/data.bin", "file", 0o640)
	assertEntry(t, report.Oracle.Filesystem, "result/data-link", "symlink", 0)
}

func TestCompareRejectsSameExecutable(t *testing.T) {
	requireUnix(t)
	directory := t.TempDir()
	driver := writeDriver(t, directory, "driver", driverScript("hello"))
	_, err := Compare(context.Background(), Options{Oracle: driver, Candidate: driver})
	if err == nil || !strings.Contains(err.Error(), "same executable") {
		t.Fatalf("error = %v", err)
	}
}

func TestExpandWorkspaceInArgumentsAndEnvironment(t *testing.T) {
	scenario := expandWorkspace(Scenario{
		Args:        []string{"$WORKSPACE/fake"},
		Environment: map[string]string{"FIXTURE": "$WORKSPACE/data"},
	}, "/tmp/workspace")
	if scenario.Args[0] != "/tmp/workspace/fake" || scenario.Environment["FIXTURE"] != "/tmp/workspace/data" {
		t.Fatalf("scenario=%#v", scenario)
	}
}

func TestCompareFailsWhenImplementationIsAbsent(t *testing.T) {
	requireUnix(t)
	directory := t.TempDir()
	driver := writeDriver(t, directory, "driver", driverScript("hello"))
	_, err := Compare(context.Background(), Options{
		Oracle: driver, Candidate: filepath.Join(directory, "absent"),
	})
	if err == nil || !strings.Contains(err.Error(), "implementation is absent") {
		t.Fatalf("error = %v", err)
	}
}

func TestCompareReportsRawOutputDifference(t *testing.T) {
	requireUnix(t)
	directory := t.TempDir()
	oracle := writeDriver(t, directory, "oracle", driverScript("left"))
	candidate := writeDriver(t, directory, "candidate", driverScript("right"))
	scenario := writeScenario(t, directory, `{"name":"different","command":"audit"}`)
	report, err := Compare(context.Background(), Options{
		Oracle: oracle, Candidate: candidate, ScenarioPath: scenario,
	})
	if err != nil {
		t.Fatal(err)
	}
	if report.Equivalent || !strings.Contains(report.Difference, "first difference") {
		t.Fatalf("report = %#v", report)
	}
}

func TestRunTimeoutKillsDelayedDescendantSideEffect(t *testing.T) {
	requireUnix(t)
	directory := t.TempDir()
	driver := writeDriver(t, directory, "descendant", `#!/bin/sh
set -eu
(sleep 0.4; printf 'escaped\n' > "$SIDE_EFFECT") &
sleep 5
`)
	sideEffect := filepath.Join(directory, "escaped.txt")
	snapshot, err := run(context.Background(), driver, Scenario{
		Name:      "descendant-timeout",
		Command:   "run",
		TimeoutMS: 50,
		Environment: map[string]string{
			"SIDE_EFFECT": sideEffect,
		},
	}, directory)
	if err != nil {
		t.Fatal(err)
	}
	if !snapshot.TimedOut {
		t.Fatalf("snapshot = %#v", snapshot)
	}
	time.Sleep(500 * time.Millisecond)
	if _, err := os.Stat(sideEffect); !os.IsNotExist(err) {
		t.Fatalf("descendant side effect survived: %v", err)
	}
}

func TestLoadScenarioRejectsUnknownFieldsAndTrailingValues(t *testing.T) {
	directory := t.TempDir()
	unknown := writeScenario(t, directory, `{"name":"x","command":"audit","extra":true}`)
	if _, err := LoadScenario(unknown); err == nil {
		t.Fatal("unknown field was accepted")
	}
	trailing := writeScenario(t, directory, `{"name":"x","command":"audit"} {}`)
	if _, err := LoadScenario(trailing); err == nil {
		t.Fatal("trailing JSON value was accepted")
	}
}

func TestCheckedInScenariosLoad(t *testing.T) {
	paths, err := filepath.Glob(filepath.Join("..", "..", "conformance", "scenarios", "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(paths) == 0 {
		t.Fatal("no checked-in conformance scenarios")
	}
	for _, path := range paths {
		t.Run(filepath.Base(path), func(t *testing.T) {
			if _, err := LoadScenario(path); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func requireUnix(t *testing.T) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("migration target is macOS/Linux")
	}
}

func writeDriver(t *testing.T, directory, name, body string) string {
	t.Helper()
	path := filepath.Join(directory, name)
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func writeScenario(t *testing.T, directory, body string) string {
	t.Helper()
	path := filepath.Join(directory, strings.ReplaceAll(t.Name(), "/", "-")+".json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func driverScript(output string) string {
	return `#!/bin/sh
set -eu
printf '` + output + `\n'
mkdir -p result
printf '\001\002bytes' > result/data.bin
chmod 0640 result/data.bin
ln -s data.bin result/data-link
printf '{"executable":"fake-harness","argv":["fake-harness","--model","fixed"],"cwd":"%s","order":1,"status":0,"timed_out":false}\n' "$PWD" >> "$SKILL_EVAL_CONFORMANCE_LOG"
`
}

func decode(t *testing.T, value string) []byte {
	t.Helper()
	data, err := base64.StdEncoding.DecodeString(value)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func assertEntry(t *testing.T, entries []TreeEntry, path, entryType string, mode uint32) {
	t.Helper()
	for _, entry := range entries {
		if entry.Path == path {
			if entry.Type != entryType || mode != 0 && entry.Mode != mode {
				t.Fatalf("entry = %#v", entry)
			}
			return
		}
	}
	t.Fatalf("missing tree entry %s", path)
}
