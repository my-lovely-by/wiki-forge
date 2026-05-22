# Spec: safe-write-ordering

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Implemented
- **Owner:** `llm_wiki_kit.write_helper`
- **Related:** [ADR-0002](../../adr/0002-journal-as-state-truth.md) (journal as state truth — `safe_write` is its largest event producer; §Negative amended by this spec), [ADR-0003](../../adr/0003-managed-regions-for-shared-files.md) (managed regions — shares the proposal path; not amended by this spec), [ADR-0004](../../adr/0004-drift-detection-and-proposal-flow.md) (drift detection — §Mechanics + §Negative amended by this spec via a §Revisions entry), [`docs/specs/safe-write-ordering/plan.md`](plan.md), retro-review issue [#23](https://github.com/eugenelim/llm-wiki-kit/issues/23) (findings `qC3`, `qC6`, `C2`)

## What this is

`safe_write` is the sanctioned write path into a user's vault
(ADR-0004). Three retro-review findings against it share one root —
the function's contract is loose around (a) what order it touches the
journal and the disk, (b) what it does when the disk already holds a
file the journal has never seen, and (c) which "kit writes to vault"
paths are exempt:

- **qC3 — event/disk ordering.** ADR-0002 §Decision pithy quote says
  every state-changing kit operation "appends one validated event
  before touching disk." `write_helper.safe_write` (and its peers
  `safe_write_region` and `resolve_proposal`) do the opposite: write
  the bytes, *then* `append_event`. The spec text and the code
  disagree.
- **qC6 — unjournaled existing files (page-scope only).** `safe_write`
  to an existing file with no prior `PageWriteEvent` silently
  overwrites. ADR-0004 §Mechanics step 2 explicitly says "no journal
  entry → direct write" and the test
  `test_first_write_overwrites_existing_file_without_journal_entry`
  pins that wording. The only mitigation is `wiki init`'s empty-dir
  guard; `wiki add`, `wiki ingest`, and future operation-runner paths
  do not enforce it. A user who drops a markdown file into the vault
  and then runs `wiki add` loses their content.
- **C2 — `.obsidianignore` bypass.** `_ensure_obsidianignore` in
  `write_helper.py` writes `.obsidianignore` with `Path.write_text`
  directly, not through `safe_write`. The AGENTS.md anti-pattern
  "Bypassing `write_helper.safe_write()` for a write that lands in a
  user's vault" is violated by `write_helper.py` itself, with no
  comment naming the exception.

This spec defines one coherent contract that resolves all three:
event-before-disk ordering, drift on unjournaled existing pages
(page-scope only), and `.obsidianignore` as a *documented*
non-journaled bypass alongside `resolve_proposal` — matching the
second half of the issue body's C2 fix sketch. ADR-0002 §Negative and
ADR-0004 §Mechanics / §Negative are amended via dated §Revisions
entries in the PR that implements the spec (the same mechanism
ADR-0004 already uses for its 2026-05-15 and 2026-05-16 amendments;
this is not a supersession — the §Decision pithy quotes hold; only
the explanatory body changes).

The scope is the three `write_helper` public functions and the
`_ensure_obsidianignore` helper. Drift detection itself (the hashes-
match check), the proposal-and-resolve flow, managed-region
semantics, and the `wiki-conflict` skill UX are unchanged.

## Inputs

The signatures do not change; the spec re-states them so the contract
is self-contained.

- **`safe_write(path, content, by, journal_path)`** — `path` is a
  vault path (absolute or relative to the vault root, which is
  `journal_path.parent.parent`). `content` is the bytes the caller
  wants written. `by` is the primitive or operation name.
  `journal_path` points at `.wiki.journal/journal.jsonl`.
- **`safe_write_region(file_path, region_id, new_content, by, journal_path)`** —
  same vault-root resolution; `region_id` names the managed region
  inside `file_path`. Behavior is unchanged for the
  no-prior-event case (see §Non-goals "Why qC6 is page-scoped").
- **`resolve_proposal(path, content, by, journal_path)`** — the
  documented `safe_write` bypass for `wiki-conflict` resolution. Same
  vault-root resolution.
- **`_ensure_obsidianignore(vault_root)`** — internal helper called
  by the proposal paths; this spec keeps its non-journaled status but
  documents the exception (see §`_ensure_obsidianignore` and
  §Contracts).

## Outputs

- **`safe_write` returns `WriteResult.WRITTEN` or `WriteResult.PROPOSAL`** —
  same enum, same two values. A new internal short-circuit (the
  byte-identical-adopt fast-path; see §Behavior) returns
  `WriteResult.WRITTEN` without touching the file. Side effects are
  rearranged per §Behavior: one journaled `PageWriteEvent` or
  `PageProposalEvent` is `fsync`'d to disk *before* the target file
  or sidecar is opened for writing.
- **`safe_write_region` returns `WriteResult.WRITTEN` or `WriteResult.PROPOSAL`** —
  same enum. Event-before-disk ordering applies here too: the
  `ManagedRegionWriteEvent` (happy path) or `PageProposalEvent` (drift
  path) is appended before the file write. The unjournaled-region
  case is *not* changed (see §Non-goals).
- **`resolve_proposal` returns `None`** — unchanged. The two events
  it appends (`PageWriteEvent` + `PageConflictResolvedEvent`, plus any
  `ManagedRegionWriteEvent`s the F-B1 fix emits) are appended *before*
  the target file is rewritten and the sidecar is deleted.
