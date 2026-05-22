"""Tests for ``llm_wiki_kit.managed_regions``.

ADR-0003 names the three functions and the two delimiter syntaxes. These
tests pin the public contract: ``parse`` extracts a ``{region_id: body}``
mapping, ``update`` rewrites a single region in place, and
``extract_unmanaged`` returns the file with every region stripped (used by
drift detection on the user-editable parts of a shared file).

Both delimiter forms are accepted simultaneously rather than via a
``flavor`` parameter — the ADR's function signatures deliberately don't
take a file path, and in practice no real file mixes the two forms.
"""

from __future__ import annotations

import pytest

from llm_wiki_kit.errors import ManagedRegionError
from llm_wiki_kit.managed_regions import (
    extract_unmanaged,
    parse,
    update,
)

# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def test_parse_empty_content_returns_empty_dict() -> None:
    assert parse("") == {}


def test_parse_content_without_markers_returns_empty_dict() -> None:
    assert parse("# AGENTS.md\n\njust prose, nothing managed\n") == {}


def test_parse_extracts_a_single_markdown_region() -> None:
    content = (
        "intro\n"
        "<!-- BEGIN MANAGED: content-types -->\n"
        "- meeting\n"
        "- recipe\n"
        "<!-- END MANAGED: content-types -->\n"
        "outro\n"
    )
    assert parse(content) == {"content-types": "- meeting\n- recipe"}


def test_parse_extracts_a_single_yaml_region() -> None:
    content = (
        "schemas:\n# BEGIN MANAGED: meeting\n  meeting:\n    title: str\n# END MANAGED: meeting\n"
    )
    assert parse(content) == {"meeting": "  meeting:\n    title: str"}


def test_parse_extracts_multiple_regions_of_mixed_flavors() -> None:
    content = (
        "<!-- BEGIN MANAGED: a -->\n"
        "alpha\n"
        "<!-- END MANAGED: a -->\n"
        "middle\n"
        "# BEGIN MANAGED: b\n"
        "beta line 1\n"
        "beta line 2\n"
        "# END MANAGED: b\n"
    )
    assert parse(content) == {
        "a": "alpha",
        "b": "beta line 1\nbeta line 2",
    }


def test_parse_returns_empty_string_for_empty_region() -> None:
    content = "<!-- BEGIN MANAGED: empty -->\n<!-- END MANAGED: empty -->\n"
    assert parse(content) == {"empty": ""}


def test_parse_preserves_blank_lines_inside_region() -> None:
    content = "<!-- BEGIN MANAGED: r -->\nfirst\n\nthird\n<!-- END MANAGED: r -->\n"
    assert parse(content) == {"r": "first\n\nthird"}


def test_parse_allows_indented_markers() -> None:
    content = "outer:\n  # BEGIN MANAGED: nested-yaml\n  key: value\n  # END MANAGED: nested-yaml\n"
    assert parse(content) == {"nested-yaml": "  key: value"}


def test_parse_rejects_nested_regions() -> None:
    content = (
        "<!-- BEGIN MANAGED: outer -->\n"
        "<!-- BEGIN MANAGED: inner -->\n"
        "x\n"
        "<!-- END MANAGED: inner -->\n"
        "<!-- END MANAGED: outer -->\n"
    )
    with pytest.raises(ManagedRegionError, match="nesting"):
        parse(content)


def test_parse_rejects_unmatched_end_marker() -> None:
    content = "<!-- END MANAGED: stray -->\n"
    with pytest.raises(ManagedRegionError, match="no matching BEGIN"):
        parse(content)


def test_parse_rejects_unclosed_begin_marker() -> None:
    content = "<!-- BEGIN MANAGED: forever -->\nbody\n"
    with pytest.raises(ManagedRegionError, match="unclosed"):
        parse(content)


def test_parse_rejects_mismatched_end_id() -> None:
    content = "<!-- BEGIN MANAGED: a -->\nx\n<!-- END MANAGED: b -->\n"
    with pytest.raises(ManagedRegionError, match="does not match"):
        parse(content)


def test_parse_rejects_duplicate_region_id() -> None:
    content = (
        "<!-- BEGIN MANAGED: dup -->\n"
        "one\n"
        "<!-- END MANAGED: dup -->\n"
        "<!-- BEGIN MANAGED: dup -->\n"
        "two\n"
        "<!-- END MANAGED: dup -->\n"
    )
    with pytest.raises(ManagedRegionError, match="duplicate"):
        parse(content)


