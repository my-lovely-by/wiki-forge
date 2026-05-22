"""Unit tests for ``llm_wiki_kit.research.http`` (RFC-0001 Task 18).

Pins the retry-and-backoff contract spec §"HTTP behavior" names and the
API-key-safety invariant: ``ResearchHTTPError`` carries only a message
and a status code; no ``Request``, no headers dict, no body.
"""

from __future__ import annotations

import io
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from llm_wiki_kit.research import http
from llm_wiki_kit.research.http import ResearchHTTPError, request_json


@pytest.fixture
def fake_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record ``time.sleep`` calls without actually sleeping.

    The fixture lets each test assert on the backoff timeline without
    paying real-world delay.
    """

    sleeps: list[float] = []

    def _record(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("llm_wiki_kit.research.http.time.sleep", _record)
    return sleeps


def _http_error(code: int) -> HTTPError:
    """Build an ``HTTPError`` with a recognisable but harmless body.

    The body deliberately contains a string that, if it ever leaked
    into a ``ResearchHTTPError.repr``, would fail the redaction test
    below — the bearer prefix is the canonical leak shape.
    """

    body = b'{"detail": "Bearer sk-DO-NOT-LOG should never leak"}'
    return HTTPError(
        url="https://api.example.com/x",
        code=code,
        msg=f"status {code}",
        hdrs={"Content-Type": "application/json"},  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )


def _make_fake_urlopen(
    responses: list[Any],
) -> tuple[Any, list[int]]:
    """Return a fake ``urlopen`` and a counter list.

    Each item in ``responses`` is either an exception to raise or a
    bytes body to return. The counter records how many times the fake
    was invoked, so tests can assert on attempt count.
    """

    calls = [0]

    def _fake(request: Any, timeout: float = 0.0) -> Any:
        idx = calls[0]
        calls[0] += 1
        if idx >= len(responses):
            raise AssertionError(f"unexpected extra urlopen call #{idx + 1}")
        item = responses[idx]
        if isinstance(item, BaseException):
            raise item

        class _Response:
            def __init__(self, body: bytes) -> None:
                self._body = body

            def read(self) -> bytes:
                return self._body

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

        return _Response(item)

    return _fake, calls


def _make_fake_urlopen_recording(
    responses: list[Any],
) -> tuple[Any, list[int], list[Any]]:
    """Variant of ``_make_fake_urlopen`` that also records the ``Request`` instances.

    Step 1 (Task 19) needs to inspect the underlying ``Request.data`` and
    ``Request.get_method()`` to prove the ``json_body=None`` path
    omits the request body. Existing tests use ``_make_fake_urlopen``
    and don't care; this variant adds a third return value with the
    captured request objects.
    """

    calls = [0]
    requests: list[Any] = []

    def _fake(request: Any, timeout: float = 0.0) -> Any:
        idx = calls[0]
        calls[0] += 1
        requests.append(request)
        if idx >= len(responses):
            raise AssertionError(f"unexpected extra urlopen call #{idx + 1}")
        item = responses[idx]
        if isinstance(item, BaseException):
            raise item

        class _Response:
            def __init__(self, body: bytes) -> None:
                self._body = body

            def read(self) -> bytes:
                return self._body

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

        return _Response(item)

    return _fake, calls, requests


def test_request_json_200_returns_parsed_dict(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    fake, calls = _make_fake_urlopen([b'{"k": 1}'])
    monkeypatch.setattr(http, "urlopen", fake)

    out = request_json(
        method="POST",
        url="https://example/x",
        headers={"Authorization": "Bearer sk-DO-NOT-LOG"},
        json_body={"q": "hello"},
    )

    assert out == {"k": 1}
    assert calls == [1]
    assert fake_sleep == []


def test_request_json_none_body_omits_data_arg(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    """``json_body=None`` builds the ``Request`` without ``data=``.

    The wire request is then an honest GET with no body — Semantic
    Scholar's GET path depends on this. Spec invariant added in
    Task 19.
    """

    fake, _, requests = _make_fake_urlopen_recording([b'{"ok": true}'])
    monkeypatch.setattr(http, "urlopen", fake)

    out = request_json(
        method="GET",
        url="https://api.example/x",
        headers={},
        json_body=None,
    )

    assert out == {"ok": True}
    assert len(requests) == 1
    assert requests[0].data is None
    assert requests[0].get_method() == "GET"


def test_request_json_dict_body_unchanged(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    """Pre-Task-19 callers continue to send JSON bytes verbatim."""

    fake, _, requests = _make_fake_urlopen_recording([b'{"ok": true}'])
    monkeypatch.setattr(http, "urlopen", fake)

    request_json(
        method="POST",
        url="https://api.example/x",
        headers={},
        json_body={"k": 1},
    )

    assert requests[0].data == b'{"k": 1}'
    assert requests[0].get_method() == "POST"


def test_request_json_empty_dict_body_still_sends_data(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    """``json_body={}`` distinguishes from ``json_body=None`` by sending ``b'{}'``."""

    fake, _, requests = _make_fake_urlopen_recording([b'{"ok": true}'])
    monkeypatch.setattr(http, "urlopen", fake)

    request_json(method="POST", url="https://api.example/x", headers={}, json_body={})

    assert requests[0].data == b"{}"


def test_request_json_default_json_body_is_none(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    """Omitting ``json_body=`` entirely defaults to ``None`` — no body sent.

    Pins the signature change includes a default value, not just a
    wider type annotation. A future regression where the parameter
    becomes required would surface here.
    """

    fake, _, requests = _make_fake_urlopen_recording([b'{"ok": true}'])
    monkeypatch.setattr(http, "urlopen", fake)

    request_json(method="GET", url="https://api.example/x", headers={})

    assert requests[0].data is None
    assert requests[0].get_method() == "GET"


def test_request_json_429_then_200_retries_with_backoff(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    fake, calls = _make_fake_urlopen([_http_error(429), b'{"ok": true}'])
    monkeypatch.setattr(http, "urlopen", fake)

    out = request_json(
        method="POST",
        url="https://example/x",
        headers={},
        json_body={"q": "hello"},
    )

    assert out == {"ok": True}
    assert calls == [2]
    # One retry after the initial attempt → one backoff at 2**0 = 1.0s.
    assert fake_sleep == [1.0]


def test_request_json_429_until_exhausted_raises_after_three_retries(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    fake, calls = _make_fake_urlopen([_http_error(429)] * 4)
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError) as exc_info:
        request_json(
            method="POST",
            url="https://example/x",
            headers={},
            json_body={},
        )

    assert "after 3 retries" in str(exc_info.value)
    assert exc_info.value.status == 429
    # Four attempts total → three sleeps between them at 2**{0,1,2}.
    assert fake_sleep == [1.0, 2.0, 4.0]
    assert calls == [4]


def test_request_json_500_retries(monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]) -> None:
    fake, calls = _make_fake_urlopen([_http_error(500)] * 4)
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError):
        request_json(method="POST", url="https://example/x", headers={}, json_body={})

    assert calls == [4]
    assert fake_sleep == [1.0, 2.0, 4.0]


def test_request_json_401_no_retry(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    fake, calls = _make_fake_urlopen([_http_error(401)])
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError) as exc_info:
        request_json(method="POST", url="https://example/x", headers={}, json_body={})

    assert str(exc_info.value) == "HTTP 401"
    assert exc_info.value.status == 401
    assert calls == [1]
    assert fake_sleep == []


def test_request_json_422_no_retry(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    """422 is not in the retry-eligible set — surfaces immediately."""

    fake, calls = _make_fake_urlopen([_http_error(422)])
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError) as exc_info:
        request_json(method="POST", url="https://example/x", headers={}, json_body={})

    assert exc_info.value.status == 422
    assert calls == [1]
    assert fake_sleep == []


def test_request_json_urlerror_retries(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    fake, calls = _make_fake_urlopen([URLError("connection refused")] * 4)
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError) as exc_info:
        request_json(method="POST", url="https://example/x", headers={}, json_body={})

    assert "connection failed after 3 retries" in str(exc_info.value)
    assert exc_info.value.status is None
    assert calls == [4]
    assert fake_sleep == [1.0, 2.0, 4.0]


def test_request_json_timeout_retries(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    fake, calls = _make_fake_urlopen([TimeoutError("timed out")] * 4)
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError):
        request_json(method="POST", url="https://example/x", headers={}, json_body={})

    assert calls == [4]
    assert fake_sleep == [1.0, 2.0, 4.0]


def test_request_json_malformed_json_no_retry(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    fake, calls = _make_fake_urlopen([b"not json"])
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError) as exc_info:
        request_json(method="POST", url="https://example/x", headers={}, json_body={})

    assert "malformed response" in str(exc_info.value)
    assert exc_info.value.status is None
    assert calls == [1]
    assert fake_sleep == []


def test_request_json_non_dict_top_level_no_retry(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    """A JSON list (not dict) at the top level is treated as malformed.

    Providers we care about — chat completions, search — all return
    objects. A list response means the API shape doesn't match what
    the caller asked for and retrying won't make it match.
    """

    fake, _ = _make_fake_urlopen([b"[1, 2, 3]"])
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError) as exc_info:
        request_json(method="POST", url="https://example/x", headers={}, json_body={})

    assert "malformed response" in str(exc_info.value)


def test_research_http_error_repr_omits_request_objects(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    """API key never appears in ``str(exc)``, ``repr(exc)``, or ``exc.args``.

    Spec invariant 2: ``ResearchHTTPError`` carries only a message and
    status code. Even when the underlying ``HTTPError`` chain carries a
    body string that includes the bearer prefix, the wrapper exception
    surfaces neither the chain nor the body. ``raise … from None``
    suppresses the cause chain so ``__cause__`` is also clean.
    """

    fake, _ = _make_fake_urlopen([_http_error(401)])
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError) as exc_info:
        request_json(
            method="POST",
            url="https://example/x",
            headers={"Authorization": "Bearer sk-DO-NOT-LOG"},
            json_body={"q": "hello"},
        )

    exc = exc_info.value
    assert "sk-DO-NOT-LOG" not in str(exc)
    assert "sk-DO-NOT-LOG" not in repr(exc)
    assert all("sk-DO-NOT-LOG" not in repr(arg) for arg in exc.args)
    # Cause chain must not surface the HTTPError (which carries hdrs / fp).
    assert exc.__cause__ is None


def test_research_http_error_repr_clean_on_urlerror(
    monkeypatch: pytest.MonkeyPatch, fake_sleep: list[float]
) -> None:
    """Same redaction guarantee on the network-failure path.

    ``URLError`` can carry the original ``Request`` via ``reason`` in
    some constructions; the helper must not let that chain through.
    """

    fake, _ = _make_fake_urlopen([URLError("connection refused")] * 4)
    monkeypatch.setattr(http, "urlopen", fake)

    with pytest.raises(ResearchHTTPError) as exc_info:
        request_json(
            method="POST",
            url="https://example/x",
            headers={"Authorization": "Bearer sk-DO-NOT-LOG"},
            json_body={"q": "hello"},
        )

    exc = exc_info.value
    assert "sk-DO-NOT-LOG" not in repr(exc)
    assert exc.__cause__ is None
