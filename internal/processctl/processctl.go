//go:build darwin || linux

package processctl

import (
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"time"
)

const defaultOutputLimit = 16 << 20

type Options struct {
	Argv             []string
	CWD              string
	Env              []string
	Timeout          time.Duration
	TerminationGrace time.Duration
	OutputLimit      int
}

type Result struct {
	Argv           []string
	ExitCode       int
	Stdout         string
	Stderr         string
	TimedOut       bool
	Cancelled      bool
	OutputOverflow bool
}

func Run(ctx context.Context, options Options) (Result, error) {
	if len(options.Argv) == 0 || strings.TrimSpace(options.Argv[0]) == "" {
		return Result{}, errors.New("command argv is empty")
	}
	if options.Timeout <= 0 {
		return Result{}, errors.New("timeout must be positive")
	}
	if options.TerminationGrace <= 0 {
		options.TerminationGrace = time.Second
	}
	if options.OutputLimit < 0 {
		return Result{}, errors.New("output limit must be non-negative")
	}
	if options.OutputLimit == 0 {
		options.OutputLimit = defaultOutputLimit
	}
	result := Result{Argv: append([]string(nil), options.Argv...)}
	if ctx.Err() != nil {
		result.ExitCode = 130
		result.Cancelled = true
		result.Stderr = "\nInterrupted by user.\n"
		return result, nil
	}

	overflow := make(chan struct{}, 1)
	stdout := newBoundedBuffer(options.OutputLimit, overflow)
	stderr := newBoundedBuffer(options.OutputLimit, overflow)
	command := exec.Command(options.Argv[0], options.Argv[1:]...)
	command.Dir = options.CWD
	if options.Env != nil {
		command.Env = options.Env
	}
	command.Stdout = stdout
	command.Stderr = stderr
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := command.Start(); err != nil {
		return Result{}, err
	}

	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	timer := time.NewTimer(options.Timeout)
	defer timer.Stop()

	var waitErr error
	select {
	case waitErr = <-done:
	case <-ctx.Done():
		result.Cancelled = true
		waitErr = terminateGroup(command.Process.Pid, options.TerminationGrace, done)
	case <-timer.C:
		result.TimedOut = true
		waitErr = terminateGroup(command.Process.Pid, options.TerminationGrace, done)
	case <-overflow:
		result.OutputOverflow = true
		waitErr = terminateGroup(command.Process.Pid, options.TerminationGrace, done)
	}

	result.Stdout = stdout.String()
	result.Stderr = stderr.String()
	result.OutputOverflow = result.OutputOverflow || stdout.Overflowed() || stderr.Overflowed()
	switch {
	case result.Cancelled:
		result.ExitCode = 130
		result.Stderr += "\nInterrupted by user.\n"
	case result.TimedOut:
		result.ExitCode = 124
		result.Stderr += fmt.Sprintf("\nTimed out after %g seconds.\n", options.Timeout.Seconds())
	case result.OutputOverflow:
		result.ExitCode = 1
		result.Stderr += fmt.Sprintf("\nCaptured output exceeded %d bytes.\n", options.OutputLimit)
	default:
		result.ExitCode = exitCode(waitErr)
	}
	return result, nil
}

func terminateGroup(pid int, grace time.Duration, done <-chan error) error {
	_ = syscall.Kill(-pid, syscall.SIGTERM)
	timer := time.NewTimer(grace)
	defer timer.Stop()
	select {
	case err := <-done:
		return err
	case <-timer.C:
		_ = syscall.Kill(-pid, syscall.SIGKILL)
		return <-done
	}
}

func exitCode(err error) int {
	if err == nil {
		return 0
	}
	var exitError *exec.ExitError
	if errors.As(err, &exitError) {
		return exitError.ExitCode()
	}
	return 1
}

type boundedBuffer struct {
	mu       sync.Mutex
	bytes    []byte
	limit    int
	overflow bool
	notify   chan<- struct{}
}

func newBoundedBuffer(limit int, notify chan<- struct{}) *boundedBuffer {
	return &boundedBuffer{bytes: make([]byte, 0, limit), limit: limit, notify: notify}
}

func (buffer *boundedBuffer) Write(data []byte) (int, error) {
	buffer.mu.Lock()
	remaining := buffer.limit - len(buffer.bytes)
	if remaining > 0 {
		keep := len(data)
		if keep > remaining {
			keep = remaining
		}
		buffer.bytes = append(buffer.bytes, data[:keep]...)
	}
	overflowedNow := len(data) > remaining && !buffer.overflow
	if overflowedNow {
		buffer.overflow = true
	}
	buffer.mu.Unlock()
	if overflowedNow {
		select {
		case buffer.notify <- struct{}{}:
		default:
		}
	}
	return len(data), nil
}

func (buffer *boundedBuffer) String() string {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return string(buffer.bytes)
}

func (buffer *boundedBuffer) Overflowed() bool {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return buffer.overflow
}
