package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"github.com/jon-devlapaz/skill-eval-loop/internal/conformance"
)

func main() {
	oracle := flag.String("oracle", "", "path to the frozen Python oracle driver")
	candidate := flag.String("candidate", "", "path to the Go candidate binary")
	scenario := flag.String("scenario", "", "path to one conformance scenario JSON file")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional arguments")
		os.Exit(2)
	}
	report, err := conformance.Compare(context.Background(), conformance.Options{
		Oracle: *oracle, Candidate: *candidate, ScenarioPath: *scenario,
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		os.Exit(1)
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(report); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		os.Exit(1)
	}
	if !report.Equivalent {
		os.Exit(1)
	}
}
