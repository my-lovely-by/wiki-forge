# Plan: journal-reader-cache

> **Implementation plan paired with `spec.md`.** The spec says *what*;
> the plan says *how, in what order, with what verification*.

- **Status:** Done
- **Spec:** [`docs/specs/journal-reader-cache/spec.md`](spec.md)
- **Owner:** TBD (maintainer)

## Approach

Land qC4 as a single PR after [safe-write-ordering](../safe-write-ordering/spec.md)
merges, since both touch `write_helper.py` and must serialise.

Two-module change:

- `journal.py` grows a `JournalReader` class, a `_CURRENT_READER`
  `ContextVar`, a `use_journal_cache` context manager, and an
  internal hook in `append_event` that calls `notify_appended` when
  the active reader's journal matches.
- `write_helper.py` switches its three internal baseline lookups
  (`_baseline_hash`, `_managed_region_baseline_hash`,
  `_known_regions_for_file`) from `read_events(journal_path)` to a
  shared `_read_events_cached(journal_path)` helper that consults
  `journal._CURRENT_READER`.
- `cli.py` wraps each install-pipeline handler body
  (`_cmd_init`, `_cmd_add`) with
  `with journal.use_journal_cache(journal_path):`.

**Ordering inside the PR.** Spec-first construction tests for the
reader and the context manager (unit-level); then the
`write_helper` integration (assert that baseline lookups go through
the cache); then the CLI wrappers; then the integration test that
pins the read-once contract end-to-end via the install pipelines.

## Pre-conditions

- safe-write-ordering spec is `Implemented` and merged (PR #44 or
  successor). Without it, the write_helper module surface is wrong
  and this work would need a non-trivial rebase.
