package runexec

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/jon-devlapaz/skill-eval-loop/internal/aggregate"
	"github.com/jon-devlapaz/skill-eval-loop/internal/evalspec"
	"github.com/jon-devlapaz/skill-eval-loop/internal/herdr"
	"github.com/jon-devlapaz/skill-eval-loop/internal/processctl"
	"github.com/jon-devlapaz/skill-eval-loop/internal/runplan"
	"github.com/jon-devlapaz/skill-eval-loop/internal/skillpayload"
)

const systemPrompt = "Work only inside the current workspace. Complete the user's task with the available capabilities."

type Input struct {
	Plan         runplan.Plan
	EvalsPath    string
	Timeout      time.Duration
	JudgeModel   string
	JudgeTimeout time.Duration
	Observer     *herdr.Observer
}

func Run(ctx context.Context, input Input) (map[string]any, error) {
	if input.Plan.Harness != "pi" && input.Plan.Harness != "claude-code" && input.Plan.Harness != "hermes" && input.Plan.Harness != "codex" {
		return nil, errors.New("unsupported run harness")
	}
	if _, err := os.Stat(input.Plan.OutputDir); err == nil {
		return nil, fmt.Errorf("%s already exists; choose a new output", input.Plan.OutputDir)
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	suite, err := evalspec.Load(input.Plan.SkillPath, input.EvalsPath)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(input.Plan.OutputDir, 0o755); err != nil {
		return nil, err
	}
	completed := 0
	if err := writeState(input.Plan.OutputDir, state{Status: "starting", Valid: false, CompletedConditions: 0}); err != nil {
		return nil, err
	}
	observerName := "headless"
	if input.Plan.Observer.Kind == "herdr" {
		observerName = "herdr"
		observerCWD, cwdErr := os.Getwd()
		if cwdErr != nil {
			return nil, cwdErr
		}
		input.Observer, err = herdr.Start(suite.SkillName, input.Plan.OutputDir, observerCWD)
		if err != nil {
			_ = writeState(input.Plan.OutputDir, state{Status: "failed", Valid: false, Error: err.Error(), Observer: observerName, CompletedConditions: 0})
			return nil, err
		}
	}
	observerState := state{Status: "running", Valid: false, Observer: observerName, CompletedConditions: 0}
	if input.Observer != nil {
		observerState.WorkspaceID = input.Observer.WorkspaceID
		observerState.WorkspaceLabel = input.Observer.WorkspaceLabel
	}
	if err := writeState(input.Plan.OutputDir, observerState); err != nil {
		return nil, err
	}
	fail := func(runErr error) (map[string]any, error) {
		failed := state{Status: "failed", Valid: false, Error: runErr.Error(), Observer: observerName, CompletedConditions: completed}
		if input.Observer != nil {
			failed.WorkspaceID, failed.WorkspaceLabel = input.Observer.WorkspaceID, input.Observer.WorkspaceLabel
			_ = input.Observer.Finish("failed", runErr.Error(), input.Plan.OutputDir)
		}
		_ = writeState(input.Plan.OutputDir, failed)
		return nil, runErr
	}

	references, err := validateReferences(ctx, input, suite)
	if err != nil {
		return fail(err)
	}
	provenancePath, provenanceHash, err := retainProvenance(input.Plan.OutputDir, suite)
	if err != nil {
		return fail(err)
	}
	snapshot, err := buildSuiteSnapshot(suite)
	if err != nil {
		return fail(err)
	}
	suitePath := filepath.Join(input.Plan.OutputDir, "suite_snapshot.json")
	if err := writeJSON(suitePath, snapshot); err != nil {
		return fail(err)
	}
	suiteHash, err := fileHash(suitePath)
	if err != nil {
		return fail(err)
	}
	skillHash, err := skillpayload.Hash(input.Plan.SkillPath)
	if err != nil {
		return fail(err)
	}

	pairs := []pairRecord{}
	schedule := []scheduleRecord{}
	for _, current := range suite.Cases {
		for trial := 1; trial <= input.Plan.TrialsPerCase; trial++ {
			order := []string{"without_skill", "with_skill"}
			if trial%2 == 0 {
				order = []string{"with_skill", "without_skill"}
			}
			pair := pairRecord{CaseID: current.ID, Trial: trial, Conditions: orderedConditions{Order: order, Values: map[string]conditionRecord{}}, ExecutionOrder: order}
			schedule = append(schedule, scheduleRecord{CaseID: current.ID, Trial: trial, Conditions: order})
			for _, condition := range order {
				record, runErr := runCondition(ctx, input, suite, current, trial, condition)
				if runErr != nil {
					if ctx.Err() != nil {
						cancelled := state{Status: "cancelled", Valid: false, Observer: observerName, CompletedConditions: completed}
						if input.Observer != nil {
							cancelled.WorkspaceID, cancelled.WorkspaceLabel = input.Observer.WorkspaceID, input.Observer.WorkspaceLabel
							input.Observer.CancelActive()
							_ = input.Observer.Finish("cancelled", "Evaluation cancelled; partial evidence retained", input.Plan.OutputDir)
						}
						_ = writeState(input.Plan.OutputDir, cancelled)
						return nil, context.Canceled
					}
					return fail(runErr)
				}
				pair.Conditions.Values[condition] = record
				completed++
				running := state{Status: "running", Valid: false, Observer: observerName, CompletedConditions: completed}
				if input.Observer != nil {
					running.WorkspaceID, running.WorkspaceLabel = input.Observer.WorkspaceID, input.Observer.WorkspaceLabel
				}
				if err := writeState(input.Plan.OutputDir, running); err != nil {
					return fail(err)
				}
			}
			pairs = append(pairs, pair)
		}
	}
	decision := "Does forced loading of the target skill improve task success?"
	conditionVariable := input.Plan.Harness + " explicit skill activation versus isolated control"
	if suite.ActivationMode == "autonomous" {
		decision = "Does autonomous access to the target skill improve task success?"
		conditionVariable = input.Plan.Harness + " native skill availability versus isolated control"
	}
	manifest := manifest{
		SchemaVersion: 1, TargetSkillName: suite.SkillName,
		Decision:          decision,
		ConditionVariable: conditionVariable,
		SkillSHA256:       skillHash, SuitePath: "suite_snapshot.json", SuiteSHA256: suiteHash,
		ProvenancePath: provenancePath, ProvenanceSHA256: provenanceHash, RequestedModel: input.Plan.Model,
		JudgeModel: optionalString(input.JudgeModel), Harness: input.Plan.Harness, HarnessVersion: input.Plan.HarnessVersion,
		Observer: observerName, ToolProfile: suite.ToolProfile, ActivationMode: suite.ActivationMode,
		ExecutionOrder: "counterbalanced_by_trial", ExecutionSchedule: schedule,
		CaseCount: len(suite.Cases), TrialsPerCase: input.Plan.TrialsPerCase, PairCount: len(pairs),
		ReferenceValidation: references, Trials: pairs,
	}
	if err := writeJSON(filepath.Join(input.Plan.OutputDir, "run_manifest.json"), manifest); err != nil {
		return fail(err)
	}
	report, err := aggregate.Run(input.Plan.OutputDir)
	if err != nil {
		return fail(err)
	}
	benchmarkBytes, err := aggregate.Bytes(report)
	if err != nil {
		return fail(err)
	}
	if err := os.WriteFile(filepath.Join(input.Plan.OutputDir, "benchmark.json"), benchmarkBytes, 0o666); err != nil {
		return fail(err)
	}
	verdict, _ := report["verdict"].(string)
	valid, _ := report["valid"].(bool)
	status := "invalid"
	if valid {
		status = "completed"
	}
	finalState := state{Status: status, Valid: valid, Verdict: verdict, Observer: observerName, CompletedConditions: completed}
	if input.Observer != nil {
		finalState.WorkspaceID, finalState.WorkspaceLabel = input.Observer.WorkspaceID, input.Observer.WorkspaceLabel
		if err := input.Observer.Finish(status, "Verdict: "+verdict, input.Plan.OutputDir); err != nil {
			return fail(err)
		}
	}
	if err := writeState(input.Plan.OutputDir, finalState); err != nil {
		return nil, err
	}
	return report, nil
}

func runCondition(ctx context.Context, input Input, suite *evalspec.Suite, current evalspec.Case, trial int, condition string) (conditionRecord, error) {
	conditionDir := filepath.Join(input.Plan.OutputDir, "eval-"+current.ID, fmt.Sprintf("trial-%03d", trial), condition)
	workspace := filepath.Join(conditionDir, "workspace")
	if err := os.MkdirAll(workspace, 0o755); err != nil {
		return conditionRecord{}, err
	}
	if err := prepareCaseWorkspace(suite, current, workspace, false); err != nil {
		return conditionRecord{}, err
	}
	installed := ""
	available := []string{}
	activation := "none"
	if condition == "with_skill" {
		if input.Plan.Harness == "claude-code" {
			installed = filepath.Join(workspace, ".claude", "skills", suite.SkillName)
		} else if input.Plan.Harness == "codex" {
			installed = filepath.Join(workspace, ".agents", "skills", suite.SkillName)
		} else {
			installed = filepath.Join(conditionDir, "installed-skill", suite.SkillName)
		}
		if err := copyPayload(input.Plan.SkillPath, installed); err != nil {
			return conditionRecord{}, err
		}
		available = append(available, suite.SkillName)
		activation = "forced_command"
		if suite.ActivationMode == "autonomous" {
			activation = "available_for_autonomous_selection"
		}
	}
	outputs := filepath.Join(conditionDir, "outputs")
	if err := os.MkdirAll(outputs, 0o755); err != nil {
		return conditionRecord{}, err
	}
	tools := map[string][]string{"no_tools": {}, "read_only": {"read", "grep", "find", "ls"}, "read_write": {"read", "write"}, "coding": {"read", "write", "edit", "bash", "grep", "find", "ls"}}[suite.ToolProfile]
	prompt := current.Prompt
	if condition == "with_skill" {
		if suite.ActivationMode == "forced" {
			if input.Plan.Harness == "claude-code" {
				prompt = "/" + suite.SkillName + " " + prompt
			} else if input.Plan.Harness == "codex" {
				prompt = "Use the $" + suite.SkillName + " skill. " + prompt
			} else if input.Plan.Harness == "hermes" {
				prompt = "Use the " + suite.SkillName + " skill. " + prompt
			} else {
				prompt = "/skill:" + suite.SkillName + " " + prompt
			}
		}
	}
	argv, environment, err := targetInvocation(input.Plan, suite, tools, conditionDir, installed, prompt)
	if err != nil {
		return conditionRecord{}, err
	}
	startedAt := time.Now().UTC()
	started := time.Now()
	tracePath := filepath.Join(outputs, "trace.jsonl")
	stderrPath := filepath.Join(outputs, "stderr.txt")
	title := fmt.Sprintf("%s · %s · trial %d", condition, current.ID, trial)
	if input.Observer != nil {
		role := "control"
		if condition == "with_skill" {
			role = "with_skill"
		}
		if err := input.Observer.Begin(role, title, tracePath, stderrPath); err != nil {
			return conditionRecord{}, err
		}
	}
	result, err := processctl.Run(ctx, processctl.Options{Argv: argv, CWD: workspace, Env: environment, Timeout: input.Timeout})
	if err != nil {
		return conditionRecord{}, err
	}
	if err := os.WriteFile(tracePath, []byte(result.Stdout), 0o666); err != nil {
		return conditionRecord{}, err
	}
	if err := os.WriteFile(stderrPath, []byte(result.Stderr), 0o666); err != nil {
		return conditionRecord{}, err
	}
	if input.Observer != nil {
		if err := input.Observer.End(title, result.ExitCode); err != nil {
			return conditionRecord{}, err
		}
	}
	metadata, err := parseTrace(tracePath, suite.SkillName)
	if err != nil {
		return conditionRecord{}, err
	}
	if input.Plan.Harness == "hermes" {
		if err := applyHermesUsage(filepath.Join(outputs, "usage.json"), &metadata); err != nil {
			return conditionRecord{}, err
		}
	}
	if input.Plan.Harness == "codex" {
		codexHome := filepath.Join(conditionDir, "codex-home")
		if err := applyCodexAttestation(codexHome, suite.SkillName, installed, &metadata); err != nil {
			return conditionRecord{}, err
		}
		if metadata.AttestationTracePath == "" {
			return conditionRecord{}, fmt.Errorf("codex persisted attestation trace missing for requested model %s; retained trace at %s", input.Plan.Model, tracePath)
		}
	}
	if !metadata.ModelAttested {
		return conditionRecord{}, fmt.Errorf("target model %s was not attested; see %s", input.Plan.Model, tracePath)
	}
	if !strings.EqualFold(metadata.ActualModel, input.Plan.Model) {
		return conditionRecord{}, fmt.Errorf("requested target model %s but attested %s; see %s", input.Plan.Model, metadata.ActualModel, tracePath)
	}
	if input.Plan.Harness == "codex" && condition == "with_skill" && suite.ActivationMode == "forced" && !metadata.SkillExplicitlyAccessed {
		return conditionRecord{}, fmt.Errorf("forced target skill %s was not explicitly accessed; see %s", suite.SkillName, tracePath)
	}
	if result.TimedOut || result.ExitCode != 0 {
		status := fmt.Sprintf("exited %d", result.ExitCode)
		if result.TimedOut {
			status = "timed out"
		}
		return conditionRecord{}, fmt.Errorf("target invocation failed (%s); retained trace at %s", status, tracePath)
	}
	responsePath := filepath.Join(outputs, "response.md")
	responseBytes := []byte(metadata.FinalResponse)
	if metadata.FinalResponse != "" {
		responseBytes = append(responseBytes, '\n')
	}
	if err := os.WriteFile(responsePath, responseBytes, 0o666); err != nil {
		return conditionRecord{}, err
	}
	external, judgeRecords, err := modelGrades(ctx, input, current, metadata.FinalResponse, filepath.Join(outputs, "judges"), fmt.Sprintf("%s · %s · trial %d", condition, current.ID, trial))
	if err != nil {
		return conditionRecord{}, err
	}
	grading, err := evalspec.GradeCase(workspace, metadata.FinalResponse, current.Graders, external)
	if err != nil {
		return conditionRecord{}, err
	}
	gradingPath := filepath.Join(conditionDir, "grading.json")
	if err := writeJSON(gradingPath, grading); err != nil {
		return conditionRecord{}, err
	}
	traceHash, _ := fileHash(tracePath)
	responseHash, _ := fileHash(responsePath)
	gradingHash, _ := fileHash(gradingPath)
	relativeInstalled := ""
	if installed != "" {
		relativeInstalled, _ = filepath.Rel(input.Plan.OutputDir, installed)
	}
	traceRelative, _ := filepath.Rel(input.Plan.OutputDir, tracePath)
	responseRelative, _ := filepath.Rel(input.Plan.OutputDir, responsePath)
	gradingRelative, _ := filepath.Rel(input.Plan.OutputDir, gradingPath)
	toolEnforcement := "exact_cli_allowlist"
	if input.Plan.Harness == "hermes" {
		if suite.ToolProfile == "no_tools" {
			toolEnforcement = "disabled_toolset"
		} else {
			toolEnforcement = "toolset_posture_only"
		}
	}
	if input.Plan.Harness == "codex" {
		toolEnforcement = "sandbox_posture_only"
	}
	attestationRelative := ""
	attestationHash := ""
	if metadata.AttestationTracePath != "" {
		attestationRelative, _ = filepath.Rel(input.Plan.OutputDir, metadata.AttestationTracePath)
		attestationHash, _ = fileHash(metadata.AttestationTracePath)
	}
	return conditionRecord{
		CaseID: current.ID, Trial: trial, Condition: condition,
		StartedAt:       startedAt.Format("2006-01-02T15:04:05.000000+00:00"),
		DurationSeconds: evalspec.PythonFloat(math.Round(time.Since(started).Seconds()*1e6) / 1e6),
		ExitCode:        result.ExitCode, TimedOut: result.TimedOut,
		RequestedModel: input.Plan.Model, ActualModel: metadata.ActualModel, ModelAttested: metadata.ModelAttested,
		SessionID: metadata.SessionID, InputTokens: metadata.InputTokens, OutputTokens: metadata.OutputTokens,
		TotalTokens: metadata.TotalTokens, Cost: nil, AvailableSkills: available,
		SkillAvailable: condition == "with_skill", SkillActivation: activation,
		RequestedTools: tools, ToolEnforcement: toolEnforcement, InstalledSkillPath: filepath.ToSlash(relativeInstalled),
		SkillInjectionAttested: metadata.SkillInjectionAttested, SkillExplicitlyAccessed: metadata.SkillExplicitlyAccessed,
		ExpectedSkillLoading: map[bool]string{true: current.ExpectedSkillLoading, false: "forbidden"}[condition == "with_skill"],
		JudgeRecords:         judgeRecords, TracePath: filepath.ToSlash(traceRelative), TraceSHA256: traceHash,
		AttestationTracePath: filepath.ToSlash(attestationRelative), AttestationTraceSHA256: attestationHash,
		ResponsePath: filepath.ToSlash(responseRelative), ResponseSHA256: responseHash,
		GradingPath: filepath.ToSlash(gradingRelative), GradingSHA256: gradingHash,
	}, nil
}

type traceMetadata struct {
	SessionID, ActualModel, FinalResponse  string
	AttestationTracePath                   string
	ModelAttested, SkillInjectionAttested  bool
	SkillExplicitlyAccessed                bool
	InputTokens, OutputTokens, TotalTokens any
}

func targetInvocation(plan runplan.Plan, suite *evalspec.Suite, tools []string, conditionDir, installed, prompt string) ([]string, []string, error) {
	if plan.Harness == "claude-code" {
		claudeTools := map[string]string{"no_tools": "", "read_only": "Read,Grep,Glob", "read_write": "Read,Write", "coding": "Read,Write,Edit,Bash,Grep,Glob"}[suite.ToolProfile]
		return []string{plan.HarnessPath, "-p", "--output-format", "stream-json", "--verbose", "--model", plan.Model, "--no-session-persistence", "--setting-sources", "project", "--strict-mcp-config", "--tools", claudeTools, "--permission-mode", "bypassPermissions", "--append-system-prompt", systemPrompt, prompt}, nil, nil
	}
	if plan.Harness == "hermes" {
		externalDirectories := "[]"
		if installed != "" {
			encoded, err := json.Marshal(filepath.Dir(installed))
			if err != nil {
				return nil, nil, err
			}
			externalDirectories = "[" + string(encoded) + "]"
		}
		configPath := filepath.Join(conditionDir, "hermes-config.yaml")
		config := fmt.Sprintf("{\"skills\": {\"external_dirs\": %s}, \"platform_toolsets\": {\"cli\": [\"file\"]}, \"agent\": {\"disabled_toolsets\": [\"file\"]}}\n", externalDirectories)
		if err := os.WriteFile(configPath, []byte(config), 0o666); err != nil {
			return nil, nil, err
		}
		usagePath := filepath.Join(conditionDir, "outputs", "usage.json")
		argv := []string{plan.HarnessPath, "-z", systemPrompt + "\n\n" + prompt, "--model", plan.Model, "--ignore-rules", "--ignore-user-config", "--usage-file", usagePath}
		if installed != "" {
			argv = append(argv, "--skills", suite.SkillName)
		}
		return argv, environmentWith("HERMES_CONFIG", configPath), nil
	}
	if plan.Harness == "codex" {
		home := filepath.Join(conditionDir, "harness-home")
		codexHome := filepath.Join(conditionDir, "codex-home")
		if err := os.MkdirAll(home, 0o755); err != nil {
			return nil, nil, err
		}
		if err := os.MkdirAll(codexHome, 0o755); err != nil {
			return nil, nil, err
		}
		sourceHome := os.Getenv("CODEX_HOME")
		if sourceHome == "" {
			userHome, err := os.UserHomeDir()
			if err != nil {
				return nil, nil, err
			}
			sourceHome = filepath.Join(userHome, ".codex")
		}
		authSource := filepath.Join(sourceHome, "auth.json")
		authTarget := filepath.Join(codexHome, "auth.json")
		if info, err := os.Stat(authSource); err == nil && info.Mode().IsRegular() {
			if err := os.Symlink(authSource, authTarget); err != nil && !os.IsExist(err) {
				return nil, nil, err
			}
		}
		sandbox := "workspace-write"
		if suite.ToolProfile == "no_tools" || suite.ToolProfile == "read_only" {
			sandbox = "read-only"
		}
		argv := []string{plan.HarnessPath, "exec", "--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules", "--sandbox", sandbox, "--model", plan.Model, prompt}
		return argv, environmentWithValues(map[string]string{"HOME": home, "CODEX_HOME": codexHome}), nil
	}
	argv := []string{plan.HarnessPath, "--print", "--mode", "json", "--no-session", "--no-skills", "--no-extensions", "--no-prompt-templates", "--no-context-files", "--approve", "--model", plan.Model, "--append-system-prompt", systemPrompt}
	if len(tools) == 0 {
		argv = append(argv, "--no-tools")
	} else {
		argv = append(argv, "--tools", strings.Join(tools, ","))
	}
	if installed != "" {
		argv = append(argv, "--skill", installed)
	}
	return append(argv, prompt), nil, nil
}

func judgeInvocation(plan runplan.Plan, tracePath, prompt string) ([]string, []string, error) {
	runDir := filepath.Dir(tracePath)
	judgeModel, _ := plan.JudgeModel.(string)
	switch plan.Harness {
	case "pi":
		return []string{plan.HarnessPath, "--print", "--mode", "json", "--no-session", "--no-skills", "--no-extensions", "--no-prompt-templates", "--no-context-files", "--no-tools", "--model", judgeModel, prompt}, nil, nil
	case "claude-code":
		return []string{plan.HarnessPath, "-p", "--output-format", "stream-json", "--verbose", "--model", judgeModel, "--no-session-persistence", "--safe-mode", "--strict-mcp-config", "--tools", "", prompt}, nil, nil
	case "codex":
		home := filepath.Join(runDir, "harness-home")
		codexHome := filepath.Join(runDir, "codex-home")
		if err := os.MkdirAll(home, 0o755); err != nil {
			return nil, nil, err
		}
		if err := os.MkdirAll(codexHome, 0o755); err != nil {
			return nil, nil, err
		}
		if err := linkCodexAuth(codexHome); err != nil {
			return nil, nil, err
		}
		return []string{plan.HarnessPath, "exec", "--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only", "--model", judgeModel, prompt}, environmentWithValues(map[string]string{"HOME": home, "CODEX_HOME": codexHome}), nil
	case "hermes":
		configPath := filepath.Join(runDir, "hermes-config.yaml")
		config := "{\"platform_toolsets\": {\"cli\": [\"file\"]}, \"agent\": {\"disabled_toolsets\": [\"file\"]}, \"skills\": {\"external_dirs\": []}}\n"
		if err := os.WriteFile(configPath, []byte(config), 0o666); err != nil {
			return nil, nil, err
		}
		usagePath := filepath.Join(runDir, "usage.json")
		return []string{plan.HarnessPath, "-z", prompt, "--model", judgeModel, "--usage-file", usagePath}, environmentWithValues(map[string]string{"HERMES_CONFIG": configPath, "HERMES_IGNORE_RULES": "1", "HERMES_IGNORE_USER_CONFIG": "1"}), nil
	default:
		return nil, nil, errors.New("unsupported judge harness")
	}
}

func linkCodexAuth(codexHome string) error {
	sourceHome := os.Getenv("CODEX_HOME")
	if sourceHome == "" {
		userHome, err := os.UserHomeDir()
		if err != nil {
			return err
		}
		sourceHome = filepath.Join(userHome, ".codex")
	}
	authSource := filepath.Join(sourceHome, "auth.json")
	authTarget := filepath.Join(codexHome, "auth.json")
	if info, err := os.Stat(authSource); err == nil && info.Mode().IsRegular() {
		if err := os.Symlink(authSource, authTarget); err != nil && !os.IsExist(err) {
			return err
		}
	}
	return nil
}

func environmentWith(key, value string) []string {
	return environmentWithValues(map[string]string{key: value})
}

func environmentWithValues(overrides map[string]string) []string {
	environment := append([]string(nil), os.Environ()...)
	for key, value := range overrides {
		prefix := key + "="
		found := false
		for index, entry := range environment {
			if strings.HasPrefix(entry, prefix) {
				environment[index] = prefix + value
				found = true
				break
			}
		}
		if !found {
			environment = append(environment, prefix+value)
		}
	}
	return environment
}

func applyHermesUsage(path string, metadata *traceMetadata) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var usage map[string]any
	if err := json.Unmarshal(data, &usage); err != nil {
		return err
	}
	if model, ok := usage["model"].(string); ok {
		if metadata.ActualModel != "" && !strings.EqualFold(metadata.ActualModel, model) {
			metadata.ModelAttested = false
		} else {
			metadata.ActualModel = model
			metadata.ModelAttested = true
		}
	}
	if session, ok := usage["session_id"].(string); ok {
		metadata.SessionID = session
	}
	metadata.InputTokens = integerJSON(usage["input_tokens"])
	metadata.OutputTokens = integerJSON(usage["output_tokens"])
	if input, ok := metadata.InputTokens.(int); ok {
		if output, ok := metadata.OutputTokens.(int); ok {
			metadata.TotalTokens = input + output
		}
	}
	return nil
}

