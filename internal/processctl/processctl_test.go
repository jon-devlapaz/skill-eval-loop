//go:build darwin || linux

package processctl

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestTimeoutTerminatesDescendants(t *testing.T) {
	sideEffect := filepath.Join(t.TempDir(), "survived")
	result, err := Run(context.Background(), Options{
		Argv:    []string{"/bin/sh", "-c", `(sleep 0.4; printf survived > "$1") & sleep 30`, "sh", sideEffect},
		Timeout: 50 * time.Millisecond, TerminationGrace: 50 * time.Millisecond,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !result.TimedOut || result.ExitCode != 124 {
		t.Fatalf("result=%#v", result)
	}
	time.Sleep(500 * time.Millisecond)
	if _, err := os.Stat(sideEffect); !os.IsNotExist(err) {
		t.Fatalf("descendant side effect survived: %v", err)
	}
}

func TestTimeoutKillsDescendantHoldingOutputPipe(t *testing.T) {
	started := time.Now()
	result, err := Run(context.Background(), Options{
		Argv:    []string{"/bin/sh", "-c", `(sleep 30) & exit 0`},
		Timeout: 50 * time.Millisecond, TerminationGrace: 50 * time.Millisecond,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !result.TimedOut || time.Since(started) > time.Second {
		t.Fatalf("result=%#v elapsed=%s", result, time.Since(started))
	}
}

func TestCancellationPreservesPartialOutput(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(50*time.Millisecond, cancel)
	result, err := Run(ctx, Options{
		Argv:    []string{"/bin/sh", "-c", `printf partial; sleep 30`},
		Timeout: time.Second, TerminationGrace: 50 * time.Millisecond,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !result.Cancelled || result.ExitCode != 130 || result.Stdout != "partial" {
		t.Fatalf("result=%#v", result)
	}
}

func TestOutputOverflowTerminatesProcess(t *testing.T) {
	result, err := Run(context.Background(), Options{
		Argv:    []string{"/bin/sh", "-c", `while :; do printf 1234567890; done`},
		Timeout: time.Second, TerminationGrace: 50 * time.Millisecond, OutputLimit: 128,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !result.OutputOverflow || result.ExitCode != 1 || len(result.Stdout) != 128 {
		t.Fatalf("result=%#v", result)
	}
}
