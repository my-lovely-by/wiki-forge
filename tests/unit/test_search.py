"""Unit tests for ``llm_wiki_kit.search`` (construction tests).

Contract tests for the spec live alongside the integration suite at
``tests/integration/test_wiki_search.py``; these cover the internals
of ``search.run_search`` and ``search.format_results``. The real ``rg``
binary is invoked when available (CI carries it; macOS dev boxes have
it via Homebrew); a module-level ``skipif`` gates the real-binary
tests so a missing ``rg`` skips rather than fails. The PATH-missing
test monkeypatches ``shutil.which`` and therefore runs regardless of
the host.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.search import (
    SearchFilters,
    SearchHit,
    _coerce_tags,
    _filters_match,
    _parse_match_counts,
    _read_page_metadata,
    format_results,
    run_search,
)

_HAS_RG = shutil.which("rg") is not None

rg_required = pytest.mark.skipif(
    not _HAS_RG, reason="ripgrep (rg) not installed; install via your OS package manager"
)


def _write_page(vault: Path, rel: str, frontmatter: str, body: str) -> Path:
    """Materialize a wiki page with the given frontmatter + body."""

    path = vault / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    content = ""
    if frontmatter:
        content += f"---\n{frontmatter}\n---\n"
    content += body
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# run_search — happy paths
# ---------------------------------------------------------------------------


@rg_required
def test_run_search_empty_vault_returns_no_hits(tmp_path: Path) -> None:
    """No ``wiki/`` directory at all → empty result, not an error."""

    assert run_search(tmp_path, "kafka", SearchFilters(), top=10) == []


@rg_required
def test_run_search_ranks_by_match_count_desc_then_path(tmp_path: Path) -> None:
    _write_page(tmp_path, "a.md", "type: meeting", "# A\nkafka here")
    _write_page(tmp_path, "b.md", "type: meeting", "# B\nkafka kafka")
    _write_page(tmp_path, "c.md", "type: meeting", "# C\nno match here")

    hits = run_search(tmp_path, "kafka", SearchFilters(), top=10)

    assert [h.path for h in hits] == ["wiki/b.md", "wiki/a.md"]
    assert hits[0].match_count == 2
    assert hits[1].match_count == 1


@rg_required
def test_run_search_top_caps_result_count(tmp_path: Path) -> None:
    for name in ("a.md", "b.md", "c.md"):
        _write_page(tmp_path, name, "", f"# {name}\nkafka")

    hits = run_search(tmp_path, "kafka", SearchFilters(), top=2)

    assert len(hits) == 2


@rg_required
def test_run_search_zero_matches_returns_empty(tmp_path: Path) -> None:
    _write_page(tmp_path, "a.md", "", "# A\njust some prose")

    assert run_search(tmp_path, "nothing-here", SearchFilters(), top=10) == []


@rg_required
def test_run_search_lexical_only_no_stemming(tmp_path: Path) -> None:
    """``run`` must not match ``running`` — invariant 3."""

    _write_page(tmp_path, "a.md", "", "# A\nthe weekly digest is running")

    hits_run = run_search(tmp_path, "running", SearchFilters(), top=10)
    hits_strict = run_search(tmp_path, "runs", SearchFilters(), top=10)

    assert [h.path for h in hits_run] == ["wiki/a.md"]
    assert hits_strict == []


# ---------------------------------------------------------------------------
# run_search — frontmatter filters
# ---------------------------------------------------------------------------


@rg_required
def test_run_search_filter_by_type_drops_non_matches(tmp_path: Path) -> None:
    _write_page(tmp_path, "m.md", "type: meeting", "# M\nstakeholder")
    _write_page(tmp_path, "i.md", "type: interview", "# I\nstakeholder")

    hits = run_search(tmp_path, "stakeholder", SearchFilters(type="meeting"), top=10)

    assert [h.path for h in hits] == ["wiki/m.md"]


@rg_required
def test_run_search_filter_by_tag_drops_pages_missing_tag(tmp_path: Path) -> None:
    _write_page(tmp_path, "a.md", "tags: [urgent, q4]", "# A\nstakeholder")
    _write_page(tmp_path, "b.md", "tags: [q4]", "# B\nstakeholder")

    hits = run_search(tmp_path, "stakeholder", SearchFilters(tag="urgent"), top=10)

    assert [h.path for h in hits] == ["wiki/a.md"]


@rg_required
def test_run_search_filter_by_status(tmp_path: Path) -> None:
    _write_page(tmp_path, "a.md", "status: active", "# A\nstakeholder")
    _write_page(tmp_path, "b.md", "status: archived", "# B\nstakeholder")

    hits = run_search(tmp_path, "stakeholder", SearchFilters(status="active"), top=10)

    assert [h.path for h in hits] == ["wiki/a.md"]


@rg_required
def test_run_search_tag_as_bare_string_coerces_to_list(tmp_path: Path) -> None:
    """Obsidian-style ``tags: urgent`` (string, not list) still matches."""

    _write_page(tmp_path, "a.md", "tags: urgent", "# A\nstakeholder")

    hits = run_search(tmp_path, "stakeholder", SearchFilters(tag="urgent"), top=10)

    assert [h.path for h in hits] == ["wiki/a.md"]


@rg_required
def test_run_search_malformed_frontmatter_still_returns_hit(tmp_path: Path) -> None:
    """Spec edge case: bad YAML degrades to blank metadata, doesn't raise."""

    page = tmp_path / "wiki" / "broken.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\nthis : is : not : yaml :::\n---\n# Broken\nstakeholder match\n",
        encoding="utf-8",
    )

    hits = run_search(tmp_path, "stakeholder", SearchFilters(), top=10)

    assert len(hits) == 1
    assert hits[0].path == "wiki/broken.md"
    assert hits[0].type == ""
    assert hits[0].status == ""
    assert hits[0].tags == []