func parseTrace(path, skillName string) (traceMetadata, error) {
	file, err := os.Open(path)
	if err != nil {
		return traceMetadata{}, err
	}
	defer file.Close()
	metadata := traceMetadata{}
	models := map[string]bool{}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var event map[string]any
		if json.Unmarshal(scanner.Bytes(), &event) != nil {
			continue
		}
		if value, ok := event["session_id"].(string); ok {
			metadata.SessionID = value
		}
		if event["type"] == "thread.started" {
			if value, ok := event["thread_id"].(string); ok {
				metadata.SessionID = value
			}
		}
		if event["type"] == "system" && event["subtype"] == "init" {
			if value, ok := event["model"].(string); ok {
				models[value] = true
				metadata.ActualModel = value
			}
			for _, value := range stringSlice(event["skills"]) {
				if strings.EqualFold(value, skillName) {
					metadata.SkillInjectionAttested = true
				}
			}
		}
		visitAssistant(event, &metadata, models)
		if item, ok := event["item"].(map[string]any); ok && item["type"] == "agent_message" {
			if text, ok := item["text"].(string); ok {
				metadata.FinalResponse = strings.TrimSpace(text)
			}
		}
		if event["type"] == "turn.completed" {
			if usage, ok := event["usage"].(map[string]any); ok {
				metadata.InputTokens = integerJSON(usage["input_tokens"])
				metadata.OutputTokens = integerJSON(usage["output_tokens"])
				if input, ok := metadata.InputTokens.(int); ok {
					if output, ok := metadata.OutputTokens.(int); ok {
						metadata.TotalTokens = input + output
					}
				}
			}
		}
		lower := strings.ToLower(string(scanner.Bytes()))
		if strings.Contains(lower, "/"+strings.ToLower(skillName)+"/skill.md") {
			metadata.SkillExplicitlyAccessed = true
		}
	}
	if err := scanner.Err(); err != nil {
		return traceMetadata{}, err
	}
	metadata.ModelAttested = len(models) == 1 && metadata.ActualModel != ""
	return metadata, nil
}