- **`_ensure_obsidianignore`** — unchanged signature, unchanged
  output. Writes `.obsidianignore` directly, does not journal. The
  spec promotes it from a quiet bypass to an *explicitly documented*
  one with a leading comment naming this spec and ADR-0004 §Negative.
- **No new event types.** The existing `PageWriteEvent`,
  `PageProposalEvent`, `PageConflictResolvedEvent`, and
  `ManagedRegionWriteEvent` cover every state change this spec
  introduces.
- **Doctor surface unchanged.** `.obsidianignore` is not journaled,
  so it never enters the kit-owned territory derived by
  `check_orphans` (qC10 + C6 replaced the previous static
  `KIT_OWNED_FILES` tuple with a derivation from journaled writes).
  `wiki doctor` continues to treat it as user-territory; the file is
  silently produced by the proposal path and the kit makes no
  drift-detection claim on it. See §Non-goals
  "Why `.obsidianignore` is not journaled".

## Behavior

### Happy path — `safe_write` direct write (file does not exist)

1. Resolve `abs_path`, `relative_path`, `vault_root` as today.
2. Compute `new_hash = sha256(content)`. No on-disk read (file does
   not exist).
3. Look up `baseline_hash` (latest `PageWriteEvent` for `relative_path`).
   `baseline_hash is None and not abs_path.exists()` — first write,
   fresh path. Proceed.
4. **Append `PageWriteEvent(timestamp, by, relative_path, hash=new_hash)`**
   to the journal. The journal-locking spec guarantees the line is
   `fsync`'d before `append_event` returns.
5. Create parent directories; write `content` to `abs_path`.
6. Return `WriteResult.WRITTEN`.

### Happy path — `safe_write` repeat write (file exists, no drift)

1. Resolve as above.
2. Compute `new_hash` and `on_disk_hash`.
3. Look up `baseline_hash`. `baseline_hash is not None and
   on_disk_hash == baseline_hash` — no drift. Proceed.
4. **Append `PageWriteEvent`**.
5. Write `content` to `abs_path`.
6. Return `WriteResult.WRITTEN`.

### Adopt fast-path — `safe_write` (file exists, no prior event, bytes already match)

The qC6 inversion's natural edge case: the file is on disk with no
journal history, and the bytes the kit would write are *already*
exactly what's there (byte-identical). Producing a
`<path>.proposed` sidecar that is byte-identical to the original
would force the user through the `wiki-conflict` skill for no
substantive reason. Instead:

1. Verify the candidate: `baseline_hash is None and abs_path.exists() and on_disk_hash == new_hash`.
2. **Re-read `abs_path` and recompute `on_disk_hash`.** If the
   re-read hash diverges from `new_hash`, a concurrent editor wrote
   between the original read at the top of `safe_write` and now —
   abandon the fast-path and route directly to the proposal branch
   with the kit's `new_hash` (no predicate re-evaluation; the top-of-
   function `on_disk_hash` snapshot is stale by construction, so the
   first-read-based decision data is no longer trustworthy). The
   re-read shrinks the kit-attributable-phantom-hash window
   substantially; see the residual-races discussion below for the
   exact bound.
3. **Recompute `now = datetime.now(UTC)`** so the journaled
   timestamp reflects the adoption decision (post-re-read), not
   the call entry. Append
   `PageWriteEvent(timestamp=now, by, relative_path, hash=new_hash)`.
   The kit adopts the file as its journaled baseline.
4. Do not write the file (bytes already match; preserves mtime and
   inode).
5. Return `WriteResult.WRITTEN`.

This is the single per-file analogue of `wiki init --adopt`
(unresolved RFC-0001 question), scoped to the unambiguous case where
no resolution is needed because the content is already what the kit
wants. The audit trail records `by` as the primitive or operation
that adopted the file.

A residual race remains: a user edit landing between step 2's
re-read and step 3's `append_event` would journal a hash that no
longer matches on disk. The window is small in practice — Pydantic
validation of the event plus the `mkdir` / `open` / `flock(LOCK_EX)`
sequence inside `append_event`, well below the millisecond editors
typically take between read and save. Not zero, but tight enough
that real-world editors (Obsidian's save, vim's atomic-rename)
won't land inside it under normal use.

A second residual race exists on the journal side: between the
top-of-function `_baseline_hash` lookup and step 3's
`append_event`, another process could append a `PageWriteEvent`
for this path (a concurrent `wiki run` in another shell, for
example), making the kit's "no prior history" assumption stale by
the time we journal. The journal-locking spec serializes
`append_event` calls themselves but does not bracket the
read-decide-append triple — that would require wrapping
`safe_write` in `journal.transaction()`. The kit's CLI handlers
are strictly serial in normal use (one terminal, one
`wiki <command>` at a time), so this race is theoretical for the
target audience; if real users report observed "adopted-then-not"
sequences, the fix is `journal.transaction()` around the adopt
branch. Until then, the recovery is `wiki doctor` reporting
`page-drift` on the next pass; the user runs `wiki-conflict` and
re-establishes the baseline.

### Drift path — `safe_write` (file exists, hash mismatch OR unjournaled-non-matching)

1. Resolve as above.
2. Compute `new_hash` and `on_disk_hash`.
3. Either: (a) `baseline_hash` exists and `on_disk_hash != baseline_hash`
   (the classic drift case), or (b) `baseline_hash` is `None` and the
   file exists on disk with `on_disk_hash != new_hash` (the qC6 case —
   kit is about to write over a user file it never journaled, and the
   content differs). The byte-identical case is handled by the
   fast-path above.
