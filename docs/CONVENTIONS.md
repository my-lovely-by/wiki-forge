# Conventions

> How we work on this repo. The companion to `AGENTS.md` — that file tells
> agents *what* to do; this file explains the lifecycle and mechanics behind
> the docs they reference.
>
> Substantive changes to this file go through an RFC. Trivial fixes (typos,
> broken links, clarifications) land as normal PRs.

## The doc hierarchy

| Doc kind | Lives in | Status | Changes via |
|---|---|---|---|
| Charter | `docs/CHARTER.md` | Frozen | RFC |
| ADR | `docs/adr/NNNN-*.md` | Frozen once accepted | New ADR that supersedes |
| RFC | `docs/rfc/NNNN-*.md` | Living until accepted, then frozen | Edit while open; supersede when closed |
| Spec | `docs/specs/<thing>/spec.md` | Living | Edit in the same PR as the code |
| Plan | `docs/specs/<thing>/plan.md` | Living until done | Edit while implementing |
| Architecture | `docs/architecture/` | Living | Edit when layout or modules change |
| Concept / how-to / reference / tutorial | `docs/{concepts,how-to,reference,tutorials}/` | Living | Normal PR |
| Roadmap | `docs/ROADMAP.md` | Living | Normal PR; substantive shifts via RFC |

The two-axis distinction is **frozen vs. living** and **decision vs. plan**.
ADRs and the charter are frozen — once accepted, they record what we
believed at a point in time, and they get superseded rather than edited.
Specs, plans, and architecture are living — they describe current truth
and must be kept in sync with the code.

## ADR vs. RFC vs. spec — when to use which

- **ADR** — we decided something load-bearing and want the decision to
  survive personnel turnover. Past tense. "We chose X because Y." Frozen
  once accepted; superseded by a new ADR when wrong.
- **RFC** — we're proposing a change and want feedback before committing.
  Future tense. "We should do X because Y." Lives in `rfc/` open for
  comment; on acceptance, produces ADRs and specs and is itself archived.
- **Spec** — the contract for one piece of the kit. Present tense. "This
  thing takes X, returns Y, holds invariant Z." Updated alongside the
  code; spec/code drift is a bug.

If you're not sure: **ADR for one-off decisions, RFC for changes that
need review, spec for ongoing definition.** When in doubt, ask in the PR.

## Numbering

- ADRs: `0001-`, `0002-`, … globally monotonic. Don't reuse numbers,
  even for withdrawn ADRs (mark them `Status: Withdrawn` and skip the
  number).
- RFCs: same scheme, separate sequence.
- Specs: no numbering — directory name `docs/specs/<thing>/` is the
  identifier.

## File naming

- ADRs: `NNNN-<kebab-case-title>.md`
- RFCs: same
- Specs: each in its own directory under `docs/specs/`, with `spec.md`
  and (optionally) `plan.md` inside.

## What counts as "load-bearing" (ADR-worthy)?

A decision is load-bearing if:

- It would be expensive to reverse (cost of switching > cost of the
  current path × 2).
