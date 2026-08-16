# Implementation Plan: Trustworthy paired skill evaluation

## Overview

Turn the current paired Codex runner from an evidence collector into a quality
evaluator. Retain deterministic runner checks, then add locked semantic
rubrics, an independently judged and blinded comparison, and calibration
evidence. The goal is six verified capabilities: runner validity,
control/treatment baseline, semantic grading, multi-dimensional rubrics,
  blinded pairwise judging, and an independently calibrated judge.

## Architecture decisions

- Keep deterministic checks for task validity, payload hashes, isolation,
  retained artifacts, and process status. Never let a quality judge override a
  failed deterministic gate.
- Keep task quality criteria as data in JSONL. Use machine-checkable graders
  only for machine-checkable outcomes; use a locked rubric for qualitative
  requirements.
- Judge each output against the same rubric, then judge the anonymized pair.
  Preserve structured scores, rationale, raw judge trace, model identity, and
  the mapping from anonymized candidates to control/treatment.
- Use the existing Codex authentication for the first semantic path, label all
  OpenAI-to-OpenAI results provisional and non-independent, and do not claim
  independence until a different provider or human calibration supplies it.
- Treat evaluator-owned injection of the exact hashed skill's `SKILL.md` as the
  intervention. Keep the control task untouched and the installed treatment
  payload available for referenced files.
- Give each live run a private Codex home under `$output/codex-home`. Copy
  only `~/.codex/auth.json` when present. Do not reuse the user's Codex home
  as the experiment environment.

## Dependency graph

```text
Task 1: locked rubric contract ───────────┐
approved provisional OpenAI judge ──────┴── Task 3: judge adapter
                                                   │
Task 3b: run-local Codex home ─────────────────────┤
                                                   ├── Task 4: blinded comparison
                                                   │          │
                                                   │          └── Task 5: reporting
                                                   │                     │
                                                   └─────────────────────┴── Task 6: calibration

Task 2: intervention semantics (resolved; no downstream gate)
```

## Task list

### Phase 1: Define evidence before scoring

## Task 1: Lock the qualitative task contract

**Description:** Extend JSONL task validation so a qualitative task declares
named rubric dimensions, descriptive levels, and a required deterministic
preflight. Preserve existing deterministic graders for structure and exact
outcomes.

**Acceptance criteria:**
- [x] A rubric task with missing dimensions, duplicate names, invalid levels,
  or no deterministic preflight fails validation.
- [x] A valid task preserves rubric criteria in the retained task snapshot.
- [x] Existing deterministic-only tasks retain their current behavior.

**Verification:**
- [x] Tests pass: `python3 -m unittest discover -s tests -v`.
- [x] Focused tests cover valid, invalid, and deterministic-only JSONL tasks.
- [x] Manual check: inspect the frozen task snapshot in a dry-run plan.

**Dependencies:** None.

**Files likely touched:**
- `skills/skill-eval-loop/scripts/skill_eval_loop.py`
- `tests/test_skill_eval_loop.py`
- `skills/skill-eval-loop/SKILL.md`
- `docs/minimum-eval-contract.md`

**Estimated scope:** M (3-4 files).

## Task 2: Confirm intervention semantics

**Description:** Confirm what the paired experiment changes and what it may
claim. The intervention is injection of the exact hashed skill's main
instructions: the control receives the original task and treatment receives
those instructions plus access to the installed payload.

**Acceptance criteria:**
- [x] Control absence, treatment presence, treatment/source hash equality, and
  treatment-only instruction injection define the intervention.
- [x] Evaluator-owned delivery is recorded independently of optional skill-read
  trace telemetry.
- [x] Claims are limited to the measured effect of injected skill instructions
  under the retained configuration.

**Verification:**
- [x] A controlled fixture asserts control absence, treatment presence, and
  treatment/source hash equality.
- [x] A fake-harness regression proves treatment delivery does not depend on a
  model-side file read.
- [x] Human review accepted the outcome-based evidence definition.

**Dependencies:** None.

**Files touched:**
- `tests/test_skill_eval_loop.py`
- `skills/skill-eval-loop/SKILL.md`
- `docs/minimum-eval-contract.md`

**Result:** Treatment instruction delivery and payload isolation are part of
runner validity; model-side reads are retained as optional telemetry.

### Checkpoint: Evidence contract

- [x] Tasks 1 and 2 are complete.
- [x] Deterministic validity, injected skill exposure, and quality evidence
  remain separate concepts.
- [x] Human approves `gpt-5.6-sol` as the provisional judge for
  `gpt-5.6-terra` runs, without an independence claim.

### Phase 2: Judge quality without exposing conditions

## Task 3: Add a provisional Codex judge path

