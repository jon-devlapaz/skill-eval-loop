package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/jon-devlapaz/skill-eval-loop/internal/aggregate"
	"github.com/jon-devlapaz/skill-eval-loop/internal/audit"
	"github.com/jon-devlapaz/skill-eval-loop/internal/evalspec"
	"github.com/jon-devlapaz/skill-eval-loop/internal/recommend"
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
	case "help", "-h", "--help":
		usage()
	case "run", "healthcheck":
		fmt.Fprintf(os.Stderr, "ERROR: %s is not implemented yet\n", os.Args[1])
		os.Exit(1)
	default:
		fmt.Fprintf(os.Stderr, "ERROR: unknown command: %s\n", os.Args[1])
		usage()
		os.Exit(2)
	}
}

func runRecommend(arguments []string) int {
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
		if *harness != "pi" {
			writeRecommendError("native model discovery is not implemented yet for this harness; pass --models with exact comma-separated ids")
			return 1
		}
		models, err = recommend.DiscoverPi(resolved)
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
	data, _ := json.MarshalIndent(map[string]any{"valid": false, "error": message}, "", "  ")
	os.Stdout.Write(append(data, '\n'))
}

func runAggregate(arguments []string) int {
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
	data, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		return 1
	}
	data = append(data, '\n')
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

func usage() {
	fmt.Fprintln(os.Stderr, "usage: skill-eval-loop <audit|recommend-models|run|aggregate|healthcheck> [options]")
}
