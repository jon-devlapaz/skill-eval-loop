# Thermo-Nuclear Code Quality Review — 2026-08-31

**Subject:** `skill-eval-loop` — current branch `ed737d9` (merge `business-ready-promotion-review`)
**Scope:** `skills/skill-eval-loop/scripts/skill_eval_loop.py` (3012 lines), `tests/test_skill_eval_loop.py` (2096 lines), `AGENTS.md`, `ZEN.md`, `tasks/plan.md`, `.github/workflows/validate.yml`
**Bar:** ZEN maintainability + thermo-nuclear rules 0–7 (1k-line ceiling, no spaghetti, boring code wins, code-judo)
**Method:** Static inspection + `git diff --stat HEAD~1` / `git log --oneline` / `wc -l` / `grep -c "if \|elif"` / `ruff check` / `python3 -m unittest discover -s tests -v`
**Verdict:** **PASS with severe structural debt** — behavior is correct and CI is honest; architecture has 3× outgrown its container by instruction and must split next.

---

## 0. Evidence Snapshot (reproducible)

| Signal | Value | Command |
|---|---|---|
| Evaluator size | **3012 lines** | `wc -l skills/skill-eval-loop/scripts/skill_eval_loop.py` |
| Test size | **2096 lines** (single file) | `wc -l tests/test_skill_eval_loop.py` |
| Combined | 5108 lines in 2 files | |
| Last diff | **+1294 lines** in `skill_eval_loop.py`, 8 files +1828/-93 | `git diff --stat HEAD~1` |
| Branching | **319 `if`/`elif`** | `grep -n "if \|elif " skill_eval_loop.py \| wc -l` |
| Lint | **1 fixable `F841`** unused `exc` at `skill_eval_loop.py:376` | `ruff check` |
| Tests | **55/55 OK** in ~24s | `python3 -m unittest discover -s tests -v` |
| CI jobs | 3: `validate` + `standalone-package` (4 platforms) + `tink-package` (pinned `tink 1.0.0`, Rust 1.95) | `.github/workflows/validate.yml` |
| Debt markers | 0 `TODO/FIXME/HACK/XXX` | `grep -n TODO skill_eval_loop.py` |

> Re-run: `python3 -m unittest discover -s tests -v && ruff check skills/skill-eval-loop/scripts/skill_eval_loop.py && wc -l skills/skill-eval-loop/scripts/skill_eval_loop.py tests/test_skill_eval_loop.py`

---

## 1. Code-Judo Proposal — Delete Categories of Complexity

**Current:** One file owns CLI + plan building + 7 harness adapters + `HarnessRuntime` (process/workspace/env/token lifecycle) + grading + rubric/pairwise judging + calibration + review packet + report rendering. `dict[str,Any]` everywhere.

**Inevitable shape (same behavior, ~40% fewer branches):**
```
skill_eval_loop.py          # CLI only (~200 lines)
harnesses/
  base.py                   # BaseHarnessAdapter + TraceResult dataclass
  codex.py / claude.py / cursor_agent.py / pi.py / hermes.py / muse.py / antigravity.py / script.py
core/
  tasks.py                  # load_tasks, parse_rubric_dimensions, parse_grader
  grading.py                # grade, grade_one, same_json, workspace_target
  judging.py                # judge_prompt, pairwise_prompt, run_rubric_judge, run_pairwise_judge, judge_conditions
  calibration.py            # load_calibration, _load_calibration_binding, run_calibration_case
  review.py                 # prepare_review, finalize_review, load_reviewer_labels
  report.py                 # write_pair_report, dimension_line, quality rollups
  runtime.py                # HarnessRuntime / CodexRuntime
```

**What disappears:**
- 7 duplicated `parse_trace` result dicts (`response`, `actual_model`, `session_id`, `skill_accessed`, `failure_message`, `input_tokens`, …) at `skill_eval_loop.py:402`, `509`, `591`, `697`, `800` — unified `TraceResult`.
- `mark_provisional` identity wrapper at `skill_eval_loop.py:1662` (pure alias of `mark_judgment_status` at `1647`).
- `BaseHarnessAdapter.is_infrastructure_failure` (420) vs free function `is_infrastructure_failure` (1113) duplication.
- Per-adapter `prepare_environment` copy-paste; `CodexAdapter:440` is the only one with real semantics.

