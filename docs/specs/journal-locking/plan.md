# Plan: journal-locking

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** Done
- **Spec:** [`docs/specs/journal-locking/spec.md`](spec.md)
- **Owner:** TBD (maintainer)

## Approach

Land the contract bottom-up: events + durability first (smallest blast
radius, no behavior change to existing callers), then the
`transaction` context manager (changes the journal's API surface but
not existing semantics), then the CLI subcommands (new surface,
isolated module), then the doctor check (composes with the existing
issue list), and finally the F-B3a notice removal + ADR-0002 amendment
in the same PR that completes the chain. The qC5 recovery work
(`read_events_lenient` sibling of `read_events`) lands with the doctor
step because the stale-lock recovery path is the most common consumer.

Each step is a self-contained PR. The plan reads like five fix PRs not
one big bang because the spec straddles three modules + ADR + vault-
side skills — landing it monolithically would either bury the locking
work behind doc churn or leave the codebase half-locked for review
purposes.

**Ordering rationale.** Step 1 (events + fsync) leaves the kit
functionally identical to today except every event is now durable.
Step 2 (locking around `append_event`) is the first behavior change;
once it lands, all existing CLI handlers are concurrent-safe for free.
Step 3 (`transaction`) adds the multi-event surface task 17 will
consume. Steps 4–5 (CLI + doctor) make the surface user-visible.
Step 6 cleans up notices and the ADR.

## Pre-conditions