func applyCodexAttestation(codexHome, skillName, installed string, metadata *traceMetadata) error {
	if metadata.SessionID == "" {
		return nil
	}
	matches := []string{}
	sessions := filepath.Join(codexHome, "sessions")
	err := filepath.Walk(sessions, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			if os.IsNotExist(walkErr) {
				return nil
			}
			return walkErr
		}
		if info.Mode().IsRegular() && strings.HasSuffix(info.Name(), metadata.SessionID+".jsonl") {
			matches = append(matches, path)
		}
		return nil
	})
	if err != nil || len(matches) != 1 {
		return err
	}
	metadata.AttestationTracePath = matches[0]
	file, err := os.Open(matches[0])
	if err != nil {
		return err
	}
	defer file.Close()
	models := map[string]bool{}
	if metadata.ActualModel != "" {
		models[metadata.ActualModel] = true
	}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var event map[string]any
		if json.Unmarshal(scanner.Bytes(), &event) != nil {
			continue
		}
		payload, _ := event["payload"].(map[string]any)
		if event["type"] == "turn_context" {
			if model, ok := payload["model"].(string); ok {
				models[model] = true
				metadata.ActualModel = model
			}
		}
		lower := strings.ToLower(string(scanner.Bytes()))
		if strings.Contains(lower, "/"+strings.ToLower(skillName)+"/skill.md") {
			if event["type"] == "world_state" {
				metadata.SkillInjectionAttested = true
			}
			if event["type"] == "response_item" && installed != "" {
				metadata.SkillExplicitlyAccessed = true
			}
		}
	}
	metadata.ModelAttested = len(models) == 1 && metadata.ActualModel != ""
	return scanner.Err()
}

