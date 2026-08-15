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
Rubric graders are counted during planning but are not yet supported in a live
minimum run.

## Run one pair

Run the identical command without `--dry-run`. The runner:

- uses a no-skill control and an exact-hash treatment;
- runs sequentially, alternating control-first and treatment-first by trial;
- invokes Codex in read-only mode;
- retains response, trace, stderr, execution metadata, and reports;
- never retries silently.

Treat `runner_valid` and the deterministic comparison separately. A valid
runner may show `both_pass`, `both_fail`, `control_only`, or `treatment_only`.
Read both responses before making a quality claim.

## Inspect retained evidence

Read `run.json` followed by each pair's `report.json`, `report.md`, responses,
traces, and stderr. Confirm the control lacks the target skill, the treatment
contains the source hash, and any trace-reported model identity agrees with the
requested model.

Do not claim broad skill quality from one pilot. Use realistic unsaturated
tasks, repeated trials, deterministic outcomes, and human transcript review.
