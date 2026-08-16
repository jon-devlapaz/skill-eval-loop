# skill-eval-loop

`skill-eval-loop` is a self-contained Python 3 Agent Skill that measures
whether explicitly applying one local skill changes task outcomes. The control
receives the original task. The treatment receives the exact hashed skill's
`SKILL.md` instructions in its prompt, with the installed payload available for
referenced files. The runner retains the raw evidence and a comparison report.

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
development evidence, not a secret client holdout.

For rubric tasks, also pass `--judge-model` with a different exact model
identifier and `--calibration /absolute/path/to/calibration.json` from an
accepted calibrate run. The runner judges each condition only after
deterministic gates pass. A valid same-provider judgment is
`provisional_non_independent`; a timeout, failed gate, malformed response, or
identity mismatch is `unknown`. A missing trace-reported model is unattested,
not a quality unknown. Omitting `--calibration` is allowed, but a rubric run
then remains quality-incomplete and cannot exit `0`.

The runner invokes Codex sequentially in read-only mode, emitting invocation
progress to stderr. Odd trials run control first; even trials run treatment
first. The evaluator injects the exact `SKILL.md` text itself, so treatment
exposure does not depend on model-side discovery. Target, judge, and calibration
invocations share one lifecycle that uses cleaned OS-temporary workspaces outside
the evaluator repository. It retains `run.json`, the
planned configuration, tasks, condition responses, traces, stderr, and a
JSON/Markdown report for every pair.

`runner_valid` means the runner held its declared variables, isolation checks,
and treatment activation. It is not a general quality claim. Read both transcripts before
interpreting `treatment_only`, `both_pass`, `control_only`, or `both_fail`.

JSON and Markdown reports expose evaluator-recorded instruction delivery plus
optional trace telemetry when Codex also reads the installed skill, rolled-up timing and token usage,
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
  --output /absolute/path/to/fresh-calibration \
  --harness codex \
  --harness-bin /absolute/path/to/codex \
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

The minimum runner supports Codex, deterministic graders, a provisional
same-provider rubric judge, blinded pairwise comparison, human-labeled
calibration fixtures, and hash-bound two-reviewer promotion evidence. It records
operator-supplied cost; it does not discover pricing, provide an independent
automated judge, authenticate human identities, run in parallel, discover
providers, or adapt other harnesses.

Live evaluation is a trusted local-operator workflow. The configured harness
and Codex executable can read the run-local Codex credentials and therefore
must be trusted. This project does not sandbox hostile executables. Keep raw
run directories local and inspect them before sharing any evidence.

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
