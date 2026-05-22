# Plan: wiki-upgrade

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** In progress
- **Spec:** `docs/specs/wiki-upgrade/spec.md`
- **Owner:** maintainer

## Approach

One new module `llm_wiki_kit/upgrade.py` holds the pure planner
(`plan_upgrade`) and the orchestration runner (`upgrade_primitives`).
`_cmd_upgrade` in `cli.py` is a thin wrapper that mirrors `_cmd_add`'s
shape: vault-root + journal boundary check, state replay, catalog
load, recipe + context build, validate-then-run inside
`journal.use_journal_cache`, summary print.

The split between planner and runner exists so the upgrade-set logic
is a pure function the unit tests can hit without I/O. The runner is a
mechanical iteration on top of `render_tree` +
`aggregate_region_contributions` — both already drift-aware via
`safe_write` / `safe_write_region` — plus one `PrimitiveUpgradeEvent`
per upgraded primitive. No new event type, no new install-pipeline
contract.

Verification mode is TDD for the planner (pure function over a
synthetic `VaultState` + catalog list); construction tests for the
runner and integration tests for `_cmd_upgrade` cover the I/O
boundary. The CLI stub-list test gets pruned alongside the new
integration suite.

### Declined patterns (commitments for REVIEW)

- **Tempted to generalize `install_primitives` to take an
  event-builder callable.** Declining — it would blur the install
  vehicle's contract and grow surface that only one new caller
  needs. The shape of the upgrade pipeline is "emit one
  `PrimitiveUpgradeEvent` per upgraded primitive, then reuse
  `render_tree` + `aggregate_region_contributions` exactly as they
  stand." That doesn't justify a callback parameter on the shared
  installer; a separate runner is cleaner.
- **Tempted to add a `wiki upgrade --dry-run` flag.** Declining —
  Non-goals already names this; a dry-run is duplicate
  `plan_upgrade` data that the user can already reconstruct from
  `wiki doctor` + reading `replay_state(...).installed_primitives`
  vs. the catalog. Add later only if a real user asks.
- **Tempted to extract a `journal_upgrade_event(...)` helper into
  `journal.py`.** Declining — one call site, one `append_event`
  line, no abstraction win.
- **Tempted to introduce a `--force` flag that re-renders every
  installed primitive regardless of version.** Declining — the
  version contract is the upgrade signal; a `--force` reruns
  `safe_write` over every file the kit already owns, which the
  existing `test_no_op_write_of_identical_content_still_records_
  event` shape says appends an event per file even on no-op.
  Loud and wasteful. If a user wants to re-baseline, the route is
  `wiki doctor` plus targeted `wiki-conflict` resolves.

## Pre-conditions

- `safe_write` / `safe_write_region` already enforce ADR-0004 +
  safe-write-ordering (shipped).
- `install.aggregate_region_contributions` already supports re-
  aggregation over `all_installed` (shipped for `wiki add`).
- `PrimitiveUpgradeEvent` already exists in `models.py` (no schema
  change needed).
- Argparse for `wiki upgrade --primitive <name>` already wired in
  `build_parser`.
- No conflicting work in flight on `cli.py:_cmd_upgrade`.

## Steps

