"""Unit tests for ``llm_wiki_kit.research.providers.perplexity``.

Mocks ``request_json`` so no real HTTP fires; asserts on the wire-shape
the provider builds (URL, headers, body) and on its error-prefixing
and env-var-precondition behavior.
"""

from __future__ import annotations

from typing import Any

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.models import ProviderConfig
from llm_wiki_kit.research import http as http_module
from llm_wiki_kit.research.http import ResearchHTTPError
from llm_wiki_kit.research.providers import perplexity
from llm_wiki_kit.research.providers.perplexity import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    PerplexityResult,
    dispatch,
)


@pytest.fixture
def perplexity_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set ``PERPLEXITY_API_KEY`` to a recognisable but harmless value.

    The recognisable string lets API-key-safety tests grep for it
    across stdout, stderr, journal lines, exception ``repr``, and
    so on. The "DO-NOT-LOG" suffix makes the intent obvious to a
    future reader who stumbles onto the literal.
    """

    key = "sk-DO-NOT-LOG"
    monkeypatch.setenv("PERPLEXITY_API_KEY", key)
    return key


def _canonical_response() -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": "Async runtimes in Rust: tokio dominates."}}],
        "citations": ["https://tokio.rs/", "https://async-std.rs/"],
    }


def _record_request_json(monkeypatch: pytest.MonkeyPatch, response: Any) -> list[dict[str, Any]]:
    """Replace ``perplexity.request_json`` with a recorder.

    Patches the symbol on the perplexity module (the import-time
    binding), not on ``llm_wiki_kit.research.http`` directly — so
    tests assert on how the provider calls the helper, not on the
    helper itself.
    """

    calls: list[dict[str, Any]] = []

    def _fake(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(perplexity, "request_json", _fake)
    return calls


def test_perplexity_dispatch_happy_path(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "PERPLEXITY_API_KEY"}),
        query="what are rust async runtimes",
    )

    assert isinstance(result, PerplexityResult)
    assert result.answer == "Async runtimes in Rust: tokio dominates."
    assert result.citations == ["https://tokio.rs/", "https://async-std.rs/"]
    assert result.model == DEFAULT_MODEL

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == DEFAULT_ENDPOINT
    assert call["headers"]["Authorization"] == f"Bearer {perplexity_env}"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["User-Agent"].startswith("llm-wiki-kit/")
    assert call["json_body"] == {
        "model": DEFAULT_MODEL,
        "messages": [{"role": "user", "content": "what are rust async runtimes"}],
    }


def test_perplexity_dispatch_missing_env_var_raises_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    calls = _record_request_json(monkeypatch, _canonical_response())

    with pytest.raises(WikiError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "PERPLEXITY_API_KEY"}),
            query="q",
        )

    assert "set PERPLEXITY_API_KEY in the environment" in str(exc_info.value)
    assert calls == []  # never called the helper


def test_perplexity_dispatch_uses_endpoint_override(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())
    config = ProviderConfig.model_validate(
        {
            "api_key_env": "PERPLEXITY_API_KEY",
            "endpoint": "https://proxy.example/v1/chat",
        }
    )

    dispatch(config=config, query="q")

    assert calls[0]["url"] == "https://proxy.example/v1/chat"


def test_perplexity_dispatch_default_model_when_unset(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "PERPLEXITY_API_KEY"}),
        query="q",
    )

    assert result.model == "sonar-pro"
    assert calls[0]["json_body"]["model"] == "sonar-pro"


def test_perplexity_dispatch_uses_model_override(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    calls = _record_request_json(monkeypatch, _canonical_response())
    config = ProviderConfig.model_validate(
        {"api_key_env": "PERPLEXITY_API_KEY", "model": "sonar-deep-research"}
    )

    result = dispatch(config=config, query="q")

    assert result.model == "sonar-deep-research"
    assert calls[0]["json_body"]["model"] == "sonar-deep-research"


def test_perplexity_dispatch_no_citations_field(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    """A Perplexity variant that omits ``citations`` returns ``[]`` — not an error."""

    response = {"choices": [{"message": {"content": "answer body"}}]}
    _record_request_json(monkeypatch, response)

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "PERPLEXITY_API_KEY"}),
        query="q",
    )

    assert result.citations == []
    assert result.answer == "answer body"


def test_perplexity_dispatch_default_api_key_env_when_unset_on_config(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    """``api_key_env`` defaults to ``PERPLEXITY_API_KEY`` per provider default."""

    _record_request_json(monkeypatch, _canonical_response())

    dispatch(config=ProviderConfig.model_validate({}), query="q")
    # Used the default env var (already set by the fixture).


def test_perplexity_dispatch_wraps_http_error_with_perplexity_prefix(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    """``ResearchHTTPError`` from the helper is re-raised with provider prefix.

    The provider is the layer that knows it's Perplexity; the helper
    stays provider-agnostic.
    """

    _record_request_json(monkeypatch, ResearchHTTPError("HTTP 401", status=401))

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "PERPLEXITY_API_KEY"}),
            query="q",
        )

    assert str(exc_info.value) == "perplexity: HTTP 401"
    assert exc_info.value.status == 401


def test_perplexity_dispatch_key_redacted_in_errors(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    """The API key never appears in any raised exception's ``str``/``repr``/``args``.

    Spec invariant 2: even when the underlying helper raises with a
    recognisable status code, the provider's wrapping exception
    contains only the human-readable prefix + status, never the
    bearer token. ``raise … from None`` keeps the ``__cause__`` chain
    clean.
    """

    _record_request_json(monkeypatch, ResearchHTTPError("HTTP 401", status=401))

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "PERPLEXITY_API_KEY"}),
            query="q",
        )

    exc = exc_info.value
    assert perplexity_env not in str(exc)
    assert perplexity_env not in repr(exc)
    assert all(perplexity_env not in repr(arg) for arg in exc.args)
    assert exc.__cause__ is None


def test_perplexity_dispatch_malformed_choices(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    """Missing ``choices[0].message.content`` surfaces as a malformed response."""

    _record_request_json(monkeypatch, {"choices": []})

    with pytest.raises(ResearchHTTPError) as exc_info:
        dispatch(
            config=ProviderConfig.model_validate({"api_key_env": "PERPLEXITY_API_KEY"}),
            query="q",
        )
    assert "perplexity: malformed response" in str(exc_info.value)


def test_perplexity_dispatch_real_http_helper_passes_through_unchanged(
    monkeypatch: pytest.MonkeyPatch, perplexity_env: str
) -> None:
    """End-to-end through the real ``request_json`` (with ``urlopen`` mocked).

    Verifies the provider doesn't accidentally bypass the retry helper
    — and gives the API-key-redaction tests an unpatched
    ``request_json`` to exercise.
    """

    calls = [0]

    def _fake_urlopen(request: Any, timeout: float = 0.0) -> Any:
        calls[0] += 1

        class _Response:
            def read(self) -> bytes:
                return b'{"choices":[{"message":{"content":"hi"}}],"citations":[]}'

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

        return _Response()

    monkeypatch.setattr(http_module, "urlopen", _fake_urlopen)

    result = dispatch(
        config=ProviderConfig.model_validate({"api_key_env": "PERPLEXITY_API_KEY"}),
        query="q",
    )

    assert result.answer == "hi"
    assert calls == [1]
