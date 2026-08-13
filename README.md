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

python3 "$SKILL_EVAL_DIR/scripts/audit_suite.py" \
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
python3 "$SKILL_EVAL_DIR/scripts/run_skill_eval.py" \
  --skill-path /path/to/target-skill \
  --harness codex \
  --target-model MODEL_ID \
  --grader-model MODEL_ID \
  --dry-run
```

Only run the live command after reviewing its invocation count and explicitly
authorizing provider calls. The default is a one-trial pilot.

See the [harness support matrix](skills/skill-eval-loop/references/harness-support.md)
for the distinction between adapter implementation and live release evidence.

## Development

The evaluator uses the Python standard library at runtime. Development checks
require Python 3.11 or newer and Ruff.

```bash
python3 -m unittest skills/skill-eval-loop/tests/test_skill_eval_loop.py
bash skills/skill-eval-loop/scripts/healthcheck.sh
ruff check tools tests skills/skill-eval-loop/scripts skills/skill-eval-loop/tests
```

The release identity verifier checks that Git, Tink receipts, and installed
payload bytes and executable modes all identify the same commit:

```bash
python3 tools/verify_release_identity.py --help
```

## License

MIT
