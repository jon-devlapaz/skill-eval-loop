# skill-eval-loop

`skill-eval-loop` is a self-contained Python 3 Agent Skill that measures
whether access to one local skill changes task outcomes. It runs the same task
under a no-skill control and an exact-hash treatment, then retains the raw
evidence and a comparison report.

## Install

Install with Tink or copy only `skills/skill-eval-loop/` into an Agent Skills
directory.

```bash
tink skill add jon-devlapaz/skill-eval-loop --skill skill-eval-loop
tink skill check
```

The public launcher requires Python 3 and no package installation:

```bash
EVALUATOR="$PWD/.agents/skills/skill-eval-loop/scripts/skill-eval-loop"
"$EVALUATOR" healthcheck
```

## Run an evaluation

Create a JSONL task file. Every non-empty line needs a unique, path-safe `id`,
a non-empty `prompt`, and one or more graders.

```json
{"id":"qualified-choice","prompt":"Choose the qualified candidate.","graders":[{"type":"regex","pattern":"(?i)\\bBlue\\b"}]}
```

Run a side-effect-free plan before a live invocation:

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

Verify the printed hashes and invocation counts, obtain authorization for the
live calls, then run the same command without `--dry-run`.

For rubric tasks, also pass `--judge-model` with a different exact model
identifier. The runner judges each condition only after deterministic gates
pass. A valid same-provider judgment is `provisional_non_independent`; a
timeout, failed gate, malformed response, or identity mismatch is `unknown`.

The runner invokes Codex sequentially in read-only mode. Odd trials run
control first; even trials run treatment first. It retains `run.json`, the
planned configuration, tasks, condition responses, traces, stderr, and a
JSON/Markdown report for every pair.

`runner_valid` means the runner held its declared variables and isolation
checks. It is not a general quality claim. Read both transcripts before
interpreting `treatment_only`, `both_pass`, `control_only`, or `both_fail`.

JSON and Markdown reports also expose activation (currently unknown),
calibration (`not_run`), every judged dimension, `quality_status`, and
`quality_outcome`. Deterministic-only reports say semantic quality was not
judged. An overall pairwise winner is not a quality pass when any dimension is
unknown or disagrees with that winner.

Live exit status is `0` when quality evidence is complete, `1` when the runner
is valid but quality is unknown or was not judged, and `2` when the runner is
invalid.

## Boundaries

The minimum runner supports Codex, deterministic graders, a provisional
same-provider rubric judge, and blinded pairwise comparison. It does not
provide independent judging, pricing, parallel execution, provider discovery,
or adapters for other harnesses.

## Development

Run the Python test suite and package healthcheck:

```bash
python3 -m unittest discover -s tests -v
skills/skill-eval-loop/scripts/healthcheck.sh
```

## License

MIT