1. **`plan_upgrade` construction tests are red.**
   - **Tests** (new file `tests/unit/test_upgrade.py`):
     - `test_plan_upgrade_no_changes_returns_empty_to_upgrade` —
       state's installed versions match catalog; `to_upgrade == []`;
       `all_installed` lists every installed primitive in
       topological order.
     - `test_plan_upgrade_returns_only_version_changed_primitives` —
       one primitive bumped in catalog; that primitive is the sole
       entry in `to_upgrade`.
     - `test_plan_upgrade_to_upgrade_in_install_order` — two
       version-bumped primitives with a `requires:` relation
       between them; the dependent appears after its dependency.
     - `test_plan_upgrade_with_only_filters_to_one_primitive` —
       `only="people"`; returns just `people` in `to_upgrade` even
       when other primitives also have bumps available.
     - `test_plan_upgrade_with_only_not_installed_raises` —
       `only="absent"` not in `state.installed_primitives` →
       `WikiError("primitive 'absent' is not installed; run `wiki
       add ...`")`.
     - `test_plan_upgrade_with_only_not_in_catalog_raises` —
       `only="gone"` is in installed but missing from catalog →
       `WikiError("primitive 'gone' is no longer in the kit
       catalog; ...")`.
     - `test_plan_upgrade_records_no_op_target_when_only_at_latest`
       — `only="people"` and the catalog already matches:
       `to_upgrade == []`, `no_op_target == ("people", "<version>")`,
       no raise.
     - `test_plan_upgrade_skips_missing_from_catalog_without_only`
       — `state` has primitive `gone` not in catalog; without
       `only`, `gone` appears in `not_in_catalog`, *not* in
       `all_installed`, and is omitted from `to_upgrade`. Other
       primitives proceed normally.
     - `test_plan_upgrade_records_downgrade_as_upgrade_target` —
       installed version > catalog version; the primitive lands in
       `to_upgrade` (the event records the transition honestly per
       spec §Edge cases).
     - `test_plan_upgrade_all_installed_includes_only_catalog_primitives`
       — `all_installed` filters out any installed primitive not
       in the catalog, so the aggregator pass never sees a
       missing-from-catalog primitive whose snippet directory may
       not exist.
   - **Verify:** `pytest tests/unit/test_upgrade.py` fails at
     import (`llm_wiki_kit.upgrade` does not exist yet).
1. **`llm_wiki_kit/upgrade.py` makes the planner tests pass.**
   - Implement `UpgradePlan` (frozen dataclass) and `plan_upgrade`
     per spec §Contracts. Sorting goes through
     `primitives.resolve_dependencies` so the order matches the
     install pipeline's invariant.
   - **Verify:** `pytest tests/unit/test_upgrade.py` green.
1. **`upgrade_primitives` construction tests are red.**
   - **Tests** (same file `tests/unit/test_upgrade.py`):
     - `test_upgrade_primitives_emits_one_event_per_to_upgrade` —
       feed a `UpgradePlan` with two `to_upgrade` primitives over
       a tmp vault (pre-seeded with `wiki init`); assert exactly
       two new `PrimitiveUpgradeEvent`s with the expected `(from,
       to, primitive, by="wiki-upgrade")` shape.
     - `test_upgrade_primitives_event_before_render` — install a
       monkeypatched `safe_write` recorder that snapshots
       `read_events(journal_path)` at write-time; assert each
       primitive's `PrimitiveUpgradeEvent` is durable before any
       of its primitive's page writes. Mirrors the spec §Behavior
       "event-before-disk" invariant.
     - `test_upgrade_primitives_runs_aggregator_with_wiki_upgrade_by`
       — after the per-primitive loop, every `ManagedRegionWriteEvent`
       appended during the call has `by == "wiki-upgrade"`.
     - `test_upgrade_primitives_returns_proposal_paths_for_page_drift`
       — feed a plan where one primitive's `safe_write` will drift
       on a user-edited page; assert the runner returns the
       vault-relative POSIX path of the produced sidecar.
     - `test_upgrade_primitives_returns_proposal_paths_for_aggregator_drift`
       (AC16 lower half) — feed a plan where the aggregator pass
       will drift on a user-edited shared region-host file (e.g.
       `frontmatter.schema.yaml`); assert the returned list
       includes the host file's `.proposed` path. Pins that the
       collection sees aggregator-phase proposals, not just
       per-primitive ones.
     - `test_upgrade_primitives_warns_when_uncached` (AC18) — call
       `upgrade_primitives` against a vault with ≥50 events
       outside a `use_journal_cache` scope; assert one WARNING is
       emitted naming the spec; a second call against the same
       resolved journal path does not double-warn. Mirrors
       `test_install_pipeline_warns_when_uncached`.
     - The "empty `to_upgrade`" runner case is *not* a test target:
       the CLI short-circuits before calling the runner (spec
       §Behavior step 6). The runner's contract starts at
       "`to_upgrade` is non-empty"; testing it on empty input would
       pin an implementation detail (aggregator's
       `ManagedRegionWriteEvent`-per-bucket-even-on-hash-match
       behaviour) that the CLI is specifically designed to avoid
       exposing. A construction test asserting the short-circuit
       lives in the integration suite (AC1, AC7, AC11) where it
       belongs.
   - **Verify:** `pytest tests/unit/test_upgrade.py` fails on the
     new tests (the runner is not yet implemented).
