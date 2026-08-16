# Independent eval authoring

Use this protocol only when `TARGET/evals/tasks.jsonl` is absent and the user
has asked to evaluate `TARGET`.

Launch a fresh-context subagent. Do not fork the coordinator conversation. Give
it only the target's absolute path and this prompt:

```text
Create an initial evaluation suite for the Agent Skill at TARGET.

Inspect only TARGET. Do not use parent conversation, proposed answers, prior
evaluation reports, or model outputs. Write only TARGET/evals/tasks.jsonl.

Create at least three realistic, distinct JSONL tasks. Each task needs a unique
path-safe id, a non-empty user prompt, and non-empty graders. Prefer observable
outcomes. For a qualitative task, include `{"type":"response_not_empty"}`
and one rubric with named dimensions. Each dimension has at least two named
levels, each with a non-empty description. Do not ask the model to repeat the
skill, reveal an expected answer in the prompt, or use regex as a proxy for
qualitative quality.

Make no live or paid model calls. Do not edit TARGET outside evals. Return only
the path written, task count, validation status, and any blocking fact.
```

After the handoff, inspect the diff. Reject changes outside `TARGET/evals/**`,
tasks that leak answers, or tasks that merely restate the skill. Keep the target
payload fixed after this point. Run the evaluator's dry-run to validate the
JSONL before authorizing live calls.

This boundary prevents conversational leakage, not filesystem access. Treat the
post-authoring diff audit as required evidence.
