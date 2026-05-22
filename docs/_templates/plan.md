# Plan: <thing>

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** Drafting | In progress | Done
- **Spec:** `docs/specs/<thing>/spec.md`
- **Owner:** <person>

## Approach

One or two paragraphs. Big-picture strategy. Tradeoffs the spec doesn't
spell out. Why this order, not another.

## Pre-conditions

What needs to be true before this plan can start?

- Dependencies on other modules / tasks
- Fixtures or data that must exist
- Decisions still pending (link to RFC if unresolved)

## Steps

Each step is one verifiable goal. Name the step by its success criterion,
not the activity. "Journal-line round-trip test passes" is a step;
"write tests" is not.

1. **<step name as success criterion>**
   - What you'll change
   - How you'll verify it (test name, command, eval)
1. **<step name>**
   - …
1. **<step name>**
   - …

## Verification gate

How will we know the whole plan succeeded? List the commands and
acceptance criteria — these should mirror the spec's acceptance criteria.

```
pytest tests/unit/test_<thing>.py
ruff check llm_wiki_kit/<thing>.py
mypy llm_wiki_kit/<thing>.py
```

## Risks

What could go wrong with this plan? What's the recovery path?

## Out of scope

What this plan deliberately defers. Link to follow-up RFC or task if known.
