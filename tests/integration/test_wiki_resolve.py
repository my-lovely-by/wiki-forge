"""End-to-end ``wiki resolve`` integration tests (retro-review #F-B2).

The vault-side ``wiki-conflict`` SKILL.md drives the user through merging
a ``.proposed`` sidecar with their on-disk edit and commits the merge
via ``wiki resolve <path>`` (optionally ``--keep`` / ``--accept``, or
stdin for a custom merge). Before this PR the subcommand didn't exist —
the skill called ``wiki resolve`` and argparse refused.

Vault construction reuses the ``test_wiki_doctor`` ``_install_kit``
pattern. Each test drives the vault into the conflict state by editing
a core-rendered page and ``safe_write``-ing a kit update, then exercises
one path through ``wiki resolve``.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import PageConflictResolvedEvent, PageWriteEvent
from llm_wiki_kit.write_helper import safe_write

REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_kit(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    (kit / "templates").mkdir()
    recipes_dir = kit / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "minimal.yaml").write_text(
        "name: minimal\n"
        "version: 0.1.0\n"
        "description: Core-only recipe for wiki resolve tests.\n"
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


def _init_vault(tmp_path: Path, kit_root: Path) -> Path:
    vault = tmp_path / "v"
    assert cli.main(["init", str(vault), "--recipe", "minimal"], kit_root=kit_root) == 0
    return vault


def _drive_to_proposal(vault: Path) -> tuple[Path, Path]:
    """Edit a kit-written file then `safe_write` a kit update to produce a sidecar."""

    target = vault / "page.md"
    journal = vault / ".wiki.journal" / "journal.jsonl"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits", encoding="utf-8")
    safe_write(target, "v2", by="core", journal_path=journal)
    sidecar = target.with_name(target.name + ".proposed")
    assert sidecar.is_file(), "precondition: sidecar must exist"
    return target, sidecar


def test_resolve_subparser_is_registered(
    kit_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression for retro-review #F-B2: `wiki resolve` was not a known subcommand.

    Driven through ``--help`` rather than introspecting ``_subparsers``
    so the test doesn't reach into argparse's private API.
    """

    with pytest.raises(SystemExit) as exc:
        cli.main(["resolve", "--help"])
    assert exc.value.code == 0
    assert "resolve" in capsys.readouterr().out


def test_resolve_accept_writes_proposed_content_and_journals_events(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    target, sidecar = _drive_to_proposal(vault)

    assert cli.main(["resolve", "page.md", "--accept"]) == 0
    assert target.read_text(encoding="utf-8") == "v2"
    assert not sidecar.exists()

    events = read_events(vault / ".wiki.journal" / "journal.jsonl")
    assert isinstance(events[-2], PageWriteEvent)
    assert isinstance(events[-1], PageConflictResolvedEvent)
    assert events[-1].by == "wiki-conflict"


def test_resolve_keep_re_baselines_to_user_edits(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    target, sidecar = _drive_to_proposal(vault)

    assert cli.main(["resolve", "page.md", "--keep"]) == 0
    assert target.read_text(encoding="utf-8") == "user edits"
    assert not sidecar.exists()


def test_resolve_stdin_writes_merged_content(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    target, _sidecar = _drive_to_proposal(vault)
    monkeypatch.setattr("sys.stdin", io.StringIO("user-merged version"))

    assert cli.main(["resolve", "page.md"]) == 0
    assert target.read_text(encoding="utf-8") == "user-merged version"


def test_resolve_accept_without_sidecar_errors(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    (vault / "page.md").write_text("standalone", encoding="utf-8")

    rc = cli.main(["resolve", "page.md", "--accept"])
    assert rc == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "no .proposed sidecar" in err


def test_resolve_outside_vault_errors(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "not-a-vault"
    outside.mkdir()
    monkeypatch.chdir(outside)

    rc = cli.main(["resolve", "page.md", "--keep"])
    assert rc == cli.WIKI_ERROR_EXIT
    assert "not a wiki vault" in capsys.readouterr().err


def test_resolve_keep_and_accept_are_mutually_exclusive(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    _drive_to_proposal(vault)

    with pytest.raises(SystemExit):
        cli.main(["resolve", "page.md", "--keep", "--accept"])
