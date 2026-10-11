# Local evaluation artifacts

This directory is gitignored except for this README.

Put live `--output` and `--output` calibration directories here:

```text
.eval-output/<run-name>/
```

Or use any path outside the repository. Do not commit run directories.

## What to keep and inspect

Retained evidence for a paired run:

- `run.json`, `config.json`, `tasks.jsonl`
- `task-<id>/trial-NNN/report.json` and `report.md`
- `control/` and `treatment/`: `response.md`, `trace.jsonl`, `stderr.txt`
- judge artifacts under `judge-NNN/` and `pairwise-NNN/` when a rubric ran

Those files are the evaluation record. Read them locally. Share only after
review; they can contain prompts, model output, and harness metadata.

## What is not evidence

Harness runtime homes are copied or created for the process, then deleted
when the run finishes cleanly:

- `cursor-home/` (includes `chats/**/store.db`)
- `codex-home/` (may include a copied `auth.json` during the run)
- `claude-home/`, `hermes-home/`, `agy-home/`
- per-invocation `home/` directories

If those directories are still here, the run was interrupted or predates
cleanup. Do not commit them. SQLite chat databases are especially noisy in
`git status` and are not part of the comparison report.
