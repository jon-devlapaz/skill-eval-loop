# Independent eval authoring

Use this protocol only in a fresh-context subagent when the target skill has no
`evals/evals.json`. The coordinating agent must not author or repair cases.

## Isolation contract

The coordinator supplies only:

- the absolute target-skill path;
- the absolute path to `references/eval-suite-schema.md`;
- permission to write only `<target-skill>/evals/**`;
- the neutral task below.

Do not supply the parent conversation, proposed or reference answers, intended
fixes, suspected weaknesses, candidate outputs, grading strategy, or prior run
artifacts. The author subagent may inspect the target skill's shipped contents
but must not modify them. Do not run paid trials.

Use this delegation prompt:

```text
Create an independent evaluation suite for the skill at TARGET_SKILL_PATH.
Work only inside TARGET_SKILL_PATH/evals/. Read the target's shipped SKILL.md
and resources, then follow SCHEMA_PATH. Do not inspect parent-chat context,
candidate implementations, prior benchmark outputs, or proposed answers.
Create a schema-version-3 suite, references, fixtures, source artifacts, and
provenance hashes. Run audit_suite.py without model calls. Return only a factual
handoff: files created, case IDs/count, behavior and routing coverage,
activation mode, grader types, provenance paths, and audit result.
```

## Suite quality gate

Require all of the following:

- at least three distinct, independently meaningful cases; prefer five to ten
  when the skill has several behaviors;
- realistic user requests that do not name the skill, quote its instructions,
  expose internal filenames, or encode the desired response;
- positive coverage plus edge or negative coverage wherever the skill has a
  meaningful boundary; avoid several paraphrases of one behavior;
- `forced` activation for measuring capability effect; use `autonomous` only
  when skill selection is itself under evaluation;
- deterministic final-state graders where feasible, with unique behavioral
  names; use model rubrics only for qualities that cannot be checked
  deterministically;
- minimal fixtures and references that pass every declared grader without
  depending on the candidate run;
- honest provenance: use `author_derived` and `author_scenario` for newly
  invented cases, never label them `held_out` or `production_regression`;
- one retained source artifact and valid suite, case, and artifact hashes for
  every case.

Run the static audit and correct failures before handoff. Report its structured
summary without pasting prompts, expected outputs, references, or grader
details into the coordinator's context. Reference execution remains a separate
preflight in the normal runner.

After authoring starts, treat the target skill as frozen. If its shipped payload
changes, discard the suite for that run and repeat authoring in another fresh
subagent so no implementation is tuned against visible cases.