def test_parse_rejects_mismatched_flavor_pair() -> None:
    content = "<!-- BEGIN MANAGED: mix -->\nbody\n# END MANAGED: mix\n"
    with pytest.raises(ManagedRegionError):
        parse(content)


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_replaces_a_markdown_region_body() -> None:
    content = "intro\n<!-- BEGIN MANAGED: r -->\nold\n<!-- END MANAGED: r -->\noutro\n"
    result = update(content, "r", "new line 1\nnew line 2")
    assert result == (
        "intro\n<!-- BEGIN MANAGED: r -->\nnew line 1\nnew line 2\n<!-- END MANAGED: r -->\noutro\n"
    )


def test_update_replaces_a_yaml_region_body() -> None:
    content = (
        "schemas:\n# BEGIN MANAGED: meeting\n  meeting:\n    title: str\n# END MANAGED: meeting\n"
    )
    result = update(content, "meeting", "  meeting:\n    title: str\n    attendees: list")
    assert "attendees: list" in result
    assert "# BEGIN MANAGED: meeting" in result
    assert "# END MANAGED: meeting" in result


def test_update_leaves_other_regions_untouched() -> None:
    content = (
        "<!-- BEGIN MANAGED: a -->\n"
        "alpha\n"
        "<!-- END MANAGED: a -->\n"
        "<!-- BEGIN MANAGED: b -->\n"
        "beta\n"
        "<!-- END MANAGED: b -->\n"
    )
    result = update(content, "a", "ALPHA")
    parsed = parse(result)
    assert parsed["a"] == "ALPHA"
    assert parsed["b"] == "beta"


def test_update_into_empty_body() -> None:
    content = "<!-- BEGIN MANAGED: r -->\nremove me\n<!-- END MANAGED: r -->\n"
    result = update(content, "r", "")
    assert result == ("<!-- BEGIN MANAGED: r -->\n<!-- END MANAGED: r -->\n")


def test_update_accepts_new_content_with_trailing_newline() -> None:
    content = "<!-- BEGIN MANAGED: r -->\nold\n<!-- END MANAGED: r -->\n"
    result = update(content, "r", "fresh\n")
    assert parse(result) == {"r": "fresh"}


def test_update_preserves_indentation_of_markers() -> None:
    content = "outer:\n  # BEGIN MANAGED: r\n  old: value\n  # END MANAGED: r\n"
    result = update(content, "r", "  new: value")
    assert "  # BEGIN MANAGED: r\n  new: value\n  # END MANAGED: r\n" in result


def test_update_raises_on_unknown_region_id() -> None:
    content = "<!-- BEGIN MANAGED: a -->\nx\n<!-- END MANAGED: a -->\n"
    with pytest.raises(ManagedRegionError, match="unknown"):
        update(content, "missing", "y")


def test_update_does_not_add_trailing_newline_when_source_has_none() -> None:
    content = "<!-- BEGIN MANAGED: r -->\nold\n<!-- END MANAGED: r -->"
    result = update(content, "r", "new")
    assert not result.endswith("\n\n")
    assert result.endswith("<!-- END MANAGED: r -->")


# ---------------------------------------------------------------------------
# extract_unmanaged
# ---------------------------------------------------------------------------


def test_extract_unmanaged_returns_content_when_no_regions() -> None:
    text = "plain prose\nwith newlines\n"
    assert extract_unmanaged(text) == text


def test_extract_unmanaged_strips_a_single_region_with_markers() -> None:
    content = "intro\n<!-- BEGIN MANAGED: r -->\nkit owned body\n<!-- END MANAGED: r -->\noutro\n"
    assert extract_unmanaged(content) == "intro\noutro\n"


def test_extract_unmanaged_strips_multiple_regions() -> None:
    content = (
        "<!-- BEGIN MANAGED: a -->\n"
        "alpha\n"
        "<!-- END MANAGED: a -->\n"
        "middle\n"
        "# BEGIN MANAGED: b\n"
        "beta\n"
        "# END MANAGED: b\n"
        "tail\n"
    )
    assert extract_unmanaged(content) == "middle\ntail\n"


def test_extract_unmanaged_returns_empty_when_file_is_only_a_region() -> None:
    content = "<!-- BEGIN MANAGED: r -->\neverything kit owned\n<!-- END MANAGED: r -->\n"
    assert extract_unmanaged(content) == ""
