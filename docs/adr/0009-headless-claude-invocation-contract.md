# ADR-0009: `wiki run --exec` invokes `claude -p` with a pinned argv shape

- **Status:** Accepted
- **Date:** 2026-05-21
- **Deciders:** maintainer
- **Related:** RFC-0003 §3 (executor shim — argv shape deferred to a
  follow-on ADR); RFC-0003 §"Decisions already made" #1 (shim
  executor, not SDK); ADR-0002 (journal as state truth);
  [`AGENTS.md` § Runtime dependencies](../../AGENTS.md#runtime-dependencies)
  (no new runtime dep without ADR); ADR-0010 (agent passthrough,
  extends this contract).

## Context

RFC-0003 §3 ships `wiki run --exec` as a shim that "invokes the
user's own locally-installed `claude` CLI in headless mode and
journals the outcome." The exact argv shape was deliberately
deferred:

> The exact flag set for headless mode is a moving target in the
> Claude CLI. Pin against a documented minimum version in a
> follow-on ADR rather than freezing in this RFC.
> *(RFC-0003 §"Unresolved questions")*

That deferral was correct for the RFC layer — the proposal is
about *whether* to shim out to `claude`, not *how*. But
implementation cannot proceed until the argv shape is pinned: the
schedule artifacts (launchd plist, systemd unit, Task Scheduler
XML) are projections of the argv string, and changing the string
after schedules are installed would invalidate every projection on
disk.

Three forces drive the design:

**The CLI's headless surface has converged.** `claude -p` (print
mode) is the documented non-interactive entrypoint as of the
currently-installed CLI on the maintainer's machine
(`claude --help` output captured 2026-05-21). Around it sit the
flags this contract needs:
`--add-dir`, `--permission-mode`, `--output-format`,
`--max-budget-usd`, `--system-prompt`, `--append-system-prompt`,
`--agent` (used by ADR-0010), and `--bare`. None of these are
experimental in the help output; all are documented in the CLI's
main help screen.

**Scheduled runs run unattended, so permission prompts must be
suppressed.** A scheduled launchd-fired `wiki run --exec` has no
TTY to prompt against. The CLI's `--permission-mode` choices are
`acceptEdits | auto | bypassPermissions | default | dontAsk |
plan`. `dontAsk` is the right shape: it skips confirmation
prompts without granting the dangerous-bypass posture
`bypassPermissions` implies.

**The dispatch event id rides in the prompt, not a flag.**
RFC-0003 §3 step 2 says "the journaled event id (so the SKILL
can append to the same conversation thread and the journal
entries chain back to the scheduled run)." An earlier draft of
this ADR pinned that chaining on the CLI's `--session-id` flag,
which would have required generating a UUID per dispatch and
journaling it. That conflicts with
[`docs/specs/wiki-run-exec/spec.md`](../specs/wiki-run-exec/spec.md)
§"Non-goals" line 671 — "No change to the dispatch-event shape.
`OperationRunEvent` is untouched" — an invariant the spec went
through four adversarial-review rounds to pin. So this ADR uses
the prompt text as the carrier: the kit inlines the
`dispatch_event_id` the spec already journals (any opaque string
identifier the kit uses internally — not necessarily a UUID), and
the SKILL reads it from the prompt. No new schema, no new flag,
no CLI-session-vs-journal-event mismatch.

The kit must not parse Claude's stdout for semantics — the
charter pins library-not-application, and parsing the model's
prose is the kit becoming an inference layer in disguise. The
CLI's `--output-format json` returns a structured envelope the
kit can read for exit-time metadata (token counts, cost) without
parsing the model's actual content.

## Decision

> **`wiki run --exec` invokes the user-installed `claude` CLI in
> print mode (`-p`) with a fixed argv shape: `--add-dir <vault>
> --permission-mode dontAsk --output-format json` plus an
> optional `--max-budget-usd <cap>` when the vault config
> supplies one. The operation's prompt is passed as the trailing
> positional argument and carries the dispatch event id inline.
> Minimum CLI version is pinned in the kit's `wiki doctor`
> check; older versions fail with a single-line error pointing
> at install instructions.**

The fixed argv shape, in order:

```
claude -p                                  # headless / print mode
  --add-dir <vault>                        # vault tool access
  --permission-mode dontAsk                # suppress prompts
  --output-format json                     # parseable exit envelope
  [--max-budget-usd <cap>]                 # optional budget cap
  [--agent <name>]                         # added by ADR-0010
  <prompt>                                 # trailing positional
```

`<prompt>` is constructed by the kit from a fixed template that
names the operation, points Claude at the SKILL, and inlines the
spec-side `dispatch_event_id` so the SKILL can chain its work
back to the same journal entry:

```
Run the `<operation>` skill against this vault. The operation's
contract is at templates/operations/<operation>/contract.yaml.
The dispatch event id for this run is <dispatch_event_id>. On
completion, write produced pages via `wiki resolve` and exit.
```

The prompt body is **not** part of this ADR's contract — it can
evolve as the SKILL-side conventions evolve. What is pinned is
*the surface* (positional prompt argument, not stdin), so the
schedule artifacts don't need to change shape when the prompt
text changes.

**Minimum CLI version.** Pinned at the version that ships all of
`-p`, `--add-dir`, `--permission-mode dontAsk`, `--output-format
json`, `--max-budget-usd`, and `--agent`. The
specific version string is captured by `wiki doctor`'s
schedule-section check via `claude --version` at install time;
hardcoding a version in this ADR would rot. The check fails
*open* with a one-line message if the CLI is older than the
minimum, pointing at `https://docs.claude.com/...` install
instructions.