func visitAssistant(value any, metadata *traceMetadata, models map[string]bool) {
	switch current := value.(type) {
	case map[string]any:
		if current["role"] == "assistant" {
			if model, ok := current["model"].(string); ok {
				models[model] = true
				metadata.ActualModel = model
			}
			if content, ok := current["content"].([]any); ok {
				parts := []string{}
				for _, raw := range content {
					item, _ := raw.(map[string]any)
					if item["type"] == "text" {
						if text, ok := item["text"].(string); ok {
							parts = append(parts, text)
						}
					}
				}
				if len(parts) > 0 {
					metadata.FinalResponse = strings.TrimSpace(strings.Join(parts, "\n"))
				}
			}
			if usage, ok := current["usage"].(map[string]any); ok {
				metadata.InputTokens = integerJSON(usage["input_tokens"])
				metadata.OutputTokens = integerJSON(usage["output_tokens"])
				if input, iok := metadata.InputTokens.(int); iok {
					if output, ook := metadata.OutputTokens.(int); ook {
						metadata.TotalTokens = input + output
					}
				}
			}
		}
		for _, nested := range current {
			visitAssistant(nested, metadata, models)
		}
	case []any:
		for _, nested := range current {
			visitAssistant(nested, metadata, models)
		}
	}
}

func validateReferences(ctx context.Context, input Input, suite *evalspec.Suite) ([]referenceRecord, error) {
	records := []referenceRecord{}
	for _, current := range suite.Cases {
		workspace, err := os.MkdirTemp("", "skill-eval-reference-")
		if err != nil {
			return nil, err
		}
		if err := prepareCaseWorkspace(suite, current, workspace, true); err != nil {
			os.RemoveAll(workspace)
			return nil, err
		}
		response, _ := current.Reference["response"].(string)
		external, judges, err := modelGrades(ctx, input, current, response, filepath.Join(input.Plan.OutputDir, "reference-judges", current.ID), "reference · "+current.ID)
		if err != nil {
			os.RemoveAll(workspace)
			return nil, err
		}
		grading, err := evalspec.GradeCase(workspace, response, current.Graders, external)
		os.RemoveAll(workspace)
		if err != nil {
			return nil, err
		}
		if grading.Summary.Failed > 0 {
			return nil, fmt.Errorf("reference solution failed graders for case %s", current.ID)
		}
		record := referenceRecord{CaseID: current.ID, Valid: true, Grading: grading, JudgeRecords: judges}
		if current.HasCounterReference {
			counterWorkspace, err := os.MkdirTemp("", "skill-eval-counter-")
			if err != nil {
				return nil, err
			}
			if err := prepareCaseWorkspace(suite, current, counterWorkspace, true); err != nil {
				os.RemoveAll(counterWorkspace)
				return nil, err
			}
			counterResponse, _ := current.CounterReference["response"].(string)
			counterExternal, counterJudges, err := modelGrades(ctx, input, current, counterResponse, filepath.Join(input.Plan.OutputDir, "counter-reference-judges", current.ID), "counter-reference · "+current.ID)
			if err != nil {
				os.RemoveAll(counterWorkspace)
				return nil, err
			}
			counterGrading, err := evalspec.GradeCase(counterWorkspace, counterResponse, current.Graders, counterExternal)
			os.RemoveAll(counterWorkspace)
			if err != nil {
				return nil, err
			}
			if suite.GraderDiscrimination == "case_contrast" {
				nonDiscriminating := []string{}
				for _, expectation := range counterGrading.Expectations {
					if expectation.Passed && map[string]bool{"response_contains": true, "response_not_contains": true, "response_regex": true, "markdown_table_column_regex": true, "model_rubric": true}[expectation.Grader] {
						nonDiscriminating = append(nonDiscriminating, expectation.Text)
					}
				}
				if len(nonDiscriminating) > 0 {
					return nil, fmt.Errorf("counter-reference did not fail response-sensitive graders: %s; case %s does not prove grader discrimination", strings.Join(nonDiscriminating, ", "), current.ID)
				}
			} else if counterGrading.Summary.Failed == 0 {
				return nil, fmt.Errorf("counter-reference passed graders for case %s; the graders do not separate a correct answer from a wrong one", current.ID)
			}
			record.CounterReference = &counterReferenceRecord{Grading: counterGrading, JudgeRecords: counterJudges}
		}
		records = append(records, record)
	}
	return records, nil
}

