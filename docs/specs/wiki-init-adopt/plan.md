# Plan: wiki-init-adopt

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** `docs/specs/wiki-init-adopt/spec.md`
- **Owner:** maintainer

## Approach

Three landing-ready PRs. The breakup respects the dependency arrow:
event-class additions are load-bearing for the
`write_helper` predicate change, which is load-bearing for the
`_cmd_init` surface. Splitting reduces blast radius and gives the
reviewer a smaller diff per pass.

- **PR-A — Event types + replay.** New `PageAdoptedEvent` and
  `ManagedRegionAdoptedEvent` in `models.py`; `VaultState` gains
  `adopted_pages` and `adopted_regions`; `replay_state` populates
  them. `_baseline_hash` and `_managed_region_baseline_hash` in
  `write_helper.py` walk both write- and adopted-class events for
  the latest baseline. `doctor.check_orphans` extends `journaled`
  to include adopted paths. No CLI change; no
  `safe_write` predicate change yet. Verification is unit-level:
  Pydantic round-trip, `replay_state` table-tests, `_baseline_hash`
  table-tests, `check_orphans` table-tests.
- **PR-B — `safe_write` / `safe_write_region` adopt-aware
  predicate.** The disjunct ADR-0008 §Decision sub-choice 3 names:
  when the latest baseline event is `PageAdoptedEvent` (or
  `ManagedRegionAdoptedEvent`) AND the kit's new hash differs from
  the adopted hash, route to proposal even when `on_disk_hash ==
  baseline_hash`. Helper
  `_latest_baseline_event_kind(journal_path, relative_path) ->
  Literal["write", "adopted", "none"]` factors the lookup out of
  the predicate. Construction tests pin both branches; spec ACs
  13–16 are the contract tests.
- **PR-C — `_cmd_init --adopt` + `adopt.py` + `install.enumerate_rendered_paths`.**
  New CLI flag; the already-a-vault refusal check; adoption-set
  computation; the journal-cache scope's adoption-phase event
  appends; pre-existing-sidecar surfacing; summary lines. PR-C
  depends on PR-A's event types and PR-B's predicate; both must
  land first or the integration tests cannot pin the end-to-end
  contract.

Verification mode is TDD throughout — every model, helper, and
behavior gets a failing test before the implementation. Contract
tests live in `spec.md` §Acceptance criteria; construction tests
attach to each plan step below.

### Declined patterns (commitments for REVIEW)

- **Tempted to use a `reason: Literal["fresh","adopt","recovery"]`
  field on `PageWriteEvent` instead of a new class.** Declined per
  ADR-0008 §Decision sub-choice 4: payload-field dispatch is
  structurally equivalent to discriminator dispatch with extra
  schema-migration cost. New classes match the kit's
  one-class-per-event convention.
- **Tempted to refactor `safe_write`'s predicate into a state
  machine / dispatch table.** Declined: today's predicate is six
  named disjuncts in a single function; the adopt addition makes
  seven. A state-machine refactor is independent surface to
  review and not justified by one new branch. If a future change
  pushes the count past ~10, revisit.
- **Tempted to walk the whole target tree at adopt time and
  journal every file (regardless of recipe ownership).** Declined
  per ADR-0008 §Alternatives "Adopt by walking the target
  directory wholesale (no recipe filter)" — that design silently
  expands kit territory and breaks `wiki doctor`'s orphan-signal.
- **Tempted to extend `_warn_if_install_pipeline_uncached` to
  cover the adoption phase too.** Declined: the adoption phase
  is a small, bounded prefix that runs INSIDE the same
  `journal.use_journal_cache` scope as the install pipeline; one
  warning suffices. The existing helper already fires once per
  resolved path.
- **Tempted to add a `wiki adopt` verb instead of a `--adopt`
  flag.** Declined: the operation IS init (one-shot vault
  creation); the flag-on-init shape is the smallest surface that
  satisfies the spec.

## Pre-conditions

- ADR-0008 merged (this plan implements the ADR's pinned
  decisions).
- `safe_write` per-file adopt fast-path is shipped (it is — see
  `docs/specs/safe-write-ordering/spec.md`); the new adopt-aware
  predicate disjunct in PR-B is additive on top of it.
- `journal.use_journal_cache` +
  `_warn_if_install_pipeline_uncached` ship in `install.py`
  already (the qC4 install-pipeline-cache contract from
  [`docs/specs/journal-reader-cache/spec.md`](../journal-reader-cache/spec.md));
  no changes here.
- `discover_primitives`, `resolve_recipe_primitives`,
  `validate_contributions`, `render_tree`, and
  `aggregate_region_contributions` keep their current shape.
- No conflicting work in flight on `models.py` / `write_helper.py`
  / `cli.py:_cmd_init`.

**Strict PR ordering: A → B → C.** PR-A extends `_baseline_hash`
to walk `PageAdoptedEvent`, so a `PageAdoptedEvent` in the journal
becomes a "no-drift" baseline for `safe_write`. PR-B ships the
adopt-aware predicate that routes differing-content writes
against an adopt baseline to the proposal branch. **PR-C must NOT
merge before PR-B**: emitting `PageAdoptedEvent`s without the
adopt-aware predicate in place silently routes differing-content
writes to the direct-write branch (the predicate at
`write_helper.py:130-134` reads `not no_history and on_disk_hash
== baseline_hash`, which is satisfied right after a fresh
`PageAdoptedEvent`), reintroducing the silent-overwrite the
spec exists to prevent. Either land PR-A and PR-B together (one
combined PR) or require PR-B's merge SHA in PR-C's base.

## Steps

### PR-A — Event types + replay

1. **`PageAdoptedEvent` and `ManagedRegionAdoptedEvent` round-trip
   through Pydantic with stable JSON.**
   - **Tests** (new file `tests/unit/test_adopted_events.py`):
     - `test_page_adopted_event_round_trips` — construct, dump to
       JSON via `dump_event_json`, parse via `parse_event_line`,
       assert structural equality.
     - `test_managed_region_adopted_event_round_trips` — same
       shape, region payload.
     - `test_adopted_events_in_discriminated_union_dispatch` —
       feed both events through `read_events(tmp_path / "j.jsonl")`
       written by `append_event`; assert each is parsed into the
       correct subclass via the `type` discriminator.
     - `test_adopted_events_default_hash_algo_sha256` — pin
       `hash_algo == "sha256"` defaults on both classes (matches
       `PageWriteEvent` / `ManagedRegionWriteEvent`).
   - **Verify:** `pytest tests/unit/test_adopted_events.py` fails
     at import (`PageAdoptedEvent` does not exist yet).
