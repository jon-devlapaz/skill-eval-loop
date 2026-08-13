package conformance

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"time"
)

const subprocessLogEnv = "SKILL_EVAL_CONFORMANCE_LOG"

type Scenario struct {
	Name        string            `json:"name"`
	Command     string            `json:"command"`
	Args        []string          `json:"args,omitempty"`
	StdinBase64 string            `json:"stdin_base64,omitempty"`
	Fixture     string            `json:"fixture,omitempty"`
	Environment map[string]string `json:"environment,omitempty"`
	Unset       []string          `json:"unset_environment,omitempty"`
	TimeoutMS   int               `json:"timeout_ms,omitempty"`
}

type Invocation struct {
	Executable          string            `json:"executable"`
	Argv                []string          `json:"argv"`
	CWD                 string            `json:"cwd"`
	SelectedEnvironment map[string]string `json:"selected_environment"`
	UnsetEnvironment    []string          `json:"unset_environment"`
}

type ProcessRecord struct {
	Executable  string            `json:"executable"`
	Argv        []string          `json:"argv"`
	CWD         string            `json:"cwd"`
	Environment map[string]string `json:"environment,omitempty"`
	Order       int               `json:"order"`
	Status      int               `json:"status"`
	TimedOut    bool              `json:"timed_out"`
	Signal      string            `json:"signal,omitempty"`
}

type TreeEntry struct {
	Path          string `json:"path"`
	Type          string `json:"type"`
	Mode          uint32 `json:"mode"`
	Size          int64  `json:"size,omitempty"`
	SHA256        string `json:"sha256,omitempty"`
	BytesBase64   string `json:"bytes_base64,omitempty"`
	SymlinkTarget string `json:"symlink_target,omitempty"`
}

type Snapshot struct {
	Scenario          string          `json:"scenario"`
	Invocation        Invocation      `json:"invocation"`
	StdinBase64       string          `json:"stdin_base64"`
	ExitCode          int             `json:"exit_code"`
	TerminatingSignal string          `json:"terminating_signal,omitempty"`
	TimedOut          bool            `json:"timed_out"`
	StdoutBase64      string          `json:"stdout_base64"`
	StderrBase64      string          `json:"stderr_base64"`
	Filesystem        []TreeEntry     `json:"filesystem"`
	Subprocesses      []ProcessRecord `json:"subprocesses"`
}

type Report struct {
	SchemaVersion  int      `json:"schema_version"`
	Scenario       string   `json:"scenario"`
	Equivalent     bool     `json:"equivalent"`
	RoleBindings   []string `json:"role_bindings"`
	Normalizations []string `json:"normalizations"`
	Oracle         Snapshot `json:"oracle"`
	Candidate      Snapshot `json:"candidate"`
	Difference     string   `json:"difference,omitempty"`
}

type Options struct {
	Oracle       string
	Candidate    string
	ScenarioPath string
}

func LoadScenario(path string) (Scenario, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Scenario{}, err
	}
	var scenario Scenario
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&scenario); err != nil {
		return Scenario{}, fmt.Errorf("decode scenario: %w", err)
	}
	if err := ensureJSONEOF(decoder); err != nil {
		return Scenario{}, err
	}
	if strings.TrimSpace(scenario.Name) == "" {
		return Scenario{}, errors.New("scenario.name is required")
	}
	if strings.TrimSpace(scenario.Command) == "" {
		return Scenario{}, errors.New("scenario.command is required")
	}
	if scenario.TimeoutMS < 0 {
		return Scenario{}, errors.New("scenario.timeout_ms must be non-negative")
	}
	if _, err := base64.StdEncoding.DecodeString(scenario.StdinBase64); err != nil {
		return Scenario{}, fmt.Errorf("scenario.stdin_base64: %w", err)
	}
	return scenario, nil
}

