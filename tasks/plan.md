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

## Dependency graph

```text
Task 1: locked rubric contract ───────────┐
approved provisional OpenAI judge ──────┴── Task 3: judge adapter
                                                   │
                                                   └── Task 4: blinded comparison
                                                              │
                                                              └── Task 5: reporting
                                                                         │
                                                                         └── Task 6: calibration

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

## Task 4: Add blinded pairwise comparison

**Description:** Present anonymized candidate outputs to the judge after
per-output rubric scoring. Preserve the randomized candidate mapping outside
the judge prompt, then reveal it only in retained evidence and the final
report.

**Acceptance criteria:**
- [ ] The judge input contains neither `control` nor `treatment` labels.
- [ ] The judge returns per-dimension scores and `A`, `B`, or `tie` with
  evidence tied to the locked rubric.
- [ ] The report restores the mapping and labels pairwise status as quality
  evidence rather than runner validity.

**Verification:**
- [ ] Tests pass: `python3 -m unittest discover -s tests -v`.
- [ ] Focused tests prove condition labels cannot enter the judge payload.
- [ ] Manual check: compare the retained blind prompt, raw judgment, and
  restored report.

**Dependencies:** Tasks 1 and 3.

**Files likely touched:**
- `skills/skill-eval-loop/scripts/skill_eval_loop.py`
- `tests/test_skill_eval_loop.py`
- `docs/minimum-eval-contract.md`

**Estimated scope:** M (3 files).

### Checkpoint: Quality path

- [ ] Tasks 3 and 4 are complete.
- [ ] A deterministic failure cannot be judged.
- [ ] A valid pair produces anonymous rubric evidence and a restored report.
- [ ] Human reviews the first raw judge artifact before more live runs.

### Phase 3: Calibrate and prove the six capabilities together

## Task 5: Make the report and exit status quality-aware

**Description:** Separate runner validity, activation evidence, deterministic
results, per-output rubric results, pairwise judgment, and calibration status
in JSON and Markdown reports. Prevent an aggregate outcome from hiding a
critical failed or unknown dimension.

**Acceptance criteria:**
- [ ] Reports expose every dimension and its status; no single aggregate can
  convert an unknown or critical failure into a quality pass.
- [ ] Exit status distinguishes invalid runner, valid-but-unknown quality, and
  complete quality evidence.
- [ ] Existing deterministic-only reports remain readable and explicitly say
  semantic quality was not judged.

**Verification:**
- [ ] Tests pass: `python3 -m unittest discover -s tests -v`.
- [ ] Focused report fixtures cover pass, tie, failed critical dimension,
  unavailable judge, and activation unknown.
- [ ] Manual check: inspect JSON and Markdown reports for the same pair.

**Dependencies:** Tasks 2, 3, and 4.

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
- [ ] Calibration includes a known-better, known-worse, and tie case with
  human rationale.
- [ ] The selected judge agrees with the locked labels at the human-approved
  threshold and reports disagreements.
- [ ] A real pilot reports all six capabilities separately and avoids a broad
  skill-quality claim from one task.

**Verification:**
- [ ] Tests pass: `python3 -m unittest discover -s tests -v`.
- [ ] Calibration command produces retained structured evidence.
- [ ] Manual check: human reviews calibration disagreements and the pilot
  transcripts before accepting the result.

**Dependencies:** Tasks 1 through 5.

**Files likely touched:**
- `tests/fixtures/`
- `tests/test_skill_eval_loop.py`
- `skills/skill-eval-loop/scripts/skill_eval_loop.py`
- `skills/skill-eval-loop/SKILL.md`
- `docs/minimum-eval-contract.md`

**Estimated scope:** M (4-5 files).

### Checkpoint: 6/6 complete

- [ ] Runner validity is deterministic and independently reported.
- [ ] Control/treatment isolation and activation evidence are reported.
- [ ] Quality uses locked semantic rubrics rather than regex proxies.
- [ ] Scores are per-dimension and retain raw judge evidence.
- [ ] Pairwise judging is blinded and restores labels only after judgment.
- [ ] The judge is independently identified, calibrated, and human-reviewed.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Codex exposes no activation telemetry | High | Stop at Task 2 and narrow the claim rather than infer use. |
| No independent judge is available | High | Label OpenAI-only evidence provisional and require human calibration before broader claims. |
| Judge prompt leaks condition labels | High | Build prompt from anonymized candidates and test the raw payload. |
| Rubric is gamed or too vague | High | Lock it before runs and calibrate against human-labeled cases. |
| Pilot is saturated or too small | Medium | Report tie/no-signal and expand only after calibration. |

## Open questions

- Which provider or human calibration process will supply independent evidence
  beyond the provisional OpenAI judge?
- What activation evidence can current Codex emit, if any?
- Which human-approved threshold should calibration meet before a pilot result
  is considered quality evidence?
