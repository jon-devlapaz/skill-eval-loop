---
name: skill-eval-loop
description: Run a local paired diagnostic in Hermes, Claude Code, Codex, or Pi, headlessly or with Herdr observation, independently authoring missing eval suites and comparing outcomes with and without one explicitly loaded agent skill.
---

# Skill Eval Loop

Run one paired diagnostic: hold prompts, fixtures, model, tools, harness, and
trial count constant while changing only explicit availability of the target
skill. Forced suites activate the skill before the task. Autonomous schema-3
suites leave the task unchanged and score trace-visible skill selection.

## Preconditions

Require:

- a target directory containing `SKILL.md`;
- an existing `evals/evals.json` or the ability to launch a fresh subagent to
  author it;
- references that pass every declared grader;
- an exact model identifier;
- a working executable and authentication for the selected harness;
- a Herdr-managed pane with `HERDR_ENV=1` only for `--observer herdr`.

Audit, dry-run, and the default headless live run do not require Herdr.

Before live trials, calculate and state the exact harness-invocation count:

```text
target invocations = 2 × trials × cases
judge invocations  = Σ(model-rubric graders for each case × (1 + 2 × trials))
total invocations  = target invocations + judge invocations
```

One agent-harness invocation may make multiple provider model calls when tools
are used, so the exact provider-call count and cost are unknown unless the
harness reports them. Wait for explicit authorization of the invocation total
and this uncertainty before starting paid trials.

In the commands below, set `SKILL_EVAL_DIR` to the absolute path of this
installed skill directory:

```bash
SKILL_EVAL_DIR=/absolute/path/to/skill-eval-loop
```

## Workflow

### Conversation contract

Ask exactly one question in each message and wait for the answer before asking
the next one. Never bundle harness, goal, model, judge, trial count, observer,
or authorization into one prompt. If the user already supplied the next choice,
record it and continue without asking it again.

Use this order, skipping questions the user has already answered or that do not
apply:

1. execution harness;
2. evaluation goal;
3. target model;
4. judge model, only when model rubrics require one;
5. observation mode;
6. authorization for the pilot invocation count and unknown provider-call
   total;
7. whether to scale after a valid pilot.

Setup remediation interrupts this sequence. Ask only whether to apply the
stated fix, wait for the answer, then resume at the interrupted step.

1. Choose the execution harness.

Before running any evaluation command, always alert the user to all supported
harnesses:

- `hermes` — Hermes Agent;
- `claude-code` — Claude Code;
- `codex` — OpenAI Codex CLI;
- `pi` — Pi coding agent.

Ask which harness they want unless their request already selects one. Do not
silently default to the harness running this skill. Confirm that harness's
executable and authentication are available, and record the choice with
`--harness` in both dry-run and live commands.

2. Independently author a missing suite.

If `evals/evals.json` is absent and no `--evals-path` was supplied, launch a
fresh-context subagent before the coordinator reads or runs any eval cases. The
subagent must create only `<target-skill>/evals/**` and follow
[the independent authoring protocol](references/eval-authoring.md). Do not fork
the main conversation into it or provide proposed answers, expected failures,
intended fixes, candidate outputs, or prior benchmark results.

Once authoring begins, freeze the target skill implementation for this run. The
coordinator may receive only the subagent's factual handoff and audit summary;
do not open case prompts, graders, fixtures, or references in the main model's
context before trials. If authoring or validation fails, delegate repair to a
new fresh-context subagent or stop. Never let the coordinator co-author the
suite. If fresh subagents are unavailable, report the blocker instead of
writing evals in the main chat.

Complete when: the subagent reports schema version 3, at least three distinct
cases, honest provenance, passing static audit, and no live model calls. If a
suite already exists, leave it unchanged and continue.

3. Audit the suite without calling a model:

```bash
python3 "$SKILL_EVAL_DIR/scripts/audit_suite.py" \
  --skill-path /absolute/path/to/skill
```

