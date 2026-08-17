# Minimum Skill Evaluation Contract

This document pins the contract exercised by the Python evaluator and its
fixtures.

## Question answered

The evaluator answers one conditional question:

> For these tasks, under this harness, model, tool environment, and trial count,
> did access to this exact skill payload improve the measured outcomes?

The measured object is the complete configuration:

```text
skill x tasks x harness x model x tools/environment x graders
```

Changing any component creates a different evaluation.

## Explicit run variables

A run requires:

- an absolute skill directory;
- either an absolute newline-delimited JSON task file or the target-owned
  `evals/tasks.jsonl` file;
- a supported harness and its resolved executable;
- an exact target model identifier;
- a different exact judge model identifier when a task uses a rubric;
- a positive trial count;
- a positive timeout;
- an absolute output directory;
- an exact judge model when any task uses a rubric grader.

The retained configuration also records the harness version, hashes of the
skill and task inputs, adapter-owned tool posture, sequential execution, and
condition order. Reported tokens and cost are recorded when available. Missing
usage or cost stays unknown.

## Input tasks

Each non-empty JSONL line is one task:

```json
{"id":"unsafe-candidate","prompt":"Choose the qualified candidate.","graders":[{"type":"response_not_empty"},{"type":"rubric","dimensions":[{"name":"safety","levels":[{"name":"not_met","description":"Selects an unsafe candidate."},{"name":"met","description":"Rejects unsafe candidates before ranking."}]}]}]}
```

Required fields are:

- `id`: a unique, non-empty, path-safe string;
- `prompt`: a non-empty string;
- `graders`: a non-empty list of supported graders.

There is no schema version and no provenance manifest. The loader validates
fields it consumes. Unknown metadata is retained in the task snapshot and does
not change execution.

The initial grader vocabulary is intentionally small:

- `response_not_empty`: a deterministic response-presence preflight;
- `regex`: the final response must match a pattern;
- `not_regex`: the final response must not match a pattern;
- `file_exists`: a workspace-relative final-state path must exist;
- `json_equal`: a workspace-relative JSON file must equal an expected value;
- `rubric`: an isolated judge assesses named dimensions against their locked,
  descriptive levels.

Deterministic outcome graders are preferred. Rubric graders are reserved for
behavior that cannot be represented fairly as an outcome check. They are
evidence, not ground truth, and require human calibration.

Every rubric task requires a `response_not_empty` preflight. It establishes
only that a response exists; it must not be interpreted as semantic quality.
Each rubric contains a non-empty `dimensions` array. Every dimension has a
unique non-empty name and at least two uniquely named levels with non-empty
descriptions. The dry-run plan retains the validated task snapshot unchanged.

## Task ownership and missing suites

An explicit `--tasks` path is caller-owned. Without that flag, the evaluator
uses `SKILL/evals/tasks.jsonl`. The evaluator never creates this file or
dispatches agents itself.

When a requested target lacks both sources, the coordinator uses a fresh-context
subagent to author only `SKILL/evals/**`. It receives the target path and task
contract, but not coordinator conversation, expected answers, prior outputs, or
reports. It makes no live model calls. The coordinator inspects the resulting
diff, then dry-runs the JSONL before a paired run.

This is a suite-bootstrap mechanism, not proof that tasks represent real use.
Use independently sourced task data, blinded judging, and human calibration for
skill-quality claims.

## Development versus promotion

Repository pull-request and push CI verifies evaluator mechanics with
deterministic tests and fake harnesses only. It makes no live model calls,
receives no model credentials, and publishes no raw evaluation evidence.
Authorized operators run live development and promotion evaluations locally.
Humans inspect the retained evidence and own the promotion decision.

The default `run` role is `development`. Development suites may be visible to
the skill author and optimization loop. They are useful for debugging and
regression detection, but repeated hill-climbing turns them into training data.

`run --promotion` is a stricter execution guardrail. It requires:

- an explicit `--tasks` path controlled outside the target skill;
- at least three trials;
- accepted calibration when the task set contains a rubric.

The retained configuration records `evaluation_role` as `development` or
`promotion`. The flag cannot prove that a task set was independently authored,
kept hidden, representative of real use, or labeled by humans. Those remain
operator evidence requirements. A second model or provider does not replace a
human-labeled holdout.

## Paired execution

Every task trial runs twice:

- `control`: the target skill is unavailable and Codex receives the original
  task prompt;
- `treatment`: the exact hashed skill payload is available and the evaluator
  injects its exact `SKILL.md` text before the original task prompt.

The original task, harness, model, timeout, fixture, and tool posture remain
fixed. The instruction injection is part of the treatment. Runs are sequential.
Condition order alternates by trial to reduce a fixed-order confound. Trials
never retry silently. The CLI emits invocation progress to stderr and stops
before repeating a detected network or transport failure.

