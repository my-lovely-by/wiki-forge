"""End-to-end ``wiki upgrade`` integration tests (RFC-0001 Phase F Task 23).

Mirrors the kit-root threading pattern from
``tests/integration/test_wiki_add.py``: a tmp kit holds the real
``core`` and the three Task-11 primitives plus a minimal recipe, the
test mutates a primitive's ``version`` in the kit between init and
upgrade to simulate "the kit shipped a new version since the vault
was created", and the CLI runs against the mutated kit.

Pinned by ``docs/specs/wiki-upgrade/spec.md`` AC1-AC19.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from llm_wiki_kit import cli
from llm_wiki_kit.journal import append_event, read_events, replay_state
from llm_wiki_kit.models import (
    ManagedRegionWriteEvent,
    PageProposalEvent,
    PageWriteEvent,
    PrimitiveInstallEvent,
    PrimitiveUpgradeEvent,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_kit(tmp_path: Path) -> Path:
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
    """Bump ``primitive.yaml`` in the tmp kit, return the old version."""

    candidates = list(kit_root.glob(f"**/{name}/primitive.yaml"))
    assert len(candidates) == 1, f"expected one primitive.yaml for {name}, got {candidates}"
    manifest = candidates[0]
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    old_version = data["version"]
    data["version"] = new_version
    manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return old_version  # type: ignore[no-any-return]


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    return _install_kit(tmp_path)


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _init_vault(tmp_path: Path, kit_root: Path) -> Path:
    vault = tmp_path / "v"
    assert cli.main(["init", str(vault), "--recipe", "minimal"], kit_root=kit_root) == 0
    return vault


# ---------------------------------------------------------------------------
# AC1, AC7, AC11 — short-circuit cases
# ---------------------------------------------------------------------------


def test_upgrade_no_changes_prints_nothing_to_upgrade(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC1: nothing to upgrade → louder no-op message, zero new events."""

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    capsys.readouterr()
    events_before = read_events(_journal_path(vault))

    assert cli.main(["upgrade"], kit_root=kit_root) == 0

    out = capsys.readouterr().out
    assert "wiki upgrade: nothing to upgrade." in out
    assert read_events(_journal_path(vault)) == events_before