**Description:** Reuse the Codex adapter with the explicitly identified judge.
Invoke it only after deterministic checks pass, record its exact model identity
and raw output, and label same-provider results as non-independent.

**Acceptance criteria:**
- [x] A live rubric task invokes the selected judge once per condition and
  retains structured output plus raw evidence.
- [x] A missing, mismatched, malformed, timed-out, or identical-model judge
  makes quality status `unknown`; it cannot produce a pass.
- [x] A valid different OpenAI model is labeled
  `provisional_non_independent` rather than independent.
- [x] Deterministic failures make zero judge calls.

**Verification:**
- [x] Tests pass: `python3 -m unittest discover -s tests -v`.
- [x] Focused fake-adapter tests cover success, malformed response, timeout,
  identity mismatch, and deterministic short-circuiting.
- [x] Manual check: inspect a retained live judge trace with the selected
  provider after separate authorization.

**Dependencies:** Tasks 1 and 2; human approval of the provisional pairing.

**Result:** Implementation and authorized live-provider verification complete.

**Files likely touched:**
- `skills/skill-eval-loop/scripts/skill_eval_loop.py`
- `tests/test_skill_eval_loop.py`
- `skills/skill-eval-loop/SKILL.md`
- `docs/minimum-eval-contract.md`

**Estimated scope:** M (3-4 files).

## Task 3b: Isolate the live Codex home

**Description:** Stop using the host `~/.codex` as the experiment home. Create
`$output/codex-home` for control, treatment, and judge, copy only
`~/.codex/auth.json` when that file exists, and keep installing the treatment
skill in the trial workspace. Dry-run and fake-harness runs must not require a
host Codex home. Land this before any authorized live Codex run.

**Acceptance criteria:**
- [x] A live run sets subprocess `CODEX_HOME` to `$output/codex-home` and does
  not consult host `CODEX_HOME/skills`.
- [x] The run-local home contains copied `auth.json` only when the host file
  exists; skills, sessions, and `config.toml` are not copied.
- [x] Dry-run and fake live tests pass without a pre-created authenticated
  host Codex home.

**Verification:**
- [x] Tests pass: `python3 -m unittest discover -s tests -v`.
- [x] Focused tests assert the subprocess `CODEX_HOME` path and that fake
  runs no longer need `CODEX_HOME` in the caller environment.
- [x] Manual check: an authorized live exec used the runtime credential and
  removed the entire run-local Codex home afterward.

**Dependencies:** Task 3.

**Result:** Implementation and authorized live-provider verification complete.
Target, judge, and calibration roles now share one `CodexRuntime` process and
cleaned OS-temporary workspace lifecycle, preventing role-specific isolation drift.

**Files likely touched:**
- `skills/skill-eval-loop/scripts/skill_eval_loop.py`
- `tests/test_skill_eval_loop.py`
- `skills/skill-eval-loop/SKILL.md`

**Estimated scope:** S (2-3 files).

## Task 4: Add blinded pairwise comparison

**Description:** Present anonymized candidate outputs to the judge after
per-output rubric scoring. Preserve the randomized candidate mapping outside
the judge prompt, then reveal it only in retained evidence and the final
report.

**Acceptance criteria:**
- [x] The judge input contains neither `control` nor `treatment` labels.
- [x] The judge returns per-dimension scores and `A`, `B`, or `tie` with
  evidence tied to the locked rubric.
- [x] The report restores the mapping and labels pairwise status as quality
  evidence rather than runner validity.

**Verification:**
- [x] Tests pass: `python3 -m unittest discover -s tests -v`.
- [x] Focused tests prove condition labels cannot enter the judge payload.
- [x] Manual check: compare the retained blind prompt, raw judgment, and
  restored report.

**Dependencies:** Tasks 1 and 3.

**Result:** Implementation and authorized live prompt/report review complete.

**Files likely touched:**
- `skills/skill-eval-loop/scripts/skill_eval_loop.py`
- `tests/test_skill_eval_loop.py`
- `docs/minimum-eval-contract.md`

**Estimated scope:** M (3 files).

### Checkpoint: Quality path

- [x] Tasks 3, 3b, and 4 are complete.
- [x] A deterministic failure cannot be judged.
- [x] A valid pair produces anonymous rubric evidence and a restored report.
- [x] Live Codex uses `$output/codex-home`, not the host `~/.codex`.
- [x] Human reviews the first raw judge artifact before more live runs.

### Phase 3: Calibrate and prove the six capabilities together

## Task 5: Make the report and exit status quality-aware

**Description:** Separate runner validity, activation evidence, deterministic
results, per-output rubric results, pairwise judgment, and calibration status
in JSON and Markdown reports. Prevent an aggregate outcome from hiding a
critical failed or unknown dimension.

