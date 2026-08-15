---
name: skill-eval-loop
description: Run a paired, evidence-retaining Codex evaluation of one local Agent Skill against a no-skill control. Use when measuring whether a skill improves JSONL-defined task outcomes, validating a skill with deterministic graders, or comparing control and treatment responses.
---

# Skill Eval Loop

Measure one conditional claim: under fixed tasks, model, harness, timeout, and
tool posture, does access to this exact skill payload change the outcome?

Keep raw responses and traces as evidence. Treat reports as derived views.

## Run the offline check first

Use the installed skill's public launcher. It requires only Python 3.

```bash
EVALUATOR=/absolute/path/to/skill-eval-loop/scripts/skill-eval-loop
"$EVALUATOR" healthcheck
```

Use newline-delimited JSON tasks. Each task needs a path-safe `id`, a non-empty
`prompt`, and at least one grader.

```json
{"id":"qualified-choice","prompt":"Choose the qualified candidate.","graders":[{"type":"regex","pattern":"(?i)\\bBlue\\b"}]}
```

Use `response_not_empty` as the deterministic preflight for every qualitative
rubric. It verifies that there is a response; it is not a quality score. A
rubric contains named dimensions, each with at least two named descriptive
levels:

```json
{"id":"scoped-change","prompt":"Make the smallest safe change.","graders":[{"type":"response_not_empty"},{"type":"rubric","dimensions":[{"name":"scope","levels":[{"name":"not_met","description":"Changes unrelated behavior."},{"name":"met","description":"Changes only the requested behavior."}]}]}]}
```

Use `regex` or `not_regex` only for genuinely machine-checkable response
requirements. Use `file_exists` and `json_equal` only when the configured
harness can create the stated workspace artifact. Keep unknown task metadata
for human review; it does not affect execution.

The current runner starts each condition in an empty read-only workspace. Use
response-only tasks unless the prompt itself contains all required material.
Do not use repository-editing or test-running tasks as quality evidence: there
is no seeded repository for the agent to change or verify.

## Find or author the suite

Pass `--tasks` when evaluating a caller-owned JSONL task file. Otherwise, the
runner uses `TARGET/evals/tasks.jsonl`.

If neither exists, do not co-author the suite. Read
[`references/eval-authoring.md`](references/eval-authoring.md), then launch a
fresh-context subagent with only the target's absolute path and the task
contract. Let it write only `TARGET/evals/**` and return a factual handoff.
Inspect the resulting diff and run a dry-run before the paired evaluation.

The Python runner does not spawn agents or create target files itself. Its
missing-suite error is the precondition for this coordinator workflow.

## Plan the exact run

Pass absolute paths and a fresh output directory. Dry-run validates consumed
inputs, resolves the Codex executable, hashes the skill and tasks, and creates
neither run artifacts nor provider calls.

```bash
"$EVALUATOR" run \
  --skill /absolute/path/to/target-skill \
  --output /absolute/path/to/fresh-run \
  --harness codex \
  --harness-bin /absolute/path/to/codex \
  --model exact-model-id \
  --trials 1 \
  --timeout-seconds 300 \
  --dry-run
```

Add `--tasks /absolute/path/to/tasks.jsonl` to evaluate a task file outside the
target skill.

Verify `valid: true`, hashes, resolved model and executable, and the predicted
invocation count. Obtain authorization for the displayed live calls before
running without `--dry-run`.

For `tasks × trials`, the runner plans two target invocations per paired trial.
For rubric tasks, also pass an exact `--judge-model`. The judge must differ from
the runner model. An OpenAI model judging another OpenAI model is explicitly
same-provider evidence, not an independent judgment. A recommended OpenAI-only
pair is `--model gpt-5.6-terra --judge-model gpt-5.6-sol`.

## Calibrate the pairwise judge

Score the judge against versioned human-labeled cases before a live quality
pilot. The suite must include `known-better`, `known-worse`, and `tie`, each
with rationale. The judge sees anonymized `A`/`B` text only.

```bash
"$EVALUATOR" calibrate \
  --fixtures /absolute/path/to/calibration/v1.json \
  --output /absolute/path/to/fresh-calibration \
  --harness codex \
  --harness-bin /absolute/path/to/codex \
  --model exact-model-id \
  --judge-model exact-judge-model-id \
  --dry-run
```

Dry-run prints the locked cases and invocation count. A live calibrate retains
`calibration.json` plus per-case judge artifacts. `accepted` is true only when
every required judgment succeeds and agreements meet `minimum_agreements`.
Disagreements keep the human rationale. Exit `0` if accepted, `1` if the
runner is valid but below threshold, and `2` if a judgment is invalid.

Do not treat same-provider calibration as independent. Do not run a live paired
pilot until calibration is accepted and a human reviews disagreements.

## Run one pair

Run the identical command without `--dry-run`. The runner:

- uses a no-skill control and an exact-hash treatment;
- runs sequentially, alternating control-first and treatment-first by trial;
- invokes Codex in read-only mode;
- retains response, trace, stderr, execution metadata, and reports;
- runs deterministic gates before any rubric judge;
- asks the judge for concrete evidence and one locked level per dimension;
- never retries silently.

Treat `runner_valid` and the deterministic comparison separately. A valid
runner may show `both_pass`, `both_fail`, `control_only`, or `treatment_only`.
Read both responses before making a quality claim.

For rubric tasks, inspect each condition's `rubric_judgments`, the pair's
`pairwise` evidence, and `dimension_results`. A successful Codex judgment is
labeled `provisional_non_independent`. A timeout, failed deterministic gate,
malformed response, mismatched judge identity, or identical runner and judge
model produces `unknown`. If the trace does not report a model, the requested
judge model is recorded as unattested CLI configuration. Pairwise comparison
runs only after both per-output judgments succeed. The pairwise prompt uses
`A` and `B`; the report restores control and treatment. Pairwise status is
quality evidence, not runner validity.

`quality_status` is evidence completeness. `quality_outcome` is `not_judged`
when there is no rubric, `unknown` when any required judgment is unknown,
`tie` when the restored winner is a tie, `inconsistent` when a pairwise
dimension disagrees with the overall winner, or the restored winner condition.
An overall winner is never a quality pass when a dimension is unknown or
disagrees. Activation is reported as unknown because Codex telemetry is not
scored. Calibration stays `not_run` until a later calibration step.

Process exit status is `0` for complete provisional quality evidence, `1` when
the runner is valid but quality is unknown or was not judged, and `2` when the
runner is invalid.

## Inspect retained evidence

Read `run.json` followed by each pair's `report.json`, `report.md`, responses,
traces, and stderr. Confirm the control lacks the target skill, the treatment
contains the source hash, and any trace-reported model identity agrees with the
requested model.

A live run creates `$output/codex-home` and points Codex at that directory.
If `~/.codex/auth.json` exists, it is copied there for the process and removed
afterward. Do not treat that file as retained evidence. Host Codex skills are
not part of the intervention.

Treat access to the exact hashed payload as the intervention. A trace may help
explain how Codex used that access, but missing activation telemetry does not
invalidate the outcome comparison or become a quality score. Phrase the result
as the measured effect of skill access under the recorded configuration; do not
claim that Codex definitely read or followed the skill.

Do not claim broad skill quality from one pilot or from same-provider judging.
Use realistic unsaturated tasks, repeated trials, deterministic outcomes,
blinded comparison, human calibration, and human transcript review.