def test_upgrade_is_idempotent_on_rerun(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7: re-running ``wiki upgrade`` after a clean upgrade is a no-op.

    The CLI short-circuits on ``plan.to_upgrade == []`` — that's what
    makes the second run journal-clean (the runner itself would emit
    a ``ManagedRegionWriteEvent`` per bucket on hash-match).
    """

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    _bump_primitive_version(kit_root, "core", "0.2.0")

    assert cli.main(["upgrade"], kit_root=kit_root) == 0
    events_first = read_events(_journal_path(vault))

    assert cli.main(["upgrade"], kit_root=kit_root) == 0
    events_second = read_events(_journal_path(vault))

    assert events_second == events_first


def test_upgrade_only_at_latest_prints_already_at_version(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC11: ``--primitive <name>`` already at catalog version → louder message."""

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    capsys.readouterr()
    events_before = read_events(_journal_path(vault))

    assert cli.main(["upgrade", "--primitive", "core"], kit_root=kit_root) == 0

    out = capsys.readouterr().out
    assert "wiki upgrade: primitive 'core' is already at version" in out
    assert read_events(_journal_path(vault)) == events_before


# ---------------------------------------------------------------------------
# AC2, AC9, AC13 — happy-path upgrade with one primitive bumped
# ---------------------------------------------------------------------------


def test_upgrade_emits_event_and_re_renders_when_catalog_version_bumps(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC2 + AC9 + AC13: PrimitiveUpgradeEvent → PageWriteEvents → aggregator;
    attribution `by="wiki-upgrade"` on PrimitiveUpgradeEvent and on every
    ManagedRegionWriteEvent during this pass. Also pins the singular
    totals row (`wiki upgrade: upgraded 1 primitive.`) per spec §Outputs."""

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    # Install meeting so the aggregator has a real bucket to write.
    assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0
    events_before = read_events(_journal_path(vault))

    old_version = _bump_primitive_version(kit_root, "core", "0.2.0")
    capsys.readouterr()

    assert cli.main(["upgrade"], kit_root=kit_root) == 0

    out = capsys.readouterr().out
    assert f"upgraded core {old_version} → 0.2.0" in out
    # AC2 singular-totals pin: a one-primitive upgrade prints
    # ``primitive`` (not ``primitive(s)``). Spec §Outputs pluralises
    # based on count.
    assert "wiki upgrade: upgraded 1 primitive." in out

    new_events = read_events(_journal_path(vault))[len(events_before) :]

    upgrade_events = [e for e in new_events if isinstance(e, PrimitiveUpgradeEvent)]
    assert [(e.primitive, e.from_version, e.to_version, e.by) for e in upgrade_events] == [
        ("core", old_version, "0.2.0", "wiki-upgrade")
    ]

    # AC9: PrimitiveUpgradeEvent comes before any of its page writes;
    # aggregator events come strictly after all per-primitive writes.
    upgrade_indices = [i for i, e in enumerate(new_events) if isinstance(e, PrimitiveUpgradeEvent)]
    page_indices = [
        i
        for i, e in enumerate(new_events)
        if isinstance(e, PageWriteEvent | PageProposalEvent) and e.by == "core"
    ]
    region_indices = [i for i, e in enumerate(new_events) if isinstance(e, ManagedRegionWriteEvent)]
    assert upgrade_indices and page_indices and region_indices
    assert max(upgrade_indices) < min(page_indices)
    assert max(page_indices) < min(region_indices)

    # AC13: aggregator-pass attribution.
    for event in new_events:
        if isinstance(event, ManagedRegionWriteEvent):
            assert event.by == "wiki-upgrade"
        if isinstance(event, PageWriteEvent) and event.by == "core":
            # PageWriteEvents from the upgrade keep their renderer-side
            # ``by`` (the primitive name), not the install vehicle.
            pass


def test_upgrade_primitive_flag_restricts_to_one_primitive(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: ``--primitive people`` upgrades only ``people``."""

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    assert cli.main(["add", "ontology:people"], kit_root=kit_root) == 0
    events_before = read_events(_journal_path(vault))

    core_old = _bump_primitive_version(kit_root, "core", "0.2.0")
    people_old = _bump_primitive_version(kit_root, "people", "0.2.0")

    assert cli.main(["upgrade", "--primitive", "people"], kit_root=kit_root) == 0

    new_events = read_events(_journal_path(vault))[len(events_before) :]
    upgrade_events = [e for e in new_events if isinstance(e, PrimitiveUpgradeEvent)]
    assert [(e.primitive, e.from_version, e.to_version) for e in upgrade_events] == [
        ("people", people_old, "0.2.0")
    ]

    state = replay_state(read_events(_journal_path(vault)))
    assert state.installed_primitives["core"] == core_old
    assert state.installed_primitives["people"] == "0.2.0"


# ---------------------------------------------------------------------------
# AC4, AC5, AC8, AC15 — boundary / shape errors
# ---------------------------------------------------------------------------


def test_upgrade_primitive_not_installed_exits_2(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC4: ``--primitive <not-installed>`` → exit 2, no events."""

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    events_before = read_events(_journal_path(vault))
    capsys.readouterr()

    assert cli.main(["upgrade", "--primitive", "nope"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "primitive 'nope' is not installed" in err
    assert read_events(_journal_path(vault)) == events_before


def test_upgrade_primitive_not_in_catalog_exits_2(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC5: ``--primitive <installed-but-missing>`` → exit 2, no events."""

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    # Pre-seed a PrimitiveInstallEvent for a name not in the kit catalog.
    append_event(
        _journal_path(vault),
        PrimitiveInstallEvent(
            timestamp=read_events(_journal_path(vault))[-1].timestamp,
            by="test-seed",
            primitive="gone",
            version="0.1.0",
        ),
    )
    events_before = read_events(_journal_path(vault))
    capsys.readouterr()

    assert cli.main(["upgrade", "--primitive", "gone"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "primitive 'gone' is no longer in the kit catalog" in err
    assert read_events(_journal_path(vault)) == events_before


def test_upgrade_refuses_when_cwd_is_not_a_vault(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC8: outside a vault → exit 2 with `not a wiki vault`."""

    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    capsys.readouterr()

    assert cli.main(["upgrade"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "not a wiki vault" in err


def test_upgrade_rejects_kind_prefix_in_primitive_flag(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC15: ``--primitive content-type:people`` → explicit error, no events."""

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    events_before = read_events(_journal_path(vault))
    capsys.readouterr()

    assert (
        cli.main(["upgrade", "--primitive", "content-type:people"], kit_root=kit_root)
        == cli.WIKI_ERROR_EXIT
    )
    err = capsys.readouterr().err
    assert "--primitive must be a bare primitive name, not <kind>:<name>" in err
    assert read_events(_journal_path(vault)) == events_before


# ---------------------------------------------------------------------------
# AC6, AC16 — drift surface
# ---------------------------------------------------------------------------


def test_upgrade_with_user_edited_file_produces_proposal(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC6: user-edited page on an upgrade path → ``.proposed`` sidecar."""

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    # Drift a kit-owned file before the upgrade pass.
    agents_md = vault / "AGENTS.md"
    user_content = "user-edited content\n"
    agents_md.write_text(user_content, encoding="utf-8")

    _bump_primitive_version(kit_root, "core", "0.2.0")
    capsys.readouterr()

    assert cli.main(["upgrade"], kit_root=kit_root) == 0

    # User file untouched; sidecar present.
    assert agents_md.read_text(encoding="utf-8") == user_content
    sidecar = vault / "AGENTS.md.proposed"
    assert sidecar.exists()
    out = capsys.readouterr().out
    assert "Wrote AGENTS.md.proposed (drift detected on AGENTS.md);" in out

    proposals = [
        e
        for e in read_events(_journal_path(vault))
        if isinstance(e, PageProposalEvent) and e.path == "AGENTS.md"
    ]
    assert proposals, "expected a PageProposalEvent for AGENTS.md"


def test_upgrade_aggregator_drift_on_shared_file_produces_sidecar_line(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC16 + AC9 two-phase proposal ordering: aggregator-emitted
    region-drift sidecar surfaces on stdout, AND when both per-primitive
    and aggregator-phase proposals are produced in the same run, the
    aggregator-phase index strictly follows every per-primitive-phase
    index in the journal slice.
    """

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0

    # Per-primitive-phase drift: edit a ``meeting``-owned page so the
    # render_tree pass produces a PageProposalEvent.
    template_page = vault / "_templates" / "meeting.md"
    assert template_page.exists(), "fixture precondition: meeting ships a templated page"
    template_page.write_text("user-edited template\n", encoding="utf-8")

    # Aggregator-phase drift: edit the shared host file inside a managed
    # region body so ``safe_write_region`` proposes the whole file.
    host = vault / "frontmatter.schema.yaml"
    text = host.read_text(encoding="utf-8")
    drifted = text.replace(
        "# BEGIN MANAGED: types\n  - meeting\n",
        "# BEGIN MANAGED: types\n  - meeting\n  - user-injected-type\n",
        1,
    )
    assert drifted != text, "fixture precondition: types-region body must be drifted"
    host.write_text(drifted, encoding="utf-8")

    _bump_primitive_version(kit_root, "meeting", "0.2.0")
    events_before = read_events(_journal_path(vault))
    capsys.readouterr()

    assert cli.main(["upgrade"], kit_root=kit_root) == 0

    out = capsys.readouterr().out
    assert (
        "Wrote frontmatter.schema.yaml.proposed "
        "(drift detected on frontmatter.schema.yaml); "
        "run the wiki-conflict skill to merge."
    ) in out
    assert (vault / "frontmatter.schema.yaml.proposed").exists()

    # AC9 two-phase ordering: per-primitive proposal index < aggregator
    # proposal index in the new-events slice. Per-primitive-phase
    # proposals carry a `path` under a primitive's render_tree output
    # (here: `_templates/meeting.md`); aggregator-phase proposals
    # carry the region-host file's `path` (`frontmatter.schema.yaml`).
    new_events = read_events(_journal_path(vault))[len(events_before) :]
    per_primitive_indices = [
        i
        for i, e in enumerate(new_events)
        if isinstance(e, PageProposalEvent) and e.path == "_templates/meeting.md"
    ]
    aggregator_indices = [
        i
        for i, e in enumerate(new_events)
        if isinstance(e, PageProposalEvent) and e.path == "frontmatter.schema.yaml"
    ]
    assert per_primitive_indices, (
        "fixture precondition: expected a per-primitive-phase PageProposalEvent "
        "for _templates/meeting.md"
    )
    assert aggregator_indices, (
        "fixture precondition: expected an aggregator-phase PageProposalEvent "
        "for frontmatter.schema.yaml"
    )
    assert max(per_primitive_indices) < min(aggregator_indices), (
        "AC9: aggregator-phase PageProposalEvent must come strictly after every "
        f"per-primitive-phase PageProposalEvent; per-primitive indices "
        f"{per_primitive_indices}, aggregator indices {aggregator_indices}"
    )


# ---------------------------------------------------------------------------
# AC10 — aggregator over full installed set
# ---------------------------------------------------------------------------


def test_upgrade_aggregator_runs_over_full_installed_set(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: aggregator preserves contributions from non-upgraded primitives.

    Two installed primitives (``people``, ``meeting``) contribute to the
    same ``frontmatter.schema.yaml`` regions. Bump only ``meeting``. The
    composed body must still include both contributors' snippets.
    """

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0

    _bump_primitive_version(kit_root, "meeting", "0.2.0")
    assert cli.main(["upgrade"], kit_root=kit_root) == 0

    schema = (vault / "frontmatter.schema.yaml").read_text(encoding="utf-8")
    types_block = schema.split("# BEGIN MANAGED: types\n", 1)[1].split("  # END MANAGED: types", 1)[
        0
    ]
    # ``meeting`` is the only declared content-type in this minimal kit;
    # the contract is that re-aggregation preserves the bucket (vs.
    # collapsing it to "this upgrade's contributors only", which would
    # be empty for a bump-without-content-change).
    assert "- meeting" in types_block


# ---------------------------------------------------------------------------
# AC12, AC19 — installed-but-missing-from-catalog
# ---------------------------------------------------------------------------


def test_upgrade_skips_missing_from_catalog_silently(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC12 + AC19: silent skip at journal level, stderr hint at UX level."""

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    append_event(
        _journal_path(vault),
        PrimitiveInstallEvent(
            timestamp=read_events(_journal_path(vault))[-1].timestamp,
            by="test-seed",
            primitive="gone",
            version="0.1.0",
        ),
    )
    _bump_primitive_version(kit_root, "core", "0.2.0")
    events_before = read_events(_journal_path(vault))
    capsys.readouterr()

    assert cli.main(["upgrade"], kit_root=kit_root) == 0

    captured = capsys.readouterr()
    # AC12: ``core`` upgraded, ``gone`` skipped silently (no upgrade event).
    new_events = read_events(_journal_path(vault))[len(events_before) :]
    upgrade_events = [e for e in new_events if isinstance(e, PrimitiveUpgradeEvent)]
    assert [e.primitive for e in upgrade_events] == ["core"]
    # AC19: stderr hint surfaces the missing-from-catalog count.
    assert (
        "note: 1 installed primitive no longer in the kit catalog; run `wiki doctor` for details."
    ) in captured.err


# ---------------------------------------------------------------------------
# AC14 — journal-cache scope
# ---------------------------------------------------------------------------


def test_upgrade_cache_loads_baseline_once_via_journal_reader(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC14 — narrow scope: pin the qC4 cache contract that the install-
    pipeline's baseline lookups load the journal exactly once via
    ``journal.use_journal_cache`` (mirrors the ``wiki add`` test).

    Scope caveat: this is NOT a "wiki upgrade reads the journal once"
    test. The monkeypatch intercepts ``_journal.read_events`` (the
    module attribute), so it catches only callers that resolve the
    name through that attribute — today, the cache-load inside
    ``JournalReader.events()``. The pre-scope state-replay
    (``cli.read_events`` import-time binding), the runner's
    ``length_before`` snapshot, and the runner's post-walk
    proposal-scan (both via ``upgrade.read_events`` import-time
    binding) all bypass this counter by design. What's pinned: the
    cache absorbs every ``safe_write`` / ``safe_write_region``
    baseline lookup after one cache load."""

    import llm_wiki_kit.journal as _journal

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    _bump_primitive_version(kit_root, "core", "0.2.0")
    journal_target = _journal_path(vault)

    reads = {"n": 0}
    original = _journal.read_events

    def counting_read_events(p: Path) -> object:
        if p == journal_target:
            reads["n"] += 1
        return original(p)

    monkeypatch.setattr(_journal, "read_events", counting_read_events)

    assert cli.main(["upgrade"], kit_root=kit_root) == 0
    assert reads["n"] == 1, (
        f"expected one cache-load read via JournalReader.events() across "
        f"wiki upgrade, got {reads['n']}"
    )


# ---------------------------------------------------------------------------
# AC17 — widened pre-flight on ``all_installed``
# ---------------------------------------------------------------------------


def test_upgrade_validates_unchanged_primitive_contributions(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC17: ``validate_contributions`` pre-flight covers every installed
    primitive — not just ``to_upgrade`` — so an aggregator-time failure
    on an unchanged-version primitive aborts the upgrade BEFORE the
    bumped primitive's upgrade event is journaled.
    """

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    # ``meeting`` requires ``people``; both get installed.
    assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0

    # Bump ``core``. Then break ``meeting``'s contribution shape (an
    # unchanged-version primitive whose snippets the aggregator will
    # read) by removing one of its ``regions/`` snippets.
    _bump_primitive_version(kit_root, "core", "0.2.0")
    meeting_regions = kit_root / "templates" / "content-types" / "meeting" / "regions"
    snippets = list(meeting_regions.iterdir())
    assert snippets, "fixture precondition: meeting must ship at least one regions snippet"
    snippets[0].unlink()

    events_before = read_events(_journal_path(vault))
    capsys.readouterr()

    assert cli.main(["upgrade"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT

    # AC17: the failure mode surfaces a PrimitiveError-shaped message
    # naming the missing snippet, not just an opaque exit code.
    err = capsys.readouterr().err
    assert "orphan snippet" in err or "snippet" in err, (
        f"expected PrimitiveError text about the missing/orphan snippet; got: {err!r}"
    )

    # Zero new PrimitiveUpgradeEvents (including for ``core``, which
    # would otherwise have upgraded).
    new_events = read_events(_journal_path(vault))[len(events_before) :]
    assert not any(isinstance(e, PrimitiveUpgradeEvent) for e in new_events)