# ---------------------------------------------------------------------------
# run_search — boundary errors
# ---------------------------------------------------------------------------


def test_run_search_rg_missing_raises_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``rg``-not-on-PATH path runs even where ``rg`` is installed."""

    monkeypatch.setattr("llm_wiki_kit.search.shutil.which", lambda _name: None)
    (tmp_path / "wiki").mkdir()

    with pytest.raises(WikiError) as excinfo:
        run_search(tmp_path, "anything", SearchFilters(), top=10)

    msg = str(excinfo.value)
    assert "ripgrep (rg) not found" in msg
    assert "brew install ripgrep" in msg


def test_run_search_timeout_raises_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wedged ``rg`` subprocess surfaces as a ``WikiError``, not a hang.

    Runs everywhere — including CI hosts without ``rg`` installed — by
    patching ``shutil.which`` to a fake path before the subprocess hook
    intercepts the call. The PATH probe and the subprocess.run call
    are independent boundaries; this test exercises the latter.
    """

    (tmp_path / "wiki").mkdir()

    monkeypatch.setattr("llm_wiki_kit.search.shutil.which", lambda _name: "/fake/rg")

    def _boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="rg", timeout=60)

    monkeypatch.setattr(subprocess, "run", _boom)

    with pytest.raises(WikiError) as excinfo:
        run_search(tmp_path, "anything", SearchFilters(), top=10)

    msg = str(excinfo.value)
    assert "exceeded" in msg
    assert "slow or unresponsive filesystem" in msg


def test_run_search_non_utf8_file_still_appears_in_results(
    tmp_path: Path,
) -> None:
    """End-to-end: a non-UTF-8 page matched by ripgrep renders with the
    filename stem as title and empty metadata. The unit-level test for
    ``_read_page_metadata`` covers the helper; this test covers the
    ``title or abs_path.stem`` fallback wiring in ``run_search``.

    Uses Latin-1 (high-bit) bytes around an ASCII query — ripgrep matches
    the substring fine, but ``read_text(encoding='utf-8')`` raises, so
    the metadata pass returns ``("", {})`` and the filename stem becomes
    the title.
    """

    if not _HAS_RG:
        pytest.skip("rg required")

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "latin1.md").write_bytes(b"caf\xe9 stakeholder s\xfar\n")

    hits = run_search(tmp_path, "stakeholder", SearchFilters(), top=10)

    assert len(hits) == 1
    assert hits[0].path == "wiki/latin1.md"
    assert hits[0].title == "latin1"
    assert hits[0].type == ""
    assert hits[0].status == ""
    assert hits[0].tags == []


def test_run_search_non_utf8_dropped_by_type_filter(
    tmp_path: Path,
) -> None:
    """A non-UTF-8 file's empty frontmatter fails any active filter."""

    if not _HAS_RG:
        pytest.skip("rg required")

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "latin1.md").write_bytes(b"caf\xe9 stakeholder s\xfar\n")

    hits = run_search(tmp_path, "stakeholder", SearchFilters(type="meeting"), top=10)

    assert hits == []


# ---------------------------------------------------------------------------
# format_results
# ---------------------------------------------------------------------------


def test_format_results_empty_prints_no_matches() -> None:
    assert format_results([]) == "no matches.\n"


