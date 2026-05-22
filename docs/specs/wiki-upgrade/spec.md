# Spec: wiki-upgrade

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit/upgrade.py`, `llm_wiki_kit/cli.py:_cmd_upgrade`
- **Related:** RFC-0001 §"CLI surface (target)" line 134 +
  §"What changes vs. v1" line 151 (`Bash sync scripts → pip install
  llm-wiki-kit; wiki upgrade`); RFC-0001 §"Phase F" Task 23;
  ADR-0002 (journal as state truth); ADR-0003 (managed regions);
  ADR-0004 (drift detection + proposal flow);
  ADR-0006 (additive managed-region contributions);
  `docs/specs/safe-write-ordering/spec.md` (event-before-disk);
  `docs/specs/wiki-upgrade/plan.md`.
- **Constrained by:** RFC-0001 §"Runtime constraints" (no new runtime
  deps without a new ADR), AGENTS.md §"Check before acting" (every
  vault write through `safe_write`; tests use `tmp_path` or fixture
  vaults), ADR-0004 (no silent overwrites on hash drift).

## What this is

`wiki upgrade [--primitive <name>]` re-applies installed primitives
against the running kit's catalog so a vault initialized at one kit
version catches up to a newer one. Every installed primitive whose
`installed_version != catalog_version` is re-rendered (its `files/`
tree through `safe_write`, its region contributions through the same
aggregator `wiki add` uses) and journaled as a `PrimitiveUpgradeEvent`.
Primitives whose installed version already matches the catalog are
silent no-ops. `--primitive <name>` restricts the operation to one
installed primitive; the default scope is every installed primitive.

`wiki upgrade` is the v2 replacement for the v1 `scripts/sync-shared.sh`
flow (RFC-0001 §"What changes vs. v1"). It is not a kit installer
(that's `pipx install` / `pip install`), and it is not a primitive
installer (that's `wiki add`).

## Inputs

CLI invocation: `wiki upgrade [--primitive <name>]`.

- `--primitive <name>` — optional. The primitive name (bare name, not
  `<kind>:<name>` — the kind is implied by what is already installed,
  and the journaled set has unique names). When absent, the operation
  runs over every installed primitive.
- Vault root: `Path.cwd().resolve()`. Must contain
  `.wiki.journal/journal.jsonl` and the journal must include a
  `vault.init` event (same boundary checks `wiki add` uses).
- Kit catalog: resolved via `_kit_paths(args.kit_root)` per the
  existing CLI wiring; `core/` plus every primitive under
  `templates/<kind>/<name>/`.
- Recipe: the on-disk recipe named by `state.recipe` is loaded for its
  `variables` (render-context defaults). A missing recipe file is a
  hard error, matching `_cmd_add`'s behavior.

## Outputs

- **Journal events.** For each upgraded primitive, exactly one
  `PrimitiveUpgradeEvent(from_version, to_version, primitive,
  by="wiki-upgrade")` is appended *before* any `safe_write` for that
  primitive's `files/` tree (event-before-disk per the safe-write-
  ordering spec). Each rendered file appends one `PageWriteEvent`
  (direct write, including the no-op idempotent re-write per
  `test_no_op_write_of_identical_content_still_records_event`) or one
  `PageProposalEvent` (drift). After per-primitive renders, the
  managed-region aggregator runs over every installed primitive (the
  same set `wiki add` uses) and appends one `ManagedRegionWriteEvent`
  per `(file, region)` bucket on the no-drift path (existing
  contract — `safe_write_region` emits an event even on hash-match
  per `llm_wiki_kit/write_helper.py:safe_write_region`), or one
  `PageProposalEvent` for a region-drifted file. All region writes
  during this pass are attributed `by="wiki-upgrade"`.
- **Stdout.** A one-line summary per primitive that produced a
  `PrimitiveUpgradeEvent` (`upgraded <name> <from> → <to>`), then
  one drift-line per `.proposed` sidecar produced during the run
  (`Wrote <path>.proposed (drift detected on <path>); run the
  wiki-conflict skill to merge.`) — matching the line `_cmd_research`
  already emits. Sidecars from *both* `render_tree` (per-primitive
  page drifts) *and* `aggregate_region_contributions` (region-host
  file drifts on shared files like `frontmatter.schema.yaml`) are
  surfaced. Finally:
  - When at least one primitive was upgraded: `wiki upgrade:
    upgraded N primitive.` for `N == 1` and `wiki upgrade: upgraded
    N primitives.` otherwise. (Spec uses count-aware pluralisation
    rather than the literal `primitive(s)` shorthand.)
  - When no primitive was upgraded and no `--primitive` was given:
    `wiki upgrade: nothing to upgrade.` (no per-primitive lines, no
    totals row).
  - When `--primitive <name>` was given and the catalog already
    matches the installed version: `wiki upgrade: primitive
    '<name>' is already at version <version>.` (no per-primitive
    lines, no totals row).
- **Stderr.** Boundary errors only (`not a wiki vault`, `--primitive
  ... not installed`, `--primitive ... not in the kit catalog`,
  `--primitive must be a bare primitive name, not <kind>:<name>`,
  recipe missing). One additional informational hint when at least
  one installed primitive is silently skipped because it's no longer
  in the catalog: `note: <N> installed primitive no longer in the
  kit catalog; run `wiki doctor` for details.` for `N == 1` (and
  `primitives` plural for `N > 1`). Same shape as the
  other write-side handlers.
- **No new event type.** `PrimitiveUpgradeEvent` already exists in
  `models.py:233` and carries `from_version` + `to_version`; no new
  field is required. `PageWriteEvent`, `PageProposalEvent`, and
  `ManagedRegionWriteEvent` cover the file writes that the install
  pipeline already produces.

## Behavior

### Happy path — no version changes

1. Vault root + journal boundary check (same as `wiki add`).
2. Reject `args.primitive` values containing `:` with `WikiError(
   "--primitive must be a bare primitive name, not <kind>:<name>")`
   before any state read. `wiki add` is the kind-aware command;
   `wiki upgrade`'s installed set already keys on bare names.
3. `state = replay_state(read_events(journal_path))`; reject when
   `state.recipe is None` or `state.vault_name is None`.
4. Load the kit catalog (`core` + `discover_primitives(templates_dir)`).
5. `plan = plan_upgrade(state, catalog, only=args.primitive)`.
   With no `--primitive` and every installed primitive already at the
   catalog version, `plan.to_upgrade` is empty.
6. **CLI short-circuit.** When `plan.to_upgrade` is empty, print one
   of the no-op messages from §Outputs and return 0 *without entering
   the runner*. No journal events are appended, no files are touched.
   This short-circuit — not aggregator idempotency — is what makes
   re-running `wiki upgrade` zero-event (the aggregator's
   `safe_write_region` call would otherwise append a
   `ManagedRegionWriteEvent` per `(file, region)` bucket even on
   hash-match).

### Happy path — one primitive bumped in the catalog

1. Boundary check, `--primitive` shape check, and `plan_upgrade` as
   above. The kit's catalog ships `core` at version 0.2.0; the vault
   was initialized when `core` was 0.1.0. `plan.to_upgrade == [core]`,
   `plan.all_installed` is the topologically-sorted full installed
   set (filtered to in-catalog primitives), `plan.not_in_catalog ==
   []`.
2. `validate_contributions(primitive, sources[primitive.name])` for
   every primitive in `plan.all_installed`. Pre-flight matches
   `_cmd_add` / `_cmd_init` but is widened from `to_upgrade` to
   `all_installed` because the aggregator (which runs after the
   per-primitive renders) reads every contributing primitive's
   `regions/<file>.<region>` snippets — a kit update can reshape an
   unchanged-version primitive's contribution set, and the
   aggregator-time failure would leave earlier `to_upgrade`
   primitives' upgrade events durable on the journal with the
   aggregator pass never run. Fail-before-writing for *every*
   primitive whose snippets the run will touch.
3. Load the recipe named by `state.recipe` for its `variables`; build
   the render context via `_build_context(recipe, state.vault_name)`.
4. Inside `journal.use_journal_cache(journal_path)`:
   1. For each primitive `p` in `plan.to_upgrade`:
      1. `append_event(journal_path, PrimitiveUpgradeEvent(
         timestamp=now, by="wiki-upgrade", primitive=p.name,
         from_version=state.installed_primitives[p.name],
         to_version=p.version))`.
      2. `render_tree(sources[p.name] / "files", vault_root, context,
         journal_path, by=p.name)` — every file goes through
         `safe_write`. Direct writes appear as `PageWriteEvent`s
         attributed `by=p.name`; drifted files appear as
         `PageProposalEvent`s with sidecars at `<path>.proposed`.
   2. `aggregate_region_contributions(plan.all_installed, sources,
      journal_path, by="wiki-upgrade")` — exactly the same
      aggregator `wiki add` uses, walking every installed primitive
      so contributions from unchanged primitives survive (ADR-0006
      §Mechanics step 5, Task-12 design callout).
5. Print one `upgraded <name> <from> → <to>` per primitive in
   `plan.to_upgrade`. Print one `Wrote <path>.proposed ...` per
   `PageProposalEvent` the renders produced. Print the totals row.
   Return 0.

### Happy path — `--primitive <name>`

Same as above except `plan_upgrade(state, catalog, only=name)` returns
either `to_upgrade == [primitive]` (when the catalog ships a different
version — newer or older) or `to_upgrade == []` (when the catalog and
journal already agree). The empty case sets
`plan.no_op_target = (name, version)` and the CLI short-circuit (step
6 above) prints `wiki upgrade: primitive '<name>' is already at
version <version>.` and returns 0 — louder than the all-primitives
no-op because the user explicitly asked.

### Edge cases

- **`--primitive <name>` not installed.** Raise `WikiError("primitive
  '<name>' is not installed; run `wiki add <kind>:<name>` first")`.
  Exit 2. No journal events, no file writes.
- **`--primitive <name>` is installed but not in the catalog.** Raise
  `WikiError("primitive '<name>' is no longer in the kit catalog; the
  installed kit version does not ship it")`. Exit 2.
- **`--primitive <name>` not installed *and* not in catalog.** Same
  shape as the first case (not-installed wins; the
  `wiki add` call the message recommends will surface the catalog
  miss in turn).
- **No `--primitive`, but an installed primitive is no longer in the
  catalog.** Journal/event-level silent skip: the primitive is
  omitted from `plan.all_installed`, no upgrade event is emitted for
  it, and `wiki doctor`'s existing `primitive-missing` check is the
  surfacing mechanism (`doctor.check_primitive_missing` already
  knows this shape). The CLI also prints a one-line stderr hint
  naming the count and pointing at `wiki doctor` (see §Outputs) so
  the situation is discoverable without running doctor first.
- **Empty journal / vault.init absent.** Raise
  `WikiError("vault at <root> has no vault.init event; the journal
  is incomplete and cannot be upgraded")` — mirrors `_cmd_add`'s
  message so users see one consistent boundary surface.
- **Recipe file missing from the kit.** Raise `WikiError` (the
  `load_recipe` call propagates `RecipeError` which is a `WikiError`
  subclass). The user reinstalls the kit version that ships the
  recipe.
- **`safe_write` drift on a re-rendered file.** Standard ADR-0004
  flow: `<path>.proposed` lands, the original user file is
  untouched, `PageProposalEvent` is journaled, one drift-line goes
  to stdout. The `PrimitiveUpgradeEvent` for the primitive that
  owns the file is already durable on the journal (event-before-
  disk); the upgrade summary still counts it as upgraded.
- **`safe_write_region` drift on an aggregated region.** Same flow,
  scoped to a managed-region file: `<file>.proposed` lands with the
  rewritten file body, `PageProposalEvent` is journaled, one
  drift-line is printed.
- **`validate_contributions` failure on a `to_upgrade` primitive.**
  Raises `PrimitiveError` before any event is appended for that
  primitive. Earlier upgrades in the same `wiki upgrade` invocation
  remain durable; later primitives are not attempted.
- **Kit version older than installed (downgrade).** Treated as an
  upgrade with `from_version > to_version` — the event records the
  observed transition honestly. Re-renders happen the same way; the
  on-disk content is re-baselined to the older kit's bytes via the
  drift-aware `safe_write` path.
- **Idempotent re-run.** Two consecutive `wiki upgrade` invocations
  produce zero new events of any kind on the second run because the
  CLI short-circuits on `plan.to_upgrade == []` and never enters the
  runner (see Happy-path step 6). The runner itself is *not* event-
  idempotent — `safe_write_region` appends a `ManagedRegionWriteEvent`
  per bucket even on hash-match — so the short-circuit is what makes
  AC7 hold.
- **Crash between events and disk writes.** The safe-write-ordering
  spec's recovery contracts apply unchanged: `wiki doctor` reports
  `missing` (event durable, file absent) or `managed-region-drift`
  (region body partial). Re-running `wiki upgrade` is not the
  recovery path — re-running re-runs `plan_upgrade`, which sees the
  upgrade event as durable and considers the primitive already
  upgraded; the user resolves any sidecars via `wiki-conflict` and
  re-runs `wiki doctor`.

### Error cases

- `not a wiki vault: ...` — exit 2 (`WikiError`).
- `--primitive must be a bare primitive name, not <kind>:<name>` —
  exit 2. Triggered when `args.primitive` contains `:`.
- `vault at <root> has no vault.init event; ...` — exit 2.
- `primitive '<name>' is not installed; ...` — exit 2.
- `primitive '<name>' is no longer in the kit catalog; ...` — exit 2.
- `PrimitiveError` from `validate_contributions` propagates as a
  `WikiError` (subclass); exit 2.
- `RecipeError` from `load_recipe` propagates; exit 2.

## Invariants

1. **Every vault write goes through `safe_write` or
   `safe_write_region`.** No new write path is introduced by
   `wiki upgrade`. `render_tree` and `aggregate_region_contributions`
   are the only modules that touch disk inside a vault during
   upgrade; both already route through the safe-write helpers.
2. **Event-before-disk holds.** Every `PrimitiveUpgradeEvent` is
   appended (and `fsync`'d) before the corresponding `render_tree`
   call opens any file. The downstream `PageWriteEvent` /
   `PageProposalEvent` / `ManagedRegionWriteEvent` ordering is
   preserved by `safe_write` / `safe_write_region` itself.
3. **No silent overwrites of user-edited files.** ADR-0004's drift
   contract is unchanged; `wiki upgrade` is one more consumer of
   `safe_write`, with the same proposal-on-mismatch behavior.
4. **Per-primitive idempotency at the planner.** When
   `state.installed_primitives[name] == catalog[name].version`, the
   primitive is absent from `plan.to_upgrade`. The CLI short-circuit
   when `plan.to_upgrade` is empty is what makes a no-op invocation
   journal-clean overall; the runner itself, if entered, would emit
   a `ManagedRegionWriteEvent` per `(file, region)` bucket via
   `safe_write_region`'s no-drift event-append.
5. **Region aggregation runs over the full installed set.** The
   aggregator pass uses `plan.all_installed`, never just
   `plan.to_upgrade`. Otherwise an upgrade of one primitive would
   collapse multi-contributor regions to "this primitive only,"
   reproducing the Task-12 footgun the `wiki add` aggregator already
   avoids.
6. **No new event type.** `PrimitiveUpgradeEvent` (existing) carries
   every field this contract needs. A future "upgrade-with-no-version-
   bump-but-content-changed" mode would warrant a separate spec; it
   is out of scope here.
7. **`by` attribution.** `PrimitiveUpgradeEvent.by == "wiki-upgrade"`.
   `PageWriteEvent` / `PageProposalEvent` keep their renderer-side
   `by=<primitive name>` (matches `wiki add`).
   `ManagedRegionWriteEvent.by == "wiki-upgrade"` (parallels `wiki
   add`'s aggregator pass with `by="wiki-add"` — the install vehicle
   is the author of a composed body, not any single primitive).
8. **Pre-flight validates every contributing primitive, not just
   `to_upgrade`.** `validate_contributions` is run over
   `plan.all_installed` *before* any `PrimitiveUpgradeEvent` is
   appended. This widens `_cmd_init` / `_cmd_add`'s pre-flight
   scope so a kit update that reshapes an unchanged-version
   primitive's contribution set (snippet removed, orphan snippet,
   filename traversal) fails the entire `wiki upgrade` invocation
   before any event lands — closing the partial-failure window
   that would otherwise leave earlier `to_upgrade` primitives'
   upgrade events durable with the aggregator pass never run.
9. **Runtime failure (`safe_write` / `safe_write_region` raising on
   I/O after pre-flight succeeds) is locally durable.** Prior
   primitives in the same `to_upgrade` sequence keep their
   `PrimitiveUpgradeEvent` + page writes; the raising primitive's
   `PrimitiveUpgradeEvent` is durable (event-before-disk) even if
   no page writes from it landed; later primitives are skipped; the
   aggregator pass does not run. `wiki doctor` is the recovery
   diagnostic via `check_missing` / `check_managed_region_drift`.

## Contracts with other modules

- **`llm_wiki_kit.cli`** — `_cmd_upgrade` does the boundary checks,
  calls `plan_upgrade`, runs `validate_contributions` over
  `plan.all_installed` (the widened pre-flight per Invariant 8 —
  every primitive whose `regions/` snippets the aggregator pass
  will read, not just the version-bumped ones), threads the render
  context, and wraps the upgrade pipeline in
  `journal.use_journal_cache` (matching `wiki init` / `wiki add`
  for the qC4 cache contract). The CLI is the
  only module here with stdout side effects.
- **`llm_wiki_kit.upgrade`** — new module. Public surface:
  - `@dataclass UpgradePlan(to_upgrade: list[Primitive],
    all_installed: list[Primitive], not_in_catalog: list[str],
    no_op_target: tuple[str, str] | None)`.
    - `to_upgrade` is sorted in install order (topological by
      `requires:`, alphabetical tiebreaker).
    - `all_installed` is the topologically-sorted installed set
      filtered to primitives currently in the catalog (so the
      aggregator pass never sees a missing-from-catalog primitive).
    - `not_in_catalog` is the list of installed primitive names
      whose name is absent from the catalog (informational; doctor
      is the surfacing mechanism).
    - `no_op_target` is `(name, version)` when `only=<name>` was
      requested but the catalog already matched; `None` otherwise.
      The CLI uses this for its louder no-op message.
  - `plan_upgrade(state: VaultState, catalog: list[Primitive], *,
    only: str | None) -> UpgradePlan` — pure. Raises `WikiError`
    when `only` is set and the named primitive is either not
    installed or not in the catalog.
  - `upgrade_primitives(*, plan: UpgradePlan, sources: Mapping[str,
    Path], journal_path: Path, context: Mapping[str, str],
    state_versions: Mapping[str, str], now: datetime) ->
    list[tuple[str, str]]` — the runner. The return type is the list
    of `(path, proposed_path)` tuples pulled directly from each
    `PageProposalEvent` appended during the run; returning both
    fields (rather than just `proposed_path`) decouples the CLI's
    `Wrote <sidecar> (drift detected on <path>)` rendering from the
    structural assumption that sidecars are always named
    `<path>.proposed`. `state_versions` carries
    `dict(state.installed_primitives)` so the runner can build
    `PrimitiveUpgradeEvent.from_version` without re-replaying state
    or importing `VaultState` (avoids a circular-import shape; keeps
    the runner pure of the journal-derived state model). Calls
    `install._warn_if_install_pipeline_uncached(journal_path)` as
    its first line so a future caller that forgets the
    `journal.use_journal_cache` scope hits the same WARNING that
    `install_primitives` already emits. Appends one
    `PrimitiveUpgradeEvent` + renders each `to_upgrade` primitive,
    then calls `aggregate_region_contributions(plan.all_installed,
    sources, journal_path, by="wiki-upgrade")`. Returns the list of
    `.proposed` sidecar paths (vault-relative POSIX) produced during
    the run by collecting **every** `PageProposalEvent` whose journal
    index is in `[length_before, length_after)` — that is, snapshot
    the journal length on entry, run the renders + aggregator, then
    `read_events(journal_path)[length_before:]` and filter for
    `PageProposalEvent`. This shape catches both per-primitive page
    drifts (from `render_tree` / `safe_write`) AND aggregator-emitted
    region-drift proposals on shared files (from
    `safe_write_region`); narrowing the filter to a primitive-tracked
    path set would silently drop the aggregator's drift surface. The
    redundant disk read is acceptable because it happens once per
    invocation, after the writes.
- **`llm_wiki_kit.install`** — `validate_contributions` and
  `aggregate_region_contributions` are reused unchanged. The
  upgrade runner does *not* call `install_primitives` (which emits
  `PrimitiveInstallEvent`s — wrong shape for the upgrade contract).
- **`llm_wiki_kit.render`** — `render_tree` is reused unchanged.
- **`llm_wiki_kit.write_helper`** — `safe_write` and
  `safe_write_region` are reused unchanged via the renderer and
  aggregator. The new `WriteResult.PROPOSAL` accounting (for the
  drift-summary line) reads the existing return value.
- **`llm_wiki_kit.journal`** — `append_event` and
  `use_journal_cache` are reused unchanged.
- **`llm_wiki_kit.errors.WikiError`** — re-used; no new exception
  type.

## Acceptance criteria

- [ ] **AC1 — `wiki upgrade` against a vault whose installed
  primitives all match the catalog prints `wiki upgrade: nothing to
  upgrade.` and exits 0 with zero new journal events.**
- [ ] **AC2 — `wiki upgrade` after bumping `core`'s catalog version
  appends events in this order in the slice for this primitive:
  exactly one `PrimitiveUpgradeEvent(primitive="core",
  from_version=<old>, to_version=<new>, by="wiki-upgrade")` first;
  then the primitive's `PageWriteEvent`s and/or `PageProposalEvent`s
  (interleaving is allowed); then the aggregator's
  `ManagedRegionWriteEvent`s (and any region-drift
  `PageProposalEvent`s) strictly after.** Pins event ordering: the
  upgrade event is journaled before any of its primitive's page-
  scope writes; aggregator events are strictly after every per-
  primitive write.
- [ ] **AC3 — `wiki upgrade --primitive people` after bumping
  `people`'s catalog version emits exactly one
  `PrimitiveUpgradeEvent` whose `primitive == "people"` and leaves
  `core`'s installed version unchanged in `replay_state(...)`.**
- [ ] **AC4 — `wiki upgrade --primitive nope` exits 2 with `primitive
  'nope' is not installed` on stderr; no journal events appended.**
- [ ] **AC5 — `wiki upgrade --primitive gone` (installed but missing
  from catalog) exits 2 with `primitive 'gone' is no longer in the
  kit catalog` on stderr; no journal events appended.**
- [ ] **AC6 — User-edited file on an upgrade path produces a
  `.proposed` sidecar; the user file is byte-identical to its
  pre-call content; a `PageProposalEvent` for the path is
  journaled.** Drift line goes to stdout, named to the offending
  vault-relative path.
- [ ] **AC7 — `wiki upgrade` is idempotent on re-run: a second
  invocation against the same kit + same vault produces zero new
  events of any kind.**
- [ ] **AC8 — Outside a vault, exits 2 with `not a wiki vault` on
  stderr; no journal events anywhere.**
- [ ] **AC9 — The aggregator pass runs after per-primitive renders.
  Every `ManagedRegionWriteEvent` index and every aggregator-phase
  `PageProposalEvent` index in the new-events list is greater than
  every `PageWriteEvent` and per-primitive-phase `PageProposalEvent`
  index attributed to a primitive in `to_upgrade`.** An
  *aggregator-phase* `PageProposalEvent` is one whose `path` equals
  a region-host file declared by any installed primitive's
  `contributes_to: [{file: …}]` entry; a *per-primitive-phase*
  `PageProposalEvent` is one whose `path` lies under the renderer's
  output (`render_tree`-emitted). Mirrors `wiki add`'s existing
  ordering test, widened to cover both proposal phases.
- [ ] **AC10 — `wiki upgrade` runs the aggregator over the full
  installed set, not just `to_upgrade`.** Pin: with two installed
  primitives contributing to the same `(file, region)` bucket,
  upgrading one of them leaves the bucket containing both
  contributors' snippets after the run.
- [ ] **AC11 — `wiki upgrade --primitive name` where the catalog
  already matches the installed version prints `wiki upgrade:
  primitive 'name' is already at version <V>.` and exits 0 with
  zero new events.**
- [ ] **AC12 — When one installed primitive is missing from the
  catalog and `--primitive` is unset, `wiki upgrade` skips that
  primitive silently (no error, no event) and upgrades the rest.**
  `wiki doctor` is the surfacing mechanism for the missing primitive
  per existing `check_primitive_missing` behavior.
- [ ] **AC13 — `PrimitiveUpgradeEvent.by == "wiki-upgrade"`; per-
  primitive `PageWriteEvent.by == <primitive name>`;
  `ManagedRegionWriteEvent.by == "wiki-upgrade"`.** Pins the
  attribution contract.
- [ ] **AC14 — `wiki upgrade` runs inside
  `journal.use_journal_cache(journal_path)`** so the cache absorbs
  baseline lookups across the renders + aggregator pass (same qC4
  contract `wiki init` / `wiki add` enforce). A counting
  monkeypatch on `journal.read_events` observes the same
  one-cache-load shape that `test_wiki_add_install_pipeline_reads_
  journal_once_via_cache` pins for `wiki add`.
- [ ] **AC15 — `--primitive` containing `:` is rejected explicitly
  with `--primitive must be a bare primitive name, not
  <kind>:<name>`.** Exit 2. The check runs before state load so the
  message is the same regardless of whether the bare-name half is
  installed. (`wiki add` is the kind-aware command; `wiki upgrade`'s
  installed set already names primitives uniquely. The explicit
  message keeps the recommendation text from copy-paste-looping
  through the `not-installed` error.)
- [ ] **AC16 — A user-edited shared region-host file (e.g.
  `frontmatter.schema.yaml`) produces an aggregator-phase
  `.proposed` sidecar whose drift-line surfaces on stdout.** Bumps a
  primitive's catalog version, pre-edits the shared file outside the
  managed-region markers, runs `wiki upgrade`, asserts:
  (a) `frontmatter.schema.yaml.proposed` exists,
  (b) `frontmatter.schema.yaml` is byte-identical to its pre-call
  content,
  (c) the journal contains a `PageProposalEvent` for the path in the
  aggregator-phase slice, and
  (d) the CLI printed a `Wrote frontmatter.schema.yaml.proposed
  (drift detected on frontmatter.schema.yaml); run the wiki-conflict
  skill to merge.` line (capital `Wrote` matches `_cmd_research`'s
  existing line at `cli.py:1121`). Pins that the sidecar-collection
  is *aggregator-aware*, not page-renderer-only.
- [ ] **AC17 — `validate_contributions` is pre-flighted over
  `all_installed`, not just `to_upgrade`.** Pin: when an unchanged-
  version primitive's contribution shape becomes invalid in the kit
  (e.g. a declared `contributes_to` snippet missing on disk), `wiki
  upgrade` exits with `PrimitiveError` and zero new
  `PrimitiveUpgradeEvent`s are appended even for primitives that
  would otherwise have upgraded.
- [ ] **AC18 — `upgrade_primitives` warns when called without an
  active `journal.use_journal_cache` scope on a vault with ≥50
  events.** Mirrors `install._warn_if_install_pipeline_uncached`'s
  contract; a unit test pins the WARNING-log shape and the
  one-warning-per-resolved-path discipline.
- [ ] **AC19 — When at least one installed primitive is missing
  from the catalog, the CLI prints a one-line stderr hint naming
  the count and pointing at `wiki doctor`.** No new journal events
  for the missing primitives; the hint is purely a UX signal.

## Non-goals

- **No removal of primitives.** `wiki upgrade` never emits a
  `PrimitiveRemoveEvent`. Removing an installed primitive is a
  future `wiki primitive remove` (or `wiki uninstall`) — not in
  this spec.
- **No recipe-list reconciliation.** `wiki upgrade` does not add
  primitives that the recipe newly lists but the journal doesn't
  carry yet. `wiki add` is the route for newly-needed primitives.
- **No `--dry-run` mode.** A preview would duplicate the
  `plan_upgrade` data; today's `wiki doctor` plus the journal
  already let an operator predict an upgrade by reading
  `installed_primitives` vs. the catalog. A future spec can add it
  if real demand surfaces.
- **No auto-bump detection.** A primitive whose `version` did not
  change but whose `files/` content did is the primitive author's
  bug. `wiki upgrade` respects the version contract; `wiki doctor`
  is where any resulting baseline drift surfaces.
- **No interactive merge.** Drift produces `.proposed` sidecars
  exactly as `wiki add` does today; `wiki-conflict` (vault-side
  skill) is the merge UI.
- **No new event type.** `PrimitiveUpgradeEvent` is sufficient.
- **No partial-render retry.** If `safe_write` raises mid-render
  for a real I/O failure (disk full, permission denied), the
  exception propagates; the `with journal.use_journal_cache`
  unwind takes the same path it does for `wiki add`. `wiki doctor`
  is the recovery diagnostic.
- **No CLI progress bar.** A one-line-per-primitive summary is the
  whole UX; primitive counts are small (under ~30 for the family
  recipe).
- **No catalog-version pinning.** `wiki upgrade` always uses the
  catalog of the running kit; pinning to an older catalog version
  is a `pipx install llm-wiki-kit==X.Y.Z` operation, not a CLI
  flag here.

## Constraints

- **One new module boundary: `llm_wiki_kit/upgrade.py`.** No sub-
  package, no new top-level directory. The runner orchestrates
  existing modules (`install`, `render`, `journal`,
  `write_helper`).
- **No new runtime dependency.** Stdlib + `pyyaml` + `pydantic`
  only, as the rest of the kit. Adding any dep requires a new ADR
  per AGENTS.md.
- **No new event class in `models.py`.** Reuses
  `PrimitiveUpgradeEvent` (already present at `models.py:233`).
- **No bypass of `write_helper.safe_write` or
  `write_helper.safe_write_region`.** The runner goes through
  `render_tree` and `aggregate_region_contributions`; both already
  route through the safe-write helpers.
- **No install-pipeline contract change.**
  `install.install_primitives` keeps its current shape; the upgrade
  pipeline reuses `aggregate_region_contributions` and
  `validate_contributions` but does not piggyback on
  `install_primitives` (which would emit the wrong event type).
- **No structural change to `cli.py`.** `_cmd_upgrade` is wired
  identically to `_cmd_add`'s shape (vault check, state load,
  catalog load, plan/validate/render, summary print).
- **No new public CLI flag beyond `--primitive`.** Argparse is
  already wired for `--primitive` per the existing stub; no other
  flags introduced.
