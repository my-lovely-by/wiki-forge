# Spec: journal-reader-cache

> **Living document.** Updated alongside the code. Drift between spec
> and code is a bug — fix the code or the spec in the same PR.

- **Status:** Implemented
- **Owner:** `llm_wiki_kit.journal` / `llm_wiki_kit.write_helper`
- **Related:** [ADR-0002](../../adr/0002-journal-as-state-truth.md) (journal as state truth — §"Neutral / monitor" already names "in-memory cache invalidated on file mtime" as a future option), [`docs/specs/safe-write-ordering/spec.md`](../safe-write-ordering/spec.md) §Non-goals "Not a `_baseline_hash` performance optimization", retro-review issue [#23](https://github.com/eugenelim/llm-wiki-kit/issues/23) (finding `qC4`)

## What this is

`write_helper.safe_write` / `safe_write_region` / `resolve_proposal`
each look up a per-path baseline by walking the journal backward
(`_baseline_hash`, `_managed_region_baseline_hash`,
`_known_regions_for_file`). Today, every lookup calls
`journal.read_events(journal_path)`, which re-parses every line of
`.wiki.journal/journal.jsonl` from scratch — O(events) per write.

`wiki init --recipe family` exercises this pathology: every primitive's
`safe_write` call replays the *entire* journal-to-date to find a
baseline that, in the fresh-install case, is `None`. With 200+ writes
across a recipe install and a journal that grows monotonically as those
writes themselves append events, the install becomes O(events × writes)
— quadratic in journal length.

This spec adds a per-CLI-invocation cache in front of `read_events`:

- `JournalReader(journal_path)` reads the journal once on first
  `events()` call, then serves subsequent calls from memory.
- `journal.append_event` extends the cached list in-memory after its
  `fsync` returns, so the cache stays consistent with disk for the
  remainder of the invocation (single-writer assumption — the kit's
  CLI handlers are strictly serial per ADR-0002).
- A `use_journal_cache(journal_path)` context manager installs a
  reader for the duration of one handler's body; outside the scope,
  `read_events` semantics are unchanged (every caller still gets a
  fresh read).
- Activation is per-handler-opt-in via the context manager; no
  signature change to `safe_write` / `safe_write_region` /
  `resolve_proposal` / `append_event`. The reader lookup is via a
  `ContextVar`, matching the pattern `journal.transaction()` already
  uses for its held-fd lookup.

## Inputs

- **`JournalReader(journal_path: Path)`** — constructor takes the
  journal path; reader holds it as its identity. Multiple readers for
  the same journal in the same context is **invalid** — the scope
  manager raises (analogous to the non-recursive `transaction()`).
- **`use_journal_cache(journal_path: Path)`** — context manager,
  installs a fresh `JournalReader` for the duration of the `with`
  block. The journal path is resolved (`.resolve()`) on entry; the
  reader's identity is the resolved path.
- **`journal.append_event(journal_path, event, *, nonblocking=False)`** —
  unchanged signature; the cache hook is internal.

## Outputs

- **`JournalReader.events()` → `list[Event]`** — returns the cached
  list. First call reads from disk (`read_events(self.journal_path)`);
  subsequent calls return the same list (extended by
  `notify_appended`). The returned list is the *internal* cache; the
  reader trusts its sole in-process caller (`write_helper`'s baseline
  lookups) not to mutate it. A defensive copy would double the
  memory cost for no real safety win against in-process callers.
- **`JournalReader.notify_appended(event: Event)` → `None`** —
  appends to the internal list if `events()` has been called; no-op
  otherwise (lazy load avoids reading the disk twice for the no-op
  case). Called by `append_event` after the line is `fsync`'d.
- **`use_journal_cache(journal_path)` → context manager** — sets a
  `ContextVar[JournalReader | None]` on enter, resets on exit. Any
  `_baseline_hash` / `_managed_region_baseline_hash` /
  `_known_regions_for_file` call inside the `with` block consults
  the reader; outside, they read from disk.
- **No new event types, no schema changes, no journal-format changes.**

## Behavior

### Cache miss → cache hit

1. CLI handler enters `with use_journal_cache(journal_path):`.
2. First `safe_write` calls `_baseline_hash(journal_path, relative_path)`.
   The helper checks the `ContextVar`; finds a `JournalReader` whose
   resolved `journal_path` matches; calls `reader.events()`.
3. `reader.events()` sees `self._events is None`, reads from disk
   via `read_events(self.journal_path)`, caches the list, returns it.
4. `_baseline_hash` walks the returned list in reverse, returns the
   matching `PageWriteEvent.hash` or `None`.
5. `safe_write` appends a new `PageWriteEvent` via `append_event`.
6. `append_event` (after fsync) consults the same `ContextVar`,
   finds the reader, calls `reader.notify_appended(event)` — the
   in-memory list grows by one.
7. The next `safe_write` calls `_baseline_hash` again; `reader.events()`
   returns the same cached list (now N+1 long); no disk read.

### Cache scope boundary

A `safe_write` invoked *outside* `use_journal_cache` finds
`_CURRENT_READER.get() is None`; the lookup falls through to today's
`read_events(journal_path)` call. Existing test code that calls
`safe_write` directly (no `with` scope) continues to work
byte-identically.

A `safe_write` invoked *inside* `use_journal_cache(j_a)` but writing
to `j_b` (different journal) finds the reader's resolved path doesn't
match the call's `journal_path.resolve()`; falls through to
`read_events(j_b)` (a stale cache for `j_a` must not pollute
baselines for `j_b`). The reader's hooks in `append_event` apply the
same path-equality test before calling `notify_appended`.

### Concurrent writer (separate process)

ADR-0002 §Negative names this case explicitly: "Concurrent writers
require an advisory lock." The journal-locking spec serialises
`append_event` calls across processes. Within a single CLI invocation
(this cache's scope), no second writer is in play; the cache's
in-memory list stays equal to the on-disk file at every `safe_write`
return.

If a second process appends events to the same journal while a CLI
handler is mid-scope, the cache becomes stale (the cached list is
shorter than the on-disk file). The kit's CLI handlers run strictly
serial per ADR-0002; the documented assumption holds. If a future
runner ships that parallelises CLI invocations against the same
vault, the recovery is to either (a) tighten the scope (drop the
cache for the parallel runner) or (b) add an mtime-check
invalidation. Neither is in this spec.

### Edge cases

- **Empty journal.** `read_events` returns `[]`; `events()` returns
  `[]`; baseline lookups return `None`. Identical to no-cache today.
- **Journal does not yet exist.** Same as above — `read_events`
  handles the missing-file case by returning `[]`. The cache is
  pre-populated with an empty list on first `events()` call;
  subsequent `notify_appended` extends it.
- **Nested `use_journal_cache`.** A handler that wraps another's body
  while both are in the `with` block is a programming error.
  `use_journal_cache` raises `RuntimeError` on re-entry, matching
  `journal.transaction()`'s precedent.
- **Reader-then-append for a different path.** A handler installs a
  cache for journal `A`, then `append_event(journal_B, ...)` fires
  (e.g. a doctor pass against a sibling vault — not a real flow today,
  but defensive). The append's path-equality check filters: the
  reader for `A` is not extended; the on-disk write to `B` lands
  normally.
- **Exception inside the `with` block.** The reader is reset on exit
  (ContextVar token); a partial cache is discarded along with the
  scope. The on-disk journal still reflects every `append_event` that
  fsync'd before the exception (event-before-disk invariant from
  safe-write-ordering).
- **Tests that drive `append_event` directly inside a `with
  use_journal_cache` block.** `append_event` notifies the reader the
  same way `safe_write` does. Test integration: any test seeding the
  journal directly before the SUT call sees its events through the
  reader.

## Invariants

- **`JournalReader.events()` ≡ `read_events(journal_path)` at every
  `safe_write` / `safe_write_region` / `resolve_proposal` return.**
  Within a single-process scope, the cache is a faithful in-memory
  mirror of the on-disk file; the equivalence holds modulo concurrent
  external writers (out of scope, ADR-0002 §Negative).
- **No behavior change.** This spec is a pure performance
  optimization: the cache returns the same events `read_events`
  would have read, so every baseline-lookup result is bit-identical
  to today's. The safe-write-ordering spec's contracts (event-before-
  disk, qC6 drift, adopt fast-path) all hold unchanged.
- **Activation is opt-in.** Code paths that do not enter
  `use_journal_cache` see no behavior change. Tests calling
  `safe_write` / `append_event` outside any scope retain today's
  semantics exactly.

## Contracts with other modules

- **`journal.py`** owns `JournalReader`, `_CURRENT_READER`, and
  `use_journal_cache`. `append_event` gains an internal
  `notify_appended` call on the matching reader (after `fsync`,
  before return). `transaction()` is unaffected — its held-fd path
  bypasses the per-call `flock` but the cache notification still
  fires per event.
- **`write_helper.py`** is unchanged at the public-API level.
  Internally, `_baseline_hash`, `_managed_region_baseline_hash`, and
  `_known_regions_for_file` switch from `read_events(journal_path)`
  to a new `_read_events_cached(journal_path)` helper that consults
  the `ContextVar` and falls through to `read_events` when no reader
  is installed.
- **`cli.py`** wraps the install-pipeline handlers (`_cmd_init`,
  `_cmd_add`) with `use_journal_cache(journal_path)`. Handlers that
  do not loop many writes (`_cmd_doctor`, `_cmd_journal_*`,
  `_cmd_resolve`, `_cmd_ingest`) skip the cache — the overhead of
  one read is amortised over zero subsequent reads, so the
  optimisation isn't needed. (`_cmd_ingest` today appends exactly
  one `IngestRoutedEvent` per invocation and does not call
  `safe_write`; the vault-side `ingest-<name>/SKILL.md` is what
  produces the page writes, in a separate Claude session. If a
  future change moves source-document rendering into the kit, this
  carve-out gets revisited.)