- The journal-locking spec is `Implemented` (already true; PR #33).
- No outstanding work changes `read_events`, `append_event`, or the
  write_helper baseline-lookup helpers.

## Steps

1. **`JournalReader` + `_CURRENT_READER` + `use_journal_cache`.**

   *Depends on:* none.

   *Verification mode:* TDD.

   *Tests (contract, in `tests/unit/test_journal.py`):*

   - `test_journal_reader_caches_events_within_scope` — instantiate
     `JournalReader(journal)`; assert `reader.events() is reader.events()`
     (identity, not just equality).
   - `test_journal_reader_lazy_loads_only_when_events_called` — instantiate
     the reader without calling `events()`; assert `read_events`
     was never called. Call `events()`; assert `read_events` called
     exactly once.
   - `test_journal_reader_notify_appended_extends_list_after_load` —
     load via `events()`, append a synthetic event via
     `notify_appended`, assert the next `events()` call returns a
     list whose last element is the appended event.
   - `test_journal_reader_notify_appended_is_noop_before_load` —
     instantiate the reader, call `notify_appended` without ever
     calling `events()`; later call `events()`; assert the list
     reflects ONLY what was on disk at `events()` time, not the
     ghost append. (Lazy-load contract: don't conjure an in-memory
     state for events we never observed by reading.)
   - `test_use_journal_cache_installs_and_resets_contextvar` —
     `_CURRENT_READER.get()` is `None` outside; non-None inside;
     `None` after.
   - `test_use_journal_cache_resets_on_exception` — body raises;
     `_CURRENT_READER.get()` is `None` after.
   - `test_use_journal_cache_non_recursive_raises` — nested entry
     raises `RuntimeError` (mirrors `transaction()`).

   *Approach:* implement `JournalReader` as a small class with
   `__init__(journal_path)`, `events() -> list[Event]`,
   `notify_appended(event) -> None`. Lazy-load: `self._events` is
   `None` until `events()` is called. `notify_appended` is a no-op
   when `_events is None`. `use_journal_cache` is a `@contextmanager`
   that sets the `ContextVar` and resets on exit. Non-recursive
   guard at entry.

   *Done when:* all seven tests pass; no existing test regresses.

1. **`append_event` hooks into the active reader.**

   *Depends on:* step 1.

   *Verification mode:* TDD.

   *Tests (contract, in `tests/unit/test_journal.py`):*

   - `test_append_event_notifies_active_reader_on_matching_journal` —
     inside `use_journal_cache(journal)`, call `events()` (forces
     load), `append_event(journal, e)`, then `events()` again;
     assert the appended event is in the tail.
   - `test_append_event_does_not_notify_reader_for_different_journal` —
     inside `use_journal_cache(journal_a)`, call
     `append_event(journal_b, e)`; the reader for `journal_a` is
     unchanged.
   - `test_append_event_does_nothing_to_cache_without_scope` — outside
     `use_journal_cache`, `append_event` behaves identically to
     today (cache hook is a no-op).

   *Approach:* in `append_event`, after `fsync` returns (both
   per-event-lock branch and held-fd branch), look up
   `_CURRENT_READER.get()`. If the reader's resolved path equals
   `journal_path.resolve()`, call `reader.notify_appended(event)`.
   Path comparison uses the same `.resolve()` that the held-fd path
   already uses, so symlinks and relative-vs-absolute spellings
   collapse to one identity.

   *Done when:* all three tests pass; the existing journal-locking
   tests (transaction, fsync, flock) still pass.

1. **`write_helper` baseline lookups consult the cache.**

   *Depends on:* steps 1, 2.

   *Verification mode:* TDD.

   *Tests (contract, in `tests/unit/test_write_helper.py`):*

   - `test_safe_write_inside_cache_scope_reads_journal_once` —
     monkeypatch `journal.read_events` with a counter; inside
     `use_journal_cache`, call `safe_write` N=5 times for distinct
     paths; assert the counter is `1`.
   - `test_safe_write_outside_cache_scope_unchanged` — same N=5
     calls without the scope; counter is `>= 5`.
   - `test_safe_write_region_inside_cache_scope_reads_journal_once` —
     analogue for `safe_write_region` (which also reads via
     `_managed_region_baseline_hash`).
   - `test_resolve_proposal_inside_cache_scope_reads_journal_once` —
     analogue for `resolve_proposal`'s
     `_known_regions_for_file` walk.
   - `test_safe_write_inside_cache_sees_just_appended_event` — cache
     scope; first `safe_write("page.md", "v1")` returns WRITTEN;
     second `safe_write("page.md", "v1")` (byte-identical) takes
     the repeat-write path (NOT adopt) because the cache reflects
     the first call's `PageWriteEvent`.

   *Approach:* extract a private helper in `write_helper.py`:

   ```python
   def _read_events_cached(journal_path: Path) -> list[Event]:
       reader = journal._CURRENT_READER.get()
       if reader is not None and reader.journal_path.resolve() == journal_path.resolve():
           return reader.events()
       return read_events(journal_path)
   ```

   Switch `_baseline_hash`, `_managed_region_baseline_hash`, and
   `_known_regions_for_file` to call `_read_events_cached` instead of
   `read_events`. No public API change.

   *Done when:* all five tests pass; the safe-write-ordering test
   suite (538 tests) stays green.

1. **CLI handler wrappers.**

   *Depends on:* steps 1-3.

   *Verification mode:* TDD + integration.

   *Tests (integration, in `tests/integration/test_wiki_init.py`
   and `tests/integration/test_wiki_add.py`):*

   - `test_wiki_init_install_pipeline_reads_journal_once_via_cache` —
     monkeypatch `journal.read_events` with a path-scoped counter
     (only counts reads against the vault's journal); run `wiki
     init --recipe family`; assert the counter is `1` (the cache
     loaded once, then `notify_appended` extended it through every
     subsequent write).
   - `test_wiki_add_install_pipeline_reads_journal_once_via_cache` —
     same shape against `wiki add ontology:people` on a fresh vault.
   - **Negative-pin status: dropped.** Earlier drafts named
     `test_wiki_init_without_cache_wrapper_reads_journal_many_times`
     as an integration-level regression-pin that would patch
     `_cmd_init`'s body to bypass the wrapper and assert
     `counter >= 2`. The unit-level
     `test_safe_write_outside_cache_scope_unchanged` (in
     `tests/unit/test_write_helper.py`) does the equivalent job at
     a less brittle boundary (no `_cmd_init` body patching, no
     coupling to the install pipeline's internal write count), so
     the integration-level negative pin was dropped as redundant.
     The two positive pins
     (`test_wiki_init_install_pipeline_reads_journal_once_via_cache`
     and its `wiki add` sibling) carry the integration-level
     contract.

   *Approach:* wrap each install-pipeline handler body with
   `with journal.use_journal_cache(journal_path):`. Two handlers
   to update — `_cmd_init` and `_cmd_add`. The doctor / journal /
   resolve / ingest handlers don't loop writes; no wrapping needed.
   (`_cmd_ingest` was named in earlier drafts but on inspection
   only appends one `IngestRoutedEvent` and never calls
   `safe_write` — the vault-side ingest skill is what produces the
   page writes, in a separate Claude session.)

   *Done when:* the two positive integration pins pass;
   `test_safe_write_outside_cache_scope_unchanged` (unit) carries
   the no-cache regression pin (the original plan named an
   integration-level negative pin but the unit-level pin does the
   equivalent job at a less brittle boundary).

1. **ADR-0002 §Negative gains a §Revisions entry naming this spec.**

   *Depends on:* steps 1-4.

   *Verification mode:* goal-based (grep + visual review).

   *Approach:* append a dated §Revisions-style note to
   `docs/adr/0002-journal-as-state-truth.md` §Negative bullet
   "Replay cost on every CLI invocation" — replace "Mitigated: replay
   over 1000 events fits comfortably under 100ms" with a pointer to
   this spec for the install-pipeline amortisation.

   *Done when:* `grep -rn "journal-reader-cache" docs/adr/` returns
   ≥1 hit; no other ADRs unintentionally changed.

1. **Knowledge entry K-0009 captures the ContextVar-as-cache-handle
   pattern.**

   *Depends on:* steps 1-5.

   *Verification mode:* goal-based.

   *Approach:* append one entry to
   `docs/knowledge/patterns.jsonl` scoped to
   `llm_wiki_kit/journal.py`, capturing the pattern "per-invocation
   cache via `ContextVar`, with `notify_appended` from the writer
   so the cache stays in sync without mtime polling". Cite K-0007
   (the flock-everywhere lesson) as a related entry — both are
   "thread the cross-cutting concern through every site, don't
   bolt it on at one end" patterns.

   *Done when:* `bash tools/lint-knowledge.sh` passes; the entry
   parses; the JSONL line counts add up.

1. **Spec status flips; issue checkbox ticks.**

   *Depends on:* steps 1-6.

   *Verification mode:* goal-based.

   *Approach:* spec `Status: Draft → Implemented`; plan
   `Status: Drafting → Done`. Tick qC4's checkbox in the PR body.

## Verification gate

```
pytest tests/unit/test_journal.py tests/unit/test_write_helper.py tests/integration/test_wiki_init.py tests/integration/test_wiki_add.py
pytest -q                          # full suite
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
bash tools/hooks/pre-pr.sh
```

## Risks

- **Cache hides a real bug in `read_events`.** A future change to
  `read_events` that broke its return shape would be masked inside
  the cache scope: the cache returns the load-time list, regardless
  of subsequent disk state. *Recovery:* the negative pin
  `test_safe_write_outside_cache_scope_unchanged` keeps the
  fall-through path exercised at every test run. Plus the doctor's
  end-to-end coverage uses `read_events` directly, so a real
  regression surfaces.
- **`notify_appended` skipped for an `append_event` call from inside
  `journal.transaction()`.** The held-fd append branch in
  `append_event` reuses the open fd; the cache hook fires there too
  (same `_CURRENT_READER` lookup at the bottom of the function). Pin
  via `test_append_event_inside_transaction_still_notifies_reader`.
  *Recovery:* if the lookup is missed in the held-fd branch, the
  cache and disk diverge inside transactions — a subtle bug. The
  pin makes it visible at test time.
- **`use_journal_cache` non-recursive guard interacts with
  `journal.transaction()`.** Both use `ContextVar`s; they're
  orthogonal (different ContextVar instances). Test:
  `test_use_journal_cache_nested_inside_transaction_is_allowed` and
  vice versa.
- **CLI scope wraps `append_event` calls that today fire BEFORE the
  install-pipeline body.** `_cmd_init` appends a `VaultInitEvent`
  before the `install_primitives` call. The wrapper should bracket
  *all* journal-touching code in the handler (the VaultInit, the
  per-primitive installs, the region aggregator), so the journal
  read happens once at the very first read. *Recovery:* the
  integration test `test_wiki_init_install_pipeline_reads_journal_once_via_cache`
  asserts read-count = 1 across the whole handler body.

## Out of scope

- **`safe_write` / `safe_write_region` / `resolve_proposal` signature
  changes.** The ContextVar threading is internal; public API stays.
- **Process-global cache or cross-invocation cache.** Lifetime is
  one `with` block.
- **mtime-based invalidation.** In-process notification is exact;
  mtime resolution varies.
- **Checkpoint events.** Separate spec, separate trade-off, ADR-0002
  §Negative names that as the path past 10k events.
- **A `read_events_cached` public API.** Cache is internal.
