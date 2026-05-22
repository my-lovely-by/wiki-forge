"""End-to-end ``wiki add`` integration tests (RFC-0001 Task 12).

Uses the same kit-root threading pattern as
``test_wiki_init_primitives.py`` (qC8): a tmp kit holds the real
``core`` and the three Task-11 primitives, plus a minimal
``recipes/minimal.yaml`` that resolves to core-only. ``wiki init``
lays down the core vault; ``wiki add`` then layers a primitive on
top, exercising the closure walk, the installed-set filter, and the
aggregator's second pass over the *full* installed set.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.journal import read_events, replay_state
from llm_wiki_kit.models import (
    ManagedRegionWriteEvent,
    PageWriteEvent,
    PrimitiveInstallEvent,
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
        "description: Core-only recipe for wiki add tests.\n"
        "primitives:\n"
        "  - core\n"
        "variables:\n"
        "  recipe_name: minimal\n",
        encoding="utf-8",
    )
    return kit


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
# Happy paths
# ---------------------------------------------------------------------------


def test_add_installs_a_zero_requires_primitive(
    tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    events_before = read_events(_journal_path(vault))

    assert cli.main(["add", "ontology:people"], kit_root=kit_root) == 0

    # The people primitive's files/ tree lands in the expected place.
    assert (vault / "wiki" / "people" / "README.md").is_file()

    events_after = read_events(_journal_path(vault))
    new_events = events_after[len(events_before) :]

    # Exactly one PrimitiveInstall event for ``people``, attributed to wiki-add.
    install_events = [e for e in new_events if isinstance(e, PrimitiveInstallEvent)]
    assert [(e.primitive, e.by) for e in install_events] == [("people", "wiki-add")]

    # Page writes are attributed to the primitive itself, not the install vehicle.
    page_writes = [e for e in new_events if isinstance(e, PageWriteEvent)]
    assert page_writes
    for event in page_writes:
        assert event.by == "people"

    # No region writes — people declares ``contributes_to: []``.
    assert not any(isinstance(e, ManagedRegionWriteEvent) for e in new_events)


def test_add_pulls_transitive_requires_in_topological_order(
    tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``meeting`` requires ``people``; adding it should install both, in
    # the order ``primitives.resolve_dependencies`` produces.
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)

    assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0

    events = read_events(_journal_path(vault))
    install_order = [e.primitive for e in events if isinstance(e, PrimitiveInstallEvent)]
    assert install_order == ["core", "people", "meeting"]

    state = replay_state(events)
    assert set(state.installed_primitives) == {"core", "people", "meeting"}


def test_add_aggregator_runs_over_full_installed_set(
    tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The aggregator must compose every contributor's snippet — not just
    # the new primitive's — or it would clobber existing region bodies
    # to "new-only" (Task-12 design callout).
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)

    assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0

    schema = (vault / "frontmatter.schema.yaml").read_text(encoding="utf-8")
    types_block = schema.split("# BEGIN MANAGED: types\n", 1)[1].split("  # END MANAGED: types", 1)[
        0
    ]
    assert types_block == "  - meeting\n"

    events = read_events(_journal_path(vault))
    region_events = [e for e in events if isinstance(e, ManagedRegionWriteEvent)]
    # Both buckets were written by the wiki-add aggregator pass.
    assert [(e.file, e.region, e.by) for e in region_events] == [
        ("frontmatter.schema.yaml", "fields", "wiki-add"),
        ("frontmatter.schema.yaml", "types", "wiki-add"),
    ]


def test_add_event_order_install_then_pages_then_regions(
    tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    before_count = len(read_events(_journal_path(vault)))

    assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0

    events = read_events(_journal_path(vault))
    new_events = events[before_count:]

    install_indices = [i for i, e in enumerate(new_events) if isinstance(e, PrimitiveInstallEvent)]
    page_indices = [i for i, e in enumerate(new_events) if isinstance(e, PageWriteEvent)]
    region_indices = [i for i, e in enumerate(new_events) if isinstance(e, ManagedRegionWriteEvent)]

    # ADR-0006 §Mechanics step 5: files render in the first pass, region
    # aggregation runs in the second. Within the add transaction:
    assert install_indices and page_indices and region_indices
    assert max(install_indices) < max(page_indices) < min(region_indices)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_add_is_idempotent_on_rerun(
    tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)

    assert cli.main(["add", "ontology:people"], kit_root=kit_root) == 0
    events_first = read_events(_journal_path(vault))

    # Re-add: should be a clean no-op — no new events, no drift.
    assert cli.main(["add", "ontology:people"], kit_root=kit_root) == 0
    events_second = read_events(_journal_path(vault))

    assert events_second == events_first
    # Doctor sees a clean vault.
    from llm_wiki_kit.doctor import run_doctor

    assert run_doctor(vault, kit_root) == []


# ---------------------------------------------------------------------------
# Validation paths
# ---------------------------------------------------------------------------


def test_add_rejects_malformed_spec(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    capsys.readouterr()

    assert cli.main(["add", "people"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "<kind>:<name>" in err


def test_add_rejects_unknown_kind(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    capsys.readouterr()

    assert cli.main(["add", "widget:people"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "widget" in err


def test_add_rejects_wrong_kind_for_existing_primitive(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # ``people`` exists, but as an ontology, not an operation.
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    capsys.readouterr()

    assert cli.main(["add", "operation:people"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    # operation/<name> won't exist on disk; surfaces via PrimitiveError.
    assert "people" in err


def test_add_refuses_when_cwd_is_not_a_vault(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)

    assert cli.main(["add", "ontology:people"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "not a wiki vault" in err


def test_wiki_add_install_pipeline_reads_journal_once_via_cache(
    tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qC4: wiki add's install pipeline reads the journal exactly once.

    Mirrors the wiki-init pin in tests/integration/test_wiki_init.py.
    The cache scope wraps ``_cmd_add``'s install_primitives call; the
    aggregator's safe_write_region pass should not re-read the
    journal for each region.
    """
    import llm_wiki_kit.journal as _journal

    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    journal_target = _journal_path(vault)

    reads = {"n": 0}
    original = _journal.read_events

    def counting_read_events(p: Path) -> object:
        if p == journal_target:
            reads["n"] += 1
        return original(p)

    monkeypatch.setattr(_journal, "read_events", counting_read_events)

    assert cli.main(["add", "content-type:meeting"], kit_root=kit_root) == 0
    # Cache absorbs every baseline lookup after the first load.
    #
    # NOTE: ``_cmd_add`` calls ``replay_state(read_events(...))`` once
    # BEFORE entering the cache scope (for the recipe / installed-set
    # lookup). That pre-scope call goes through ``cli.read_events``
    # — the import-time binding made via ``from llm_wiki_kit.journal
    # import read_events`` — which the monkeypatch above does NOT
    # intercept (we patch ``_journal.read_events``, the module
    # attribute; ``cli.read_events`` still points at the original).
    # So this assertion pins only the cache-load-once contract; the
    # pre-scope read is invisible to the counter by design. If a
    # future refactor changes the import to ``journal.read_events``
    # (via the module), this comment needs revisiting.
    assert reads["n"] == 1, (
        f"expected one cache-load read of the vault journal across wiki add, got {reads['n']}"
    )


