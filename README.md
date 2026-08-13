# skill-eval-loop

`skill-eval-loop` is a paired behavioral evaluation tool for Agent Skills. It
runs the same task with and without a target skill, keeps the execution
conditions fixed, and reports whether the skill materially improved the
result.

The evaluator supports Hermes, Claude Code, Codex, and Pi. It uses semantic
grading, explicit provenance, response-sensitive grader contrasts, and
fail-closed runtime attestation.

## Install

Install the skill from this repository with Tink:

```bash
tink skill add jon-devlapaz/skill-eval-loop --skill skill-eval-loop
tink skill check
```

The complete interaction contract is in
[`skills/skill-eval-loop/SKILL.md`](skills/skill-eval-loop/SKILL.md).

## Validate an evaluation suite

```bash
export SKILL_EVAL_DIR="$PWD/.agents/skills/skill-eval-loop"

"$SKILL_EVAL_DIR/scripts/skill-eval-loop" audit \
  --skill-path /path/to/target-skill
```

Auditing is static and does not call a model. See the
[suite schema](skills/skill-eval-loop/references/eval-suite-schema.md) and
[evaluation authoring guide](skills/skill-eval-loop/references/eval-authoring.md)
for the format and evidence requirements.

## Run a paired diagnostic

Start with a dry run. It validates the plan and reports the exact invocation
count without creating a run or calling a model.

```bash
"$SKILL_EVAL_DIR/scripts/skill-eval-loop" run \
  --skill-path /path/to/target-skill \
  --harness codex \
  --model MODEL_ID \
  --judge-model MODEL_ID \
  --dry-run
```

Only run the live command after reviewing its invocation count and explicitly
authorizing provider calls. The default is a one-trial pilot.

See the [harness support matrix](skills/skill-eval-loop/references/harness-support.md)
for the distinction between adapter implementation and live release evidence.

## Development

The installed evaluator is a self-contained Go binary selected by a thin
macOS/Linux launcher. Development checks require Go 1.24 or newer.

```bash
go test -race ./...
go vet ./...
skills/skill-eval-loop/scripts/healthcheck.sh
```

Tink verifies that installed payload bytes and executable modes match its lock:

```bash
tink skill lock
tink skill verify
```

## License

MIT