4. **Append `PageProposalEvent(timestamp, by, relative_path, proposed_path, hash=new_hash)`**
   to the journal.
5. Create parent directories for the sidecar; write `content` to
   `<abs_path>.proposed`.
6. Call `_ensure_obsidianignore(vault_root)` (still non-journaled;
   see §Non-goals).
7. Return `WriteResult.PROPOSAL`.

The on-disk file at `abs_path` is **not** touched on the drift path.
The user's content survives until the `wiki-conflict` skill drives
`resolve_proposal`.

### Happy path — `safe_write_region` (in-region write, no drift)

1. Resolve as today. Read the file, parse regions, compute the
   region's current hash.
2. Compare to the latest `ManagedRegionWriteEvent` for `(file, region)`.
   If match (or **no prior event for `(file, region)`**), no drift —
   proceed. See §Non-goals "Why qC6 is page-scoped" for why the
   no-prior-event case stays on the direct-write path here.
3. **Append `ManagedRegionWriteEvent(timestamp, by, file, region, content_hash=new_region_hash)`**.
4. Write the rewritten file (managed region replaced, rest verbatim)
   to disk.
5. Return `WriteResult.WRITTEN`.

### Drift path — `safe_write_region` (in-region body mismatch)

1. Resolve and parse as above.
2. Compute the rewritten file body (managed region replaced).
3. **Append `PageProposalEvent`** naming the parent file and the
   `<file>.proposed` sidecar; `hash` is the sha256 of the rewritten
   *file*, not the region.
4. Write the rewritten file to `<abs_path>.proposed`.
5. Call `_ensure_obsidianignore` (non-journaled).
6. Return `WriteResult.PROPOSAL`.

### `resolve_proposal` (user-mediated merge)

1. Resolve `abs_path`, `relative_path`, `vault_root` as today.
2. Compute `new_hash = sha256(content)`.
3. **Append `PageWriteEvent(timestamp, by, relative_path, hash=new_hash)`**.
   This is the new baseline; subsequent `safe_write` calls see no drift.
4. **Append `PageConflictResolvedEvent(timestamp, by, relative_path, hash=new_hash)`**
   (audit).
5. Walk the journal for `ManagedRegionWriteEvent`s on this file. For
   every known region present in the resolved content, **append one
   `ManagedRegionWriteEvent`** with the region's hash in the resolved
   content (the F-B1 fix, unchanged in shape, moved before the disk
   write). If the resolution destroys markers, skip the region event
   loop but still write the resolved content to disk — the user's
   merge survives; subsequent `safe_write_region` calls will raise
   `ManagedRegionError` on the next attempt, which is the surfacing
   mechanism. (Pre-spec behaviour returned without writing, which
   silently discarded the user's resolution work. The event-before-
   disk reorder forced this question, and "preserve the user's
   resolution" is the safer answer.)
6. Write `content` to `abs_path`.
7. Delete `<abs_path>.proposed` if present.

### `_ensure_obsidianignore` (documented non-journaled bypass)

The helper's signature, body, and call sites are unchanged. The
spec's contribution is to (a) introduce a module-level constant
`OBSIDIANIGNORE_BYPASS_DOC = "docs/specs/safe-write-ordering/spec.md"`
in `write_helper.py` whose value is the spec's path, (b) have
`_ensure_obsidianignore`'s docstring cite the constant by name
(`See OBSIDIANIGNORE_BYPASS_DOC §Non-goals "Why .obsidianignore is
not journaled" and ADR-0004 §Negative for the rationale.`), and
(c) call out the bypass in the AGENTS.md anti-pattern carve-out
alongside `resolve_proposal`. A test imports the constant and
asserts its value, so a paraphrase of the docstring doesn't
silently shift the contract — the *constant* is the addressable
anchor, and `grep OBSIDIANIGNORE_BYPASS_DOC` surfaces every
dependency. The reasoning:

- The file's purpose is to keep Obsidian from indexing `.proposed`
  sidecars. Its content is one regex line the kit appends additively.
- Journaling it as a `PageWriteEvent` and adding it to
  `KIT_OWNED_FILES` would force every user edit (adding their own
  ignore pattern) to register as `page-drift` in `wiki doctor`. The
  file is user-editable by design (it's how Obsidian configures its
  index); a drift report on every user edit is the wrong UX.
- Routing through `safe_write` proper would produce
  `.obsidianignore.proposed` on user drift, which Obsidian would then
  index (because `.obsidianignore` itself no longer has the
  proposed-pattern). The bootstrap is self-defeating.

The helper's additive-merge body (read existing, skip if
pattern present, append) survives. Idempotency is enforced by the
pattern-already-present check, not by journal events. The trade-off
is recorded in ADR-0004 §Negative via the §Revisions entry this
spec lands.

### Edge cases

- **First write to a fresh vault (no journal entry, no on-disk file).** —
  Direct-write path. No drift, no proposal. `wiki init` rendering,
  every primitive's first install, lands here.
- **First write to an existing file with no prior journal entry,
  bytes already match (`new_hash == on_disk_hash`).** — Adopt
  fast-path: journal `PageWriteEvent`, skip the disk write, return
  `WRITTEN`. No `.proposed` sidecar, no `wiki-conflict` invocation
  required.
- **First write to an existing file with no prior journal entry,
  bytes differ.** — Drift path: write `.proposed` sidecar, journal
  `PageProposalEvent`, return `PROPOSAL`. The pre-existing file is
  untouched.
