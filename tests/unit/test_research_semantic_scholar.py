"""Unit tests for ``llm_wiki_kit.research.providers.semantic_scholar`` (Task 19).

The Semantic Scholar Graph API supports a keyless tier (~100 reqs /
5 min / IP) and a keyed tier with elevated limits. The provider
mirrors that asymmetry by passing ``max_retries=5`` keyless and
``max_retries=3`` keyed.

The body rendering is deterministic: the same response renders the
same answer string byte-for-byte. ``EXPECTED_BODY`` is hand-authored,
not regenerated from the implementation — a renderer drift surfaces
as both a spec diff and a test diff (spec invariant 8).

The fixture key ``ss-DO-NOT-LOG`` is intentionally provider-specific.
Do not consolidate with Perplexity's or Gemini's prefixes.
"""

from __future__ import annotations

import ast
import io
import locale
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.models import ProviderConfig
from llm_wiki_kit.research import http as http_module
from llm_wiki_kit.research.http import ResearchHTTPError
from llm_wiki_kit.research.providers import semantic_scholar
from llm_wiki_kit.research.providers.semantic_scholar import (
    DEFAULT_FIELDS,
    DEFAULT_LIMIT,
    DEFAULT_MODEL,
    KEYED_MAX_RETRIES,
    KEYLESS_MAX_RETRIES,
    SemanticScholarResult,
    dispatch,
)

# ---------------------------------------------------------------------------
# Hand-authored expected body.
#
# Hand-authored snapshot. If the renderer template changes, re-author
# this constant in the same commit — do not paste the implementation's
# output. Tautology defense per spec invariant 8.
#
# The two trailing-space artefacts on the paper-2 line are real — the
# template is `{abstract}\n` with `abstract == ""`, so the line ends
# with ". " before the newline. Stripping them would be a renderer
# template change requiring a spec amendment.
# ---------------------------------------------------------------------------
EXPECTED_BODY = (
    "1. **T1** (2024) — A1, A2. *V1*. X\n"
    "   https://a\n"
    "2. **T2** (2023) — unknown authors. *V2*. \n"
    "   https://b\n"
)


@pytest.fixture(autouse=True)
def _locale_c() -> Any:
    """Run snapshot tests under ``LC_ALL=C``, restoring the prior locale after.

    Spec invariant 8: deterministic and locale-independent. Belt-and-
    braces against future locale-sensitive formatting in the renderer.

    ``locale.setlocale`` mutates process-global C-library state, so the
    fixture must capture-and-restore around each test rather than
    leaving the override in place for subsequent tests in the session.
    """

    previous = locale.setlocale(locale.LC_ALL, None)
    try:
        locale.setlocale(locale.LC_ALL, "C")
    except locale.Error:
        # System lacks the C locale (rare; some minimal CI images).
        # Skip rather than pretend — invariant 8's locale-independence
        # claim should be visibly unverified when the prerequisite
        # isn't met, not silently passed over.
        pytest.skip("C locale not available; invariant 8 requires LC_ALL=C")
    try:
        yield
    finally:
        locale.setlocale(locale.LC_ALL, previous)


@pytest.fixture
def ss_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set ``SEMANTIC_SCHOLAR_API_KEY`` to a recognisable but harmless value."""

    key = "ss-DO-NOT-LOG"
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", key)
    return key


def _canonical_response() -> dict[str, Any]:
    return {
        "total": 2,
        "offset": 0,
        "data": [
            {
                "title": "T1",
                "authors": [{"name": "A1"}, {"name": "A2"}],
                "year": 2024,
                "abstract": "X",
                "url": "https://a",
                "venue": "V1",
            },
            {
                "title": "T2",
                "authors": [],
                "year": 2023,
                "abstract": "",
                "url": "https://b",
                "venue": "V2",
            },
        ],
    }


def _record_request_json(monkeypatch: pytest.MonkeyPatch, response: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(semantic_scholar, "request_json", _fake)
    return calls


def test_semantic_scholar_dispatch_happy_path_keyed(
    monkeypatch: pytest.MonkeyPatch, ss_env: str
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "SEMANTIC_SCHOLAR_API_KEY"}),
        query="rust async",
    )

    assert isinstance(result, SemanticScholarResult)
    assert result.answer == EXPECTED_BODY
    assert result.citations == ["https://a", "https://b"]
    assert result.model == DEFAULT_MODEL

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "GET"
    assert call["json_body"] is None
    assert call["max_retries"] == KEYED_MAX_RETRIES
    # Query string is deterministic: query, then limit, then fields.
    assert "query=rust+async" in call["url"]
    assert f"limit={DEFAULT_LIMIT}" in call["url"]
    assert f"fields={DEFAULT_FIELDS}" in call["url"].replace("%2C", ",")
    assert call["headers"]["x-api-key"] == ss_env
    assert call["headers"]["User-Agent"].startswith("llm-wiki-kit/")


def test_semantic_scholar_dispatch_happy_path_keyless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env var → no ``x-api-key`` header; ``max_retries=5`` (keyless tier)."""

    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    calls = _record_request_json(monkeypatch, _canonical_response())

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "SEMANTIC_SCHOLAR_API_KEY"}),
        query="q",
    )

    assert result.answer == EXPECTED_BODY
    call = calls[0]
    assert "x-api-key" not in call["headers"]
    assert call["max_retries"] == KEYLESS_MAX_RETRIES


