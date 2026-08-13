package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/jon-devlapaz/skill-eval-loop/internal/aggregate"
	"github.com/jon-devlapaz/skill-eval-loop/internal/audit"
	"github.com/jon-devlapaz/skill-eval-loop/internal/evalspec"
	"github.com/jon-devlapaz/skill-eval-loop/internal/recommend"
	"github.com/jon-devlapaz/skill-eval-loop/internal/runexec"
	"github.com/jon-devlapaz/skill-eval-loop/internal/runplan"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	switch os.Args[1] {
	case "audit":
		os.Exit(runAudit(os.Args[2:]))
	case "aggregate":
		os.Exit(runAggregate(os.Args[2:]))
	case "recommend-models":
		os.Exit(runRecommend(os.Args[2:]))
	case "run":
		os.Exit(runRun(os.Args[2:]))
	case "help", "-h", "--help":
		usage()
	case "healthcheck":
		os.Exit(runHealthcheck(os.Args[2:]))
	default:
		fmt.Fprintf(os.Stderr, "ERROR: unknown command: %s\n", os.Args[1])
		usage()
		os.Exit(2)
	}
}

func runHealthcheck(arguments []string) int {
	if hasHelp(arguments) {
		fmt.Print(healthcheckHelp)
		return 0
	}
	flags := flag.NewFlagSet("healthcheck", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	skillDir := flags.String("skill-dir", "", "installed skill directory")
	if err := flags.Parse(arguments); err != nil {
		return 2
	}
	if flags.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "ERROR: unexpected positional arguments")
		return 2
	}
	root := *skillDir
	if root == "" {
		executable, err := os.Executable()
		if err != nil {
			fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
			return 1
		}
		root = filepath.Clean(filepath.Join(filepath.Dir(executable), "..", ".."))
	}
	root, err := filepath.Abs(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		return 1
	}
	required := []string{"SKILL.md", "references/eval-suite-schema.md", "references/harness-support.md"}
	errorsFound := []string{}
	for _, relative := range required {
		info, statErr := os.Stat(filepath.Join(root, filepath.FromSlash(relative)))
		if statErr != nil || !info.Mode().IsRegular() {
			errorsFound = append(errorsFound, relative+" is missing")
		}
	}
	report := struct {
		Valid    bool     `json:"valid"`
		SkillDir string   `json:"skill_dir"`
		Commands []string `json:"commands"`
		Errors   []string `json:"errors"`
	}{Valid: len(errorsFound) == 0, SkillDir: root, Commands: []string{"audit", "recommend-models", "run", "aggregate", "healthcheck"}, Errors: errorsFound}
	data, marshalErr := json.MarshalIndent(report, "", "  ")
	if marshalErr != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", marshalErr)
		return 1
	}
	_, _ = os.Stdout.Write(append(data, '\n'))
	if !report.Valid {
		return 1
	}
	return 0
}