func modelGrades(ctx context.Context, input Input, current evalspec.Case, response, traceDir, _ string) (map[string]map[string]any, []judgeRecord, error) {
	external := map[string]map[string]any{}
	records := []judgeRecord{}
	index := 0
	for _, grader := range current.Graders {
		if grader["type"] != "model_rubric" {
			continue
		}
		if input.JudgeModel == "" {
			return nil, nil, errors.New("model_rubric graders require --judge-model")
		}
		index++
		tracePath := filepath.Join(traceDir, fmt.Sprintf("judge-%03d.jsonl", index))
		grade, record, err := runModelGrade(ctx, input, current, grader, response, tracePath)
		if err != nil {
			return nil, nil, err
		}
		name := grader["name"].(string)
		record.GraderName = name
		external[name] = grade
		records = append(records, record)
	}
	return external, records, nil
}

func runModelGrade(ctx context.Context, input Input, current evalspec.Case, grader map[string]any, response, tracePath string) (map[string]any, judgeRecord, error) {
	reference, _ := current.Reference["response"].(string)
	prompt := fmt.Sprintf("You are grading one agent response.\n\nTASK:\n%s\n\nRUBRIC:\n%s\n\nKNOWN-GOOD REFERENCE:\n%s\n\nCANDIDATE:\n%s\n\nJudge the candidate against the task and rubric, not by exact wording or\nsimilarity to the reference. The candidate passes only if it satisfies every\nrubric requirement. Return JSON only:\n{\"passed\": true, \"reason\": \"specific evidence\"}\n", current.Prompt, grader["rubric"], reference, response)
	if err := os.MkdirAll(filepath.Dir(tracePath), 0o755); err != nil {
		return nil, judgeRecord{}, err
	}
	argv, environment, err := judgeInvocation(input.Plan, tracePath, prompt)
	if err != nil {
		return nil, judgeRecord{}, err
	}
	title := "Judge · " + current.ID + " · " + grader["name"].(string)
	stderrPath := strings.TrimSuffix(tracePath, filepath.Ext(tracePath)) + ".stderr.txt"
	if input.Observer != nil {
		if err := input.Observer.Begin("judge_results", title, tracePath, stderrPath); err != nil {
			return nil, judgeRecord{}, err
		}
	}
	result, err := processctl.Run(ctx, processctl.Options{Argv: argv, Env: environment, Timeout: input.JudgeTimeout})
	if err != nil {
		return nil, judgeRecord{}, err
	}
	if err := os.WriteFile(tracePath, []byte(result.Stdout), 0o666); err != nil {
		return nil, judgeRecord{}, err
	}
	if err := os.WriteFile(stderrPath, []byte(result.Stderr), 0o666); err != nil {
		return nil, judgeRecord{}, err
	}
	if input.Observer != nil {
		if err := input.Observer.End(title, result.ExitCode); err != nil {
			return nil, judgeRecord{}, err
		}
	}
	if result.TimedOut {
		return nil, judgeRecord{}, fmt.Errorf("judge timed out after %g seconds; see %s", input.JudgeTimeout.Seconds(), tracePath)
	}
	if result.ExitCode != 0 {
		return nil, judgeRecord{}, fmt.Errorf("judge exited %d; see %s", result.ExitCode, tracePath)
	}
	metadata, err := parseTrace(tracePath, "")
	if err != nil {
		return nil, judgeRecord{}, err
	}
	if input.Plan.Harness == "hermes" {
		if err := applyHermesUsage(filepath.Join(filepath.Dir(tracePath), "usage.json"), &metadata); err != nil {
			return nil, judgeRecord{}, err
		}
	}
	if input.Plan.Harness == "codex" {
		if err := applyCodexAttestation(filepath.Join(filepath.Dir(tracePath), "codex-home"), "", "", &metadata); err != nil {
			return nil, judgeRecord{}, err
		}
		if metadata.AttestationTracePath == "" {
			return nil, judgeRecord{}, fmt.Errorf("judge model %s was not attested; persisted Codex rollout is missing; see %s", input.JudgeModel, tracePath)
		}
	}
	if !metadata.ModelAttested {
		return nil, judgeRecord{}, fmt.Errorf("judge model %s was not attested; see %s", input.JudgeModel, tracePath)
	}
	if !strings.EqualFold(metadata.ActualModel, input.JudgeModel) {
		return nil, judgeRecord{}, fmt.Errorf("requested judge model %s but attested %s; see %s", input.JudgeModel, metadata.ActualModel, tracePath)
	}
	grade, err := parseJudgeGrade(metadata.FinalResponse)
	if err != nil {
		return nil, judgeRecord{}, err
	}
	traceHash, _ := fileHash(tracePath)
	relativeTrace, _ := filepath.Rel(input.Plan.OutputDir, tracePath)
	record := judgeRecord{RequestedModel: input.JudgeModel, ActualModel: metadata.ActualModel, ModelAttested: metadata.ModelAttested, SessionID: metadata.SessionID, TracePath: filepath.ToSlash(relativeTrace), TraceSHA256: traceHash, TotalTokens: metadata.TotalTokens, Cost: nil}
	if metadata.AttestationTracePath != "" {
		relative, _ := filepath.Rel(input.Plan.OutputDir, metadata.AttestationTracePath)
		record.AttestationTracePath = filepath.ToSlash(relative)
		record.AttestationTraceSHA256, _ = fileHash(metadata.AttestationTracePath)
	}
	return grade, record, nil
}