Each condition starts in an empty OS-temporary read-only workspace outside the
evaluator repository, preventing ancestor project instructions from entering
the trial. The minimum runner does not seed a repository or fixture tree. Consequently, repository-editing tasks
and claims about executed project tests are not reproducible under this
contract; use self-contained response tasks until a separately justified
workspace-fixture capability exists.

The intervention is evaluator-owned injection of the exact hashed skill's main
instructions. This guarantees treatment exposure without depending on the
model to discover or open `SKILL.md`; the installed payload remains available
for referenced files. Delivery does not prove faithful compliance, so human
transcript review remains required.

The first Codex implementation is deliberately direct. A shared harness
abstraction is not justified until a second real harness demonstrates common
behavior.

## Codex home isolation

The experiment Codex home is not the user's `~/.codex`. A live run temporarily creates
`$output/codex-home` and sets `CODEX_HOME` to that directory for control,
treatment, and judge. Place it under the output directory, not the OS temp
directory: some Codex builds refuse a temp-dir home.

If `~/.codex/auth.json` exists, copy only that file into the run-local home.
Do not copy skills, sessions, or `config.toml`. Copied credentials are
runtime-only and the runner removes the entire run-local home when it exits. The runner
does not intentionally serialize credentials into evidence. Because the
configured executable can read the copied file, the local operator must trust
the harness and inspect raw artifacts before sharing them. Dry-run and
fake-harness runs must not require an authenticated host Codex home.

The treatment skill remains a workspace payload at `.agents/skills/<name>`.
Host `CODEX_HOME/skills` is not the intervention and is not consulted. A
same-name skill in the user's Codex home is not a runner gate once the
experiment uses a run-local home.

This isolation protects the experiment from ambient Codex configuration; it is
not a security sandbox for hostile executables. Strong isolation of untrusted
harnesses requires a separate OS or broker boundary and is outside this
project.

## Dry-run accounting

Dry-run validates consumed inputs and prints the complete plan without creating
artifacts or calling a provider.

For `t` tasks and `n` trials:

```text
paired trials                 = t x n
target invocations            = 2 x t x n
per-output judge invocations  = 2 x n x total rubric graders across all tasks
pairwise judge invocations    = n x total rubric graders across all tasks
judge invocations             = per-output + pairwise
total invocations             = target invocations + judge invocations
```

The golden fixture contains two tasks, three trials, and one rubric grader:

```text
paired trials      = 6
target invocations = 12
judge invocations  = 9
total invocations  = 21
```

Provider calls inside a harness invocation may differ when the harness uses
tools. The evaluator reports harness invocations exactly and does not invent
provider-call or cost accounting.

## Evidence ownership

Each condition retains its response, raw trace, stderr, exit status, duration,
CLI-configured model, any separately trace-reported model identity, and reported
usage. When the harness does not expose resolved identity, the report keeps it
unknown rather than calling the configured value attested. A trace-reported
model mismatch invalidates the runner. Each grade includes the grader, result,
and concrete evidence. A Markdown report links differences and failures to both
condition transcripts.

Raw evidence is authoritative. Reports are derived views intended to help a
human inspect the pair, not replace that inspection.

## Rubric judging

Rubric judging begins only after both condition runs satisfy runner isolation
and execution checks and every deterministic grader passes. A failed gate
produces quality status `unknown` and makes no judge call.

Each qualifying condition is judged separately in a fresh OS-temporary read-only
workspace outside the evaluator repository. Target, judge, and calibration
roles share the same workspace, environment, process, trace, and cleanup lifecycle.
The prompt presents the task, untrusted candidate response, and locked rubric,
but no control or treatment label. For every dimension, the judge must return
concrete response evidence and exactly one declared level. The runner retains
the raw trace, raw response, stderr, timing, usage when reported, requested
model, and trace-reported model.

The judge fails closed to `unknown` for a timeout, process failure, a
mismatched trace-reported identity, malformed dimensions, or an identical
runner and judge model. When the trace does not report a model, the requested
judge model is recorded as unattested CLI configuration rather than a failed
judgment. A structurally valid Codex judgment from a different
OpenAI model is labeled `provisional_non_independent`; model separation within
one provider or family is not independent evaluation.

After both per-output judgments succeed, the runner presents the two responses
as anonymized candidates `A` and `B`. The mapping from those labels to
control and treatment is chosen per trial, kept outside the judge prompt, and
restored only in retained evidence. The pairwise prompt contains neither
`control` nor `treatment` labels. The judge returns per-dimension `A`, `B`,
or `tie` plus an overall winner. Pairwise status is quality evidence; it does
not change runner validity. A failed per-output judgment makes pairwise status
`unknown` and makes no pairwise call.

