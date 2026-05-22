"""Tests for ``llm_wiki_kit.render``.

ADR-0001 names the contract: ``str.format_map`` over a ``SafeDict`` for a
small allowlist of files, byte-for-byte copy for everything else, and
every write into the vault routed through ``write_helper.safe_write``
per ADR-0004. These tests pin that contract end-to-end against a
``tmp_path`` source tree and a real journal.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import PageWriteEvent
from llm_wiki_kit.render import INTERPOLATED_FILES, SafeDict, render_tree


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "vault" / ".wiki.journal").mkdir(parents=True)
    return tmp_path / "vault"


@pytest.fixture
def journal(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


@pytest.fixture
def src(tmp_path: Path) -> Path:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    return src_dir


def _ctx(**overrides: str) -> Mapping[str, str]:
    base = {
        "vault_name": "demo-vault",
        "recipe_name": "family",
        "rendered_ontologies": "- people\n- meals\n",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# INTERPOLATED_FILES: the allowlist is the public contract from ADR-0001.
# ---------------------------------------------------------------------------


def test_interpolated_files_matches_adr_0001() -> None:
    assert INTERPOLATED_FILES == {
        "AGENTS.md",
        "CORE.md",
        "identity.md",
        "frontmatter.schema.yaml",
        ".gitignore",
    }


# ---------------------------------------------------------------------------
# SafeDict: missing keys return the original ``{key}`` token, so an
# interpolated file that contains an as-yet-unsubstituted token survives the
# render unchanged.
# ---------------------------------------------------------------------------


def test_safedict_missing_key_returns_original_token() -> None:
    out = "{unknown}".format_map(SafeDict({"known": "x"}))
    assert out == "{unknown}"


def test_safedict_known_key_substitutes_normally() -> None:
    out = "{known}".format_map(SafeDict({"known": "value"}))
    assert out == "value"


def test_safedict_mixed_known_and_unknown() -> None:
    out = "{known} and {unknown}".format_map(SafeDict({"known": "x"}))
    assert out == "x and {unknown}"


def test_safedict_double_brace_escape_renders_literal_braces() -> None:
    """The standard ``str.format_map`` escape — ``{{`` and ``}}`` survive."""
    out = "{{literal}}".format_map(SafeDict({}))
    assert out == "{literal}"


def test_safedict_positional_braces_require_escape() -> None:
    """``str.format_map`` rejects positional fields entirely, so a regex like
    ``\\d{4}`` won't reach ``__missing__`` — the file author must escape it
    as ``\\d{{4}}``. Pinned so the limitation is visible and intentional."""
    with pytest.raises(ValueError):
        r"\d{4}-\d{2}".format_map(SafeDict({}))
    out = r"\d{{4}}-\d{{2}}".format_map(SafeDict({}))
    assert out == r"\d{4}-\d{2}"


def test_safedict_preserves_format_spec_on_missing_key() -> None:
    """Tokens with a format spec round-trip; SafeDict drops the spec when
    re-emitting, on the assumption that a file author never applies a spec to
    a token they don't intend to substitute."""
    out = "{unknown:>10}".format_map(SafeDict({}))
    assert out == "{unknown}"


def test_safedict_preserves_dotted_access_on_missing_key() -> None:
    out = "{unknown.attr}".format_map(SafeDict({}))
    assert out == "{unknown.attr}"


def test_safedict_preserves_indexed_access_on_missing_key() -> None:
    out = "{unknown[0]}".format_map(SafeDict({}))
    assert out == "{unknown[0]}"


# ---------------------------------------------------------------------------
# render_tree: interpolated files
# ---------------------------------------------------------------------------


