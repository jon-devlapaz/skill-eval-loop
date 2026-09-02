# skill-eval-loop

`skill-eval-loop` measures one question: for this skill, these JSONL tasks,
this CLI harness, and this opaque model id, does injecting `SKILL.md` change
the outcome versus the same prompt with no skill?

The runner does not talk to a model vendor. It runs whatever binary
`--harness` / `--harness-bin` names, passes `--model` through unchanged, and
keeps traces. `--harness script` is the escape hatch for any other program.
Control gets the raw task. Treatment gets the hashed skill instructions.
Reports are derived from retained evidence.

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
"$EVALUATOR" models --harness pi
```

Copy `--model` and `--judge-model` from that harness's listing. If the listing
is non-empty, `run` and `calibrate` reject ids that are not on it. An empty
listing does not reject. Omit `--harness-bin` when the CLI is on `PATH` or in a
standard location such as `~/.local/bin`. Relative, `~/`, and symlink paths are
resolved to canonical absolute paths before validation and recording. Re-use an
output directory with `--force`. The default timeout is 300 seconds. For a
quality-complete rubric run, use the same `--harness` for student and judge: a
different `--judge-harness` can mark a run independent, but that calibration
cannot bind.

## Run an evaluation

Create a JSONL task file. Every non-empty line needs a unique, path-safe `id`,
a non-empty `prompt`, and one or more graders.

```json
{"id":"qualified-choice","prompt":"Choose the qualified candidate.","graders":[{"type":"regex","pattern":"(?i)\\bBlue\\b"}]}
```

Run a side-effect-free plan before a live invocation:

```bash
"$EVALUATOR" run \
  --skill ./skills/target-skill \
  --tasks ./tasks.jsonl \
  --output "$PWD/.eval-output/fresh-run" \
  --harness pi \
  --model exact-model-id \
  --trials 1 \
  --dry-run
```

Verify the printed hashes and invocation counts, obtain authorization for the
live calls, then run the same command without `--dry-run`.

### Public reference benchmark

The checked-in development benchmark evaluates Vercel's
`vercel-react-best-practices` skill against the no-skill control:

- repository: `https://github.com/vercel-labs/agent-skills.git`
- revision: `b8caa260a420a73042e35521de4b5c8baf6446cc`
- skill path: `skills/react-best-practices`
- tasks: `tasks/react-best-practices-v1.jsonl`
- expected evaluator payload SHA-256:
  `5cbdbd8d9acc6913b8f4e0c7151830e88417872421a5975b86fa4b3eba5c36d3`
- expected task SHA-256:
  `621a609cfcdb82756ebe6870a0fad16c6ef12f6186f6c75abb213195b4333c92`

Fetch that exact revision into a controlled local directory and pass the
absolute skill subpath plus the checked-in task file to `run --dry-run`. Reject
the plan if the revision or payload hash differs. The public task file is
development evidence, not a secret client holdout. The 2026-09-01 Cursor run
showed v1 is saturated for `claude-sonnet-5-medium`: both conditions already
met the rubric.

### Quality dataset (v2)

Use `tasks/react-best-practices-v2.jsonl` with
`tests/fixtures/calibration/react-review-v1.json`. Eight review-in-prompt
examples share binary dimensions `primary_diagnosis`, `actionable_fix`, and
`grounded_claims`. Bound calibration must cover every task rubric dimension
name. Run at least 3 trials on the same harness pair. This remains a
development experiment until humans review a sample of transcripts.

- expected v2 task SHA-256:
  `9c3a558691d2507ccb332d8f20d25422f52f4ab9602b479c0e2e0c3a498e959b`
- expected react-review calibration fixture SHA-256:
  `5b093a444351abe683dbe3177f0c1ef93f161e02c466a94b9b0376de599cfd06`

The 2026-09-01 same-harness Cursor v2 run
(`.eval-output/react-best-practices-v2-cursor/`) used the previous task SHA
and is retained as provisional evidence. `memo-default-callback` was then
rewritten so the gold label matches React memo semantics: Header's inline
default is passed into a memoized child.

The 2026-09-02 bound independent run
(`.eval-output/react-best-practices-v2-cursor-agy-judge/`) used that SHA,
`claude-sonnet-5-medium` on `cursor-agent`, and `gemini-3.6-flash-high` on
`antigravity` after a 3/3 accepted same-harness calibration of that judge id.
It exited 0 with `quality_status: independent`. Across 24 paired trials,
`quality_outcome` was control 15, tie 4, treatment 2, inconsistent 3.
Per-output binary scores were at ceiling for both conditions
(`primary_diagnosis` and `grounded_claims` 24/24; `actionable_fix` control
23/24, treatment 24/24). Pairwise leftover scoring is not a skill win. This
is not a promotion claim.

