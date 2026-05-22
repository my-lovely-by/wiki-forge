# Plan: wiki-run-exec

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** In progress (v1 complete; v2 awaits RFC-0004)
- **Spec:** [`docs/specs/wiki-run-exec/spec.md`](spec.md)
- **Owner:** `llm_wiki_kit/run.py`, `llm_wiki_kit/cli.py:_cmd_run`

## Approach

v1 of `wiki run --exec` shipped in PR #73 (commit `4585e03`). The
construction-phase work-breakdown — pure helpers first, then
orchestrator, then CLI wiring — lives in the merged commit history
and is not duplicated here. This file's job now is to capture the
**single queued follow-on** that the v1 spec explicitly defers, so
a future contributor can pick it up without re-deriving the context.

## Pre-conditions

Already satisfied for v1; carried forward for the queued task:

- ADR-0009 (headless argv shape) — Accepted.
- ADR-0010 (agent passthrough) — Accepted; the *flag insertion
  point* is pinned, but the **resolution-chain inputs** (recipe-
  declared mapping, schedule-entry override) live in RFC-0004.
- RFC-0004 (recipe-declared agent bindings + schedule-entry agent
  override) — **not landed**. This is the blocker for the queued
  task below.

## Steps

(v1 is shipped. The list below is the queued v2 work the spec's
§Non-goals explicitly defers.)

1. **`_build_argv` emits the ADR-0010 `--agent <name>` pair when
   RFC-0004's resolution chain produces a non-`None` name; the
   model and CLI surfaces follow in the same PR.**
   - **Depends on:** RFC-0004 accepted (resolution-chain inputs).
   - **Verification mode:** TDD. The spec amendment lands in this
     same PR (it is not a prerequisite handed off elsewhere): it
     adds new CTs covering each sub-concern listed below, and the
     construction tests below ride alongside the production code.
   - **Sub-concern (a) — argv emission.** `_build_argv` learns to
     accept an optional resolved agent name and emit
     `["--agent", <name>]` immediately before the trailing prompt
     positional (per ADR-0010 §Decision step 2).
     **Construction tests:** `test_build_argv_emits_agent_pair`
     and `test_build_argv_omits_agent_when_none` in
     `tests/unit/test_run_exec.py` (the latter reinforces v1's
     CT-13 invariant under the new code path).
   - **Sub-concern (b) — model additive field.** Extend
     `OperationExecFailedEvent` with `agent: str | None = None`
     (ADR-0002 additive-schema rule), so failures record the
     active agent.
     **Construction tests:**
     `test_legacy_exec_failed_event_without_agent_replays_as_none`
     (legacy line with no `agent` key replays as `None`) in
     `tests/unit/test_run_exec.py`.
   - **Sub-concern (c) — CLI surface.** The CLI exposes
     `wiki run --agent <name>`; `dispatch_and_exec` gains an
     `agent: str | None` keyword that flows from CLI through to
     `_build_argv`. Resolution order matches RFC-0004 §4
     (schedule-entry → recipe `agents:<>:runs` → operation
     `preferred_agent` → no agent).
     **Construction tests:** `test_cli_agent_flag_flows_through`
     in `tests/integration/test_cli_run_exec.py` driving the
     subprocess with `--agent` and asserting the captured argv;
     resolution-chain coverage lands wherever RFC-0004 places the
     chain (probably `tests/unit/test_run_dispatch.py` if the
     chain is dispatch-adjacent).
   - **Spec edits in the same PR:** flip §Non-goals "Agent
     passthrough" entry from "deferred" to a back-reference to the
     v2 CT(s); update §Outputs §"Exec phase" step 4 to mention the
     optional flag insertion; add new contract tests under
     §"Acceptance criteria" — one CT per sub-concern above.

## Verification gate

Already met for v1 — the merged PR cleared `ruff check`, `ruff
format --check`, `mypy`, and `pytest -m 'not slow'` (and the
spec's acceptance criteria CT-1..CT-17 are all marked `[x]`).

For the queued task above, the gate is the same four commands
plus the new contract tests pinned in that task's spec amendment:

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

## Risks

- **RFC-0004 lands with a different resolution chain than
  ADR-0010 assumed.** Mitigation: ADR-0010 §"What this ADR does
  not cover" explicitly defers the chain to RFC-0004; the kit's
  emit site is a single optional flag pair, so a chain-shape
  change is a SKILL-level edit, not a re-architecture.

## Out of scope

Everything in spec §Non-goals stays out of scope unless the
queued-task amendment above moves it. In particular:

- `OperationExecSucceededEvent` — deferred indefinitely per
  spec §Non-goals.
- SDK-based execution — out per RFC-0003 §"Decisions already
  made".
- Cost / token tracking, streaming exec output, journal-lock
  hold-across-subprocess, partial-work recovery, auto-retry,
  and `--dry-run` — all out per spec §Non-goals.
- Vault-side SKILL contract — owned by the `wiki-schedule`
  vault-side SKILL.md, not by this spec.
- **Env-scrubbing for the exec subprocess.** Discussed in spec
  §Invariants as a possible future ADR; not queued here because
  no concrete bug has surfaced and v1 deliberately tests the
  pass-through. If env scrubbing is needed, it lands as a fresh
  ADR + a separate spec amendment, not by inheriting this plan.
