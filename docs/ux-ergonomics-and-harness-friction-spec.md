# RFC: Evaluator UX Ergonomics & Multi-Harness Operational Polish

**Status**: Implemented (2026-09-01)
**Date**: 2026-08-31
**Author**: Antigravity Agent
**Context**: Post-Phase 2 dogfooding findings against live agent CLIs (`cursor-agent`, `muse`, `codex`)

Implemented against the 2026-09-01 harness-agnostic runner. Deviations from the
original draft:

- `--output` is allowed to not exist yet. The draft's `must_exist=True` for every
  path would reject a fresh output directory.
- Timeout sends `SIGTERM` then `SIGKILL` to the harness process, not a process
  group. Current adapters do not daemonize children that need group kill.
- Credential isolation is adapter-owned for every harness, not a Codex special
  case. Temp workspaces already used `ignore_cleanup_errors=True`.

---

## 1. Executive Summary & Problem Statement

During live dogfooding of `skill-eval-loop` with real autonomous coding agents (`cursor-agent` using `claude-sonnet-5-medium` and `gpt-5.6-sol-medium` judge), five primary operational friction points were identified:

1. **Path Pedantry**: The CLI rejected relative paths (`--skill .agents/skills/karpathy-guidelines`) with `ERROR: skill path must be absolute`, violating standard CLI ergonomics.
2. **Binary Discovery Blindspots**: Harnesses installed in standard user binary paths (such as `~/.local/bin/cursor-agent` or `~/.local/bin/muse`) were not detected unless passed via explicit `--harness-bin <abs-path>`.
3. **Output Directory Collisions**: Re-running iterative evaluations or calibrations aborted with `ERROR: output directory already exists`, forcing repetitive manual `rm -rf` cleanup.
4. **Agent Execution Latency Under-estimation**: The default `120s` timeout expired on complex agent tasks where autonomous coding agents inspect repositories, execute sub-commands, and run test suites.
5. **Opaque Multi-Turn Progress**: Running long agent trials (60–180s) provided zero streaming feedback between `PROGRESS: starting target ...` and completion, preventing operators from distinguishing active tool use from deadlocks.

This RFC defines the formal specification to eliminate these friction points while preserving strict deterministic reproducibility and host credential isolation.

---

## 2. Detailed Technical Specification

### Seam 1: Intelligent Path Normalization

#### Requirement:
All path arguments (`--skill`, `--tasks`, `--output`, `--fixtures`, `--calibration`, `--harness-bin`, `--judge-harness-bin`) MUST accept relative paths, tilde expansions (`~/...`), and symlinks, resolving them deterministically to canonical absolute paths prior to validation and snapshot recording.

#### Implementation:
```python
def normalize_user_path(path_str: str | Path | None, label: str, must_exist: bool = True) -> Path:
    if path_str is None:
        raise ValueError(f"{label} is required")
    expanded = Path(path_str).expanduser()
    resolved = expanded.resolve()
    if must_exist and not resolved.exists():
        raise ValueError(f"{label} path does not exist: {path_str}")
    return resolved
```

#### Behavior & Verification:
- `--skill ./skills/my-skill` resolves to `$(pwd)/skills/my-skill`.
- `run.json` and `calibration.json` continue to record the fully resolved canonical path for immutable provenance.

---

### Seam 2: Standard Harness Binary Auto-Discovery

#### Requirement:
When `--harness <name>` is provided without an explicit `--harness-bin`, the evaluator MUST probe standard user and system binary directories before raising a missing executable error.

#### Discovery Search Order:
1. Explicit `--harness-bin` argument (if supplied).
2. Ambient `shutil.which(name)`.
3. Candidate search directories in priority order:
   - `~/.local/bin/<name>`
   - `~/.cargo/bin/<name>`
   - `~/.bun/bin/<name>`
   - `~/.npm-global/bin/<name>`
   - `/opt/homebrew/bin/<name>`
   - `/opt/homebrew/sbin/<name>`
   - `/usr/local/bin/<name>`
   - `/usr/bin/<name>`
   - `/bin/<name>`

