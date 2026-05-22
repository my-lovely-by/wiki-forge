# ADR-0002: The journal is the single source of truth for vault state

- **Status:** Accepted
- **Date:** 2026-05-15
- **Deciders:** maintainer
- **Related:** RFC-0001, ADR-0003, ADR-0004, ADR-0005, `docs/architecture/overview.md` ("The journal")

## Context

A vault built by the kit accumulates state over time: which primitives
are installed, which sources have been ingested, which pages have been
written, which operations have run, when conflicts were resolved. Some
of this state needs to be queryable on every kit invocation:

- "Is the `meeting` content-type installed?" — `wiki ingest` routing.
- "Have I already ingested this transcript?" — duplicate-source detection.
- "Did the user edit this page since I last wrote it?" — drift detection.
- "When did `weekly-digest` last run?" — idempotency for scheduled ops.

The state-tracking models commonly available:

1. **Separate lockfile or manifest** (npm, pip, conda) — a single JSON/TOML
   file rewritten on every change. Simple to read, but rewrite races with
   editors, no history, no audit trail.
2. **Database** (SQLite) — fast queries, schema migrations, but adds a
   binary file the user can't grep, a dep, and a recovery path when it
   corrupts.
3. **Append-only event log** (Kafka, event sourcing) — every state change
   is an immutable event; current state is derived by replay. History is
   free; recovery is "replay from a checkpoint."
4. **Filesystem as state** — let the on-disk presence/absence of files
   be the truth. No separate state file at all.

The kit's needs are skewed toward audit trail, recovery, and conflict
resolution: when something went wrong, the user (and Claude) need to be
able to ask "what was the kit's last understanding of this file?" without
forensics.

## Decision

> **A single append-only JSONL file at `.wiki.journal/journal.jsonl` is
> the source of truth for vault state. Every state-changing kit operation
> appends one validated event before touching disk, guarded by
> `fcntl.flock` exclusive locking (where the filesystem supports it)
> and an `fsync` on the journal fd before the call returns. Current
> state is derived by replay.**

Mechanics:

- Each line is a JSON object with a `type` discriminator field and
  event-specific payload, validated through a Pydantic v2 discriminated
  union (`Event`) defined in `models.py`.
- `journal.append_event(path, event)` validates, takes `fcntl.flock`
  (`LOCK_EX`) on the journal fd, appends one JSONL line, then
  `fsync`s before releasing the lock. Two simultaneous appends from
  different processes serialize cleanly; the line is durable on disk
  before the call returns. Multi-event sequences (e.g. an operation
  run) wrap the work in `journal.transaction(path, by, reason)`,
  which holds the lock for the duration of the `with` block and
  brackets the body with `lock.acquired` / `lock.released` events.
  Filesystems that reject `flock` (iCloud Drive, some SMB / NFS
  mounts) fall back to the pre-spec append-without-locking behavior
  with a one-shot `WARNING` log; see
  [`docs/specs/journal-locking/spec.md`](../specs/journal-locking/spec.md)
  §Edge cases.
- `journal.read_events(path)` parses and validates every line; on the
  first malformed line, raises `JournalCorruptError(line=N)` with field
  context. We fail loudly — corrupted state is the user's signal, not
  ours to paper over.
- `journal.replay_state(events)` returns a `VaultState` (installed
  primitives, page-write history keyed by path, ingested sources,
  recent operation runs, recent research queries).
- No separate manifest, lockfile, or state cache. If a question can't
  be answered by replaying the journal, it doesn't have an answer.

The journal lives under `.wiki.journal/` so the user can see it,
optionally git-track it, and back it up. We do not hide it under a dot-
prefixed home-dir cache.

## Consequences

### Positive

- **Audit trail is free.** Every state change is in the journal in order.
  `wiki journal tail` is the kit's debugger.
- **Drift detection is trivial.** The latest `PageWrite` event for a path
  contains the hash; compare to on-disk hash to know if the user edited.
  (See ADR-0004.)
- **One state model, not three.** Without a manifest + cache + on-disk
  inference, there's nothing to reconcile.