func parseJudgeGrade(response string) (map[string]any, error) {
	candidates := []string{strings.TrimSpace(response)}
	fenced := regexp.MustCompile("(?s)```(?:json)?\\s*(\\{.*?\\})\\s*```")
	for _, match := range fenced.FindAllStringSubmatch(response, -1) {
		candidates = append([]string{match[1]}, candidates...)
	}
	for _, candidate := range candidates {
		var value map[string]any
		if json.Unmarshal([]byte(candidate), &value) != nil {
			continue
		}
		passed, passedOK := value["passed"].(bool)
		reason, reasonOK := value["reason"].(string)
		if passedOK && reasonOK && strings.TrimSpace(reason) != "" {
			return map[string]any{"passed": passed, "evidence": strings.TrimSpace(reason)}, nil
		}
	}
	return nil, errors.New("judge did not return {passed: boolean, reason: string}")
}

func buildSuiteSnapshot(suite *evalspec.Suite) (suiteSnapshot, error) {
	sourceHash, err := fileHash(suite.SourcePath)
	if err != nil {
		return suiteSnapshot{}, err
	}
	result := suiteSnapshot{
		SchemaVersion: suite.SchemaVersionNumber, SkillName: suite.SkillName, SuiteType: suite.SuiteType,
		DatasetOrigin: suite.DatasetOrigin, ToolProfile: suite.ToolProfile, ActivationMode: suite.ActivationMode,
		GraderDiscrimination: suite.GraderDiscrimination, SourceSHA256: sourceHash,
	}
	for _, current := range suite.Cases {
		promptHash, err := evalspec.CanonicalSHA256(current.Prompt)
		if err != nil {
			return suiteSnapshot{}, err
		}
		graders := make([]any, len(current.Graders))
		for index, grader := range current.Graders {
			graders[index] = grader
		}
		gradersHash, err := evalspec.CanonicalSHA256(graders)
		if err != nil {
			return suiteSnapshot{}, err
		}
		modelRubrics := 0
		sensitive := []sensitiveGrader{}
		for _, grader := range current.Graders {
			typeName, _ := grader["type"].(string)
			if typeName == "model_rubric" {
				modelRubrics++
			}
			if map[string]bool{"response_contains": true, "response_not_contains": true, "response_regex": true, "markdown_table_column_regex": true, "model_rubric": true}[typeName] {
				sensitive = append(sensitive, sensitiveGrader{Name: grader["name"].(string), Type: typeName})
			}
		}
		var routing any
		if current.RoutingClass != "" {
			routing = current.RoutingClass
		}
		result.Cases = append(result.Cases, snapshotCase{ID: current.ID, BehaviorClass: current.BehaviorClass, RoutingClass: routing, ExpectedSkillLoading: current.ExpectedSkillLoading, ModelRubricCount: modelRubrics, ResponseSensitiveGraders: sensitive, CounterReferenceDeclared: current.HasCounterReference, PromptSHA256: promptHash, GradersSHA256: gradersHash})
	}
	return result, nil
}