def test_semantic_scholar_dispatch_empty_api_key_omits_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty-string env var value is treated as keyless — header omitted."""

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
    calls = _record_request_json(monkeypatch, _canonical_response())

    dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "SEMANTIC_SCHOLAR_API_KEY"}),
        query="q",
    )

    assert "x-api-key" not in calls[0]["headers"]
    assert calls[0]["max_retries"] == KEYLESS_MAX_RETRIES


def test_semantic_scholar_dispatch_empty_data_returns_no_papers_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _record_request_json(monkeypatch, {"total": 0, "offset": 0, "data": []})

    result = dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert result.answer == "No papers found.\n"
    assert result.citations == []
    assert result.model == DEFAULT_MODEL


def test_semantic_scholar_dispatch_renders_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Byte-for-byte snapshot against the hand-authored EXPECTED_BODY."""

    _record_request_json(monkeypatch, _canonical_response())

    result = dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert result.answer == EXPECTED_BODY


def test_semantic_scholar_dispatch_missing_scalar_fields_render_empty_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paper missing ``abstract``/``venue``/``year``/``url`` renders, doesn't raise.

    The paper appears in the body; the empty ``url`` means it's
    *not* included in ``citations``.
    """

    response = {
        "data": [
            {"title": "T", "authors": [{"name": "A"}]},
        ]
    }
    _record_request_json(monkeypatch, response)

    result = dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert "**T**" in result.answer
    assert result.citations == []


def test_semantic_scholar_dispatch_all_fields_missing_renders_no_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response: dict[str, Any] = {"data": [{}]}
    _record_request_json(monkeypatch, response)

    result = dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert result.answer == "1. *(no metadata)*\n"
    assert result.citations == []


def test_semantic_scholar_dispatch_empty_authors_renders_unknown_authors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty ``authors`` list renders the slot as ``unknown authors``."""

    response = {
        "data": [
            {
                "title": "T",
                "authors": [],
                "year": 2024,
                "abstract": "a",
                "url": "https://x",
                "venue": "V",
            }
        ]
    }
    _record_request_json(monkeypatch, response)

    result = dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert "unknown authors" in result.answer


def test_semantic_scholar_dispatch_skips_non_string_author_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "data": [
            {
                "title": "T",
                "authors": [
                    {"name": "A"},
                    {"name": None},
                    {"name": 123},
                    {},
                ],
                "year": 2024,
                "abstract": "a",
                "url": "https://x",
                "venue": "V",
            }
        ]
    }
    _record_request_json(monkeypatch, response)

    result = dispatch(config=ProviderConfig.model_validate({}), query="q")

    # Only "A" survives the filter.
    assert "— A. " in result.answer
    # The non-string author entries do not appear anywhere.
    assert "None" not in result.answer


def test_semantic_scholar_dispatch_malformed_top_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list-typed payload at the top level is malformed."""

    _record_request_json(monkeypatch, [1, 2, 3])

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert "semantic-scholar: malformed response" in str(exc_info.value)


def test_semantic_scholar_dispatch_malformed_data_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _record_request_json(monkeypatch, {"total": 5})

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert "semantic-scholar: malformed response" in str(exc_info.value)


def test_semantic_scholar_dispatch_malformed_data_not_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _record_request_json(monkeypatch, {"data": {"not": "a list"}})

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert "semantic-scholar: malformed response" in str(exc_info.value)


def test_semantic_scholar_dispatch_url_encodes_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Query string is encoded via ``quote_plus`` — spaces become ``+``, ``&`` becomes ``%26``."""

    calls = _record_request_json(monkeypatch, _canonical_response())

    dispatch(
        config=ProviderConfig.model_validate({}),
        query="machine learning & ai",
    )

    url = calls[0]["url"]
    assert "query=machine+learning+%26+ai" in url


def test_semantic_scholar_dispatch_wraps_http_error_with_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _record_request_json(
        monkeypatch,
        ResearchHTTPError("HTTP 429 after 5 retries", status=429),
    )

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert str(exc_info.value) == "semantic-scholar: HTTP 429 after 5 retries"
    assert exc_info.value.status == 429