Human calibration is a separate `calibrate` command. It loads a versioned
fixture with `known-better`, `known-worse`, and `tie` cases, each with a
locked human winner and rationale. The judge sees anonymized candidates `A`
and `B`. The report restores `better`/`other`/`tie`, counts agreements against
`minimum_agreements`, and retains disagreements with the human rationale. The
production assignment includes both `A=better` and `B=better`; a calibration
with only one orientation is not bindable. Same-provider calibration remains
provisional. A live paired pilot should wait until calibration is accepted and
a human reviews disagreements.

A rubric `run` may consume the retained result with
`--calibration /absolute/path/to/calibration.json`. A binding is accepted only
when the calibration is valid and accepted, its runner and judge models match
the run, its retained cases agree with the locked fixture, and the fixture at
its recorded absolute path still has the retained SHA-256 hash. The binding is
checked during planning and again before live execution. `run.json` and every
pair report retain `calibration_status` and `fixtures_sha256`.

For this contract, the operator-controlled `calibration.json` and its original
absolute fixture path are the binding trust root. The runner verifies their
internal consistency but does not authenticate the origin of the raw prompt,
response, trace, or stderr artifacts. Those raw artifacts remain the evidence
for human inspection. Moving the fixture invalidates the binding even when its
content is unchanged.

## Reports and exit status

Pair reports separate runner validity, evaluator-recorded activation, deterministic comparison,
per-output rubric status, pairwise status, quality completeness, quality
outcome, and calibration. `run.json` repeats the rolled-up runner validity and
quality status and rolled-up timing/token usage. Activation is `observed` when
the evaluator injects the hashed treatment instructions. `trace_skill_read`
separately records whether Codex opened the installed main file; it is telemetry,
not a validity gate.
Calibration is `accepted` only when a
validated binding is supplied. Without `--calibration`, a rubric run records
`not_run`, quality remains `unknown`, and the runner cannot exit `0`.

`quality_status` is `not_required` when no rubric is present, `unknown` when
any required judgment is unknown, and `provisional_non_independent` when every
required judgment succeeded. `quality_outcome` lists every dimension through
`dimension_results` and is never a restored winner when a pairwise dimension
favors the condition opposing the overall winner (`inconsistent`) or when
quality is unknown or not judged. A tied dimension is compatible with an
otherwise coherent winner. Deterministic-only Markdown reports state that semantic quality
was not judged.

Process exit status distinguishes those cases:

- `0`: runner valid and quality evidence complete;
- `1`: runner valid, but quality unknown or not judged;
- `2`: runner invalid.

A supplied calibration that is malformed, unaccepted, model-mismatched,
assignment-degenerate, unavailable at its retained fixture path, or hash-
drifted is runner-invalid and exits `2`. Deterministic-only runs do not require
calibration; their semantic quality remains not judged and they exit `1`.

## Runner acceptance versus skill quality

**Runner acceptance** means the evaluator held the declared variables fixed,
isolated control and treatment, retained the evidence, applied the declared
graders, and reported the result honestly.

A **skill-quality claim** requires more: realistic tasks from the intended use
distribution, repeated trials, fair graders, and human review of transcripts.
Improvement, regression, and no difference are all legitimate outcomes of a
valid run.

A promotion claim additionally requires an independently controlled holdout,
human labels for the rubric or preference decisions, and measured agreement
between those labels and any automated judge. A visible development suite must
not be relabeled as a holdout after it has guided changes.

After a quality-complete promotion run, `prepare-review` creates a hash-bound
packet containing only the blinded A/B prompts and empty templates. The
custodian attests task-hash-bound independence, development secrecy, and
coverage of positive, negative, ambiguous, near-tie, and adversarial cases. Two
distinct reviewers label every transcript and dimension independently with
rationale. `finalize-review` verifies those inputs against the retained run,
measures human and automated-judge agreement, restores condition outcomes,
reports variance by task, regressions, usage, and operator-recorded cost, and
retains limitations plus hash-listed copies of the manifest, attestation, and
complete reviewer inputs. See the packaged
[promotion workflow](../skills/skill-eval-loop/references/promotion-workflow.md).

The machine cannot authenticate human identity or custody claims. A complete
review package is evidence for the accountable owner; it is not an automatic
promotion verdict.

A one-task pilot can establish runner acceptance. It cannot establish that a
skill is generally effective. Capability suites should contain enough
realistic, unsaturated tasks to reveal meaningful differences; regression
suites should protect behavior already demonstrated reliably.

## Outside this contract

The minimum reference does not initially include:

- Herdr or another live observer;
- schema compatibility or migration;
- mandatory provenance manifests;
- reference or counter-reference judge calls;
- automatic model discovery or pricing;
- parallel execution;
- multiple judges;
- autonomous skill selection;
- Claude Code, Pi, or Hermes adapters;
- container runners, Harbor, or per-condition Codex homes;
- API-key versus OAuth login menus.

These capabilities can return only after an observed requirement or repeated
failure justifies their complexity.