1. **`models.py` adds the two classes and threads them into the
   discriminated `Event` union; the tests above pass.**
   - Implement `PageAdoptedEvent(_EventBase)` with `type:
     Literal["page.adopted"] = "page.adopted"`, `path: str`,
     `hash: str`, `hash_algo: str = "sha256"`.
   - Implement `ManagedRegionAdoptedEvent(_EventBase)` with
     `type: Literal["managed_region.adopted"] =
     "managed_region.adopted"`, `file: str`, `region: str`,
     `content_hash: str`, `hash_algo: str = "sha256"`.
   - Append both to the `Event = Annotated[... | ... , Field(
     discriminator="type")]` union.
   - Extend `VaultState`: `adopted_pages: dict[str,
     PageAdoptedEvent] = Field(default_factory=dict)` and
     `adopted_regions: dict[tuple[str, str],
     ManagedRegionAdoptedEvent] = Field(default_factory=dict)`.
     Pydantic v2 supports `tuple[str, str]` as a dict key for
     in-memory use; the field is not serialized to disk (it's
     derived state), so the tuple key is fine.
   - **Verify:** `pytest tests/unit/test_adopted_events.py` green.
1. **`replay_state` populates the new fields.**
   - **Tests** (extend existing `tests/unit/test_journal.py`):
     - `test_replay_state_populates_adopted_pages` — append one
       `PageAdoptedEvent`; replay; assert
       `state.adopted_pages["wiki/people/.gitkeep"]` is the
       event.
     - `test_replay_state_populates_adopted_regions` — append one
       `ManagedRegionAdoptedEvent`; replay; assert
       `state.adopted_regions[("frontmatter.schema.yaml",
       "types")]` is the event.
     - `test_replay_state_latest_adopted_event_wins` — append two
       `PageAdoptedEvent`s for the same path; replay; assert
       `state.adopted_pages[path]` is the later event.
     - `test_replay_state_page_write_supersedes_page_adopted` —
       append `PageAdoptedEvent` then `PageWriteEvent` for the
       same path; replay; assert the path appears in
       `state.page_writes` AND ALSO in `state.adopted_pages`
       (replay tracks each event type's latest separately; the
       "supersession" semantics live in `_baseline_hash`'s walk,
       not in `replay_state`).
     - `test_replay_state_legacy_journal_unaffected` — replay a
       journal containing only pre-AC20 events; assert
       `state.adopted_pages == {}` and `state.adopted_regions ==
       {}` (round-trip equivalence).
   - Implement the two new dispatch branches in
     `journal.replay_state`. Keep the existing branches
     unchanged.
   - **Verify:** `pytest tests/unit/test_journal.py` green.
1. **`_baseline_hash` and `_managed_region_baseline_hash` walk
   both event classes.**
   - **Tests** (extend `tests/unit/test_write_helper.py` — these
     are extensions of an existing helper, not the adopt-aware
     predicate; the new `test_write_helper_adopt.py` file
     introduced in PR-B is reserved for adopt-aware-predicate
     contract tests so `git grep adopted tests/unit/test_*` lands
     a focused set):
     - `test_baseline_hash_returns_latest_across_adopted_and_write` —
       seed journal with `PageAdoptedEvent(hash=h1)` then
       `PageWriteEvent(hash=h2)`; assert `_baseline_hash` returns
       `h2`.
     - `test_baseline_hash_returns_adopted_when_only_adopted` —
       seed journal with only `PageAdoptedEvent(hash=h1)`;
       assert `_baseline_hash` returns `h1`.
     - Same shape for `_managed_region_baseline_hash`:
       `test_managed_region_baseline_walks_adopted` and
       `test_managed_region_baseline_latest_wins_across_classes`.
   - Implement: walk both `PageWriteEvent` and `PageAdoptedEvent`
     in `_baseline_hash`'s `for event in reversed(...)`; return
     the first match's `hash` regardless of class. Same shape for
     the region-level lookup.
   - **Verify:** `pytest tests/unit/test_write_helper.py` green.
1. **`cli._EVENT_SUMMARY_FIELDS` extends to the two new event
   classes; `wiki journal tail` / `grep` / `explain` render the
   adopt rows without crashing.**
   - **Tests** (extend `tests/unit/test_wiki_journal_readers.py`,
     or the file housing the current journal-reader tests):
     - `test_format_event_line_for_page_adopted` — construct a
       `PageAdoptedEvent`; call `_format_event_line(1, event)`;
       assert the output contains `page.adopted` and
       `path=<path>`.
     - `test_format_event_line_for_managed_region_adopted` —
       same shape, `managed_region.adopted` row.
     - `test_wiki_journal_tail_over_adopt_events_runs` — write
       a journal with a `VaultInitEvent` + one of each adopt
       event; run `wiki journal tail` via the CLI harness;
       assert exit 0 and stdout contains both `page.adopted`
       and `managed_region.adopted` literals.
   - Implement: append two rows to `_EVENT_SUMMARY_FIELDS` at
     `cli.py:1328`:
     - `PageAdoptedEvent: (("path", "path", False),)` (mirrors
       `PageWriteEvent`).
     - `ManagedRegionAdoptedEvent: (("file", "file", False),
       ("region", "region", False))` (mirrors
       `ManagedRegionWriteEvent`).
   - **Verify:** `pytest tests/unit/test_wiki_journal_readers.py`
     green; `wiki journal tail` smoke-tests pass.