- **`install.py`** is unchanged. The aggregator's `safe_write_region`
  loop benefits transparently — every `safe_write_region` call
  inside the cache scope hits the cache.
- **`doctor.py`** is unchanged. `run_doctor` calls `read_events` /
  `read_events_lenient` directly (one read, no scope needed); the
  doctor pass doesn't write, so cache-staleness is not a concern.
- **`ADR-0002`** §"Neutral / monitor" mentions an "in-memory cache
  invalidated on file mtime" as a possible future direction. This
  spec is the in-process version of that idea, but invalidates via
  in-process `notify_appended` rather than mtime (mtime resolution
  varies by filesystem; in-process notification is exact). §Negative
  gets a §Revisions-style amendment naming this spec as the lookup-
  cost mitigation.

## Acceptance criteria

### Cache hit reduces journal reads

- [x] `test_journal_reader_caches_events_within_scope` — inside
      `use_journal_cache`, two successive `events()` calls return
      the same list object identity. (Brittle? Yes — that's the
      point. The cache is the load-bearing optimisation and the
      identity check is the cheapest pin for "did not re-read from
      disk".)
- [x] `test_journal_reader_lazy_loads_only_when_events_called` /
      `test_safe_write_inside_cache_scope_reads_journal_once` —
      monkeypatch `journal.read_events` with a counter; install a
      cache; call `safe_write` N times for distinct paths; assert
      `read_events` was invoked exactly once.
- [x] `test_safe_write_outside_cache_scope_unchanged` — calling
      `safe_write` outside `use_journal_cache` produces the same
      result as today (no cache, no behavior change).

### Cache stays consistent with disk on append

- [x] `test_append_event_notifies_active_reader_on_matching_journal` —
      install a cache, call `events()` (forces load), call
      `append_event` directly, call `events()` again; the new event
      appears at the tail of the returned list.
- [x] `test_safe_write_inside_cache_sees_just_appended_event` —
      install a cache; call `safe_write` for `path.md` once (appends
      a `PageWriteEvent`); call `safe_write` for the same path again
      with matching content; the second call's `_baseline_hash`
      consults the cache and routes to direct-write (not adopt, not
      proposal). Tests the qC6 cross-cutting concern with the cache
      in play.

### Scope discipline

- [x] `test_use_journal_cache_installs_and_resets_contextvar` —
      after the `with` block, the `ContextVar` is reset; a
      subsequent `_read_events_cached` call falls through to disk.
- [x] `test_use_journal_cache_resets_on_exception` — same, but the
      body raises; the `finally` arm still resets.
- [x] `test_use_journal_cache_non_recursive_raises` — entering
      `use_journal_cache` while another is active in the same
      context raises `RuntimeError` (analogue of
      `transaction()`'s re-entry guard).
- [x] `test_append_event_does_not_notify_reader_for_different_journal` —
      install a cache for journal `A`; call `append_event(journal_B,
      event)`; the cache for `A` is unchanged.

### CLI wiring

- [x] `test_wiki_init_install_pipeline_reads_journal_once_via_cache` —
      monkeypatch `journal.read_events` with a counter (path-scoped
      to the vault's journal); run `wiki init --recipe family`;
      assert the install pipeline read the journal at most once
      (the cache absorbs the rest). Integration-level pin against a
      future refactor that bypassed the cache.
- [x] `test_wiki_add_install_pipeline_reads_journal_once_via_cache` —
      same shape against `wiki add`.

## Non-goals

- **Not a per-process cache.** Cache lifetime is bounded by a single
  `use_journal_cache` scope. Subsequent CLI invocations start fresh.
- **Not a cross-process cache.** Concurrent writers would invalidate
  any in-process cache; the journal-locking spec serializes
  `append_event` but does not coordinate readers.
- **Not an mtime-based invalidation.** Filesystems vary in mtime
  resolution; in-process notification via `append_event` is exact
  and avoids the variance.
- **Not a `read_events_cached` public API.** The cache is internal to
  the write-helper baseline lookups and the journal append hook;
  callers that want events should keep calling `read_events`.
- **Not a checkpoint-event optimisation.** ADR-0002 §Negative names
  checkpoint events as the future direction for vaults past 10k
  events. This spec is a simpler in-process amortisation that buys
  significant headroom without touching the journal format.
- **Not a `safe_write` signature change.** The cache is threaded
  through a `ContextVar`, not a new kwarg. Existing callers — tests
  and production handlers — keep their current call sites unchanged.