def test_wiki_add_over_unjournaled_user_file_proposes_not_overwrites(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """qC6: ``wiki add`` over an unjournaled user file proposes, doesn't overwrite.

    spec.md §Behavior "Drift path" sub-case (b): the user dropped a
    markdown file at a path a primitive will render to. The kit must
    not silently overwrite — write ``.proposed`` and leave the user
    file untouched.
    """
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)

    # Drop a user file at a path ``add ontology:people`` will render to.
    user_path = vault / "wiki" / "people" / "README.md"
    user_path.parent.mkdir(parents=True)
    user_content = "user's pre-existing notes about people\n"
    user_path.write_text(user_content, encoding="utf-8")

    assert cli.main(["add", "ontology:people"], kit_root=kit_root) == 0

    # User file untouched.
    assert user_path.read_text(encoding="utf-8") == user_content
    # Sidecar carries the kit's intended content.
    sidecar = vault / "wiki" / "people" / "README.md.proposed"
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8") != user_content

    # Journal records a proposal, not a page-write, for that path.
    events = read_events(_journal_path(vault))
    page_writes_to_user = [
        e for e in events if isinstance(e, PageWriteEvent) and e.path == "wiki/people/README.md"
    ]
    assert page_writes_to_user == []
    proposals_to_user = [
        e
        for e in events
        if e.__class__.__name__ == "PageProposalEvent"
        and getattr(e, "path", None) == "wiki/people/README.md"
    ]
    assert proposals_to_user, "expected a PageProposalEvent for the user-owned path"