1. **`upgrade_primitives` runner makes its tests pass.**
   - Implement per spec §Contracts. The runner:
     1. Calls `install._warn_if_install_pipeline_uncached(
        journal_path)` first so the cache-discipline warning fires
        on uncached invocations.
     2. Snapshots `length_before = len(read_events(journal_path))`
        — a parsed-event count, not a raw line count. The slice in
        step 5 indexes the parsed events list; using a raw line
        count would desync if any blank line snuck into the journal
        (the strict reader skips blanks but `len()` over file lines
        would not). The cost is one full journal walk before the
        renders + aggregator; the active cache scope absorbs it for
        downstream consumers and the wall-clock cost on a
        production-sized vault is sub-millisecond.
     3. For each primitive `p` in `plan.to_upgrade`: appends
        `PrimitiveUpgradeEvent(timestamp=now, by="wiki-upgrade",
        primitive=p.name, from_version=<state>, to_version=p.version)`
        (event-before-disk), then calls `render_tree(
        sources[p.name] / "files", vault_root, context,
        journal_path, by=p.name)`. The runner reads the `from_version`
        out of a `state_versions: Mapping[str, str]` argument the
        CLI threads in (factored from `state.installed_primitives`
        at the boundary; keeping the runner pure of `VaultState`
        avoids a circular-import shape).
     4. Calls `aggregate_region_contributions(plan.all_installed,
        sources, journal_path, by="wiki-upgrade")` exactly once
        after the per-primitive loop.
     5. Reads `read_events(journal_path)[length_before:]` — note:
        through `llm_wiki_kit.journal.read_events`, the disk
        re-read path, NOT a cached `JournalReader.events()` slice;
        the latter aliases the cache's internal list and a slice
        would risk corrupting it. The redundant disk read happens
        exactly once per `wiki upgrade`, after every write, and is
        the load-bearing source of the drift surface — its cost is
        negligible. Filters for `PageProposalEvent` and returns
        `(event.path, event.proposed_path)` tuples in journal
        order, INCLUDING aggregator-emitted proposals on shared
        region-host files. Returning both fields (rather than just
        `proposed_path`) decouples the CLI's `Wrote <sidecar>
        (drift detected on <path>)` rendering from the structural
        assumption that sidecars are always named `<path>.proposed`.
   - **Verify:** `pytest tests/unit/test_upgrade.py` green.
1. **`_cmd_upgrade` CLI handler integration tests are red.**
   - **Tests** (new file `tests/integration/test_wiki_upgrade.py`,
     reusing the `kit_root` / `_init_vault` shape from
     `tests/integration/test_wiki_add.py`):
     - `test_upgrade_no_changes_prints_nothing_to_upgrade` (AC1) —
       initialize vault; run `wiki upgrade`; capture stdout; assert
       `"wiki upgrade: nothing to upgrade."` and no new events.
     - `test_upgrade_emits_event_and_re_renders_when_catalog_version_bumps`
       (AC2 + AC9 + AC13) — mutate `core/primitive.yaml` in the
       tmp kit to bump its `version`; run `wiki upgrade`; assert
       exactly one `PrimitiveUpgradeEvent` with the expected
       `from`/`to`/`by`, followed by `PageWriteEvent`s, followed
       by `ManagedRegionWriteEvent`s (`max(upgrade_indices) <
       min(page_indices)` ordering pin), and that
       `ManagedRegionWriteEvent.by == "wiki-upgrade"`.
     - `test_upgrade_primitive_flag_restricts_to_one_primitive`
       (AC3) — bump both `core` and `people` in the kit; run
       `wiki upgrade --primitive people`; assert only `people`
       gets a `PrimitiveUpgradeEvent`; `state.installed_primitives[
       "core"]` is still the old version.
     - `test_upgrade_primitive_not_installed_exits_2` (AC4) —
       `wiki upgrade --primitive nope` against a vault that does
       not install `nope`; assert exit 2 and the "not installed"
       message on stderr; assert journal length unchanged.
     - `test_upgrade_primitive_not_in_catalog_exits_2` (AC5) —
       hand-edit the journal (or pre-seed via fixtures) so
       `installed_primitives` includes a name not in the kit
       catalog; `wiki upgrade --primitive <that-name>`; assert
       exit 2 and the "no longer in the kit catalog" message;
       assert journal length unchanged.
     - `test_upgrade_with_user_edited_file_produces_proposal`
       (AC6) — bump `people`'s catalog version; pre-edit a
       `people`-owned file in the vault to differ from its
       baseline; run `wiki upgrade`; assert the user file is
       byte-identical to its pre-call content; assert a
       `<path>.proposed` sidecar exists with the new render;
       assert a `PageProposalEvent` for the path is journaled;
       assert the drift-line goes to stdout.
     - `test_upgrade_is_idempotent_on_rerun` (AC7) — bump `core`,
       run upgrade, capture event count, run upgrade again,
       assert the journal length did not grow.
     - `test_upgrade_refuses_when_cwd_is_not_a_vault` (AC8) —
       same shape as `test_add_refuses_when_cwd_is_not_a_vault`;
       exit 2; stderr contains `not a wiki vault`.
     - `test_upgrade_aggregator_runs_over_full_installed_set`
       (AC10) — two installed primitives (`people` + `meeting`)
       contribute to the same managed region; bump only
       `meeting`; run upgrade; read
       `frontmatter.schema.yaml` and assert both contributors'
       snippets are present in the aggregated body.
     - `test_upgrade_only_at_latest_prints_already_at_version`
       (AC11) — `wiki upgrade --primitive core` when `core` is
       already at the catalog version; assert the louder message
       and zero new events.
     - `test_upgrade_skips_missing_from_catalog_silently`
       (AC12) — pre-seed `installed_primitives` to include a
       name missing from the catalog; run `wiki upgrade`
       (without `--primitive`); assert exit 0; assert no
       `PrimitiveUpgradeEvent` for the missing name; assert
       other primitives upgrade normally.
     - `test_upgrade_install_pipeline_reads_journal_once_via_cache`
       (AC14) — same shape as the existing
       `test_wiki_add_install_pipeline_reads_journal_once_via_cache`
       qC4 pin.
     - `test_upgrade_rejects_kind_prefix_in_primitive_flag` (AC15)
       — `wiki upgrade --primitive content-type:people`; assert
       exit 2 with the *explicit* stderr `--primitive must be a
       bare primitive name, not <kind>:<name>`; assert zero new
       journal events. The check runs before state load so the
       error is identical whether or not `people` is installed.
     - `test_upgrade_aggregator_drift_on_shared_file_produces_sidecar_line`
       (AC16) — pre-edit `frontmatter.schema.yaml` outside its
       managed-region markers; bump a primitive's catalog version;
       run `wiki upgrade`; assert
       `frontmatter.schema.yaml.proposed` exists, the original
       host file is byte-identical to its pre-call content, the
       new-events slice contains a `PageProposalEvent` for
       `frontmatter.schema.yaml`, and stdout contains the drift
       line for the host file. Pins the aggregator-aware sidecar
       collection.
     - `test_upgrade_validates_unchanged_primitive_contributions`
       (AC17) — install two primitives (`people` at 0.1.0,
       `meeting` at 0.1.0); bump `people` to 0.2.0 in the kit;
       remove a snippet file from `meeting`'s `regions/`
       directory in the kit (simulating a kit author error for an
       unchanged-version primitive); run `wiki upgrade`; assert
       exit 2 with `PrimitiveError` text; assert zero new
       `PrimitiveUpgradeEvent`s (including for `people`, which
       would otherwise have upgraded). Pins pre-flight covering
       `all_installed`, not just `to_upgrade`.
     - The CLI-side companion for AC18 lives entirely in the unit
       suite: a `test_upgrade_primitives_warns_when_uncached` test
       on `upgrade_primitives` directly is sufficient because the
       CLI handler always wraps the call in
       `journal.use_journal_cache(journal_path)` and a "future
       contributor who forgets the wrap" is exactly the regression
       the unit test catches. Adding an integration mirror that
       monkeypatches the cache to a no-op would test the
       monkeypatch, not the contract.
     - `test_upgrade_missing_from_catalog_emits_stderr_hint` (AC19)
       — pre-seed `installed_primitives` to include a name missing
       from the catalog; bump `core`'s catalog version; run `wiki
       upgrade`; assert stderr contains the count-aware singular
       hint (`note: 1 installed primitive no longer in the kit
       catalog; run \`wiki doctor\` for details.`; the plural form
       `primitives` applies for `N > 1`) and stdout contains the
       per-primitive upgrade line for `core`.
   - **Verify:** all integration tests fail (or red on assertion)
     because `_cmd_upgrade` is still the stub.