func Compare(ctx context.Context, options Options) (Report, error) {
	oracle, err := resolveImplementation(options.Oracle)
	if err != nil {
		return Report{}, fmt.Errorf("oracle: %w", err)
	}
	candidate, err := resolveImplementation(options.Candidate)
	if err != nil {
		return Report{}, fmt.Errorf("candidate: %w", err)
	}
	same, err := sameImplementation(oracle, candidate)
	if err != nil {
		return Report{}, err
	}
	if same {
		return Report{}, errors.New("oracle and candidate resolve to the same executable")
	}
	scenario, err := LoadScenario(options.ScenarioPath)
	if err != nil {
		return Report{}, err
	}
	runRoot, err := os.MkdirTemp("", "skill-eval-conformance-")
	if err != nil {
		return Report{}, err
	}
	defer os.RemoveAll(runRoot)
	oracleSnapshot, err := runAtRoot(ctx, oracle, scenario, filepath.Dir(options.ScenarioPath), runRoot)
	if err != nil {
		return Report{}, fmt.Errorf("run oracle: %w", err)
	}
	if err := os.RemoveAll(runRoot); err != nil {
		return Report{}, err
	}
	if err := os.Mkdir(runRoot, 0o700); err != nil {
		return Report{}, err
	}
	candidateSnapshot, err := runAtRoot(ctx, candidate, scenario, filepath.Dir(options.ScenarioPath), runRoot)
	if err != nil {
		return Report{}, fmt.Errorf("run candidate: %w", err)
	}
	normalizedOracle, err := normalize(oracleSnapshot, oracle)
	if err != nil {
		return Report{}, err
	}
	normalizedCandidate, err := normalize(candidateSnapshot, candidate)
	if err != nil {
		return Report{}, err
	}
	oracleJSON, err := canonicalJSON(normalizedOracle)
	if err != nil {
		return Report{}, err
	}
	candidateJSON, err := canonicalJSON(normalizedCandidate)
	if err != nil {
		return Report{}, err
	}
	report := Report{
		SchemaVersion:  1,
		Scenario:       scenario.Name,
		Equivalent:     bytes.Equal(oracleJSON, candidateJSON),
		RoleBindings:   []string{"top-level executable path -> $IMPLEMENTATION"},
		Normalizations: manifestNormalizations(oracleSnapshot, candidateSnapshot),
		Oracle:         oracleSnapshot,
		Candidate:      candidateSnapshot,
	}
	if !report.Equivalent {
		report.Difference = firstDifference(string(oracleJSON), string(candidateJSON))
	}
	return report, nil
}

func resolveImplementation(value string) (string, error) {
	if strings.TrimSpace(value) == "" {
		return "", errors.New("executable path is required")
	}
	resolved, err := exec.LookPath(value)
	if err != nil {
		return "", fmt.Errorf("implementation is absent: %s", value)
	}
	resolved, err = filepath.Abs(resolved)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return "", err
	}
	if info.IsDir() || info.Mode()&0o111 == 0 {
		return "", fmt.Errorf("implementation is not executable: %s", resolved)
	}
	return resolved, nil
}

func sameImplementation(left, right string) (bool, error) {
	leftInfo, err := os.Stat(left)
	if err != nil {
		return false, err
	}
	rightInfo, err := os.Stat(right)
	if err != nil {
		return false, err
	}
	return os.SameFile(leftInfo, rightInfo), nil
}

func run(ctx context.Context, implementation string, scenario Scenario, scenarioDir string) (Snapshot, error) {
	root, err := os.MkdirTemp("", "skill-eval-conformance-")
	if err != nil {
		return Snapshot{}, err
	}
	defer os.RemoveAll(root)
	return runAtRoot(ctx, implementation, scenario, scenarioDir, root)
}

