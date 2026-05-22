"""End-to-end ``wiki search`` integration tests.

Drives the CLI against a tmp kit + tmp vault and asserts on the
rendered stdout, exit code, and journal-quiescence invariant. The
``rg`` binary is invoked for real; module-level ``skipif`` skips the
suite where it isn't installed (the unit-level PATH-missing test
covers that branch separately).

See ``docs/specs/wiki-search/spec.md``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.journal import read_events

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("rg") is None,
    reason="ripgrep (rg) not installed; install via your OS package manager",
)


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    shutil.copytree(REPO_ROOT / "templates", kit / "templates")

    recipes_dir = kit / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "minimal.yaml").write_text(
        "name: minimal\n"
        "version: 0.1.0\n"
        "description: Core only — search exercises pages we write directly.\n"
        "primitives: []\n"
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


def _write_page(vault: Path, rel: str, frontmatter: str, body: str) -> None:
    path = vault / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"---\n{frontmatter}\n---\n{body}" if frontmatter else body
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Boundary errors
# ---------------------------------------------------------------------------


def test_wiki_search_no_vault_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli.main(["search", "anything"]) == cli.WIKI_ERROR_EXIT

    err = capsys.readouterr().err
    assert "not a wiki vault" in err


def test_wiki_search_empty_query_exits_2(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", ""]) == cli.WIKI_ERROR_EXIT

    err = capsys.readouterr().err
    assert "search query must not be empty" in err


def test_wiki_search_top_zero_exits_2(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", "q", "--top", "0"]) == cli.WIKI_ERROR_EXIT

    err = capsys.readouterr().err
    assert "--top must be ≥ 1" in err


@pytest.mark.parametrize(
    "flag,expected_err",
    [
        ("--type", "--type must not be empty"),
        ("--tag", "--tag must not be empty"),
        ("--status", "--status must not be empty"),
    ],
)
def test_wiki_search_empty_filter_value_exits_2(
    vault: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    expected_err: str,
) -> None:
    """AC13: empty filter values are rejected at the CLI boundary."""

    assert cli.main(["search", "q", flag, ""]) == cli.WIKI_ERROR_EXIT

    err = capsys.readouterr().err
    assert expected_err in err


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_wiki_search_empty_wiki_prints_no_matches(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A vault with no ``wiki/`` tree is a legitimate fresh state."""

    assert cli.main(["search", "kafka"]) == 0

    out = capsys.readouterr().out
    assert out == "no matches.\n"


def test_wiki_search_ranks_and_renders_results(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_page(vault, "a.md", "type: meeting\nstatus: active", "# Alpha\nstakeholder")
    _write_page(vault, "b.md", "type: meeting\ntags: [urgent]", "# Beta\nstakeholder stakeholder")

    assert cli.main(["search", "stakeholder"]) == 0

    out = capsys.readouterr().out
    # b.md comes first (2 matches) then a.md (1 match).
    assert "## Beta — wiki/b.md" in out
    assert "## Alpha — wiki/a.md" in out
    assert out.index("Beta") < out.index("Alpha")
    assert "- type: meeting" in out
    assert "- matches: 2" in out
    assert "- matches: 1" in out


def test_wiki_search_type_filter_excludes_other_types(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_page(vault, "m.md", "type: meeting", "# M\nstakeholder")
    _write_page(vault, "i.md", "type: interview", "# I\nstakeholder")

    assert cli.main(["search", "stakeholder", "--type", "meeting"]) == 0

    out = capsys.readouterr().out
    assert "wiki/m.md" in out
    assert "wiki/i.md" not in out


def test_wiki_search_is_read_only_journal_unchanged(vault: Path) -> None:
    """Invariant 1 / AC11: search appends no events on success."""

    _write_page(vault, "a.md", "", "# A\nstakeholder")

    pre = len(read_events(_journal_path(vault)))
    assert cli.main(["search", "stakeholder"]) == 0
    assert cli.main(["search", "no-match"]) == 0
    post = len(read_events(_journal_path(vault)))

    assert pre == post


def test_wiki_search_boundary_errors_leave_journal_untouched(vault: Path) -> None:
    """AC11 (boundary-error branch): every error path is read-only too.

    A future regression that journaled a "search attempted" event from
    any error path — empty query, bad top, empty filter — would land
    silent without this assertion. The "no wiki vault" branch is
    covered separately because it returns before there's a journal at
    all to write to.
    """

    _write_page(vault, "a.md", "", "# A\nstakeholder")
    pre = len(read_events(_journal_path(vault)))

    assert cli.main(["search", ""]) == cli.WIKI_ERROR_EXIT
    assert cli.main(["search", "q", "--top", "0"]) == cli.WIKI_ERROR_EXIT
    assert cli.main(["search", "q", "--type", ""]) == cli.WIKI_ERROR_EXIT
    assert cli.main(["search", "q", "--tag", ""]) == cli.WIKI_ERROR_EXIT
    assert cli.main(["search", "q", "--status", ""]) == cli.WIKI_ERROR_EXIT

    assert len(read_events(_journal_path(vault))) == pre


def test_wiki_search_title_skips_h1_in_code_fence(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC14: a `# ` line inside a ``` block is not picked as the title."""

    _write_page(
        vault,
        "p.md",
        "",
        "```python\n# fake title in a comment\nprint('hi')\n```\n# Real Title\nstakeholder body\n",
    )

    assert cli.main(["search", "stakeholder"]) == 0

    out = capsys.readouterr().out
    assert "## Real Title — wiki/p.md" in out
    assert "## fake title" not in out


def test_wiki_search_scans_gitignored_pages(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`.gitignore` does not hide vault pages; the journal is authoritative."""

    _write_page(vault, "kept.md", "", "# Kept\nstakeholder")
    _write_page(vault, "drafts/draft.md", "", "# Draft\nstakeholder")
    (vault / "wiki" / ".gitignore").write_text("drafts/\n", encoding="utf-8")

    assert cli.main(["search", "stakeholder"]) == 0

    out = capsys.readouterr().out
    assert "wiki/kept.md" in out
    assert "wiki/drafts/draft.md" in out


def test_wiki_search_deterministic_across_invocations(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invariant 2: output is byte-identical for a fixed state."""

    _write_page(vault, "a.md", "", "# A\nstakeholder")
    _write_page(vault, "b.md", "", "# B\nstakeholder")

    assert cli.main(["search", "stakeholder"]) == 0
    first = capsys.readouterr().out

    assert cli.main(["search", "stakeholder"]) == 0
    second = capsys.readouterr().out

    assert first == second