Complete when: the audit returns `"valid": true`. If it fails, report the
errors and stop with target trials unstarted; suite repair requires separate
authority.

4. Discover and recommend models without calling one.

Ask whether the goal is a quick diagnostic, a release decision, or portability
across model tiers. Classify the task as `simple`, `standard`, `complex`, or
`portability` using the target skill and the eval author's factual summary;
do not open hidden eval content to make this choice.

```bash
python3 "$SKILL_EVAL_DIR/scripts/recommend_models.py" \
  --skill-path /absolute/path/to/skill \
  --harness selected-harness \
  --task-profile standard
```

The recommender queries authenticated harness-native inventory for Pi, Codex,
and Hermes. Claude Code has no stable non-interactive inventory command, so
ask the user to choose exact ids from its model picker and rerun with
`--models exact-id-1,exact-id-2`. Never hardcode a subscription assumption or
invent availability, price, or quota.

Show the budget, balanced, and quality frontier; disclose tier fallbacks and
unknown cost. For a release claim, prefer the intended deployment model. For a
portability claim, plan separate runs across tiers. Use no judge for fully
deterministic suites. When model rubrics are unavoidable, recommend the
strongest available judge and disclose whether it is the same model as the
target.

Ask the user to confirm the exact target model before placing it in any run
command. If model rubrics require a judge, ask a separate question to confirm
the judge model after the target model is settled. Recommend a one-trial pilot
first; inspect validity, traces, grading, and actual cost before proposing more
trials.

If discovery or setup fails, follow
[the setup remediation protocol](references/setup-remediation.md): explain the
failed check and exact proposed fix, then wait for confirmation before making
any change. Rerun the read-only check afterward.

Complete when: the user confirms exact pinned model ids and understands the
pilot invocation count, tier heuristic, provider-call and cost uncertainty,
and judge limitations.

5. Choose the observation mode, then inspect the run plan.

Before constructing the dry run, always alert the user to both options:

- `headless` (default) records the full evidence without opening a Herdr
  workspace;
- `herdr` mirrors live transcripts into a retained 2x2 workspace and requires
  a Herdr-managed pane with `HERDR_ENV=1`.

Ask which option they want unless their request already selects one. Do not
silently enable Herdr. If they select Herdr, add `--observer herdr` to both the
dry-run and live commands and verify its environment before live trials.

```bash
python3 "$SKILL_EVAL_DIR/scripts/run_skill_eval.py" \
  --skill-path /absolute/path/to/skill \
  --harness selected-harness \
  --model exact-provider/model-id \
  --trials 1 \
  --dry-run
```

The default output must resolve below:

```text
<agent-skills-root>/.eval-runs/<skill-name>/<run-id>/
```

`--output-dir` is an override, but the runner rejects paths inside the active
`skills/` directory.

Complete when: the plan names the requested skill, selected harness, exact
model, trial count, pair count, exact harness-invocation count, counterbalanced
execution order, observer, and an output path outside `skills/`. Dry-run must
not create files, workspaces, or panes.

Present the validated plan as a compact two-column Markdown table rather than
a bullet list. Use rows for harness, target model, judge model when present,
trials per case, cases, paired trials, observation, credential status, target
invocations, judge invocations, and total harness invocations. Bold the total
invocation value. State above the table that the dry run created no provider
model calls or artifacts, and that the live provider-call count and cost are
unknown.

After the table, wait for explicit authorization of the selected observation
mode, pilot invocation count, and provider-call uncertainty before the live
command.

6. Run the paired pilot:

```bash
python3 "$SKILL_EVAL_DIR/scripts/run_skill_eval.py" \
  --skill-path /absolute/path/to/skill \
  --harness selected-harness \
  --model exact-provider/model-id \
  --trials 1
```