**What this ADR does not cover.** The prompt template body (free
to evolve); the SKILL's response shape (vault-side, separate
contract); error-recovery semantics when `claude` exits non-zero
(covered by RFC-0003 §3 step 4's `OperationExecFailedEvent`);
agent passthrough (ADR-0010); the wheel-acceptance test that
exercises a real `claude` binary (covered by the implementation
spec).

## Consequences

### Positive

- Schedule artifacts (launchd plist, systemd unit, Task
  Scheduler XML) have a stable argv string they can template.
  Adding a flag to the contract requires a new ADR (or an
  amendment to this one) and a coordinated bump of the
  artifact emitter.
- The kit never parses Claude's prose output. Exit-time
  metadata flows through `--output-format json`, which is
  structured and versioned by the CLI.
- The dispatch event id rides in the prompt text, not a flag.
  `OperationRunEvent` stays untouched (per the spec's
  invariant), and the SKILL can chain its journal appends to
  the dispatch event by reading the id from the prompt — no
  CLI-session-vs-journal-event coordination problem.
- `--permission-mode dontAsk` is the conservative choice:
  unattended runs don't grant the `bypassPermissions` posture
  by default, so a malicious or buggy SKILL can't silently
  escalate against the rest of the user's machine.

### Negative

- The kit is now coupled to specific CLI flag names. If the
  CLI renames `--permission-mode` or drops `--output-format
  json` before the kit gates the version, every schedule
  artifact on disk breaks at next fire. Mitigated by the
  `wiki doctor` version check, but not eliminated.
- `--output-format json` has its own schema that the kit
  consumes. Schema drift in the CLI is now a kit failure
  surface. Mitigated by reading only the fields the kit
  needs (exit code, cost) and ignoring the rest.
- `dontAsk` permission-mode means a SKILL that genuinely
  needs to prompt the user (rare in operation contracts, but
  possible) silently no-ops the prompt. The
  `requires_interaction: bool` field RFC-0003 §Drawbacks
  flagged as a future operation-contract extension would
  catch this; until then, SKILL authors must assume no
  interactivity in scheduled runs.

### Neutral / monitor

- The CLI may add richer headless flags (structured plan
  output, multi-step session resumption, cost-budget-tier
  policies) that the kit could opt into. Revisit this ADR
  when (a) a CLI release ships a flag the kit's SKILL
  authors want, or (b) a CLI release deprecates one of the
  five flags pinned here.
- The prompt template is intentionally outside the ADR's
  scope. If prompt-shape changes start invalidating SKILL
  contracts, that signals the template needs its own
  contract — likely as a managed-region in
  `core/files/CORE.md` or a sibling kit-owned file.

## Alternatives considered

### Alt 1: Pass the prompt via stdin instead of as a positional argument

The CLI accepts both. Stdin avoids quoting/escaping issues for
prompts that contain shell metacharacters. Rejected: schedule
artifacts (launchd plists, systemd units) describe the argv
inline, and piping stdin from a templated artifact adds a shell
layer (`bash -c 'echo … | claude -p …'`) that the artifact
emitters would have to generate per-OS. Positional-arg keeps
the artifacts shell-free and the quoting concern is solved by
restricting the prompt template to ASCII-safe text the kit
controls.

### Alt 2: Use `--bare` mode for scheduled runs

`--bare` skips hooks, LSP, plugin sync, auto-memory, and
CLAUDE.md auto-discovery. Tempting for headless invocations:
strip everything the kit doesn't need. Rejected: scheduled runs
*do* benefit from CLAUDE.md auto-discovery (the vault's
`.claude/skills/<skill>/SKILL.md` and `.claude/agents/<name>/AGENT.md`
are exactly the kind of context `--bare` strips). Using `--bare`
would force the kit to manually wire every piece of context via
`--system-prompt-file` / `--add-dir`, which is more brittle than
letting the CLI do its normal discovery.

### Alt 3: `--permission-mode bypassPermissions`

Sidesteps every permission concern. Rejected: this grants the
SKILL's tool calls unconditional access to the user's machine.
The kit's threat model assumes a SKILL might have a bug — a
typo, a prompt-injection in ingested content — and giving every
scheduled run unrestricted permissions is an unacceptable
default. `dontAsk` is the smaller hammer.

### Alt 4: Inline custom agents via `--agents <json>` instead of vault files

The CLI's `--agents <json>` flag accepts inline agent definitions.
The kit could read `AGENT.md` files and inline them into the
argv. Rejected: violates "the kit ships files; the user's
Claude reads them" (CHARTER principle 5). Inlining means the kit
becomes a persona-embedding layer, which is the thing the RFC-0004
"library-not-application" decision (RFC-0004 §"Decisions already
made" #4) explicitly rejected. The kit emits `--agent <name>` and
the CLI handles loading — see ADR-0010.

### Alt 5: Defer the ADR until the implementation lands

Write the executor, see what shape settles, then document it
backward. Rejected: the schedule artifacts (launchd plists,
etc.) are user-installed and journaled at install time. Changing
the argv shape after schedules are in the field would invalidate
every plist on disk, with no clean migration. The ADR has to lead
the implementation, not trail it.

## References

- RFC-0003 §3 "The executor: `wiki run --exec`"
- RFC-0003 §"Unresolved questions" — "Headless `claude`
  invocation flags"
- `claude --help` output captured 2026-05-21 (the source of
  truth for flag names; not pasted here because it rots).
- ADR-0010 (agent passthrough, depends on this ADR).
- [`AGENTS.md` § Runtime dependencies](../../AGENTS.md#runtime-dependencies)
  — why this is a shim rather than an SDK call.
