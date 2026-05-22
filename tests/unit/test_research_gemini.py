"""Unit tests for ``llm_wiki_kit.research.providers.gemini`` (RFC-0001 Task 19).

Mocks ``request_json`` so no real HTTP fires; asserts on the wire-shape
the provider builds (URL, headers, body), on grounded-citation parsing
(including non-``web`` chunk shapes), and on the API-key-safety
invariants pinned by spec invariants 2 and 4.

The fixture key ``gk-DO-NOT-LOG`` is intentionally provider-specific
(distinct from Perplexity's ``sk-`` and Semantic Scholar's ``ss-``) so
the URL-redaction tests distinguish the gemini provider's leak surface
from the others. Do not consolidate the three prefixes.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.models import ProviderConfig
from llm_wiki_kit.research import http as http_module
from llm_wiki_kit.research.http import ResearchHTTPError
from llm_wiki_kit.research.providers import gemini
from llm_wiki_kit.research.providers.gemini import (
    DEFAULT_MODEL,
    GeminiResult,
    dispatch,
)


@pytest.fixture
def gemini_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set ``GEMINI_API_KEY`` to a recognisable but harmless value."""

    key = "gk-DO-NOT-LOG"
    monkeypatch.setenv("GEMINI_API_KEY", key)
    return key


def _canonical_response() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": "Async runtimes in Rust: tokio dominates."}]},
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://tokio.rs/"}},
                        {"web": {"uri": "https://async-std.rs/"}},
                    ]
                },
            }
        ]
    }


def _record_request_json(monkeypatch: pytest.MonkeyPatch, response: Any) -> list[dict[str, Any]]:
    """Replace ``gemini.request_json`` with a recorder.

    Patches the symbol on the gemini module (the import-time binding),
    not on ``llm_wiki_kit.research.http`` directly — tests assert on
    how the provider calls the helper, not on the helper itself.
    """

    calls: list[dict[str, Any]] = []

    def _fake(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(gemini, "request_json", _fake)
    return calls


def test_gemini_dispatch_happy_path(monkeypatch: pytest.MonkeyPatch, gemini_env: str) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
        query="what are rust async runtimes",
    )

    assert isinstance(result, GeminiResult)
    assert result.answer == "Async runtimes in Rust: tokio dominates."
    assert result.citations == ["https://tokio.rs/", "https://async-std.rs/"]
    assert result.model == DEFAULT_MODEL

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith(f"models/{DEFAULT_MODEL}:generateContent")
    assert call["headers"]["x-goog-api-key"] == gemini_env
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["User-Agent"].startswith("llm-wiki-kit/")
    assert call["json_body"] == {
        "contents": [{"role": "user", "parts": [{"text": "what are rust async runtimes"}]}],
        "tools": [{"google_search": {}}],
    }


def test_gemini_dispatch_url_omits_api_key_value(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    """Spec invariant 4: the API key never appears in the URL.

    Asserts both (a) common Google-style query-param names aren't in
    the URL, and (b) the literal key value isn't a substring. The
    second assertion catches a future bug where the URL is built with
    a different parameter name.
    """

    calls = _record_request_json(monkeypatch, _canonical_response())

    dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
        query="q",
    )

    url = calls[0]["url"]
    assert "?key=" not in url
    assert "&key=" not in url
    assert "?api_key=" not in url
    assert gemini_env not in url


def test_gemini_dispatch_missing_env_var_raises_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    calls = _record_request_json(monkeypatch, _canonical_response())

    with pytest.raises(WikiError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
            query="q",
        )

    assert "set GEMINI_API_KEY in the environment" in str(exc_info.value)
    assert calls == []