func runAtRoot(ctx context.Context, implementation string, scenario Scenario, scenarioDir, root string) (Snapshot, error) {
	workspace := filepath.Join(root, "workspace")
	if err := os.MkdirAll(workspace, 0o755); err != nil {
		return Snapshot{}, err
	}
	if scenario.Fixture != "" {
		fixture, err := below(scenarioDir, scenario.Fixture)
		if err != nil {
			return Snapshot{}, fmt.Errorf("fixture: %w", err)
		}
		if err := copyTree(fixture, workspace); err != nil {
			return Snapshot{}, fmt.Errorf("copy fixture: %w", err)
		}
	}
	scenario = expandWorkspace(scenario, workspace)
	stdin, _ := base64.StdEncoding.DecodeString(scenario.StdinBase64)
	argv := append([]string{implementation, scenario.Command}, scenario.Args...)
	commandCtx := ctx
	cancel := func() {}
	if scenario.TimeoutMS > 0 {
		commandCtx, cancel = context.WithTimeout(ctx, time.Duration(scenario.TimeoutMS)*time.Millisecond)
	}
	defer cancel()
	cmd := exec.Command(implementation, append([]string{scenario.Command}, scenario.Args...)...)
	cmd.Dir = workspace
	cmd.Stdin = bytes.NewReader(stdin)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	logPath := filepath.Join(root, "subprocesses.jsonl")
	environment := selectedEnvironment(scenario.Environment, scenario.Unset)
	environment[subprocessLogEnv] = logPath
	cmd.Env = mergeEnvironment(environment, scenario.Unset)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		return Snapshot{}, err
	}
	waitErr, timedOut := waitProcessGroup(commandCtx, cmd)
	exitCode, signal := processOutcome(cmd.ProcessState, waitErr)
	tree, err := snapshotTree(workspace)
	if err != nil {
		return Snapshot{}, err
	}
	subprocesses, err := readProcessRecords(logPath)
	if err != nil {
		return Snapshot{}, err
	}
	sort.Strings(scenario.Unset)
	return Snapshot{
		Scenario: scenario.Name,
		Invocation: Invocation{
			Executable:          implementation,
			Argv:                argv,
			CWD:                 workspace,
			SelectedEnvironment: environment,
			UnsetEnvironment:    append([]string(nil), scenario.Unset...),
		},
		StdinBase64:       base64.StdEncoding.EncodeToString(stdin),
		ExitCode:          exitCode,
		TerminatingSignal: signal,
		TimedOut:          timedOut,
		StdoutBase64:      base64.StdEncoding.EncodeToString(stdout.Bytes()),
		StderrBase64:      base64.StdEncoding.EncodeToString(stderr.Bytes()),
		Filesystem:        tree,
		Subprocesses:      subprocesses,
	}, nil
}

func expandWorkspace(scenario Scenario, workspace string) Scenario {
	replace := func(value string) string {
		return strings.ReplaceAll(value, "$WORKSPACE", workspace)
	}
	for index := range scenario.Args {
		scenario.Args[index] = replace(scenario.Args[index])
	}
	for key, value := range scenario.Environment {
		scenario.Environment[key] = replace(value)
	}
	return scenario
}

func waitProcessGroup(ctx context.Context, cmd *exec.Cmd) (error, bool) {
	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
	}()
	select {
	case err := <-done:
		return err, false
	case <-ctx.Done():
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
		timer := time.NewTimer(time.Second)
		defer timer.Stop()
		select {
		case err := <-done:
			return err, errors.Is(ctx.Err(), context.DeadlineExceeded)
		case <-timer.C:
			_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
			return <-done, errors.Is(ctx.Err(), context.DeadlineExceeded)
		}
	}
}

func selectedEnvironment(set map[string]string, unset []string) map[string]string {
	selected := make(map[string]string, len(set)+1)
	for key, value := range set {
		selected[key] = value
	}
	for _, key := range unset {
		delete(selected, key)
	}
	return selected
}

