# Spec: wiki-init-adopt

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit/cli.py:_cmd_init`,
  `llm_wiki_kit/install.py`, `llm_wiki_kit/write_helper.py`,
  `llm_wiki_kit/models.py`
- **Related:** [ADR-0008](../../adr/0008-init-adopt-ownership-policy.md)
  (the policy pin); ADR-0002 (journal as state truth); ADR-0003
  (managed regions); ADR-0004 (drift detection); ADR-0006 (additive
  managed-region contributions);
  [`docs/specs/safe-write-ordering/spec.md`](../safe-write-ordering/spec.md)
  (per-file adopt fast-path; "Not a `wiki init --adopt` flag"
  non-goal at line 782 — this spec lifts that deferral);
  [`docs/specs/wiki-init-adopt/plan.md`](plan.md);
  RFC-0001 §"Unresolved questions" (lines 444–451);
  ROADMAP.md "Deferred from v2.0".
- **Constrained by:** ADR-0008 §Decision (the six sub-choices); ADR-0004
  ("No silent overwrites of user-edited files"); RFC-0001 §"Runtime
  constraints" (no new runtime deps without a new ADR); AGENTS.md
  §"Check before acting" (every vault write through `safe_write` /
  `safe_write_region`; tests use `tmp_path` or fixture vaults).

## What this is

`wiki init <path> --recipe <name> --adopt` adds a single behavior to
`wiki init`: it allows the target directory to be non-empty, and
journals each pre-existing file the recipe would render as a kit
baseline before the normal install pipeline runs. The seed baselines
are sticky — a kit-owned file that the kit would render with content
differing from the user's pre-existing bytes lands as a `.proposed`
sidecar in the render phase, not a silent overwrite. Files outside
the recipe's rendered closure are not touched and not journaled. A
target that is already a vault refuses (the recovery path is `wiki
upgrade`). The flag is a one-shot first-time setup: once any
`PrimitiveInstallEvent` lands in the journal, re-running hits the
already-a-vault refusal; an init-in-progress journal (no
`PrimitiveInstallEvent` yet — left by a crash during the adoption
phase) is re-runnable for crash recovery — see ADR-0008 §6
Idempotency and §Edge cases "Crash during the adoption phase".

`wiki init --adopt` is the v2.0-deferred companion to
`safe_write`'s per-file adopt fast-path
([`docs/specs/safe-write-ordering/spec.md`](../safe-write-ordering/spec.md)
§Behavior "Adopt fast-path"). The per-file fast-path handles the
byte-identical leaf; this spec handles the vault-wide flow including
differing-bytes pre-existing files and managed-region host files.

## Inputs

CLI invocation: `wiki init <path> --recipe <name> --adopt`.

- `<path>` — required positional. The target directory. May exist
  with content (the `--adopt` flag's only effect on pre-conditions).
- `--recipe <name>` — required, unchanged from `wiki init`.
- `--adopt` — required boolean flag for this surface. Without it,
  `wiki init` keeps its existing refuse-on-non-empty behavior.
- Kit catalog: resolved via `_kit_paths(args.kit_root)` per the
  existing CLI wiring; `core/` plus every primitive under
  `templates/<kind>/<name>/`. Same lookup `wiki init` uses today.

Pre-conditions checked at the CLI boundary, in this order:

1. `<path>` is not a regular file (existing `_cmd_init` check —
   raises `WikiError`).
2. `<path>/.wiki.journal/journal.jsonl` either does not exist OR
   exists but contains zero `PrimitiveInstallEvent`s (new check
   per ADR-0008 §Decision sub-choice 5 + §6 Idempotency — refuse
   with `WikiError` pointing the user at `wiki upgrade` when the
   journal already carries a `PrimitiveInstallEvent`; allow re-run
   when the journal is "init-in-progress" with only
   `VaultInitEvent` and/or adoption events present, to recover
   from a mid-adoption crash).

If both pass and `<path>` exists with content, the adoption phase
kicks in; if `<path>` is empty (or missing), `--adopt` collapses to a
normal `wiki init` (zero pre-existing files to adopt; no adoption
events emitted).

## Outputs

### Journal events

Ordered, all appended *before* the corresponding disk reads/writes
land (event-before-disk per the safe-write-ordering spec):

1. **`VaultInitEvent`** — one, attributed `by="wiki-init"` (NOT
   `"wiki-init-adopt"`; the vault is the same vault regardless of
   the install vehicle, and `replay_state` keys on the first init
   event's `recipe`/`vault_name` — preserving the existing `by` value
   keeps the journal-tail vehicle filter for "what created this
   vault" consistent across `init` and `init --adopt`).
2. **`PageAdoptedEvent` + `ManagedRegionAdoptedEvent` interleaved per
   host file** — for each pre-existing kit-owned-by-recipe file on
   disk (in `sorted(adoptable_paths)` order, stable across runs),
   the journal receives one `PageAdoptedEvent(by="wiki-init-adopt",
   path, hash=<sha256 of file's current bytes — NOT the kit's
   would-render content>)` followed immediately by any
   `ManagedRegionAdoptedEvent(by="wiki-init-adopt", file=<path>,
   region=<id>, content_hash=<sha256 of
   managed_regions.canonical_region_body(<body on disk>)>)` rows
   the host file produces (one per managed region present on disk,
   in `sorted(region)` order within the file). The interleave —
   page event followed by all its region events before moving to
   the next host file — ensures that a crash mid-adoption leaves a
   consistent prefix: every `PageAdoptedEvent` in the journal has
   the region events it implies (or none, for non-host files). The
   `content_hash` uses the same canonicalisation
   `safe_write_region` uses for its baseline hash, so the
   region-baseline lookup matches without spurious drift on the
   first aggregator call. **Pre-flight refusal** — if any pre-
   existing kit-owned host file's markers do NOT parse cleanly via
   `managed_regions.parse`, OR if any region the aggregator will
   write to is absent from the pre-existing host file's markers,
   the adopt is refused before any event is appended. See §Error
   cases.
3. **The normal install-pipeline events** — `PrimitiveInstallEvent`
   per primitive, `PageWriteEvent` / `PageProposalEvent` per
   rendered file, `ManagedRegionWriteEvent` /
   `PageProposalEvent` per aggregated region. Same shape and
   attribution as today's `wiki init` (`by` is the primitive name
   for page-level writes, `by="wiki-init"` for the aggregator pass
   — NOT `"wiki-init-adopt"`; the install vehicle for the aggregator
   matches the `VaultInitEvent`'s `by`, so `_cmd_init` continues to
   thread `INSTALL_VEHICLE_INIT` into `install_primitives` exactly
   as it does today).

No new event type is required *beyond* the two new classes named in
ADR-0008 §Decision sub-choice 3 (`PageAdoptedEvent`,
`ManagedRegionAdoptedEvent`). All other events are existing classes.

### Stdout

- **One line per `PageProposalEvent` produced during the run**, in
  journal order, with the existing capital-W shape (`Wrote <path>
  (drift detected on <original>); run the wiki-conflict skill to
  merge.`). Sidecars come from BOTH the per-primitive render
  (`safe_write` drift) AND the aggregator (`safe_write_region`
  drift) — the line-collection walk is journal-aware, not
  renderer-aware.
- **A final summary line** when adoption happened (the target was
  non-empty at start AND at least one kit-owned file was adopted):
  - `wiki init: adopted N file.` for `N == 1`.
  - `wiki init: adopted N files.` for `N != 1`.
- When the target was empty (or did not exist), no adopt-summary
  line is printed; the run is indistinguishable from a normal
  `wiki init` on stdout.

### Stderr

- One-line `WikiError` messages for the refusal cases (§Error cases).
- One informational line per kit-owned `.proposed` sidecar
  surfaced from the adoption walk (kit-owned = path's
  non-`.proposed` form is in
  `enumerate_rendered_paths(...)`): `wiki init: found N
  pre-existing kit-owned .proposed sidecar.` (`sidecars.` for
  `N != 1`) — one line total, count-aware. The line is on
  stderr only (not stdout) to match the "warning" framing and
  keep stdout parse-clean for tools counting only the adopt
  summary. Sidecars outside the rendered closure are ignored.

### Disk

- `.wiki.journal/journal.jsonl` created and populated with the
  ordered event sequence above.
- `.obsidianignore` written (or extended) by
  `_ensure_obsidianignore` if any `.proposed` sidecar lands during
  the run (existing helper; non-journaled bypass per the
  safe-write-ordering spec).
- `<path>.proposed` sidecars for each render-time drift, side-by-
  side with the user's untouched original. No pre-existing user
  file at a kit-owned path is rewritten by this run; the adoption
  baselines protect them.
- The render pipeline's normal output for paths the recipe owns
  whose target does NOT pre-exist (e.g., new `.gitkeep`s under a
  freshly-created ontology directory).

## Behavior

### Happy path — empty target

1. `<path>` does not exist or exists empty.
2. Pre-condition checks pass (not a file; no journal).
3. `args.adopt is True`; the empty-dir refusal in the current
   `_cmd_init` is skipped.
4. `target.mkdir(parents=True, exist_ok=True)`.
5. The journal-cache scope opens. `VaultInitEvent` is appended
   (`by="wiki-init"`).
6. **Adoption phase is a no-op** because there are no pre-existing
   files: `enumerate_rendered_paths(...)` returns its set; the
   intersection with on-disk files is empty; zero adopt events
   appended.
7. Normal install pipeline runs (`install_primitives` over the full
   ordered primitive closure). Existing render + aggregator passes
   produce `PageWriteEvent`s and `ManagedRegionWriteEvent`s exactly
   as today.
8. No adopt-summary line printed. Return 0.

### Happy path — non-empty target with mixed kit-owned content

1. Pre-condition checks pass; `args.adopt is True`.
2. `target.mkdir(parents=True, exist_ok=True)` (no-op since it
   exists).
3. Build the recipe + primitive closure (`load_recipe`,
   `discover_primitives`, `resolve_recipe_primitives`). Pre-flight
   `validate_contributions(primitive, sources[primitive.name])` per
   the existing `_cmd_init` pre-flight.
4. **Compute the adoption set.**
   `adopt.compute_adoption_set(target, ordered, sources)` returns
   an `AdoptionSet(host_adoptions:
   list[HostAdoption], pre_existing_sidecars: list[str])`:
   - `host_adoptions`: one entry per pre-existing kit-owned
     file, in `sorted(path)` order. Each `HostAdoption` carries
     `path`, `hash` (the file's current bytes), and `regions:
     list[AdoptedRegion]` — empty for non-host files, populated
     in `sorted(region)` order for managed-region host files.
     Pre-flight refusals (markers don't parse; required region
     absent) raise `WikiError` here BEFORE any journal append.
   - `pre_existing_sidecars`: vault-relative POSIX paths ending
     in `.proposed` that exist in the target AND whose
     non-`.proposed` form is in
     `enumerate_rendered_paths(ordered, sources)`. Sorted; not
     journaled. Sidecars at user-territory paths are ignored.
5. The journal-cache scope opens.
   1. `append_event(journal_path, VaultInitEvent(by="wiki-init", ...))`.
   2. For each `HostAdoption` in `adopt_set.host_adoptions` (in
      sorted order): append one
      `PageAdoptedEvent(timestamp=now, by="wiki-init-adopt",
      path=host.path, hash=host.hash)`, IMMEDIATELY followed by
      one `append_event` call per region in `host.regions`:
      `ManagedRegionAdoptedEvent(timestamp=now,
      by="wiki-init-adopt", file=host.path, region=region.region,
      content_hash=region.content_hash)`. The interleave
      (page → its regions → next host's page → its regions)
      keeps any crash prefix consistent.
   3. `install_primitives(to_install=ordered, all_installed=ordered,
      ..., install_vehicle="wiki-init", now=now)` — identical
      invocation to today's `_cmd_init`. The render pipeline's
      `safe_write` / `safe_write_region` calls now see baselines
      from step 2; ADR-0008 §Decision sub-choice 3's adopt-aware
      predicate routes byte-differing kit content to the proposal
      branch, byte-identical kit content to direct-write.
6. Collect `PageProposalEvent`s from the new-events slice
   (`read_events(journal_path)[length_before:]`) and print one
   drift line per event in journal order.
7. If `adopt_set.host_adoptions` is non-empty, print the adopt-
   summary line on stdout per §Outputs Stdout.
8. If `adopt_set.pre_existing_sidecars` is non-empty, print the
   kit-owned-sidecar warning on stderr per §Outputs Stderr.
9. Return 0.

### Adoption-only happy path — kit content matches user content

When every adopted page's on-disk bytes are byte-identical to the
kit's would-render content (the "user already ran the kit's render
manually and we're claiming the result" scenario), the run
produces:

- One `PageAdoptedEvent` per pre-existing kit-owned file (step 5.2
  above).
- One `PageWriteEvent` per rendered file via `safe_write`'s
  matched-content direct-write path (which, with a
  `PageAdoptedEvent` baseline and equal hashes, takes the no-op
  rewrite branch per ADR-0008 §Decision sub-choice 3 — kit content
  matches adopted content → direct-write, no proposal).
- One `ManagedRegionWriteEvent` per aggregator-emitted region (the
  aggregator's existing no-drift event-append still fires; this is
  ADR-0006's "single write per region" rule, unchanged here).
- Zero `PageProposalEvent`s. Zero sidecars. Adoption is "silent" in
  the sense that no merge is required.

### Adoption-only happy path — kit content differs from user content

The differing-bytes case the ADR's §Positive describes:

- One `PageAdoptedEvent` per pre-existing kit-owned file.
- One `PageProposalEvent` per file the kit's render would change
  (kit content differs from adopted baseline). The `.proposed`
  sidecar contains the kit's would-render bytes; the original
  file is byte-identical to its pre-call content.
- For managed-region host files where the user's pre-existing
  region body differs from the kit's aggregated body: one
  aggregator-phase `PageProposalEvent` (the host file's
  `.proposed` contains the kit's rewritten file with the new
  region body). The original host file is untouched.
- Drift lines on stdout, one per proposal.
- The user runs `wiki-conflict` for each sidecar to merge.

### Edge cases

- **Target does not exist** — `target.mkdir(parents=True,
  exist_ok=True)` creates it; adoption set is empty; the run
  collapses to a normal `wiki init`. No adopt-summary line.
- **Target is empty** — same as the existing `wiki init` flow;
  adopt-summary line not printed.
- **Target contains only `.wiki.journal/`** — that directory by
  itself is not a vault (no `journal.jsonl`). Proceed. If the
  directory is non-empty otherwise, treat the contents normally.
- **Target contains `.git/` or other VCS metadata** — proceed. The
  VCS directory's contents are user-territory (no recipe renders
  inside `.git/`); not adopted, not journaled. *See also:
  `docs/specs/wiki-init-git/spec.md` §"Variant — target already
  contains `.git/`" for how `wiki init`'s git-init phase responds
  to a pre-existing `.git/` when both flags compose.*
- **Target contains `.proposed` sidecars from a prior run** — the
  sidecars are recorded in `adopt_set.pre_existing_sidecars` and
  surfaced via the informational stderr-only warning line (per
  §Outputs Stderr), but no adoption events are emitted for them.
  They are explicitly NOT adopted (a `.proposed` is by definition
  unresolved kit-vs-user state; rolling it into the adoption
  baseline would erase the conflict signal). The user resolves
  them via `wiki-conflict` after the run.
- **A kit-owned file exists on disk as a symlink whose target
  resolves outside the vault** — `_relative_to_vault`'s symlink-
  escape refusal fires when `safe_write` later runs against the
  same path. The adoption phase pre-empts the same refusal:
  `compute_adoption_set` calls `_relative_to_vault` per candidate
  path; symlink-escapers raise `WikiError` at adoption time
  rather than half-journaling baseline events. Tests pin both
  surfaces.
- **A managed-region host file exists but its markers are
  malformed or are missing a region the aggregator will write
  to** — refuse the run with `WikiError`. `safe_write_region`
  has no graceful page-scope fallback (its body reads + parses
  the host file at every call, so a missing or malformed
  marker raises `ManagedRegionError` from inside the install
  pipeline — too late to recover cleanly). Pre-flighting the
  parse during `compute_adoption_set` lets the kit refuse with
  a clear message before any journal event lands. See §Error
  cases for the refusal text.
- **A managed-region host file is parseable on disk but the
  adoption-walk hash of one region differs from what
  `managed_regions.parse(<rewritten body>)` produces on the
  aggregator's next call** — classic region-scope drift,
  handled by `safe_write_region`'s adopt-aware predicate (see
  §Contracts → `safe_write_region`): the host file gets a
  `.proposed` sidecar with the rewritten body, the original
  file is untouched, and a `PageProposalEvent` is journaled.
  The user merges via `wiki-conflict`.
- **TOCTOU between adoption walk and render-phase writes** —
  the adoption phase hashes each kit-owned file at time T1
  inside `compute_adoption_set`; the render phase re-reads the
  bytes at time T2 inside `safe_write`. A user edit (or
  filesystem-sync event) between T1 and T2 leaves the on-disk
  bytes diverged from the journaled adopted hash. Two
  sub-cases:
  - *Bytes still match the kit's would-render content.* The
    adopt-aware predicate finds `new_hash == adopted_hash !=
    on_disk_hash` (kit content == journaled baseline, on-disk
    has drifted from baseline) — classic drift path: a
    `.proposed` sidecar lands, original survives. The user's
    edit is preserved.
  - *Bytes match neither.* Same drift path; same sidecar.
  Net: a TOCTOU window does not silently lose user edits, but
  the journaled adopt baseline records a hash that never
  matches what's on disk (the snapshot at T1). `wiki doctor`
  will report `page-drift` on the next pass; the user resolves
  via `wiki-conflict`, which emits a fresh `PageWriteEvent`
  (new baseline). The window is the time between
  `compute_adoption_set`'s file-read and `safe_write`'s file-
  read in the same `_cmd_init` invocation — small but real.
  Pinned by the spec's §Edge cases and not eliminated.
- **The adoption set is enormous** (e.g., the user dropped 10k
  pre-existing files under a kit-owned subtree). Adoption walks
  every kit-owned path and hashes each pre-existing file once.
  Sha256 over a typical markdown file is sub-millisecond per the
  ADR-0004 §Negative discussion; 10k files is ~10s of hashing
  plus 10k journal appends. Acceptable; documented in the spec's
  §Constraints "No performance optimisation for large adoption
  sets in this spec."
- **The kit catalog cannot render a primitive named by the
  recipe** — pre-flight `validate_contributions` raises before
  any adoption event is appended; the journal stays empty.
  Recovery: fix the kit install. Matches today's `_cmd_init`
  behavior.
- **Crash during the adoption phase (after `VaultInitEvent`,
  before the first `PrimitiveInstallEvent`)** — the journal
  contains `VaultInitEvent` and some prefix of the adoption
  events. The already-a-vault refusal predicate is "journal
  contains a `PrimitiveInstallEvent`" (NOT "journal file exists";
  see ADR-0008 §6 Idempotency and re-run semantics). A journal in this
  intermediate state is "init-in-progress"; re-running `wiki init
  --adopt` is the recovery: the second run sees no
  `PrimitiveInstallEvent` in the journal, proceeds, and the
  adopt-phase `compute_adoption_set` re-emits the adopt events
  (idempotent: the latest-wins replay overwrites the partial
  prefix). The install pipeline then runs and lands every
  `PrimitiveInstallEvent`. Subsequent retries hit the
  already-a-vault refusal once the journal carries any
  `PrimitiveInstallEvent`. **Side effect:** the journal file grows
  with duplicate `PageAdoptedEvent` / `ManagedRegionAdoptedEvent`
  entries (the partial-prefix events stay; new ones are appended).
  `replay_state` collapses them via latest-wins; the duplicates
  are benign for state but visible in `wiki journal tail`.
  Compacting is out of scope for this spec (no ADR-0002 §Negative
  checkpoint events yet).
- **Crash between the last adoption event and the first
  `PrimitiveInstallEvent`** — same recovery as above. The next
  `wiki init --adopt` re-emits adopt events (idempotent over the
  on-disk content) and proceeds into the install pipeline.
- **Crash inside the install pipeline (after a
  `PrimitiveInstallEvent` landed)** — the already-a-vault refusal
  fires; `wiki init --adopt` is no longer the recovery path.
  Recovery routes through `wiki upgrade` (which re-renders the
  primitive closure over the adopted baselines using the
  drift-aware safe-write helpers — byte-identical files pass
  through, differing files surface as `.proposed` sidecars). The
  user resolves sidecars via `wiki-conflict`.

### Error cases

- `target path is a file, not a directory: <target>` — existing
  `_cmd_init` error, exit 2. Adopt-aware: the check fires before
  any `--adopt` logic.
- `target is already a wiki vault: <target>; run \`wiki upgrade\`
  to refresh installed primitives or \`wiki add\` to install
  more.` — new error, exit 2. Fires when
  `<target>/.wiki.journal/journal.jsonl` exists AND contains at
  least one `PrimitiveInstallEvent`. A journal with only
  `VaultInitEvent` and/or adoption events is treated as
  init-in-progress and the re-run proceeds (§Edge cases "Crash
  during the adoption phase").
- `cannot adopt managed-region host '<file>': markers do not parse
  (<reason>)` — new error, exit 2. Fires from
  `compute_adoption_set` when a pre-existing host file's
  `managed_regions.parse` raises. The user fixes or removes the
  file before re-running.
- `cannot adopt managed-region host '<file>': missing markers for
  region '<region>' the recipe needs` — new error, exit 2. Fires
  from `compute_adoption_set` when a parseable host file lacks
  markers for a region the aggregator pass will write to (the
  union of every `contributes_to` declaration targeting `<file>`
  across the recipe's primitive closure). The user adds the
  marker block (`<!-- BEGIN MANAGED: <region> -->` /
  `<!-- END MANAGED: <region> -->`) before re-running.
- `target directory is not empty: <target>\nwiki init refuses to
  render over existing files. Choose an empty directory or
  remove its contents first.` — existing error, exit 2. Fires
  only when `--adopt` is NOT set (the empty-dir refusal). With
  `--adopt`, this branch is suppressed.
- `path '<path>' resolves to '<resolved>', which is not inside
  the vault rooted at '<vault>'` — from `_relative_to_vault`
  during the adoption walk (symlink escape). Exit 2.
- `PrimitiveError` from `validate_contributions` — propagates
  as `WikiError`. Exit 2. The journal is empty (pre-flight is
  before any append).

## Invariants

1. **Event-before-disk holds end-to-end.** Every adoption event
   (`PageAdoptedEvent`, `ManagedRegionAdoptedEvent`) is appended
   to the journal and fsync'd before the install pipeline runs.
   No filesystem write happens between `VaultInitEvent` and the
   install pipeline's first `safe_write` call; the adoption phase
   only READS pre-existing files (to hash them) and APPENDS to
   the journal. The render-phase events keep the event-before-
   disk invariant via `safe_write` / `safe_write_region` itself.
2. **No silent overwrites of user-edited files.** ADR-0004's
   central invariant is preserved. The adoption baseline tells
   `safe_write` / `safe_write_region` "the user's bytes are the
   baseline; the kit has not consented to overwrite"; any kit
   write of differing content routes through the proposal
   branch.
3. **The adoption set is a strict subset of the recipe's
   rendered closure.** `compute_adoption_set` does not journal
   files outside `enumerate_rendered_paths(...)`; user-territory
   files remain user-territory.
4. **Adopt baselines are sticky until a `PageWriteEvent`
   supersedes them.** A `PageAdoptedEvent` is the latest baseline
   for a path until the next `PageWriteEvent` for the same path
   lands (from the render phase's adopt-match no-rewrite branch,
   the render phase's adopt-differ proposal followed by
   `resolve_proposal`, or any later `wiki upgrade` / `wiki
   add`). The adopt-aware predicate in `safe_write` dispatches
   on `_latest_baseline_event_kind(journal_path, relative_path)
   -> Literal["write","adopted","none"]`; the helper's literal
   maps 1:1 to the latest event's discriminator. A
   `PageProposalEvent` does NOT supersede an adopt baseline —
   proposals don't establish a kit baseline; only writes do.
5. **Region-level adoption preserves the aggregator contract
   (ADR-0006).** A `ManagedRegionAdoptedEvent` counts as a
   baseline for `_managed_region_baseline_hash`. The aggregator's
   "no-prior-event-direct-write" path is unchanged for regions
   the adoption phase did NOT seed (e.g., a region introduced by
   a primitive whose host file did not pre-exist on disk).
6. **The install vehicle for adopt-phase events is
   `"wiki-init-adopt"`.** `VaultInitEvent`,
   `PrimitiveInstallEvent`, and the aggregator's
   `ManagedRegionWriteEvent`s continue to use `"wiki-init"`
   (same as today's vanilla `wiki init`); the install pipeline's
   per-primitive `PageWriteEvent`s use the primitive name. Only
   the two adoption-phase event types use the new vehicle string,
   so a journal grep `by=wiki-init-adopt` returns the adoption
   slice exactly.
7. **One-shot semantics once installed.** Once the journal contains
   a `PrimitiveInstallEvent`, `wiki init --adopt` refuses with the
   already-a-vault error. Init-in-progress journals (no
   `PrimitiveInstallEvent` yet — left by a crash during the
   adoption phase) are re-runnable for crash recovery per §Edge
   cases "Crash during the adoption phase"; the second run re-emits
   the adopt events idempotently and proceeds into the install
   pipeline. `wiki upgrade` is the productive recovery path once
   installation has begun.

## Contracts with other modules

- **`llm_wiki_kit.cli`** — `_cmd_init` grows the `--adopt` flag
  (boolean), the already-a-vault refusal check, and the
  adoption-phase invocation. The non-`--adopt` flow keeps its
  current empty-dir refusal. The flag is parsed in
  `build_parser` as a `store_true` boolean. The summary-line
  emission, drift-line collection (from
  `read_events(journal_path)[length_before:]` filtered for
  `PageProposalEvent`), and pre-existing-sidecar warning all
  live in `_cmd_init`. No new top-level CLI dispatch.
  **`_EVENT_SUMMARY_FIELDS` at `cli.py:1328` gains two rows**:
  one for `PageAdoptedEvent` (mirroring `PageWriteEvent`'s
  `("path", "path", False)` entry) and one for
  `ManagedRegionAdoptedEvent` (mirroring
  `ManagedRegionWriteEvent`'s `("file", "file", False)`,
  `("region", "region", False)` entries). Without this, the
  dict's documented "missing-row raises `KeyError`" invariant
  (`cli.py:1409`) crashes `wiki journal tail` / `grep` /
  `explain` on the first adopt event — see AC21b.
- **`llm_wiki_kit.adopt`** — new module. Public surface:
  - `@dataclass(frozen=True) class AdoptedRegion(region: str,
    content_hash: str)`.
  - `@dataclass(frozen=True) class HostAdoption(path: str,
    hash: str, regions: tuple[AdoptedRegion, ...])`. `regions`
    is an empty tuple for non-host files; sorted by `region` for
    host files.
  - `@dataclass(frozen=True) class AdoptionSet(host_adoptions:
    tuple[HostAdoption, ...], pre_existing_sidecars:
    tuple[str, ...])`.
  - `compute_adoption_set(vault_root: Path, primitives:
    Sequence[Primitive], sources: Mapping[str, Path]) ->
    AdoptionSet` — pure (modulo filesystem reads). Walks every
    primitive's `files/` tree to compute the kit-owned-by-recipe
    path set, intersects with on-disk paths, hashes each adopted
    file, parses managed-region host files and refuses with
    `WikiError` on (a) malformed markers or (b) absent markers
    for a region the recipe needs. Also raises `WikiError` on
    symlink-escape. Does NOT append journal events; the caller
    (`_cmd_init`) appends them in the cache scope.
    Pure-of-state-changes for the unit tests.
  - `INSTALL_VEHICLE_ADOPT = "wiki-init-adopt"` — single
    source of truth for the adopt-phase install-vehicle string.
    Lives in `adopt.py` (co-located with the module that defines
    the adopt operation's identity, mirroring `upgrade.py`'s
    `UPGRADE_VEHICLE`). `cli._cmd_init` imports it for the
    `append_event(... by=adopt.INSTALL_VEHICLE_ADOPT ...)`
    calls. Adopting the `cli.INSTALL_VEHICLE_*` placement
    convention is rejected because the value is conceptually
    owned by the adopt module, and a future spec might surface
    the constant from elsewhere (e.g., `wiki doctor` filtering
    by vehicle); centralising it in `adopt.py` keeps the
    grep-discoverable surface intact.
- **`llm_wiki_kit.install`** — `install_primitives` is reused
  unchanged. A new helper
  `enumerate_rendered_paths(primitives: Sequence[Primitive],
  sources: Mapping[str, Path]) -> set[str]` walks each
  primitive's `files/` tree and returns the union of vault-
  relative POSIX paths the renderer would produce. Pure
  function; unit-testable without invoking `render_tree`. Called
  by `adopt.compute_adoption_set`. **Source-of-truth pin (AC22):**
  `enumerate_rendered_paths` is the canonical walker for the
  kit-owned-by-recipe path set. `render_tree` (in
  `llm_wiki_kit.render`) is refactored to delegate its own path-
  enumeration step to `enumerate_rendered_paths` so the two
  cannot diverge — equivalence is structural (shared
  implementation), not a test-time coincidence. Any path filter
  the renderer applies (e.g., skipping templates that produce
  empty output) must be applied identically in both call sites
  OR — preferred — the renderer always writes every path
  `enumerate_rendered_paths` lists, even when the rendered
  output is empty (an empty file is still a kit-owned file the
  adoption baseline should journal).
- **`llm_wiki_kit.models`** — two new event classes:
  `PageAdoptedEvent` and `ManagedRegionAdoptedEvent`. Both
  inherit `_EventBase` (timestamp, by) and carry the same
  payload as their `Write` counterparts. Added to the
  `Event` discriminated union. `VaultState` gains two new
  fields: `adopted_pages: dict[str, PageAdoptedEvent]` and
  `adopted_regions: dict[tuple[str, str],
  ManagedRegionAdoptedEvent]`. Both have `default_factory=dict`
  so older `VaultState` payloads round-trip unchanged.
  **Semantics:** these dicts track the latest *adopt event* per
  path / `(file, region)` — they are NOT a "currently sticky-
  adopt" view. A subsequent `PageWriteEvent` does not remove
  the entry from `adopted_pages`; it just means
  `_latest_baseline_event_kind` will return `"write"`. Callers
  who need "kit-owned territory" (e.g.,
  `doctor.check_orphans`) use `set(state.page_writes) |
  set(state.adopted_pages)` — the union of all paths the kit
  has ever claimed. Callers who need "is this path currently
  sticky-adopt?" must consult `_latest_baseline_event_kind`,
  not the dict membership.
- **`llm_wiki_kit.journal`** — `replay_state` extends to
  populate `state.adopted_pages` and `state.adopted_regions`
  from the new events. No locking or fsync behavior change.
- **`llm_wiki_kit.write_helper`** —
  - `_baseline_hash` walks both `PageWriteEvent` and
    `PageAdoptedEvent`, returning the latest-by-position hash
    regardless of class.
  - `_managed_region_baseline_hash` walks both
    `ManagedRegionWriteEvent` and `ManagedRegionAdoptedEvent`,
    returning the latest content_hash.
  - `_latest_baseline_event_kind(journal_path, relative_path) ->
    Literal["write", "adopted", "none"]` — new internal helper
    returning the discriminator-equivalent for the latest
    page-level baseline event. Used by `safe_write`'s adopt-
    aware branch.
  - `safe_write` gains two new disjuncts in its predicate (ADR-
    0008 §Decision sub-choice 3), evaluated when the latest
    baseline event is a `PageAdoptedEvent`:
    1. **Adopt-match no-rewrite.** `new_hash == adopted_hash ==
       on_disk_hash` — append a `PageWriteEvent(hash=new_hash)`
       (supersedes the adopt baseline, clearing the sticky-adopt
       state for the path) and DO NOT touch the file. Mirrors
       the existing per-file adopt fast-path's inode-preserving
       behavior, extended to the journaled-as-adopted case.
       Returns `WriteResult.WRITTEN`.
    2. **Adopt-differ proposal.** `new_hash != adopted_hash`
       (and any `on_disk_hash`) — route to the proposal branch
       even if `on_disk_hash == baseline_hash`. The user's
       adopted bytes survive untouched; the kit's intended
       content lands as `<path>.proposed`. Emits
       `PageProposalEvent`, returns `WriteResult.PROPOSAL`.
  - `safe_write_region` gains the equivalent two disjuncts
    keyed on `ManagedRegionAdoptedEvent` (match-no-rewrite +
    differ-proposal). The match-no-rewrite branch appends a
    `ManagedRegionWriteEvent(content_hash=new_region_hash)`
    that supersedes the region adopt baseline, AND does not
    touch the file — preserves the host file's unmanaged
    content byte-for-byte.
  - `_known_regions_for_file` (private helper used by
    `resolve_proposal`'s region re-baseline path) extends to
    walk both `ManagedRegionWriteEvent` and
    `ManagedRegionAdoptedEvent` — without this extension, a
    host whose regions only have adopt events emits zero
    `ManagedRegionWriteEvent`s during resolve, leaving the
    region-level sticky-adopt baselines uncleared and looping
    on every subsequent aggregator pass.
  - `resolve_proposal` is unchanged in shape; the
    `PageWriteEvent` it emits supersedes any prior
    `PageAdoptedEvent` (latest-wins in `_baseline_hash`),
    clearing the page-level sticky-adopt state. Together with
    the `_known_regions_for_file` extension above, the
    `ManagedRegionWriteEvent`s emitted in the region re-baseline
    loop clear the region-level sticky-adopt state.
- **`llm_wiki_kit.doctor`** — `check_orphans` extends the
  `journaled` set to `set(state.page_writes) |
  set(state.adopted_pages)`. The kit-owned-dir derivation
  (top-level component of each journaled path) takes the union
  too. No new `Issue` kind; `orphan` continues to mean
  "in kit-owned territory but no event."
  `check_managed_region_drift` keeps its current shape — the
  baseline-hash lookup goes through
  `_managed_region_baseline_hash` (above), so adopt-time
  baselines surface as drift-clean unless the on-disk region
  body actually diverges. `check_missing` is unaffected (an
  adopted file is on disk by definition; the check looks for
  journaled paths that are absent on disk).
- **`llm_wiki_kit.errors.WikiError`** — re-used; no new
  exception type. The already-a-vault refusal, symlink-escape,
  and pre-flight errors all raise the existing `WikiError`.

## Acceptance criteria

- [ ] **AC1 — `wiki init --adopt` over an empty target behaves
  identically to `wiki init`.** Same journal events, same on-disk
  output, no `wiki init: adopted ...` summary line printed. Pinned
  by comparing the journal byte-for-byte against a non-`--adopt`
  control run.
- [ ] **AC2 — `wiki init --adopt` over a target containing only
  byte-identical kit-owned files emits one `PageAdoptedEvent` per
  file, one `PageWriteEvent` per file from the render phase's
  adopt-match no-rewrite branch, and zero `PageProposalEvent`s.**
  All adoption events appear in `read_events(journal_path)`
  between the `VaultInitEvent` and the first
  `PrimitiveInstallEvent`. Original files are byte-identical to
  their pre-call content AND inode-preserved (`stat().st_ino`
  unchanged across the call — the adopt-match no-rewrite branch
  in `safe_write` skips the disk write).
- [ ] **AC3 — `wiki init --adopt` over a target containing
  byte-differing kit-owned files emits one `PageAdoptedEvent` per
  file AND one `PageProposalEvent` per file during the render
  phase.** The adopted-bytes baseline and the proposed-bytes
  hash are both in the journal. The `.proposed` sidecar contains
  the kit's would-render bytes; the original file is
  byte-identical to its pre-call content.
- [ ] **AC4 — `wiki init --adopt` against a target whose
  `<target>/.wiki.journal/journal.jsonl` already contains a
  `PrimitiveInstallEvent` exits 2 with `target is already a wiki
  vault` on stderr.** No journal modification, no on-disk writes.
  Pinned alongside AC4b below to nail the predicate's exact
  condition.
- [ ] **AC4b — `wiki init --adopt` against a target whose journal
  carries only `VaultInitEvent` and/or adoption events (no
  `PrimitiveInstallEvent`) PROCEEDS as a re-run AND re-emits the
  adopt events idempotently.** Pinned by hand-pre-seeding a
  journal with `VaultInitEvent` + one `PageAdoptedEvent` for a
  pre-placed file, then running `wiki init --adopt`. Assert:
  (a) exit 0;
  (b) the journal slice between the seed prefix and the first
  new `PrimitiveInstallEvent` contains a FRESH `PageAdoptedEvent`
  for the same path with the same hash (verifying re-emit, NOT a
  skip-if-already-adopted optimisation that would diverge from
  the spec's idempotent-replay claim);
  (c) `PrimitiveInstallEvent`s land afterward, completing the
  install. Pins the recovery contract for §Edge cases "Crash
  during the adoption phase".
- [ ] **AC5 — `wiki init` (without `--adopt`) against a
  non-empty target retains today's `target directory is not
  empty` refusal.** Pinned by `test_wiki_init_refuses_non_empty`
  (existing test should remain green).
- [ ] **AC6 — `PageAdoptedEvent`s emit in `sorted(path)` order,
  interleaved with each host file's `ManagedRegionAdoptedEvent`s
  (in `sorted(region)` order) immediately after that file's
  page event.** Pinned by feeding a target with two unordered
  pre-existing host files each with two regions; assert the
  slice has the literal pattern `[PageAdoptedEvent(file_a),
  ManagedRegionAdoptedEvent(file_a, region_x),
  ManagedRegionAdoptedEvent(file_a, region_y),
  PageAdoptedEvent(file_b), ManagedRegionAdoptedEvent(file_b,
  region_x), ManagedRegionAdoptedEvent(file_b, region_y)]` —
  no host-file region events appear after a different host
  file's page event.
- [ ] **AC7 — `by="wiki-init-adopt"` on adoption-phase events
  ONLY.** `VaultInitEvent.by == "wiki-init"`,
  `PrimitiveInstallEvent.by == "wiki-init"`,
  aggregator-emitted `ManagedRegionWriteEvent.by == "wiki-init"`,
  per-primitive `PageWriteEvent.by == <primitive_name>`. Only
  `PageAdoptedEvent` and `ManagedRegionAdoptedEvent` carry the
  new vehicle string.
- [ ] **AC8 — A pre-existing `frontmatter.schema.yaml` with the
  user's region content gets both a `PageAdoptedEvent` AND one
  `ManagedRegionAdoptedEvent` per parseable region.** Pinned by
  pre-seeding a target with a hand-rolled
  `frontmatter.schema.yaml` containing two managed regions and
  asserting the journal contains both events with hashes that
  match `managed_regions.canonical_region_body` applied to each
  region body.
- [ ] **AC9 — A pre-existing managed-region host file whose
  markers DO NOT parse causes `wiki init --adopt` to exit 2
  with `cannot adopt managed-region host '<file>': markers do
  not parse`.** No journal events appended. Pinned by feeding a
  target with an unbalanced-markers host file and asserting the
  exit code, the stderr text, and that
  `.wiki.journal/journal.jsonl` does not exist (or, if the
  `target.mkdir` ran first, is empty / contains zero events).
- [ ] **AC9b — A pre-existing managed-region host file that
  parses but is missing markers for a region the recipe needs
  causes `wiki init --adopt` to exit 2 with `cannot adopt
  managed-region host '<file>': missing markers for region
  '<region>' the recipe needs`.** No journal events appended.
  Pinned by feeding a target whose host file declares only
  `types` but the recipe includes a primitive that contributes
  to `fields`.
- [ ] **AC10 — A pre-existing `.proposed` sidecar whose
  non-`.proposed` path is in the recipe's rendered closure is
  surfaced as `pre_existing_sidecars` but NOT adopted.** Pinned
  by feeding a target with both `wiki/people/.gitkeep`
  (kit-owned) and `wiki/people/.gitkeep.proposed`. Assert
  exactly one `PageAdoptedEvent` for `wiki/people/.gitkeep`,
  zero adoption events for the sidecar, and stderr contains
  the informational `wiki init: found 1 pre-existing kit-owned
  .proposed sidecar.` line. A `.proposed` file at a path
  OUTSIDE the rendered closure (e.g.,
  `notes/personal.md.proposed`) is ignored entirely (no
  warning).
- [ ] **AC11 — A user-territory file under a kit-owned directory
  is NOT adopted but DOES surface as `orphan` post-run.** Pinned
  by feeding a target with `wiki/people/uncle-bob.md` (the
  user's own page, not produced by the `people` primitive's
  `files/` tree). Assert no adoption event for `uncle-bob.md`,
  and `wiki doctor` after the run reports `Issue(ORPHAN,
  "wiki/people/uncle-bob.md")`.
- [ ] **AC12 — A user-territory file OUTSIDE every kit-owned
  directory is NOT adopted and NOT surfaced as `orphan`.**
  Pinned by feeding a target with `notes/personal.md` (no
  primitive declares anything under `notes/`). Assert no
  adoption event AND `wiki doctor` does not flag the file.
- [ ] **AC13 — `safe_write` after a `PageAdoptedEvent` baseline
  produces a `PageProposalEvent` when the kit's content differs
  from the adopted bytes, even though `on_disk_hash ==
  baseline_hash`.** Construction test in
  `tests/unit/test_write_helper.py`: append
  `PageAdoptedEvent(hash=h_adopted)`; call `safe_write(content)`
  where `sha256(content) != h_adopted`; assert
  `WriteResult.PROPOSAL`, a `.proposed` sidecar lands, the
  original file's bytes are byte-identical to its pre-call
  content, and a `PageProposalEvent` is the latest journal
  entry for the path.
- [ ] **AC14 — `safe_write` after a `PageAdoptedEvent` baseline
  with `new_hash == adopted_hash == on_disk_hash` takes the
  adopt-match no-rewrite branch.** Assert `WriteResult.WRITTEN`,
  exactly one new `PageWriteEvent(hash=new_hash)` is journaled,
  no sidecar, AND `target.stat().st_ino` equals the pre-call
  inode (the file is NOT rewritten). Pins the inode-preservation
  contract AC2 depends on.
- [ ] **AC15 — `safe_write_region` after a
  `ManagedRegionAdoptedEvent` baseline produces a
  `PageProposalEvent` when the kit's aggregated region body
  differs from the adopted region body.** Same shape as AC13.
  The host file's `stat().st_ino` is unchanged when the
  match-no-rewrite branch fires (kit's region content matches
  adopted region content).
- [ ] **AC16 — `resolve_proposal` against an adopted-then-proposed
  page path emits a `PageWriteEvent` that becomes the new latest
  baseline; subsequent `safe_write` calls with the same content
  take the direct-write branch.** Pins the page-level
  "sticky-adopt clears on resolve" contract.
- [ ] **AC16b — `resolve_proposal` against an adopted-then-proposed
  managed-region host file emits one `ManagedRegionWriteEvent`
  per region present in BOTH the journal's adopt events for the
  file AND the resolved content (the
  `_known_regions_for_file`-walks-both extension).** Pinned by
  pre-seeding a host with a `PageAdoptedEvent` + two
  `ManagedRegionAdoptedEvent`s for it, surfacing an aggregator-
  drift proposal, calling `resolve_proposal`, and asserting two
  `ManagedRegionWriteEvent`s appear (one per region) AFTER the
  `PageWriteEvent` `resolve_proposal` emits. Without this AC,
  the region-level sticky-adopt baselines never clear and every
  subsequent aggregator pass re-proposes the host.
- [ ] **AC17 — `wiki doctor` after `wiki init --adopt` on a mixed
  target reports exactly the expected issues: zero orphans for
  user-territory files outside kit-owned directories; one
  orphan per user-territory file under a kit-owned directory;
  one `pending-proposal` per adopt-time proposal; zero
  `missing`, zero `page-drift`, zero `managed-region-drift`.**
  Integration-level assertion against `doctor.run_doctor` after
  a representative `wiki init --adopt` run.
- [ ] **AC18 — Adoption phase events are durable before any
  install-pipeline event.** Assert the slice
  `read_events(journal_path)` in run order has all
  `PageAdoptedEvent`s and `ManagedRegionAdoptedEvent`s strictly
  before the first `PrimitiveInstallEvent`. Mirrors `wiki
  upgrade` AC9's "aggregator strictly after per-primitive" pin.
- [ ] **AC19 — Symlink-escape during the adoption walk raises
  `WikiError` and leaves the journal empty.** Pinned by feeding
  a target with a symlink whose target resolves outside the
  vault root; assert `WikiError`, exit 2, no
  `.wiki.journal/journal.jsonl` (or an empty one if the
  `target.mkdir` ran before the walk).
- [ ] **AC20 — `replay_state` over a journal with adoption
  events populates `state.adopted_pages` and
  `state.adopted_regions` correctly; older journals without
  adoption events replay unchanged (round-trip equivalence with
  pre-AC20 behavior).** Pinned by a Pydantic-model round-trip
  test on a hand-crafted journal containing one each of the
  new event types.
- [ ] **AC21 — `wiki init --adopt` over a target with N
  pre-existing kit-owned files prints `wiki init: adopted N
  file.` (or `files.`) as the final stdout line.** Pluralisation
  pinned for `N == 1` and `N != 1`. When N == 0, no summary
  line is emitted.
- [ ] **AC20a — TOCTOU window between `compute_adoption_set` and
  the render phase produces a `.proposed` sidecar, not a silent
  overwrite.** Integration-level construction: pre-place a
  kit-owned file with content C₁ (matching what the kit would
  render — so `compute_adoption_set` hashes h(C₁) as the adopt
  baseline). Wrap `adopt.compute_adoption_set` (the module-level
  contract surface, NOT any `safe_write` internal helper or
  `Path.read_bytes`) with a one-shot side-effect that
  atomically `os.replace`s the file with C₂ after returning the
  `AdoptionSet`. Run `wiki init --adopt`. Assert:
  (a) the user's C₂ bytes survive on disk;
  (b) a `<path>.proposed` sidecar lands with the kit's content
  C₁;
  (c) the journal contains both `PageAdoptedEvent(hash=h(C₁))`
  and `PageProposalEvent(path, proposed_path,
  hash=h(C₁))` (the journaled adopt hash is the snapshot at
  walk time, NOT what's currently on disk — `wiki doctor` will
  later report `page-drift` against the adopted baseline,
  which is the documented surfacing for the TOCTOU residual
  per spec §Edge cases "TOCTOU between adoption walk and
  render-phase writes").
- [ ] **AC21b — `wiki journal tail` / `grep` / `explain` over a
  post-adopt journal render `page.adopted` and
  `managed_region.adopted` rows without raising.** Pins the
  `_EVENT_SUMMARY_FIELDS` extension that the new event classes
  require (the dict's docstring at `cli.py:1409` says missing
  rows raise `KeyError` — silent fallthrough is rejected by
  design). Without this AC, the ADR §Positive "audit trail is
  explicit" claim is structurally undelivered: every
  post-adopt user running `wiki journal tail` crashes on the
  first adopt row.
- [ ] **AC22 — `install.enumerate_rendered_paths` is the source
  of truth for the kit-owned path set AND `render_tree` walks
  that same set.** Structural pin (not a coincidence-of-output
  pin): `render_tree`'s implementation calls into
  `enumerate_rendered_paths` (or its underlying walker) so the
  two functions cannot diverge. Acceptance test asserts
  `set(enumerate_rendered_paths([p], sources))` equals the
  set of vault-relative paths produced by driving `render_tree`
  with the same inputs into a tmp dir AND walking the output
  tree. Without this AC, two reasonable implementations of the
  two functions could differ on "templates that render empty
  output," producing `missing` issues in `wiki doctor` forever
  or silent overwrites via the no-history → direct-write path. Pinned by
  driving `render_tree` against a fixture primitive into a tmp
  dir, walking the output tree, and asserting set-equality with
  `enumerate_rendered_paths`'s return value. Without this AC,
  `compute_adoption_set` could journal adopt baselines for paths
  the renderer never writes (surfacing as `missing` in `wiki
  doctor` forever) or miss baselines for paths the renderer does
  write (silently overwriting user content).

## Non-goals

- **No retry-adopt command.** A future `wiki init --retry-adopt`
  is out of scope (ADR-0008 §Decision sub-choice 6). `wiki
  upgrade` is the productive recovery path.
- **No `wiki upgrade --adopt` extension.** `wiki upgrade` keeps
  its current contract (re-render version-bumped primitives over
  the existing closure). The adopt-aware `safe_write` /
  `safe_write_region` predicates apply transparently to
  upgrade-time writes — an adopted baseline routes a
  differing-content upgrade write to proposal — but the
  upgrade-side is not in scope for THIS spec.
- **No CLI flag to suppress region-level adoption.** ADR-0008
  §Neutral leaves this as a future option if real users hit
  parsing trouble.
- **No CLI flag to flip the default.** `--adopt` is explicit
  (ADR-0008 §Neutral); re-evaluate after one release cycle.
- **No `FileAdoptedEvent`-by-different-name.** The shape is
  `PageAdoptedEvent` to mirror `PageWriteEvent`; the
  page-vs-managed-region split parallels the existing event
  taxonomy.
- **No journal schema migration tooling.** The two new event
  classes are additive (defaults on `VaultState` fields,
  discriminated-union additions); older journals replay
  unchanged. A future schema-migration spec can revisit if more
  invasive changes land.
- **No support for adopting from a different folder.** `--adopt
  <other-folder>` (taking a separate source path) is not
  supported; the flag is boolean and operates on the
  positional `<path>`. Cross-folder adoption is a copy-then-
  init operation outside the kit's scope.
- **No performance optimisation for large adoption sets.**
  N-file adoption is O(N) hashing + O(N) journal appends; the
  kit assumes vaults under ~1k files at adopt time. If real
  usage hits 10k+ files, a future spec can introduce streaming
  or batched journal appends.
- **No interactive adopt-then-resolve flow.** `wiki init
  --adopt` runs end-to-end, then surfaces proposals via the
  existing `wiki-conflict` skill. An interactive prompt-per-
  conflict is out of scope.
- **No `wiki doctor --pre-adopt` dry-run.** Knowing what would
  be adopted ahead of time is computable from the recipe + on-
  disk state; a dedicated CLI surface is overhead. Revisit on
  user signal.
- **No backward-compat for pre-ADR-0008 vaults without adoption
  events.** Existing vaults journal-replay unchanged because
  the new `VaultState` fields default to empty dicts.

## Constraints

- **No new top-level CLI subcommand.** The flag attaches to
  `wiki init`; no `wiki adopt` verb.
- **No new module boundary except `llm_wiki_kit/adopt.py`.**
  Adoption-set computation lives there; helpers in `install.py`
  (`enumerate_rendered_paths`), `write_helper.py` (predicate
  disjunct, `_latest_baseline_event_kind`), and `models.py`
  (two new event classes + `VaultState` fields) stay in their
  existing modules.
- **No new runtime dependency.** Stdlib + `pyyaml` +
  `pydantic` only.
- **No new event payload field on `PageWriteEvent` /
  `ManagedRegionWriteEvent`.** The discriminated-union approach
  (new classes) replaces the rejected `reason` field option
  per ADR-0008 §Decision sub-choice 4.
- **No bypass of `safe_write` / `safe_write_region`.** Adoption
  events are appended directly via `journal.append_event`
  (matching how `VaultInitEvent` and `PrimitiveInstallEvent`
  are appended today — these are not page writes, so they don't
  route through `safe_write`). The render phase routes
  exclusively through the existing safe-write helpers.
- **No journal-locking change.** The adoption phase appends
  events inside the existing `journal.use_journal_cache` scope;
  `fcntl.flock` semantics are unchanged.
- **No change to the `.obsidianignore` bypass.** Adoption-time
  proposals (from the render phase) trigger
  `_ensure_obsidianignore` exactly as `wiki add` /
  `wiki ingest` do today.
- **No change to managed-region marker syntax.** ADR-0003's
  `<!-- BEGIN MANAGED: id -->` / `# BEGIN MANAGED: id` are the
  only markers the adoption phase recognizes.