def test_render_tree_interpolates_allowlisted_file(vault: Path, journal: Path, src: Path) -> None:
    (src / "AGENTS.md").write_text("vault: {vault_name}\nrecipe: {recipe_name}\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    assert (vault / "AGENTS.md").read_text() == "vault: demo-vault\nrecipe: family\n"


def test_render_tree_preserves_unknown_tokens_in_interpolated_file(
    vault: Path, journal: Path, src: Path
) -> None:
    (src / "AGENTS.md").write_text("known: {vault_name}\nunknown: {not_in_context}\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    assert (vault / "AGENTS.md").read_text() == "known: demo-vault\nunknown: {not_in_context}\n"


def test_render_tree_interpolates_every_allowlisted_file(
    vault: Path, journal: Path, src: Path
) -> None:
    for name in INTERPOLATED_FILES:
        (src / name).write_text("name={vault_name}\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    for name in INTERPOLATED_FILES:
        assert (vault / name).read_text() == "name=demo-vault\n"


def test_render_tree_allowlist_is_basename_only(vault: Path, journal: Path, src: Path) -> None:
    """``AGENTS.md`` anywhere in the tree gets interpolated — the allowlist
    is by basename, not by relative path. This matches how primitives ship
    contributions: ``core/files/AGENTS.md`` and a hypothetical
    ``some-primitive/files/subdir/AGENTS.md`` both interpolate."""
    nested = src / "subdir" / "AGENTS.md"
    nested.parent.mkdir()
    nested.write_text("hello {vault_name}\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    assert (vault / "subdir" / "AGENTS.md").read_text() == "hello demo-vault\n"


# ---------------------------------------------------------------------------
# render_tree: byte-for-byte copy for non-allowlisted files. The Templater
# double-brace non-collision is ADR-0001's load-bearing invariant.
# ---------------------------------------------------------------------------


def test_render_tree_copies_non_allowlisted_file_byte_for_byte(
    vault: Path, journal: Path, src: Path
) -> None:
    body = "# Meeting {{date}}\n\nattendees: {{attendees}}\n"
    (src / "meeting.md").write_text(body)
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    assert (vault / "meeting.md").read_text() == body


def test_render_tree_preserves_obsidian_templater_in_skill_files(
    vault: Path, journal: Path, src: Path
) -> None:
    """SKILL.md is not in the allowlist — Templater syntax inside it must
    reach the vault unchanged so Obsidian can process it at note-creation
    time."""
    skill_dir = src / "skills" / "ingest"
    skill_dir.mkdir(parents=True)
    body = "Use {{title}} for the page name, {{date:YYYY-MM-DD}} for today.\n"
    (skill_dir / "SKILL.md").write_text(body)
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    assert (vault / "skills" / "ingest" / "SKILL.md").read_text() == body


def test_render_tree_walks_subdirectories(vault: Path, journal: Path, src: Path) -> None:
    (src / "a" / "b" / "c").mkdir(parents=True)
    (src / "a" / "b" / "c" / "leaf.md").write_text("leaf\n")
    (src / "top.md").write_text("top\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    assert (vault / "a" / "b" / "c" / "leaf.md").read_text() == "leaf\n"
    assert (vault / "top.md").read_text() == "top\n"


# ---------------------------------------------------------------------------
# render_tree: writes route through safe_write so the journal records every
# file dropped into the vault (the drift-detection baseline per ADR-0004).
# ---------------------------------------------------------------------------


def test_render_tree_emits_page_write_event_per_file(vault: Path, journal: Path, src: Path) -> None:
    (src / "AGENTS.md").write_text("vault: {vault_name}\n")
    (src / "meeting.md").write_text("body\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    events = read_events(journal)
    page_writes = [e for e in events if isinstance(e, PageWriteEvent)]
    paths = {e.path for e in page_writes}
    assert paths == {"AGENTS.md", "meeting.md"}


def test_render_tree_event_carries_by_attribution(vault: Path, journal: Path, src: Path) -> None:
    (src / "AGENTS.md").write_text("{vault_name}\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="meeting")
    events = read_events(journal)
    page_writes = [e for e in events if isinstance(e, PageWriteEvent)]
    assert all(e.by == "meeting" for e in page_writes)


def test_render_tree_event_hash_reflects_rendered_content(
    vault: Path, journal: Path, src: Path
) -> None:
    """The journaled hash matches the rendered (post-substitution) bytes,
    not the source bytes. This is what makes drift detection work: a
    subsequent ``safe_write`` of the same rendered content sees no drift."""
    (src / "AGENTS.md").write_text("vault: {vault_name}\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    import hashlib

    expected_hash = hashlib.sha256(b"vault: demo-vault\n").hexdigest()
    page_writes = [e for e in read_events(journal) if isinstance(e, PageWriteEvent)]
    assert page_writes[0].hash == expected_hash


def test_render_tree_a_second_run_with_same_context_is_clean(
    vault: Path, journal: Path, src: Path
) -> None:
    """Re-rendering an unchanged tree should not produce ``.proposed``
    sidecars — the journal baseline matches the on-disk content."""
    (src / "AGENTS.md").write_text("vault: {vault_name}\n")
    (src / "meeting.md").write_text("body\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    assert not (vault / "AGENTS.md.proposed").exists()
    assert not (vault / "meeting.md.proposed").exists()


def test_render_tree_user_edited_file_falls_through_to_proposal(
    vault: Path, journal: Path, src: Path
) -> None:
    """If a user edits a file the kit previously rendered, a re-render
    routes the new content to ``<path>.proposed`` per ADR-0004."""
    (src / "AGENTS.md").write_text("vault: {vault_name}\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    (vault / "AGENTS.md").write_text("user edits\n")

    (src / "AGENTS.md").write_text("vault: {vault_name}\nrecipe: {recipe_name}\n")
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    assert (vault / "AGENTS.md").read_text() == "user edits\n"
    assert (vault / "AGENTS.md.proposed").read_text() == "vault: demo-vault\nrecipe: family\n"


# ---------------------------------------------------------------------------
# render_tree: empty / missing source trees are a no-op, not a crash.
# ---------------------------------------------------------------------------


def test_render_tree_empty_source_is_noop(vault: Path, journal: Path, src: Path) -> None:
    render_tree(src, vault, _ctx(), journal_path=journal, by="core")
    assert read_events(journal) == []


def test_render_tree_missing_source_is_noop(vault: Path, journal: Path, tmp_path: Path) -> None:
    nonexistent = tmp_path / "does-not-exist"
    render_tree(nonexistent, vault, _ctx(), journal_path=journal, by="core")
    assert read_events(journal) == []


# ---------------------------------------------------------------------------
# render_tree: dest must match the vault root derived from journal_path.
# safe_write computes vault-relative paths from ``journal_path.parent.parent``;
# if dest disagrees we'd produce nonsense journal entries, so fail loudly.
# ---------------------------------------------------------------------------


def test_render_tree_rejects_dest_outside_journal_vault(
    vault: Path, journal: Path, src: Path, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    (src / "page.md").write_text("body\n")
    with pytest.raises(WikiError):
        render_tree(src, other, _ctx(), journal_path=journal, by="core")


# ---------------------------------------------------------------------------
# render_tree: non-UTF-8 files. Every file the kit ships today is text;
# a binary asset would crash UTF-8 decode. ``render_tree`` raises a
# structured ``WikiError`` so a primitive author sees a clear message
# instead of a UnicodeDecodeError traceback.
# ---------------------------------------------------------------------------


def test_render_tree_rejects_non_utf8_file_with_wikierror(
    vault: Path, journal: Path, src: Path
) -> None:
    (src / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03")
    with pytest.raises(WikiError):
        render_tree(src, vault, _ctx(), journal_path=journal, by="core")