func runRecommend(arguments []string) int {
	if hasHelp(arguments) {
		fmt.Print(recommendHelp)
		return 0
	}
	flags := flag.NewFlagSet("recommend-models", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	skillPath := flags.String("skill-path", "", "target skill directory")
	harness := flags.String("harness", "", "harness name")
	harnessBin := flags.String("harness-bin", "", "harness executable")
	profile := flags.String("task-profile", "", "task profile")
	modelsValue := flags.String("models", "", "exact comma-separated model ids")
	if err := flags.Parse(arguments); err != nil {
		return 2
	}
	if *skillPath == "" || *harness == "" || *profile == "" {
		fmt.Fprintln(os.Stderr, "ERROR: --skill-path, --harness, and --task-profile are required")
		return 2
	}
	executable := *harnessBin
	if executable == "" {
		executable = map[string]string{"pi": "pi", "codex": "codex", "hermes": "hermes", "claude-code": "claude"}[*harness]
	}
	resolved, err := exec.LookPath(executable)
	if err != nil {
		writeRecommendError(fmt.Sprintf("%s executable not found: %s", *harness, executable))
		return 1
	}
	versionCommand := exec.Command(resolved, "--version")
	versionOutput, err := versionCommand.Output()
	if err != nil {
		writeRecommendError(err.Error())
		return 1
	}
	version := strings.TrimSpace(string(versionOutput))
	if version == "" {
		writeRecommendError(fmt.Sprintf("%s returned an empty version", *harness))
		return 1
	}
	suite, err := evalspec.Load(*skillPath, "")
	if err != nil {
		writeRecommendError(err.Error())
		return 1
	}
	counts := make([]int, len(suite.Cases))
	counters := make([]bool, len(suite.Cases))
	total := 0
	for index, current := range suite.Cases {
		for _, grader := range current.Graders {
			if grader["type"] == "model_rubric" {
				counts[index]++
				total++
			}
		}
		counters[index] = current.HasCounterReference
	}
	models := recommend.ParseExplicit(*modelsValue)
	if *modelsValue == "" {
		switch *harness {
		case "pi":
			models, err = recommend.DiscoverPi(resolved)
		case "codex":
			models, err = recommend.DiscoverCodex()
		case "hermes":
			models, err = recommend.DiscoverHermes()
		case "claude-code":
			writeRecommendError("Claude Code does not expose a stable non-interactive model inventory; after `claude auth status`, pass --models with the exact ids shown by its model picker")
			return 1
		default:
			writeRecommendError("native model discovery is not implemented yet for this harness; pass --models with exact comma-separated ids")
			return 1
		}
		if err != nil {
			writeRecommendError(err.Error())
			return 1
		}
	}
	report, err := recommend.Build(recommend.Input{Harness: *harness, Models: models, TaskProfile: *profile, CaseCount: len(suite.Cases), ModelRubricCounts: counts, CounterReferences: counters, Trials: 1})
	if err != nil {
		writeRecommendError(err.Error())
		return 1
	}
	data, err := recommend.Bytes(report, version, suite.SkillName, len(suite.Cases), total)
	if err != nil {
		writeRecommendError(err.Error())
		return 1
	}
	if _, err = os.Stdout.Write(data); err != nil {
		return 1
	}
	return 0
}

func writeRecommendError(message string) {
	data, _ := json.MarshalIndent(struct {
		Valid bool   `json:"valid"`
		Error string `json:"error"`
	}{Valid: false, Error: message}, "", "  ")
	os.Stdout.Write(append(data, '\n'))
}

func runAggregate(arguments []string) int {
	if hasHelp(arguments) {
		fmt.Print(aggregateHelp)
		return 0
	}
	flags := flag.NewFlagSet("aggregate", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	flags.Usage = func() {
		fmt.Fprintln(flags.Output(), "usage: skill-eval-loop aggregate --run-dir PATH [--output PATH]")
		flags.PrintDefaults()
	}
	runDir := flags.String("run-dir", "", "retained run directory")
	output := flags.String("output", "", "benchmark output path")
	if err := flags.Parse(arguments); err != nil {
		return 2
	}
	if *runDir == "" {
		fmt.Fprintln(os.Stderr, "ERROR: --run-dir is required")
		return 2
	}
	report, err := aggregate.Run(*runDir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		return 1
	}
	data, err := aggregate.Bytes(report)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		return 1
	}
	destination := *output
	if destination == "" {
		destination = filepath.Join(*runDir, "benchmark.json")
	}
	if err := os.WriteFile(destination, data, 0o666); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		return 1
	}
	if _, err = os.Stdout.Write(data); err != nil {
		return 1
	}
	return 0
}

func runAudit(arguments []string) int {
	if hasHelp(arguments) {
		fmt.Print(auditHelp)
		return 0
	}
	flags := flag.NewFlagSet("audit", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	flags.Usage = func() {
		fmt.Fprintln(flags.Output(), "usage: skill-eval-loop audit --skill-path PATH [--evals-path PATH] [--output PATH]")
		flags.PrintDefaults()
	}
	skillPath := flags.String("skill-path", "", "target skill directory")
	evalsPath := flags.String("evals-path", "", "eval suite JSON path")
	output := flags.String("output", "", "write report to path")
	if err := flags.Parse(arguments); err != nil {
		return 2
	}
	if flags.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "ERROR: unexpected positional arguments")
		return 2
	}
	if *skillPath == "" {
		fmt.Fprintln(os.Stderr, "ERROR: --skill-path is required")
		return 2
	}
	report := audit.Run(*skillPath, *evalsPath)
	if err := audit.Write(report, *output); err != nil {
		fmt.Fprintln(os.Stderr, audit.FormatError(err))
		return 1
	}
	return audit.ExitCode(report)
}