def test_semantic_scholar_dispatch_key_redacted_in_errors(
    monkeypatch: pytest.MonkeyPatch, ss_env: str
) -> None:
    _record_request_json(monkeypatch, ResearchHTTPError("HTTP 401", status=401))

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "SEMANTIC_SCHOLAR_API_KEY"}),
            query="q",
        )

    exc = exc_info.value
    assert ss_env not in str(exc)
    assert ss_env not in repr(exc)
    assert all(ss_env not in repr(arg) for arg in exc.args)
    assert exc.__cause__ is None


def test_semantic_scholar_dispatch_endpoint_with_query_string_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())
    config = ProviderConfig.model_validate(
        {"endpoint": "https://proxy.example/paper/search?internal=1"}
    )

    with pytest.raises(WikiError) as exc_info:
        dispatch(config=config, query="q")

    assert (
        "research-providers.yaml: semantic-scholar endpoint must be a bare "
        "scheme://host/path (no query, fragment, or userinfo)" in str(exc_info.value)
    )
    assert calls == []


def test_semantic_scholar_dispatch_endpoint_with_fragment_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())
    config = ProviderConfig.model_validate(
        {"endpoint": "https://proxy.example/paper/search#section"}
    )

    with pytest.raises(WikiError):
        dispatch(config=config, query="q")
    assert calls == []


def test_semantic_scholar_dispatch_endpoint_with_userinfo_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())
    config = ProviderConfig.model_validate(
        {"endpoint": "https://user:tok@proxy.example/paper/search"}
    )

    with pytest.raises(WikiError):
        dispatch(config=config, query="q")
    assert calls == []


def test_semantic_scholar_dispatch_endpoint_with_password_only_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``https://:tok@host/path`` has empty ``username`` but populated ``password``.

    Tested explicitly because ``urlsplit().username`` is falsy in that
    form; only ``.password`` catches the userinfo separator. Pinned so
    a future refactor that drops the password check doesn't open a
    credential-leakage hole.
    """

    calls = _record_request_json(monkeypatch, _canonical_response())
    config = ProviderConfig.model_validate({"endpoint": "https://:tok@proxy.example/paper/search"})

    with pytest.raises(WikiError):
        dispatch(config=config, query="q")
    assert calls == []


def test_semantic_scholar_dispatch_real_http_helper_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through the real ``request_json`` (with ``urlopen`` mocked)."""

    calls = [0]

    def _fake_urlopen(request: Any, timeout: float = 0.0) -> Any:
        calls[0] += 1
        # Confirm the wire request really is GET with no body.
        assert request.get_method() == "GET"
        assert request.data is None

        class _Response:
            def read(self) -> bytes:
                return b'{"data": []}'

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

        return _Response()

    monkeypatch.setattr(http_module, "urlopen", _fake_urlopen)

    result = dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert result.answer == "No papers found.\n"
    assert calls == [1]


def test_semantic_scholar_keyless_real_retries_use_correct_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyless mode actually triggers 5-retry backoff in the real helper."""

    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    def _always_429(request: Any, timeout: float = 0.0) -> Any:
        raise HTTPError(
            url="https://api.semanticscholar.org/graph/v1/paper/search",
            code=429,
            msg="rate limited",
            hdrs={},  # type: ignore[arg-type]
            fp=io.BytesIO(b"{}"),
        )

    sleeps: list[float] = []

    def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_module, "urlopen", _always_429)
    monkeypatch.setattr("llm_wiki_kit.research.http.time.sleep", _record_sleep)

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(config=ProviderConfig.model_validate({}), query="q")

    assert "semantic-scholar: HTTP 429 after 5 retries" in str(exc_info.value)
    # 5 retries → 5 sleeps between 6 attempts; backoff is 2 ** attempt.
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_semantic_scholar_module_imports_no_urllib_request() -> None:
    """Spec invariant 9 (Task 19): the provider does not import ``urllib.request``."""

    source_path = Path(semantic_scholar.__file__)
    # Explicit ``encoding="utf-8"`` — the autouse locale fixture sets
    # ``LC_ALL=C``, under which ``Path.read_text()``'s default encoding
    # falls back to ASCII and chokes on the source's em-dashes.
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "urllib.request":
            offenders.append(ast.dump(node))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "urllib.request":
                    offenders.append(ast.dump(node))

    assert offenders == [], (
        f"semantic_scholar.py must reach the network via research.http only; found {offenders}"
    )


def test_semantic_scholar_module_namespace_has_no_urlopen() -> None:
    assert "urlopen" not in semantic_scholar.__dict__
