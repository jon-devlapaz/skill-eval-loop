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

- `id`: a unique, non-empty string;
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

## Paired execution

Every task trial runs twice:

- `control`: the target skill is unavailable;
- `treatment`: the exact hashed skill payload is available.

Prompt, harness, model, timeout, fixture, and tool posture remain fixed. Runs
are sequential. Condition order alternates by trial to reduce a fixed-order
confound. Trials never retry silently.

Each condition starts in an empty read-only workspace. The minimum runner does
not seed a repository or fixture tree. Consequently, repository-editing tasks
and claims about executed project tests are not reproducible under this
contract; use self-contained response tasks until a separately justified
workspace-fixture capability exists.

The intervention is availability of the exact hashed skill payload, not a
required execution path. Trace evidence about skill access is diagnostic when
available. Its absence does not invalidate the paired outcome comparison and
must not be scored as output quality. Results may claim only that access to the
skill changed measured outcomes under the retained configuration, not that the
model definitely read or followed the skill.

The first Codex implementation is deliberately direct. A shared harness
abstraction is not justified until a second real harness demonstrates common
behavior.

## Dry-run accounting

Dry-run validates consumed inputs and prints the complete plan without creating
artifacts or calling a provider.

For `t` tasks and `n` trials:

```text
paired trials      = t x n
target invocations = 2 x t x n
judge invocations  = 2 x n x total rubric graders across all tasks
total invocations  = target invocations + judge invocations
```

The golden fixture contains two tasks, three trials, and one rubric grader:

```text
paired trials      = 6
target invocations = 12
judge invocations  = 6
total invocations  = 18
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

Each qualifying condition is judged separately in a fresh read-only workspace.
The prompt presents the task, untrusted candidate response, and locked rubric,
but no control or treatment label. For every dimension, the judge must return
concrete response evidence and exactly one declared level. The runner retains
the raw trace, raw response, stderr, timing, usage when reported, requested
model, and trace-reported model.

The judge fails closed to `unknown` for a timeout, process failure, missing or
mismatched trace-reported identity, malformed dimensions, or an identical
runner and judge model. A structurally valid Codex judgment from a different
OpenAI model is labeled `provisional_non_independent`; model separation within
one provider or family is not independent evaluation.

This per-output judgment does not yet choose between the paired responses.
Blinded pairwise comparison, position swapping, and human calibration remain
separate requirements before a comparative quality claim.

## Runner acceptance versus skill quality

**Runner acceptance** means the evaluator held the declared variables fixed,
isolated control and treatment, retained the evidence, applied the declared
graders, and reported the result honestly.

A **skill-quality claim** requires more: realistic tasks from the intended use
distribution, repeated trials, fair graders, and human review of transcripts.
Improvement, regression, and no difference are all legitimate outcomes of a
valid run.

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
- blinded pairwise judging and position swapping;
- autonomous skill selection;
- Claude Code, Pi, or Hermes adapters.

These capabilities can return only after an observed requirement or repeated
failure justifies their complexity.