**Acceptance criteria:**
- [x] Reports expose every dimension and its status; no single aggregate can
  convert an unknown or critical failure into a quality pass.
- [x] Exit status distinguishes invalid runner, valid-but-unknown quality, and
  complete quality evidence.
- [x] Existing deterministic-only reports remain readable and explicitly say
  semantic quality was not judged.

**Verification:**
- [x] Tests pass: `python3 -m unittest discover -s tests -v`.
- [x] Focused report fixtures cover pass, tie, failed critical dimension,
  unavailable judge, and runner-invalid isolation failures.
- [x] Manual check: inspect JSON and Markdown reports for the same pair.

**Dependencies:** Tasks 2, 3, and 4.

**Result:** Implementation and authorized live report inspection complete.

**Files likely touched:**
- `skills/skill-eval-loop/scripts/skill_eval_loop.py`
- `tests/test_skill_eval_loop.py`
- `skills/skill-eval-loop/SKILL.md`
- `README.md`

**Estimated scope:** M (3-4 files).

## Task 6: Calibrate with known outcomes and run one real pilot

**Description:** Create small, versioned calibration fixtures containing known
better, worse, and tied responses. Compare judge output against human labels,
then run one real paired pilot only if calibration accepts the chosen judge.

**Acceptance criteria:**
- [x] Calibration includes a known-better, known-worse, and tie case with
  human rationale.
- [x] The selected judge agrees with the locked labels at the human-approved
  threshold and reports disagreements.
- [x] A real pilot reports all six capabilities separately and avoids a broad
  skill-quality claim from one task.

**Verification:**
- [x] Tests pass: `python3 -m unittest discover -s tests -v`.
- [x] Calibration command produces retained structured evidence.
- [x] Manual check: human reviews calibration disagreements and the pilot
  transcripts before accepting the result.

**Dependencies:** Tasks 1 through 5 and Task 3b.

**Result:** Calibration command and v1 fixtures are complete. Live `gpt-5.6-sol`
calibration against the locked cases accepted 3/3 with no disagreements.
Codex 0.147.0 `exec --json` traces do not report a model; missing identity is
  unattested CLI configuration, not a failed judgment. One paired live pilot
  under the superseded availability-only intervention reported activation
  unknown, deterministic both_pass, per-dimension rubric scores, and a blinded
  pairwise tie. It is retained as historical evidence, not an applied-skill
  quality claim. Judge evidence remains same-provider and non-independent.

**Files likely touched:**
- `tests/fixtures/`
- `tests/test_skill_eval_loop.py`
- `skills/skill-eval-loop/scripts/skill_eval_loop.py`
- `skills/skill-eval-loop/SKILL.md`
- `docs/minimum-eval-contract.md`

**Estimated scope:** M (4-5 files).

### Checkpoint: 6/6 complete

- [x] Runner validity is deterministic and independently reported.
- [x] Control/treatment isolation and activation evidence are reported.
- [x] Quality uses locked semantic rubrics rather than regex proxies.
- [x] Scores are per-dimension and retain raw judge evidence.
- [x] Pairwise judging is blinded and restores labels only after judgment.
- [ ] The judge is independently identified, calibrated, and human-reviewed.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Codex does not open the installed `SKILL.md` | Low | Inject the exact main instructions in treatment; retain file-read events only as telemetry. |
| No independent judge is available | High | Label OpenAI-only evidence provisional and require human calibration before broader claims. |
| Judge prompt leaks condition labels | High | Build prompt from anonymized candidates and test the raw payload. |
| Rubric is gamed or too vague | High | Lock it before runs and calibrate against human-labeled cases. |
| Pilot is saturated or too small | Medium | Report tie/no-signal and expand only after calibration. |
| Host Codex home leaks extra skills | High | Use a run-local `$output/codex-home` and copy only `auth.json`. |
| A hostile harness reads or echoes `auth.json` | High | Live runs are trusted local-operator workflows; hostile-process isolation is a separate system. Keep raw artifacts local and inspect before sharing. |

## Open questions

- Which provider or human calibration process will supply independent evidence
  beyond the provisional OpenAI judge?
- Do referenced multi-file skills require an additional fixture before promotion use?
- Which human-approved threshold should calibration meet before a pilot result
  is considered quality evidence?

## Phase 2: Validate one public skill

**Objective:** Demonstrate the evaluator on one frozen public skill versus the
existing no-skill control. Use an independently authored, externally grounded
benchmark; do not select a weak opponent or change the evaluator into a
multi-skill comparison framework.

**Selected target:** Vercel's `vercel-react-best-practices` skill.

- repository: `https://github.com/vercel-labs/agent-skills.git`
- revision: `b8caa260a420a73042e35521de4b5c8baf6446cc`
- subpath: `skills/react-best-practices`
- evaluator payload SHA-256:
  `5cbdbd8d9acc6913b8f4e0c7151830e88417872421a5975b86fa4b3eba5c36d3`
