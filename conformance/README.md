# Black-box conformance harness

`skill-eval-conformance` runs one JSON scenario against two distinct executable
implementations, retains each raw observation in its report, and fails when the
normalized behavioral snapshots differ.

The frozen Python oracle driver delegates directly to the five existing entry
points. It contains no evaluator logic:

```sh
go run ./cmd/skill-eval-conformance \
  --oracle ./conformance/python-oracle \
  --candidate ./path/to/skill-eval-loop \
  --scenario ./conformance/scenarios/audit-help.json
```

The command fails closed when either implementation is absent, is not
executable, or resolves to the same filesystem object as the other.

## Scenario contract

Each scenario declares:

- a stable `name` and command/subcommand;
- exact trailing `args` and base64 stdin;
- an optional fixture copied into a fresh working directory;
- selected environment assignments and explicitly unset variables;
- an optional timeout in milliseconds.

Each raw snapshot captures the top-level executable and argv, stdin, working
directory, selected/set and unset environment, exit code or terminating signal,
raw stdout/stderr bytes, timeout status, and the complete resulting workspace
tree. Tree records include relative path, type, permission bits, file bytes and
SHA-256, or symlink target.

Fake harnesses append one JSON object per invocation to the path supplied in
`SKILL_EVAL_CONFORMANCE_LOG`. Each record must state executable, argv, cwd,
selected environment, order, exit status, timeout, and signal. The evaluator
does not receive special conformance behavior; only fake provider executables
write this instrumentation log.

## Comparison rules

Raw snapshots are never rewritten. Comparison uses copies with two explicit
transformations:

1. The distinct top-level executable paths are bound to the common semantic
   role `$IMPLEMENTATION`. This is a role binding, not a claim that the paths
   are nondeterministic.
2. The independently allocated temporary root is replaced with `$RUN_ROOT`.
   This is the only initial nondeterministic-field normalization.

No stdout/stderr content, errors, artifact structure or bytes, hashes, routing
claims, grades, model identity, safety result, exit status, signal, or
subprocess accounting is normalized. Additional normalizations require a
fixture proving the field nondeterministic and an update to this document.

The harness itself owns a POSIX process group for each implementation. On
timeout it sends SIGTERM to the group, waits one second, then sends SIGKILL.
Its tests prove a delayed descendant cannot escape and create a side effect.
