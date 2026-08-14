---
name: skill-eval-loop
description: >
  Run a paired Codex diagnostic that holds a task fixed and changes only access
  to one local Agent Skill. Use when measuring whether a skill improves task
  outcomes, validating a skill against JSONL eval tasks, or comparing control
  and treatment responses with retained evidence.
---

# Skill Eval Loop

Run one **paired loop**:

```text
same task + same Codex model + same tool posture
                         |
              +----------+----------+
              |                     |
        control: absent       treatment: present
              |                     |
              +----------+----------+
                         |
              responses + grades + traces
```

The target skill is the only intended variable. Treat raw responses and traces
as evidence; treat the report as a derived view.

## 1. Pin the run

Collect or infer:

- absolute target skill directory containing `SKILL.md`;
- absolute JSONL task path;
- exact Codex model identifier;
- trial count, normally `1` for a pilot;
- positive timeout;
- fresh absolute output directory.

Use Codex for the minimum path. Calculate before any live run:

```text
paired trials      = tasks × trials
target invocations = 2 × tasks × trials
judge invocations  = 0
total invocations  = target invocations
```

Keep the target skill and task file unchanged from dry-run through live
execution.

**Complete when:** every run variable and the exact live invocation count are
visible to the user.

## 2. Check the standalone package

Resolve the installed skill folder and launcher:

```bash
SKILL_EVAL_DIR=/absolute/path/to/skill-eval-loop
EVALUATOR="$SKILL_EVAL_DIR/scripts/skill-eval-loop"

"$EVALUATOR" healthcheck
codex --version
codex login status
```

Existing ChatGPT authentication is sufficient; the evaluator references the
authenticated Codex home without copying credentials. If the target skill is
already installed in that Codex home's `skills/` directory, report control
contamination and stop before invocation.

**Complete when:** healthcheck succeeds, Codex is executable and authenticated,
and the target skill is absent from global Codex skills.

## 3. Prepare deterministic tasks

Use one JSON object per non-empty line:

```json
{"id":"qualified-choice","prompt":"Choose the qualified candidate.","graders":[{"type":"regex","pattern":"(?i)\\bBlue\\b"}]}
```

Each task requires a unique path-safe `id`, a non-empty `prompt`, and at least
one grader. The live minimum supports:

- `regex` with `pattern`;
- `not_regex` with `pattern`;
- `file_exists` with workspace-relative `path`;
- `json_equal` with workspace-relative `path` and JSON `expected`.

Prefer outcome checks that distinguish a useful response from a plausible but
wrong response. Preserve semantic requirements, references, and
counter-references as extra task metadata for manual review; they do not add
judge calls.

**Complete when:** every task has a meaningful deterministic check and the
task file is frozen for the paired run.

## 4. Dry-run

Run the packaged launcher with explicit inputs:

```bash
"$EVALUATOR" run \
  --skill /absolute/path/to/target-skill \
  --tasks /absolute/path/to/tasks.jsonl \
  --output /absolute/path/to/fresh-run \
  --harness codex \
  --harness-bin /absolute/path/to/codex \
  --model exact-model-id \
  --trials 1 \
  --timeout-seconds 300 \
  --dry-run
```

Verify `valid: true`, the resolved paths and hashes, the exact model and Codex
version, `created_artifacts: false`, and the predicted invocation counts.
Present the plan and obtain explicit authorization for the reported live calls
and unknown cost.

**Complete when:** dry-run is valid, predicts the expected calls, creates no
output directory, and live execution is explicitly authorized.

## 5. Run once

Execute the same command without `--dry-run`. Monitor that process and its
condition traces. The runner executes sequentially, alternates condition order
by trial, and retains partial evidence on failure.

A failed attempt remains evidence. Diagnose it, choose a new output directory,
and obtain authorization before another live attempt.

**Complete when:** the process exits, both planned conditions are accounted
for, and the retained `run.json` is available for inspection.

## 6. Inspect the pair

Read:

```text
run.json
task-<id>/trial-<n>/report.json
task-<id>/trial-<n>/report.md
task-<id>/trial-<n>/control/response.md
task-<id>/trial-<n>/control/trace.jsonl
task-<id>/trial-<n>/treatment/response.md
task-<id>/trial-<n>/treatment/trace.jsonl
```

Confirm:

- suite and pair report `valid` / `runner_valid` are true;
- target and total invocation counts equal the dry-run plan;
- control target skill is absent;
- treatment target skill is present and its installed/source hash matches;
- treatment trace shows explicit skill access;
- both executions completed under the same requested model and tool posture;
- configured and resolved model evidence are labeled separately;
- grader evidence agrees with the raw responses;
- reported tokens are exact and missing cost stays unknown.

Interpret `treatment_only`, `both_pass`, `control_only`, `both_fail`, and
`not_scored` literally. `both_pass` can mean the task is saturated; it is not an
evaluator failure. Compare semantic requirements manually even when both
conditions pass deterministic checks.

**Complete when:** runner validity and skill outcome are reported separately,
both transcripts have been read, and every conclusion points to retained
evidence.

## Claim boundary

A one-task pilot proves that the paired loop operates for that configuration.
It does not establish general skill quality. Broader claims require realistic
unsaturated tasks, repeated trials, fair graders, and transcript review.

Codex receives the exact requested model through `--model`. When its JSON trace
does not expose resolved backend identity, report `cli_configured` and keep the
resolved identity unknown. Provider-call count and monetary cost also remain
unknown unless Codex reports them.

## Legacy branch

When the user explicitly requests schema-versioned suite auditing, model
recommendations, aggregation, another harness, or Herdr observation, use the
legacy commands and load only the relevant reference:

- suite validation: [references/eval-suite-schema.md](references/eval-suite-schema.md);
- harness status: [references/harness-support.md](references/harness-support.md);
- setup failures: [references/setup-remediation.md](references/setup-remediation.md);
- retained legacy layout: [references/workspace-layout.md](references/workspace-layout.md);
- benchmark interpretation: [references/interpret-benchmark.md](references/interpret-benchmark.md).

Keep that branch separate from the minimum JSONL paired loop.
