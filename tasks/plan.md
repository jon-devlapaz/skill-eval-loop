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
- Treat availability of the exact hashed skill payload as the intervention.
  Activation telemetry is optional diagnostic evidence, not a quality score or
  a gate on the outcome comparison.
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
claim. The intervention is access to the exact hashed skill payload. Activation
telemetry can diagnose how Codex used that access, but is not required for an
outcome comparison and must not become a path-based quality metric.

**Acceptance criteria:**
- [x] Control absence, treatment presence, and treatment/source hash equality
  define the isolated intervention.
- [x] Missing activation telemetry does not invalidate an outcome comparison.
- [x] Claims are limited to the measured effect of skill access under the
  retained configuration.

**Verification:**
- [x] A controlled fixture asserts control absence, treatment presence, and
  treatment/source hash equality.
- [x] Manual check: Codex CLI 0.147.0 treatment trace exposes no activation
  event; this remains diagnostic rather than a gate.
- [x] Human review accepted the outcome-based evidence definition.

**Dependencies:** None.

**Files touched:**
- `tests/test_skill_eval_loop.py`
- `skills/skill-eval-loop/SKILL.md`
- `docs/minimum-eval-contract.md`

**Result:** Resolved without activation machinery.

### Checkpoint: Evidence contract

- [x] Tasks 1 and 2 are complete.
- [x] Deterministic validity, exact skill availability, and quality evidence
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
- [ ] Manual check: inspect a retained live judge trace with the selected
  provider after separate authorization.

**Dependencies:** Tasks 1 and 2; human approval of the provisional pairing.

**Result:** Implementation complete with fake-adapter evidence. Live provider
verification remains part of the later authorized pilot.

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
- [ ] Manual check: one authorized live exec with only copied `auth.json`
  remains deferred to the later pilot.

**Dependencies:** Task 3.

**Result:** Implementation complete with fake-adapter evidence. Live provider
verification remains part of the later authorized pilot.

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
- [ ] Manual check: compare the retained blind prompt, raw judgment, and
  restored report.

**Dependencies:** Tasks 1 and 3.

**Result:** Implementation complete with fake-adapter evidence. Live prompt and
restored-report review remains part of the later authorized pilot.

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
  unavailable judge, and activation unknown.
- [ ] Manual check: inspect JSON and Markdown reports for the same pair.

**Dependencies:** Tasks 2, 3, and 4.

**Result:** Implementation complete with fake-adapter evidence. Live report
inspection remains part of the later authorized pilot.

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
reported runner validity, activation unknown, deterministic both_pass, per-
dimension rubric scores, and a blinded pairwise tie. That is not a skill-
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
| Codex exposes no activation telemetry | High | Stop at Task 2 and narrow the claim rather than infer use. |
| No independent judge is available | High | Label OpenAI-only evidence provisional and require human calibration before broader claims. |
| Judge prompt leaks condition labels | High | Build prompt from anonymized candidates and test the raw payload. |
| Rubric is gamed or too vague | High | Lock it before runs and calibrate against human-labeled cases. |
| Pilot is saturated or too small | Medium | Report tie/no-signal and expand only after calibration. |
| Host Codex home leaks extra skills | High | Use a run-local `$output/codex-home` and copy only `auth.json`. |
| Copied `auth.json` is published as evidence | High | Treat it as runtime-only; keep it out of reports and condition artifacts. |

## Open questions

- Which provider or human calibration process will supply independent evidence
  beyond the provisional OpenAI judge?
- What activation evidence can current Codex emit, if any?
- Which human-approved threshold should calibration meet before a pilot result
  is considered quality evidence?

## Phase 2: Karpathy hill climb (next agent)

**Baseline:** branch `python-core-redesign`, no upstream. Tasks 1–6 code and
docs are committed. `python3 -m unittest discover -s tests -v` is green (22
tests). Live artifacts under `.eval-runs/` are gitignored: `calibrate-v1b`
accepted 3/3 (still `provisional_non_independent`); `pilot-v1` was a saturated
toy (“Choose Blue.” / pairwise tie). Codex CLI 0.147.0 traces omit model
identity; missing identity is unattested, not fail-closed.

**Objective:** Make `skill-eval-loop` a CI-gated hill climb on one locked
non-toy skill suite: `run` consumes an accepted `calibrate` fixture hash; live
calibration A/B-flips so `A` is not always the known-better seed; a live paired
`calibrate` then `run` exits `0` with complete quality evidence
(`quality_outcome` never a restored winner when a dimension is unknown or
inconsistent). Same-provider judging stays `provisional_non_independent`. Out
of scope: modularizing the evaluator script, installing GSD/NTT123, and any
quality-winner claim on the toy pilot.

**Do not start by splitting `skills/skill-eval-loop/scripts/skill_eval_loop.py`.**
The bottleneck is eval validity, not file size.

**Stop and ask** if the first target skill is unnamed, if the user wants a
second-provider judge before the CI gate, or if transcripts are still
`human_transcript_review_required`.

### Task 7: Lock a non-toy skill suite

**Description:** Replace the toy Blue prompt with one real skill directory and
a locked JSONL suite that can fail. Do not invent the skill; ask.

**Acceptance criteria:**
- [ ] Named skill path and task file are recorded here and used by later tasks.
- [ ] Tasks are not saturated at baseline (not every row `both_pass` by design).

**Dependencies:** User names the skill. No code until that answer exists.

### Task 8: Bind calibration into live `run`

**Description:** Force A/B assignment flips in live `calibrate` (not only the
fake adapter). Make `run` consume the accepted calibration fixture hash and
refuse a quality-complete exit when calibration is `not_run` or the hash
drifts.

**Acceptance criteria:**
- [ ] Live calibrate seeds are not all mapped `A=better`.
- [ ] `run.json` records the bound fixture hash; mismatch or `not_run` cannot
  exit `0` on a rubric run.

**Verification:**
- [ ] `python3 -m unittest discover -s tests -v`
- [ ] Focused tests cover hash bind, missing calibration, and A/B flip.

**Dependencies:** Task 7 for the live suite; tests can land first.

### Task 9: CI as the product UI

**Description:** Add a CI job that runs unit tests and, when secrets exist, the
locked calibrate-then-run pair. The gate is complete hash-bound quality
evidence, not a skill-quality winner.

**Acceptance criteria:**
- [ ] CI fails on unittest failure or runner-invalid (`exit 2`).
- [ ] Rubric runs without bound accepted calibration cannot look like a quality
  pass.

**Dependencies:** Task 8.

### Task 10: Independent judge or holdout

**Description:** Only after Tasks 8–9. A second provider or a held-out human
set. Same-provider evidence stays provisional until then.

**Dependencies:** Tasks 8 and 9. User approval before adding a provider.
