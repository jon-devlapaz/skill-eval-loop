# Go evaluator migration report

Frozen Python baseline: `db20c4423f4f5a68b97c06c16ef81f865f700be3`.

The installed evaluator is now a self-contained Go binary selected by a thin
POSIX launcher for Darwin/Linux on amd64/arm64. Legacy script names remain as
shell launchers and contain no evaluator logic.

## Preserved surface

- Commands: `audit`, `recommend-models`, `run`, `aggregate`, `healthcheck`.
- Suite schemas 2 and 3, including provenance, grader contrast, autonomous
  routing, model rubrics, and counter-references.
- Pi, Claude Code, Codex, and Hermes target and judge adapters.
- Retained run manifests, snapshots, grading, benchmark, raw traces, response
  hashes, model/runtime attestation, and operation accounting.
- Headless execution and retained Herdr observation.

The migration conformance suite was run against the frozen Python oracle before
its removal. The retained-Python aggregate fixture remains checked in so Go can
continue proving independent evidence revalidation.

## Benchmark

Measured on Apple arm64 macOS with warm filesystem caches. Each command was
executed serially 100 times against the same deterministic schema-2 audit
fixture or retained paired-run fixture. Wall-clock totals:

| Operation | Frozen Python | Packaged Go |
|---|---:|---:|
| Audit, 100 executions | 4.85 s | 0.54 s |
| Aggregate, 100 executions | 6.06 s | 0.45 s |

This is a local startup/validation microbenchmark, not a claim about provider
latency or Go generally. Performance was not a release gate.

Packaged binary sizes are 3.0–3.2 MiB each; the four-platform payload is about
12.6 MiB.

## Limits

- Supported packaged platforms are Darwin and Linux on amd64 and arm64.
- Harness behavior is constrained by each real CLI's stable machine-readable
  output and available tool controls; conformance uses deterministic fakes and
  makes no provider calls.
- Herdr remains an optional observer. Raw harness traces remain the evidence
  owner.
- No intentional behavioral changes were approved or introduced.
