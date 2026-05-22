# RFC-0003: scheduling and autonomous execution of operation primitives

- **Status:** Accepted
- **Author:** maintainer
- **Created:** 2026-05-20
- **Discussion:** PR opened against `main`
- **Resolves to:** ADR(s) for cadence vocabulary + executor shim, one or
  more specs under `docs/specs/`, follow-on tasks, and (eventually)
  a vault-side `wiki-schedule` SKILL.md

## Summary

Operation primitives already declare a cadence (`period: weekly`,
`period: daily`, …) but nothing fires them. Today `wiki run <op>` is a
human-attended dispatch: the kit journals an
`OperationRunEvent(status="dispatched")` and prints a pointer; a Claude
session has to be open for the SKILL to actually do the work. This RFC
proposes the two pieces that close the gap without violating
[`docs/CHARTER.md`](../CHARTER.md)'s "does not host or sync vaults" and
"does not include an LLM" boundaries:

1. A **schedule declaration** that lives in the vault, owned by the user,
   journaled like any other primitive contribution.
2. A **shim executor** — `wiki run --exec` — that invokes the user's
   own locally-installed `claude` CLI in headless mode and journals the
   outcome. No new runtime dependency, no kit-hosted daemon, no SDK.

The shape is deliberately conservative: schedules emit OS-native
artifacts (launchd plist on macOS today; systemd timer + Task Scheduler
XML on the roadmap), and `claude` is shelled out as a subprocess. The
kit remains a library, not a service.

## Motivation

The Karpathy/Torres pattern this kit is built around assumes operations
run on a cadence without an operator at the terminal:

> Inspiration: Teresa Torres, *My Team of Agents*
> ([producttalk.org/my-team-of-agents](https://www.producttalk.org/my-team-of-agents))
> — launchd + headless Claude + Obsidian task folders. An always-on
> machine fires the cron entry, headless Claude reads the prompt, and
> the wiki accumulates state between runs.

In the kit's current state, `period: weekly` on `weekly-digest` is
documentation, not behavior — the digest only lands when a human
remembers to type `wiki run weekly-digest` *and* opens Claude *and*
runs the SKILL. The integration tests prove this by mocking the SKILL
side entirely (see [`docs/specs/task-17-wiki-run/spec.md`](../specs/task-17-wiki-run/spec.md)
§"Non-goals" — "Executing the operation's work").

Three observations make this gap expensive:

1. **The cadence vocabulary is already on disk.** Every operation
   primitive's `primitive.yaml` carries `period:`. Recipes assemble a
   coherent cadence for a vault without anything firing it. The kit
   has been promising scheduled behavior since v2.0.0.
2. **Users own always-on machines.** The kit's audience — non-engineer
   professionals and families ([CHARTER.md § Mission](../CHARTER.md#mission))
   — typically has a laptop or desktop they leave on, and increasingly
   a Mac Mini or NUC. macOS launchd, systemd `--user` timers, and
   Windows Task Scheduler each provide the "fire this command at this
   time on this machine I own" primitive the kit needs.
3. **No competing surface exists.** There is no `wiki daemon`,
   no `wiki cron`, no kit-hosted scheduler today. Whatever this RFC
   ships is the only schedule story v2 will have. That argues for
   shipping the simplest viable surface, not a maximalist one.

This RFC does not propose hosting, cloud cron, GitHub Actions runners,
or a daemonized kit. It proposes the smallest set of primitives the
existing pattern needs to actually fire.

## Decisions already made (and out of scope to relitigate)

These three were settled before drafting and are stated here so review
focuses on the open questions:

1. **Shim executor, not SDK.** The kit shells out to a user-installed
   `claude` CLI in headless mode. No new runtime dependency. Users who
   don't have Claude Code installed get a clear error pointing them at
   install instructions. SDK integration (adding `anthropic` as a
   runtime dep) is **out of scope** for this RFC; it would need its own
   ADR per [`AGENTS.md` § Runtime dependencies](../../AGENTS.md#runtime-dependencies).
2. **Library-not-application stays intact.** The kit emits OS-native
   schedule artifacts (launchd plists / systemd timers / Task
   Scheduler XML) and invokes the user's local Claude binary. It does
   not host, daemonize, or sync. This preserves the CHARTER's
   "does not host or sync vaults" and the kit's
   library-not-application principle.
3. **Agent identity primitives are deferred to a follow-up RFC.** This
   RFC ships the schedule + executor only; identity/persona primitives
   (so different operations can run under different agent profiles)
   come later. Anything that grows the "who is the agent" surface
   stops at this RFC's boundary.

## Proposal

Three concrete additions, plus integration into existing surfaces.

### 1. Where schedule declarations live

**Decision:** in the journal, contributed by recipes and primitives,
materialised to OS-native artifacts on demand.

The two rejected alternatives, with their tradeoffs:

- *In the recipe directly.* Tempting — `recipes/family.yaml` could
  grow a `schedule:` block. Rejected: a recipe describes which
  primitives compose the vault; cadence belongs to the primitive
  (`weekly-digest`'s `period: weekly` already declares it). A
  recipe-level schedule would split cadence ownership across two
  files and break the existing `period:` source-of-truth.
- *A new `.wiki.schedule/` vault directory.* Rejected: parallel state
  with the journal, violating ADR-0002 (journal as state truth).
  Drift detection would then need a third surface.

**Chosen shape.** Each operation primitive's `contract.yaml` already
carries `period:`. The user (via `wiki schedule install <op>`, see
§3) journals one `ScheduleInstalledEvent` per (operation, machine)
pair. The journal is canonical; the launchd plist / systemd timer /
Task Scheduler XML is a *projection* of that journal entry written
under the user's home directory (`~/Library/LaunchAgents/`,
`~/.config/systemd/user/`, `%AppData%/Microsoft/Windows/...`). The
kit detects drift between journal and OS-side projection in `wiki
doctor` (see §5).

This keeps cadence ownership in the primitive, scheduling state in
the journal, and execution in the OS — exactly one source of truth
per concern.

### 2. Cadence vocabulary

**Decision:** keep `period:` as the primitive-level declaration; add
an opt-in `default_time:` field on operation primitives; let the user
override at `wiki schedule install` time via a restricted DSL. Do
**not** accept cron strings.

Rationale: the charter audience is non-engineers. `0 9 * * 0` parses
fine for an engineer and is hostile to everyone else. The restricted
DSL covers the actual cadence vocabulary operations declare:

```
period: daily            → default_time: 07:00
period: weekly           → default_day: SUN, default_time: 09:00
period: monthly          → default_day_of_month: 1, default_time: 09:00
period: quarterly        → first day of each quarter, 09:00
period: on-demand        → no automatic schedule (manual `wiki run` only)
```

User override at install time:

```
wiki schedule install weekly-digest           # uses primitive defaults
wiki schedule install weekly-digest --at="SUN 09:00"
wiki schedule install meal-planning --at="daily 06:30"
```

The grammar is intentionally tiny: `<DAY> <HH:MM>` or `daily <HH:MM>`
or `monthly <DD> <HH:MM>`. Anything more expressive (multiple times
per day, last-day-of-month) waits until a user actually asks. We can
add cron-string acceptance later as a `--cron "<expr>"` escape hatch
without breaking this DSL; we cannot remove cron acceptance once
shipped.

`period: on-demand` is an explicit no-schedule signal so `wiki
schedule install` can refuse cleanly when a user picks an operation
that was never meant to run on a cadence.

### 3. The executor: `wiki run --exec`

**Decision:** extend `wiki run` with an `--exec` flag that locates a
user-installed `claude` binary and invokes it in headless mode against
the journaled dispatch.

Exact CLI shape:

```
wiki run <operation> [args ...] --exec
wiki run <operation> [args ...] --exec --claude-binary /usr/local/bin/claude
```

When `--exec` is set, after the normal dispatch sequence (validate
args, journal the `OperationRunEvent`), the kit:

1. Locates the `claude` binary. Resolution order: `--claude-binary`
   explicit path → `WIKI_CLAUDE_BINARY` env var → `shutil.which("claude")`.
   On none-of-the-above, raise `WikiError("--exec set but no claude
   binary found; install Claude Code or pass --claude-binary")`.
2. Constructs the headless invocation. The kit passes:
   - the SKILL path (`<vault>/.claude/skills/<skill>/SKILL.md` or
     wherever the SKILL was installed),
   - the operation name,
   - the journaled event id (so the SKILL can append to the same
     conversation thread and the journal entries chain back to the
     scheduled run).
3. Streams stdout / stderr to a per-run log under
   `.wiki.journal/exec-logs/<event-id>.log` (gitignored).
4. On non-zero exit: journals an `OperationExecFailedEvent` carrying
   the exit code, last 4KB of stderr, and a pointer to the full log.
5. Surfaces the failure to a vault-side `inbox/scheduled-failures.md`
   page so the user notices on next vault open. (This is the only
   place the kit writes outside the journal during exec; it routes
   through `safe_write` per ADR-0004, with the page treated as
   kit-owned-additive.)

Exec is opt-in per invocation. Schedules emit `wiki run --exec`
commands in their OS artifacts; an interactive `wiki run weekly-digest`
without `--exec` keeps the existing dispatch-only behavior.

### 4. New journal events

Additive per ADR-0002 — no existing event changes:

- `schedule.installed` — `ScheduleInstalledEvent` with fields:
  `operation`, `machine_id` (hostname), `os_artifact_path`,
  `cadence_dsl` (the resolved time string), `installed_at`.
- `schedule.uninstalled` — `ScheduleUninstalledEvent` with the same
  identifying fields plus `removed_artifact: bool`.
- `operation.exec_failed` — `OperationExecFailedEvent` with
  `operation`, `event_id` (of the dispatch this exec was paired with),
  `exit_code`, `stderr_tail`, `log_path`.

Extending `OperationRunEvent` with a per-run `triggered_by:
"scheduled" | "manual"` field is **considered but rejected** in
favour of a separate `ScheduledRunEvent` wrapper. Rationale: the
additive-schema rule allows adding a field, but downstream replay
code that asserts on the existing field set is a known footgun
(Task 17's `args` / `error` extension already crossed that line; we
shouldn't keep crossing it). Open for review.

### 5. `wiki doctor` integration

`wiki doctor` gains three checks:

1. **Schedule presence.** For each `ScheduleInstalledEvent` that has
   no later `ScheduleUninstalledEvent`, verify the OS-side artifact
   exists at the journaled `os_artifact_path`. Missing → drift.
2. **Schedule liveness.** Where the OS allows it (launchd's `launchctl
   list`; systemd's `systemctl --user is-enabled`), check whether the
   schedule is loaded and enabled. Disabled-but-present → drift.
3. **Exec-failure backlog.** Count `OperationExecFailedEvent` entries
   in the last N days (N=7 default) that have no later
   `OperationRunEvent(status="dispatched")` for the same operation.
   Non-zero → surfaced as a warning, not a failure.

Drift surfaces as a `wiki doctor` warning with a one-line fix
suggestion (`wiki schedule install <op>` or `wiki schedule remove
<op>`); it does not silently re-install.

### 6. OS coverage

**Decision:** ship macOS launchd at v1; ship Linux systemd `--user`
timers at v1 if testable in CI without a real systemd instance,
otherwise flag as roadmap; ship Windows Task Scheduler XML emission
at v1 but mark the Windows path "best-effort" (no CI coverage).

Rationale: Torres has proven the launchd path end-to-end. systemd is
mechanically simple (two files: `.service` + `.timer`) and pure-text;
the risk is testing without `systemd-run --user` available in CI.
Windows is the long pole — Task Scheduler XML is verbose and the kit
has no Windows CI today.

A concrete sequencing proposal:

- **v1 (this RFC):** macOS launchd, fully tested. Linux systemd
  `.service` + `.timer` emission, tested at the file-emission layer
  but not end-to-end. Windows Task Scheduler XML emission, file-emission
  tested only.
- **Roadmap:** Linux systemd end-to-end (needs a containerized CI step),
  Windows end-to-end (needs a Windows runner).

### 7. Conflict-aware exec refusal

**Decision:** before invoking `claude`, the executor checks for any
`.proposed` sidecar files anywhere under the vault and any
`wiki-conflict` skill activations in the most recent journal slice. If
either is present, the executor:

1. Skips invocation.
2. Journals `OperationExecFailedEvent` with
   `exit_code=-1, stderr_tail="vault has unresolved conflicts; resolve before scheduled exec"`.
3. Surfaces to `inbox/scheduled-failures.md` per §3.5.

Rationale: a scheduled run racing against an unresolved conflict is
exactly the failure mode ADR-0004 was designed to prevent. Refusing
cleanly is the contract; the next manual `wiki run --exec` after
resolution picks up normally.

### What stays the same

- `wiki run <op>` without `--exec` keeps its current dispatch-boundary
  semantics (validate + journal + print pointer; spec is canonical at
  [`docs/specs/task-17-wiki-run/spec.md`](../specs/task-17-wiki-run/spec.md)).
- `OperationRunEvent` shape is unchanged (modulo the rejected
  `triggered_by` field — see §4).
- No new runtime dependency. `pyyaml`, `pydantic>=2`, stdlib remain
  the whole runtime closure.
- The vault-side SKILL contract is unchanged. SKILLs receive the
  same inputs whether invoked manually or by exec.

### Migration path

This RFC, on acceptance, produces a numbered task sequence (probably
six to eight tasks) covering:

1. New event types in `models.py`.
2. `llm_wiki_kit/schedule/` module + `wiki schedule {install,
   uninstall, list}` CLI verbs.
3. macOS launchd plist emitter, tested end-to-end.
4. Linux systemd `.service` + `.timer` emitter, file-level tests.
5. Windows Task Scheduler XML emitter, file-level tests.
6. `wiki run --exec` shim + conflict check + log routing.
7. `wiki doctor` schedule checks.
8. Vault-side `wiki-schedule` SKILL.md (tells Claude when to prompt
   the user about installing a schedule, and how to react to the
   `inbox/scheduled-failures.md` page).

Sequencing notes: tasks 2 and 3 are the load-bearing pair; 4 and 5
can land in any order after 2; 6 depends on 1; 7 depends on 1 and 2;
8 is last.

### Compatibility

Existing vaults keep working untouched. No `period:` field changes
behavior unless the user explicitly runs `wiki schedule install`. The
additive event types preserve replay over older journals (ADR-0002).

## Alternatives

### Alt 1: SDK-based executor (`anthropic` Python SDK)

Rejected up front (see §"Decisions already made"). Trades a one-line
subprocess invocation for a runtime dep that 100% of users pay even
if they never schedule a thing. The kit's "pipx install on fresh
Python 3.11" promise (CHARTER principle 3) breaks the moment
`anthropic` requires a transitive that fails to wheel-build on a
user's box. The shim is forward-compatible — a future ADR can add
SDK execution as an *alternative* `--exec-engine` without breaking
the shim users.

### Alt 2: Kit-hosted daemon (`wiki daemon`)

A long-running Python process that keeps schedules in memory and fires
them from a single supervisor. Rejected: violates "library-not-application"
and "does not host or sync vaults". Adds process-lifecycle, restart-on-
crash, and log-rotation concerns the kit shouldn't own. The OS already
has battle-hardened daemons for this; we'd be a worse launchd.

### Alt 3: Cron strings everywhere

Accept arbitrary cron expressions as the cadence DSL. Rejected:
audience accessibility. We can layer cron support on top of the
restricted DSL later without breaking the DSL; we can't remove cron
acceptance once shipped without breaking schedules.

### Alt 4: Cloud cron (GitHub Actions, etc.) as a first-class option

Schedules fire from a cloud runner that has access to the vault via
git. Rejected: scope creep. The kit doesn't sync vaults, and cloud
runners imply auth, secret management, and a network-of-record the
kit has no opinion on. Users who want to run the kit from a runner
they own (their own VPS, their own GitHub Actions self-hosted runner)
can do so today by invoking `wiki run --exec` themselves from any
shell — this RFC's executor doesn't preclude that, it just doesn't
build infrastructure for it.

### Alt 5: Inaction — leave `period:` as documentation

The honest baseline. Rejected: the kit has shipped `period:` for
~two months and the gap is the most common user-facing question.
Inaction is itself a decision to ship a partially-realised pattern.

## Drawbacks

- **A new top-level vault directory (`.wiki.journal/exec-logs/`).**
  Sibling to `.wiki.journal/journal.jsonl`, gitignored by default,
  but it's growth. Mitigated by rotating logs older than 30 days at
  next `wiki run --exec` invocation.
- **OS-specific code paths for the first time.** Until now the kit
  has been OS-agnostic markdown + Python. launchd / systemd / Task
  Scheduler emitters introduce three OS-conditional code paths. Each
  one is text-template-shaped (no native API calls), but they will
  drift if not kept under test.
- **A failure mode that requires touching the OS to debug.** When a
  schedule "doesn't fire", the user has to know how to run
  `launchctl list` (or equivalent). `wiki doctor` should surface this
  but won't fully hide it. Documented in the `wiki-schedule` SKILL.
- **An additional way for `claude` to fail.** Headless invocations
  can hit auth issues, rate limits, model availability problems, all
  of which surface as exec failures rather than spec-side bugs. The
  failure-surfacing-to-vault page (§3.5) is the user's main signal
  that something needs attention.
- **An expectation gap.** Once schedules work, users will expect
  *all* operations to be schedulable, including ones whose SKILL
  prompts for input interactively. The `period: on-demand` signal
  helps but won't catch every case. We'll need to evolve operation
  contracts to declare `requires_interaction: bool` over time.

## Unresolved questions

- **`triggered_by` field on `OperationRunEvent`, or a separate
  `ScheduledRunEvent`?** §4 picks the latter, but the additive-field
  path is cheaper if reviewers buy that the replay-assertion risk is
  small. Reviewer call.
- **Does `wiki schedule install` require root / admin for system-wide
  schedules, or always user-scope?** Proposed: always user-scope
  (launchd `~/Library/LaunchAgents/`, systemd `--user`, Task
  Scheduler current-user). System-wide schedules would let one
  install fire for any user logged in on the machine, but require
  admin on all three OSes and break the "no privileged operations"
  posture.
- **How does `wiki run --exec` discover the SKILL path?** Proposed:
  `<vault>/.claude/skills/<skill>/SKILL.md` is canonical (Claude Code
  convention). Vaults that have moved the SKILL elsewhere break.
  Workaround: an explicit `--skill-path` override on `wiki run`.
- **Logs: how long do we keep them?** Proposed: 30 days, rotated at
  next exec. Open to either much shorter (each exec wipes the
  previous) or no rotation (user manages).
- **What's the granularity of conflict-aware refusal?** §7 refuses
  on *any* unresolved conflict anywhere in the vault. A future
  refinement could refuse only when the conflict touches a page the
  operation declares it produces. Out of scope for v1; flagged here.
- **Should `wiki schedule list` show OS-side reality, journal state,
  or both?** Proposed: both, with a column indicating drift. Open
  to feedback.
- **Headless `claude` invocation flags.** The exact flag set for
  headless mode is a moving target in the Claude CLI. Pin against a
  documented minimum version in a follow-on ADR rather than freezing
  in this RFC.

## Outcome

Filled in on acceptance. Expected: a new ADR or two (cadence DSL;
executor shim), a numbered task sequence under a follow-on spec
directory (`docs/specs/wiki-schedule/`, `docs/specs/wiki-run-exec/`),
and a vault-side `wiki-schedule` SKILL.md. The agent-identity
follow-up RFC remains a separate effort.
