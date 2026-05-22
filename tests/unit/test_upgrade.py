"""Unit tests for ``llm_wiki_kit.upgrade`` — the wiki-upgrade pipeline.

Pinned by ``docs/specs/wiki-upgrade/spec.md``. The planner is TDD'd as a
pure function over a synthetic ``VaultState`` + catalog list; the runner
construction tests pre-seed a fixture vault and assert on the
journal/disk shape after one ``upgrade_primitives`` call.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import (
    ManagedRegionWriteEvent,
    PageProposalEvent,
    PageWriteEvent,
    Primitive,
    PrimitiveKind,
    PrimitiveUpgradeEvent,
    VaultState,
)
from llm_wiki_kit.upgrade import UpgradePlan, plan_upgrade, upgrade_primitives

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Pure-planner tests (no I/O)
# ---------------------------------------------------------------------------


def _prim(name: str, version: str, *, requires: tuple[str, ...] = ()) -> Primitive:
    return Primitive(
        name=name,
        kind=PrimitiveKind.ONTOLOGY,
        version=version,
        description=f"{name} test primitive",
        requires=list(requires),
    )


def _state(installed: dict[str, str]) -> VaultState:
    return VaultState(
        vault_name="v",
        recipe="minimal",
        installed_primitives=dict(installed),
    )


def test_plan_upgrade_no_changes_returns_empty_to_upgrade() -> None:
    state = _state({"core": "0.1.0", "people": "0.1.0"})
    catalog = [_prim("core", "0.1.0"), _prim("people", "0.1.0")]
    plan = plan_upgrade(state, catalog, only=None)
    assert plan.to_upgrade == []
    assert [p.name for p in plan.all_installed] == ["core", "people"]
    assert plan.not_in_catalog == []
    assert plan.no_op_target is None


def test_plan_upgrade_returns_only_version_changed_primitives() -> None:
    state = _state({"core": "0.1.0", "people": "0.1.0"})
    catalog = [_prim("core", "0.2.0"), _prim("people", "0.1.0")]
    plan = plan_upgrade(state, catalog, only=None)
    assert [p.name for p in plan.to_upgrade] == ["core"]
    assert plan.no_op_target is None


def test_plan_upgrade_to_upgrade_in_install_order() -> None:
    state = _state({"core": "0.1.0", "people": "0.1.0", "meeting": "0.1.0"})
    catalog = [
        _prim("core", "0.2.0"),
        _prim("people", "0.2.0"),
        _prim("meeting", "0.2.0", requires=("people",)),
    ]
    plan = plan_upgrade(state, catalog, only=None)
    assert [p.name for p in plan.to_upgrade] == ["core", "people", "meeting"]


def test_plan_upgrade_with_only_filters_to_one_primitive() -> None:
    state = _state({"core": "0.1.0", "people": "0.1.0"})
    catalog = [_prim("core", "0.2.0"), _prim("people", "0.2.0")]
    plan = plan_upgrade(state, catalog, only="people")
    assert [p.name for p in plan.to_upgrade] == ["people"]


def test_plan_upgrade_with_only_not_installed_raises() -> None:
    state = _state({"core": "0.1.0"})
    catalog = [_prim("core", "0.1.0"), _prim("absent", "0.1.0")]
    with pytest.raises(WikiError, match="primitive 'absent' is not installed"):
        plan_upgrade(state, catalog, only="absent")


def test_plan_upgrade_with_only_not_in_catalog_raises() -> None:
    state = _state({"core": "0.1.0", "gone": "0.1.0"})
    catalog = [_prim("core", "0.1.0")]
    with pytest.raises(WikiError, match="primitive 'gone' is no longer in the kit catalog"):
        plan_upgrade(state, catalog, only="gone")


def test_plan_upgrade_records_no_op_target_when_only_at_latest() -> None:
    state = _state({"core": "0.1.0", "people": "0.1.0"})
    catalog = [_prim("core", "0.1.0"), _prim("people", "0.1.0")]
    plan = plan_upgrade(state, catalog, only="people")
    assert plan.to_upgrade == []
    assert plan.no_op_target == ("people", "0.1.0")


def test_plan_upgrade_skips_missing_from_catalog_without_only() -> None:
    state = _state({"core": "0.1.0", "gone": "0.1.0"})
    catalog = [_prim("core", "0.2.0")]
    plan = plan_upgrade(state, catalog, only=None)
    assert [p.name for p in plan.to_upgrade] == ["core"]
    assert plan.not_in_catalog == ["gone"]
    assert [p.name for p in plan.all_installed] == ["core"]


def test_plan_upgrade_records_downgrade_as_upgrade_target() -> None:
    state = _state({"core": "0.2.0"})
    catalog = [_prim("core", "0.1.0")]
    plan = plan_upgrade(state, catalog, only=None)
    assert [p.name for p in plan.to_upgrade] == ["core"]


def test_plan_upgrade_only_neither_installed_nor_in_catalog_prefers_not_installed_message() -> None:
    """Spec §Edge cases "not-installed wins": when `--primitive <name>` is
    BOTH not installed AND missing from catalog, the "not installed"
    error fires first (the `wiki add` recommendation is the actionable
    next step)."""

    state = _state({"core": "0.1.0"})
    catalog = [_prim("core", "0.1.0")]
    with pytest.raises(WikiError, match="primitive 'ghost' is not installed"):
        plan_upgrade(state, catalog, only="ghost")


def test_upgrade_primitives_rejects_empty_to_upgrade(tmp_path: Path, kit_root: Path) -> None:
    """Runner precondition: `to_upgrade` must be non-empty (CLI short-circuit
    is the only sanctioned route for the empty case per spec §Behavior step 6)."""

    vault = _init_vault(tmp_path, kit_root)
    plan = _make_plan_for_kit(kit_root, vault)
    assert plan.to_upgrade == []  # nothing bumped; the test fixture is at catalog version
    sources = _sources_for_plan(plan, kit_root)
    from llm_wiki_kit.journal import replay_state

    state = replay_state(read_events(_journal_path(vault)))

    with pytest.raises(ValueError, match="to_upgrade to be non-empty"):
        upgrade_primitives(
            plan=plan,
            sources=sources,
            journal_path=_journal_path(vault),
            context={"vault_name": "v", "recipe_name": "minimal"},
            state_versions=dict(state.installed_primitives),
            now=datetime.now(UTC),
        )


def test_upgrade_primitives_rejects_sources_missing_all_installed(
    tmp_path: Path, kit_root: Path
) -> None:
    """Runner precondition: `sources` must cover every primitive in
    `plan.all_installed` (the aggregator reads `regions/` for each)."""

    vault = _init_vault(tmp_path, kit_root)
    _bump_primitive_version(kit_root, "core", "0.2.0")
    plan = _make_plan_for_kit(kit_root, vault)
    # Sources mapping deliberately drops one of the all_installed entries.
    full_sources = _sources_for_plan(plan, kit_root)
    incomplete = {k: v for k, v in full_sources.items() if k != "core"}
    from llm_wiki_kit.journal import replay_state

    state = replay_state(read_events(_journal_path(vault)))

    with pytest.raises(ValueError, match="sources missing entries"):
        upgrade_primitives(
            plan=plan,
            sources=incomplete,
            journal_path=_journal_path(vault),
            context={"vault_name": "v", "recipe_name": "minimal"},
            state_versions=dict(state.installed_primitives),
            now=datetime.now(UTC),
        )


def test_plan_upgrade_all_installed_includes_only_catalog_primitives() -> None:
    state = _state({"core": "0.1.0", "gone": "0.1.0"})
    catalog = [_prim("core", "0.1.0")]
    plan = plan_upgrade(state, catalog, only=None)
    assert [p.name for p in plan.all_installed] == ["core"]
    assert "gone" not in {p.name for p in plan.all_installed}


# ---------------------------------------------------------------------------
# Runner construction tests (drive a real fixture vault)
# ---------------------------------------------------------------------------


def _install_kit(tmp_path: Path) -> Path:
    """Mirror ``tests/integration/test_wiki_add.py::_install_kit``."""

    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    templates_src = REPO_ROOT / "templates"
    (kit / "templates").mkdir()
    for relative in (
        "ontologies/people",
        "content-types/meeting",
        "operations/weekly-digest",
    ):
        kind = relative.split("/", 1)[0]
        (kit / "templates" / kind).mkdir(exist_ok=True)
        shutil.copytree(templates_src / relative, kit / "templates" / relative)
    recipes_dir = kit / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "minimal.yaml").write_text(
        "name: minimal\n"
        "version: 0.1.0\n"
        "description: Core-only recipe for wiki upgrade tests.\n"
        "primitives:\n"
        "  - core\n"
        "variables:\n"
        "  recipe_name: minimal\n",
        encoding="utf-8",
    )
    return kit


def _bump_primitive_version(kit_root: Path, name: str, new_version: str) -> str:
    """Mutate a primitive's ``primitive.yaml`` in the tmp kit, return old version."""

    candidates = list(kit_root.glob(f"**/{name}/primitive.yaml"))
    assert len(candidates) == 1, f"expected exactly one primitive.yaml for {name}, got {candidates}"
    manifest = candidates[0]
    text = manifest.read_text(encoding="utf-8")
    import yaml as _yaml

    data = _yaml.safe_load(text)
    old_version = data["version"]
    data["version"] = new_version
    manifest.write_text(_yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return old_version  # type: ignore[no-any-return]


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    return _install_kit(tmp_path)


def _init_vault(tmp_path: Path, kit_root: Path) -> Path:
    vault = tmp_path / "v"
    assert cli.main(["init", str(vault), "--recipe", "minimal"], kit_root=kit_root) == 0
    return vault


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _make_plan_for_kit(kit_root: Path, vault: Path) -> UpgradePlan:
    from llm_wiki_kit.journal import replay_state
    from llm_wiki_kit.primitives import discover_primitives, load_primitive

    state = replay_state(read_events(_journal_path(vault)))
    catalog = [load_primitive(kit_root / "core")]
    catalog.extend(discover_primitives(kit_root / "templates"))
    return plan_upgrade(state, catalog, only=None)


def _sources_for_plan(plan: UpgradePlan, kit_root: Path) -> dict[str, Path]:
    core_dir = kit_root / "core"
    templates_dir = kit_root / "templates"
    return {
        p.name: cli._primitive_source_dir(p, core_dir, templates_dir) for p in plan.all_installed
    }


def test_upgrade_primitives_emits_one_event_per_to_upgrade(tmp_path: Path, kit_root: Path) -> None:
    vault = _init_vault(tmp_path, kit_root)
    old_version = _bump_primitive_version(kit_root, "core", "0.2.0")

    plan = _make_plan_for_kit(kit_root, vault)
    sources = _sources_for_plan(plan, kit_root)

    from llm_wiki_kit.journal import replay_state

    state = replay_state(read_events(_journal_path(vault)))
    context = {"vault_name": "v", "recipe_name": "minimal"}
    before = len(read_events(_journal_path(vault)))

    proposals = upgrade_primitives(
        plan=plan,
        sources=sources,
        journal_path=_journal_path(vault),
        context=context,
        state_versions=dict(state.installed_primitives),
        now=datetime.now(UTC),
    )

    assert proposals == []
    new_events = read_events(_journal_path(vault))[before:]
    upgrade_events = [e for e in new_events if isinstance(e, PrimitiveUpgradeEvent)]
    assert [(e.primitive, e.from_version, e.to_version, e.by) for e in upgrade_events] == [
        ("core", old_version, "0.2.0", "wiki-upgrade")
    ]


def test_upgrade_primitives_event_before_render(tmp_path: Path, kit_root: Path) -> None:
    vault = _init_vault(tmp_path, kit_root)
    _bump_primitive_version(kit_root, "core", "0.2.0")
    plan = _make_plan_for_kit(kit_root, vault)
    sources = _sources_for_plan(plan, kit_root)

    from llm_wiki_kit.journal import replay_state

    state = replay_state(read_events(_journal_path(vault)))
    context = {"vault_name": "v", "recipe_name": "minimal"}

    upgrade_primitives(
        plan=plan,
        sources=sources,
        journal_path=_journal_path(vault),
        context=context,
        state_versions=dict(state.installed_primitives),
        now=datetime.now(UTC),
    )

    events = read_events(_journal_path(vault))
    upgrade_indices = [i for i, e in enumerate(events) if isinstance(e, PrimitiveUpgradeEvent)]
    core_page_indices = [
        i for i, e in enumerate(events) if isinstance(e, PageWriteEvent) and e.by == "core"
    ]
    assert upgrade_indices and core_page_indices
    # The PrimitiveUpgradeEvent for core lands BEFORE every core page write
    # produced during the upgrade pass. (Initial init's page writes pre-date
    # the upgrade event; we look at the upgrade pass's slice.)
    assert max(upgrade_indices) < min(i for i in core_page_indices if i > max(upgrade_indices))


def test_upgrade_primitives_runs_aggregator_with_wiki_upgrade_by(
    tmp_path: Path, kit_root: Path
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    # Install a content-type so the aggregator has a real `(file, region)`
    # bucket to write — ``core`` alone has no ``contributes_to`` entries.
    import os

    cwd = os.getcwd()
    try:
        os.chdir(vault)
        assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0
    finally:
        os.chdir(cwd)
    _bump_primitive_version(kit_root, "core", "0.2.0")
    plan = _make_plan_for_kit(kit_root, vault)
    sources = _sources_for_plan(plan, kit_root)

    from llm_wiki_kit.journal import replay_state

    state = replay_state(read_events(_journal_path(vault)))
    before = len(read_events(_journal_path(vault)))

    upgrade_primitives(
        plan=plan,
        sources=sources,
        journal_path=_journal_path(vault),
        context={"vault_name": "v", "recipe_name": "minimal"},
        state_versions=dict(state.installed_primitives),
        now=datetime.now(UTC),
    )

    new_events = read_events(_journal_path(vault))[before:]
    region_events = [e for e in new_events if isinstance(e, ManagedRegionWriteEvent)]
    assert region_events
    for event in region_events:
        assert event.by == "wiki-upgrade"


def test_upgrade_primitives_returns_proposal_paths_for_page_drift(
    tmp_path: Path, kit_root: Path
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    _bump_primitive_version(kit_root, "core", "0.2.0")

    # Drift a kit-owned page so the re-render produces a sidecar.
    agents_md = vault / "AGENTS.md"
    agents_md.write_text("user-edited content\n", encoding="utf-8")

    plan = _make_plan_for_kit(kit_root, vault)
    sources = _sources_for_plan(plan, kit_root)
    from llm_wiki_kit.journal import replay_state

    state = replay_state(read_events(_journal_path(vault)))

    proposals = upgrade_primitives(
        plan=plan,
        sources=sources,
        journal_path=_journal_path(vault),
        context={"vault_name": "v", "recipe_name": "minimal"},
        state_versions=dict(state.installed_primitives),
        now=datetime.now(UTC),
    )

    assert ("AGENTS.md", "AGENTS.md.proposed") in proposals
    # And the file itself is byte-identical to what the user wrote
    assert agents_md.read_text(encoding="utf-8") == "user-edited content\n"
    assert (vault / "AGENTS.md.proposed").exists()


def test_upgrade_primitives_returns_proposal_paths_for_aggregator_drift(
    tmp_path: Path, kit_root: Path
) -> None:
    # Install a content-type so the aggregator has real region content
    vault = _init_vault(tmp_path, kit_root)
    import os

    cwd = os.getcwd()
    try:
        os.chdir(vault)
        assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0
    finally:
        os.chdir(cwd)

    # Drift the host file *inside* one of its managed regions — that's
    # what ``safe_write_region`` detects as drift. Editing outside the
    # BEGIN/END markers is fine by ADR-0003 and triggers nothing.
    host = vault / "frontmatter.schema.yaml"
    text = host.read_text(encoding="utf-8")
    drifted = text.replace(
        "# BEGIN MANAGED: types\n  - meeting\n",
        "# BEGIN MANAGED: types\n  - meeting\n  - user-injected-type\n",
        1,
    )
    assert drifted != text, "fixture precondition: expected to find a region body to drift"
    host.write_text(drifted, encoding="utf-8")

    _bump_primitive_version(kit_root, "meeting", "0.2.0")
    plan = _make_plan_for_kit(kit_root, vault)
    sources = _sources_for_plan(plan, kit_root)
    from llm_wiki_kit.journal import replay_state

    state = replay_state(read_events(_journal_path(vault)))

    proposals = upgrade_primitives(
        plan=plan,
        sources=sources,
        journal_path=_journal_path(vault),
        context={"vault_name": "v", "recipe_name": "minimal"},
        state_versions=dict(state.installed_primitives),
        now=datetime.now(UTC),
    )

    assert ("frontmatter.schema.yaml", "frontmatter.schema.yaml.proposed") in proposals
    assert (vault / "frontmatter.schema.yaml.proposed").exists()


def test_upgrade_primitives_warns_when_uncached(
    tmp_path: Path,
    kit_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    _bump_primitive_version(kit_root, "core", "0.2.0")

    # Pad the journal past the 50-event warn threshold.
    from llm_wiki_kit import journal as _journal_mod
    from llm_wiki_kit.models import LintRunEvent

    for _ in range(55):
        _journal_mod.append_event(
            _journal_path(vault),
            LintRunEvent(timestamp=datetime.now(UTC), by="test-pad", status="ok"),
        )

    plan = _make_plan_for_kit(kit_root, vault)
    sources = _sources_for_plan(plan, kit_root)
    from llm_wiki_kit.journal import replay_state

    state = replay_state(read_events(_journal_path(vault)))

    # Clear the install-pipeline warn cache so this test sees a fresh slate.
    import llm_wiki_kit.install as _install_mod

    _install_mod._UNCACHED_PIPELINE_WARNED.clear()

    with caplog.at_level(logging.WARNING, logger="llm_wiki_kit.install"):
        upgrade_primitives(
            plan=plan,
            sources=sources,
            journal_path=_journal_path(vault),
            context={"vault_name": "v", "recipe_name": "minimal"},
            state_versions=dict(state.installed_primitives),
            now=datetime.now(UTC),
        )

    warnings_first = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "use_journal_cache" in r.getMessage()
    ]
    assert len(warnings_first) == 1, (
        f"expected one cache-discipline warning on first call; got {warnings_first}"
    )

    # Now bump again and re-run to verify the one-warning-per-resolved-path
    # discipline — ``_UNCACHED_PIPELINE_WARNED`` should suppress the second
    # warning. Without this, a future refactor that accidentally cleared
    # the set per call would still pass the first assertion.
    _bump_primitive_version(kit_root, "core", "0.3.0")
    state = replay_state(read_events(_journal_path(vault)))
    plan = _make_plan_for_kit(kit_root, vault)
    sources = _sources_for_plan(plan, kit_root)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="llm_wiki_kit.install"):
        upgrade_primitives(
            plan=plan,
            sources=sources,
            journal_path=_journal_path(vault),
            context={"vault_name": "v", "recipe_name": "minimal"},
            state_versions=dict(state.installed_primitives),
            now=datetime.now(UTC),
        )

    warnings_second = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "use_journal_cache" in r.getMessage()
    ]
    assert warnings_second == [], (
        f"second uncached call must not double-warn for the same journal path; "
        f"got {[r.getMessage() for r in warnings_second]}"
    )


# Mirror unused-import guards.
_ = PageProposalEvent
