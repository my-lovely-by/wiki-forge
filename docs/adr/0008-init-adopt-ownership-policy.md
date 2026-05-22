# ADR-0008: `wiki init --adopt` adopts pre-existing files via dedicated baseline events

- **Status:** Proposed
- **Date:** 2026-05-20
- **Deciders:** maintainer
- **Related:** RFC-0001 §"Unresolved questions" (`--adopt` deferral),
  RFC-0001 §"Phase B" Task 10 (`wiki init` end-to-end);
  ADR-0002 (journal as state truth);
  ADR-0003 (managed regions); ADR-0004 (drift detection +
  proposal flow); ADR-0006 (additive managed-region contributions);
  [`docs/specs/safe-write-ordering/spec.md`](../specs/safe-write-ordering/spec.md)
  (per-file adopt fast-path; "Not a `wiki init --adopt` flag" non-goal at
  line 782);
  [`docs/specs/wiki-init-adopt/spec.md`](../specs/wiki-init-adopt/spec.md)
  (the contract this ADR pins).

## Context

RFC-0001 §"Unresolved questions" deferred `wiki init --adopt` past v2.0
with three concrete sub-questions named in `cli.py:_cmd_init`'s
docstring: (i) which pre-existing files get journaled at adopt time,
(ii) how baseline hashes are seeded so the kit can later detect drift,
(iii) what happens to files the kit wouldn't otherwise own. The
`safe-write-ordering` spec's per-file adopt fast-path (`safe_write`,
[`docs/specs/safe-write-ordering/spec.md`](../specs/safe-write-ordering/spec.md)
§Behavior "Adopt fast-path") covers the *byte-identical* leaf of the
problem — when the kit's would-render content already matches what's
on disk, the file adopts silently and no `.proposed` sidecar is
produced. That non-goal at line 782 of the same spec explicitly
defers the *vault-wide* `--adopt` (differing-bytes pre-existing files,
mixed user/kit territory) to a follow-on, which this ADR resolves.

Three forces drive the design:

**The "noisy folder" failure mode (ADR-0004 §Negative bullets 3–4).**
A user converting an existing pile of markdown into a kit-managed
vault hits a wall today: `wiki init` refuses on non-empty targets, and
even if it didn't, every pre-existing file would route through
`safe_write`'s drift path and surface as a `.proposed` sidecar. For a
folder with N pre-existing files where M are byte-identical to the
kit's would-render and `N - M` differ, the user faces `N - M`
`wiki-conflict` invocations in a row. We need a flow that journals
baselines for ALL pre-existing kit-owned files without forcing each
into the proposal merge UX unnecessarily, while still preserving
ADR-0004's "no silent overwrites of user-edited files" invariant.

