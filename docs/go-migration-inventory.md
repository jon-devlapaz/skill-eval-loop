# Go evaluator migration inventory

Frozen Python baseline: `db20c4423f4f5a68b97c06c16ef81f865f700be3`.

This document records the observable contract of the Python evaluator before
any Go package layout is chosen. It is an inventory, not a design. The frozen
Python files remain the behavioral oracle until the migration gates pass.

## Baseline proof

- Branch point: clean `main` at the frozen commit above.
- `uv run --quiet --with pytest python -m pytest`: 140 passed on macOS with
  Python 3.11.15 and pytest 9.1.1.
- `skills/skill-eval-loop/scripts/healthcheck.sh`: compiles every Python module,
  checks three command help surfaces, and runs 127 evaluator `unittest` cases.
- The healthcheck does not cover `recommend_models.py`, release-identity tests,
  packaging without Python, Go, Linux, output bounds, or all descendant cleanup
  cases required by the migration prompt.

## Command contract

All four Python commands use `argparse`. Consequently `-h`/`--help` writes the
generated usage text to stdout and exits 0; missing required arguments, invalid
choices, and invalid `int` values write usage plus an error to stderr and exit
2. None reads stdin.

### `audit_suite.py`

Purpose: validate one suite without starting model trials.

Arguments:

| Argument | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--skill-path PATH` | yes | none | Target skill directory. |
| `--evals-path PATH` | no | `<skill>/evals/evals.json` | Suite JSON. |
| `--output PATH` | no | none | Write the report instead of stdout. |

Success and validation failure both produce two-space-indented UTF-8 JSON with
one terminal newline. Without `--output` it is stdout; with `--output`, stdout
is empty and the parent directory is **not** created. Exit is 0 when
`report.valid` is true and 1 otherwise. Handled validation failures are encoded
as `{valid:false, errors:[code], details:[message]}`; they do not use stderr.
Uncaught write errors propagate with a Python traceback.

The valid report owns: `valid`, `errors`, `schema_version`, `skill_name`,
`suite_type`, `dataset_origin`, `activation_mode`, `case_count`, sorted unique
`routing_classes`, the four-field `grader_discrimination` summary, and
`provenance_case_count`.

### `recommend_models.py`

Purpose: discover only local/authenticated model inventory and recommend a
configuration without a provider call.

Arguments:

| Argument | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--skill-path PATH` | yes | none | Target skill and default suite. |
| `--harness NAME` | yes | none | `hermes`, `claude-code`, `codex`, or `pi`. |
| `--harness-bin PATH` | no | harness name on `PATH` | Executable override. |
| `--task-profile NAME` | yes | none | `simple`, `standard`, `complex`, or `portability`. |
| `--models CSV` | no | empty | Exact user-supplied model IDs; bypasses native discovery. |

The harness is resolved and `<executable> --version` must exit 0 with nonempty
stdout even when `--models` is supplied. Pi discovery runs
`<executable> --list-models` with a 30-second timeout. Codex reads
`$CODEX_HOME/models_cache.json` (default `~/.codex`); Hermes reads
`$HERMES_HOME/provider_models_cache.json` (default `~/.hermes`). Claude Code
requires `--models`.

Success writes two-space-indented JSON plus newline to stdout and exits 0.
Handled `OSError`, `RuntimeError`, `ValueError`, and `SubprocessError` failures
also write JSON to stdout (`{valid:false,error:string}`) and exit 1; stderr is
normally empty. The report includes the exact inventory, heuristic tier and
fallback disclosure, target/judge recommendations, invocation accounting,
harness version, suite counts, confirmation requirement, unknown cost/provider
calls, and limitations. Inventory is deduplicated by exact ID and sorted by
tier then ID. Tier inference tokenizes only the model leaf plus description.

### `run_skill_eval.py`

Purpose: dry-plan or execute paired control/treatment evaluations.

