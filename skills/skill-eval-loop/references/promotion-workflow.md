# Promotion evidence workflow

This is the complete business workflow for turning a valid paired run into an
auditable, human-grounded promotion decision package. The evaluator prepares
and verifies evidence; the accountable human owner makes the decision.

## Roles

- **Holdout custodian:** controls the task file, keeps it out of skill authoring
  and development runs, and attests that it covers positive, negative,
  ambiguous, near-tie, and adversarial cases.
- **Evaluator operator:** calibrates the judge, runs the locked promotion
  evaluation, prepares the blinded packet, and records actual cost.
- **Two reviewers:** independently inspect every blinded A/B transcript and
  label the overall winner plus every rubric dimension with rationale.
- **Decision owner:** reviews disagreements, regressions, variance, usage, cost,
  and limitations before accepting or rejecting promotion.

One person may operate the evaluator and own the decision. The two label files
must still use distinct reviewer identities and be completed independently.

## 1. Lock and run the holdout

The custodian supplies an absolute task path outside the target skill. Run the
accepted calibration and inspect the dry-run plan before authorizing live calls.

```bash
"$EVALUATOR" run \
  --skill /absolute/path/to/target-skill \
  --tasks /absolute/custodian/path/holdout.jsonl \
  --output /absolute/path/to/fresh-promotion-run \
  --harness codex \
  --harness-bin /absolute/path/to/codex \
  --model exact-runner-model \
  --judge-model exact-judge-model \
  --calibration /absolute/path/to/calibration.json \
  --trials 3 \
  --timeout-seconds 300 \
  --promotion \
  --dry-run
```

Run the identical command without `--dry-run` only after the hashes, models,
invocation count, and cost authority are accepted.

## 2. Prepare a blinded review packet

```bash
"$EVALUATOR" prepare-review \
  --run-dir /absolute/path/to/fresh-promotion-run \
  --output /absolute/path/to/fresh-review-packet
```

Give reviewers only the review packet, not the retained run. The packet contains
the exact A/B prompts previously shown to the automated judge, a hash-bound
manifest, a label template, and a holdout-attestation template. It contains no
control/treatment mapping.

The custodian completes `holdout-attestation-template.json`. Each reviewer makes
a private copy of `labels-template.json`, sets a distinct `reviewer_id`, labels
every overall comparison and dimension with `A`, `B`, or `tie`, provides a
rationale, and sets `transcript_reviewed` to `true` only after reading the item.
Reviewers must not inspect each other's labels before both files are final.

## 3. Finalize the evidence

```bash
"$EVALUATOR" finalize-review \
  --run-dir /absolute/path/to/fresh-promotion-run \
  --manifest /absolute/path/to/fresh-review-packet/manifest.json \
  --holdout-attestation /absolute/custodian/path/holdout-attestation.json \
  --labels /absolute/reviewer-a/labels.json \
  --labels /absolute/reviewer-b/labels.json \
  --cost-usd 12.34 \
  --cost-note "Provider invoice or subscription allocation." \
  --output /absolute/path/to/fresh-promotion-review
```

Finalization fails if the run is not a quality-complete promotion run, hashes
drift, holdout coverage or custody is not attested, reviewer identities are not
distinct, a transcript or dimension is unlabeled, a rationale is missing, or
the recorded cost is invalid.

The output reports:

- human agreement overall and by dimension;
- automated-judge agreement with each reviewer and human consensus;
- disagreements with both rationales;
- unblinded control/treatment/tie outcomes by task and across trials;
- treatment improvements and control-winning regressions;
- usage, recorded cost, and explicit limitations.

It also retains hash-listed copies of the manifest, holdout attestation, and
both complete reviewer files so the final handoff does not depend on scattered
inputs.

`complete_human_review` means the evidence package is complete. It does not mean
the skill should be promoted. The decision owner must inspect disagreements and
regressions and record the business decision separately.

## What the machine cannot prove

The evaluator binds files and enforces completeness. It cannot authenticate a
reviewer's real-world identity, prove that the custodian kept the holdout secret,
or decide whether the observed tradeoff is acceptable for the client. Those are
explicit human-accountability boundaries, not hidden automation claims.