func retainProvenance(outputDir string, suite *evalspec.Suite) (any, any, error) {
	if len(suite.ProvenanceRecords) == 0 {
		return nil, nil, nil
	}
	caseIDs := make([]string, 0, len(suite.ProvenanceRecords))
	for caseID := range suite.ProvenanceRecords {
		caseIDs = append(caseIDs, caseID)
	}
	sort.Strings(caseIDs)
	snapshot := provenanceSnapshot{SchemaVersion: 1, SourceManifestSHA256: suite.ProvenanceSHA256}
	for _, caseID := range caseIDs {
		record := suite.ProvenanceRecords[caseID]
		source, err := evalspec.SafeRelativePath(suite.SuiteRoot, record["artifact"], "provenance."+caseID+".artifact")
		if err != nil {
			return nil, nil, err
		}
		extension := filepath.Ext(source)
		if extension == "" {
			extension = ".json"
		}
		destination := filepath.Join(outputDir, "provenance", caseID+extension)
		if err := copyFile(source, destination); err != nil {
			return nil, nil, err
		}
		hash, err := fileHash(destination)
		if err != nil {
			return nil, nil, err
		}
		snapshot.Cases = append(snapshot.Cases, retainedProvenanceRecord{
			CaseID: caseID, Origin: record["origin"].(string), SourceID: record["source_id"].(string),
			SourceType: record["source_type"].(string), ObservedAt: record["observed_at"].(string), TaskAuthor: record["task_author"].(string),
			Artifact: record["artifact"].(string), ArtifactSHA256: record["artifact_sha256"].(string), CaseSHA256: record["case_sha256"].(string),
			RetainedArtifactPath: filepath.ToSlash(filepath.Join("provenance", caseID+extension)), RetainedArtifactSHA256: hash,
		})
	}
	path := filepath.Join(outputDir, "provenance_snapshot.json")
	if err := writeJSON(path, snapshot); err != nil {
		return nil, nil, err
	}
	hash, err := fileHash(path)
	if err != nil {
		return nil, nil, err
	}
	return "provenance_snapshot.json", hash, nil
}

func copyFile(source, destination string) error {
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	info, err := input.Stat()
	if err != nil {
		return err
	}
	output, err := os.OpenFile(destination, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, info.Mode().Perm())
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(output, input)
	closeErr := output.Close()
	if copyErr != nil {
		return copyErr
	}
	return closeErr
}

func writeState(root string, value state) error {
	return writeJSON(filepath.Join(root, "run_state.json"), value)
}
func writeJSON(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o666)
}
func fileHash(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}
func copyPayload(source, destination string) error {
	files, err := skillpayload.Files(source)
	if err != nil {
		return err
	}
	for _, path := range files {
		relative, _ := filepath.Rel(source, path)
		target := filepath.Join(destination, relative)
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		input, err := os.Open(path)
		if err != nil {
			return err
		}
		info, _ := input.Stat()
		output, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, info.Mode().Perm())
		if err != nil {
			input.Close()
			return err
		}
		_, copyErr := io.Copy(output, input)
		closeErr := output.Close()
		input.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	return nil
}
func prepareCaseWorkspace(suite *evalspec.Suite, current evalspec.Case, workspace string, reference bool) error {
	container := current.Raw
	key := "fixture"
	if reference {
		container = current.Reference
		key = "workspace"
	}
	value, _ := container[key].(string)
	if value == "" {
		return nil
	}
	source, err := evalspec.SafeRelativePath(suite.SuiteRoot, value, current.ID+"."+key)
	if err != nil {
		return err
	}
	info, err := os.Stat(source)
	if err != nil || !info.IsDir() {
		return fmt.Errorf("fixture directory not found: %s", source)
	}
	return copyTree(source, workspace)
}
func copyTree(source, destination string) error {
	return filepath.Walk(source, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("symlinked fixture entry is not allowed: %s", path)
		}
		relative, _ := filepath.Rel(source, path)
		target := filepath.Join(destination, relative)
		if info.IsDir() {
			return os.MkdirAll(target, info.Mode().Perm())
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
		if copyErr != nil {
			return copyErr
		}
		return closeErr
	})
}
func optionalString(value string) any {
	if value == "" {
		return nil
	}
	return value
}
func stringSlice(value any) []string {
	raw, _ := value.([]any)
	result := []string{}
	for _, item := range raw {
		if text, ok := item.(string); ok {
			result = append(result, text)
		}
	}
	return result
}
func integerJSON(value any) any {
	if number, ok := value.(float64); ok && number == math.Trunc(number) {
		return int(number)
	}
	return value
}

type state struct {
	Status              string `json:"status"`
	Valid               bool   `json:"valid"`
	Verdict             string `json:"verdict,omitempty"`
	Error               string `json:"error,omitempty"`
	Observer            string `json:"observer,omitempty"`
	CompletedConditions int    `json:"completed_conditions"`
	WorkspaceID         string `json:"workspace_id,omitempty"`
	WorkspaceLabel      string `json:"workspace_label,omitempty"`
}
type sensitiveGrader struct{ Name, Type string }

func (value sensitiveGrader) MarshalJSON() ([]byte, error) {
	return json.Marshal(struct {
		Name string `json:"name"`
		Type string `json:"type"`
	}{value.Name, value.Type})
}

