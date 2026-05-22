# Spec: <thing>

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft | Implemented | Deprecated
- **Owner:** <module or person>
- **Related:** RFC-NNNN, ADR-NNNN, `docs/specs/<thing>/plan.md`
- **Constrained by:** ADR-NNNN, RFC-NNNN, or `none` — external decisions
  this spec inherits from. The adversarial-reviewer reads ADRs cited
  here when running in spec/plan-review mode. Distinct from
  `## Constraints` below, which lists self-imposed structural choices
  for this spec.

## What this is

One paragraph defining the thing and its boundary. A reader should be
able to tell from this paragraph what the thing *is* and what it *isn't*.

## Inputs

What does this thing receive? File paths, function arguments, environment,
journal events. Be exact about types and required fields.

## Outputs

What does this thing produce? Return values, files written, journal
events appended, side effects.

## Behavior

Step-by-step what happens between input and output. Include:

- **Happy path** — the canonical flow
- **Edge cases** — what happens when an input is missing, malformed,
  conflicting with prior state
- **Error cases** — what raises, what's caught, what's surfaced to the user

## Invariants

What must always be true before, during, and after this thing runs?

- Things that hold even on failure
- Things the user can rely on
- Things tests verify

## Contracts with other modules

Who calls this? Who does it call? What does the journal record about it?

## Acceptance criteria

What does "done" look like? These translate directly into tests.

- [ ] <observable behavior>
- [ ] <invariant tested>
- [ ] <error case covered>

## Non-goals

What this thing *won't* do, in case anyone asks.

## Constraints

What *implementation strategies* are off the table for this spec? Where
Non-goals enumerates behaviors we won't ship, Constraints enumerates the
structural choices we won't make — the dependencies, module boundaries,
or architectural surface area that this work must not introduce.

This is what protects the diff from sprawl. A spec can be tight on
non-goals and still produce three new abstraction layers; Constraints
is what prevents that. The work-loop SKILL's structural-change trigger
measures the plan against this list when it fires; if this section is
empty or missing, it falls back to Non-goals, the plan's
declined-pattern register, and AGENTS.md.

Each entry names a *specific* structural choice. Generic guardrails
("keep it simple", "avoid over-engineering") belong in code review,
not here.

Examples:
- No new module boundary under `llm_wiki_kit/`.
- No new top-level dependency (would require an ADR per AGENTS.md).
- No new top-level directory at the repo root.
- No bypass of `write_helper.safe_write()`.
- No new public CLI verb beyond what Behavior describes.