func mergeEnvironment(set map[string]string, unset []string) []string {
	values := map[string]string{}
	for _, entry := range os.Environ() {
		key, value, ok := strings.Cut(entry, "=")
		if ok {
			values[key] = value
		}
	}
	for _, key := range unset {
		delete(values, key)
	}
	for key, value := range set {
		values[key] = value
	}
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	result := make([]string, 0, len(keys))
	for _, key := range keys {
		result = append(result, key+"="+values[key])
	}
	return result
}

func processOutcome(state *os.ProcessState, waitErr error) (int, string) {
	if state == nil {
		return -1, ""
	}
	code := state.ExitCode()
	if status, ok := state.Sys().(syscall.WaitStatus); ok && status.Signaled() {
		return code, status.Signal().String()
	}
	if waitErr != nil {
		return code, ""
	}
	return code, ""
}

func snapshotTree(root string) ([]TreeEntry, error) {
	entries := []TreeEntry{}
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if path == root {
			return nil
		}
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		record := TreeEntry{Path: filepath.ToSlash(relative), Mode: uint32(info.Mode().Perm())}
		switch {
		case info.Mode()&os.ModeSymlink != 0:
			record.Type = "symlink"
			record.SymlinkTarget, err = os.Readlink(path)
		case info.IsDir():
			record.Type = "directory"
		case info.Mode().IsRegular():
			record.Type = "file"
			record.Size = info.Size()
			data, readErr := os.ReadFile(path)
			if readErr != nil {
				return readErr
			}
			digest := sha256.Sum256(data)
			record.SHA256 = hex.EncodeToString(digest[:])
			record.BytesBase64 = base64.StdEncoding.EncodeToString(data)
		default:
			record.Type = "other"
		}
		if err != nil {
			return err
		}
		entries = append(entries, record)
		return nil
	})
	return entries, err
}

func readProcessRecords(path string) ([]ProcessRecord, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return []ProcessRecord{}, nil
	}
	if err != nil {
		return nil, err
	}
	lines := bytes.Split(data, []byte("\n"))
	records := []ProcessRecord{}
	for _, line := range lines {
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		var record ProcessRecord
		decoder := json.NewDecoder(bytes.NewReader(line))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&record); err != nil {
			return nil, fmt.Errorf("decode subprocess record: %w", err)
		}
		records = append(records, record)
	}
	return records, nil
}

func normalize(raw Snapshot, implementation string) (Snapshot, error) {
	encoded, err := json.Marshal(raw)
	if err != nil {
		return Snapshot{}, err
	}
	var snapshot Snapshot
	if err := json.Unmarshal(encoded, &snapshot); err != nil {
		return Snapshot{}, err
	}
	replace := func(value string) string {
		value = strings.ReplaceAll(value, implementation, "$IMPLEMENTATION")
		return value
	}
	snapshot.Invocation.Executable = replace(snapshot.Invocation.Executable)
	for index := range snapshot.Invocation.Argv {
		snapshot.Invocation.Argv[index] = replace(snapshot.Invocation.Argv[index])
	}
	snapshot.Invocation.CWD = replace(snapshot.Invocation.CWD)
	for key, value := range snapshot.Invocation.SelectedEnvironment {
		snapshot.Invocation.SelectedEnvironment[key] = replace(value)
	}
	for index := range snapshot.Subprocesses {
		snapshot.Subprocesses[index].Executable = replace(snapshot.Subprocesses[index].Executable)
		snapshot.Subprocesses[index].CWD = replace(snapshot.Subprocesses[index].CWD)
		for argIndex := range snapshot.Subprocesses[index].Argv {
			snapshot.Subprocesses[index].Argv[argIndex] = replace(snapshot.Subprocesses[index].Argv[argIndex])
		}
		for key, value := range snapshot.Subprocesses[index].Environment {
			snapshot.Subprocesses[index].Environment[key] = replace(value)
		}
	}
	for index := range snapshot.Filesystem {
		snapshot.Filesystem[index].SymlinkTarget = replace(snapshot.Filesystem[index].SymlinkTarget)
		if strings.HasSuffix(filepath.ToSlash(snapshot.Filesystem[index].Path), "/run_manifest.json") {
			if err := normalizeRunManifest(&snapshot.Filesystem[index]); err != nil {
				return Snapshot{}, err
			}
		}
	}
	return snapshot, nil
}

