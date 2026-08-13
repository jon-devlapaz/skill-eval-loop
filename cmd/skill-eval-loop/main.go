package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/jon-devlapaz/skill-eval-loop/internal/audit"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	switch os.Args[1] {
	case "audit":
		os.Exit(runAudit(os.Args[2:]))
	case "help", "-h", "--help":
		usage()
	case "recommend-models", "run", "aggregate", "healthcheck":
		fmt.Fprintf(os.Stderr, "ERROR: %s is not implemented yet\n", os.Args[1])
		os.Exit(1)
	default:
		fmt.Fprintf(os.Stderr, "ERROR: unknown command: %s\n", os.Args[1])
		usage()
		os.Exit(2)
	}
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