| Argument | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--skill-path PATH` | yes | none | Target skill. |
| `--evals-path PATH` | no | `<skill>/evals/evals.json` | Suite override. |
| `--output-dir PATH` | no | `.eval-runs/<skill>/<timestamp-random-id>` outside `skills/` | Retained run. |
| `--model ID` | yes | none | Exact pinned target model. |
| `--trials INT` | no | 1 | Paired trials per case; must be at least 1. |
| `--harness NAME` | yes | none | One of the four adapters. |
| `--harness-bin PATH` | no | harness name on `PATH` | Executable override. |
| `--pi-bin PATH` | no | none | Pi-only compatibility override. |
| `--timeout-seconds INT` | no | 120 | Target timeout. |
| `--judge-model ID` | no | none | Required when any `model_rubric` exists. |
| `--judge-timeout-seconds INT` | no | 120 | Judge timeout. |
| `--observer NAME` | no | `headless` | `headless` or `herdr`. |
| `--dry-run` | no | false | Validate and print a plan; create no run output and call no model. |

Models reject empty, `auto`, `default`, and IDs containing `latest`
case-insensitively. Planning validates the skill, suite, fixture isolation,
output boundaries, model-rubric judge requirement, observer, and harness
version. Odd trials execute control then treatment; even trials reverse this.
Dry-run writes the two-space-indented plan plus newline to stdout and exits 0.

Execution validates references and counter-references before target trials,
then writes a retained run and aggregates it. Success writes the benchmark JSON
to stdout and exits 0. Handled operational or validation errors write
`ERROR: <message>\n` to stderr, no stdout, and exit 1. Interrupt writes
`ERROR: evaluation cancelled; partial evidence was preserved\n` to stderr and
exits 130. Observer-finalization failures are warnings on stderr and do not
replace the primary result.

Run state transitions are `starting` -> `running` -> `completed` or `invalid`;
exceptions produce `failed`; keyboard interrupt produces `cancelled`. Every
nonterminal and exceptional state has `valid:false` and a completed-condition
count. Partial output remains after failure/cancellation.

### `aggregate_benchmark.py`

Purpose: revalidate a retained run and write a schema-2 benchmark.

| Argument | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--run-dir PATH` | yes | none | Directory containing `run_manifest.json`. |
| `--output PATH` | no | `<run-dir>/benchmark.json` | Report destination. |

Success writes identical two-space-indented JSON plus newline to the output file
and stdout, then exits 0. Handled read, schema, hash, path, and JSON failures
write `ERROR: <message>\n` to stderr, leave stdout empty, and exit 1. Parent
directories for an explicit output are not created.

The report owns schema version 2, verdicts, artifact/mechanism/runtime validity,
grader-discrimination status, unique reason lists, paired task-success counts,
routing metrics, usage/cost coverage, and limitations. Unknown usage remains
`null`; it is not coerced to zero.

### `healthcheck.sh`

No arguments are defined. Bash uses `set -euo pipefail`, resolves the skill
directory from `BASH_SOURCE`, runs `python3 -m py_compile scripts/*.py`, checks
help for audit/run/aggregate (not recommend), then runs verbose unittest
discovery. Output is the underlying compiler/test output; first nonzero command
terminates the script with that status. It requires Bash and `python3`.

### Internal `herdr_runtime.py`

This is an implementation entry point used by the observer. Its mutually
exclusive internal arguments are `--run-job PATH` and `--follow-status PATH`.
It owns Herdr workspace creation, pane commands, retained job/status JSON,
stream forwarding, cancellation, and finish notification. It is not a public
skill command, but compatibility tests exercise it directly.

## Suite input contract

The loader uses Python `json.loads` over UTF-8 text. Invalid UTF-8 fails while
reading; malformed JSON fails during parsing. At the frozen baseline duplicate
object keys are accepted with the last value winning, unknown fields are
retained, and JSON numbers follow Python's normal `int`/`float` decoding. Those
are observable baseline facts, not endorsements; conformance fixtures must pin
them before Go behavior is chosen.

Root fields:

- Required in schemas 2 and 3: `schema_version` (exact integer 2 or 3),
  `skill_name` matching the directory, `suite_type` (`capability` or
  `regression`), `dataset_origin`, `tool_profile`, and nonempty `evals`.
- Optional defaults: `activation_mode: forced` and
  `grader_discrimination: none`.
- Schema 3 requires `routing_class` per case and a valid
  `provenance_manifest`; autonomous activation and `case_contrast` require
  schema 3. Schema 3 rejects the obsolete `distribution_policy` field.
- Dataset origins: `author_derived`, `held_out`, `production_regression`.
  Tool profiles: `no_tools`, `read_only`, `read_write`, `coding`.

Each case requires a unique lowercase kebab `id` (1-64 characters), nonempty
`prompt`, `behavior_class` (`positive`, `edge`, `negative`), nonempty `graders`,
and an object `reference`. `expected_skill_loading` defaults to `required`.
`fixture`, `reference.workspace`, `reference.response`, and
`counter_reference` are conditional inputs. Schema-3 routing/loading pairs are
validated exactly as documented in `references/eval-suite-schema.md`.

Grader objects require a unique nonempty `name` and one of:

- `response_contains(value)`; `response_not_contains(value)`;
- `response_regex(pattern)`;
- `markdown_table_column_regex(column, pattern)`;
- `file_exists(path)`; `json_exact(path, expected)`;
- `model_rubric`: schema 2 requires nonempty `rubric`; schema 3 requires a
  nonempty `criteria` list of `{requirement,prompt_quote}`, with each quote
  appearing case-insensitively in the prompt.

Schema-3 provenance is schema version 1 with an exact suite hash and exactly
one unique record per case. Each record binds case ID, dataset origin, unique
source ID, allowed source type, nonempty observation/author metadata, a safe
artifact path and hash, and the canonical case hash. Canonical hashes use
UTF-8 JSON sorted by key with compact separators and unescaped Unicode.

All user paths must be relative, must not contain a `..` component, and must
resolve beneath their owner root. Absolute paths and symlink escapes fail.
Generated skill/run path components accept only
`[A-Za-z0-9][A-Za-z0-9._-]*` and reject `.` and `..`.

## Output and retained evidence contracts

Contractual JSON writers use `json.dumps(value, indent=2) + "\n"`. Python
insertion order determines key order; `ensure_ascii` remains at its default
except for canonical hashing. Floats and Unicode therefore follow Python 3.11
JSON rendering. Files are normal process-umask files; directories use normal
`mkdir`/`TemporaryDirectory` modes.

A successful run may create:

- `run_state.json`;
- `suite_snapshot.json` and optional `provenance_snapshot.json` plus copied
  provenance artifacts;
- `run_manifest.json` and `benchmark.json`;
- per-case/trial/condition workspaces;
- installed treatment-only skill payloads;
- `outputs/trace.jsonl`, `stderr.txt`, `response.md`, judge traces/stderr/usage,
  and `grading.json`;
- isolated Codex/Hermes homes/configuration and optional retained Herdr
  workspace/job/status records.

Every retained trace, attestation trace, response, grading, suite snapshot,
provenance snapshot/artifact, and installed skill payload is revalidated by
path, content hash, structure, identity, or a combination. Aggregation requires
the complete unique case/trial matrix, exact two-condition records, consistent
grading summaries, declared judge allocation, skill-payload identity, target
and judge model attestation, control isolation, and counter-reference evidence.

Dry-run may resolve a harness and read suite/model metadata but must not create
the default run directory or invoke a provider model. Recommendation may call
only `--version`, local model-list commands, or authenticated cache files.

## Subprocess and environment contract

`resolve_harness` searches `PATH`, then runs `<resolved> --version`. All target
and judge adapters inherit the evaluator environment before applying overrides.

- Pi: exact CLI tool allowlist (or `--no-tools`), JSON print mode, no session,
  skills/extensions/templates/context disabled, optional isolated `--skill`.
