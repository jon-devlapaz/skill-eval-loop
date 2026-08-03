# Interpret benchmark.json

Load this after a pilot or scaled run finishes, or when revalidating a copied
run. Report only local paired evidence.

## Fields

- `valid` and `artifact_valid` — evidence integrity, not causal attribution.
- `mechanism_valid` — whether the adapter assigned the sealed skill treatment,
  used the suite's activation mode, and kept the control unexposed.
- `runtime_attestation_complete` — whether the trace independently names skill
  injection or explicit skill access. Some harness traces omit this lower-layer
  event.
- `outcome_verdict` — `improved`, `regressed`, or `no_difference`.
- `verdict` — top-level result; becomes `invalid` or `mechanism_unconfirmed`
  when those boundaries fail.
- `task_success.delta` — treatment rate minus control rate.
- `selection_verdict` and `routing.accuracy` — trace-visible access only, for
  autonomous schema-3 suites.
- `routing` — treatment availability, trace-visible injection, explicit access,
  selection errors, and control exposure.
- `operations` — errors, timeouts, tokens, and cost.

## Separation of claims

Treat assigned intervention, runtime attestation, routing decision, and task
outcome as separate evidence layers.

Leave unproven: causal attribution, statistical significance, distribution
readiness, security approval, and blind-review independence. Condition order is
counterbalanced by the runner, but temporal drift remains possible. Tool
enforcement varies across harnesses — report the harness-specific posture from
the run artifact rather than assuming uniform control.