- **Recovery is conceptually simple.** Replay the journal, accept what
  it says. The user can grep/edit it as plain text in an emergency.
- **Git plays nicely.** Append-only files don't produce merge conflicts
  on independent additions; concatenation is the resolution.

### Negative

- **Replay cost on every CLI invocation.** Mitigated: replay over 1000
  events fits comfortably under 100ms (acceptance criterion in Task 4).
  The install pipeline's per-write baseline lookups (which formerly
  re-read the journal on every ``safe_write``) are further amortised
  by an in-process ``JournalReader`` cache scoped to the active CLI
  handler — see
  [`docs/specs/journal-reader-cache/spec.md`](../specs/journal-reader-cache/spec.md).
  If vaults grow past 10k events, we add a checkpoint event type; the
  schema accommodates it without breaking compatibility.
- **No transactional writes across journal + disk.** If the kit crashes
  between appending an event and writing the file, the journal records
  an intent that didn't materialize. Mitigated: `safe_write` appends
  the event before touching disk per
  [`docs/specs/safe-write-ordering/spec.md`](../specs/safe-write-ordering/spec.md);
  a crash between the event append and the disk write is reconciled by
  `wiki doctor`'s `missing` / `page-drift` / `managed-region-drift`
  checks. Crash window is small and recoverable.
- **Schema evolution requires care.** Adding a field to an existing
  event type must default-populate for older lines. Pydantic v2 makes
  this explicit via `default=` and `model_validator(mode="before")`.
- **Concurrent writers require an advisory lock.** The journal
  assumes a single writer at a time; concurrent processes are
  serialized by `fcntl.flock` around every `append_event` call, and
  long multi-event sequences hold the lock via
  `journal.transaction()`. Implemented in
  [`docs/specs/journal-locking/spec.md`](../specs/journal-locking/spec.md)
  on 2026-05-16; the `wiki-lock` skill and `wiki doctor`'s stale-lock
  check are the user-facing surface.

### Neutral / monitor

- If vault sizes grow past the 10k-event mark, evaluate (a) checkpoint
  events, (b) a SQLite read-through index. Both are additive and don't
  change the source-of-truth model.
- If the JSONL parsing cost dominates a benchmark, evaluate an in-
  memory cache invalidated on file mtime. Don't pre-optimize.

## Alternatives considered

### Alt 1: SQLite as state

Tempting: fast queries, schema migrations, the user already has SQLite.
Loses because:

- The user can't `cat` or `grep` it. Recovery requires opening the DB.
- Adds a binary file to git. Diffs are useless.
- Corruption recovery is harder: no human can edit a corrupted SQLite
  file to limp forward.
- The journal's natural shape is "events in time order" — SQL would
  encode this as a single big append-only table anyway.

### Alt 2: Filesystem as state

Let on-disk presence of files be the truth. Loses because:

- No way to tell *who* wrote a file (kit vs. user) without metadata.
- No history. We can't answer "when did this page last change?" without
  filesystem mtime, which is unreliable.
- No way to tell "did this source already get ingested?" without parsing
  every page and looking for a frontmatter source field — slow and fragile.

### Alt 3: Lockfile (single JSON file rewritten on change)

The npm / pip model. Loses because:

- Rewrite is not atomic; concurrent editors race.
- No history without keeping old copies somewhere.
- No clean way to record "this conflict was resolved on date X by these
  three lines from each side"; we'd end up with a separate audit log
  next to the lockfile, at which point we have two state files.

### Alt 4: Distributed event store / Kafka / etc.

Overkill. The kit is a single-user CLI; the operational complexity of
running an event broker would defeat the purpose of the kit.

## References

- [Event Sourcing pattern](https://martinfowler.com/eaaDev/EventSourcing.html)
- ADR-0003 (managed regions) — the journal records `managed_region.write`
  events.
- ADR-0004 (drift detection) — the journal's `PageWrite` events are the
  baseline for drift detection.
- ADR-0005 (Pydantic for schemas) — event validation lives there.
- Migration RFC `docs/rfc/0001-v2-architecture.md` (Task 4)