- declared skill license: MIT

The repository does not vendor the target. An operator fetches the exact
revision into a temporary or controlled source directory, verifies the revision,
then passes the absolute skill subpath to the evaluator. `run.json` binds the
actual payload hash.

### Task 7: Lock a public benchmark

**Description:** Replace the prior skill-specific suite with response-only
React/Next review tasks authored without inspecting the target skill. Ground
task metadata in official React and Next.js documentation.

**Acceptance criteria:**
- [x] The public target identity, immutable revision, subpath, license, and
  evaluator payload hash are recorded.
- [x] `tasks/react-best-practices-v1.jsonl` contains realistic positive,
  negative-control, ambiguous, and false-positive-sensitive cases.
- [x] The suite passes an evaluator dry-run against the frozen target.

The checked-in suite is a public development benchmark, not a secret promotion
holdout. A client promotion claim still requires an independently controlled
task file that was unavailable to the skill-authoring and hill-climbing loop.

**Result:** A fresh-context author created ten response-only tasks using only
official React and Next.js documentation. The suite includes two explicit
negative controls. Dry-run against the exact target revision is valid with
target hash `5cbdbd8d9acc6913b8f4e0c7151830e88417872421a5975b86fa4b3eba5c36d3`,
task hash `621a609cfcdb82756ebe6870a0fad16c6ef12f6186f6c75abb213195b4333c92`,
10 paired trials, 50 planned invocations, zero provider calls, and no artifacts.

### Task 8: Bind calibration into live `run`

**Description:** Force A/B assignment flips in live `calibrate` (not only the
fake adapter). Make `run` consume the accepted calibration fixture hash and
refuse a quality-complete exit when calibration is `not_run` or the hash
drifts.

**Acceptance criteria:**
- [x] Live calibrate seeds are not all mapped `A=better`.
- [x] `run.json` records the bound fixture hash; mismatch or `not_run` cannot
  exit `0` on a rubric run.

**Verification:**
- [x] `python3 -m unittest discover -s tests -v`
- [x] Focused tests cover hash bind, missing calibration, and A/B flip.

**Dependencies:** The calibration implementation is independent of the selected
public target.

**Result:** Production calibration alternates both candidate orientations.
Rubric runs bind a validated accepted calibration and fixture hash; missing
calibration cannot complete quality evidence, and malformed or drifted supplied
bindings are runner-invalid. Unit and fake-harness verification is complete;
no external Codex run was added. The trust root is the operator-controlled
`calibration.json` plus its original absolute fixture path.

### Task 9: CI protects evaluator mechanics

**Description:** Keep CI deterministic and credential-free. CI tests evaluator
mechanics with fake harnesses; authorized operators run live evaluations
locally. Complete hash-bound quality evidence is a local evaluation property,
not a CI or skill-quality claim.

**Acceptance criteria:**
- [x] CI fails on unittest failure or runner-invalid (`exit 2`).
- [x] Rubric runs without bound accepted calibration cannot look like a quality
  pass.

**Dependencies:** Task 8.

**Result:** Pull-request and push CI run the Python tests, healthcheck, packaging
checks, and fake-harness coverage. CI has no live model invocation, model
credential, calibration run, or raw evidence upload. Public benchmark and live
calibration runs remain local, explicit operator actions. Real promotion
evidence remains external and incomplete; no treatment winner is claimed.

### Task 10: Human-labeled holdout and judge validation

**Description:** Separate visible development/regression cases from promotion
evidence. Promotion uses an independently controlled, human-labeled holdout,
repeated trials, and measured agreement between human labels and any automated
judge. A second provider is useful corroboration, not a substitute for the
holdout or human agreement.

**Acceptance criteria:**
- [x] `run --promotion` requires an explicit task path, at least three trials,
  and accepted calibration for rubric tasks.
- [x] The public React suite is classified as development evidence; live runs
  are local and explicitly authorized.
- [ ] An independently controlled holdout covers positive, negative,
  ambiguous, near-tie, and adversarial cases from the intended use
  distribution.
- [ ] At least two humans label the holdout; disagreements and rationales are
  retained.
- [ ] Automated-judge agreement with the retained human labels is measured
  before a promotion claim.
- [ ] A repeated-trial promotion run is transcript-reviewed and reports
  per-dimension outcomes, regressions, variance, usage, and cost.

**Result so far:** The evaluator now distinguishes `development` and
`promotion` roles and rejects underpowered or uncalibrated rubric promotion
runs. No holdout content was invented in this repository: independence and
human labels remain the next evidence gate.

**Dependencies:** Tasks 8 and 9. User approval before adding a provider or
making paid calls.