1. **`doctor.check_orphans` extends `journaled` to include adopted
   pages.**
   - **Tests** (extend `tests/unit/test_doctor.py`):
     - `test_check_orphans_treats_adopted_pages_as_kit_owned` —
       seed a vault with one `PageAdoptedEvent` and the
       corresponding on-disk file; assert
       `check_orphans(state, vault_root) == []`.
     - `test_check_orphans_adopted_only_does_not_double_count` —
       no orphans even though only adoption events exist (no
       `PageWriteEvent`).
     - `test_check_orphans_user_file_in_kit_dir_after_adopt_is_orphan`
       — adopt a `wiki/people/.gitkeep`; add an unrelated
       `wiki/people/uncle-bob.md` to disk; assert
       `Issue(ORPHAN, "wiki/people/uncle-bob.md")` is reported.
       Pins AC11 at the unit level.
   - Implement: extend the `journaled = set(state.page_writes)`
     line to `journaled = set(state.page_writes) |
     set(state.adopted_pages)`; extend the owned-dirs derivation
     to walk the union too. **Update the inline doctrine comment
     at `doctor.py:242` ("Doctrine: only `page.write` events
     extend territory.")** to read "Doctrine: only `page.write`
     AND `page.adopted` events extend territory."
   - **Verify:** `pytest tests/unit/test_doctor.py` green.
1. **PR-A integration smoke.**
   - `pytest -m 'not slow'`, `ruff check llm_wiki_kit tests`,
     `ruff format --check llm_wiki_kit tests`, `mypy
     llm_wiki_kit tests` — all green.
   - Commit message: `feat(adopt): wiki-init-adopt — event classes +
     replay (PR-A of 3)`.

### PR-B — `safe_write` / `safe_write_region` adopt-aware predicate

**Test-file note.** PR-B's adopt-aware predicate tests land in a
new file `tests/unit/test_write_helper_adopt.py` rather than
extending the already-large `test_write_helper.py`. The
discoverability pattern follows `tests/unit/test_install_skill_closure.py`
(carved out of `test_install.py`): a grep for "adopted" finds the
contract pins in one file. Existing `test_write_helper.py` tests
stay green and untouched; new tests below name the new file
explicitly.

1. **`_latest_baseline_event_kind` returns the discriminator of
   the latest baseline event.**
   - **Tests** (new file `tests/unit/test_write_helper_adopt.py`):
     - `test_latest_baseline_kind_returns_write_when_only_write` —
       seed `PageWriteEvent`; assert `"write"`.
     - `test_latest_baseline_kind_returns_adopted_when_only_adopted` —
       seed `PageAdoptedEvent`; assert `"adopted"`.
     - `test_latest_baseline_kind_returns_none_for_unknown_path`
       — empty journal; assert `"none"`.
     - `test_latest_baseline_kind_write_supersedes_adopted` —
       seed `PageAdoptedEvent` then `PageWriteEvent` for the
       same path; assert `"write"`.
     - `test_latest_baseline_kind_latest_adopted_wins` — seed
       `PageWriteEvent` then `PageAdoptedEvent` for the same
       path (the resolve-then-re-adopt edge); assert
       `"adopted"`.
   - Implement: walk `reversed(_read_events_cached(journal_path))`,
     dispatch on `isinstance` for both `PageWriteEvent` and
     `PageAdoptedEvent` for the page path, return the matching
     literal. Mirror for managed-region kind if needed (the
     region predicate calls a region-specific equivalent).
   - **Verify:** `pytest tests/unit/test_write_helper_adopt.py
     tests/unit/test_write_helper.py` green (the new file and
     the existing one stay green; the new file holds the
     adopt-aware predicate tests, the old file's existing tests
     stay untouched).
1. **`safe_write` produces a `PageProposalEvent` for adopted-then-
   differing content (AC13).**
   - **Tests** (extend `tests/unit/test_write_helper_adopt.py`):
     - `test_safe_write_after_page_adopted_with_differing_content_proposes` —
       seed `PageAdoptedEvent(hash=h_user)`; pre-place file on
       disk with bytes matching `h_user`; call `safe_write(new
       content where sha256(new) != h_user)`; assert
       `WriteResult.PROPOSAL`, original file bytes unchanged,
       `.proposed` sidecar present with the new content,
       `PageProposalEvent` is the latest event for the path.
     - `test_safe_write_after_page_adopted_with_matching_content_no_rewrite`
       (AC14) — seed `PageAdoptedEvent(hash=h_kit)`; pre-place
       file with bytes matching `h_kit`; capture
       `target.stat().st_ino` as `pre_ino`; call `safe_write(
       content where sha256(content) == h_kit)`; assert
       `WriteResult.WRITTEN`, no sidecar, exactly one new
       `PageWriteEvent(hash=h_kit)`, AND
       `target.stat().st_ino == pre_ino` (the file was NOT
       rewritten — the adopt-match no-rewrite branch fired).
       Pins the inode-preservation contract AC2 depends on.
     - `test_safe_write_after_page_adopted_event_durable_when_disk_write_raises`
       — monkeypatch `Path.write_bytes` to raise; assert the
       `PageProposalEvent` is durable, no sidecar on disk,
       original file unchanged. Pins event-before-disk for the
       new branch.
     - `test_safe_write_resolve_then_safe_write_clears_adopt_sticky`
       — seed `PageAdoptedEvent`; call `resolve_proposal(content)`;
       assert a subsequent `safe_write(content)` takes the
       direct-write branch (no new proposal). Pins AC16 at the
       unit level.
   - Implement the new disjunct in `safe_write`:
     - Before the existing `direct_write` computation, look up
       `latest_kind = _latest_baseline_event_kind(journal_path,
       relative_path)`. If `latest_kind == "adopted"` and
       `new_hash != baseline_hash`, force the proposal branch
       (skip the existing direct-write disjuncts).
     - Adopt-sticky note: a `PageWriteEvent` after a
       `PageAdoptedEvent` (e.g., from `resolve_proposal`) makes
       `latest_kind == "write"`, restoring the standard
       semantics. Pinned by
       `test_safe_write_resolve_then_safe_write_clears_adopt_sticky`.
   - **Verify:** `pytest tests/unit/test_write_helper_adopt.py` green.
1. **`safe_write_region` produces a `PageProposalEvent` for
   region-adopted-then-differing content (AC15).**
   - **Tests** (same file):
     - `test_safe_write_region_after_adopted_with_differing_body_proposes`
       — seed `ManagedRegionAdoptedEvent(content_hash=h_user)`;
       pre-place the region body on disk with that hash;
       construct `new_content` whose canonicalised hash
       differs; call `safe_write_region`; assert
       `WriteResult.PROPOSAL`, host file unchanged, sidecar
       present, `PageProposalEvent` journaled.
     - `test_safe_write_region_after_adopted_with_matching_body_no_rewrite`
       (AC15) — seed `ManagedRegionAdoptedEvent(content_hash=h_kit)`;
       pre-place body matching `h_kit`; capture
       `host.stat().st_ino`; call `safe_write_region(new_content)`
       whose canonicalised hash equals `h_kit`; assert
       `WriteResult.WRITTEN`, exactly one new
       `ManagedRegionWriteEvent(content_hash=h_kit)`, host file
       inode unchanged.
     - `test_known_regions_for_file_walks_adopted_events` —
       seed two `ManagedRegionAdoptedEvent`s (no
       `ManagedRegionWriteEvent`s); call
       `_known_regions_for_file(journal_path, file)`; assert
       both regions are returned in first-seen order. Pins the
       PR-B extension to `_known_regions_for_file` that AC16b
       depends on.
     - `test_resolve_proposal_re_baselines_region_only_adopted_host`
       (AC16b) — seed `PageAdoptedEvent(host)` and two
       `ManagedRegionAdoptedEvent(host, region_x|region_y)`,
       then a `PageProposalEvent` for `host` with a body
       containing both regions; call `resolve_proposal(host,
       merged_content, ...)`; assert the journal slice contains
       one `PageWriteEvent(host)` followed by two
       `ManagedRegionWriteEvent`s (one per region) — without
       the `_known_regions_for_file` extension, the test goes
       red (zero region events emitted). Pins the
       region-resolve clearing contract end-to-end.
   - Implement the equivalent disjunct in `safe_write_region`'s
     predicate. Reuse the region-level `_latest_baseline_event_kind`
     equivalent (a small `_latest_managed_region_event_kind`
     helper if the page-level one doesn't fit; keep both local
     to `write_helper.py`).
   - **Verify:** `pytest tests/unit/test_write_helper_adopt.py` green.
1. **`resolve_proposal` clears the sticky-adopt state.**
   - The PR-B change to `resolve_proposal` is zero — it already
     emits `PageWriteEvent` which, per
     `_latest_baseline_event_kind`'s "write supersedes adopted"
     semantics, clears the sticky state.
   - The test `test_safe_write_resolve_then_safe_write_clears_adopt_sticky`
     above pins this end-to-end.
1. **PR-B integration smoke.**
   - Re-run the four gates; commit message `spec:
     wiki-init-adopt — adopt-aware safe_write predicate (PR-B
     of 3)`.

### PR-C — `_cmd_init --adopt` + `adopt.py` + `install.enumerate_rendered_paths`

1. **`install.enumerate_rendered_paths` returns the set of
   vault-relative paths the renderer would produce.**
   - **Tests** (new file `tests/unit/test_install_enumerate.py`):
     - `test_enumerate_rendered_paths_for_core_returns_seed_files`
       — feed `[core]` + sources; assert the returned set
       contains the known core seed paths (e.g.,
       `frontmatter.schema.yaml`, `.gitignore`,
       `AGENTS.md`).
     - `test_enumerate_rendered_paths_union_across_primitives` —
       feed `[core, people]`; assert the union is the symmetric
       sum of each primitive's `files/` tree contents.
     - `test_enumerate_rendered_paths_handles_nested_directories`
       — feed a primitive whose `files/` tree has a deep nested
       structure; assert paths are vault-relative POSIX.
     - `test_enumerate_rendered_paths_returns_empty_for_no_files_dir`
       — feed a primitive with no `files/` directory; assert no
       contribution to the set.
     - `test_enumerate_rendered_paths_matches_render_tree_output`
       (AC22) — render one primitive's `files/` tree into a
       tmp vault via `render_tree(...)`, walk the produced
       paths, and assert the resulting set equals
       `enumerate_rendered_paths([primitive], sources)`. With
       the structural pin below (render_tree shares the
       walker), this test catches "renderer skips a path
       enumerate-paths lists" — which should be impossible
       after the refactor but is the regression we'd want loud
       if it landed.
   - Implement: walk `sources[primitive.name] / "files"` for each
     primitive; for each file, compute the relative path against
     the source root; return the union as a `set[str]`. Pure
     function; no I/O outside reading the file tree.
   - **Structural pin (AC22 source-of-truth):** refactor
     `llm_wiki_kit.render.render_tree` to call into
     `enumerate_rendered_paths` for its own path enumeration
     step. Today `render_tree` walks the source tree directly
     (`render.py`); after the refactor, it iterates
     `enumerate_rendered_paths([primitive], {primitive.name:
     source_dir.parent})` for the path set and reads source
     content for each path. This makes equivalence structural.
     Construction tests:
     - `test_render_tree_uses_enumerate_rendered_paths` mocks
       `enumerate_rendered_paths` to return a fixed set and
       asserts `render_tree` writes exactly those paths.
     - `test_render_tree_output_byte_equal_before_and_after_refactor`
       — characterization pin. Land the refactor as a separate
       commit. Snapshot `render_tree`'s output tree (paths +
       bytes) over the full `core` + `people` primitive
       fixture BEFORE the refactor; run the same render AFTER;
       assert byte-equal output. This is the equivalence pin
       that protects the install-pipeline callers (`wiki init`,
       `wiki add`, `wiki upgrade`) from a silent regression in
       what gets rendered when. The pre-refactor snapshot can
       be captured as a fixture file checked into
       `tests/fixtures/render-tree-snapshots/` for replay.
   - **Verify:** `pytest tests/unit/test_install_enumerate.py`
     green.