**ADR-0004 §Mechanics step 2's contract is page-scoped and assumes
the kit has either written the file or not seen it.** Adoption
introduces a third state: "kit didn't write the file, but it claims
the path as baseline." `safe_write`'s current predicate (`no_history
and file_present and bytes_differ` → proposal) cannot tell the
difference between "first kit write over a user file" (drift, write
proposal) and "kit reading its own adopted baseline" (no drift, may
write directly) — they look identical at the journal level.

**Managed-region host files (ADR-0003, ADR-0006) need both
page-level and region-level seeding.** A pre-existing
`frontmatter.schema.yaml` with the user's own region content gets
both a page-scope baseline (so `safe_write` doesn't propose it
wholesale on the next install) AND region-scope baselines per
`(file, region)` pair (so `safe_write_region`'s no-prior-event
direct-write path — ADR-0006 §Mechanics step 5; preserved by the
`safe-write-ordering` spec §Non-goals "Why qC6 is page-scoped" —
does not silently overwrite the user's region body on the
aggregator's first pass).

Implementation cannot proceed until the policy is pinned. The
sub-questions are not implementation details: they're contract-level
decisions that interact with ADR-0002 (journal as truth), ADR-0003
(managed regions), ADR-0004 (drift), and ADR-0006 (aggregation
order).

## Decision

> **`wiki init --recipe <name> --adopt <path>` adopts the target
> directory as a vault by journaling pre-existing kit-owned files as
> dedicated `PageAdoptedEvent`s (and, for managed-region host
> files, `ManagedRegionAdoptedEvent`s) *before* the normal install
> pipeline runs. Adopted baselines are sticky: the kit treats the
> next install-time write of differing content as drift and routes
> through the proposal flow, even though `on_disk_hash ==
> baseline_hash`. Files outside the recipe's rendered closure are
> left strictly alone (no event, no touch). A target that is already
> a vault — `<target>/.wiki.journal/journal.jsonl` exists — refuses;
> the recovery path is `wiki upgrade`.**

The decision pins six sub-choices.

### 1. CLI shape

`wiki init <path> --recipe <name> --adopt`. The `<path>` positional is
reused unchanged; `--adopt` is a boolean flag whose only effect is to
flip the empty-directory pre-check (`wiki init` refuses on non-empty
targets by default; `--adopt` allows them). The flag does not change
the `--recipe` semantics, the render pipeline, or any vault-side skill.

### 2. File classification at the target

Three buckets, exhaustive:

- **Kit-owned-by-recipe** — a path the recipe's primitives would
  render through `render_tree` (a file under any installed
  primitive's `files/` tree) OR a managed-region host file (a path
  named by any contributor's `regions/<file>.<region>` snippet, per
  ADR-0006 §Mechanics). For each such path that already exists at
  the target on disk, the adoption phase emits one
  `PageAdoptedEvent(timestamp, by="wiki-init-adopt", path, hash=<on-disk
  user bytes>)` followed (interleaved per host file) by one
  `ManagedRegionAdoptedEvent(timestamp, by="wiki-init-adopt", file,
  region, content_hash=<canonicalised region body>)` per region
  present in the file. **Refusal pre-flight**: if any pre-existing
  managed-region host file's markers do not parse via
  `managed_regions.parse`, OR if a region the aggregator will write
  to is absent from the pre-existing host file's markers, the run
  is refused with `WikiError` before any journal event lands.
  Graceful page-level fallback was considered and rejected:
  `safe_write_region` parses the host file's markers on every call
  (`write_helper.py:382-405`) and has no page-scope drift fallback
  — a malformed or marker-missing host would either raise
  `ManagedRegionError` mid-install or, worse, take the
  `baseline_hash is None` direct-write path and silently overwrite
  the user's region body. Failing pre-flight with a clear
  remediation message ("fix or remove the file before --adopt") is
  the only invariant-preserving answer.
- **Pending sidecars from a prior run** — `<path>.proposed` files at
  paths in the recipe's rendered closure. The adoption phase is one-
  shot (see §6), so these are unexpected. They are left in place
  but trigger a one-line stderr warning at the end of the run
  pointing the user at `wiki-conflict` to resolve them after
  adoption completes. Sidecars OUTSIDE the rendered closure (e.g.,
  user-territory `.proposed` files from another tool) are ignored
  entirely — no warning, no journal entry, no kit claim.
- **User territory** — every other file. Not touched. Not journaled.
  Surfaces as orphan-territory in `wiki doctor` only if it lies under
  a directory the recipe owns (the kit-owned-dir derivation in
  `doctor.check_orphans` is unchanged). This matches the kit's
  existing "any non-kit file under a kit-owned dir is an orphan"
  semantics; adoption neither widens nor narrows it.

### 3. Seed-baseline event shape

Two new event classes in `models.py`, slotted into the discriminated
`Event` union after their non-adopt counterparts:

- `PageAdoptedEvent` — same payload as `PageWriteEvent` (`path`,
  `hash`, `hash_algo`). Discriminator `type:
  Literal["page.adopted"]`. Counts as a baseline for `_baseline_hash`
  lookup (so the path is "journaled" for `doctor.check_orphans`
  purposes — same kit-owned-territory derivation).
- `ManagedRegionAdoptedEvent` — same payload as
  `ManagedRegionWriteEvent` (`file`, `region`, `content_hash`,
  `hash_algo`). Discriminator `type:
  Literal["managed_region.adopted"]`. Counts as a baseline for
  `_managed_region_baseline_hash` lookup.

`safe_write`'s predicate gains two disjuncts evaluated when the
latest baseline event for a path is `PageAdoptedEvent`. Dispatch
is via the helper `_latest_baseline_event_kind(journal_path,
relative_path) -> Literal["write","adopted","none"]`; the
predicate branches on the literal, not on `isinstance` — the
helper centralises the "latest baseline by event class" walk so
the predicate stays a short readable disjunction.

1. **Adopt-match no-rewrite** — `new_hash == adopted_hash ==
   on_disk_hash`. Append a `PageWriteEvent(hash=new_hash)`
   (supersedes the adopt baseline, clearing sticky-adopt for the
   path) and DO NOT touch the file. Preserves the file's inode and
   mtime (load-bearing for Obsidian / inotify consumers). Returns
   `WriteResult.WRITTEN`.
2. **Adopt-differ proposal** — `new_hash != adopted_hash` (and any
   `on_disk_hash`). Route to the proposal branch even when
   `on_disk_hash == baseline_hash`. The user's adopted bytes
   survive untouched; the kit's intended content lands as
   `<path>.proposed`. Emits `PageProposalEvent`, returns
   `WriteResult.PROPOSAL`.

`safe_write_region` gains the equivalent two disjuncts keyed on
`ManagedRegionAdoptedEvent`. The match-no-rewrite branch appends a
fresh `ManagedRegionWriteEvent` superseding the region adopt
baseline AND preserves the host file's bytes verbatim.

`resolve_proposal` is unchanged in *shape* but is extended to walk
both `ManagedRegionWriteEvent` and `ManagedRegionAdoptedEvent` in
its `_known_regions_for_file` helper — without this extension, a
managed-region host whose regions only have adopt events would
emit zero `ManagedRegionWriteEvent`s during resolve, leaving the
region-level sticky-adopt baselines uncleared and looping on every
subsequent aggregator pass. The `PageWriteEvent`
`resolve_proposal` emits supersedes any prior `PageAdoptedEvent`
(latest-wins in `_baseline_hash`); the
`ManagedRegionWriteEvent`s emitted in the region re-baseline loop
supersede region-level adopt baselines analogously. Sticky-adopt
clears on the first resolve; subsequent installs and upgrades
treat the path normally.

### 4. Considered alternative: `reason` field on `PageWriteEvent`

The future-note at `write_helper.py:155` proposed adding a `reason:
Literal["fresh","adopt","recovery"]` field to `PageWriteEvent` for
audit-trail clarity. Evaluated and rejected for this purpose: a
`reason` field is good for human-readable audit (`wiki journal tail`
shows `reason=adopt`) but bad for `safe_write`'s predicate
dispatch — branching on a payload enum value is structurally
equivalent to branching on a discriminator, with extra schema-
migration cost (existing journal lines need `reason="fresh"`
defaulting) and no upside. The discriminated-union shape (one class
per event type, per ADR-0005) is the kit's convention for this
exact situation. The audit win the future-note wanted is delivered
by `_format_event_block` reading the discriminator directly. If a
future spec wants to distinguish "fresh" from "recovery"
`PageWriteEvent`s for finer audit, it can introduce a third class
then.

### 5. Refusal cases

- **`<target>/.wiki.journal/journal.jsonl` exists AND contains at
  least one `PrimitiveInstallEvent`** — refuse with `WikiError(
  "target is already a wiki vault: ...; run `wiki upgrade` to
  refresh installed primitives or `wiki add` to install more.")`.
  Re-initializing would either clobber the existing journal or
  produce a journal with two `VaultInitEvent`s, breaking
  `replay_state`'s assumption that there is one. Exit 2.
- **`<target>/.wiki.journal/journal.jsonl` exists but contains
  zero `PrimitiveInstallEvent`s** — proceed as a re-run. This is
  the init-in-progress state left by a crash during the adoption
  phase (see §6). The adopt-phase event emission is idempotent on
  replay (latest-wins on hash mismatch; identical content
  re-emits the same event); the install pipeline then runs from
  the prefix the journal already carries.
- **Malformed managed-region host file** — refuse with
  `WikiError("cannot adopt managed-region host '<file>': markers
  do not parse (<reason>)")`. Exit 2. The user fixes the file or
  removes it before re-running.
- **Managed-region host file missing markers for a region the
  recipe needs** — refuse with `WikiError("cannot adopt managed-
  region host '<file>': missing markers for region '<region>'
  the recipe needs")`. Exit 2. The user adds the marker block
  before re-running.
- **Target is a file, not a directory** — refuse (already
  `_cmd_init`'s precondition; the check fires before any `--adopt`
  logic).
- **Target does not exist** — proceed; create it. `--adopt` over a
  missing target is degenerate but not an error; it collapses to a
  normal `wiki init` (no kit-owned-paths-to-adopt because there are
  no files), with the same outcome as omitting `--adopt`.
- **Target contains `.git`, `.hg`, or other VCS metadata** —
  proceed. The kit does not detect VCS, and ADR-0002 §Positive
  explicitly accommodates git-tracked vaults. A user initializing a
  vault inside an existing git repo is supported.
- **Target contains `<path>.proposed` files** — proceed, with the
  warning described in §2. The pre-existing sidecars are not the
  kit's responsibility to clean up; the user resolves them after.

### 6. Idempotency and re-run semantics

`wiki init --adopt` is **one-shot once the install pipeline has
started landing `PrimitiveInstallEvent`s**. The refusal predicate
keys on "journal contains a `PrimitiveInstallEvent`" rather than
"journal file exists" so the adoption phase itself is retryable:

- **Crash during the adoption phase** (after `VaultInitEvent`,
  before the first `PrimitiveInstallEvent`) — the journal records
  some prefix of the adopt events durably (per the safe-write-
  ordering spec's event-before-disk invariant). The recovery is
  `wiki init --adopt` again: the refusal predicate sees no
  `PrimitiveInstallEvent`, proceeds, `compute_adoption_set`
  re-walks the on-disk content and re-emits the adopt events
  (idempotent — same on-disk content produces the same hashes;
  latest-wins replay absorbs duplicates). The install pipeline
  then runs and lands every `PrimitiveInstallEvent` for the first
  time.
- **Crash inside the install pipeline** (after at least one
  `PrimitiveInstallEvent` landed) — the already-a-vault refusal
  fires; `wiki init --adopt` is no longer the recovery path.
  Recovery routes through `wiki upgrade` (which re-renders the
  primitive closure over the adopted baselines using the drift-
  aware safe-write helpers — byte-identical files take the
  no-rewrite branch, differing files surface as `.proposed`
  sidecars). The user resolves any sidecars via `wiki-conflict`.
- **Sidecars from the adopt phase pile up** — the user resolves
  them via `wiki-conflict` exactly like any other proposal
  sidecar. Resolution emits `PageWriteEvent`, which supersedes
  the adopt baseline (latest-wins in `_baseline_hash`).
  Subsequent upgrades treat the path normally.

A future "retry adopt" command is unnecessary because the
refusal predicate already permits re-running over an init-in-
progress journal; `wiki upgrade` covers post-install recovery.

## Consequences

### Positive

- **The "noisy folder" wall comes down.** A user with 100
  pre-existing markdown files, 80 of which the kit would render
  byte-identically, hits 0 sidecars for the 80 (handled by
  `safe_write`'s existing per-file adopt fast-path; the adopt-phase
  event matches `new_hash`) and 20 sidecars for the differing ones
  (matches the user's mental model — "the kit and I disagree on
  these 20 files; let me reconcile them").
- **`wiki doctor`'s orphan check Just Works.** Adopted paths
  contribute to the kit-owned-territory derivation
  (`check_orphans` walks `state.page_writes` *and* the new
  `state.adopted_pages`), so the post-adopt run is clean for kit-
  owned files. User-territory files outside the recipe's closure
  remain user-territory.
- **No silent overwrites.** ADR-0004's central invariant is
  preserved end-to-end. The kit writes a sidecar before touching
  any pre-existing user file whose content the kit disagrees with —
  same UX as the post-install drift path users already know.
- **Audit trail is explicit.** A future `wiki journal tail`
  invocation distinguishes `page.adopted` from `page.write` at the
  discriminator field, not through reasoning about the `by`
  attribution. The audit story is grep-able.
- **Managed-region adoption preserves the aggregator contract.**
  Pre-existing region bodies in a `frontmatter.schema.yaml` survive
  through the aggregator's first pass (region-scope baselines are
  in place); the kit's would-write content surfaces as a sidecar.
  ADR-0006's "additive contributions" semantics are unchanged.

### Negative

- **Two new event classes in `models.py`.** The discriminated
  `Event` union grows from 16 to 18 entries. Replay logic adds two
  new dispatches (`adopted_pages: dict[str, PageAdoptedEvent]`,
  `adopted_regions: dict[tuple[str, str],
  ManagedRegionAdoptedEvent]` in `VaultState`). Modest schema growth.
- **`safe_write`'s predicate grows two disjuncts** (adopt-match
  no-rewrite, adopt-differ proposal — see §Decision sub-choice 3).
  Dispatch is via `_latest_baseline_event_kind(journal_path,
  relative_path) -> Literal["write","adopted","none"]`; the
  predicate branches on the literal, not on `isinstance`. A future
  refactor of `safe_write` has two more branches to reason about;
  the contract tests in
  [`docs/specs/wiki-init-adopt/spec.md`](../specs/wiki-init-adopt/spec.md)
  §Acceptance criteria (AC13, AC14, AC15, AC16, AC16b) pin both
  branches so a regression is loud. `safe_write_region` grows the
  matching pair on the region side.
- **Pre-existing markers in shared region-host files require
  parsing AND introduce a new refusal class.** Adoption parses
  `frontmatter.schema.yaml` (and similar) with
  `managed_regions.parse` before the install pipeline runs;
  malformed markers or markers missing a region the recipe needs
  refuse the run with `WikiError` (per §Decision sub-choice 2 and
  §5). The codepath is one more place that touches the parser, AND
  it raises the user-facing error surface by two cases. Spec
  acceptance criteria AC9 and AC9b pin the refusal messages and
  the no-events-leaked invariant; the user gets an actionable
  remediation ("fix or remove the file before --adopt") rather
  than a confusing mid-install crash.
- **`wiki init --adopt` and `wiki init` are subtly different
  commands.** The `--adopt` flag is not just "skip the empty-dir
  check"; it triggers a structurally different first phase
  (adoption-walk + seed events). Users reading the help text need
  to understand both modes. Mitigation: the spec's CLI-help text
  and the `--adopt` docstring at `cli.py:_cmd_init` carry a
  one-paragraph explanation, and the spec's §Behavior happy-path
  diagrams the two phases.
- **The kit-owned-by-recipe set must be enumerable before the
  render phase.** This is structurally available (walk every
  primitive's `files/` tree; build the relative-path set) but
  requires one new helper. The spec names it
  `install.enumerate_rendered_paths(primitives, sources)` and
  pins its contract.
- **Adoption inherits the orphan check's existing kit-owned-dir
  semantics.** A user with `wiki/people/uncle-bob.md` (their own
  page) under a kit-owned `wiki/people/` directory still sees
  `uncle-bob.md` flagged as `orphan` post-adopt. This is unchanged
  from today's `wiki doctor` behavior; the ADR neither widens nor
  narrows the rule. Mitigation: documentation in the spec's
  §Non-goals.

### Neutral / monitor

- **If `--adopt` becomes the dominant first-time UX**, evaluate
  promoting it to the default and inverting the flag to
  `--no-adopt` (refuse-on-non-empty). Today's empty-dir refusal is
  conservative; once `--adopt` has shipped and users adopt it
  routinely, the empty-dir refusal becomes the surprising case.
  Re-evaluate after one release cycle of real use.
- **If the adopt-phase region parsing trips users frequently** (a
  pre-existing `frontmatter.schema.yaml` from another tool with
  marker-like comments that parse incorrectly), evaluate a
  `--no-adopt-regions` sub-flag that suppresses
  `ManagedRegionAdoptedEvent` emission while still emitting
  page-level adopts. The current decision keeps the flag set
  minimal; revisit on user signal.
- **If `wiki upgrade` becomes the recovery path for partial adopts
  more often than expected**, consider adding a `wiki init
  --retry-adopt` flag that revives a journal-with-adopt-events-but-
  no-renders into a useful state. Today's "doctor + upgrade"
  recovery is good enough for the rare crash window.

## Alternatives considered

### Alt 1: Reuse `PageWriteEvent` with a `reason: Literal[...]` field

The future-note shape at `write_helper.py:155`. Tempting because it
reuses an existing class and the `reason` field doubles as an audit
discriminator. Loses because branching `safe_write`'s predicate on a
payload field is structurally equivalent to branching on the event
class — same code, more schema-migration cost — and ADR-0005's "one
class per event type" convention is the kit's standard answer. See
§Decision sub-choice 4.

### Alt 2: Adopt by walking the target directory wholesale (no recipe filter)

A simpler-looking design: at adopt time, emit one
`PageAdoptedEvent` for every file under the target (regardless of
whether the recipe renders that path), claiming the entire folder as
kit territory. Loses on two counts:

- It silently expands the kit's claimed territory to files the kit
  cannot upgrade or maintain (e.g., a user's `notes/2023-archive/`
  subtree). `wiki doctor`'s orphan check would then surface no
  orphans, hiding the real "the user has files in this folder that
  the kit's recipe doesn't recognize" signal.
- It violates ADR-0002's "the journal records what the kit knows
  about" principle: claiming a baseline for a path the kit has no
  recipe-driven plan to ever rewrite is journal noise.

The selected design's "kit-owned-by-recipe only" rule keeps the
journal scoped to files the kit actually has a plan for.

### Alt 3: Emit `PageProposalEvent` for every pre-existing file at adopt time

A "be loud about every adoption" design: instead of journaling a
baseline and letting the render phase produce sidecars only for
differing-bytes files, journal a proposal for every pre-existing
file upfront. Loses because (a) the kit doesn't know the kit's
would-render content until the render phase runs, so the
proposal's `proposed_path` would be empty or speculative; (b) for
byte-identical files, this surfaces a sidecar where none is
needed — exactly the "noisy folder" problem we're trying to
solve.

### Alt 4: Refuse `--adopt` for managed-region host files; require them empty

A scoped-down variant: `--adopt` works for normal pages but
refuses if any managed-region host file (`frontmatter.schema.yaml`,
`.gitignore`, `research-providers.yaml`) already exists with
content. Loses because shared infra files are exactly the case
where pre-existence is most common — a user porting an existing
vault into the kit's model often has their own `.gitignore` and a
hand-rolled frontmatter schema. The kit either needs to handle
adoption of these files cleanly or refuse `--adopt` over any vault
that contains any of them, which severely narrows the feature's
usefulness. The region-level adoption path (§Decision sub-choice 2)
handles them.

### Alt 5: Make `--adopt` the default; flip `wiki init` to permissive

Skip the flag entirely; have `wiki init` adopt pre-existing files
automatically. Loses because it removes the user's explicit
consent: `wiki init` against a folder with content does something
non-obvious without asking. The flag-gated design forces a
deliberate choice. Re-evaluate per §Neutral if real-world use
shows the flag is always-on.

### Alt 6: Defer to `wiki upgrade` (no `wiki init --adopt` at all)

The current state-of-the-world: `wiki init` refuses, the user
either empties the directory or moves their content out, runs
`wiki init`, then moves their content back in and runs `wiki
doctor` to resolve the resulting orphans / drifts. Loses because
the workflow is gnarly enough to be a deterrent ("I'd have to move
my files out, re-init, then move them back in?"), and the
per-file post-init drift storm is the same noisy-folder failure
mode the per-file adopt fast-path tried to address. The deferral
was a v2.0 ship constraint, not a "this is the right answer"
decision.

## References

- ADR-0002 (journal as state truth) — the source-of-truth
  invariant the adopt phase inherits.
- ADR-0003 (managed regions) — the contract the
  `ManagedRegionAdoptedEvent` builds on.
- ADR-0004 (drift detection + proposal flow) — §Mechanics step 2
  and §Negative bullets 3–4 are the immediate forebears; this ADR
  resolves their `--adopt` carve-out.
- ADR-0006 (additive managed-region contributions) — §Mechanics
  step 5's "no-prior-event-direct-write" rule is the rule the
  region-level adoption baselines protect against.
- [`docs/specs/safe-write-ordering/spec.md`](../specs/safe-write-ordering/spec.md)
  — the per-file adopt fast-path at §Behavior "Adopt fast-path";
  §Non-goals "Not a `wiki init --adopt` flag" line 782 is the
  deferral this ADR closes.
- [`docs/specs/wiki-init-adopt/spec.md`](../specs/wiki-init-adopt/spec.md)
  — the implementation contract.
- RFC-0001 §"Unresolved questions" (lines 444–451) and Task 10
  (line 211) — the original deferral records.
- `llm_wiki_kit/cli.py:_cmd_init` docstring at lines 259–266 — the
  inline breadcrumb this ADR resolves.
- `llm_wiki_kit/write_helper.py:155` — the FUTURE note proposing
  the `reason` field, evaluated and rejected in §Decision
  sub-choice 4.
