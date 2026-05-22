"""End-to-end ``wiki init`` tests for the Task-11 primitives.

The Task-10 suite (``test_wiki_init.py``) verifies the core-only render
against the real shipped recipes. This suite covers the next step: a
recipe that pulls in the three Task-11 primitives (``people``,
``meeting``, ``weekly-digest``) and verifies the install pipeline's
second pass — region aggregation per ADR-0006.

The shipped recipes (``family``, ``work-os``, ``personal``) keep their
``primitives: [core]`` listing in this task; Tasks 13/14/15 expand
them. To drive the new primitives through ``wiki init`` without
mutating the shipped recipes, the tests pass an explicit ``kit_root``
into ``cli.main`` (qC8) pointing at a tmp directory with a custom
``recipes/full.yaml``. This is the same shape an end user would get
if they wrote their own recipe.
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
    VaultInitEvent,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_kit(tmp_path: Path) -> Path:
    """Build a tmp kit directory with the real core + Task-11 primitives.

    Returns the kit root that tests pass via ``cli.main(argv, kit_root=...)``.
    The kit's bundled ``recipes/`` is recreated with one test recipe
    (``full.yaml``) that lists all three Task-11 primitives plus
    ``core`` (resolved transitively by the recipe loader).
    """

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
    (recipes_dir / "full.yaml").write_text(
        "name: full\n"
        "version: 0.1.0\n"
        "description: Test recipe pulling in every Task-11 primitive.\n"
        "primitives:\n"
        "  - people\n"
        "  - meeting\n"
        "  - weekly-digest\n"
        "variables:\n"
        "  recipe_name: full\n",
        encoding="utf-8",
    )
    return kit


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    return _install_kit(tmp_path)


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def test_init_renders_three_primitives_into_vault(tmp_path: Path, kit_root: Path) -> None:
    vault = tmp_path / "v"

    assert cli.main(["init", str(vault), "--recipe", "full"], kit_root=kit_root) == 0

    # Each primitive's ``files/`` tree lands in the expected place.
    assert (vault / "wiki" / "people" / "README.md").is_file()
    assert (vault / "wiki" / "meetings" / "README.md").is_file()
    assert (vault / "_templates" / "meeting.md").is_file()
    assert (vault / "skills" / "ingest-meeting" / "SKILL.md").is_file()
    assert (vault / "skills" / "weekly-digest" / "SKILL.md").is_file()
    # Core's six skills are still present.
    for core_skill in ("ingest", "wiki-search", "wiki-conflict"):
        assert (vault / "skills" / core_skill / "SKILL.md").is_file()


def test_init_aggregates_meeting_into_schema_regions(tmp_path: Path, kit_root: Path) -> None:
    vault = tmp_path / "v"

    assert cli.main(["init", str(vault), "--recipe", "full"], kit_root=kit_root) == 0

    schema = (vault / "frontmatter.schema.yaml").read_text(encoding="utf-8")
    # The ``types`` region's seed body is replaced by ``- meeting``.
    types_block = schema.split("# BEGIN MANAGED: types\n", 1)[1].split("  # END MANAGED: types", 1)[
        0
    ]
    assert types_block == "  - meeting\n"
    assert "Populated by content-type primitives" not in types_block

    # The ``fields`` region holds the meeting-scoped fields.
    fields_block = schema.split("# BEGIN MANAGED: fields\n", 1)[1].split(
        "  # END MANAGED: fields", 1
    )[0]
    assert "meeting_date:" in fields_block
    assert "meeting_attendees:" in fields_block


def test_init_journal_order_with_three_primitives(tmp_path: Path, kit_root: Path) -> None:
    vault = tmp_path / "v"
    assert cli.main(["init", str(vault), "--recipe", "full"], kit_root=kit_root) == 0

    events = read_events(_journal_path(vault))

    # (1) VaultInit first.
    assert isinstance(events[0], VaultInitEvent)
    assert events[0].recipe == "full"

    # (2) PrimitiveInstall events for every primitive in topological
    # order. ``core`` is always first (always-include-core policy).
    # ``people`` has no requires, ``meeting`` requires people,
    # ``weekly-digest`` requires meeting — so the topo order is:
    # core, people, meeting, weekly-digest.
    install_events = [e for e in events if isinstance(e, PrimitiveInstallEvent)]
    assert [e.primitive for e in install_events] == [
        "core",
        "people",
        "meeting",
        "weekly-digest",
    ]

    # (3) ManagedRegionWriteEvents land *after* all PageWriteEvents.
    # ADR-0006 §Mechanics step 5: files/ render first, region
    # aggregation runs in a second pass.
    last_page_write = max(i for i, e in enumerate(events) if isinstance(e, PageWriteEvent))
    first_region_write = min(
        i for i, e in enumerate(events) if isinstance(e, ManagedRegionWriteEvent)
    )
    assert last_page_write < first_region_write

    # (4) Two region writes: fields then types (alphabetical bucket order).
    region_events = [e for e in events if isinstance(e, ManagedRegionWriteEvent)]
    assert [(e.file, e.region) for e in region_events] == [
        ("frontmatter.schema.yaml", "fields"),
        ("frontmatter.schema.yaml", "types"),
    ]

    # (5) Replayed state lists all four primitives.
    state = replay_state(events)
    assert set(state.installed_primitives) == {"core", "people", "meeting", "weekly-digest"}


def test_init_fails_loudly_on_missing_snippet(
    tmp_path: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Remove meeting's ``types`` snippet to provoke the validator
    # before any state write happens.
    snippet = (
        kit_root
        / "templates"
        / "content-types"
        / "meeting"
        / "regions"
        / ("frontmatter.schema.yaml.types")
    )
    snippet.unlink()

    vault = tmp_path / "v"
    exit_code = cli.main(["init", str(vault), "--recipe", "full"], kit_root=kit_root)

    assert exit_code == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "meeting" in err
    assert "missing" in err
    # No partial vault, no journal — validator runs before mkdir.
    assert not vault.exists()


def test_init_fails_loudly_on_orphan_snippet(
    tmp_path: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Drop a stray snippet into meeting's regions/ with no matching
    # contributes_to entry.
    orphan = (
        kit_root
        / "templates"
        / "content-types"
        / "meeting"
        / "regions"
        / ("AGENTS.md.installed-skills")
    )
    orphan.write_text("stray\n", encoding="utf-8")

    vault = tmp_path / "v"
    exit_code = cli.main(["init", str(vault), "--recipe", "full"], kit_root=kit_root)

    assert exit_code == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "orphan" in err
    assert not vault.exists()
