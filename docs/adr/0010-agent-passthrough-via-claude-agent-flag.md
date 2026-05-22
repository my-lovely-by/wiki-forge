# ADR-0010: `wiki run --exec --agent <name>` passes through via `claude --agent`

- **Status:** Accepted
- **Date:** 2026-05-21
- **Deciders:** maintainer
- **Related:** RFC-0004 §4 (executor argv shape — exact flag
  deferred to a follow-on ADR); RFC-0004 §"Decisions already made"
  #4 (library-not-application: the kit ships files, not embedded
  personas); ADR-0009 (headless invocation contract this ADR
  extends); ADR-0002 (journal as state truth).

## Context

RFC-0004 §4 ships agent passthrough for scheduled operations.
The exact CLI flag was deferred:

> The exact flag set is pinned in a follow-on ADR — same approach
> RFC-0003 took for its own headless flag set ("pin against a
> documented minimum version in a follow-on ADR rather than
> freezing in this RFC").
> *(RFC-0004 §4)*

Two flag-shape options were on the table in the RFC:

> - **`claude --agent <path>`** if the CLI exposes a first-class
>   `--agent` flag at the version we pin against. This is the
>   preferred path; the kit emits one flag and the CLI handles
>   loading.
> - **`claude --system-prompt-file <path>`** (or equivalent) as
>   the fallback. Less semantically clean but works on older CLI
>   versions.

The introspection done while drafting ADR-0009 settled which is
available: `claude --help` (output captured 2026-05-21) shows
`--agent <agent>` as a documented top-level flag — *"Agent for
the current session. Overrides the 'agent' setting."* The flag
takes an agent *name*, not a path; the CLI resolves the name
against the `.claude/agents/<name>/AGENT.md` discovery surface
in the working directory.

This means the "preferred path" the RFC named is also the
*available* path on the minimum CLI version ADR-0009 pins
against — there is no need to ship a fallback for v1.

Three forces drive the design:

**The CLI already does the right thing.** `--agent <name>` plus
the vault root passed via `--add-dir <vault>` (per ADR-0009)
gives the CLI everything it needs: a name to resolve and a
directory to resolve it under. The kit reads zero bytes of
`AGENT.md`. This preserves CHARTER principle 5
(library-not-application) without any additional design.

**The kit's resolution chain produces a name, not a path.**
RFC-0004 §4 specifies the resolution order: schedule-entry agent
→ recipe-declared `agents:<>:runs` mapping → operation's
`preferred_agent` → no agent. Every step in that chain produces
a name (an installed agent primitive's `name:` field) or
nothing. The flag's contract (name-not-path) matches the chain's
output exactly.

**Inlining the body is the alternative that loses on principle.**
The CLI also exposes `--agents <json>` for inline custom-agent
definitions and `--append-system-prompt-file <path>` for raw
system-prompt injection. Both work mechanically but both turn
the kit into a persona-embedding layer — which is the exact
posture RFC-0004 §"Decisions already made" #4 ruled out
("the kit ships identity *files*; the user's Claude reads them.
The kit does not embed personas in code…").

## Decision

> **When a scheduled `wiki run --exec` resolves an agent name
> from its resolution chain, the kit appends `--agent <name>` to
> the `claude` invocation defined in ADR-0009 — and nothing
> else. The CLI loads the agent body from the vault's
> `.claude/agents/<name>/AGENT.md` via its normal agent
> discovery. The kit reads zero bytes of `AGENT.md` at dispatch
> time.**

Concrete mechanics:

1. The kit's resolution chain (RFC-0004 §4) produces either a
   resolved agent name (string) or `None`.
2. When the resolved value is a name, the kit appends two
   tokens to the ADR-0009 argv list, immediately before the
   trailing prompt positional: `--agent` and the name.
3. When the resolved value is `None`, the argv is unchanged
   from ADR-0009. No `--agent` flag is appended; the CLI
   uses its default (no agent for the session).
4. **Name validation happens at dispatch time, not at install
   time.** Before exec, the kit replays the journal and
   verifies the resolved name corresponds to a
   currently-installed `kind: agent` primitive (a
   `PrimitiveInstallEvent` with no later
   `PrimitiveRemoveEvent`). If the agent is missing, the kit
   raises `WikiError("scheduled run resolved agent '<name>'
   but it is not installed; run 'wiki add agent:<name>' or
   re-run 'wiki init'")` and journals an
   `OperationExecFailedEvent` (RFC-0003 §3 step 4). The CLI
   is never invoked with a dangling name.
5. **The `AGENT.md` file's on-disk presence is checked by
   `wiki doctor`, not by exec.** RFC-0004 §7's "Agent
   bindings" doctor check is the periodic verifier; exec-time
   verification covers only the journal side (name installed
   per replay). A vault whose `.claude/agents/<name>/AGENT.md`
   was deleted between install and exec will get a CLI-side
   error message (from `claude --agent` failing to resolve)
   rather than a kit-side one — that's acceptable because
   `wiki doctor` would have already flagged the drift.

**What this ADR does not cover.** The CLI's internal resolution
of `--agent <name>` against `.claude/agents/` (CLI-side
contract, not kit's concern); the prompt template body (covered
by ADR-0009 §"What this ADR does not cover"); multi-agent
coordination (out of scope per RFC-0004 §Non-goals); fallback
flags for older CLI versions (not needed — ADR-0009 pins the
minimum version at one that already ships `--agent`).

## Consequences

### Positive

- One additional flag is the whole change. The kit's argv
  emitter, schedule artifacts, and doctor checks all stay
  shape-stable; adding agent passthrough is a single optional
  insertion before the prompt positional.
- The kit does not parse or embed `AGENT.md`. Persona
  content stays vault-side; the kit stays
  library-not-application.
- Agent updates are picked up automatically. If the user
  edits `<vault>/.claude/agents/household-manager/AGENT.md`,
  the next scheduled run reads the new body via the CLI's
  own discovery. No kit-side cache to invalidate.
- The name-vs-path distinction also gives users a clean
  override surface: `wiki schedule install <op> --agent
  <name>` writes the name into the schedule artifact, not a
  filesystem path that could rot if the vault moves.

### Negative

- Coupling to the CLI's `--agent` flag is now load-bearing.
  If a future CLI release renames or removes it, every
  schedule artifact with `--agent` in it breaks on next
  fire. Mitigated by the `wiki doctor` version check from
  ADR-0009, but a CLI breaking change between two checks
  still surfaces as exec failures.
- Dispatch-time validation only checks the *journal* (agent
  primitive installed). A vault whose `AGENT.md` was deleted
  manually but whose `PrimitiveInstallEvent` is still in the
  journal will pass the kit's check and fail at CLI time
  with a less-helpful error. Mitigated by `wiki doctor`'s
  agent-bindings check (RFC-0004 §7) — but only if the user
  runs doctor before the schedule fires.
- No fallback for older CLI versions means users on a CLI
  that pre-dates `--agent` cannot use scheduled identities
  at all. They get the no-agent default. Acceptable
  trade-off for v1: shipping a fallback would require the
  kit to read `AGENT.md` and inline it via
  `--append-system-prompt-file`, which is the exact
  embedding posture this ADR rejects.

### Neutral / monitor

- The CLI may evolve `--agent` semantics (e.g. accept a
  path instead of a name, support multiple agents per
  session). Revisit if (a) a CLI release changes the
  flag's input shape, or (b) a SKILL author asks for
  multi-agent support that would require coordination.
- `wiki schedule list`'s new Agent column (RFC-0004 §7)
  is the user-facing surface for "who runs this on the
  cadence." If users routinely have to grep through
  multiple sources to answer that question, the binding
  resolution chain may need to be flattened — defer until
  a user complains.

## Alternatives considered

### Alt 1: Pass a full path via `--system-prompt-file`

Read `AGENT.md`'s body, write it as the system prompt via
`--system-prompt-file <path>`. Rejected on principle (the kit
embedding personas) and on mechanics (loses the CLI's own
discovery, which knows how to merge agent context with the
SKILL's own context — replicating that in the kit would mean
re-implementing a CLI feature).

### Alt 2: Inline via `--agents <json>`

The CLI accepts inline JSON agent definitions. The kit could
read `AGENT.md`, parse its frontmatter, and emit JSON. Rejected
for the same library-not-application reason as Alt 1, plus an
additional cost: the kit would need a YAML-frontmatter → JSON
translator that the CLI's own discovery already provides for
free.

### Alt 3: Pin a flag fallback for older CLIs

Ship both `--agent <name>` (primary) and an
`--append-system-prompt-file <path>` fallback for users on
older CLIs, gated by a CLI-version probe. Rejected: ADR-0009's
minimum-version gate already requires a CLI that ships
`--agent`, so any vault whose `wiki doctor` passes has the
primary flag available. Adding a fallback would split the
test matrix and create a second invocation path that drifts
from the primary one.

### Alt 4: Resolve the name to a path in the kit, pass the path

Even with the CLI accepting names, the kit could resolve
`<name>` → `<vault>/.claude/agents/<name>/AGENT.md` and pass
the path explicitly via some path-accepting flag (the
hypothetical `--agent-file <path>`, which doesn't exist
today). Rejected: the CLI's `--agent` flag does this
resolution itself; duplicating the logic in the kit creates
two ways for "agent location" to drift apart, and breaks the
property that "users editing `AGENT.md` get the new body on
next run" without any kit-side cache.

## References

- RFC-0004 §4 "Executor argv shape"
- RFC-0004 §"Decisions already made" #4 (no persona embedding
  in code)
- RFC-0004 §"Resolved before review" — the six pre-review
  resolutions this ADR implements alongside.
- ADR-0009 — base headless invocation contract this ADR
  extends with one optional flag.
- `claude --help` output captured 2026-05-21 confirming
  `--agent <agent>` is documented at the pinned minimum
  version.