1. **`adopt.compute_adoption_set` builds the adoption set from a
   recipe closure and on-disk state.**
   - **Tests** (new file `tests/unit/test_adopt.py`):
     - `test_compute_adoption_set_empty_target_returns_empty` —
       fresh tmp dir; assert
       `AdoptionSet(host_adoptions=(), pre_existing_sidecars=())`.
     - `test_compute_adoption_set_kit_owned_file_present_is_adopted`
       — pre-place `wiki/people/.gitkeep` (assuming `people`
       owns that path); assert one `HostAdoption(path=...,
       hash=<file's bytes>, regions=())` in `host_adoptions`.
     - `test_compute_adoption_set_user_territory_file_is_skipped`
       — pre-place `notes/personal.md` (no recipe owns
       `notes/`); assert NOT in `host_adoptions` AND not in
       `pre_existing_sidecars`.
     - `test_compute_adoption_set_user_file_in_kit_dir_is_skipped`
       — pre-place `wiki/people/uncle-bob.md` (people owns
       `wiki/people/` but not this specific file); assert NOT
       in `host_adoptions`.
     - `test_compute_adoption_set_managed_region_host_emits_region_adopts`
       — pre-place `frontmatter.schema.yaml` with two parseable
       managed regions; assert one
       `HostAdoption(path="frontmatter.schema.yaml", ...,
       regions=(AdoptedRegion("types", ...),
       AdoptedRegion("fields", ...)))` with regions sorted by
       `region`.
     - `test_compute_adoption_set_malformed_region_markers_raises`
       — pre-place a host file with unbalanced markers; assert
       `WikiError` with text containing `cannot adopt
       managed-region host` and `markers do not parse`. Pins
       AC9 at the unit level.
     - `test_compute_adoption_set_missing_required_region_raises`
       — pre-place a parseable host file declaring only
       region `types` while the primitive closure includes a
       contributor to region `fields` on the same file; assert
       `WikiError` with text `missing markers for region
       'fields' the recipe needs`. Pins AC9b at the unit
       level.
     - `test_compute_adoption_set_kit_owned_sidecar_listed`
       — pre-place `wiki/people/.gitkeep.proposed`; assert
       it's in `pre_existing_sidecars`, NOT in
       `host_adoptions`.
     - `test_compute_adoption_set_user_territory_sidecar_ignored`
       — pre-place `notes/personal.md.proposed` at a path
       OUTSIDE `enumerate_rendered_paths(...)`; assert
       `pre_existing_sidecars` is empty (no warning would
       fire). Pins the Concern-6 scoping.
     - `test_compute_adoption_set_host_adoptions_sorted_by_path`
       — feed three pre-existing kit-owned files in non-sorted
       creation order; assert `host_adoptions` is sorted by
       `.path`. Pins AC6's outer ordering at the unit level.
     - `test_compute_adoption_set_regions_sorted_within_each_host`
       — pre-place two host files each with two regions in
       random declaration order; assert each
       `HostAdoption.regions` is sorted by `.region`. Pins
       AC6's inner ordering.
     - `test_compute_adoption_set_symlink_escape_raises` —
       pre-place a symlink whose target resolves outside the
       vault root; assert `WikiError` ("path ... resolves to
       ... which is not inside the vault rooted at ...").
       Pins AC19 at the unit level.
   - Implement `llm_wiki_kit/adopt.py`:
     - `@dataclass(frozen=True) class AdoptedRegion`,
       `HostAdoption`, `AdoptionSet`.
     - `compute_adoption_set(vault_root, primitives, sources)`:
       1. `rendered = enumerate_rendered_paths(primitives, sources)`.
       2. Build `required_regions: dict[str, set[str]]` from
          every primitive's `contributes_to` — keyed on host
          `file`, valued by the set of region ids the
          aggregator will write.
       3. `host_adoptions: list[HostAdoption] = []`.
       4. For each `relative_path` in `sorted(rendered)`:
          a. `abs_path = vault_root / relative_path`.
          b. If not `abs_path.exists()` or not
             `abs_path.is_file()`: skip.
          c. Resolve via
             `_relative_to_vault(abs_path, vault_root)` (imported
             from `write_helper`). On `WikiError`, propagate
             (symlink escape).
          d. `hash = sha256(abs_path.read_bytes()).hexdigest()`.
          e. If `relative_path in required_regions`:
             i. Try `regions =
                managed_regions.parse(abs_path.read_text(
                encoding="utf-8"))`. On `ManagedRegionError`:
                raise `WikiError(f"cannot adopt managed-region
                host '{relative_path}': markers do not parse
                ({exc})")`.
             ii. For each `region_id` in
                 `required_regions[relative_path]`: if
                 `region_id not in regions`, raise
                 `WikiError(f"cannot adopt managed-region
                 host '{relative_path}': missing markers for
                 region '{region_id}' the recipe needs")`.
             iii. Build `adopted_regions: list[AdoptedRegion]
                  = [AdoptedRegion(region=r,
                  content_hash=sha256(
                  managed_regions.canonical_region_body(
                  regions[r])).hexdigest()) for r in
                  sorted(required_regions[relative_path])]`.
          f. Else: `adopted_regions = []`.
          g. Append `HostAdoption(path=relative_path, hash=hash,
             regions=tuple(adopted_regions))`.
       5. Compute `pre_existing_sidecars`: walk every
          `relative_path in rendered`, check if
          `vault_root / (relative_path + ".proposed")` exists;
          collect those that do, in `sorted` order. Scoped to
          the rendered closure per Concern-6.
       6. Return `AdoptionSet(host_adoptions=tuple(
          host_adoptions),
          pre_existing_sidecars=tuple(pre_existing_sidecars))`.
   - **Note on `_relative_to_vault`:** the helper is currently
     module-private to `write_helper.py`. `adopt.py` imports it
     directly (`from llm_wiki_kit.write_helper import
     _relative_to_vault`); no new module boundary. Spec
     §Constraints "No new module boundary except
     `llm_wiki_kit/adopt.py`" holds. If a future spec needs
     `_relative_to_vault` in a third call site (currently only
     `write_helper` and `adopt`), revisit the lift then.
   - **Verify:** `pytest tests/unit/test_adopt.py` green.
1. **`_cmd_init` rejects `--adopt` over an already-installed
   vault but accepts an init-in-progress journal.**
   - **Tests** (new file `tests/integration/test_wiki_init_adopt.py`,
     reusing the `kit_root` fixture pattern from
     `tests/integration/test_wiki_add.py`):
     - `test_wiki_init_adopt_refuses_when_primitive_install_event_present`
       (AC4) — pre-init a vault (the install pipeline emits
       `PrimitiveInstallEvent`s); re-run `wiki init <same
       path> --recipe <r> --adopt`; assert exit 2, stderr
       contains `target is already a wiki vault`, the
       existing `.wiki.journal/journal.jsonl` is byte-
       identical to its pre-call content.
     - `test_wiki_init_adopt_resumes_when_journal_has_only_init_and_adopt_events`
       (AC4b) — hand-seed `.wiki.journal/journal.jsonl` with
       `VaultInitEvent` + one `PageAdoptedEvent(path=p,
       hash=h)` (no `PrimitiveInstallEvent`); pre-place the
       adopted file at path `p` with bytes hashing to `h` on
       disk; run `wiki init <same path> --recipe <r> --adopt`.
       Assert:
       (a) exit 0;
       (b) the journal slice between the seeded events and
       the first new `PrimitiveInstallEvent` contains a FRESH
       `PageAdoptedEvent(path=p, hash=h)` — verifying re-emit,
       NOT a skip-if-already-adopted optimisation that would
       diverge from the spec's idempotent-replay claim;
       (c) `PrimitiveInstallEvent`s appear after the fresh
       adopt event, completing the install.
       Pins the init-in-progress recovery from §Edge cases.
   - Implement: in `_cmd_init`, add a "has `PrimitiveInstallEvent`?"
     check (reading the journal with `read_events` if the
     journal file exists) BEFORE the empty-dir check. Refuse
     only when at least one such event is present; otherwise
     proceed (the adopt-phase re-emission is idempotent on
     replay because latest-wins).
   - **Verify:** both tests green; existing init/add tests
     stay green.
1. **`_cmd_init` retains the non-`--adopt` empty-dir refusal.**
   - **Tests:**
     - `test_wiki_init_without_adopt_refuses_non_empty` (AC5) —
       pre-place a file in target; run `wiki init <target>
       --recipe <r>` (no `--adopt`); assert exit 2 with `target
       directory is not empty`.
   - Implementation: keep the existing empty-dir check inside an
     `if not args.adopt:` guard.
   - **Verify:** the existing test stays green; the new test
     pins the no-`--adopt` branch.
1. **`_cmd_init --adopt` over an empty target collapses to a
   normal init (AC1).**
   - **Tests:**
     - `test_wiki_init_adopt_empty_target_matches_plain_init` —
       run `wiki init <tmp> --recipe <r> --adopt`; record the
       journal bytes; run `wiki init <tmp2> --recipe <r>` (no
       `--adopt`); record the journal bytes; assert the byte
       sequences are equal modulo timestamps (use a normalising
       comparator that strips `timestamp` fields).
     - Assert stdout does NOT contain `wiki init: adopted` for
       the `--adopt` empty-target run.
   - Implementation: `compute_adoption_set` returns an empty
     `AdoptionSet`; the adoption-phase event loop is a no-op;
     the install pipeline runs identically to today.
   - **Verify:** test green.
1. **`_cmd_init --adopt` over a target with byte-identical
   kit-owned files (AC2).**
   - **Tests:**
     - `test_wiki_init_adopt_byte_identical_files_no_sidecars` —
       pre-render the core seed manually into a tmp dir (use
       the same `render_tree` the kit uses) so the target's
       on-disk content is byte-identical to the kit's would-
       render. Run `wiki init <target> --recipe core --adopt`.
       Assert:
       (a) one `PageAdoptedEvent` per pre-existing file with
       `by == "wiki-init-adopt"` and `hash == sha256(file_bytes)`;
       (b) zero `PageProposalEvent`s;
       (c) original files are byte-identical to their pre-call
       content (inode preserved for the per-file fast-path);
       (d) stdout contains `wiki init: adopted N files.` (or
       `file.` for `N == 1`).
   - **Verify:** test green.
1. **`_cmd_init --adopt` over a target with byte-differing
   kit-owned files (AC3).**
   - **Tests:**
     - `test_wiki_init_adopt_differing_files_produce_sidecars`
       — pre-place `wiki/people/.gitkeep` (or analogous kit-
       owned file) with bytes the kit's render would NOT
       produce (e.g., empty file vs. the kit's seed content).
       Run `wiki init --adopt`. Assert:
       (a) one `PageAdoptedEvent(hash=h_user)` for the path;
       (b) one `PageProposalEvent(path, proposed_path)` with
       `proposed_path == path + ".proposed"`;
       (c) the `.proposed` sidecar exists with the kit's
       would-render content;
       (d) the original file is byte-identical to its pre-call
       content;
       (e) stdout contains the drift line `Wrote
       wiki/people/.gitkeep.proposed (drift detected on
       wiki/people/.gitkeep); run the wiki-conflict skill to
       merge.`
       (f) stdout contains `wiki init: adopted 1 file.`
   - **Verify:** test green.
1. **`_cmd_init --adopt` against a managed-region host file
   (AC8, AC9).**
   - **Tests:**
     - `test_wiki_init_adopt_emits_managed_region_adopted_for_parseable_host`
       — pre-place `frontmatter.schema.yaml` with two managed
       regions containing user content. Run `wiki init
       --adopt`. Assert:
       (a) one `PageAdoptedEvent` for `frontmatter.schema.yaml`;
       (b) two `ManagedRegionAdoptedEvent`s with `content_hash`
       matching `sha256(managed_regions.canonical_region_body(
       <body>))` for each region.
     - `test_wiki_init_adopt_malformed_host_refuses_with_wiki_error`
       (AC9) — pre-place a host file with unbalanced markers.
       Run `wiki init --adopt`. Assert exit 2, stderr contains
       `cannot adopt managed-region host` and `markers do not
       parse`, AND the journal contains zero adoption events
       (the `target.mkdir` may or may not have run, but no
       `.wiki.journal/journal.jsonl` is present, or, if it is,
       it is empty). Integration twin of
       `test_compute_adoption_set_malformed_region_markers_raises`.
     - `test_wiki_init_adopt_missing_required_region_refuses`
       (AC9b) — pre-place a parseable host file declaring only
       region `types` while the recipe includes a primitive
       contributing to region `fields` on the same host. Run
       `wiki init --adopt`. Assert exit 2, stderr contains
       `missing markers for region 'fields' the recipe needs`,
       and the journal contains zero adoption events.
1. **Pre-existing sidecars in the target are reported, not
   adopted (AC10).**
   - **Tests:**
     - `test_wiki_init_adopt_pre_existing_sidecar_surfaced_not_adopted`
       — pre-place `wiki/people/.gitkeep.proposed` alongside
       `wiki/people/.gitkeep`. Run `wiki init --adopt`. Assert:
       (a) one `PageAdoptedEvent` for `.gitkeep`;
       (b) zero adoption events for `.gitkeep.proposed`;
       (c) **stderr** contains the literal `wiki init: found 1
       pre-existing kit-owned .proposed sidecar.` line, AND
       **stdout** does NOT contain that text. Pins the
       stderr-only framing per spec §Outputs Stderr.
     - `test_wiki_init_adopt_user_territory_sidecar_no_warning`
       — pre-place `notes/personal.md.proposed` at a path
       OUTSIDE the rendered closure. Run `wiki init --adopt`.
       Assert stderr does NOT contain `pre-existing kit-owned
       .proposed sidecar` (the sidecar is ignored entirely).
1. **User-territory files in and out of kit dirs (AC11, AC12).**
   - **Tests:**
     - `test_wiki_init_adopt_user_file_outside_kit_dirs_invisible`
       — pre-place `notes/personal.md`. Run `wiki init --adopt
       --recipe core`. Assert no adoption event for
       `notes/personal.md`, and `run_doctor(vault_root,
       kit_root)` returns no `Issue` for the path.
     - `test_wiki_init_adopt_user_file_in_kit_dir_surfaces_as_orphan`
       — assume `core` owns `wiki/` (or, more realistically,
       a primitive whose closure includes `wiki/people/`).
       Pre-place `wiki/people/uncle-bob.md` and run `wiki
       init --adopt --recipe family`. Assert no adoption event
       for `uncle-bob.md` AND `run_doctor` returns
       `Issue(ORPHAN, "wiki/people/uncle-bob.md")`.
1. **TOCTOU between adoption walk and render-phase produces
   `.proposed`, not silent overwrite (AC20a).**
   - **Tests:**
     - `test_wiki_init_adopt_toctou_between_walk_and_render_proposes`
       — pre-place a kit-owned file with bytes matching the
       kit's would-render (C₁). Wrap `adopt.compute_adoption_set`
       with a one-shot side-effect that, after returning the
       `AdoptionSet`, atomically `os.replace`s the file with
       C₂. (The wrap site is module-level — `adopt.compute_adoption_set`
       is the function under contract — NOT a monkeypatch on
       `Path.read_bytes` or any internal helper inside
       `safe_write`. A future refactor that switches
       `safe_write` to `hashlib.file_digest` or chunked reads
       leaves this test green.) Run `wiki init --adopt`.
       Assert (a) on-disk bytes equal C₂; (b) a
       `<path>.proposed` exists with C₁; (c) journal contains
       `PageAdoptedEvent(hash=h(C₁))` and a subsequent
       `PageProposalEvent(path, proposed_path, hash=h(C₁))`.
       Pins the TOCTOU residual the spec §Edge cases names
       and AC20a contracts.
1. **Crash-during-install recovers via `wiki upgrade` (spec
   §Edge cases "Crash inside the install pipeline").**
   - **Tests:**
     - `test_wiki_init_adopt_crash_during_install_recovers_via_upgrade`
       — drive `wiki init --adopt` to crash partway through
       the install pipeline by monkeypatching
       `install.install_primitives` to raise `RuntimeError`
       after appending the first `PrimitiveInstallEvent` for
       a primitive whose render hasn't started yet. Catch
       the propagated exception. Then run `wiki upgrade` in
       the same vault. Assert (a) `wiki upgrade` completes
       without error; (b) byte-identical pre-existing files
       remain byte-identical (the adopt-match no-rewrite
       branch fires for them); (c) byte-differing pre-
       existing files surface as `.proposed` sidecars;
       (d) `wiki doctor` post-upgrade reports only
       `pending-proposal` (per AC17), not `missing` /
       `page-drift`.
1. **Ordering invariant: adoption events strictly before
   install-pipeline events (AC18).**
   - **Tests:**
     - `test_wiki_init_adopt_event_ordering_adopt_before_install`
       — run `wiki init --adopt` over a non-empty target;
       compute the journal slice from start; assert the slice
       has shape `[VaultInitEvent, *PageAdoptedEvents,
       *ManagedRegionAdoptedEvents, *PrimitiveInstallEvents,
       ...]`. The first non-adoption, non-init event must come
       AFTER every adoption event.
1. **`by` attribution discipline (AC7).**
   - **Tests:**
     - `test_wiki_init_adopt_by_attribution` — run `wiki init
       --adopt`; for each event in the new journal, assert the
       expected `by` per the spec's Invariant 6 table:
       `VaultInitEvent.by == "wiki-init"`,
       `PageAdoptedEvent.by == "wiki-init-adopt"`,
       `ManagedRegionAdoptedEvent.by == "wiki-init-adopt"`,
       `PrimitiveInstallEvent.by == "wiki-init"`,
       per-primitive `PageWriteEvent.by == <primitive.name>`,
       aggregator-emitted `ManagedRegionWriteEvent.by ==
       "wiki-init"`.
1. **Sticky-adopt clears on resolve (AC16 end-to-end).**
   - **Tests:**
     - `test_wiki_init_adopt_then_resolve_clears_sticky` —
       run `wiki init --adopt` producing one `.proposed`
       sidecar; call `write_helper.resolve_proposal(path,
       merged_content, by="wiki-conflict", journal_path)`;
       run a second `safe_write` against the path with the
       same `merged_content`; assert direct-write (no new
       proposal).
   - Pins the integration between the adopt-aware predicate
     (PR-B) and the adopt-phase journal (PR-C) and the resolve
     bypass.
1. **Symlink escape during adoption raises and leaves the
   journal empty (AC19).**
   - **Tests:**
     - `test_wiki_init_adopt_symlink_escape_raises_and_does_not_journal`
       — pre-place a kit-owned symlink whose target resolves
       outside the vault. Run `wiki init --adopt`. Assert
       `WikiError`, exit 2,
       `.wiki.journal/journal.jsonl` does NOT contain any
       `PageAdoptedEvent` for the offending path (it may or
       may not contain a `VaultInitEvent` depending on when in
       `_cmd_init`'s flow the symlink walk happens — pin the
       flow so the symlink check runs BEFORE
       `append_event(VaultInitEvent)`, so the journal stays
       absent. Implementation: call `compute_adoption_set`
       before the journal cache scope opens; propagate any
       `WikiError` to the boundary).
1. **`wiki doctor` after `wiki init --adopt` reports the
   expected issue set (AC17).**
   - **Tests:**
     - `test_wiki_init_adopt_doctor_clean_for_byte_identical_targets`
       — after AC2's scenario, run `run_doctor`; assert no
       issues at all (zero orphans, zero pending-proposals,
       zero missing, zero drift).
     - `test_wiki_init_adopt_doctor_reports_pending_proposals_for_differing_targets`
       — after AC3's scenario, run `run_doctor`; assert one
       `pending-proposal` for the conflicting path; zero
       orphans for kit-owned territory; zero drift; zero
       missing.
     - `test_wiki_init_adopt_doctor_reports_orphan_for_user_file_in_kit_dir`
       — after AC11's scenario, assert `Issue(ORPHAN,
       "wiki/people/uncle-bob.md")` and nothing else.
1. **`_cmd_init` final pipeline.**
   - Replace the body of `_cmd_init`:
     1. `target = Path(args.path).resolve()`.
     2. If `target.exists() and target.is_file()`: raise existing
        error.
     3. `journal_path = target / ".wiki.journal" / "journal.jsonl"`.
        If `journal_path.is_file()`: read events via `read_events(
        journal_path)`; if any event is a `PrimitiveInstallEvent`,
        raise the new already-a-vault error. Otherwise (init-in-
        progress journal: `VaultInitEvent` and/or adoption events
        only) PROCEED — the adopt-phase re-emission is idempotent
        on replay. Spec AC4 / AC4b.
     4. If `not args.adopt`: existing empty-dir refusal.
     5. Load recipe, catalog, ordered closure, sources,
        pre-flight `validate_contributions` (existing logic,
        unchanged).
     6. If `args.adopt and target.exists()`:
        `adopt_set = adopt.compute_adoption_set(target, ordered,
        sources)`. Called OUTSIDE the journal-cache scope so a
        symlink-escape or malformed-host refusal does not create
        a half-init journal. Otherwise
        `adopt_set = AdoptionSet(host_adoptions=(),
        pre_existing_sidecars=())`.
     7. `target.mkdir(parents=True, exist_ok=True)`.
     8. `with journal.use_journal_cache(journal_path):`
        i. `append_event(journal_path, VaultInitEvent(
           timestamp=now, by=INSTALL_VEHICLE_INIT, vault_name,
           recipe=recipe.name))`.
        ii. For each `HostAdoption` in
            `adopt_set.host_adoptions`:
            a. `append_event(journal_path, PageAdoptedEvent(
               timestamp=now,
               by=adopt.INSTALL_VEHICLE_ADOPT,
               path=host.path, hash=host.hash))`.
            b. For each `region` in `host.regions`:
               `append_event(journal_path,
               ManagedRegionAdoptedEvent(timestamp=now,
               by=adopt.INSTALL_VEHICLE_ADOPT,
               file=host.path, region=region.region,
               content_hash=region.content_hash))`.
            The page → its regions → next host interleave keeps
            any crash prefix consistent (spec §Outputs Journal
            events bullet 2).
        iii. `install_primitives(to_install=ordered,
             all_installed=ordered, sources=sources,
             journal_path=journal_path, context=context,
             install_vehicle=INSTALL_VEHICLE_INIT, now=now)`.
     9. Collect proposals: walk the journal slice from
        `length_before` and filter for `PageProposalEvent`.
     10. For each proposal, print the drift line on stdout
         (matching `_cmd_upgrade`'s line shape).
     11. If `adopt_set.host_adoptions` is non-empty: print the
         count-aware `wiki init: adopted N file(s).` line on
         stdout.
     12. If `adopt_set.pre_existing_sidecars` is non-empty: print
         the count-aware `wiki init: found N pre-existing kit-
         owned .proposed sidecar(s).` line on **stderr only**
         (matching spec §Outputs Stderr's stderr-only framing).
     13. Return 0.
   - The vehicle constant `INSTALL_VEHICLE_ADOPT =
     "wiki-init-adopt"` lives in `adopt.py` (mirrors
     `upgrade.UPGRADE_VEHICLE`'s single-source-of-truth
     pattern); `cli.py` imports it.
   - **Verify:** the full integration suite passes:
     `pytest tests/integration/test_wiki_init_adopt.py`.
1. **`build_parser` wires the `--adopt` flag.**
   - Add `init_parser.add_argument("--adopt",
     action="store_true", help="Adopt pre-existing files in the
     target as kit baselines. See docs/specs/wiki-init-adopt.")`.
   - Update the `_cmd_init` docstring to point at ADR-0008 and
     the new spec, replacing the in-line deferral breadcrumb at
     lines 259–266.
   - **Verify:** `wiki init --help` includes the `--adopt` flag
     (smoke test via subprocess) — covered by an existing CLI
     help test that walks every subparser's args (or add a new
     `test_wiki_init_help_lists_adopt` if no such test exists).
1. **Doc sweep.**
   - `docs/rfc/0001-v2-architecture.md` §"Unresolved questions"
     (lines 444–451): replace the `--adopt` paragraph with
     "Resolved by ADR-0008 and `docs/specs/wiki-init-adopt/`;
     awaiting implementation." Same for line 212's Task 10
     parenthetical and the lines 273–285 ROADMAP-acceptance
     paragraph (drop the "needs its own spec" wording).
   - `docs/ROADMAP.md` "Deferred from v2.0" entry: replace
     "Needs its own spec before any task picks it up" with
     "Spec landed at `docs/specs/wiki-init-adopt/`; awaiting
     implementation (three PRs per the plan)."
   - `docs/adr/0004-drift-detection-and-proposal-flow.md` lines
     144–147: amend the `--adopt` bullet to point at ADR-0008
     for the pinned semantics (NOT a §Revisions entry — the
     §Negative bullet's wording stays correct; just append a
     pointer).
   - `docs/specs/safe-write-ordering/spec.md` line 782 "Not a
     `wiki init --adopt` flag" Non-goal: add a one-line "Now
     covered by `docs/specs/wiki-init-adopt/spec.md`." pointer.
     Mark the deferral closed.
   - `llm_wiki_kit/write_helper.py:155` FUTURE comment: replace
     with a one-liner "Decision pinned by ADR-0008 §Decision
     sub-choice 4: rejected `reason` field in favor of
     `PageAdoptedEvent` class; see
     `docs/specs/wiki-init-adopt/spec.md`." Don't delete the
     comment; the breadcrumb is load-bearing for grep.
   - `CHANGELOG.md` `[Unreleased]` section: add a
     `### Added` entry "`wiki init --adopt` — adopt an
     existing folder as a vault (`docs/specs/wiki-init-adopt/`;
     ADR-0008)."
   - **Verify:** `git grep -n "wiki init --adopt.*defer\|--adopt
     flag itself was\|needs its own spec" docs llm_wiki_kit`
     returns no live admonitions for the `--adopt` deferral.
1. **Patterns capture.**
   - Append one entry to `docs/knowledge/patterns.jsonl` scoped
     to `llm_wiki_kit/{write_helper,adopt}.py` capturing the
     decision tree: "adopted baseline is sticky until a
     `PageWriteEvent` supersedes it; do not silently overwrite
     a `PageAdoptedEvent` baseline with differing kit content
     (route to proposal instead) even if `on_disk_hash ==
     baseline_hash`." Keep the entry under 250 chars.
   - **Verify:** `python -m json.tool < docs/knowledge/patterns.jsonl`
     parses every line as a valid JSON object; `id` is unique.

## Verification gate

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

All four green across all three PRs (A, B, C). The integration
suite at `tests/integration/test_wiki_init_adopt.py` is green in
particular. Smoke: after a representative
`wiki init <fixture> --recipe family --adopt` run on a fixture
target containing mixed kit-owned and user-territory content,
`wiki doctor` reports zero `missing` / `page-drift` /
`managed-region-drift` issues; only `pending-proposal` (for the
differing-bytes files) and `orphan` (for user-files-in-kit-dirs)
issues, consistent with AC17.

## Risks

- **The adopt-aware predicate adds one new branch to
  `safe_write` and `safe_write_region` — the kit's most
  load-bearing functions.** Mitigation: PR-B's TDD construction
  tests pin both branches AND the resolve-clears-sticky
  contract; the adversarial-reviewer pass should focus on the
  predicate before approving PR-B.
- **`enumerate_rendered_paths` and the renderer must agree on
  the path set.** If `render_tree` skips a file (e.g., a
  template that produces empty output) and
  `enumerate_rendered_paths` lists it, an adopt event lands for
  a path the render never writes — surfacing as `missing` in
  `wiki doctor`. Mitigation: a construction test
  `test_enumerate_matches_render_tree_output` walks both
  functions over a fixture primitive and asserts the path sets
  match.
- **`replay_state` with the new fields touches a hot path.**
  Replay over 1000+ events is in the load-bearing performance
  envelope (ADR-0002 §Negative names <100ms as the budget).
  Two new dict-update branches per event are sub-microsecond;
  no realistic regression. The existing performance test
  (`test_replay_state_under_100ms_over_1000_events` if it
  exists; add it if not) is the gate.
- **The `_relative_to_vault` lift could destabilise existing
  callers.** Mitigation: keep the original symbol in
  `write_helper` as a re-export (`from llm_wiki_kit.paths
  import _relative_to_vault as _relative_to_vault`) so callers
  importing from `write_helper` keep working. The PR-C
  construction test asserts the re-export path.
- **A symlink-rich target (e.g., a vault inside a `git
  worktree` with submodules) could surface unexpected
  `WikiError`s during adoption.** Mitigation: AC19 plus the
  spec's §Edge cases bullet name the behavior; the error
  message points at the resolved target so the user can
  remediate. A future spec can introduce a `--ignore-symlinks`
  flag if real users hit this; out of scope here.
- **Pre-existing `.proposed` sidecars from another tool (some
  user has a workflow that produces files ending in
  `.proposed`) would be misclassified.** Mitigation: the
  adoption-phase docstring + ADR-0008 explicitly name
  `.proposed` as a kit-owned suffix; users with conflicting
  workflows should rename their files before `--adopt`. Same
  trade-off the kit makes elsewhere (`.obsidianignore` is
  another kit-claimed name).

## Out of scope

- `wiki init --retry-adopt` (Non-goal; `wiki upgrade` covers
  partial-adopt recovery).
- `wiki upgrade --adopt` (the adopt-aware `safe_write` predicate
  applies transparently to upgrade-time writes; no new flag is
  needed and out of scope for this spec).
- `wiki adopt` standalone verb (Non-goal; the flag-on-init
  shape is the smallest surface).
- Interactive adopt-then-resolve UI (Non-goal; the existing
  `wiki-conflict` skill is the merge surface).
- Performance optimisation for 10k+ file adoption (Non-goal;
  revisit if real usage demands it).
- Backward-compat schema migration for pre-ADR-0008 vaults
  (additive-only; no migration needed).
- Suppressing region-level adoption via a CLI flag (ADR-0008
  §Neutral leaves this as future work).
- Vault-side `wiki-init-adopt` SKILL.md (the flag is a kit-side
  CLI surface; vault-side workflow during adoption uses the
  existing `wiki-conflict` skill for any resulting sidecars).