def test_gemini_dispatch_missing_env_var_uses_resolved_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``config.api_key_env`` override names *that* variable in the error."""

    monkeypatch.delenv("MY_GEMINI_KEY", raising=False)
    calls = _record_request_json(monkeypatch, _canonical_response())

    with pytest.raises(WikiError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "MY_GEMINI_KEY"}),
            query="q",
        )

    assert "set MY_GEMINI_KEY in the environment" in str(exc_info.value)
    # The default literal must NOT appear when the override resolves elsewhere.
    assert "GEMINI_API_KEY" not in str(exc_info.value)
    assert calls == []


def test_gemini_dispatch_no_grounding_metadata_returns_empty_citations(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    """Gemini may omit ``groundingMetadata`` for ungrounded answers — not an error."""

    response = {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}
    _record_request_json(monkeypatch, response)

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
        query="q",
    )

    assert result.citations == []
    assert result.answer == "answer"


def test_gemini_dispatch_non_web_chunks_skipped(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    """``retrievedContext`` and malformed chunks skip silently — Gemini real-shape."""

    response = {
        "candidates": [
            {
                "content": {"parts": [{"text": "a"}]},
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://a"}},
                        {"retrievedContext": {"uri": "corpus://x"}},
                        {},
                        {"web": {"uri": "https://b"}},
                    ]
                },
            }
        ]
    }
    _record_request_json(monkeypatch, response)

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
        query="q",
    )

    assert result.citations == ["https://a", "https://b"]


def test_gemini_dispatch_multiple_text_parts_concatenated(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    """``parts[*].text`` strings concatenate with empty-string join; non-text skips."""

    response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "a"},
                        {"thoughtSignature": "ignored"},
                        {"text": "b"},
                    ]
                }
            }
        ]
    }
    _record_request_json(monkeypatch, response)

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
        query="q",
    )

    assert result.answer == "ab"


def test_gemini_dispatch_duplicate_citation_uris_deduped(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    response = {
        "candidates": [
            {
                "content": {"parts": [{"text": "x"}]},
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://a"}},
                        {"web": {"uri": "https://a"}},
                        {"web": {"uri": "https://b"}},
                    ]
                },
            }
        ]
    }
    _record_request_json(monkeypatch, response)

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
        query="q",
    )

    assert result.citations == ["https://a", "https://b"]


def test_gemini_dispatch_malformed_response_no_text_parts(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    """No string-typed ``text`` in any part — kit cannot synthesize an answer."""

    response = {"candidates": [{"content": {"parts": [{"thoughtSignature": "x"}]}}]}
    _record_request_json(monkeypatch, response)

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
            query="q",
        )

    assert "gemini: malformed response" in str(exc_info.value)


def test_gemini_dispatch_malformed_response_missing_candidates(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    _record_request_json(monkeypatch, {"other": "shape"})

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
            query="q",
        )

    assert "gemini: malformed response" in str(exc_info.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"candidates": []},  # empty candidates list
        {"candidates": [{}]},  # no content key
        {"candidates": [{"content": {}}]},  # no parts key
        {"candidates": [{"content": {"parts": "not a list"}}]},  # parts not a list
        {"candidates": [{"content": {"parts": [{}]}}]},  # part with no text
        {"candidates": ["not a dict"]},  # first candidate not a dict
    ],
)
def test_gemini_dispatch_malformed_response_shapes_raise(
    monkeypatch: pytest.MonkeyPatch,
    gemini_env: str,
    payload: dict[str, Any],
) -> None:
    """Each malformed shape surfaces ``ResearchHTTPError`` with the documented prefix.

    Pins that pre-condition validation lives in the provider (raises
    a typed ``ResearchHTTPError`` with the ``gemini:`` prefix), not
    in the dispatcher. A future refactor that drops one ``isinstance``
    check would let a ``KeyError``, ``TypeError``, or ``AttributeError``
    bubble out of the provider — the dispatcher would not know to
    wrap that as a research dispatch error, and the user would see a
    raw traceback instead of a one-line message.
    """

    _record_request_json(monkeypatch, payload)

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
            query="q",
        )

    assert "gemini: malformed response" in str(exc_info.value)


def test_gemini_dispatch_good_parts_with_broken_grounding_chunks(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    """Well-formed text + non-list ``groundingChunks`` → answer + empty citations.

    The citation extractor is purely defensive — a malformed grounding
    payload should not destroy a recoverable answer. Inverse of the
    malformed-parts case where the answer is the load-bearing field.
    """

    response = {
        "candidates": [
            {
                "content": {"parts": [{"text": "answer"}]},
                "groundingMetadata": {"groundingChunks": "broken"},
            }
        ]
    }
    _record_request_json(monkeypatch, response)

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
        query="q",
    )

    assert result.answer == "answer"
    assert result.citations == []


def test_gemini_dispatch_endpoint_override(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())
    config = ProviderConfig.model_validate(
        {
            "api_key_env": "GEMINI_API_KEY",
            "endpoint": "https://proxy.example/v1/models/gemini-2.5-pro:generateContent",
        }
    )

    dispatch(config=config, query="q")

    assert calls[0]["url"] == ("https://proxy.example/v1/models/gemini-2.5-pro:generateContent")


def test_gemini_dispatch_model_override_builds_url(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())
    config = ProviderConfig.model_validate(
        {"api_key_env": "GEMINI_API_KEY", "model": "gemini-2.5-flash"}
    )

    result = dispatch(config=config, query="q")

    assert result.model == "gemini-2.5-flash"
    assert calls[0]["url"].endswith("models/gemini-2.5-flash:generateContent")


def test_gemini_dispatch_default_api_key_env_when_unset_on_config(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    """``config.api_key_env`` defaults to ``GEMINI_API_KEY``."""

    _record_request_json(monkeypatch, _canonical_response())

    dispatch(config=ProviderConfig.model_validate({}), query="q")
    # Fixture's setenv is the default env var — no exception means it was found.


def test_gemini_dispatch_wraps_http_error_with_prefix(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    _record_request_json(monkeypatch, ResearchHTTPError("HTTP 401", status=401))

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
            query="q",
        )

    assert str(exc_info.value) == "gemini: HTTP 401"
    assert exc_info.value.status == 401


def test_gemini_dispatch_key_redacted_in_errors(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    """Spec invariant 2: the API key never appears in any exception surface."""

    _record_request_json(monkeypatch, ResearchHTTPError("HTTP 401", status=401))

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
            query="q",
        )

    exc = exc_info.value
    assert gemini_env not in str(exc)
    assert gemini_env not in repr(exc)
    assert all(gemini_env not in repr(arg) for arg in exc.args)
    assert exc.__cause__ is None


def test_gemini_dispatch_real_http_helper_passes_through(
    monkeypatch: pytest.MonkeyPatch, gemini_env: str
) -> None:
    """End-to-end through the real ``request_json`` (with ``urlopen`` mocked).

    Verifies the provider routes through ``request_json`` and not
    around it.
    """

    calls = [0]

    def _fake_urlopen(request: Any, timeout: float = 0.0) -> Any:
        calls[0] += 1

        class _Response:
            def read(self) -> bytes:
                return b'{"candidates":[{"content":{"parts":[{"text":"hi"}]}}]}'

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

        return _Response()

    monkeypatch.setattr(http_module, "urlopen", _fake_urlopen)

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "GEMINI_API_KEY"}),
        query="q",
    )

    assert result.answer == "hi"
    assert calls == [1]


def test_gemini_module_imports_no_urllib_request() -> None:
    """Spec invariant 9: the provider must not import ``urllib.request``.

    AST-grep over the source file rather than ``__dict__`` so a
    ``from urllib.request import urlopen as _x`` aliased import is
    caught even when the alias isn't a public namespace name.
    """

    source_path = Path(gemini.__file__)
    # Explicit ``encoding="utf-8"`` — defends against any future
    # autouse fixture that forces ``LC_ALL=C``, under which
    # ``Path.read_text()``'s default encoding falls back to ASCII
    # and chokes on the source's em-dashes.
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
        f"gemini.py must reach the network via research.http only; found {offenders}"
    )


def test_gemini_module_namespace_has_no_urlopen() -> None:
    assert "urlopen" not in gemini.__dict__
