# Spec: journal-locking

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Implemented
- **Owner:** `llm_wiki_kit.journal` + `llm_wiki_kit.cli`
- **Related:** [ADR-0002](../../adr/0002-journal-as-state-truth.md) (journal as state truth — amended by this spec), [ADR-0005](../../adr/0005-pydantic-for-disk-bound-schemas.md) (schemas), [`docs/specs/journal-locking/plan.md`](plan.md), retro-review issue [#23](https://github.com/eugenelim/llm-wiki-kit/issues/23) (findings `F-B3b`, `qB1`, `qC5` absorbed into recovery)
- **Supersedes:** the F-B3a "not yet shipped" notices in
  [`core/files/skills/wiki-lock/SKILL.md`](../../../core/files/skills/wiki-lock/SKILL.md), [`core/files/skills/wiki-doctor/SKILL.md`](../../../core/files/skills/wiki-doctor/SKILL.md), and [`core/files/AGENTS.md`](../../../core/files/AGENTS.md) — those headers were removed in the PR that landed this spec.

## What this is

The journal is the kit's source of truth (ADR-0002). Before this spec
landed, every kit command wrote to `.wiki.journal/journal.jsonl`
through one `fh.write` in append mode and returned — no `fsync`, no
advisory lock. Two failure modes followed: a hard crash between
`write()` and the kernel's page-cache flush lost the most recent
intent event (qB1); two concurrent invocations (two terminals, an
automation crossing a manual run, a device sync) could interleave a
single event line across processes and corrupt the journal beyond
`read_events`'s tolerance (F-B3 / qB2).

This spec defines the contract for the kit's concurrent-writer story:
an `fcntl.flock`-based advisory exclusive lock around every
`append_event` call, an `fsync` on the journal fd before the lock
releases, two new event types (`lock.acquired` / `lock.released`) to
record long multi-event operations, two CLI subcommands
(`wiki lock acquire|release`) the vault-side `wiki-lock` skill drives,
and a `wiki doctor` check that reports a stale lock with no matching
release.

This spec covers **the journal's contract only**. It does not file-lock
arbitrary vault files (the kit's drift detection is the file-level
contract; the lock is journal-level), and it does not touch the
operation runner itself (task 17). The runner is a *consumer* of this
contract.

## Inputs

- **`journal.append_event(journal_path: Path, event: Event)`** — the
  one entry point. Caller doesn't know or care that locking happens.
- **`journal.transaction(journal_path: Path, by: str, reason: str | None
  = None)`** — a context manager the operation runner and bulk-ingest
  paths wrap around a multi-event sequence. Holds the lock for the
  duration of the `with` block; emits `LockAcquiredEvent` on enter and
  `LockReleasedEvent` on exit; nested `append_event` calls inside the
  block reuse the held fd instead of re-locking per event.
- **`wiki lock acquire --by <name> [--reason "<text>"]`** — CLI surface
  for the vault-side `wiki-lock` skill. Wraps the context manager but
  returns to shell with the lock held.
- **`wiki lock release [--by <name>] [--force]`** — releases the lock
  the matching `acquire` took. `--force` is required when the caller's
  `--by` doesn't match the holder; intended for stale-lock recovery
  after the doctor surfaces one.
- **`wiki doctor`** — adds a `stale-lock` issue kind. No new flag; the
  default doctor pass runs the new check.

## Outputs

- **`append_event` returns `None`.** Side effects: one validated JSON
  line appended to the journal, the journal fd `fsync`'d before the
  function returns.
- **`transaction` yields `None`** (a typed `Iterator[None]`). Side
  effects: one `LockAcquiredEvent` on enter, one `LockReleasedEvent` on
  exit (success or exception). The block's own `append_event` calls
  produce their event lines as usual.
- **`wiki lock acquire` exits 0** with the held-state recorded; exits
  `LOCK_HELD_EXIT` (= 3) when another holder is in possession, printing
  the current holder + acquired-at timestamp.
- **`wiki lock release` exits 0** on a clean release; exits `WIKI_ERROR_EXIT`
  (= 2) on a `--by` mismatch without `--force`; exits 0 (silent no-op)
  when no lock is held — the `wiki-lock` SKILL.md already documents
  this as harmless.
- **`wiki doctor`** appends `Issue("stale-lock", holder, "acquired <iso>")`
  to its existing issue list when the latest `LockAcquiredEvent` has no
  matching `LockReleasedEvent` and the on-disk `.wiki.journal/lock`
  file is older than 24 hours (configurable; see Invariants).
- **Two new event classes**:
  - `LockAcquiredEvent(timestamp, by, reason: str | None = None)`
  - `LockReleasedEvent(timestamp, by, reason: str | None = None)` —
    the optional `reason` carries the audit string for the
    stale-holder reclaim path (`reason="stale lock reclaimed"`,
    see §Edge cases). Defaults to `None` for ordinary releases.
  Both go into the `Event` discriminated union per ADR-0002's additive
  schema-evolution rule.

## Behavior

### Happy path — single-event command (e.g. `wiki ingest`)

1. `append_event(journal_path, event)` opens the journal in append mode.
2. Calls `fcntl.flock(fh.fileno(), LOCK_EX)` — blocks if another
   process holds it; the kit prefers blocking to racing for every
   writer except `wiki lock acquire`, which probes with `LOCK_NB`
   (see "Acquire-side contention semantics" below).
3. Validates `event` via the existing Pydantic discriminated union.
4. Writes the JSON line + `\n`.
5. `fh.flush(); os.fsync(fh.fileno())` — the line is durable on disk
   before the function returns.
6. Releases the lock implicitly when the file is closed (the `with`
   block exits).

### Happy path — multi-event operation (e.g. `wiki run weekly-digest`)

1. `wiki run` enters `journal.transaction(path, by="weekly-digest",
   reason="2026-W20 digest")`.
2. The context manager opens the journal once, takes `LOCK_EX` once,
   appends a `LockAcquiredEvent`, then yields.
3. The operation runner calls `append_event` N times inside the block.
   Each call validates + writes + `fsync`'s **but reuses the already-held
   lock** — measured by checking the held fd in a `contextvars.ContextVar`
   so per-event re-locking is a no-op for nested calls.
4. On block exit (success), appends a `LockReleasedEvent`, then releases
   the lock + closes the fd.
5. On block exit (exception), still appends `LockReleasedEvent` (in a
   `finally` clause) before re-raising. The lock never outlives the
   process.

### Acquire-side contention semantics — non-blocking CLI

`append_event` takes `LOCK_EX` (blocking) so two concurrent writers
serialize cleanly. `wiki lock acquire` is the exception: a CLI user who
sees their shell hang on a held lock cannot easily reason about
who's holding it or when it'll release, and ctrl-C against a blocked
`flock(2)` is platform-dependent. The CLI therefore probes with
`LOCK_EX | LOCK_NB` against a transient fd and exits
`LOCK_HELD_EXIT` (= 3) on `EAGAIN`/`EWOULDBLOCK`. Only the CLI
acquire path takes this non-blocking shortcut — every other caller
(every `append_event`, every `transaction()` from the runner) stays
blocking. The `wiki-lock` SKILL.md treats a non-zero exit as
"surface to the user, do not retry", which is the agreed UX.

### Happy path — Claude-session manual hold (`wiki lock acquire`)

The vault-side `wiki-lock` SKILL.md describes a workflow where a
multi-turn Claude session acquires the lock at the start, does work
across several tool calls, and releases at the end. This is the
"manual hold" path:

1. `wiki lock acquire --by <agent> --reason "<text>"` calls
   `transaction(..., persist=True)`.
2. The CLI writes the lock holder + start time to
   `.wiki.journal/lock` (a one-line text file) so a second process
   can read the holder name without parsing the journal.
3. The CLI exits 0 *without releasing*. The fd is closed at process
   exit; the holder-file persists; future `fcntl.flock` attempts from
   other processes block until `wiki lock release` runs.
4. `wiki lock release --by <agent>` reopens the journal, `flock`s
   exclusively (it will succeed because the holder-file is advisory,
   not an OS-level held lock — see "Why advisory and not OS-held"),
   appends `LockReleasedEvent`, deletes `.wiki.journal/lock`, closes.

### Edge cases

- **Crash between `append_event`'s `fsync` and the calling code
  observing success** — the line is on disk; the caller sees an
  exception; the kit's existing `wiki doctor` reconciliation handles
  the "event-without-file" case via `check_missing` and `check_orphans`.
  No change.
- **Crash inside `transaction` block** — `LockReleasedEvent` is
  emitted by the `finally`. If even the `finally` fails (out-of-disk,
  signal kill -9), `wiki doctor` reports `stale-lock` on next run.
- **Two concurrent `wiki ingest` invocations** — second one blocks on
  `fcntl.flock` until the first returns. No interleaving possible.
- **Lock held by a dead PID** — `fcntl.flock` advisory locks are
  released by the kernel when the holding process exits, *but* the
  `.wiki.journal/lock` holder-file persists for the Claude-session
  manual-hold path. Next `acquire` attempt sees: holder-file present,
  but kernel `flock` succeeds — meaning the previous holder process
  is dead. The new acquire wins, overwrites the holder-file, and
  appends a `LockReleasedEvent(by="wiki-doctor", reason="stale lock
  reclaimed")` before its own `LockAcquiredEvent` for clean audit.
- **NFS / iCloud Drive / SMB** — `fcntl.flock` is local-only by spec.
  On a synced filesystem, two devices can each hold "the lock"
  simultaneously. Documented as a Negative consequence; the kit's
  target use case is local single-machine vaults. A user who
  multi-device-syncs gets the existing pre-spec behaviour (no
  protection) plus, where flock works at all, single-device
  protection. The `wiki-lock` SKILL.md already warns about this.
- **Filesystem rejects `flock` outright** — iCloud Drive, some SMB
  mounts, and NFS without `lockd` raise `OSError` with one of
  `EOPNOTSUPP` / `ENOTSUP` / `ENOLCK` (the set differs by platform
  and mount). Behavior on the filesystem families named in the
  previous bullet is mount- and config-dependent: an iCloud Drive
  vault may either succeed-locally-but-not-cross-device (previous
  bullet) or raise outright (this bullet), and the kit accepts both
  fallback paths. On the raise path, `append_event` catches that
  errno set, logs one `WARNING` through the `journal` module's
  logger (`logging.getLogger(__name__)`, which resolves to
  `llm_wiki_kit.journal`) naming ADR-0002, and continues without
  locking — pre-spec behavior, no crash. Any other `OSError`
  propagates (it's a real disk error); `EINTR` specifically is *not*
  in the fallback set so PEP 475 auto-retry semantics on CPython
  remain intact and a future refactor can't silently swallow it.
  Warning is gated to once-per-resolved-path per process so a long
  `wiki run` on an unsupported filesystem produces one informative
  line, not one per event; the resolved-path keying collapses
  symlink / relative-vs-absolute spellings of the same file.

### Error cases

- **Lock contention** — `acquire --by` exits `LOCK_HELD_EXIT` (3) with
  `lock held by <name> since <iso>` on stderr.
- **`fsync` failure (EIO)** — propagates as `OSError`. Caller's
  `WikiError` handler catches `WikiError`, not `OSError`; the
  traceback surfaces (this is correct — disk errors are not "user-
  fixable through the CLI"). The journal's last successful line is
  fully durable.
- **Journal directory missing** — `append_event` already creates it
  (`journal_path.parent.mkdir(parents=True, exist_ok=True)`).
  Unchanged.
- **`release --by` mismatch without `--force`** — `WIKI_ERROR_EXIT`
  with `lock held by <other> since <iso>; pass --force to override`.
  The acquired-at timestamp is included so an operator running the
  command can decide whether the holder is stale (and worth
  ``--force``-ing) without consulting `wiki doctor` separately.

## Invariants

- **Every line in the journal is durable on disk before the
  appending function returns.** `fsync` enforces this. (qB1 closed.)
- **Two simultaneous `append_event` calls in different processes
  cannot interleave bytes within a single line.** `fcntl.flock`
  enforces this. (qB2 closed.)
- **`LockAcquiredEvent` always pairs with a `LockReleasedEvent` in
  the same process lifetime**, *except* when the process crashes
  hard enough to bypass the `finally`. `wiki doctor` detects the
  exception case. (F-B3b closed.)
- **The lock is journal-scoped, not file-scoped.** Editing
  `frontmatter.schema.yaml` while a `wiki run` is in flight is the
  user's prerogative; the kit's drift detection handles that
  interaction (ADR-0004). The lock only protects the journal itself.
- **`fcntl.flock` is non-recursive at the OS level**, but the
  in-process re-entry guard (a `ContextVar` holding the active fd)
  makes `append_event` callable inside an open `transaction` without
  deadlock.
- **The stale-lock threshold is 24 hours by default** and
  configurable via `WIKI_LOCK_STALE_HOURS` (env var, integer hours).
  Doctor reads this on each run; no journal event records the
  threshold (it's a CLI/env knob, not a vault invariant).

## Contracts with other modules

- **`journal.py`** owns `append_event`, `read_events`, `replay_state`,
  `transaction`. The lock + fsync logic lives here. No caller outside
  `journal.py` touches `fcntl` directly.
- **`write_helper.py`** continues to call `append_event` exactly as
  today; the additions are transparent.
- **`cli.py`** grows `_cmd_lock_acquire` and `_cmd_lock_release`. The
  existing handlers (`_cmd_init`, `_cmd_add`, `_cmd_ingest`,
  `_cmd_resolve`, `_cmd_doctor`) gain no new `transaction` wraps in
  this spec — those land as task-17's operation runner brings in
  multi-event sequences. (Today every other handler emits one event
  or a tight sequence inside one CLI invocation; per-event locking is
  sufficient.)
- **`doctor.py`** gains `check_stale_lock(state, threshold_hours) ->
  list[Issue]`. Reads `state.held_lock` directly — the
  last-acquire-wins / release-clears semantics live in
  `replay_state` (one source of truth, no parallel walk). Wired into
  `run_doctor` in the same place the existing checks are.
  Pattern-matches `check_page_drift`, `check_pending_proposals`,
  `check_orphans`, `check_missing`, and `check_primitive_missing` —
  every other doctor check takes the replayed `VaultState`.
- **`models.py`** gains `LockAcquiredEvent` + `LockReleasedEvent`; both
  added to the `Event` discriminated union; `replay_state` records the
  current holder into `VaultState.held_lock: HeldLock | None`.
- **Vault-side `core/files/skills/wiki-lock/SKILL.md`** loses its
  F-B3a "not yet shipped" header in the same PR that lands this spec's
  implementation. Same for the doctor SKILL row and `core/files/AGENTS.md`.

## Acceptance criteria

The same list translates 1-to-1 into the construction tests in
[`plan.md`](plan.md) §Steps.

### Durability (qB1)

- [x] `test_append_event_fsyncs_before_returning` — `os.fsync` patched
      to a counter; `append_event` raises if the counter doesn't tick
      between `write` and return.
- [x] `test_append_event_fsync_fileno_is_journal_fd` — the call passes
      the journal fd specifically, not an unrelated fd.

### Mutual exclusion (qB2 / F-B3b)

- [x] `test_concurrent_append_does_not_interleave_lines` — two
      subprocesses each call `append_event` 100 times; the resulting
      journal parses as exactly 200 valid JSONL lines.
- [x] `test_append_event_blocks_when_another_process_holds_lock` —
      one process holds via `transaction(persist=True)`; the second's
      `append_event` blocks for ≥100ms; releases when the first does.

### Transaction context manager

- [x] `test_transaction_emits_lock_acquired_and_released_on_clean_exit` —
      events bracket the body's events in journal order.
- [x] `test_transaction_emits_lock_released_on_exception` — body
      raises; `LockReleasedEvent` is still the last event; exception
      re-raises.
- [x] `test_nested_append_event_reuses_held_lock` — `append_event`
      called inside `transaction` does not call `flock` twice (assert
      via monkeypatched `fcntl.flock` counter).

### CLI surface

- [x] `test_wiki_lock_acquire_exits_zero_on_first_acquire`
- [x] `test_wiki_lock_acquire_exits_three_when_held` — second
      invocation against an existing holder file exits `LOCK_HELD_EXIT`
      with stderr naming the holder.
- [x] `test_wiki_lock_release_clears_holder_and_journals_release_event`
- [x] `test_wiki_lock_release_refuses_by_mismatch_without_force`
- [x] `test_wiki_lock_release_with_force_overrides_holder`
- [x] `test_wiki_lock_release_on_unheld_is_silent_zero`

### Doctor

- [x] `test_doctor_reports_stale_lock_after_threshold` — acquire,
      sleep-past-threshold (monkeypatch the wall clock), no release;
      `wiki doctor` reports `stale-lock: <holder>`.
- [x] `test_doctor_does_not_report_stale_lock_within_threshold`
- [x] `test_doctor_does_not_report_when_release_event_follows_acquire`

### Recovery (qC5 absorbed)

- [x] `test_doctor_runs_against_corrupt_journal_and_reports_journal_corrupt` —
      a new `journal.read_events_lenient(path) -> tuple[list[Event],
      Corruption | None]` (sibling of strict `read_events`, not a
      flag on it; see plan §Risks) returns the valid-events prefix
      plus a `Corruption(line, reason)` row; doctor uses it;
      corruption surfaces as `Issue("journal-corrupt", str(line),
      reason)` rather than crashing the doctor itself. Pairs with
      the locking work because corrupt journals are the most common
      state the stale-lock recovery hits.

### Schema evolution (ADR-0002 §Negative)

- [x] `test_old_journal_without_lock_events_replays_cleanly` — a
      journal written before this spec lands replays without raising;
      `VaultState.held_lock is None`.
- [x] `test_lock_event_round_trips_through_pydantic_union` —
      dump + load + dump produces identical bytes.

### ADR amendment

- [x] `docs/adr/0002-journal-as-state-truth.md` §Decision text and
      §Negative ("Concurrent writers are not safe.") updated in the
      same PR. The F-B3a 2026-05-16 amendment in §Negative is
      replaced by an "Implemented in `docs/specs/journal-locking/spec.md`
      on <date>" pointer.

### Vault-side skill notice removal

- [x] `core/files/skills/wiki-lock/SKILL.md` — F-B3a header removed.
- [x] `core/files/skills/wiki-doctor/SKILL.md` — F-B3a note on the
      stale-lock row + triage paragraph removed.
- [x] `core/files/AGENTS.md` — F-B3a note on the "many files at once"
      reference replaced with the live instruction.

## Non-goals

- **Not OS-level file locking on arbitrary vault files.** Drift
  detection (ADR-0004) is the file-level contract. This spec doesn't
  duplicate it.
- **Not distributed locking.** A user who multi-device-syncs the same
  vault via iCloud/Dropbox/git is responsible for not running `wiki`
  on two devices simultaneously. `fcntl.flock` is per-machine; the
  spec documents this in §Edge cases.
- **Not lock leases or auto-recovery thresholds tuned per-operation.**
  The 24-hour stale threshold is one knob, configurable by env var.
  Per-operation tuning belongs in a follow-up if real usage shows the
  default is wrong.
- **Not the operation runner itself.** Task 17 introduces `wiki run`
  and is a consumer of `transaction()`. The runner's behavior is its
  own spec.
- **Not Windows.** `fcntl` is POSIX-only. Windows is documented as
  best-effort (the kit's target audience is Mac + Linux; Windows
  support is a charter-level decision and not in scope here).
- **Not `wiki lock list` / introspection.** The CLI surface is
  `acquire` + `release` only. `wiki doctor` is the read path for "is
  the lock held?" — there's no need for a second tool.
- **Not a structured journal-event signal for the unsupported-flock
  fallback.** When `append_event` engages the fallback on an
  unsupported filesystem the only operator-facing signal is the
  one-shot `WARNING` log record. There is no `lock.unsupported`
  audit event recorded in the journal itself; an operator answering
  "did this vault run under a degraded lock regime?" reads logs, not
  the journal. Recording a structured event is a follow-up — it's
  an additive schema change that fits ADR-0002's evolution rule and
  belongs with the doctor work (step 6) if `wiki doctor` ever needs
  to surface "this vault has run unlocked".