#### Code Contract:
```python
def discover_executable(executable_name: str) -> str:
    # 1. Standard PATH lookup
    found = shutil.which(executable_name)
    if found:
        return found

    # 2. Known standard user/system bin directories
    candidates = [
        Path.home() / ".local" / "bin",
        Path.home() / ".cargo" / "bin",
        Path.home() / ".bun" / "bin",
        Path.home() / ".npm-global" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/opt/homebrew/sbin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
    ]
    for directory in candidates:
        target = directory / executable_name
        if target.is_file() and os.access(target, os.X_OK):
            return str(target)

    raise ValueError(
        f"executable {executable_name!r} not found in PATH or standard binary locations (~/.local/bin, /opt/homebrew/bin, etc.). "
        f"Please supply --harness-bin /path/to/{executable_name}"
    )
```

---

### Seam 3: Idempotent Output & Overwrite Control

#### Requirement:
To accommodate rapid development cycles without sacrificing safety against accidental overwrites:
1. Running into an existing output directory MUST default to exiting with code 1 and a helpful error message suggesting `--force`.
2. Supplying `--force` / `-f` MUST safely purge and recreate the target output directory before execution begins.

#### CLI Contract:
```bash
# Refuses to clobber existing directory:
python3 skill_eval_loop.py run --skill ... --output ./out
# ERROR: output directory already exists: /path/to/out. Use --force to overwrite.

# Safely recreates and proceeds:
python3 skill_eval_loop.py run --skill ... --output ./out --force
```

---

### Seam 4: Realistic Execution Latency Budgets

#### Requirement:
Agent harnesses executing multi-turn tool calling (e.g. running Vitest, creating files, linting) routinely exceed simple prompt-completion timeouts.

#### Standard Timeout Configuration:
- Default `--timeout-seconds`: `300` (5 minutes).
- CLI flag preserves full operator override: `--timeout-seconds <int>`.
- Graceful termination: Send `SIGTERM` to the process group, wait up to 5s, followed by `SIGKILL` on timeout.

---

### Seam 5: Heartbeat & Progress Telemetry

#### Requirement:
Invocations exceeding 15 seconds MUST print periodic heartbeats (e.g. every 15s) indicating active elapsed time and running role (`control`, `treatment`, `judge`, `pairwise`), ensuring the operator has live confirmation that the subprocess is healthy.

#### Terminal Output Format:
```text
PROGRESS: starting target ambiguous-auth-scope control
PROGRESS: [ambiguous-auth-scope control] still running... (elapsed: 15s)
PROGRESS: [ambiguous-auth-scope control] still running... (elapsed: 30s)
PROGRESS: finished target ambiguous-auth-scope control: completed in 41200 ms
```

---

## 3. Security & Invariant Protections

1. **Credential isolation**: Each adapter owns auth copying or ambient login.
   Tokens are not written into public trial reports.
2. **Workspace tempfile lifecycle**: Temporary workspace directories use
   `ignore_cleanup_errors=True` to guarantee robust cleanup even when child
   processes generate locked files or deep `node_modules` trees.
3. **No Network in CI**: All automated unit and regression tests MUST use fake
   mock harnesses without network access or live API dependencies.

---

## 4. Verification Plan

| Capability | Test Scenario | Acceptance Criteria |
|---|---|---|
| Relative Path Resolution | Run with `--skill ../karpathy-guidelines` | Canonical path recorded in `run.json`; command executes without error. |
| Harness Auto-Discovery | Place mock executable in `~/.local/bin/` | `--harness <name>` locates executable without `--harness-bin`. |
| Overwrite Flag | Run twice with `--output <dir>` and `--force` | First run creates directory; second run with `--force` successfully purges and re-evaluates. |
| Timeout Headroom | Execute long task taking 180s | Successfully finishes under 300s default without premature timeout abort. |
| Progress Heartbeat | Task running > 30s | Periodic stderr progress messages emitted every 15s. |