1. **`_cmd_upgrade` makes the integration tests pass.**
   - Replace the body of `_cmd_upgrade` at `cli.py:486` with the
     pipeline:
     1. `--primitive` shape check: reject `:` with the explicit
        message (spec §Behavior step 2).
     2. Vault root + journal boundary check.
     3. State load (`replay_state(read_events(journal_path))`);
        reject if `state.recipe is None` or `state.vault_name is
        None`.
     4. Catalog load (`core` + `discover_primitives(templates_dir)`).
     5. `plan = plan_upgrade(state, catalog, only=args.primitive)`.
        Errors from the planner (`--primitive` not installed / not
        in catalog) propagate as `WikiError`.
     6. Short-circuit when `plan.to_upgrade is empty`: print the
        appropriate no-op message (factoring on
        `plan.no_op_target`), and if `plan.not_in_catalog` is
        non-empty, print the stderr hint; return 0 *without*
        entering the runner.
     7. `validate_contributions(p, sources[p.name])` for every
        primitive in `plan.all_installed` (widened pre-flight per
        spec §Invariants 8).
     8. Load recipe; build context; build `sources` dict over
        `all_installed`.
     9. Wrap the runner call in `journal.use_journal_cache(
        journal_path)`. Call `upgrade.upgrade_primitives(
        plan=plan, sources=sources, journal_path=journal_path,
        context=context, state_versions=
        dict(state.installed_primitives), now=now)`.
     10. Print one `upgraded <name> <from> → <to>` line per
         primitive in `plan.to_upgrade` (snapshotted before the
         run; `from_version` comes from `state.installed_primitives`,
         `to_version` from `p.version`).
     11. Print one drift line per `.proposed` sidecar the runner
         returned, in journal order.
     12. Print the count-aware totals row: `wiki upgrade: upgraded
         1 primitive.` when `N == 1`, `wiki upgrade: upgraded N
         primitives.` for `N > 1`.
     13. If `plan.not_in_catalog` is non-empty, print the stderr
         hint (singular `primitive` for `N == 1`, plural otherwise).
     14. Return 0.
   - Use `upgrade.UPGRADE_VEHICLE` (defined in `upgrade.py`) as the
     single source of truth for the `"wiki-upgrade"` vehicle string —
     do not duplicate it as a `cli.INSTALL_VEHICLE_UPGRADE` constant.
     The runner owns the value because it's the only producer of the
     attribution; the CLI never reads it directly.
   - **Verify:** `pytest tests/integration/test_wiki_upgrade.py`
     green.