def test_format_results_renders_block_per_hit() -> None:
    hits = [
        SearchHit(
            path="wiki/a.md",
            title="A",
            type="meeting",
            status="active",
            tags=["urgent", "q4"],
            match_count=3,
        ),
        SearchHit(
            path="wiki/b.md",
            title="B",
            type="meeting",
            status="",
            tags=[],
            match_count=1,
        ),
    ]
    expected = (
        "## A — wiki/a.md\n"
        "- type: meeting\n"
        "- status: active\n"
        "- tags: urgent, q4\n"
        "- matches: 3\n"
        "\n"
        "## B — wiki/b.md\n"
        "- type: meeting\n"
        "- status: \n"
        "- tags: \n"
        "- matches: 1\n"
    )

    assert format_results(hits) == expected


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def test_parse_match_counts_extracts_end_records() -> None:
    """Only ``type: "end"`` records carry per-file aggregates."""

    stdout = (
        '{"type":"begin","data":{"path":{"text":"wiki/a.md"}}}\n'
        '{"type":"match","data":{"path":{"text":"wiki/a.md"}}}\n'
        '{"type":"end","data":{"path":{"text":"wiki/a.md"},"stats":{"matches":2}}}\n'
        '{"type":"summary","data":{"stats":{"matches":2}}}\n'
    )
    assert _parse_match_counts(stdout) == {"wiki/a.md": 2}


def test_parse_match_counts_skips_zero_matches() -> None:
    stdout = '{"type":"end","data":{"path":{"text":"wiki/a.md"},"stats":{"matches":0}}}\n'
    assert _parse_match_counts(stdout) == {}


def test_parse_match_counts_skips_malformed_json() -> None:
    stdout = (
        "not-json\n" + '{"type":"end","data":{"path":{"text":"wiki/a.md"},"stats":{"matches":1}}}\n'
    )
    assert _parse_match_counts(stdout) == {"wiki/a.md": 1}


def test_read_page_metadata_returns_title_and_frontmatter(tmp_path: Path) -> None:
    page = tmp_path / "p.md"
    page.write_text(
        "---\ntype: meeting\nstatus: active\ntags: [a, b]\n---\n# The Title\nbody\n",
        encoding="utf-8",
    )

    title, fm = _read_page_metadata(page)

    assert title == "The Title"
    assert fm == {"type": "meeting", "status": "active", "tags": ["a", "b"]}


def test_read_page_metadata_no_frontmatter(tmp_path: Path) -> None:
    page = tmp_path / "p.md"
    page.write_text("# Naked Title\nbody\n", encoding="utf-8")

    title, fm = _read_page_metadata(page)

    assert title == "Naked Title"
    assert fm == {}


def test_read_page_metadata_non_utf8_returns_empty(tmp_path: Path) -> None:
    page = tmp_path / "p.md"
    page.write_bytes(b"\xff\xfe# Not UTF-8")

    title, fm = _read_page_metadata(page)

    assert title == ""
    assert fm == {}


def test_read_page_metadata_propagates_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OS-level read errors are not swallowed (spec §Edge cases).

    A page ripgrep matched but the metadata reader can't open is a
    system-level failure the user should see, not a silent
    blank-metadata hit.
    """

    page = tmp_path / "p.md"
    page.write_text("# T\nbody\n", encoding="utf-8")

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", _boom)

    with pytest.raises(PermissionError):
        _read_page_metadata(page)


def test_read_page_metadata_skips_h1_inside_fenced_code_block(tmp_path: Path) -> None:
    """A ``# foo`` inside a ``` … ``` block must not become the title."""

    page = tmp_path / "p.md"
    page.write_text(
        "```python\n# this is a Python comment, not a title\nprint('hi')\n```\n"
        "# Real Title\nbody\n",
        encoding="utf-8",
    )

    title, _ = _read_page_metadata(page)

    assert title == "Real Title"


def test_read_page_metadata_no_h1_outside_fence_returns_empty_title(tmp_path: Path) -> None:
    page = tmp_path / "p.md"
    page.write_text(
        "```bash\n# this comment looks like a header but isn't\n```\n",
        encoding="utf-8",
    )

    title, _ = _read_page_metadata(page)

    assert title == ""


def test_coerce_tags_handles_string_list_and_other() -> None:
    assert _coerce_tags("urgent") == ["urgent"]
    assert _coerce_tags(["a", "b"]) == ["a", "b"]
    assert _coerce_tags([1, 2]) == ["1", "2"]
    assert _coerce_tags(None) == []
    assert _coerce_tags({"a": 1}) == []


def test_filters_match_returns_true_when_all_filters_pass() -> None:
    fm: dict[str, object] = {"type": "meeting", "status": "active", "tags": ["urgent"]}
    assert _filters_match(fm, SearchFilters(type="meeting", status="active", tag="urgent"))


def test_filters_match_returns_false_when_any_filter_fails() -> None:
    fm: dict[str, object] = {"type": "meeting", "status": "active", "tags": ["q4"]}
    assert not _filters_match(fm, SearchFilters(tag="urgent"))
    assert not _filters_match(fm, SearchFilters(type="interview"))
    assert not _filters_match(fm, SearchFilters(status="archived"))


def test_filters_match_no_filters_always_true() -> None:
    assert _filters_match({}, SearchFilters())
