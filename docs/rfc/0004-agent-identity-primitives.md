# RFC-0004: agent identity primitives for autonomous operations

- **Status:** Accepted
- **Author:** maintainer
- **Created:** 2026-05-20
- **Discussion:** PR opened against `main`
- **Resolves to:** ADR(s) for the identity primitive kind + executor
  argv contract, one or more specs under `docs/specs/` (default
  identities per recipe; `wiki list agents` / doctor coverage), and
  a vault-side `wiki-agent` SKILL.md.

## Summary

RFC-0003 closed the scheduling gap: `wiki run --exec` now fires
operation primitives headlessly on a cadence via the user's locally
installed Claude CLI. Every scheduled run, however, wears the same
generic persona. The leverage of the Torres pattern this kit is
built around — *My Team of Agents* — comes from *identity*: a
"Household Manager" that knows the family's vocabulary and standing
context is qualitatively different from a generic assistant that
happens to be running the `weekly-digest` skill. Identity carries
role, audience, tone, and what-it-knows that a one-shot SKILL
invocation can't.

This RFC proposes a third primitive kind — **agent** — parallel to
skills and operations. Agents are vault-side markdown files
(`AGENT.md`) installed by `wiki init` alongside skills. They compose
with skills at dispatch time ("Household Manager runs the
weekly-digest skill") via the existing shim executor. No new runtime
dependency; no kit-side persona embedding; the kit ships files and
the user's Claude reads them.

## Motivation

The scheduling RFC accepted the design constraint that operations
fire on a cadence without an operator at the terminal. The next
constraint, surfaced by users running RFC-0003 in anger, is that
scheduled operations need an identity to be useful:

> Inspiration: Teresa Torres, *My Team of Agents*
> ([producttalk.org/my-team-of-agents](https://www.producttalk.org/my-team-of-agents))
> — distinct personas (Podcast Manager, Research Manager) carry
> standing context that a one-shot prompt can't. The leverage isn't
> the schedule; it's the *who* the schedule wakes up.

Three concrete gaps motivate this RFC:

1. **Skills describe *what to do*; operations describe *what to
   produce*. Neither describes *who is doing it.*** A SKILL.md is
   instructions; an `AGENT.md` is a perspective. Today the kit has
   only the former. The weekly-digest SKILL produces a digest; the
   "Household Manager" agent produces it *for this family, in their
   voice, knowing they care about the grandparents' visit on the
   17th*. There is no place for that standing context to live.
1. **The scheduled-run UX names the wrong actor.** When a scheduled
   `weekly-digest` runs and writes a `.proposed` sidecar, the
   `wiki-conflict` skill surfaces it as "the kit wants to overwrite
   your notes." That framing is correct mechanically and wrong
   experientially: from the user's perspective, an *agent* (the
   Household Manager) is proposing a change. Conflict-resolution UX
   reads very differently when it can name the agent that proposed
   the write.
1. **Audience-specific kits have audience-specific roles.** The
   three shipped recipes (`family`, `work-os`, `personal`) cover
   distinct audiences but every recipe's scheduled operations run
   under the same anonymous shim today. The recipes already encode
   audience-specific primitives (medical, customers, identity); they
   should also encode audience-specific *agents* that wear those
   primitives.

This RFC does not propose multi-agent coordination, learning agents,
or persona marketplaces. It proposes the smallest set of primitives
that gives scheduled operations a name and a perspective.

## Decisions already made (and out of scope to relitigate)

These four were settled before drafting and are stated here so review
focuses on the open questions:

1. **Identities are a new primitive kind**, parallel to skills and
   operations — not a field tacked onto either. `PrimitiveKind` gains
   an `AGENT` value. Agents compose with skills at dispatch time;
   they do not extend or replace either existing kind. Restructuring
   the existing catalog is **out of scope**.
1. **Vault-side, not kit-side.** Agents live under
   `core/files/agents/<name>/AGENT.md` (and
   `templates/agents/<name>/files/agents/<name>/AGENT.md` for
   primitive-shipped ones, mirroring how operations ship their
   SKILLs). `wiki init` copies them into the user's vault at
   `<vault>/.claude/agents/<name>/AGENT.md`, where the user's Claude
   session reads them. They do **not** live at the repo-root
   `.claude/agents/` — that path is reserved for kit-development
   subagents (adversarial-reviewer, quality-engineer, …) and the two
   scopes never mix (see [`AGENTS.md` § Two scopes, one
   repo](../../AGENTS.md#two-scopes-one-repo)).
1. **No new runtime dep.** Identity composition happens in the shim
   executor's argv assembly. The kit emits a `claude` invocation that
   references the agent file; it does **not** load, parse, or embed
   the agent body in Python. Pure file plumbing on the kit side.
1. **Library-not-application stays intact.** The kit ships identity
   *files*; the user's Claude reads them. The kit does not embed
   personas in code, does not host a persona registry, and does not
   sync agents between vaults. (CHARTER principles 3 and 5.)

## Proposal

Six concrete additions, plus integration into the existing scheduling
and conflict surfaces.

### 1. The `agent` primitive kind

**Decision:** add `AGENT = "agent"` to `PrimitiveKind` in
`models.py`. Discovery walks
`templates/agents/<name>/primitive.yaml` the same way it walks
`templates/operations/<name>/primitive.yaml` today. `_CATALOG_DIRS`
in `primitives.py` gains an `"agents"` entry.

The `primitive.yaml` for an agent is the same shape as any other
primitive (`name`, `kind: agent`, `version`, `description`,
`requires`, `contributes_to`, `config`). An agent primitive's
`files/agents/<name>/AGENT.md` is the body the user's Claude
actually reads — analogous to how an operation primitive ships its
SKILL via `files/skills/<skill>/SKILL.md`.

`AGENT.md` frontmatter, matching SKILL.md conventions for
consistency:

```yaml
---
name: household-manager
description: >-
  Coordinates household operations — meal planning, follow-ups,
  medical summaries, weekly digests — in the voice of a family-side
  operator who knows the household's standing context.
license: MIT
audience: family            # enum: family | work-os | personal | shared
role: coordinator           # free-text role label, shown in conflict UX
tone: warm, brief, direct   # free-text tone label
knows:                      # vault pages the agent treats as standing context
  - identity.md
  - people/index.md
  - dashboards/follow-ups.md
---

# Household Manager

You are the Household Manager for this vault. Your job is to keep
recurring household operations coherent across runs — you are the
*who*, not the *what*. When you run a SKILL, you wear that SKILL's
instructions but you bring this perspective:

- You know the people in `people/` and refer to them by name.
- You prefer short, direct prose; you do not pad summaries.
- When you propose a write that conflicts, you surface as
  "the Household Manager proposed this," not "the kit proposed this."

…
```

The body below the frontmatter is freeform prose, like SKILL.md. The
`knows:` list is a hint to Claude about which pages to load as
context at run time; it is **not** enforced by kit code (the kit
does not read agent bodies).

### 2. Where agents live on disk

**Decision:** mirror the skill/operation layout exactly.

```
core/
  files/
    agents/                       ← default identities (audience: shared)
      <name>/
        AGENT.md
templates/
  agents/                         ← catalog identities (audience-specific)
    <name>/
      primitive.yaml
      files/
        agents/
          <name>/
            AGENT.md
```

Installed into a user's vault by `wiki init` at:

```
<vault>/.claude/agents/<name>/AGENT.md
```

This is the canonical Claude Code agent path inside the vault, and
the same shape `.claude/skills/<skill>/SKILL.md` already uses. The
user's Claude discovers agents the same way it discovers skills.

The repo-root `.claude/agents/` directory (kit-development
subagents) is unaffected and never written by `wiki init` — they
are different scopes by [`AGENTS.md`](../../AGENTS.md) decree.

### 3. How identities and operations bind

This is the design fork the RFC must pick. Three options were
considered:

- **(a) Operation-declared preferred agent.** Operation
  `contract.yaml` grows a `preferred_agent:` field. `wiki run
  weekly-digest` defaults to `preferred_agent`; user can override
  with `--agent`.
- **(b) Recipe-declared pairs.** `recipes/family.yaml` grows an
  `agents:` block mapping operations to agents. The recipe is the
  composition layer; agents are part of that composition.
- **(c) Schedule-declared at install.** The user names the agent at
  `wiki schedule install` time and the schedule entry carries it
  through to the OS artifact.

**Chosen primary path: (b), with (a) as the per-primitive fallback
and (c) as the per-install override.**

Rationale: the recipe is already where audiences become coherent.
The `family` recipe declares that this vault is a household;
declaring "the Household Manager runs `weekly-digest` and
`meal-planning`; the Trip Planner runs `trip-prep`" in the same
file keeps audience composition in one place. Operation primitives
declaring a default lets primitive authors ship a sensible
single-agent fallback for vaults that haven't customized; the
schedule override is the escape hatch for users who want a
different agent firing the same operation on a different cadence.

The `family.yaml` recipe under this RFC grows:

```yaml
agents:
  household-manager:
    runs:
      - weekly-digest
      - meal-planning
      - follow-up-tracker
  trip-planner:
    runs:
      - trip-prep
```

Operation `contract.yaml` grows one optional field:

```yaml
preferred_agent: household-manager   # nullable; falls back to no agent
```

`wiki schedule install <op> --agent <name>` overrides both for that
schedule entry only and journals the agent name on the
`ScheduleInstalledEvent` (additive field per ADR-0002).

### 4. Executor argv shape

**Decision:** the shim concatenates the agent file path into the
`claude` invocation via a documented flag. The kit does **not**
parse or embed the agent body; the user's Claude does.

Two argv shapes are on the table, depending on the Claude CLI
version we target:

- **`claude --agent <path>`** if the CLI exposes a first-class
  `--agent` flag at the version we pin against. This is the
  preferred path; the kit emits one flag and the CLI handles
  loading.
- **`claude --system-prompt-file <path>`** (or equivalent) as the
  fallback. Less semantically clean but works on older CLI
  versions.

The exact flag set is pinned in a follow-on ADR — same approach
RFC-0003 took for its own headless flag set ("pin against a
documented minimum version in a follow-on ADR rather than freezing
in this RFC"). The kit's *interface* is stable here:

```
wiki run <operation> --exec [--agent <name>]
```

Resolution order when `--agent` is not passed: schedule-entry agent
→ recipe-declared `agents:<>:runs` mapping → operation's
`preferred_agent` → no agent (today's behavior). The kit translates
the resolved agent name to a path
(`<vault>/.claude/agents/<name>/AGENT.md`) and appends the
appropriate flag to the `claude` invocation.

When no agent resolves, the kit invokes `claude` exactly as
RFC-0003 specifies today — preserving the no-agent default for
backward compatibility.

### 5. New journal events and field extensions

Additive per ADR-0002:

- **No new install event.** Agents are installed via the existing
  `PrimitiveInstallEvent`, the same way ontology, content-type,
  operation, and infrastructure primitives are. The kind is
  recoverable from the primitive name via the installed catalog,
  so a separate `AgentInstallEvent` discriminator would be pure
  ceremony — `wiki list agents` filters the primitive set by kind
  at replay time.
- `operation.run_by_agent` — recorded by `wiki run --exec` *only
  when an agent was resolved*. Fields: `operation`, `agent`,
  `event_id` (of the paired `OperationRunEvent`). Stays additive
  rather than extending `OperationRunEvent` for the same
  replay-assertion safety reason RFC-0003 §4 picked
  `ScheduledRunEvent` over a `triggered_by` field. Also recorded
  by `wiki run <op>` *without* `--exec` if the user passed
  `--agent` — manual invocations get the same audit tag as
  scheduled ones, even though the kit doesn't pass the agent
  anywhere on the dispatch-only path.

`ScheduleInstalledEvent` (from RFC-0003) gains one optional field
with default:

- `agent: str | None = None` — the agent name resolved at install
  time, or `None` if no agent was bound. Existing schedule entries
  replay unchanged.

`PageProposalEvent` gains one optional field with default:

- `proposed_by_agent: str | None = None` — the agent name (if any)
  that wore the run that produced the proposal. Drives the
  agent-aware conflict UX in §6. Existing proposal events replay
  unchanged.

### 6. Conflict-aware UX naming the agent

**Decision:** when the `wiki-conflict` SKILL surfaces a `.proposed`
file that carries `proposed_by_agent`, it names the agent in the
explanation. Mechanism: the SKILL reads the most recent
`PageProposalEvent` for the path, picks up `proposed_by_agent` if
present, and renders:

> "The Household Manager wants to update your weekly digest. You
> edited the action-items section since the last run; the Household
> Manager proposes replacing the digest body without touching your
> additions. Keep yours, take the proposal, or merge?"

vs. today's:

> "The kit wants to update your weekly digest. …"

This is a SKILL-side change, not a kit-side change — the kit just
makes the agent name available via the journal field. The SKILL
update lands in the same PR sequence (one of the migration tasks).

### 7. Discovery: `wiki list agents` and `wiki doctor`

`wiki list agents` lists installed agents (one per
`PrimitiveInstallEvent` with `kind: agent` and no later remove
event), with the recipe and operations each agent runs. Output is
short — name, audience, role, operations bound.

`wiki schedule list` (from RFC-0003) gains an **Agent** column
showing the resolved agent for each schedule entry. Without it,
"who runs `weekly-digest` on Sunday at 9am" requires reading the
schedule entry, the recipe's `agents:` block, and the operation's
`preferred_agent:`. No schema change — the column reads the
agent already journaled on `ScheduleInstalledEvent`.

`wiki doctor` gains two checks (additive to RFC-0003's three):

4. **Agent bindings.** For each operation in any schedule entry's
   resolved agent chain, verify the agent's `AGENT.md` exists at
   the journaled path. Missing → drift, with the same one-line
   fix suggestion shape as RFC-0003 (`wiki add agent:<name>` or
   re-run `wiki init`).
5. **Bound-agent version drift.** When an agent primitive has been
   upgraded (a `PrimitiveUpgradeEvent` for `kind: agent`) since the
   most recent `OperationRunByAgentEvent` referencing it, surface a
   warning naming the agent, the old/new versions, and the
   operations it's bound to. Rationale: an upgrade can change
   `role:`, `tone:`, or `knows:` in ways the user wants to read
   before the next scheduled run. `wiki upgrade` itself does not
   rebind silently — the journal-side rebinding is a no-op, but the
   doctor warning prompts the user to review the persona change.

### 8. Default identities per recipe

A small v1 catalog. The numbers are deliberately tight — agents
*compose* across operations, so two well-shaped agents cover most
of a recipe's surface.

**`family` (3 agents):**

- `household-manager` — runs `weekly-digest`, `meal-planning`,
  `follow-up-tracker`. Voice: warm, brief, knows the people graph.
- `trip-planner` — runs `trip-prep`. Voice: practical, checklisty,
  knows trip docs and travel preferences.
- `care-coordinator` — runs `medical-summary`. Voice: careful,
  precise, knows medical records. Opt-in only (medical is opt-in
  per the family recipe's existing posture).

**`work-os` (3 agents):**

- `stakeholder-steward` — runs `stakeholder-map-refresh`,
  `status-synthesis`. Voice: concise, executive-summary-shaped,
  knows the people and projects graphs.
- `renewals-watch` — runs `renewal-reminders`. Voice: deadline-
  focused, knows the vendor-contract surface.
- `customer-listener` — runs `action-item-rollup` over the
  customer-feedback corpus. Voice: theme-extracting, knows the
  customer-feedback content-type.

**`personal` (2 agents):**

- `personal-coordinator` — runs `weekly-digest`,
  `follow-up-tracker`, `meal-planning`. Voice: matches the
  `identity.md` page's tone (the personal recipe's identity
  primitive already captures owner preferences). Reads the
  `identity` ontology for standing context.
- `decision-companion` — available for `wiki run decision`-shaped
  reviews; no scheduled operation today. Reserved for a future
  `decision-review` operation.

Each default agent ships as a primitive under `templates/agents/`
with its own `primitive.yaml`, so users can opt out, version, or
replace them like any other primitive.

### What stays the same

- The scheduling and executor surface from RFC-0003 is **unchanged**
  except for the additive `--agent` flag and the additive journal
  fields. The `wiki run --exec` contract, OS-artifact emission, and
  conflict-aware refusal all behave identically when no agent is
  resolved.
- Existing SKILLs are unchanged. An agent does not modify a SKILL;
  it wears it. SKILL.md authors don't need to know about agents.
- No new runtime dependency. `pyyaml`, `pydantic>=2`, stdlib remain
  the runtime closure.
- The repo-root `.claude/agents/` directory (kit-development
  subagents) is unaffected. Repeat for emphasis: this scope does
  not mix with vault-side agents.
- Vaults that never opt in to an agent see no behavioral change.

### Migration path

On acceptance, this RFC produces a numbered task sequence (probably
seven tasks):

1. `PrimitiveKind.AGENT` + `_CATALOG_DIRS` entry +
   `OperationRunByAgentEvent` in `models.py`. (No new install
   event — agents reuse `PrimitiveInstallEvent`.)
2. Optional fields on `ScheduleInstalledEvent` and
   `PageProposalEvent` (additive; replay tests).
3. Agent discovery + install plumbing in `primitives.py` and the
   installer (mirrors operation install).
4. Recipe schema extension (`agents:` block) + recipe loader update.
5. Executor agent-resolution + argv extension in `wiki run --exec`.
6. `wiki list agents` + `wiki doctor` agent-binding check.
7. Default agent catalog (the eight `templates/agents/*` entries
   listed in §8) + vault-side `wiki-conflict` SKILL update to
   surface the agent name.

Sequencing: 1 and 2 land first; 3 and 4 are the load-bearing pair;
5 depends on 1, 2, 3, 4; 6 depends on 3; 7 depends on everything
prior. The vault-side SKILL update for agent-aware conflict UX
piggybacks on task 7.

### Compatibility

Existing vaults keep working untouched. Schedules installed before
this RFC's tasks land have no `agent` field on their
`ScheduleInstalledEvent` and resolve to "no agent" — exactly today's
behavior. Operation `contract.yaml` files that don't declare
`preferred_agent` keep their current shape; the field is optional
with default `None`. The additive journal fields preserve replay
over older journals (ADR-0002).

## Alternatives

### Alt 1: Identity as a field on operation primitives

Tack a `persona:` block on `contract.yaml` (a name + a body of
prose) and call it done. Rejected for three reasons:

- Personas compose across operations. The Household Manager runs
  three different operations; embedding the persona in each
  operation forces duplication and drift.
- Personas are not operation-shaped. They have audience, tone,
  what-they-know — fields that don't belong on a contract that
  describes inputs and outputs.
- The primitive catalog already distinguishes ontology from
  content-type from operation. Identity is a fourth shape, not a
  field on an existing one.

### Alt 2: Identity as a SKILL-level decorator

Make agents a kind of SKILL that "wraps" another SKILL. Rejected:
SKILLs describe instructions, not perspective. Wrapping creates a
nested-instruction surface that's harder for Claude to reason about
than a parallel `--agent` flag at invocation time. Also breaks the
"SKILLs are leaves" mental model the kit ships today.

### Alt 3: Identity embedded in recipes only (no primitive kind)

The recipe is the only place that knows about agents; there's no
catalog, no `templates/agents/`, no `wiki add agent:<name>`. Each
recipe ships its agents as inline YAML. Rejected: agents should be
swappable. A user on the `family` recipe should be able to install
the `decision-companion` agent from the `personal` recipe without
copying YAML between files. The primitive-kind framing gives
agents the same install/upgrade/remove surface every other
primitive has.

### Alt 4: Multi-agent coordination from the start

Let agents call other agents (the Household Manager delegates to
the Care Coordinator for a medical follow-up). Rejected for v1:
the coordination protocol is its own design problem, the scheduling
RFC explicitly defers it, and one-agent-per-run is sufficient to
get the leverage we're after. A follow-up RFC can layer
coordination on top.

### Alt 5: Inaction — generic invocations stay generic

The honest baseline. Rejected: the UX problem in Motivation §2
(conflicts named "the kit" instead of "the Household Manager") is
real today, and the Torres pattern's leverage stays out of reach
without a way to name an actor. Inaction ships a scheduling story
that works mechanically and reads thinly.

## Drawbacks

- **A fourth primitive kind expands the catalog surface.** Every
  `wiki doctor` check, every install path, every test fixture
  multiplies by the kinds count. Mitigation: the agent kind is
  shaped exactly like the existing kinds, so the multiplication is
  mechanical rather than design-fresh.
- **A small catalog of default agents that we have to maintain.**
  Eight entries across three recipes. Each agent's prose body is a
  surface that drifts as the kit's own conventions evolve; a stale
  Household Manager that still references a deprecated SKILL is a
  bug. Mitigation: agents version with the primitive contract;
  `wiki upgrade` surfaces stale agents like any other primitive.
- **Personas tempt scope creep.** Once we ship "the Household
  Manager," users will ask for "make my Household Manager remember
  last week's grocery preferences." That's identity *learning* —
  out of scope here (see Non-goals) but the request shape is
  predictable. Mitigation: be explicit in the `wiki-agent` SKILL
  and the user docs that agents are static files; users wanting
  memory should rely on vault pages (`identity.md`, `dashboards/*`)
  that they edit.
- **Agent-aware conflict UX adds a code path to wiki-conflict.**
  The SKILL needs to handle both "this proposal has an agent" and
  "this proposal does not" cleanly. Mitigation: the no-agent path
  is exactly today's UX; only the agent path is new.
- **The argv shape depends on the Claude CLI's agent flag
  stability.** If the flag we target gets renamed in a future CLI
  release, the kit's invocation breaks for users who upgrade their
  `claude` binary. Mitigation: pin the flag-set in a follow-on ADR
  (same approach RFC-0003 took); the kit can detect-and-fall-back
  on version mismatch.

## Non-goals (out of scope; do not bury in the proposal)

Called out explicitly so reviewers can hold the boundary:

- **Multi-agent coordination / agents-calling-agents.** v1 is one
  identity per scheduled invocation. Coordination protocols are a
  separate RFC.
- **Identity learning** — agents that update their own AGENT.md
  based on feedback or vault contents. Static files only.
- **Replacing skills with identities** or any restructuring of the
  existing primitive catalog. The four-kinds shape is the
  expansion, not a rewrite.
- **Hosting personas externally** — a registry, a marketplace,
  shared identity bundles. The kit ships files; users can fork.
- **Any change to the RFC-0003 scheduling/executor surface beyond
  the additive `--agent` flag and additive journal fields.** If
  reviewers find a load-bearing change is needed there, amend
  RFC-0003, don't bury it here.

## Resolved before review

Six open questions were raised during drafting and resolved with
the author before this RFC opened for review. Captured here as a
trace of where the proposal could have forked, so reviewers can
push back if any resolution looks wrong:

1. **`PrimitiveInstallEvent` covers all kinds; no separate
   `AgentInstallEvent`.** The kind is recoverable from the
   installed catalog, so a dedicated discriminator was ceremony.
   `wiki list agents` filters by kind at replay time. (Folded
   into §5.)
1. **`audience:` is an enum.** `family | work-os | personal |
   shared` matches the three shipped recipes plus a catch-all.
   Free-text was rejected for v1 because doctor can't catch
   typos; revisit when a fourth recipe lands or someone forks.
   (Folded into §1.)
1. **`wiki schedule list` shows the resolved agent.** New
   **Agent** column, reads the field already journaled on
   `ScheduleInstalledEvent`. (Folded into §7.)
1. **Agent-specific config lives in vault pages, not on the
   agent.** CHARTER principle 4 — vault state belongs in
   journal/pages. Defer per-agent config until a user actually
   asks. (Stated in the §6 / §1 framing; no new surface needed.)
1. **Conflict UX reads the *latest* proposal's
   `proposed_by_agent`.** The edge case isn't recipe-switching
   (the kit has no "switch recipe" verb) — it's a user
   `wiki add`-ing a different agent and rebinding an operation,
   or `wiki upgrade` swapping the bound agent. Either way, the
   merge is about the latest conflict, not the page's identity
   history. (Folded into §6.)
1. **`wiki run <op>` (without `--exec`) accepts `--agent` and
   journals it.** The flag is meaningless on the dispatch-only
   path (no `claude` invocation), but tagging the audit trail
   keeps manual and scheduled runs symmetric. (Folded into §5.)

## Unresolved questions

One item deferred to a follow-on ADR rather than frozen in this RFC:

- **Claude CLI flag set for agent passthrough.** The exact flag
  (`--agent <path>` vs `--system-prompt-file <path>` vs something
  else) is a moving target in the Claude CLI. Pin against a
  documented minimum version in a follow-on ADR rather than
  freezing in this RFC. Same approach RFC-0003 took for its own
  headless flag set.

## Outcome

**Accepted 2026-05-20.** This RFC produces:

- An ADR pinning the Claude CLI agent-passthrough flag set
  (resolves the one item in Unresolved questions) against a
  documented minimum CLI version.
- A follow-on spec sequence under `docs/specs/wiki-agents/`
  covering the seven migration-path tasks in §"Migration path":
  primitive-kind plumbing, additive journal fields, recipe schema
  extension, executor agent resolution, `wiki list agents` +
  doctor coverage, and the default agent catalog.
- A vault-side `wiki-agent` SKILL.md teaching Claude when to
  prompt the user about installing or rebinding an agent, and
  how the agent-aware `wiki-conflict` UX reads.

Multi-agent coordination, if it ever lands, comes in a separate
RFC that names this one as its predecessor.
