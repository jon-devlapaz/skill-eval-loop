# Agent instructions

## Maintainability

Follow the maintainability principles in [ZEN.md](ZEN.md).

## Next change

Harness adapters, judging, and domain tests now have owners. `skill_eval_loop.py` is still ~2k lines; load models still serialize to dicts before the rest of the pipeline. Do not split further until a later change needs a new owner.

The remaining evidence gate is Task 10: a repeated-trial promotion run on an independently controlled, human-labeled holdout. Do not invent that holdout here.