- Claude Code: stream JSON, verbose, no session persistence, strict MCP config,
  explicit tool list, project-only settings for targets, safe/no-tools judge.
- Codex: isolated `HOME` and `CODEX_HOME`, with the source Codex `auth.json`
  symlinked read-only-by-convention into the isolated home when present; JSON
  exec, ignored user config/rules, explicit sandbox and model.
- Hermes: generated `HERMES_CONFIG`, explicit toolset posture, usage file, and
  `HERMES_IGNORE_RULES=1` / `HERMES_IGNORE_USER_CONFIG=1` for judges.

Environment inputs read directly are `PATH`, `HOME`, `CODEX_HOME`,
`HERMES_HOME`, and `HERDR_ENV`. Herdr job serialization permits only `HOME`,
`CODEX_HOME`, and `HERMES_CONFIG` overrides. Harness children otherwise inherit
all environment variables; the conformance harness must select and record the
relevant subset without exposing credential values.

Headless subprocesses start a new POSIX session, capture text stdout/stderr in
memory, and wait with a timeout. On timeout or keyboard interrupt the evaluator
sends SIGTERM to the process group, waits one second, then SIGKILLs the group.
Timeout produces return code 124 and appends a diagnostic newline to stderr;
interrupt produces synthetic code 130 and appends `Interrupted by user.`.
Herdr has a separate group-owned supervision path and handles SIGINT/SIGTERM.

## Platform assumptions

- Current process control requires POSIX process groups, `start_new_session`,
  `os.killpg`, SIGTERM, and SIGKILL. Windows is not supported by this baseline.
- Paths and symlink semantics are tested on macOS; intended release scope is
  macOS and Linux.
- Bash, executable mode bits, `PATH` lookup, UTF-8 files, and atomic-enough
  single-process file writes are assumed.
- Codex persisted attestation assumes session JSONL under
  `$CODEX_HOME/sessions`; model caches and Hermes caches have fixed local
  filenames.
- Herdr observation assumes the `herdr` CLI and `HERDR_ENV=1`.

## Dependency graph

```text
audit_suite -> eval_spec
recommend_models -> eval_spec, runtime_adapters
run_skill_eval -> aggregate_benchmark, eval_runtime, eval_spec, model_grader,
                  runtime_adapters, runtime_attestation, workspace_paths
aggregate_benchmark -> eval_spec, runtime_adapters, runtime_attestation
model_grader -> process_control, runtime_adapters, runtime_attestation
eval_runtime -> process_control, herdr_runtime
runtime_attestation -> runtime_adapters.model_matches
healthcheck -> audit_suite, run_skill_eval, aggregate_benchmark, unittest suite
```

Runtime dependencies are Python 3 standard-library modules only. Repository
test orchestration additionally uses pytest/uv, Bash, and fake executables.

## Nondeterministic fields

The differential harness may normalize only fields proven nondeterministic:

- generated run IDs: UTC timestamp with microseconds plus 3 random bytes;
- `started_at` UTC timestamps and rounded monotonic `duration_seconds`;
- `tempfile` roots and platform-specific temporary prefixes;
- absolute checkout/output paths when fixtures intentionally relocate roots;
- OS-assigned PIDs/process timing and signal race timing;
- harness/provider session IDs, reported usage/cost, and authenticated cache
  `fetched_at` text;
- Herdr workspace IDs and timestamps.

Normalization must not erase errors, argv, artifact structure, hashes, routing,
grading, model identity, attestation, safety decisions, or provider-call counts.

## Existing-test behavior map

The exact per-test map is checked in beside this inventory as
[`go-migration-test-map.md`](go-migration-test-map.md).

Every evaluator test in `test_skill_eval_loop.py` is mapped by its owning class:

- `WorkflowContractTests` (4): SKILL workflow rules for fresh-context suite
  authoring, confirmation, one-question interaction, and evidence matrix links.