1. **CLI stub-list test updates.**
   - In `tests/unit/test_cli.py`, drop the two
     `["upgrade", ...]` entries from `SUBCOMMANDS_WITH_ARGS`.
   - Update the comment block above the list to note `upgrade`
     graduated in Phase F Task 23 (`docs/specs/wiki-upgrade/`).
   - **Verify:** `pytest tests/unit/test_cli.py` green.
1. **Doc sweep + RFC strike.**
   - `docs/rfc/0001-v2-architecture.md` Phase F: tick Task 23
     with ✅ and a one-line shipped-status footnote (mirroring
     Task 24's shape on line ~308). Update the "Progress to
     date" paragraph (lines ~162–179) to list only Task 22 as
     remaining in Phase F. The commit message format is `v2:
     implement wiki upgrade` per the brief.
   - `docs/architecture/overview.md` line 119: drop the
     "(`upgrade` and ...)" qualifier and re-list `journal` as the
     remaining stub category. Add a row for `upgrade.py` after
     the existing `install.py` row.
   - **Verify:** `git grep -n "wiki upgrade.*stub\|upgrade.*not yet
     implemented" docs core` returns no live admonitions for the
     `upgrade` surface.
1. **Patterns capture.**
   - One entry appended to `docs/knowledge/patterns.jsonl` scoped
     to `llm_wiki_kit/upgrade.py` capturing the "reuse
     `aggregate_region_contributions` from `install.py` rather
     than generalize `install_primitives`" decision so a future
     maintainer doesn't re-derive it.
   - **Verify:** the JSONL line parses with `python -m json.tool`
     (one valid object per line); `id` is unique within the file.

## Verification gate

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

All four green. `pytest tests/unit/test_upgrade.py
tests/integration/test_wiki_upgrade.py tests/unit/test_cli.py`
green in particular. Smoke: `wiki doctor` against a fixture vault
after `wiki upgrade` returns no new issues.

## Risks

- **Sidecar-collection by journal tail is fragile.** A future
  refactor that emits `PageProposalEvent`s for paths the runner
  did not render against would inflate the drift-line count.
  Mitigation: the construction test
  `test_upgrade_primitives_returns_proposal_paths` pins exact
  shapes; the integration test
  `test_upgrade_with_user_edited_file_produces_proposal` pins
  the CLI-line output.
- **Mutating `primitive.yaml` in tests is fiddly.** Each
  integration test that bumps a version reads-mutates-writes the
  YAML manifest in the tmp kit. The kit fixture's `_install_kit`
  helper builds a fresh tmp kit per test (no cross-test bleed);
  the test's mutation step happens *between* `_init_vault` and
  the `wiki upgrade` call, so the journal records the old
  version and the catalog reports the new one. A small
  `_bump_primitive_version(tmp_kit, "core", "0.2.0")` helper
  factors this out.
- **Aggregator drift behavior under upgrade is not new ground.**
  The same drift-on-region path that `wiki add` exercises kicks
  in here; the AC10 test pins the multi-contributor survivor
  case so the aggregator's "use the full installed set" contract
  cannot regress.
- **`_cmd_upgrade` reads `state.recipe` to load variables.** If
  the recipe was renamed in a kit update, `wiki upgrade`
  refuses. This matches `_cmd_add`. A future task that pins
  recipe rename semantics is a separate spec.
- **Path traversal in `--primitive` value is not a live risk.**
  `--primitive` is a journal lookup key (matched against
  `state.installed_primitives`, which validates names at install
  time against `NAME_PATTERN = ^[a-z][a-z0-9-]*$`). No FS access
  is keyed off the raw value. Recording here only to document
  that the `:` rejection (AC15) is a UX concern, not a security
  one.

## Out of scope

- `wiki primitive remove` (future spec).
- `wiki upgrade --dry-run` (Non-goal; reconsider on user demand).
- `wiki upgrade --force` (Non-goal; declined-pattern register).
- Recipe-list reconciliation; new primitives the recipe adds in
  a kit update (`wiki add` is the route).
- FTS5 or any non-upgrade surface (orthogonal to this spec).