---

## 2. Findings (severity → file:line → remedy)

### BLOCKER — File Sprawl Past 1k Ceiling (Rule 1)

| # | Location | Observation | Remedy |
|---|---|---|---|
| F1 | `skill_eval_loop.py:1` — 3012 lines, 3× ceiling | Grew +1294 in one PR (`git diff --stat HEAD~1`). Rule 1 treats >1k as strong smell. | Next PR must split `harnesses/` first. Waiver no longer justified: `tasks/todo.md` shows Phase 2 hill-climb is check-listed done; `AGENTS.md:8` “Do not start by splitting” has expired. |
| F2 | `tests/test_skill_eval_loop.py:1` — 2096 lines, 55 tests in one class | Single `SkillEvalLoopCliTests` hides domains. | Split to `test_tasks.py` / `test_harnesses.py` / `test_judging.py` / `test_calibration.py` / `test_review.py`. |

### HIGH — Spaghetti / Scattered Conditionals (Rule 2)

| # | Location | Observation | Remedy |
|---|---|---|---|
| F3 | `HarnessRuntime._invoke:1239–1361` | 120+ line method mixing PATH scrubbing (`pyenv`, `homebrew`, `/usr/bin` at 1272), `CODEX_HOME` injection (1230), `SKILL_EVAL_SKILL_NAME` vs `SKILL_EVAL_ROLE` (1288), timeout vs infra failure (1330), model identity (1328). 5 responsibilities, sequential side-effects. | Extract `EnvironmentPolicy` + `ModelIdentityCheck` + `ArtifactLayout`. Adapter owns `env`/`command`/`trace schema`; runtime owns only `subprocess.run` + lay-down. |
| F4 | `grade_one:1151`, `judge_conditions:1728` | `if configuration["judge_model"] == configuration["model"] → same_model` then `elif not runner_is_valid → runner_gate_failed` then `elif deterministic_status != pass → deterministic_gate_failed` — branching where a `JudgeGate` policy object should exist. | Replace with `JudgeGate.evaluate(conditions, isolation) -> GateResult` (typed). |
| F5 | `prepare_review:2445` + `finalize_review:2640` | 500+ lines of manual JSON IO + path math + `relative_to` + hash checks inside free functions. No `ReviewPacket`/`HoldoutAttestation` model. | Introduce `ReviewPacket`, `ReviewerLabels`, `Agreement` dataclasses; IO at boundary only. |

### HIGH — Duplication / Thin Abstractions (Rules 3, 4, 6)

| # | Location | Observation | Remedy |
|---|---|---|---|
| F6 | `mark_provisional:1662` | `def mark_provisional(result): return mark_judgment_status(result)` — identity wrapper, zero clarity. Callers at `1663` only. | Delete; call `mark_judgment_status` directly or inline `provisional_non_independent` assignment. |
| F7 | `BaseHarnessAdapter:357–434` + `CodexAdapter:436`, `ClaudeAdapter:479`, `CursorAgentAdapter:546`, `PiAdapter:628`, `HermesAdapter:646`, `MuseAdapter:669`, `AntigravityAdapter:740`, `ScriptAdapter:763` | 7 adapters duplicate `parse_trace` 7-field dict + `build_command` boilerplate + `prepare_environment` returning `({}, None)` or `({HOME: str(home)}, home)`. `CursorAgentAdapter:550` silently copies `~/.config/cursor/cli-config.json` with `except OSError: pass` — untested host leakage. | `base.py: TraceResult dataclass` + `NoopEnv` vs `IsolatedHome(name)` helpers. `CursorAgent` adapter should require explicit opt-in or be deleted. `ScriptAdapter:763` is the only legitimate distinct `resolve` (no default executable). |
| F8 | `is_infrastructure_failure:420` (method) vs `is_infrastructure_failure:1113` (free fn) | Two copies of same `casefold` + 7-marker check. | Keep one canonical (free fn), have method delegate. |
| F9 | `SUPPORTED_HARNESSES:836` dict is correct, but `get_harness_adapter:848` + `resolve_harness:856` re-resolve string → adapter → executable in two places | Architectural drift — harness identity resolved twice. | Single `HarnessRegistry.resolve(name, executable) -> (Adapter, executable, version)`. |