- **`safe_write` to a path that is currently a `.proposed` sidecar of
  another file.** — Out of contract; caller bug. No new defense.
- **Any of `safe_write` / `safe_write_region` / `resolve_proposal`
  against a path that resolves outside the vault root (e.g. via a
  vault-internal symlink pointing at an external directory).** —
  Deliberate refusal: the shared `_relative_to_vault` helper raises
  `WikiError` rather than journaling a path whose resolved target
  diverges from its lexical position. The journal must not record
  a path that escapes the vault — a subsequent write against the
  same lexical path would diverge from the resolved target and
  silently split the baseline. Retro-review qC9; pinned by
  `test_safe_write_rejects_symlink_that_escapes_vault`.
- **`safe_write_region` to a file that exists but has no prior
  `ManagedRegionWriteEvent` for `(file, region)`** — Direct-write
  path (unchanged). The region's on-disk body is whichever
  primitive's empty template produced it; the kit's first write
  takes ownership. The qC6 inversion is page-scoped only; see
  §Non-goals.
- **Crash between event append and disk write.** Three sub-cases by
  call site:
  - *`safe_write` happy path, fresh file.* Event durable; file
    absent. `wiki doctor check_missing` reports `missing: <path>`.
    Recovery: re-run the operation; on next run, the journaled
    event means `baseline_hash is not None`; the file is absent
    (`on_disk_hash is None`); the `safe_write` predicate routes
    to the **direct-write** branch (the third disjunct in plan
    step 2's spelled-out predicate: "event durable, file absent").
    Net: file lands; `check_missing` clears on the next doctor
    pass. Pinned by
    `test_safe_write_recovers_missing_file_when_baseline_journaled`
    (Acceptance criteria below).
  - *`safe_write_region` happy path.* Event durable; on-disk file
    either intact (write didn't start) or partially rewritten (write
    crashed mid-`write_text`). Partial-rewrite means the region's
    hash matches *neither* the new nor the old —
    `check_managed_region_drift` surfaces as
    `managed-region-drift: <file>:<region>`. Recovery is the
    proposal flow: the second-pass call sees `baseline_hash`
    equal to the just-journaled `new_region_hash` and
    `current_region_hash` equal to the partial-write body
    (different), so the predicate routes to **proposal**, not
    direct-write. A `<file>.proposed` sidecar lands with the kit's
    intended full rewrite; the user invokes the `wiki-conflict`
    skill to reconcile against the half-written file (which they
    can read directly) and the kit's complete version. This is
    not a clean automatic retry — it surfaces as a conflict the
    user must resolve. Acceptable given the rarity of
    crash-during-region-write; the alternative (a third predicate
    disjunct for "partial-write we authored") cannot safely
    distinguish a partial write from a real user edit. Pinned by
    `test_safe_write_region_crash_recovery_routes_to_proposal`
    (Acceptance criteria below).
  - *`resolve_proposal`.* Event(s) durable; target file either
    untouched or partially rewritten. If untouched, the file's hash
    matches no journaled `PageWriteEvent` so
    `check_page_drift` fires. If partially rewritten, the hash
    likewise diverges; same surface. Recovery: re-run `wiki-conflict`
    — the skill reads both `path` and `path.proposed` (which is
    still there because step 7 hasn't run), produces the same merged
    content, and `resolve_proposal` is invoked a second time, this
    time succeeding through to step 7. The second invocation appends
    a duplicate `PageWriteEvent` + `PageConflictResolvedEvent` pair
    (and duplicate `ManagedRegionWriteEvent`s for every known
    region); replay is idempotent under last-write-wins, so derived
    `VaultState` is unaffected, but `wiki journal tail` will show
    two resolution events for one user-visible action. Acceptable
    given the rarity of crash-during-resolve; future work may
    short-circuit when the latest journal entry already matches
    `(path, content_hash)`.
- **Crash between disk write and journal append (pre-spec ordering,
  no longer possible).** — Eliminated by this spec.

### Error cases

- **Vault root resolution failure** — `_relative_to_vault` raises
  `WikiError` (retro-review qB3 + qC9 landed in the same drain-plan
  PR 2 that derived the orphan-territory set; see §Edge cases
  "symlink that escapes the vault"). The wrapping replaces the
  bare `ValueError` that older versions of this helper raised.
- **Journal `append_event` raises (full disk, locked FS, etc.)** —
  Propagates. No disk write happens. The journal is the source of
  truth; if it refuses, the kit refuses.
- **Disk write fails after the event was appended** — The event is
  durable; the file is missing or partial. `wiki doctor` surfaces
  the gap (existing behavior — `check_missing` for absent,
  `check_page_drift` / `check_managed_region_drift` for partial).
  The CLI returns its existing exit codes (`WIKI_ERROR_EXIT` for
  `WikiError`, unhandled exceptions propagate); this spec does not
  introduce a rollback.

## Invariants

- **Every kit write to a user vault file appends a journal event
  *before* the file is opened for writing**, with `_ensure_obsidianignore`
  as the one documented exception (named in §Contracts and
  ADR-0004 §Negative — `resolve_proposal` is the other documented
  exception, but its events come before its disk write, so the
  ordering invariant holds; only the bypass-status differs).
- **`safe_write` never silently overwrites a file the kit has not
  previously journaled and that differs in content.** The adopt
  fast-path covers the byte-identical case; everything else routes
  through `.proposed`.
- **`safe_write_region` keeps no-prior-event-direct-write semantics.**
  The region is kit-owned by marker (ADR-0003); a user editing
  inside `<!-- BEGIN MANAGED: id -->` markers is already outside the
  managed-region contract, and the install pipeline's aggregator
  (`install.py::aggregate_region_contributions`) depends on the
  direct-write behavior to introduce new region buckets on
  `wiki add` without proposing every one.
- **The adopt fast-path is state-idempotent.** Calling `safe_write`
  twice on a byte-identical unjournaled file leaves identical
  observable state: file unchanged, baseline journaled, no
  `.proposed` sidecar. The journal accumulates one
  `PageWriteEvent` per call (the second call sees the journaled
  baseline, hashes match, takes the repeat-write path, and appends
  per existing
  `test_no_op_write_of_identical_content_still_records_event`).
  Idempotence is in the observable outcome, not the event count.
- **`_ensure_obsidianignore` remains the *only* documented
  non-journaled write into a vault.** This spec does not introduce
  new bypasses; it names this one explicitly.

## Contracts with other modules

- **`write_helper.py`** owns the ordering. Three public functions
  (`safe_write`, `safe_write_region`, `resolve_proposal`) emit events
  before touching disk. `_ensure_obsidianignore` remains a
  module-private non-journaled write; its leading docstring grows a
  one-line pointer to this spec and ADR-0004 §Negative.
  `_ensure_obsidianignore`'s signature stays `(vault_root: Path)
  -> None` — no caller change.
- **`journal.py`** is unchanged. The journal-locking spec already
  delivers `append_event` with `fsync` and `fcntl.flock`; this spec
  is a consumer, not a contributor.
- **`doctor.py`** is unchanged. The orphan check, page-drift check,
  managed-region-drift check, and missing check all keep their
  current shape and the derived kit-owned set (qC10 + C6 replaced
  the previous static `KIT_OWNED_FILES` / `KIT_OWNED_DIRS` tuples
  with a derivation from `state.page_writes` and managed-region
  writes) does not grow to include `.obsidianignore`. The new
  crash windows (§Edge cases "Crash between event append and disk
  write") are recoverable through existing checks; no new `Issue`
  kind.
- **`cli.py`** is unchanged. The CLI handlers (`_cmd_init`,
  `_cmd_add`, `_cmd_ingest`, `_cmd_resolve`, the future `_cmd_run`)
  continue to call `safe_write` / `safe_write_region` /
  `resolve_proposal` exactly as today. The qC6 fix is internal to
  `safe_write`; CLI callers benefit transparently.
- **`install.py::aggregate_region_contributions`** is unchanged. It
  depends on `safe_write_region`'s no-prior-event-direct-write
  semantics to introduce new region buckets on `wiki add`. The spec
  preserves that behavior; see §Non-goals.
- **`AGENTS.md`** (the kit's, at the repo root) — the
  "Don't bypass `write_helper.safe_write()`" anti-pattern under
  §"Things you should not do without asking" gains a *new*
  parenthetical carve-out (no prior carve-out exists today). The
  amended bullet reads: "Don't bypass `write_helper.safe_write()`
  for any file write that lands in a user's vault. Drift detection
  is load-bearing. (Documented exceptions: `resolve_proposal` for
  user-mediated merges; `_ensure_obsidianignore` for the additive
  Obsidian-index config — see
  `docs/specs/safe-write-ordering/spec.md`.)" Same PR.
- **`ADR-0002`** §Negative gets a §Revisions-style amendment: the
  "Mitigated: `safe_write` writes the file *after* validating the
  event" sentence is replaced by a pointer to this spec for the
  precise ordering contract. The §Decision pithy quote is unchanged
  (it always said event-before-disk; the code is catching up).
- **`ADR-0004`** §Mechanics step 2 is amended via a dated §Revisions
  entry: "If no prior `PageWriteEvent` *and* the file does not exist
  on disk, this is a first write — go to step 4. If no prior event,
  the file exists, and bytes match the kit's proposed content,
  adopt the file (append `PageWriteEvent`, skip the disk write) and
  return. If no prior event, the file exists, and bytes differ,
  treat as drift and go to step 5." §Mechanics steps 4 and 5 are
  reordered internally so the event-append happens before the disk
  write. §Negative grows two bullets: (i) the now-universal
  noisy-existing-folder consequence (no longer just `wiki init`'s
  problem), (ii) `.obsidianignore` as a documented non-journaled
  bypass. §Revisions gets a new dated entry naming this spec.

## Acceptance criteria

Contract tests below. Per-task construction tests (call-sequence
snapshots, fixture plumbing, edge-cases tied to one task's
implementation) live in [`plan.md`](plan.md) §Steps and are
revisable per CONVENTIONS §"Contract tests vs. construction tests".

### Event-before-disk ordering (qC3) — contract tests

The load-bearing contract is "if the disk write fails, the event
is still durable so doctor can reconcile." Tests split into two
sub-families with different roles before/after the reorder.

#### Behavioral ordering — RED today, GREEN after the reorder

These observe the qC3 contract at a black-box surface. The
failure-injection family pins "if the disk write fails, the event
is durable"; the snapshot family pins "even when the disk write
succeeds, the event is durable *before* the disk write happens"
— together they pin both halves of the ordering contract, so a
future refactor that flipped the order in the happy path (and
left the failure path intact) cannot pass both:

- [x] `test_safe_write_event_durable_when_disk_write_raises` —
      monkeypatch `Path.write_bytes` (path-scoped) to raise
      `OSError`; call `safe_write` for a fresh path; observe the
      exception; assert the journal contains the `PageWriteEvent`
      AND `not abs_path.exists()`. Today's code raises before
      appending, so the event assertion fails red. After the
      reorder, the event is durable and both assertions hold.
- [x] `test_safe_write_region_event_durable_when_disk_write_raises` —
      same shape; `ManagedRegionWriteEvent` durable; file content
      unchanged from pre-call.
- [x] `test_resolve_proposal_events_durable_when_disk_write_raises` —
      same shape; both `PageWriteEvent` and
      `PageConflictResolvedEvent` durable; file unchanged.
- [x] `test_safe_write_event_in_journal_at_moment_of_disk_write` —
      pin the happy-path ordering at a contract surface (the
      construction tests in `plan.md` also pin this but are
      revisable; this one is durable). Wrap `Path.write_bytes`
      with a path-scoped recorder that, on the call against
      `abs_path`, snapshots `read_events(journal_path)` and then
      delegates to the original implementation. Call `safe_write`
      on a fresh path; expect success; assert the snapshotted
      event list contains a `PageWriteEvent` for `relative_path`
      whose `hash` matches the content being written. Catches a
      future refactor that put `write → if write_succeeded:
      append_event` (the failure-injection family above would not
      catch this rewrite). Same shape covers `safe_write_region`
      and `resolve_proposal`:
- [x] `test_safe_write_region_event_in_journal_at_moment_of_disk_write`
- [x] `test_resolve_proposal_events_in_journal_at_moment_of_disk_write`

#### Recovery contract — GREEN today, pinned for the future

These pre-seed the journal manually and assert the doctor reports
the gap. They pass against today's code as well — their job is to
pin the §Edge cases recovery story so a future refactor can't
silently drop the doctor's reconciliation hook:

- [x] `test_doctor_surfaces_orphan_page_event_as_missing` —
      append a `PageWriteEvent` directly for a path that is then
      never written; run `run_doctor`; assert `Issue(MISSING,
      "<path>")` is in the result.
- [x] `test_doctor_surfaces_orphan_managed_region_event_as_drift` —
      append a `ManagedRegionWriteEvent` whose `content_hash`
      differs from the on-disk region body; assert
      `Issue(MANAGED_REGION_DRIFT, "<file>:<region>")` is reported
      (no trailing detail for hash-mismatch per `doctor.py`).
- [x] `test_doctor_surfaces_orphan_resolve_events` — same shape
      for the resolve path; assert `MISSING` or `PAGE_DRIFT` as
      appropriate.

#### Crash-recovery retry (new disjunct in the predicate)

The recovery story requires that a second `safe_write` against a
path whose `PageWriteEvent` is journaled but whose file is absent
takes the **direct-write** branch (not the proposal branch).
Without this, retry adds a `pending-proposal` issue on top of the
existing `missing` issue.

**Fixture isolation:** each sub-case test pre-seeds the journal
with `append_event` calls inline (not via a shared helper). Any
extracted helper *must* inline the explicit pre-state assertion
(file present/absent + journal length) before exercising the
predicate, so a regression in one sub-case cannot be masked by a
"fix" in the shared plumbing.

- [x] `test_safe_write_recovers_missing_file_when_baseline_journaled` —
      manually append a `PageWriteEvent` for a fresh path; call
      `safe_write` with the same content; assert `WriteResult.WRITTEN`,
      file is on disk with the journaled content, no `.proposed`
      sidecar is created, exactly one *new* `PageWriteEvent` is
      appended (two total: the seed and the recovery write).
- [x] `test_resolve_proposal_crash_recovery_produces_idempotent_state` —
      drive a `resolve_proposal` call that crashes after the events
      are durable but before `abs_path.write_bytes`; re-run
      `resolve_proposal` with the same content; assert
      `replay_state(read_events(journal))` produces a `VaultState`
      whose `page_writes[path]` and `pending_proposals` entries
      match the single-call outcome (last-write-wins idempotence;
      duplicate audit events in the journal are acceptable
      per §Edge cases).
- [x] `test_safe_write_region_crash_recovery_routes_to_proposal` —
      pre-seed a vault with a managed-region file; journal a
      `ManagedRegionWriteEvent` whose `content_hash` differs from
      the on-disk region body (simulating "event durable, write
      partial"); call `safe_write_region` with the same
      `new_content`; assert `WriteResult.PROPOSAL`, a
      `<file>.proposed` sidecar is created with the kit's intended
      rewrite, a `PageProposalEvent` is appended. Pins the
      "recovery is proposal flow, not direct retry" contract from
      §Edge cases sub-case 2.

### Unjournaled existing pages are drift, byte-identical is adopt (qC6)

- [x] `test_safe_write_to_unjournaled_existing_file_writes_proposal` —
      *inverts* today's
      `test_first_write_overwrites_existing_file_without_journal_entry`.
      Pre-existing file content survives; sidecar is created;
      `PageProposalEvent` is appended. Bytes differ.
- [x] `test_safe_write_to_unjournaled_existing_file_does_not_touch_original` —
      explicit assertion on the original bytes.
- [x] `test_safe_write_first_write_to_absent_file_still_writes_directly` —
      guard against over-broadening the change: no-journal-AND-no-disk
      stays on the direct-write path (this is what makes `wiki init`
      work).
- [x] `test_safe_write_adopt_fastpath_byte_identical_existing_file_writes_no_sidecar` —
      pre-existing file content is byte-identical to the kit's
      proposed content; `safe_write` returns `WriteResult.WRITTEN`,
      appends one `PageWriteEvent`, leaves the file's **inode**
      unchanged (load-bearing — Obsidian, file-watching editors,
      and `inotify` consumers care about inode preservation; mtime
      is a noisy proxy that breaks on coarse-mtime filesystems),
      and produces no `.proposed` sidecar. Assert
      `target.stat().st_ino == pre_ino` (captured before the call).
- [x] `test_safe_write_adopt_fastpath_baseline_becomes_journaled` —
      after the adopt fast-path, a subsequent `safe_write` with the
      same content sees the journaled baseline and takes the repeat-
      write path; with drifted content, takes the classic drift
      path.
- [x] `test_safe_write_adopt_fastpath_abandons_when_disk_changes_between_reads`
      — pre-seed an unjournaled file whose bytes match the kit's
      proposed content; install a path-scoped `Path.read_bytes`
      recorder that flips the on-disk content on its second
      invocation (the re-read inside `safe_write`'s adopt branch);
      call `safe_write`; assert `WriteResult.PROPOSAL`, a
      `.proposed` sidecar exists, the journal's last event is a
      `PageProposalEvent` (not `PageWriteEvent`). Pins the adopt
      fast-path's abandon branch — the only contract test that
      catches an implementer who drops the re-read.
- [x] `test_safe_write_adopt_fastpath_records_post_reread_timestamp`
      — pin spec §Behavior "Adopt fast-path" step 3's `now`
      recompute. Freeze `datetime.now` with a recorder that
      appends each call to a list and returns the next of
      `[T0, T1, T2, …]`; call `safe_write` along the adopt path;
      assert `len(now_calls) == 2` (call-entry + post-re-read
      recompute, no more) AND the journaled
      `PageWriteEvent.timestamp == T1`. The call-count assertion
      catches a future refactor that adds or drops a `now` call;
      the timestamp-equality assertion catches the original
      "implementer reused call-entry `now`" case.
- [x] `test_wiki_add_over_unjournaled_user_file_proposes_not_overwrites` —
      integration test driving the CLI in
      `tests/integration/test_wiki_add.py`: pre-seed a vault, drop
      a user-authored markdown file at a path a primitive will
      render to with differing content, run `wiki add`, assert
      `.proposed` sidecar lands and the user file is untouched.
- [x] `test_safe_write_region_unjournaled_region_byte_identical_still_writes_directly` —
      explicit guard distinct from the existing
      `test_safe_write_region_first_write_with_drifted_baseline_is_written`
      pin. The existing pin covers the differing-bytes
      unjournaled-region case (which a region-scoped qC6 inversion
      would break in `aggregate_region_contributions`); this new
      test covers the byte-identical case (where a future "let's
      mirror the page-level adopt fast-path into regions" refactor
      could regress). Seed a file with a managed region whose body
      already equals `new_content`; call `safe_write_region` with
      no prior event; assert `WriteResult.WRITTEN` and exactly one
      `ManagedRegionWriteEvent` appended. Pairs with §Non-goals
      "Why qC6 is page-scoped".

### `.obsidianignore` is a documented non-journaled bypass (C2)

- [x] `test_ensure_obsidianignore_does_not_journal` — first drift on
      a fresh vault produces zero `PageWriteEvent`s whose `path` is
      `".obsidianignore"`. Pins the spec's contract that this is the
      explicit choice, not an oversight.
- [x] `test_ensure_obsidianignore_remains_idempotent_via_pattern_check` —
      second and subsequent drift events do not rewrite
      `.obsidianignore` once the pattern is present.
- [x] `test_obsidianignore_bypass_doc_constant_points_at_this_spec` —
      `from llm_wiki_kit.write_helper import OBSIDIANIGNORE_BYPASS_DOC`;
      assert `OBSIDIANIGNORE_BYPASS_DOC == "docs/specs/safe-write-ordering/spec.md"`.
      The constant is the load-bearing pin; the docstring
      references it by name. A paraphrase of the docstring no
      longer silently shifts the contract — only changing the
      constant's value does, and that's grep-discoverable.
- [x] `test_ensure_obsidianignore_docstring_references_bypass_constant` —
      `"OBSIDIANIGNORE_BYPASS_DOC"` appears in
      `_ensure_obsidianignore.__doc__`. Cheap-and-brittle by
      design, paired with the constant test above so the two
      together catch both "constant renamed but docstring stale"
      and "docstring paraphrased away from the constant".
- [x] `test_doctor_does_not_flag_obsidianignore_as_orphan` —
      `.obsidianignore` exists in a vault with no journal entry for
      it; `run_doctor` does not produce `Issue(ORPHAN,
      ".obsidianignore")`. Post qC10 + C6, the orphan check derives
      its kit-owned set from journaled writes, so an unjournaled
      `.obsidianignore` is never a candidate; this test pins that
      absence so a future maintainer doesn't add a special-case
      claim back in.

### Pin removal and ADR amendment

- [x] **DELETE** `tests/unit/test_write_helper.py::test_first_write_overwrites_existing_file_without_journal_entry`.
      Its replacement above pins the inverted contract for the
      differing-bytes case; the adopt fast-path test covers the
      byte-identical case. The PR description names the removal
      explicitly so a reviewer doesn't mistake the deletion for
      a regression.
- [x] `tests/unit/test_write_helper.py::test_safe_write_region_first_write_with_drifted_baseline_is_written`
      **stays as-is** (pins the differing-bytes unjournaled-region
      case). The new
      `test_safe_write_region_unjournaled_region_byte_identical_still_writes_directly`
      pins the byte-identical complement. Together they pin the
      page-vs-region distinction per §Non-goals "Why qC6 is
      page-scoped".
- [x] `docs/adr/0004-drift-detection-and-proposal-flow.md` §Mechanics
      step 2 and steps 4/5 are amended; §Negative grows two
      bullets; §Revisions gets a dated entry naming this spec.
- [x] `docs/adr/0002-journal-as-state-truth.md` §Negative drops the
      "Mitigated: `safe_write` writes the file *after* validating
      the event" sentence and gains a pointer to this spec.
- [x] `AGENTS.md` (repo root) §"Things you should not do without
      asking" — the "Don't bypass `write_helper.safe_write()`"
      bullet gains a new parenthetical carve-out (none exists
      today) naming both `resolve_proposal` and
      `_ensure_obsidianignore` as the two documented exceptions,
      with a pointer to this spec.

### Knowledge capture

- [x] One entry appended to `docs/knowledge/patterns.jsonl` scoped
      to `llm_wiki_kit/write_helper.py`. Uses the canonical
      `{id, kind, scope, title, body, source, created, updated}`
      schema; cites K-0002 as the entry it extends (the existing
      K-0002 forbids the bypass categorically; the new entry adds
      the event-before-disk invariant and names the two documented
      exceptions). Plan §Steps step 5 spells out the exact JSON.
      Captures the load-bearing invariant a future maintainer
      would otherwise re-discover by reverse-engineering tests.

## Non-goals

- **Why qC6 is page-scoped (and `safe_write_region` keeps
  no-prior-event-direct-write).** The install pipeline's
  `aggregate_region_contributions` (see `llm_wiki_kit/install.py`)
  calls `safe_write_region` over `all_installed` on every `wiki
  init` and `wiki add`. For a primitive that introduces a *new*
  region bucket — a managed region none of the previously installed
  primitives contributed to — there is no
  `ManagedRegionWriteEvent` for `(file, region)`, but the file
  exists on disk (rendered earlier in the same install pass) with
  the region present as core's empty template body. Inverting the
  region-level no-prior-event predicate would propose a sidecar on
  every new region introduced by `wiki add` — the regression a
  region-scoped qC6 inversion would cause is much worse than the
  bug it would close. The region is kit-owned by marker (ADR-0003);
  a user writing inside `<!-- BEGIN MANAGED: id -->` markers is
  already outside the managed-region contract. The spec preserves
  the existing direct-write behavior here.
- **Why `.obsidianignore` is not journaled.** Three reasons,
  cumulatively: (1) every user edit to `.obsidianignore` (adding
  their own scratch-dir ignore) would register as `page-drift` in
  `wiki doctor` — wrong UX for a file users are expected to edit;
  (2) routing through `safe_write` proper would produce
  `.obsidianignore.proposed` on user drift, which Obsidian then
  indexes because `.obsidianignore` itself no longer carries the
  proposed-pattern (the bootstrap is self-defeating); (3) the
  alternative — special-casing `check_page_drift` to skip
  `.obsidianignore` — re-introduces the bypass the spec is supposed
  to remove. The documented non-journaled bypass is the smallest
  consistent answer.
- **Not a `wiki init --adopt` flag.** The page-level adopt fast-path
  covers the byte-identical case; the differing-bytes case routes
  through `.proposed`. A vault-wide `--adopt` (which would journal
  every pre-existing file as a `PageWriteEvent` to claim ownership
  without surfacing each as a proposal) remains RFC-0001's
  unresolved-question for a future task. This spec does not block
  it.
- **Not a rollback or two-phase commit.** Event-before-disk + the
  doctor checks named in §Edge cases is the recovery model. A real
  2PC would require fsyncing intent and rollback markers around
  every write pair; the kit's "single user, single machine, doctor
  reconciles" model rejects that complexity (per ADR-0002).
- **Not a `KitInfraWriteEvent` discriminator.** Reusing
  `PageWriteEvent` for `.obsidianignore` was the journaling option;
  this spec rejects journaling, so the discriminator question is
  moot. If `.obsidianignore`-like infra files proliferate and start
  needing audit, revisit with a new spec.
- **Not a rewrite of `_relative_to_vault`'s error surface.** qB3
  (wrap `ValueError` as `WikiError`) and qC9 (`.resolve()` both
  sides) shipped in their own retro-cleanup PR; this spec is a
  consumer of the updated helper, not a contributor. The §Edge
  cases "symlink that escapes the vault" bullet documents how the
  refusal surfaces through this spec's write paths.
- **Not `safe_write` for arbitrary file types.** Binary, large
  files, and non-utf-8 content are out of contract today and remain
  so. `content: str` is the type and `.encode("utf-8")` is the
  serialization, unchanged.
- **Not concurrent-writer semantics.** The journal-locking spec
  already covers concurrency around `append_event`; this spec's
  event-then-disk ordering inherits that contract unchanged.
- **Not a `_baseline_hash` performance optimization.** Both
  `_baseline_hash` and `_managed_region_baseline_hash` walk the
  full journal on every `safe_write` / `safe_write_region` call
  (today's behavior, unchanged by this spec). ADR-0002 §Negative
  names the checkpoint-event path as the future optimization once
  vaults grow past 10k events; for now, O(N) is the documented
  trade-off and this spec does not address it.