type snapshotCase struct {
	ID                       string            `json:"id"`
	BehaviorClass            string            `json:"behavior_class"`
	RoutingClass             any               `json:"routing_class"`
	ExpectedSkillLoading     string            `json:"expected_skill_loading"`
	ModelRubricCount         int               `json:"model_rubric_count"`
	ResponseSensitiveGraders []sensitiveGrader `json:"response_sensitive_graders"`
	CounterReferenceDeclared bool              `json:"counter_reference_declared"`
	PromptSHA256             string            `json:"prompt_sha256"`
	GradersSHA256            string            `json:"graders_sha256"`
}
type suiteSnapshot struct {
	SchemaVersion        int            `json:"schema_version"`
	SkillName            string         `json:"skill_name"`
	SuiteType            string         `json:"suite_type"`
	DatasetOrigin        string         `json:"dataset_origin"`
	ToolProfile          string         `json:"tool_profile"`
	ActivationMode       string         `json:"activation_mode"`
	GraderDiscrimination string         `json:"grader_discrimination"`
	SourceSHA256         string         `json:"source_sha256"`
	Cases                []snapshotCase `json:"cases"`
}
type provenanceSnapshot struct {
	SchemaVersion        int                        `json:"schema_version"`
	SourceManifestSHA256 string                     `json:"source_manifest_sha256"`
	Cases                []retainedProvenanceRecord `json:"cases"`
}
type retainedProvenanceRecord struct {
	CaseID                 string `json:"case_id"`
	Origin                 string `json:"origin"`
	SourceID               string `json:"source_id"`
	SourceType             string `json:"source_type"`
	ObservedAt             string `json:"observed_at"`
	TaskAuthor             string `json:"task_author"`
	Artifact               string `json:"artifact"`
	ArtifactSHA256         string `json:"artifact_sha256"`
	CaseSHA256             string `json:"case_sha256"`
	RetainedArtifactPath   string `json:"retained_artifact_path"`
	RetainedArtifactSHA256 string `json:"retained_artifact_sha256"`
}
type referenceRecord struct {
	CaseID           string                  `json:"case_id"`
	Valid            bool                    `json:"valid"`
	Grading          evalspec.GradeResult    `json:"grading"`
	JudgeRecords     []judgeRecord           `json:"judge_records"`
	CounterReference *counterReferenceRecord `json:"counter_reference,omitempty"`
}
type counterReferenceRecord struct {
	Grading      evalspec.GradeResult `json:"grading"`
	JudgeRecords []judgeRecord        `json:"judge_records"`
}
type judgeRecord struct {
	RequestedModel         string `json:"requested_model"`
	ActualModel            string `json:"actual_model"`
	ModelAttested          bool   `json:"model_attested"`
	SessionID              string `json:"session_id"`
	TracePath              string `json:"trace_path"`
	TraceSHA256            string `json:"trace_sha256"`
	AttestationTracePath   string `json:"attestation_trace_path"`
	AttestationTraceSHA256 string `json:"attestation_trace_sha256"`
	TotalTokens            any    `json:"total_tokens"`
	Cost                   any    `json:"cost"`
	GraderName             string `json:"grader_name"`
}
type scheduleRecord struct {
	CaseID     string   `json:"case_id"`
	Trial      int      `json:"trial"`
	Conditions []string `json:"conditions"`
}
type conditionRecord struct {
	CaseID                  string               `json:"case_id"`
	Trial                   int                  `json:"trial"`
	Condition               string               `json:"condition"`
	StartedAt               string               `json:"started_at"`
	DurationSeconds         evalspec.PythonFloat `json:"duration_seconds"`
	ExitCode                int                  `json:"exit_code"`
	TimedOut                bool                 `json:"timed_out"`
	RequestedModel          string               `json:"requested_model"`
	ActualModel             string               `json:"actual_model"`
	ModelAttested           bool                 `json:"model_attested"`
	SessionID               string               `json:"session_id"`
	InputTokens             any                  `json:"input_tokens"`
	OutputTokens            any                  `json:"output_tokens"`
	TotalTokens             any                  `json:"total_tokens"`
	Cost                    any                  `json:"cost"`
	AvailableSkills         []string             `json:"available_skills"`
	SkillAvailable          bool                 `json:"skill_available"`
	SkillActivation         string               `json:"skill_activation"`
	RequestedTools          []string             `json:"requested_tools"`
	ToolEnforcement         string               `json:"tool_enforcement"`
	InstalledSkillPath      string               `json:"installed_skill_path"`
	SkillInjectionAttested  bool                 `json:"skill_injection_attested"`
	SkillExplicitlyAccessed bool                 `json:"skill_explicitly_accessed"`
	ExpectedSkillLoading    string               `json:"expected_skill_loading"`
	JudgeRecords            []judgeRecord        `json:"judge_records"`
	TracePath               string               `json:"trace_path"`
	TraceSHA256             string               `json:"trace_sha256"`
	AttestationTracePath    string               `json:"attestation_trace_path"`
	AttestationTraceSHA256  string               `json:"attestation_trace_sha256"`
	ResponsePath            string               `json:"response_path"`
	ResponseSHA256          string               `json:"response_sha256"`
	GradingPath             string               `json:"grading_path"`
	GradingSHA256           string               `json:"grading_sha256"`
}
type orderedConditions struct {
	Order  []string
	Values map[string]conditionRecord
}

func (conditions orderedConditions) MarshalJSON() ([]byte, error) {
	parts := []string{}
	for _, name := range conditions.Order {
		data, err := json.Marshal(conditions.Values[name])
		if err != nil {
			return nil, err
		}
		key, _ := json.Marshal(name)
		parts = append(parts, string(key)+":"+string(data))
	}
	return []byte("{" + strings.Join(parts, ",") + "}"), nil
}

type pairRecord struct {
	CaseID         string            `json:"case_id"`
	Trial          int               `json:"trial"`
	Conditions     orderedConditions `json:"conditions"`
	ExecutionOrder []string          `json:"execution_order"`
}
type manifest struct {
	SchemaVersion       int               `json:"schema_version"`
	TargetSkillName     string            `json:"target_skill_name"`
	Decision            string            `json:"decision"`
	ConditionVariable   string            `json:"condition_variable"`
	SkillSHA256         string            `json:"skill_sha256"`
	SuitePath           string            `json:"suite_path"`
	SuiteSHA256         string            `json:"suite_sha256"`
	ProvenancePath      any               `json:"provenance_path"`
	ProvenanceSHA256    any               `json:"provenance_sha256"`
	RequestedModel      string            `json:"requested_model"`
	JudgeModel          any               `json:"judge_model"`
	Harness             string            `json:"harness"`
	HarnessVersion      string            `json:"harness_version"`
	Observer            string            `json:"observer"`
	ToolProfile         string            `json:"tool_profile"`
	ActivationMode      string            `json:"activation_mode"`
	ExecutionOrder      string            `json:"execution_order"`
	ExecutionSchedule   []scheduleRecord  `json:"execution_schedule"`
	CaseCount           int               `json:"case_count"`
	TrialsPerCase       int               `json:"trials_per_case"`
	PairCount           int               `json:"pair_count"`
	ReferenceValidation []referenceRecord `json:"reference_validation"`
	Trials              []pairRecord      `json:"trials"`
}