Add `--judge-model exact-provider/model-id` only when the suite contains a
`model_rubric` grader. Judge calls use the same selected harness with skills
disabled and that harness's strictest supported tool posture. The run artifact
records whether this is an exact allowlist, a disabled toolset, or only a
sandbox posture. Prefer deterministic graders.

The default runner is headless. Add `--observer herdr` to mirror live
transcripts into one retained workspace named `eval:<skill>:<run-id>`:

```text
coordinator | control
with-skill  | judge-results
```

The observer focuses the workspace once, reuses each condition pane
sequentially, and routes model-rubric calls through the judge-results pane.
Raw harness traces remain the evidence owner in both modes; Herdr is only an
observer.

Counterbalance conditions deterministically:

```text
odd trial:  without-skill → with-skill
even trial: with-skill → without-skill
```

The runner validates references before target trials and retains provenance
and hashes. After every harness invocation, it verifies trace-attested model
identity before starting any downstream invocation. A missing or mismatched
judge identity therefore stops during the first reference judge; without model
rubrics, a missing or mismatched target identity stops after the first target
invocation. For a forced Codex treatment, the runner also requires
trace-visible access to the full structured skill payload before starting
downstream grading.

Complete when: the pilot pair finishes and `run_manifest.json` plus
`benchmark.json` exist. For an invalid run, preserve its evidence, report the
cause, and start a new run only after correcting the cause. For a valid pilot,
report the observed counts, routing evidence, actual cost, and limits; then ask
whether to scale to the user's confirmed trial count in a new run.

With Herdr observation, the workspace remains open after completion, failure,
or cancellation, is renamed with its terminal status, and sends one
notification. On Ctrl-C in either mode, stop the active harness process, preserve
partial artifacts, and require `run_state.json` to report
`"status": "cancelled"` and `"valid": false`.

7. Revalidate an existing run after copying or reviewing it:

```bash
python3 "$SKILL_EVAL_DIR/scripts/aggregate_benchmark.py" \
  --run-dir /absolute/path/to/run
```

Aggregation fails on missing artifacts, hash drift, inconsistent grading,
control exposure, or an installed payload that differs from the evaluated
skill. It reparses hashed runtime-attestation traces instead of trusting cached
routing booleans in the manifest.

Complete when: aggregation succeeds and the regenerated benchmark has
`"valid": true`. Otherwise report the integrity failure and preserve the run.

## Interpret the result

Use `benchmark.json`:

- `valid` and `artifact_valid` report evidence integrity, not causal
  attribution.
- `mechanism_valid` reports whether the selected adapter assigned the sealed
  skill treatment, used the suite's activation mode, and kept the control
  unexposed.
- `runtime_attestation_complete` reports whether the trace independently names
  skill injection or explicit skill access. Some harness traces do not expose
  this lower-layer event.
- `outcome_verdict` is `improved`, `regressed`, or `no_difference`.
- `verdict` is the top-level result and becomes `invalid` or
  `mechanism_unconfirmed` when those boundaries fail.
- `task_success.delta` is the treatment rate minus the control rate.
- `selection_verdict` and `routing.accuracy` score trace-visible access only
  for autonomous schema-3 suites.
- `routing` reports treatment availability, trace-visible injection, explicit
  access, selection errors, and control exposure.
- `operations` reports errors, timeouts, tokens, and cost.

Treat the assigned intervention, runtime attestation, routing decision, and
task outcome as separate evidence.

Report only local paired evidence. Mark causal attribution, statistical
significance, distribution readiness, security approval, and blind-review
independence as unproven. Also report that condition order is counterbalanced
but temporal drift remains possible, and that tool enforcement varies across
harnesses.

Only the independent eval-author subagent should read
[the suite schema](references/eval-suite-schema.md) and
[the authoring protocol](references/eval-authoring.md) before trials. Read
[the setup remediation protocol](references/setup-remediation.md) only after a
failed environment or model-discovery check. Read
[the workspace layout](references/workspace-layout.md) only when inspecting
retained evidence.