- Future code will reference it as a constraint ("we can't add X because
  ADR-NNNN says…").
- Reasonable people would disagree, and we need a tiebreak the next time
  the question comes up.

Examples of load-bearing decisions in this repo: rendering engine
(`ADR-0001`), state-truth model (`ADR-0002`), shared-file write model
(`ADR-0003`), drift-detection model (`ADR-0004`), schema model
(`ADR-0005`).

Examples of NOT load-bearing: which test framework to use (pytest is
the default; switching is local), which CLI library (Click vs. argparse,
local), formatting choices (ruff config).

## How to add an ADR

1. Copy `docs/_templates/adr.md` to `docs/adr/NNNN-<title>.md` with the
   next free number.
1. Fill in context, decision, consequences, alternatives.
1. Mark `Status: Proposed`.
1. Open a PR that adds the ADR alongside the change it justifies.
1. On merge, change `Status:` to `Accepted` and don't touch it again.

## How to add an RFC

1. Copy `docs/_templates/rfc.md` to `docs/rfc/NNNN-<title>.md`.
1. Mark `Status: Open for comment`.
1. Open a PR. The PR is the discussion thread.
1. When ready to decide: either land the PR with `Status: Accepted` and
   the ADRs/specs/code it produces, or close with `Status: Rejected` or
   `Withdrawn` and a one-paragraph rationale.

## How to add a spec + plan

1. Create `docs/specs/<thing>/`.
1. Copy `docs/_templates/spec.md` to `spec.md`, fill it out.
1. Copy `docs/_templates/plan.md` to `plan.md`, fill it out — but only
   if the work needs more than one PR. For a single-PR change, the plan
   is overhead.
1. Reference the spec from the code (a module-level docstring is fine).
1. Keep them in sync as the code evolves.

## Commit messages

During v2 development: `v2: task <N> - <one-line summary>` where N is
the task number from `docs/rfc/0001-v2-architecture.md`.

After v2: conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`,
`test:`, `chore:`) with the affected module in parens when useful:
`fix(journal): handle empty file as zero events`.

## PR scope

- **One task per PR.** Migration tasks each get one PR. Don't bundle.
- **Spec and code together.** If you're changing behavior, the spec and
  any affected docs change in the same PR.
- **ADRs are their own PR or rolled into the PR that motivated them.**
  Whichever produces clearer history.

### Tutorial-touching PRs

When a PR touches `docs/guides/tutorials/`, the reviewer reads the
PR's cold-walk paragraph first; missing paragraph or unaddressed
thinks-required steps block merge. The cold-walk discipline lives in
`docs/specs/task-21-examples-tutorials/spec.md` AC12 — it's a manual
check that the literal commands in the tutorial walk end-to-end
without requiring the reader to think past what the prose tells them.

## Tests as the bar for "done"

A task is done when the acceptance criteria pass — not when the code
compiles or "looks right." For migration tasks, acceptance criteria
come from the migration RFC. For everything else, from the spec.

The mechanical gates (`pytest`, `ruff`, `mypy`) catch the cheap problems
before review. They're necessary, not sufficient.

## How we do non-trivial work

For anything beyond a one-line edit, follow the **plan → execute → verify
→ review → iterate** loop. The mechanics live in the
[`work-loop`](../.claude/skills/work-loop/SKILL.md) skill; this section is
the why.

**Why a loop, not a single pass.** LLM self-assessment is unreliable —
agents declare victory when they *feel* done. Mechanical gates (`ruff`,
`mypy`, `pytest`) plus an adversarial review pass replace "feel" with
verifiable termination. The loop keeps going until both kinds of check
are satisfied, or until it hits a hard cap and surfaces.

**Why think before acting.** The cost of a wrong start is higher than the
cost of thinking. For high-stakes work (load-bearing decisions, multi-file
refactors, anything touching the journal, managed regions, drift
detection, or `safe_write`), use your agent's extended-thinking facility
— it catches the wrong assumption *before* it becomes 14 commits of wrong
code. For routine work, skip the ceremony; the discipline is "match
thinking depth to stakes," not "always think hardest."

**Why iterate, not retry from scratch.** Most loops converge: gates fail,
review surfaces a finding, the next pass fixes it. Restart-from-scratch
loses the planning context. The other-shape (fresh-context every
iteration) is the [Ralph harness](#when-to-reach-for-ralph), used only
when fresh context is the *point*.

**Three verification modes** — every plan task picks one *before* code is
written:

- **TDD** — pure functions, models, validators, parsers, journal events,
  managed-region rendering. Default for the kit's testable logic.
  Contract tests live in `spec.md`; construction tests live in
  `plan.md` (per-task `Tests:` subsection precedes `Approach:`). Red →
  green → refactor.
- **Goal-based** — build config, scaffolding, CLI wiring,
  generated-code consumption. The task's `Done when:` is the contract;
  verify with a one-liner (`pytest -k`, `wiki --help`, `ruff check`,
  `grep`) instead of writing a test that re-asserts what the typechecker
  already proves.
- **Visual / manual QA** — the kit ships an Obsidian-readable vault and
  a CLI. For vault rendering, the verification artifact is "open the
  vault in Obsidian (or run `wiki doctor`) and confirm what the *user*
  sees." For CLI output, capture the actual stdout/stderr the user
  reads. A test that passes when the on-screen result is wrong is
  mode-mismatched, regardless of what framework wrote it.

**Why capture learnings.** A loop that finishes without updating *some*
doc, skill, or note has wasted what it learned. The next agent (Ralph or
human) will pay for it again. The work-loop skill enumerates where each
kind of learning belongs.

**Kit anti-patterns the reviewers should flag** — first-class checks for
the kit's reviewer subagents:

- Bypassing `write_helper.safe_write()` for a write that lands in a
  user's vault. Drift detection short-circuits and the user loses their
  edits.
- Hard-coding a vault path in kit code. The kit only knows what the CLI
  passes in; tests use `tmp_path` fixtures or `tests/fixtures/*-vault/`.
- Adding a runtime dependency without an ADR. Runtime deps are
  `pyyaml`, `pydantic>=2`, and stdlib; everything else is a dep the end
  user could fail to install.
- Creating a new top-level directory. Structure is intentional; new
  directories go through RFC.
- Editing `docs/CHARTER.md` substantively without an RFC.
- Conflating kit-side and vault-side skills (`.claude/skills/` vs.
  `core/files/skills/`). They have different audiences and different
  lifecycles.

## Contract tests vs. construction tests

Tests are designed *up front, before any implementation*. They live in
two places, with different shapes and different lifecycles:

- **Contract tests** live in `docs/specs/<thing>/spec.md`. Black-box,
  behaviour-only — they define "done" for the spec. Any valid
  implementation must pass them. They are stable against
  *implementation* change (that's the whole point of a contract); they
  still evolve with *spec* (behavioural) change during the spec's
  living phase, and freeze when the spec freezes.
- **Construction tests** live in `docs/specs/<thing>/plan.md`, attached
  to each task. Units, edge cases, property tests, fixtures — they
  guide the implementer through the build. For kit code, the
  construction tests usually correspond to pytest test files under
  `tests/unit/` or `tests/integration/`; the plan task names them
  explicitly. Construction tests are *revisable* if one turns out to
  over-specify an internal detail the plan changed.

Within a plan task, the **Tests** subsection comes *before* Approach.
Tests drive implementation, not the other way around. Red → green →
refactor: write the failing test, make it pass, refactor — separate
commits for each when the change is non-trivial.

This is the forcing function that keeps specs honest (you can't write a
contract test for a vague behavioural claim) and keeps implementations
honest (you can't drift from the spec if the spec's tests are red).

The two failure modes the reviewer subagents watch for here:

- A behaviour in `spec.md` with no contract test pinned to it — a
  promise without a check, drift waiting to happen.
- A construction test asserting an internal shape (mock-call counts,
  attribute presence on a private helper) when the observable contract
  is a returned value or an on-disk file. Mock-shape tests change in
  lockstep with production code; they are mirrors, not contracts.

## Work-loop state

A spec-driven loop carries a small amount of session-scoped state — how
many iterations have run, what budget is left, what findings the last
review surfaced. Putting that in prose leaves it un-enforceable; putting
it on disk as data lets a tiny script gate each phase. That script is
[`tools/check-done.py`](../tools/check-done.py); the data lives at
`docs/specs/<feature>/state.json`, and the schema lives at
[`docs/_templates/state.json`](_templates/state.json).

**Fields:**

| Field | Meaning |
|---|---|
| `feature` | spec slug (informational) |
| `iteration_count` and the iteration cap field | how many in-session loops have run / the hard ceiling. The cap is data, not prose — tune it per spec rather than editing the SKILL. The literal JSON form lives in [`docs/_templates/state.json`](_templates/state.json); refer to it from prose by name. |
| `token_budget_used_pct` / `token_budget_cap_pct` | session token budget — **advisory** until the orchestrator populates `_used_pct`. The threshold lives in data so a project can tune it. |
| `consecutive_same_error_count` / `consecutive_same_error_threshold` | gate-error stuck-loop counter / cap. Advisory until the SKILL prescribes when to increment `_count`. |
| `plan_review_status` | `pending` until the spec-mode adversarial review clears, then `approved`. Enforced as a gate on **all phases** (`plan`, `implement`, `review`) — not just `--phase plan`. |
| `last_commit_sha` | latest commit produced by the loop (informational; advisory). |
| `finding_fingerprints` / `previous_finding_fingerprints` | hashes of reviewer findings, rotated each REVIEW iteration; used to detect circling. Algorithm pinned in the work-loop SKILL §REVIEW. |
| `worktrees` | one entry per `implementer` subagent dispatched in the current session's supervisor pass: `{task_id, branch, path, status, report_path}` where status is `in-progress` / `ready` / `blocked` / `failed` and `report_path` points at the implementer's markdown report under `docs/specs/<feature>/notes/`. Report files are gitignored — session-scratch, not history. Entries persist with their terminal status for the rest of the loop. Empty in single-agent loops. See [§ Supervisor mode](#supervisor-mode). |

**Exit contract.** `check-done.py` exits 0 when the phase is satisfied
and non-zero when it isn't, with a one-line reason on stderr. Treat
non-zero as "stop and surface" — with one deliberate exception: the
SKILL's PLAN-init step calls the script with `--phase plan` *expecting*
exit 1 with `plan not approved`. That exit-1 is the cue to run the
spec-mode reviewer, not a real stop. Any other non-zero exit terminates
the loop.

**Lifecycle.** `state.json` is **per-session scratch**, not history. The
file is gitignored (`docs/specs/*/state.json` in
[`.gitignore`](../.gitignore)); the SKILL initializes it from the
template at PLAN start. Across sessions, a fresh run re-initializes —
intentionally; a new session deserves a fresh budget.

**Atomic writes.** The orchestrator updates `state.json` mid-iteration;
`check-done.py` reads it between phases. Always write atomically
(tmp-file + `os.replace`, or shell `mv`) so a partial-write doesn't
present as malformed JSON and falsely stop the loop.

**Changing the cap.** Editing
[`docs/_templates/state.json`](_templates/state.json) changes the
*starting point* for any **newly-initialized** spec. To change the cap
for a spec that's already running, edit that spec's own (gitignored)
`docs/specs/<feature>/state.json` — the template edit doesn't propagate
backward. The numbers move with the data, not the SKILL prose.

## Model selection

Every subagent file declares `model:` in its frontmatter explicitly. The
[`lint-agent-artifacts.sh`](../tools/lint-agent-artifacts.sh) linter
enforces this. The current choices:

| Subagent | Model | Why |
|---|---|---|
| `adversarial-reviewer` | `opus` | Adversarial judgment; stakes are correctness. Output drives a hard gate. |
| `security-reviewer` | `opus` | Threat-model reasoning; stakes are security (`safe_write`, ingest input handling, recipe loading). |
| `quality-engineer` | `opus` | Maintenance lens; spec-level coverage pass over an integrated journey. |
| `implementer` | `sonnet` | One narrow plan task per dispatch; gates rerun in the primary; supervisor judges merge readiness. Cost beats capability here. |

Changing a subagent's model is a behaviour change, not a configuration
tweak — note the change in the PR that makes it, with a one-line
justification. If the change reverses a previous choice in a way a
future maintainer would ask "why", surface it in the PR description.

## Supervisor mode

When a plan has multiple tasks declaring `Depends on: none`, the
work-loop enters **supervisor mode**: one primary orchestrator
dispatches `implementer` subagents in parallel, each working in its own
git worktree, then merges the results back and runs gates in the
primary. The mechanics live in the
[`work-loop` skill](../.claude/skills/work-loop/SKILL.md) §EXECUTE; this
section is the why and the boundary.

**Why a separate mode instead of a separate skill.** The trigger is
structural (the plan's shape), not a choice the user makes. Branching
inside `work-loop` means contributors never pick the wrong skill, and
the overlap with single-agent flow stays single-sourced.

**Why an implementer subagent, not a recursive work-loop.** The
implementer's job is narrow — build one task, run gates, report.
Reviewing, dispatch decisions, and merge belong to the supervisor. A
recursive work-loop would let an implementer spawn its own
implementers; that's nested coordination overhead with no clear win.
Keep the tree two levels deep: supervisor → leaf implementers.

**Worktrees as the coordination primitive.** Each independent task
gets `.worktrees/<task-id>/` checked out on its own branch
(`<base-branch>-<task-id>`). Worktrees are git-native, support parallel
checkout of the same repo, and avoid lockfile contention. The directory
is gitignored ([`.gitignore`](../.gitignore)); branches live in git
history for traceability.

**Merge discipline.** The supervisor merges with `git merge --no-ff
<base>-<task-id>` into the primary branch, **sequentially in task-id
order** (numeric where IDs look like `T1`, `T2`, …; lexicographic
otherwise). If a sequential merge conflicts, the tasks weren't actually
independent — the plan was wrong. Surface that as a PLAN-level
escalation, not a `git mergetool` session.

**Gates run in the primary, not the worktree.** Each implementer runs
gates inside its worktree and reports the result, but those results are
**advisory**. The supervisor reruns `ruff` / `mypy` / `pytest` against
the merged state — that's the only signal that counts.

**Escalating implementer failures.** If an implementer reports
`blocked` or `failed`, the supervisor surfaces the failure list to a
human and returns to PLAN. It does **not** redispatch the same
implementer on the same task — the assumption that produced the
failure is what needs revising, not the attempt.

## Knowledge base

The repo accumulates practitioner-level lessons in
`docs/knowledge/patterns.jsonl`: patterns ("when you touch
`write_helper`, also remember managed regions short-circuit drift
detection"), gotchas ("the journal treats an empty file as zero events,
not as a missing journal"), and antipatterns ("don't mock the journal
in integration tests"). One JSON object per line, scoped to a file
glob. The schema and curation conventions land alongside the file
itself in PR-3 of RFC-0002.

**Why a separate bucket.** ADRs answer *why we decided X*;
[`docs/architecture/`](architecture/) describes *current structure*;
[`docs/guides/`](guides/) is for *users*. Knowledge entries are
practitioner residue — the things you learn by building, not by
deciding or documenting. They earn a home because they're scoped to
globs (an agent priming for `llm_wiki_kit/journal.py` should see the
journal gotchas, not every lesson the repo ever learned) and
append-only (a lesson that stops being true gets a *new* entry citing
the old one, not an edit — which keeps history honest).

**How agents see it.** [`tools/hooks/session-start.sh`](../tools/hooks/session-start.sh)
reads the file at session open and prints the entries —
optionally filtered by a path or narrower glob. Matching uses Python's
`fnmatch` with the caller's `--scope` value as the *path* argument and
the entry's stored glob as the *pattern*, so an agent working in
`llm_wiki_kit/render.py` gets entries scoped to `llm_wiki_kit/**` plus
any repo-wide `*` entries. The work-loop SKILL's *Capture what was
learned* section points contributors at this file as the destination
for pattern/gotcha/antipattern-shaped learnings; other shapes still go
where they already belong (AGENTS.md, skill bodies,
`docs/architecture/`, `docs/guides/explanation/`).

## Enforcement (the triplet)

Three layered mechanisms enforce the project's discipline. Together
they are "the enforcement triplet":

| Layer | Mechanism | What it gates |
|---|---|---|
| Caps | [`tools/check-done.py`](../tools/check-done.py) | Iteration cap, token budget, plan approval, fingerprint stasis (see [§ Work-loop state](#work-loop-state)). |
| Artifacts | [`tools/lint-agents-md.sh`](../tools/lint-agents-md.sh), [`tools/lint-agent-artifacts.sh`](../tools/lint-agent-artifacts.sh), [`tools/lint-skill-deps.sh`](../tools/lint-skill-deps.sh), [`tools/lint-knowledge.sh`](../tools/lint-knowledge.sh) | Shape, manifest, and content hygiene for every `.claude/`, `AGENTS.md`, and `docs/knowledge/` artifact. |
| Aggregation | [`tools/hooks/pre-pr.sh`](../tools/hooks/pre-pr.sh) | Runs caps + artifact linters together before a PR opens, plus the kit's `ruff` / `mypy` / `pytest` gates. CI mirrors this. |

All three layers are wired up. The artifact linters and the
aggregation hook run in CI via
[`.github/workflows/agent-artifacts.yml`](../.github/workflows/agent-artifacts.yml);
the existing [`ci.yml`](../.github/workflows/ci.yml) keeps running the
language gates (`ruff` / `mypy` / `pytest`) independently on the
Python matrix.

## Scaling profiles

The vendored work-loop is designed to work across three repo sizes:

- **Profile A** — microservice or single-component, 1–3 contributors.
  Minimum viable set: work-loop SKILL, `check-done.py`, an `AGENTS.md`.
  Supervisor mode and specialist reviewers are usually overkill.
- **Profile B** — single library or app, 4–10 contributors. Supervisor
  mode earns its keep when a plan has two or more `Depends on: none`
  tasks. `adversarial-reviewer` is worth using on every PR;
  `security-reviewer` and `quality-engineer` when the diff warrants.
- **Profile C** — medium platform / engine, 10–50 contributors. All
  tooling in active use; the knowledge base is actively populated and
  the `session-start` hook is wired in the consumer's
  `.claude/settings.json`.

**The kit operates at Profile B.** It's a single Python package with a
small contributor base. Profile C tooling (knowledge base, hooks)
ships pre-installed via RFC-0002's later PRs, but is used actively
only when the diff warrants it — a one-line bug fix doesn't need a
quality-engineer pass, while a refactor across `journal`,
`write_helper`, and `managed_regions` probably does.

The reviewers themselves are stack-agnostic; they read AGENTS.md and
this file at session start. Tuning to the kit happens through these
files, not through edits to the agent bodies.

## When to reach for Ralph

The same work-loop can run unattended — a fresh Claude Code session
per iteration, state in files only. That's a Ralph loop. The harness
and operating instructions live at [`tools/ralph.sh`](../tools/ralph.sh)
and [`tools/RALPH.md`](../tools/RALPH.md).

Reach for Ralph only when *all* of these hold:

- Completion is fully mechanical (tests pass, a spec checklist is
  fully ticked, a benchmark hits a threshold).
- The task slices into context-window-sized items.
- Verification is reliable (flaky tests turn Ralph into a slot machine).
- You have already validated the approach in-session with the regular
  work-loop. Ralph amplifies whatever your conventions are; if those
  aren't tight, Ralph just produces more bad code faster.

Ralph is the wrong tool when "done" is fuzzy or aesthetic, when the
task needs human judgment mid-flight (architectural choices,
ambiguous spec language, security-sensitive decisions), or when
verification is unreliable. Read [`tools/RALPH.md`](../tools/RALPH.md)
before running. Ralph is a sharp tool — useful, narrow, and not the
answer to most work.

## When this file is wrong

Same rule as AGENTS.md: flag drift, don't work around it. The
conventions exist to make the work boring and predictable. If they're
producing friction without value, fix them — via RFC for substantive
shifts, normal PR for cleanups.