### MEDIUM — Type / Boundary Cleanliness (Rule 5)

| # | Location | Observation | Remedy |
|---|---|---|---|
| F10 | `load_tasks:130`, `load_calibration:177`, `judge_prompt:1520`, `parse_judge_dimensions:1599` | `dict[str,Any]` throughout; rubric invariant (“needs `response_not_empty` preflight + ≥1 rubric with ≥2 levels”) validated deep inside `parse_rubric_dimensions:69`, not at boundary. Callers re-check `isinstance`. | Typed `Task`, `Rubric`, `Dimension`, `Level`, `CalibrationBinding` dataclasses. `load_tasks` returns `list[Task]` or raises `CalibrationBindingError` once. |
| F11 | `unknown_judgment:1626` hard-codes 7-field `execution` dict; `unknown_pairwise:1643` aliases it | Same shape repeated via dict literal, no contract. | Reuse `TraceResult.unknown(judge_model)` factory. |
| F12 | `pairwise_mapping:1552` uses `random.Random(trial).randrange(2)`; `calibration_mapping:1558` uses `seed % 2` | Two different determinism idioms for same blind-label flip. | One `blind_flip(seed) -> Mapping` function. |

### MEDIUM — Orchestration / Atomicity (Rule 7)

| # | Location | Observation | Remedy |
|---|---|---|---|
| F13 | `run_live:2066` sequential `control → treatment → judge × N → pairwise × N` | Control/treatment per trial are independent but serialized. No correctness need; measurable wall-time cost for `trials > 1`. | After modularization, experiment with `concurrent.futures` for control/treatment pair; keep artifact dirs isolated (already are per `pair_dir/control` vs `pair_dir/treatment`). Additive, not blocking. |
| F14 | `prepare_run_codex_home:1027` + `discard_runtime_home:1041` manual `shutil.rmtree` vs `TemporaryDirectory` in `run_condition:1374` | Two lifecycles for same concern; `prepare_run_codex_home` copies only `auth.json`, but `HarnessRuntime.__init__:1212` re-injects `CODEX_HOME` at 1230. | Unify: `HarnessRuntime` owns one `TemporaryDirectory` for codex-home; `prepare_run_codex_home` becomes `CodexHome.create(output_dir) -> Path` context manager. |

### LOW — Nits That Still Matter

| # | Location | Observation | Remedy |
|---|---|---|---|
| F15 | `skill_eval_loop.py:376` `except (...) as exc:` unused | `F841` flagged by `ruff`. | `except (CalledProcessError, OSError):` — delete binding. |
| F16 | `BaseHarnessAdapter.resolve:373` runs `[resolved, "--version"]` synchronously without timeout | Could hang on broken binary. | Add `timeout=5` or delete version probing (unused beyond display). |
| F17 | `docs/minimum-eval-contract.md` 17k lines vs `docs/thermo-nuclear-review-2026-08-31.md` (this file) — contract is thorough but not linked from `README.md` run section | Discoverability. | Already fixed in `d982330 docs: link promotion review evidence` — verify link stays. |

---

## 3. What’s Good — Do Not Regress

- Deterministic gate precedes judging; `unknown` on malformed/timeout/mismatch; zero judge calls on deterministic failure — `judge_conditions:1728`, validated by 55 tests.
- Blinded pairwise mapping preserved outside prompt (`pairwise_mapping:1552`, `calibration_mapping:1558`).
- Calibration binding hard-fails on hash drift (`_load_calibration_binding:237`, `load_calibration_binding:321`).
- CI honesty: `.github/workflows/validate.yml:12` — `unittest` + `healthcheck.sh` + whitespace + tracked-artifact rejection + `standalone-package` (4 platforms, `env -i PATH=/usr/bin:/bin`) + `tink-package` (pinned `tink 1.0.0`, Rust 1.95). No live calls, no credentials.
- Zero `TODO/FIXME/HACK` — clean working tree beyond this doc.

