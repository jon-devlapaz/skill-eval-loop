//go:build darwin || linux

package herdr

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestObserverCreatesRetainedLayoutAndFinishesOnce(t *testing.T) {
	root := t.TempDir()
	logPath := filepath.Join(root, "calls.log")
	executable := filepath.Join(root, "herdr")
	script := `#!/bin/sh
printf '%s\n' "$*" >> "$HERDR_TEST_LOG"
case "$*" in
  'workspace create '*) printf '{"result":{"workspace":{"workspace_id":"w1"},"root_pane":{"pane_id":"w1:p1"}}}\n' ;;
  'pane split w1:p1 --direction down '*) printf '{"result":{"pane":{"pane_id":"w1:p2"}}}\n' ;;
  'pane split w1:p1 --direction right '*) printf '{"result":{"pane":{"pane_id":"w1:p3"}}}\n' ;;
  'pane split w1:p2 --direction right '*) printf '{"result":{"pane":{"pane_id":"w1:p4"}}}\n' ;;
  *) printf '{"result":{}}\n' ;;
esac
`
	if err := os.WriteFile(executable, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", root+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("HERDR_ENV", "1")
	t.Setenv("HERDR_TEST_LOG", logPath)
	output := filepath.Join(root, "run-1")
	observer, err := Start("fixture-skill", output, root)
	if err != nil {
		t.Fatal(err)
	}
	if observer.WorkspaceID != "w1" || observer.panes["control"] != "w1:p3" || observer.panes["with_skill"] != "w1:p2" || observer.panes["judge_results"] != "w1:p4" {
		t.Fatalf("observer=%#v", observer)
	}
	trace := filepath.Join(output, "trace.jsonl")
	stderr := filepath.Join(output, "stderr.txt")
	if err := observer.Begin("control", "fixture", trace, stderr); err != nil {
		t.Fatal(err)
	}
	if err := observer.End("fixture", 0); err != nil {
		t.Fatal(err)
	}
	if err := observer.Finish("completed", "Verdict: improved", output); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatal(err)
	}
	calls := string(data)
	if strings.Count(calls, "notification show") != 1 || strings.Contains(calls, "workspace close") || !strings.Contains(calls, "workspace rename w1 [completed] eval:fixture-skill:run-1") {
		t.Fatalf("calls:\n%s", calls)
	}
	if _, err := os.Stat(filepath.Join(output, "herdr", "summary.txt")); err != nil {
		t.Fatal(err)
	}
}

func TestObserverRequiresManagedEnvironment(t *testing.T) {
	t.Setenv("HERDR_ENV", "")
	if err := RequireEnvironment(); err == nil || !strings.Contains(err.Error(), "HERDR_ENV=1") {
		t.Fatalf("error=%v", err)
	}
}
