# Prompt: recover the evaluator core before rebuilding it in Python

Use this prompt from the repository root on branch `python-core-redesign`.

```text
Work in /Users/jondev/dev/active/skill-eval-loop.

Use $martin-fowler-perspective for this review. Follow its activation and
evidence rules, including the first-reply disclaimer. If that skill is not
available, stop and report the missing prerequisite rather than silently
imitating it.

This first phase is read-only. Do not edit files, create design artifacts,
change branches, commit, install dependencies, rebuild binaries, or begin the
Python implementation. Present the migration design in the conversation for
review. Any persistent artifact or implementation requires a separately
approved contract.

## Goal

Recover the smallest coherent evaluator model from the repository's observable
behavior, then propose programmatic infrastructure in Python around that model.
The target is not a line-for-line Go port. It is a behavior-preserving redesign
that separates deterministic decisions from orchestration and effects.

Treat this premise as a hypothesis to test: repeated agent sessions may have
allowed the Go implementation to accumulate duplicated contracts, accidental
boundaries, and session-shaped abstractions. Do not accept that premise merely
because it appears in this prompt. Identify concrete evidence for and against
it.

Neither language implementation is automatically authoritative:

- the older Python scripts may preserve useful behavior as well as historical
  complexity;
- the Go implementation may contain deliberate corrections as well as
  accidental structure;
- tests, conformance scenarios, retained formats, public documentation, and
  actual CLI behavior are competing evidence;
- unresolved disagreement must remain visible rather than being settled by
  whichever implementation is newer.

## Read first

1. `AGENTS.md` and `ZEN.md`.
2. `README.md`, `skills/skill-eval-loop/SKILL.md`, and
   `docs/minimum-eval-contract.md`.
3. `docs/go-migration-inventory.md`, `docs/go-migration-test-map.md`, and
   `docs/go-migration-report.md`. Treat them as evidence, not binding design.
4. The public Python scripts under `skills/skill-eval-loop/scripts/`.
5. `cmd/`, `internal/`, and the Go tests.
6. `conformance/`, especially scenarios that pin failures, isolation,
   attestation, retained evidence, and Python compatibility.

Begin by verifying the current branch, commit, and worktree state. Record any
drift from these instructions before analyzing the design.

## Working distinctions

Use these categories consistently:

- **Core decision logic**: deterministic transformations and policies that can
  run without filesystem access, subprocesses, environment variables, clocks,
  randomness, credentials, or network access.
- **Application orchestration**: sequencing the paired experiment, state
  transitions, counterbalancing, failure handling, and invocation accounting.
- **Effect adapters**: filesystem, process control, harness invocation, model
  discovery, authentication references, clocks, IDs, signals, and Herdr.
- **Evidence contracts**: task/suite parsing, canonicalization, hashing,
  grading, retained artifacts, reports, and compatibility formats.
- **Delivery shell**: CLI parsing, launchers, packaged binaries, platform
  support, installation, and health checks.

Do not call code “pure” merely because it does not invoke a model. Parsing a
file, reading environment state, generating an ID, or writing a report is still
an effect even when the underlying transformation could be pure.

## Required analysis

### 1. Establish observable invariants

Build an evidence-backed table containing:

- invariant or externally visible behavior;
- why it exists;
- current Go owner;
- current Python owner, if any;
- tests, conformance cases, or documentation that prove it;
- whether it is required, accidental, obsolete, or unresolved;
- consequence of changing it.

Cover at least isolation, payload identity, task/suite validation, model
identity, invocation accounting, deterministic grading, process termination,
partial evidence, report validity, artifact integrity, path safety, and dry-run
side-effect freedom.

Do not equate test coverage with product intent. A test proves only the
behavior it checks.

### 2. Recover the domain model

Define the smallest set of concepts and relationships needed to explain the
evaluator. Challenge overloaded or duplicated terms such as run, suite, task,
case, pair, trial, condition, invocation, provider call, activation, grade,
validity, evidence, trace, and report.

Identify where the minimum JSONL evaluator and the legacy schema evaluator are
the same product, where they are genuinely different, and where the repository
currently avoids making that decision.

Do not create `CONTEXT.md` or an ADR during this phase. If terminology cannot be
resolved from repository evidence, list the exact ambiguity and recommend
$domain-modeling as a separately approved follow-up.

### 3. Map logic and effects

For each Go package and each public Python script, classify responsibilities
into the five working categories. Highlight:

- effectful code mixed into deterministic policy;
- knowledge duplicated across packages or languages;
- temporal decomposition that causes multiple modules to share one decision;
- pass-through layers and shallow abstractions;
- schemas or report structures with multiple owners;
- compatibility behavior that is intentional versus merely inherited;
- names that conceal rather than explain the domain.

### 4. Propose the Python target

Propose the smallest Python module structure that can express the recovered
model. For every proposed module state:

- its one-sentence responsibility;
- the knowledge it owns and hides;
- its public inputs and outputs;
- whether it is pure, orchestration, or an adapter;
- allowed dependencies;
- the invariant or change boundary that justifies its existence.

Prefer plain functions and immutable values for deterministic logic. Introduce
classes, protocols, dependency injection, repositories, services, factories,
or plugins only when a current requirement proves they reduce total
complexity. Three explicit calls are preferable to a speculative framework.

Do not choose a package layout until the behavior and ownership model make the
boundaries evident.

### 5. Design the verification bridge

Specify executable characterization and differential tests that allow Go and
Python to be compared without declaring either implementation universally
correct. Include:

- language-neutral fixtures for pure transformations;
- CLI and retained-artifact conformance where those interfaces remain public;
- normalization limited to proven nondeterministic fields;
- negative and failure-path cases, not only successful runs;
- a rule for adjudicating Go/Python disagreement against product intent;
- an explicit retirement condition for temporary differential machinery.

Separate tests that protect valuable behavior from tests that freeze an
accidental implementation detail.

### 6. Produce a reversible migration sequence

Recommend thin, independently verifiable slices. Each slice must state:

- one behavior or ownership decision moved;
- the characterization test that is red before and green after;
- the old and new owners during the transition;
- rollback method;
- deletion or simplification unlocked;
- stop condition before the next slice.

Start with the smallest high-confidence core, not the CLI shell. Keep one public
path authoritative at every stage. Temporary dual execution must have a narrow
purpose and a removal gate.

### 7. Name what not to port

Create an explicit list of Go structures, legacy Python structures,
compatibility paths, duplicated schemas, generated artifacts, and abstractions
that should not survive unless evidence justifies them. For every proposed
deletion, cite the evidence and state the compatibility risk.

## What not to do

- Do not perform a big-bang rewrite.
- Do not translate Go packages, structs, interfaces, or error patterns one for
  one into Python.
- Do not restore the historical Python architecture merely because Python is
  the destination.
- Do not combine the minimum and legacy evaluator contracts without an
  explicit product decision.
- Do not create a third schema, compatibility adapter, event framework, plugin
  system, generalized repository layer, or configuration mechanism.
- Do not retain both implementations indefinitely “for safety.”
- Do not treat regex grader failures as semantic truth when transcripts
  contradict them.
- Do not optimize packaging, concurrency, providers, dashboards, or deployment
  before the core ownership model is settled.
- Do not modify tests to make a proposed design appear compatible.
- Do not create documentation whose only purpose is to narrate the migration.
  Persist only decisions and contracts needed to make the next change clearly.

## Deliverable

Return one concise **Python Core Redesign Brief** with:

1. bounded verdict on whether the current Go design has escaped coherent
   ownership;
2. confirmed facts, framework inferences, and unknowns;
3. domain vocabulary and context boundaries;
4. invariant/evidence matrix;
5. current logic/effect ownership map;
6. proposed Python module boundaries and dependency direction;
7. characterization and differential-test bridge;
8. ordered migration slices with rollback and deletion gates;
9. “do not port” list;
10. unresolved decisions that genuinely require human judgment.

End with exactly one recommended next slice and its acceptance evidence. Do not
implement it. If persisting the brief would be useful, propose that as a
separate, narrowly scoped mutation and wait for approval.

Success means a future implementer can move one behavior into Python without
guessing what must remain invariant, which module owns the decision, how to
prove parity, or how to roll back. It does not mean every current behavior is
preserved or every future module has been predicted.
```