---

## 4. Validation Checklist for Next Agent

Run these exactly; all must be green before merge:

```bash
python3 -m unittest discover -s tests -v
ruff check skills/skill-eval-loop/scripts/skill_eval_loop.py
ruff check tests/
skills/skill-eval-loop/scripts/healthcheck.sh
wc -l skills/skill-eval-loop/scripts/skill_eval_loop.py  # target: <1000 after split
grep -c "if \|elif " skills/skill-eval-loop/scripts/skill_eval_loop.py  # expect ↓ from 319
git diff --stat HEAD  # no file should grow >1k in one PR
```

**Behavioral invariants to assert (add as tests if splitting):**
- [ ] `control` never sees skill payload; `treatment` hash matches source (`run_condition:1387`).
- [ ] Same-model judge → `unknown` `same_model` (no pass) (`judge_conditions:1742`).
- [ ] Deterministic failure → 0 judge invocations.
- [ ] `provisional_non_independent` vs `independent` labeled correctly (`mark_judgment_status:1647` — same harness vs cross-harness).
- [ ] Calibration binding rejects fixture/hash drift (`run_calibrate:2310`, `load_calibration_binding:321`).
- [ ] `prepare_review` packet is blinded; `finalize_review` measures agreement without exposing mapping prematurely.

---

## 5. Recommended Next 3 PRs (smallest complete changes, in order)

1. **PR A — Extract `harnesses/`** (~400 lines deleted): Move 7 adapters to `harnesses/*.py`, introduce `TraceResult` dataclass, `NoopEnv`/`IsolatedHome` helpers, delete `mark_provisional` wrapper, fix `F841`. Tests unchanged.
2. **PR B — Extract `core/judging.py`**: Unify `unknown_judgment`/`unknown_pairwise`, `is_infrastructure_failure`, `JudgeGate` policy; collapse `if blocked_reason` chain in `judge_conditions:1748`.
3. **PR C — Typed boundaries**: `Task`/`Rubric`/`CalibrationBinding` dataclasses at `load_tasks`/`load_calibration`; `prepare_review`/`finalize_review` → `ReviewPacket` model; split `tests/test_skill_eval_loop.py` by domain.

Each PR: `python3 -m unittest discover -s tests -v` green, `ruff` clean, file stays <1k delta.

---

## 6. Reviewer Sign-off

| Reviewer | Date | Result | Notes |
|---|---|---|---|
| (agent 1) | 2026-08-31 | — | Authored findings |
| (agent 2) |  |  | Validate checklist above, confirm metrics, challenge judo proposal |
| Human |  |  | Approve split sequencing; lift `AGENTS.md:8` “Do not split” constraint |

---

## Appendix — Raw Counts

```
git log --oneline -10:
ed737d9 Merge pull request #3 from jon-devlapaz/codex/business-ready-promotion-review
d982330 docs: link promotion review evidence
cc43c96 feat: report trial outcome variance
ca5b9df fix: retain complete promotion review inputs
a355612 docs: define the promotion evidence handoff
a387521 feat: finalize human-grounded promotion evidence
0c7165d feat: prepare blinded promotion reviews
57bcba0 Merge pull request #2 from jon-devlapaz/codex/phase-2-evaluator-hardening
f425a7e docs: define promotion evidence workflow
b32889e test: add public React skill benchmark

git diff --stat HEAD~1:
 README.md                                         |   19 +-
 docs/minimum-eval-contract.md                     |   18 +-
 skills/skill-eval-loop/SKILL.md                   |   14 +-
 .../references/promotion-workflow.md              |  106 ++
 skills/skill-eval-loop/scripts/skill_eval_loop.py | 1294 ++++++++++++++++++--
 tasks/plan.md                                     |   30 +-
 tasks/todo.md                                     |    8 +-
 tests/test_skill_eval_loop.py                     |  432 ++++++-
 8 files changed, 1828 insertions(+), 93 deletions(-)

grep inventory (abbrev):
  def / class  count: 48 definitions in skill_eval_loop.py
  if / elif   count: 319
```

*Generated for independent validation. File: `docs/thermo-nuclear-review-2026-08-31.md`.*