func normalizeRunManifest(entry *TreeEntry) error {
	data, err := base64.StdEncoding.DecodeString(entry.BytesBase64)
	if err != nil {
		return err
	}
	var manifest map[string]any
	if err := json.Unmarshal(data, &manifest); err != nil {
		return err
	}
	trials, _ := manifest["trials"].([]any)
	for _, rawPair := range trials {
		pair, _ := rawPair.(map[string]any)
		conditions, _ := pair["conditions"].(map[string]any)
		for _, rawCondition := range conditions {
			condition, _ := rawCondition.(map[string]any)
			condition["started_at"] = "$STARTED_AT"
			condition["duration_seconds"] = "$DURATION_SECONDS"
		}
	}
	normalized, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	normalized = append(normalized, '\n')
	sum := sha256.Sum256(normalized)
	entry.BytesBase64 = base64.StdEncoding.EncodeToString(normalized)
	entry.Size = int64(len(normalized))
	entry.SHA256 = hex.EncodeToString(sum[:])
	return nil
}

func manifestNormalizations(oracle, candidate Snapshot) []string {
	if hasTreeSuffix(oracle.Filesystem, "/run_manifest.json") && hasTreeSuffix(candidate.Filesystem, "/run_manifest.json") {
		return []string{
			"run_manifest condition started_at -> $STARTED_AT",
			"run_manifest condition duration_seconds -> $DURATION_SECONDS",
			"run_manifest snapshot size/hash -> normalized manifest bytes",
		}
	}
	return []string{}
}

func hasTreeSuffix(entries []TreeEntry, suffix string) bool {
	for _, entry := range entries {
		if strings.HasSuffix(filepath.ToSlash(entry.Path), suffix) {
			return true
		}
	}
	return false
}

func below(root, relative string) (string, error) {
	if filepath.IsAbs(relative) {
		return "", errors.New("path must be relative")
	}
	clean := filepath.Clean(relative)
	if clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", errors.New("path escapes scenario directory")
	}
	resolved := filepath.Join(root, clean)
	return resolved, nil
}

func copyTree(source, destination string) error {
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		target := filepath.Join(destination, relative)
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		switch {
		case info.Mode()&os.ModeSymlink != 0:
			link, err := os.Readlink(path)
			if err != nil {
				return err
			}
			return os.Symlink(link, target)
		case info.IsDir():
			return os.MkdirAll(target, info.Mode().Perm())
		case info.Mode().IsRegular():
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			input, err := os.Open(path)
			if err != nil {
				return err
			}
			defer input.Close()
			output, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, info.Mode().Perm())
			if err != nil {
				return err
			}
			_, copyErr := io.Copy(output, input)
			closeErr := output.Close()
			return errors.Join(copyErr, closeErr)
		default:
			return fmt.Errorf("unsupported fixture entry: %s", path)
		}
	})
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("scenario contains multiple JSON values")
		}
		return err
	}
	return nil
}

func canonicalJSON(value any) ([]byte, error) {
	return json.Marshal(value)
}

func firstDifference(left, right string) string {
	limit := len(left)
	if len(right) < limit {
		limit = len(right)
	}
	index := 0
	for index < limit && left[index] == right[index] {
		index++
	}
	start := index - 80
	if start < 0 {
		start = 0
	}
	leftEnd := index + 160
	if leftEnd > len(left) {
		leftEnd = len(left)
	}
	rightEnd := index + 160
	if rightEnd > len(right) {
		rightEnd = len(right)
	}
	return fmt.Sprintf("first difference at byte %d; oracle=%q candidate=%q", index, left[start:leftEnd], right[start:rightEnd])
}

func Platform() string {
	return runtime.GOOS + "/" + runtime.GOARCH
}