For rubric tasks, also pass `--judge-model` with a different exact model
identifier and `--calibration` from an accepted calibrate run on that same
harness pair. The runner judges each condition only after deterministic gates
pass. Color-choice fixtures cannot bind v2. A valid same-harness judgment
is `provisional_non_independent`; a timeout, failed gate, malformed response, or
identity mismatch is `unknown`. A missing trace-reported model is unattested,
not a quality unknown. Omitting `--calibration` is allowed, but a rubric run
then remains quality-incomplete and cannot exit `0`.

The runner invokes the configured harness sequentially in read-only mode,
emitting invocation start, 15-second heartbeats, and finish lines to stderr. Odd trials run control first; even
trials run treatment first. The evaluator injects the exact `SKILL.md` text
itself, so treatment exposure does not depend on model-side discovery. Target,
judge, and calibration invocations share one lifecycle that uses cleaned
OS-temporary workspaces outside the evaluator repository. It retains `run.json`,
the planned configuration, tasks, condition responses, traces, stderr, and a
JSON/Markdown report for every pair.

`runner_valid` means the runner held its declared variables, isolation checks,
and treatment activation. It is not a general quality claim. Read both transcripts before
interpreting `treatment_only`, `both_pass`, `control_only`, or `both_fail`.

JSON and Markdown reports expose evaluator-recorded instruction delivery plus
optional trace telemetry when the harness also reads the installed skill, rolled-up timing and token usage,
calibration (`not_run`, or `accepted` plus `fixtures_sha256` when a bound
calibration is supplied), every judged dimension, `quality_status`, and
`quality_outcome`. Deterministic-only reports say semantic quality was not
judged. An overall pairwise winner is not a quality pass when any dimension is
unknown or favors the opposing condition. A tied dimension is compatible with
an otherwise coherent winner.

Live exit status is `0` when quality evidence is complete, which for rubric
runs requires a bound accepted calibration, `1` when the runner is valid but
quality is unknown or was not judged, and `2` when the runner is invalid.

Calibrate the pairwise judge against versioned human-labeled
`known-better`, `known-worse`, and `tie` cases before a live quality pilot:

```bash
python3 skills/skill-eval-loop/scripts/skill_eval_loop.py calibrate \
  --fixtures /absolute/path/to/calibration/v1.json \
  --output "$PWD/.eval-output/fresh-calibration" \
  --harness pi \
  --model exact-model-id \
  --judge-model exact-judge-model-id \
  --dry-run
```

`calibrate` exits `0` when agreements meet the locked threshold, `1` when the
runner is valid but the judge disagrees, and `2` when a judgment is invalid.

## Complete a promotion review

A business-ready promotion result requires more than `run --promotion`. Use the
[promotion evidence workflow](skills/skill-eval-loop/references/promotion-workflow.md)
to create a blinded packet, collect two independent human label files and the
custodian's holdout
attestation, then run `finalize-review`. The final report measures human and
automated-judge agreement, outcomes across trials, regressions, usage, and
recorded cost while leaving the promotion decision with the accountable human
owner.

## Boundaries

The supported path is: list enumerable model ids, use those ids on the harness
that will call them, keep student and judge on the same harness, calibrate,
dry-run, live-run, and read the retained traces. Deterministic graders can
complete without a judge.

Adapters exist for Codex, Claude Code, Cursor Agent, Muse, Hermes, Pi,
Antigravity, and a custom script. CI proves mechanics with fake Codex and
`script`. Local live dogfood on 2026-09-01 succeeded on cursor-agent, pi,
muse, antigravity, hermes, and codex. Claude is installed but not logged in
on this machine (`claude auth status` reports `loggedIn: false`); the adapter
treats that JSON error as a failed invocation rather than a model answer.
`models` enumerates a harness when that CLI can list ids (Muse catalog,
Cursor Agent `--list-models`, Pi `--list-models`, Antigravity `agy models`).
An empty listing does not reject an id. `--harness script` wraps any other
binary.

`--promotion` plus `prepare-review` / `finalize-review` implement a human
review workflow. They do not prove an independent holdout or complete a
promotion claim. Tasks run in empty temp workspaces, so repository-editing
evals are not quality evidence.

The runner records operator-supplied cost. It does not pick models, discover
providers, price calls, authenticate reviewers, or run in parallel.

Live evaluation is a trusted local-operator workflow. Write `--output` under
the gitignored `.eval-output/` directory or outside the repo. The runner
retains reports, responses, traces, and stderr. Harness homes (`cursor-home`,
`codex-home`, and Cursor `chats/**/store.db`) are deleted after a clean run;
leftovers from interrupted runs are still not git material. The configured
harness executable can read local credentials and therefore must be trusted.
This project does not sandbox hostile executables. Inspect raw runs locally
before sharing any evidence.

## Development

Pull-request and push CI verifies evaluator mechanics with deterministic tests
and fake harnesses. It makes no live model calls, receives no model credentials,
and uploads no evaluation evidence. Authorized operators run live evaluations
locally; humans inspect the retained evidence and own promotion decisions.

Run the Python test suite and package healthcheck:

```bash
python3 -m unittest discover -s tests -v
skills/skill-eval-loop/scripts/healthcheck.sh
```

## License

MIT
