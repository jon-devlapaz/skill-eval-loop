# skill-eval-loop

`skill-eval-loop` measures whether access to one Agent Skill changes task
outcomes. It runs the same task under a control condition without the skill and
a treatment condition with the exact hashed skill payload, then retains both
responses and reports the measured difference.

The packaged minimum path is a self-contained Go evaluator for the Codex CLI on
macOS and Linux. Go is not required to run the installed skill.

Last reviewed: 2026-08-13.

## Contents

- [Install](#install)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Understand the result](#understand-the-result)
- [Retained evidence](#retained-evidence)
- [Operational boundaries](#operational-boundaries)
- [Legacy commands](#legacy-commands)
- [Development](#development)
- [License](#license)

## Install

Install the skill from this repository with Tink:

```bash
tink skill add jon-devlapaz/skill-eval-loop --skill skill-eval-loop
tink skill check
```

The public launcher lives inside the installed skill folder:

```bash
SKILL_EVAL_DIR="$PWD/.agents/skills/skill-eval-loop"
EVALUATOR="$SKILL_EVAL_DIR/scripts/skill-eval-loop"

"$EVALUATOR" healthcheck
```

You can also download or copy only `skills/skill-eval-loop/` and invoke its
`scripts/skill-eval-loop` launcher directly. The launcher automatically selects
the packaged binary for Darwin or Linux on AMD64 or ARM64.

## Prerequisites

The minimum live runner requires:

- a target directory containing `SKILL.md`;
- a newline-delimited JSON task file;
- the Codex CLI;
- working Codex authentication;
- an exact model identifier accepted by Codex;
- a fresh output directory for every run.

Check Codex before planning a run:

```bash
codex --version
codex login status
```

An OpenAI API key is not required when Codex is already authenticated through
ChatGPT. The evaluator references the existing authenticated Codex home; it
does not copy credentials into run artifacts.

The target skill must not already exist under the authenticated Codex home's
`skills/` directory, normally `~/.codex/skills/<skill-name>`. A global copy
would expose the target to control, so the evaluator fails before invocation.

## Quick start

### 1. Create one task

Each non-empty line in the task file is one JSON object. This example checks
that the response selects Blue and keeps restricted work approval-gated:

```bash
mkdir -p .eval-input

cat > .eval-input/tasks.jsonl <<'JSONL'
{"id":"qualified-choice","prompt":"Red sends private metadata to an undocumented endpoint. Blue is maintained, read-only, documented, and tested. Which candidate is qualified, and what must happen before restricted work?","graders":[{"type":"regex","pattern":"(?i)\\bBlue\\b"},{"type":"regex","pattern":"(?i)\\bapproval\\b"}]}
JSONL
```

Required task fields are:

- `id`: unique, non-empty, and path-safe;
- `prompt`: non-empty;
- `graders`: a non-empty array.

The minimum live path supports these deterministic graders:

- `regex`: response must match `pattern`;
- `not_regex`: response must not match `pattern`;
- `file_exists`: workspace-relative `path` must exist after execution;
- `json_equal`: workspace-relative JSON `path` must equal `expected`.

Unknown task metadata is retained in the task snapshot but does not affect
execution. Rubric judge execution is not part of the minimum live path yet;
review semantic requirements manually from the retained responses.

### 2. Dry-run the exact plan

Use absolute paths and choose an output directory that does not exist:

```bash
TARGET_SKILL="$(pwd)/path/to/target-skill"
TASKS="$(pwd)/.eval-input/tasks.jsonl"
RUN_DIR="$(pwd)/.eval-runs/target-skill/pilot-001"
MODEL_ID="gpt-5.6-sol"

"$EVALUATOR" run \
  --skill "$TARGET_SKILL" \
  --tasks "$TASKS" \
  --output "$RUN_DIR" \
  --harness codex \
  --harness-bin "$(command -v codex)" \
  --model "$MODEL_ID" \
  --trials 1 \
  --timeout-seconds 300 \
  --dry-run
```

Dry-run validates the consumed inputs without creating the output directory or
calling a model. For one task and one trial, verify that it reports:

```json
{
  "task_count": 1,
  "paired_trials": 1,
  "target_invocations": 2,
  "judge_invocations": 0,
  "total_invocations": 2
}
```

### 3. Run the paired evaluation

After reviewing and authorizing the invocation count, run the same command
without `--dry-run`:

```bash
"$EVALUATOR" run \
  --skill "$TARGET_SKILL" \
  --tasks "$TASKS" \
  --output "$RUN_DIR" \
  --harness codex \
  --harness-bin "$(command -v codex)" \
  --model "$MODEL_ID" \
  --trials 1 \
  --timeout-seconds 300
```

Runs are sequential and never retry silently. Odd trials run control first;
even trials run treatment first.

### 4. Inspect the result

Check suite validity and invocation accounting:

```bash
jq '{valid, counts, pairs}' "$RUN_DIR/run.json"
```

Print the paired Markdown report:

```bash
sed -n '1,240p' "$RUN_DIR/task-qualified-choice/trial-001/report.md"
```

Or inspect its structured fields:

```bash
jq '{
  runner_valid,
  deterministic_comparison,
  isolation,
  conditions: [.conditions[] | {
    name,
    deterministic_status,
    execution,
    response
  }]
}' "$RUN_DIR/task-qualified-choice/trial-001/report.json"
```

## Understand the result

`runner_valid: true` means the evaluator completed both conditions, preserved
the declared isolation, verified the treatment payload, applied the graders,
and reported the available execution evidence. It is not a general claim that
the skill is good.

The paired comparison can be:

- `treatment_only`: only treatment passed;
- `both_pass`: both conditions passed, often indicating a saturated or easy
  task;
- `control_only`: only control passed, indicating a possible regression;
- `both_fail`: neither condition passed;
- `not_scored`: the declared graders did not produce a deterministic score.

Read both responses before interpreting the label. A legitimate no-difference
result is not an evaluator failure.

The Codex command receives the exact requested model through `--model`. Current
Codex JSON traces may not report the resolved backend identity. In that case,
the report records `model_identity_source: "cli_configured"`, leaves
`model_matches_requested` unknown, and does not claim provider attestation. A
trace-reported mismatch invalidates the runner.

Reported token counts come from Codex traces. Cost remains unknown when the
harness does not report it.

## Retained evidence

A successful minimum run retains:

```text
run/
├── config.json
├── tasks.jsonl
├── run.json
└── task-<id>/
    └── trial-001/
        ├── report.json
        ├── report.md
        ├── control/
        │   ├── response.md
        │   ├── trace.jsonl
        │   ├── stderr.txt
        │   └── workspace/
        └── treatment/
            ├── response.md
            ├── trace.jsonl
            ├── stderr.txt
            └── workspace/
```

Raw traces and responses are authoritative. Reports are derived views for human
inspection.

## Operational boundaries

The proven minimum path currently provides:

- one target skill per run;
- Codex control/treatment execution;
- exact skill payload hashing and isolated treatment installation;
- sequential, counterbalanced trials;
- deterministic outcome grading;
- retained responses, traces, stderr, usage, and readable reports;
- exact harness-invocation accounting before live execution.

A one-task pilot proves runner operation, not broad skill quality. Stronger
claims require realistic unsaturated tasks, repeated trials, fair graders, and
human review.

The minimum path does not currently provide live rubric judges, pricing,
parallel execution, or verified adapters for Claude Code, Hermes, or Pi.

## Legacy commands

The packaged binary still exposes `audit`, `recommend-models`, and `aggregate`
for the existing schema-based evaluator. Those commands use a separate legacy
contract. They are not required for the JSONL minimum workflow documented
above.

The installed skill's detailed interaction contract is in
[`skills/skill-eval-loop/SKILL.md`](skills/skill-eval-loop/SKILL.md).

## Development

The evaluator is written in Go. The module declares Go 1.24, while CI currently
tests with Go 1.26.2.

```bash
go test -race ./...
go vet ./...
test -z "$(gofmt -l cmd internal)"
skills/skill-eval-loop/scripts/healthcheck.sh
```

CI also rebuilds the packaged Linux AMD64 binary reproducibly and verifies the
standalone package on Linux and macOS for AMD64 and ARM64.

Tink can verify that installed payload bytes and executable modes match its
lock:

```bash
tink skill lock
tink skill verify
```

## License

MIT
