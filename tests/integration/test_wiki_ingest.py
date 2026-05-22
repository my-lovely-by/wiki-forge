"""End-to-end ``wiki ingest`` integration tests (RFC-0001 Task 16).

Uses the tmp-kit threading pattern from ``test_wiki_add.py`` (qC8):
a tmp kit holds the real ``core`` plus a small set of
content-type primitives whose ``routing:`` blocks are pinned in this
file. Pinning the routing surface here (rather than asserting against
the bundled family-recipe primitives) keeps the test stable when later
tasks tune the shipped primitives' routing rules — Task 16's
correctness contract is "the CLI does what the routing config says,"
not "the bundled config says exactly X."

The matrix covered:

* single-match route (URL → recipe; filename → medical-record)
* ambiguous route (two primitives whose rules both fire on one source)
* no-match route (a source nothing claims)
* ``--as`` override (skips detection)
* not-a-vault failure (no journal → ``WikiError`` exit code)
* unknown ``--as`` target (``WikiError`` exit code)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import IngestRoutedEvent

REPO_ROOT = Path(__file__).resolve().parents[2]


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _write_content_type(
    templates_dir: Path,
    name: str,
    routing_yaml: str | None,
    *,
    requires: list[str] | None = None,
) -> None:
    """Drop a minimal content-type primitive directory into ``templates_dir``.

    Each primitive declares no ``contributes_to`` and ships no files, so
    ``install_primitives`` walks past it cleanly. The only field that
    matters for these tests is the optional ``routing:`` block.
    """

    target = templates_dir / "content-types" / name
    target.mkdir(parents=True)
    requires_block = ""
    if requires:
        requires_block = "requires:\n" + "".join(f"  - {dep}\n" for dep in requires)
    manifest = (
        f"name: {name}\n"
        "kind: content-type\n"
        "version: 0.1.0\n"
        f"description: Test content-type primitive {name}.\n"
        f"{requires_block}"
    )
    if routing_yaml is not None:
        manifest += routing_yaml
    (target / "primitive.yaml").write_text(manifest, encoding="utf-8")
    (target / "files").mkdir()


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    (kit / "templates").mkdir()

    # ``recipe`` — URL-host + URL-path routing.
    _write_content_type(
        kit / "templates",
        "recipe",
        "routing:\n"
        "  url_domains:\n"
        "    - allrecipes.com\n"
        "    - '*.bonappetit.com'\n"
        "  url_path_patterns:\n"
        "    - /recipe/*\n",
    )

    # ``medical-record`` — filename-glob routing only.
    _write_content_type(
        kit / "templates",
        "medical-record",
        "routing:\n  filename_patterns:\n    - 'EOB-*'\n    - '*visit-summary*'\n",
    )

    # ``receipt`` — filename + extension routing. Its ``.pdf`` claim is
    # what makes ``EOB-2026.pdf`` collide with ``medical-record`` below.
    _write_content_type(
        kit / "templates",
        "receipt",
        "routing:\n  filename_patterns:\n    - '*receipt*'\n  file_extensions:\n    - '.pdf'\n",
    )

    # ``interview`` — declares no routing block; reachable only via --as.
    _write_content_type(kit / "templates", "interview", None)

    recipes_dir = kit / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "minimal.yaml").write_text(
        "name: minimal\n"
        "version: 0.1.0\n"
        "description: Core + content-types for ingest routing tests.\n"
        "primitives:\n"
        "  - recipe\n"
        "  - medical-record\n"
        "  - receipt\n"
        "  - interview\n"
        "variables:\n"
        "  recipe_name: minimal\n",
        encoding="utf-8",
    )

    return kit


@pytest.fixture
def vault(tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "vault"
    assert cli.main(["init", str(v), "--recipe", "minimal"], kit_root=kit_root) == 0
    monkeypatch.chdir(v)
    return v


def _latest_routed(vault: Path) -> IngestRoutedEvent:
    events = read_events(_journal_path(vault))
    routed = [e for e in events if isinstance(e, IngestRoutedEvent)]
    assert routed, "expected at least one ingest.routed event in the journal"
    return routed[-1]


# ---------------------------------------------------------------------------
# Happy paths — single match
# ---------------------------------------------------------------------------


def test_ingest_routes_recipe_url_to_recipe_primitive(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(
        ["ingest", "https://allrecipes.com/recipe/sheet-pan-tacos"], kit_root=kit_root
    )
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "content-type:recipe" in out
    assert "ingest-recipe" in out

    event = _latest_routed(vault)
    assert event.content_type == "recipe"
    assert event.via == "auto"
    assert event.by == "wiki-ingest"
    assert any(s.startswith("url_domain:") for s in event.signals)


def test_ingest_routes_filename_pattern_to_medical_record(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["ingest", "EOB-2026-04-15-jake.txt"], kit_root=kit_root)
    assert exit_code == 0
    assert "content-type:medical-record" in capsys.readouterr().out

    event = _latest_routed(vault)
    assert event.content_type == "medical-record"
    assert "filename_pattern:EOB-*" in event.signals


# ---------------------------------------------------------------------------
# Ambiguity
# ---------------------------------------------------------------------------


def test_ingest_ambiguous_returns_exit_2_and_lists_candidates(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ``EOB-2026.pdf`` matches ``medical-record`` (filename pattern EOB-*)
    # and ``receipt`` (file extension .pdf). Both fire; orchestrator
    # refuses to pick.
    exit_code = cli.main(["ingest", "EOB-2026-04-15.pdf"], kit_root=kit_root)
    assert exit_code == cli.WIKI_ERROR_EXIT or exit_code == cli.INGEST_ROUTE_FAILED_EXIT

    err = capsys.readouterr().err
    assert "medical-record" in err
    assert "receipt" in err
    assert "--as" in err

    event = _latest_routed(vault)
    assert event.content_type is None
    assert event.candidates == ["medical-record", "receipt"]
    assert event.via == "auto"


# ---------------------------------------------------------------------------
# No match
# ---------------------------------------------------------------------------


def test_ingest_no_match_returns_exit_2_and_lists_available(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["ingest", "/tmp/mystery.bin"], kit_root=kit_root)
    assert exit_code == cli.INGEST_ROUTE_FAILED_EXIT

    err = capsys.readouterr().err
    assert "No content-type matched" in err
    assert "interview" in err  # available content-types listed

    event = _latest_routed(vault)
    assert event.content_type is None
    assert event.candidates == []


# ---------------------------------------------------------------------------
# --as override
# ---------------------------------------------------------------------------


def test_ingest_as_override_bypasses_detection(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Source is a URL that *would* auto-route to ``recipe``; --as forces
    # ``interview`` instead, even though interview has no routing block.
    exit_code = cli.main(
        ["ingest", "https://allrecipes.com/recipe/sheet-pan-tacos", "--as", "interview"],
        kit_root=kit_root,
    )
    assert exit_code == 0
    assert "content-type:interview" in capsys.readouterr().out

    event = _latest_routed(vault)
    assert event.content_type == "interview"
    assert event.via == "as_flag"
    assert event.signals == []


def test_ingest_as_override_for_unknown_primitive_is_wiki_error(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["ingest", "anything.txt", "--as", "nonexistent"], kit_root=kit_root)
    assert exit_code == cli.WIKI_ERROR_EXIT
    assert "nonexistent" in capsys.readouterr().err

    # Failed --as is a kit-side error, not a route — no event is appended.
    events = read_events(_journal_path(vault))
    assert not any(isinstance(e, IngestRoutedEvent) for e in events)


# ---------------------------------------------------------------------------
# Pre-flight: must be a vault
# ---------------------------------------------------------------------------


def test_ingest_outside_a_vault_is_wiki_error(
    tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_a_vault = tmp_path / "elsewhere"
    not_a_vault.mkdir()
    monkeypatch.chdir(not_a_vault)
    assert cli.main(["ingest", "foo.txt"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT
