"""Unit tests for ``llm_wiki_kit.ingest`` (RFC-0001 Task 16).

The orchestrator is intentionally a pile of pure functions over the
installed content-type primitives' ``routing:`` blocks. These tests pin
the routing contract: filename / extension / URL-host / URL-path
matching, deterministic ambiguity, ``--as`` override semantics, and the
source-kind classifier that gates which rule kinds may fire.
"""

from __future__ import annotations

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.ingest import (
    Ambiguous,
    NoMatch,
    Routed,
    SourceKind,
    classify_source,
    route,
)
from llm_wiki_kit.models import Primitive, PrimitiveKind, PrimitiveRouting


def _ct(
    name: str,
    *,
    file_extensions: list[str] | None = None,
    filename_patterns: list[str] | None = None,
    url_domains: list[str] | None = None,
    url_path_patterns: list[str] | None = None,
) -> Primitive:
    """Build a content-type primitive with the requested routing block."""

    routing = None
    if any(
        v is not None for v in (file_extensions, filename_patterns, url_domains, url_path_patterns)
    ):
        routing = PrimitiveRouting(
            file_extensions=file_extensions or [],
            filename_patterns=filename_patterns or [],
            url_domains=url_domains or [],
            url_path_patterns=url_path_patterns or [],
        )
    return Primitive(
        name=name,
        kind=PrimitiveKind.CONTENT_TYPE,
        version="0.1.0",
        description=f"test primitive {name}",
        routing=routing,
    )


def _other(name: str, kind: PrimitiveKind) -> Primitive:
    return Primitive(
        name=name,
        kind=kind,
        version="0.1.0",
        description=f"test primitive {name}",
    )


# ---------------------------------------------------------------------------
# classify_source
# ---------------------------------------------------------------------------


def test_classify_source_recognises_http_url() -> None:
    assert classify_source("http://example.com/recipe").kind == "url"


def test_classify_source_recognises_https_url() -> None:
    assert classify_source("https://allrecipes.com/recipe/x").kind == "url"


def test_classify_source_classifies_dash_as_stdin() -> None:
    assert classify_source("-").kind == "stdin"


def test_classify_source_classifies_relative_path_as_file() -> None:
    assert classify_source("EOB-2026-04-15.pdf").kind == "file"


def test_classify_source_classifies_absolute_path_as_file() -> None:
    assert classify_source("/tmp/walmart-receipt.jpg").kind == "file"


def test_classify_source_strips_url_to_host_and_path() -> None:
    classified = classify_source("https://www.allrecipes.com/recipe/123/sheet-pan-tacos")
    assert classified.url_host == "www.allrecipes.com"
    assert classified.url_path == "/recipe/123/sheet-pan-tacos"


def test_classify_source_strips_file_to_basename_and_suffix() -> None:
    classified = classify_source("/clinic/visits/EOB-2026-04-15.PDF")
    assert classified.filename == "EOB-2026-04-15.PDF"
    assert classified.suffix == ".pdf"  # lower-cased for case-insensitive match


# ---------------------------------------------------------------------------
# route — single match
# ---------------------------------------------------------------------------


def test_route_url_domain_match_picks_single_primitive() -> None:
    catalog = [_ct("recipe", url_domains=["allrecipes.com"])]
    result = route("https://allrecipes.com/recipe/x", catalog, as_override=None)
    assert isinstance(result, Routed)
    assert result.content_type == "recipe"
    assert result.via == "auto"
    assert any(s.startswith("url_domain:") for s in result.signals)


def test_route_url_domain_supports_glob() -> None:
    catalog = [_ct("recipe", url_domains=["*.bonappetit.com"])]
    result = route("https://www.bonappetit.com/recipe/123", catalog, as_override=None)
    assert isinstance(result, Routed)
    assert result.content_type == "recipe"


def test_route_url_path_pattern_match() -> None:
    catalog = [_ct("recipe", url_path_patterns=["/recipe/*"])]
    result = route("https://elsewhere.example/recipe/sheet-pan", catalog, as_override=None)
    assert isinstance(result, Routed)
    assert result.content_type == "recipe"


def test_route_filename_pattern_match() -> None:
    catalog = [_ct("medical-record", filename_patterns=["EOB-*"])]
    result = route("EOB-2026-04-15.pdf", catalog, as_override=None)
    assert isinstance(result, Routed)
    assert result.content_type == "medical-record"


def test_route_filename_pattern_is_case_insensitive() -> None:
    catalog = [_ct("receipt", filename_patterns=["*receipt*"])]
    result = route("WALMART-RECEIPT-001.jpg", catalog, as_override=None)
    assert isinstance(result, Routed)
    assert result.content_type == "receipt"


def test_route_file_extension_match_is_case_insensitive() -> None:
    catalog = [_ct("tax-document", file_extensions=[".pdf"])]
    result = route("/forms/W-2.PDF", catalog, as_override=None)
    assert isinstance(result, Routed)
    assert result.content_type == "tax-document"


def test_route_url_signals_do_not_fire_on_file_sources() -> None:
    """A primitive with only url_domains must not match a filesystem path
    that happens to contain the domain text."""

    catalog = [_ct("recipe", url_domains=["allrecipes.com"])]
    result = route("/local/allrecipes.com/recipe.pdf", catalog, as_override=None)
    assert isinstance(result, NoMatch)


def test_route_file_signals_do_not_fire_on_url_sources() -> None:
    """A primitive with only file_extensions must not match a URL whose
    path happens to end in that suffix."""

    catalog = [_ct("tax-document", file_extensions=[".pdf"])]
    result = route("https://example.com/forms/w2.pdf", catalog, as_override=None)
    assert isinstance(result, NoMatch)


# ---------------------------------------------------------------------------
# route — ambiguity and no-match
# ---------------------------------------------------------------------------


def test_route_ambiguous_returns_sorted_candidates() -> None:
    catalog = [
        _ct("tax-document", file_extensions=[".pdf"]),
        _ct("medical-record", filename_patterns=["EOB-*"]),
    ]
    result = route("EOB-2026-04-15.pdf", catalog, as_override=None)
    assert isinstance(result, Ambiguous)
    assert result.candidates == ["medical-record", "tax-document"]


def test_route_no_match_lists_installed_content_types() -> None:
    catalog = [
        _ct("recipe", url_domains=["allrecipes.com"]),
        _ct("decision"),  # no routing block — reachable only via --as
    ]
    result = route("/tmp/mystery.bin", catalog, as_override=None)
    assert isinstance(result, NoMatch)
    assert result.available == ["decision", "recipe"]


def test_route_no_match_for_stdin_without_override() -> None:
    catalog = [_ct("recipe", url_domains=["allrecipes.com"])]
    result = route("-", catalog, as_override=None)
    assert isinstance(result, NoMatch)


def test_route_ignores_non_content_type_primitives() -> None:
    catalog = [
        _ct("recipe", url_domains=["allrecipes.com"]),
        _other("food", PrimitiveKind.ONTOLOGY),
        _other("meal-planning", PrimitiveKind.OPERATION),
    ]
    result = route("https://allrecipes.com/recipe/x", catalog, as_override=None)
    assert isinstance(result, Routed)
    assert result.content_type == "recipe"


def test_route_skips_primitives_without_routing_block() -> None:
    catalog = [
        _ct("recipe", url_domains=["allrecipes.com"]),
        _ct("decision"),  # no routing → never auto-routes
    ]
    result = route("https://decisions.example.com/d/1", catalog, as_override=None)
    assert isinstance(result, NoMatch)


# ---------------------------------------------------------------------------
# route — --as override
# ---------------------------------------------------------------------------


def test_route_as_override_bypasses_detection() -> None:
    catalog = [
        _ct("recipe", url_domains=["allrecipes.com"]),
        _ct("interview"),  # no routing
    ]
    result = route("anything-goes.txt", catalog, as_override="interview")
    assert isinstance(result, Routed)
    assert result.content_type == "interview"
    assert result.via == "as_flag"
    assert result.signals == []


def test_route_as_override_with_unknown_name_raises_wiki_error() -> None:
    catalog = [_ct("recipe", url_domains=["allrecipes.com"])]
    with pytest.raises(WikiError) as excinfo:
        route("foo.pdf", catalog, as_override="not-installed")
    assert "not-installed" in str(excinfo.value)


def test_route_as_override_rejects_non_content_type_primitive() -> None:
    catalog = [
        _ct("recipe", url_domains=["allrecipes.com"]),
        _other("food", PrimitiveKind.ONTOLOGY),
    ]
    with pytest.raises(WikiError):
        route("anything", catalog, as_override="food")


def test_route_as_override_does_not_consult_signals() -> None:
    """``--as`` should pick the named primitive verbatim, even if some
    other primitive's auto-route would have matched. No silent surprise."""

    catalog = [
        _ct("recipe", url_domains=["allrecipes.com"]),
        _ct("meeting"),
    ]
    result = route("https://allrecipes.com/recipe/sheet-pan-tacos", catalog, as_override="meeting")
    assert isinstance(result, Routed)
    assert result.content_type == "meeting"
    assert result.via == "as_flag"


# ---------------------------------------------------------------------------
# SourceKind sentinels
# ---------------------------------------------------------------------------


def test_source_kind_values_are_stable() -> None:
    # The CLI prints these labels; pinning them keeps log messages stable.
    assert SourceKind.URL.value == "url"
    assert SourceKind.FILE.value == "file"
    assert SourceKind.STDIN.value == "stdin"