func runRun(arguments []string) int {
	if hasHelp(arguments) {
		fmt.Print(runHelp)
		return 0
	}
	flags := flag.NewFlagSet("run", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	skillPath := flags.String("skill-path", "", "target skill directory")
	evalsPath := flags.String("evals-path", "", "eval suite JSON path")
	outputDir := flags.String("output-dir", "", "retained run directory")
	model := flags.String("model", "", "exact pinned target model")
	trials := flags.Int("trials", 1, "paired trials per case")
	harness := flags.String("harness", "", "harness name")
	harnessBin := flags.String("harness-bin", "", "harness executable")
	piBin := flags.String("pi-bin", "", "Pi compatibility executable")
	timeoutSeconds := flags.Int("timeout-seconds", 120, "target timeout")
	judgeModel := flags.String("judge-model", "", "exact pinned judge model")
	judgeTimeoutSeconds := flags.Int("judge-timeout-seconds", 120, "judge timeout")
	observer := flags.String("observer", "headless", "headless or herdr")
	dryRun := flags.Bool("dry-run", false, "validate and print the run plan")
	if err := flags.Parse(arguments); err != nil {
		return 2
	}
	if *skillPath == "" || *model == "" || *harness == "" {
		fmt.Fprintln(os.Stderr, "ERROR: --skill-path, --model, and --harness are required")
		return 2
	}
	plan, err := runplan.Build(runplan.Input{
		SkillPath: *skillPath, EvalsPath: *evalsPath, OutputDir: *outputDir,
		Model: *model, Trials: *trials, Harness: *harness, HarnessBin: *harnessBin,
		PiBin: *piBin, JudgeModel: *judgeModel, Observer: *observer,
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		return 1
	}
	if !*dryRun {
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		report, runErr := runexec.Run(ctx, runexec.Input{Plan: plan, EvalsPath: *evalsPath, Timeout: time.Duration(*timeoutSeconds) * time.Second, JudgeModel: *judgeModel, JudgeTimeout: time.Duration(*judgeTimeoutSeconds) * time.Second})
		if runErr != nil {
			if errors.Is(runErr, context.Canceled) {
				fmt.Fprintln(os.Stderr, "ERROR: evaluation cancelled; partial evidence was preserved")
				return 130
			}
			fmt.Fprintf(os.Stderr, "ERROR: %v\n", runErr)
			return 1
		}
		data, renderErr := aggregate.Bytes(report)
		if renderErr != nil {
			fmt.Fprintf(os.Stderr, "ERROR: %v\n", renderErr)
			return 1
		}
		if _, writeErr := os.Stdout.Write(data); writeErr != nil {
			return 1
		}
		return 0
	}
	data, err := runplan.Bytes(plan)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		return 1
	}
	if _, err := os.Stdout.Write(data); err != nil {
		return 1
	}
	return 0
}

func hasHelp(arguments []string) bool {
	for _, argument := range arguments {
		if argument == "-h" || argument == "--help" {
			return true
		}
	}
	return false
}

const auditHelp = `usage: audit_suite.py [-h] --skill-path SKILL_PATH [--evals-path EVALS_PATH]
                      [--output OUTPUT]

Validate a local eval suite, its routing, and its provenance.

options:
  -h, --help            show this help message and exit
  --skill-path SKILL_PATH
  --evals-path EVALS_PATH
  --output OUTPUT
`

const recommendHelp = `usage: recommend_models.py [-h] --skill-path SKILL_PATH
                           --harness {hermes,claude-code,codex,pi}
                           [--harness-bin HARNESS_BIN]
                           --task-profile {simple,standard,complex,portability}
                           [--models MODELS]

Recommend a no-call model configuration for one skill eval.

options:
  -h, --help            show this help message and exit
  --skill-path SKILL_PATH
  --harness {hermes,claude-code,codex,pi}
  --harness-bin HARNESS_BIN
  --task-profile {simple,standard,complex,portability}
  --models MODELS       Exact comma-separated model ids when native discovery
                        is unavailable.
`

const aggregateHelp = `usage: aggregate_benchmark.py [-h] --run-dir RUN_DIR [--output OUTPUT]

Validate a paired Pi run and write benchmark.json.

options:
  -h, --help         show this help message and exit
  --run-dir RUN_DIR
  --output OUTPUT
`

const runHelp = `usage: run_skill_eval.py [-h] --skill-path SKILL_PATH
                         [--evals-path EVALS_PATH] [--output-dir OUTPUT_DIR]
                         --model MODEL [--trials TRIALS]
                         --harness {hermes,claude-code,codex,pi}
                         [--harness-bin HARNESS_BIN] [--pi-bin PI_BIN]
                         [--timeout-seconds TIMEOUT_SECONDS]
                         [--judge-model JUDGE_MODEL]
                         [--judge-timeout-seconds JUDGE_TIMEOUT_SECONDS]
                         [--observer {headless,herdr}] [--dry-run]

Run a paired harness evaluation with and without one skill.

options:
  -h, --help            show this help message and exit
  --skill-path SKILL_PATH
  --evals-path EVALS_PATH
  --output-dir OUTPUT_DIR
                        Defaults to .eval-runs/<skill-name>/<run-id>/.
  --model MODEL
  --trials TRIALS
  --harness {hermes,claude-code,codex,pi}
  --harness-bin HARNESS_BIN
  --pi-bin PI_BIN
  --timeout-seconds TIMEOUT_SECONDS
  --judge-model JUDGE_MODEL
  --judge-timeout-seconds JUDGE_TIMEOUT_SECONDS
  --observer {headless,herdr}
                        Run headlessly or mirror processes in a retained Herdr
                        workspace.
  --dry-run             Validate and print the run plan without creating files
                        or calling a model.
`

const healthcheckHelp = `usage: skill-eval-loop healthcheck [-h] [--skill-dir SKILL_DIR]

Validate an installed Go evaluator package without provider calls.

options:
  -h, --help            show this help message and exit
  --skill-dir SKILL_DIR installed skill directory; inferred from packaged binary
`

func usage() {
	fmt.Fprintln(os.Stderr, "usage: skill-eval-loop <audit|recommend-models|run|aggregate|healthcheck> [options]")
}