- `ModelRecommendationTests` (9): exact inventory parsing/tiering,
  recommendations/fallbacks, and exact invocation accounting validation.
- `SuiteAuditTests` (7): valid provenance and actionable audit codes for
  missing/malformed/non-discriminating contrasts and provenance tampering.
- `SuiteValidationTests` (2): unique grader names and schema-2 rubric rules.
- `ObsoletePolicyTests` (1): schema-3 rejection of `distribution_policy`.
- `CounterReferenceTests` (9): shape, compatibility, response sensitivity,
  retained judge evidence, and runtime good/bad discrimination.
- `TargetAttestationOwnerTests` (12): shared target/judge model reason order,
  Codex rollout ownership, exact provider/model identity, and forced access.
- `RuntimeTests` (21): four adapters, isolation, commands/tool posture, payload
  hashes/modes/symlink refusal, trace parsing, Codex persisted attestation,
  injection/access distinction, and conflicting model evidence.
- `ProcessControlTests` (3): timeout group termination, interrupt group
  termination, and partial output preservation.
- `PlanningTests` (15): defaults, external output and fixture safety, reference
  fail-fast, observer environment/layout/job behavior, cancellation and finish.
- `EndToEndTests` (11): fake runs across four harnesses, paired artifacts,
  counterbalancing/accounting, shared judge display, Codex attestation hashes,
  forced access and early stop on target/judge mismatch.
- `AggregateTests` (29): complete accounting, contrast snapshots, usage
  coverage, verdicts, routing/mechanism claims, trace reparse, hash drift,
  complete matrices, record identity, grading consistency, and control exposure.
- `EvaluatorMutationTests` (3): sealed-run tampering and deterministic grader
  mutation rejection.
- `ModelGraderTests` (1): fenced JSON grade parsing.

Every release-identity test in `tests/test_verify_release_identity.py` is also
in the baseline regression gate (13): exact receipt/revision/source binding,
canonical source validation, payload bytes and executable modes, empty
directories, receipt path, installed/git symlink refusal, and separation of Git
identity from uncommitted worktree drift. These tests do not exercise evaluator
behavior directly but must remain green throughout packaging changes.

The exact test names and their category-level proof above are mechanically
recoverable with:

```sh
rg '^    def test_|^def test_' \
  skills/skill-eval-loop/tests/test_skill_eval_loop.py \
  tests/test_verify_release_identity.py
```

## Explicitly uncovered or under-specified behavior

These are not license to change behavior; each needs a black-box scenario and,
where Python itself is ambiguous or unsafe, an explicitly approved decision.

- No current test pins duplicate JSON keys, unknown fields, invalid UTF-8,
  non-finite numbers, numeric coercion, exact Unicode escaping, or all JSON key
  orders. The source facts above are the provisional oracle.
- Tests do not snapshot raw help/error bytes, argparse validation order, stdin,
  cwd, selected inherited environment, complete filesystem modes, or every
  symlink target.
- Output capture is unbounded. There is no output-overflow behavior to preserve.
- Timeout tests prove a direct process-group child is terminated, but not a
  delayed descendant side effect, a descendant retaining stdout/stderr pipes,
  cancellation during spawn, reaping after parent exit, or SIGTERM resistance
  across all adapters.
- `exec.CommandContext` has no Python equivalent here; Go must establish its own
  group ownership and bounded cancellation proof.
- Linux behavior is intended but not exercised in the current local suite.
- The Python writer does not use atomic replacement or fsync; crash consistency
  is unspecified.
- Recommendation tier quality is explicitly heuristic and unbenchmarked.
- No benchmark currently demonstrates a Go distribution/performance benefit.
- Existing tests call fake harnesses and make no real provider calls, but some
  test names say "paid call" as an accounting boundary rather than performing
  one.
- Compatibility callers outside this repository have not yet been searched;
  Python script removal and launcher removal therefore remain gated.
- "Externally demonstrated" parity needs a concrete CI/package environment and
  scenario count after the conformance matrix is enumerated.
