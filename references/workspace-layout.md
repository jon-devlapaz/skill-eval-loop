# Run workspace

Each run is immutable and lives outside the active `skills/` directory:

```text
.eval-runs/<skill-name>/<run-id>/
  suite_snapshot.json
  provenance_snapshot.json
  provenance/
  reference-judges/<case-id>/
    codex-home/sessions/.../rollout-*.jsonl  # Codex attestation, when used
  eval-<case-id>/
    trial-001/
      without_skill/
        workspace/
        codex-home/sessions/.../rollout-*.jsonl  # Codex attestation, when used
        outputs/
          trace.jsonl
          stderr.txt
          response.md
        grading.json
      with_skill/
        installed-skill/<skill-name>/
        workspace/
        codex-home/sessions/.../rollout-*.jsonl  # Codex attestation, when used
        outputs/
          trace.jsonl
          stderr.txt
          response.md
        grading.json
  run_manifest.json
  benchmark.json
  run_state.json
```

The default root is `.eval-runs/` beneath the skill installation's agent-skills
root. Use `run_manifest.json` as the evidence index for the paths shown above.
It records the headless or Herdr observer and the counterbalanced condition
schedule. When Codex supplies a persisted rollout for model or skill
attestation, the manifest records and hashes that rollout separately from the
public JSONL transcript.