- The retro-review fix PRs (#20, #21, #22) are merged (they are).
- `core/files/skills/wiki-lock/SKILL.md` carries the F-B3a header from
  PR #20 (it does). Step 6 removes it.
- ADR-0002 carries the 2026-05-16 amendment from PR #20. Step 6
  replaces it.
- No outstanding work changes the `Event` discriminated union or
  `VaultState` shape (none open as of branch date).

## Steps

1. **Lock + release event types validate and round-trip through the journal.**
   - Add `LockAcquiredEvent` (timestamp, by, reason: str | None) and
     `LockReleasedEvent` (timestamp, by, reason: str | None) to
     `llm_wiki_kit/models.py`; the optional `reason` on `LockReleasedEvent`
     was added retroactively by step 5 (this PR — `feat: wiki lock
     acquire|release CLI`) for the stale-holder reclaim audit pair
     (spec §Edge cases). Original step 1 (PR #27) shipped without it;
     extend the `Event` union; extend `VaultState` with `held_lock:
     HeldLock | None` where `HeldLock` is a small frozen dataclass
     (`by`, `acquired_at`, `reason`).
   - Wire into `replay_state`: `LockAcquiredEvent` sets `held_lock`,
     `LockReleasedEvent` clears it.
   - **Verification:**
     - `tests/unit/test_models.py::test_lock_event_round_trips_through_pydantic_union`
     - `tests/unit/test_journal.py::test_replay_state_tracks_held_lock`
     - `tests/unit/test_journal.py::test_old_journal_without_lock_events_replays_cleanly`

1. **Every `append_event` call is durable on disk before returning.**
   - In `llm_wiki_kit/journal.py::append_event`, after `fh.write(line)`:
     `fh.flush(); os.fsync(fh.fileno())`. Docstring updated to name
     the new guarantee (replacing the "atomic enough" hedge).
   - **Verification:**
     - `tests/unit/test_journal.py::test_append_event_fsyncs_before_returning`
     - `tests/unit/test_journal.py::test_append_event_fsync_fileno_is_journal_fd`
     - Full `pytest -q` still passes (no semantic regression).

1. **Two concurrent `append_event` calls cannot interleave bytes within a line.**
   - Wrap the existing `with journal_path.open("a", ...) as fh:`
     block in `fcntl.flock(fh.fileno(), fcntl.LOCK_EX)`. Lock is
     released by the kernel when `fh` closes at the `with` block end.
   - **Verification:**
     - `tests/unit/test_journal.py::test_concurrent_append_does_not_interleave_lines`
       (spawn two `multiprocessing.Process`es, each appending 100
       distinct events; assert the resulting journal parses to 200
       events in deterministic order is not required — only that all
       200 are present and lines are well-formed).
     - `tests/unit/test_journal.py::test_append_event_blocks_when_another_process_holds_lock`
       (one process holds via `transaction(persist=True)` from step 4;
       the second's `append_event` blocks ≥100ms then returns).
       *(This test depends on step 4; either land step 3 with a
       subprocess-driven flock holder and back-fill the test in step 4,
       or merge step 3 with step 4. The first is preferred for review
       size — recommend the standalone-holder fixture pattern.)*

1. **`journal.transaction()` context manager brackets multi-event sequences.**
   - Add `transaction(journal_path, by, reason=None, persist=False) ->
     Iterator[None]`. On enter: open fd, take `LOCK_EX`, append
     `LockAcquiredEvent`, set a `ContextVar[FD | None]` so nested
     `append_event` calls reuse the fd without re-locking. On exit
     (try/finally): append `LockReleasedEvent`, release lock, close fd.
   - `append_event` becomes lock-aware: if the `ContextVar` is set, it
     writes to the held fd instead of opening its own.
   - **Verification:**
     - `tests/unit/test_journal.py::test_transaction_emits_lock_acquired_and_released_on_clean_exit`
     - `tests/unit/test_journal.py::test_transaction_emits_lock_released_on_exception`
     - `tests/unit/test_journal.py::test_nested_append_event_reuses_held_lock`
       (`fcntl.flock` monkeypatched to a counter; assert called once
       per `transaction`, not once per nested event).

1. **`wiki lock acquire|release` CLI surface is wired and held-lock-safe.**
   - Add `_cmd_lock_acquire` and `_cmd_lock_release` to `cli.py`. Both
     subparsers under a `lock` parent (`wiki lock acquire ...` / `wiki
     lock release ...`). `acquire` writes `.wiki.journal/lock` with
     `<by>\n<iso-timestamp>\n[<reason>]` and appends
     `LockAcquiredEvent`. `release` validates `--by` against the
     holder-file (`--force` overrides), appends `LockReleasedEvent`,
     deletes the holder-file. Add `LOCK_HELD_EXIT = 3`.
   - **Verification:**
     - `tests/integration/test_wiki_lock.py` (new file):
       - `test_wiki_lock_acquire_exits_zero_on_first_acquire`
       - `test_wiki_lock_acquire_exits_three_when_held`
       - `test_wiki_lock_release_clears_holder_and_journals_release_event`
       - `test_wiki_lock_release_refuses_by_mismatch_without_force`
       - `test_wiki_lock_release_with_force_overrides_holder`
       - `test_wiki_lock_release_on_unheld_is_silent_zero`
     - Review-driven additions (same file):
       - `test_wiki_lock_acquire_requires_by_argument` (argparse boundary)
       - `test_wiki_lock_acquire_reclaims_stale_holder` (audit pair)
       - `test_wiki_lock_acquire_unsupported_fs_skips_reclaim_audit`
       - `test_wiki_lock_acquire_raceloss_after_stale_reclaim_exits_three`
       - `test_wiki_lock_acquire_rejects_newline_in_by_or_reason`
         (parameterized over `--by`/`--reason` × `\n`/`\r`)
       - `test_wiki_lock_release_without_by_uses_holder`
       - `test_wiki_lock_acquire_outside_a_vault_is_wiki_error`
       - `test_wiki_lock_release_outside_a_vault_is_wiki_error`
     - `tests/unit/test_journal.py::test_transaction_nonblocking_raises_lockunavailable_when_held`
       (clean-unwind invariants for the new `transaction(nonblocking=True)` path)

1. **`wiki doctor` reports stale locks and survives a corrupt journal.**
   - A new sibling function `journal.read_events_lenient(path) ->
     tuple[list[Event], Corruption | None]` returns the valid-events
     prefix plus an optional `Corruption(line, reason)` row. Strict
     `read_events` is unchanged — only `run_doctor` flips to lenient
     so the existing seven callers still fail loudly on a torn
     journal. (The original sketch added a `stop_on_corruption: bool`
     flag to `read_events`; §Risks names `read_events_lenient` as the
     cleaner resolution, and that's what shipped.)
   - `doctor.check_stale_lock(state, threshold_hours)` reads
     `state.held_lock` (populated by `replay_state` from the
     acquire/release event pair) and emits a stale-lock issue when
     `held_lock.acquired_at` is older than `threshold_hours`.
     Threshold read from `WIKI_LOCK_STALE_HOURS` env var (default 24)
     by an internal helper in `run_doctor`. Quality-engineer review
     during step 6 collapsed the original `(events, vault_root,
     threshold_hours)` sketch into the state-based signature so
     `replay_state` stays the single source of truth for "is the lock
     held."
   - `run_doctor` calls `read_events_lenient(journal_path)`,
     surfaces `Issue("journal-corrupt", str(line_number), reason)` if
     `Corruption` is non-None, and runs the rest of the checks on
     the partial event list.
   - **Verification:**
     - `tests/unit/test_journal.py::test_read_events_lenient_returns_none_corruption_on_clean_journal`
     - `tests/unit/test_journal.py::test_read_events_lenient_returns_partial_events_and_corruption_at_bad_line`
     - `tests/unit/test_doctor.py::test_check_stale_lock_returns_issue_when_acquired_at_older_than_threshold`
     - `tests/unit/test_doctor.py::test_check_stale_lock_returns_empty_when_within_threshold`
     - `tests/unit/test_doctor.py::test_check_stale_lock_returns_empty_when_held_lock_is_none`
     - `tests/unit/test_doctor.py::test_check_stale_lock_coerces_naive_acquired_at_without_crashing`
     - `tests/unit/test_doctor.py::test_doctor_reports_stale_lock_after_threshold_via_run_doctor`
     - `tests/unit/test_doctor.py::test_doctor_warns_and_falls_back_when_env_var_unparseable`
     - `tests/integration/test_wiki_doctor.py::test_doctor_runs_against_corrupt_journal_and_reports_journal_corrupt`

1. **F-B3a notices removed; ADR-0002 reflects the implemented contract.**
   - `core/files/skills/wiki-lock/SKILL.md` — F-B3a header (added
     in PR #20) removed; body stands on its own.
   - `core/files/skills/wiki-doctor/SKILL.md` — F-B3a note on the
     stale-lock row + triage paragraph removed.
   - `core/files/AGENTS.md` — F-B3a note on the "many files at once"
     reference replaced with the live "acquire the lock via
     `skills/wiki-lock/SKILL.md`" line.
   - `docs/adr/0002-journal-as-state-truth.md` §Decision pithy quote
     amended to name `fcntl.flock` + `fsync` as the contract; §Negative
     "Concurrent writers" entry replaced by an "Implemented in
     `docs/specs/journal-locking/spec.md` on <date>" pointer; the
     2026-05-16 F-B3a amendment is removed as superseded.
   - Spec status flipped from `Draft` to `Implemented`.
   - **Verification:**
     - `grep -r "F-B3a" core/ docs/adr/` returns no hits.
     - Spec frontmatter `Status: Implemented`.
     - Plan frontmatter `Status: Done`.
     - Issue #23 checkboxes for qB1, qB2, qC5, F-B3b checked.

## Verification gate

The whole plan succeeds when:

```
pytest tests/unit/test_journal.py tests/unit/test_doctor.py tests/integration/test_wiki_lock.py tests/integration/test_wiki_doctor.py
pytest -q                            # full suite, no regressions
ruff check llm_wiki_kit/ tests/
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
grep -r "F-B3a" core/ docs/adr/      # no hits
```

And the spec's acceptance-criteria checkboxes are all ticked.

## Risks

- **`fcntl.flock` semantics on macOS vs. Linux.** On macOS, `flock`
  works on local filesystems but iCloud Drive (`~/Library/Mobile
  Documents/com~apple~CloudDocs/`) is documented as "do not run
  POSIX-locking programs here." The errno on rejection varies by
  platform and mount: macOS and Linux disagree on whether `flock`
  on an unsupported FS raises `EOPNOTSUPP` or `ENOTSUP`, and NFS
  without `lockd` raises `ENOLCK`. *Recovery:* step 3 catches the
  `{EOPNOTSUPP, ENOTSUP, ENOLCK}` set and logs one `WARNING` per
  journal path through `logging.getLogger("llm_wiki_kit.journal")`
  naming ADR-0002, then continues without locking — matching
  pre-spec behavior. Any other `OSError` from `flock` propagates
  (it's a real disk error, not a "this FS is unsupported" signal).
  Spec §Edge cases carries the full contract.
- **`fsync` cost.** sha256-on-a-line + an `fsync` per event is a few
  ms on SSD, more on rotational. The journal grows at ~1 line per
  CLI invocation; the cost is invisible to the user except during a
  `wiki run` operation that emits dozens. ADR-0002's "replay over
  1000 events fits comfortably under 100ms" acceptance criterion
  measures `replay_state`, not `append_event`; the new latency
  appears in the `append_event` benchmark only. *Recovery:* if
  measurable user-visible regression appears, an `fsync` debounce
  inside `transaction` (one `fsync` on block exit, none per nested
  `append_event`) is the obvious follow-up — track as a new finding.
- **Test flakiness from concurrent-process tests.** Step 3's
  interleaving test spawns subprocesses and reads timing. On loaded
  CI runners, the ≥100ms block assertion may flake. *Recovery:* tag
  with `@pytest.mark.flaky` (allowed by the repo's pytest config) or
  drop the timing assertion in favor of "the second's call returns
  *after* the first releases" using a `multiprocessing.Event`.
- **`stop_on_corruption` API doubles `read_events` return shape.**
  Returning `(events, corruption | None)` vs `events` is a typed
  union the existing 7 callers don't want to handle. *Recovery:* keep
  `read_events(path) -> list[Event]` as the unchanged default;
  introduce `read_events_lenient(path) -> tuple[list[Event], Corruption
  | None]` as a separate function. `run_doctor` calls the lenient
  one; everyone else keeps today's strict one.

## Out of scope

- **Task 17 (`wiki run` operation runner).** The runner *consumes*
  `transaction()`; its own behavior is task 17's spec. This plan
  unblocks task 17 by landing the contract, not by writing the
  consumer.
- **Lock leases or per-operation thresholds.** One env-var knob covers
  the documented cases.
- **Windows support.** Charter-level decision; out of this plan.
- **Migrating `wiki ingest` / `wiki add` / `wiki resolve` into
  explicit `transaction` blocks.** They benefit from step 3's
  per-event lock automatically. Wrapping them in `transaction` is a
  performance optimization, not a correctness fix, and belongs in a
  follow-up if measured.
- **A `wiki lock status` / `wiki lock list` subcommand.** `wiki
  doctor` is the read path. A dedicated status command would be a
  small follow-up if usage shows the need.
