"""Provider-agnostic HTTP helper for ``wiki research``.

Wraps ``urllib.request.urlopen`` with bounded retry-and-backoff on
transient failures (HTTP 429, 5xx, ``URLError``, ``socket.timeout``).
Other 4xx and malformed-JSON responses raise immediately — the server
either rejected our request or returned something we cannot parse, and
retrying either is silly.

The retry contract: ``max_retries=3`` retries after the initial attempt
(four attempts total), with backoff ``2 ** attempt`` between retries
(``[1.0, 2.0, 4.0]``). No jitter — small N, deterministic timing is
easier to assert against a fake ``time.sleep`` than to fuzz.

API-key safety (spec invariant 2): ``ResearchHTTPError`` carries only a
human-readable message string and a numeric status code (when known).
The headers dict, the ``urllib.request.Request`` instance, and the
request body all live in locals that go out of scope on raise — they
are never logged, stringified, or attached to the exception. A caller
who sets ``--verbose`` and observes a traceback sees the message and
the status code, not the bearer token.

See ``docs/specs/task-18-research-perplexity/spec.md`` §HTTP behavior.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from llm_wiki_kit.errors import WikiError

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3


class ResearchHTTPError(WikiError):
    """HTTP-layer failure raised by :func:`request_json`.

    Constructor signature is deliberately narrow: a message string and
    an optional integer status code. Nothing else is stored — not the
    request URL (which could be useful but isn't worth the chance the
    URL itself ever carries a bearer-style query param), not the
    headers, not the body, not the ``HTTPError`` instance that caused
    it. Spec invariant 2 — API keys must not reach exception surfaces —
    is enforced here at the constructor; nothing in the retry loop
    saves a ``Request`` object or stringifies the headers dict.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# 4xx codes that retry-eligible behaviour explicitly excludes. 429 is
# the only client-error code we retry — the server is asking us to slow
# down, not telling us our request is bad. Other 4xx surfaces (auth,
# permission, validation, not-found) are user-configuration errors.
_RETRY_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _is_retry_eligible_http(exc: HTTPError) -> bool:
    """Return True if this HTTPError is one we should back off and retry.

    Whitelist over blacklist so a new 5xx code we don't know about
    still triggers retry, while a stray 422 from a misconfigured
    request fails fast.
    """

    return exc.code in _RETRY_HTTP_STATUSES or 500 <= exc.code < 600


def request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """POST/GET JSON to ``url`` with bounded retry and timeout.

    On success, returns the parsed JSON response body as a dict. On any
    error after retries are exhausted, raises :class:`ResearchHTTPError`
    with a redacted message and (when known) the final status code.

    Pass ``json_body=None`` for true GETs that should not ship a body —
    the underlying :class:`urllib.request.Request` is built without
    ``data=``, so the wire request has no body. Pass a dict (including
    the empty dict ``{}``) to send a JSON body. The default is
    ``None``; Task 18's Perplexity provider passes a dict and continues
    to send JSON; Task 19's Semantic Scholar GET path passes ``None``.

    The retry-eligible exception set is ``HTTPError`` with status in
    ``_RETRY_HTTP_STATUSES`` (or any 5xx), ``URLError``, and
    ``socket.timeout``. With ``max_retries=3`` that means four attempts
    total — index 0 (initial) plus indices 1, 2, 3 (retries) — with
    backoff intervals ``[2 ** 0, 2 ** 1, 2 ** 2]`` between them. Other
    HTTP statuses (e.g. 401, 403, 404, 422) raise immediately. Malformed
    JSON in a 200 response also raises immediately — the server said
    "OK" and the bytes lied; retrying would only mask the lie.
    """

    body_bytes: bytes | None
    if json_body is None:
        body_bytes = None
    else:
        body_bytes = json.dumps(json_body).encode("utf-8")

    last_status: int | None = None
    last_message: str | None = None

    for attempt in range(max_retries + 1):
        # Build a fresh Request per attempt so a previous attempt's
        # object lifetime is bounded by this iteration. The headers
        # dict is the caller's; we don't mutate or stash it. When
        # ``body_bytes`` is ``None``, the ``Request`` is built without
        # ``data=`` so the wire request has no body — this is the
        # ``json_body=None`` path the helper offers for true GETs.
        request_kwargs: dict[str, Any] = {
            "url": url,
            "headers": dict(headers),
            "method": method,
        }
        if body_bytes is not None:
            request_kwargs["data"] = body_bytes
        request = Request(**request_kwargs)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                # 200 with malformed bytes. Retrying would only mask the
                # lie. No status code (the HTTP layer succeeded; this
                # is a content-shape failure).
                raise ResearchHTTPError("malformed response", status=None) from exc
            if not isinstance(parsed, dict):
                raise ResearchHTTPError("malformed response", status=None)
            return parsed
        except HTTPError as exc:
            last_status = exc.code
            last_message = f"HTTP {exc.code}"
            if not _is_retry_eligible_http(exc):
                # Fatal — fail immediately. Note that the original
                # HTTPError is NOT chained via ``from exc``: ``HTTPError``
                # carries ``hdrs`` (response headers) and a buffered
                # ``fp`` whose repr could surface bytes in a
                # ``--verbose`` traceback. Spec invariant 2.
                raise ResearchHTTPError(last_message, status=last_status) from None
            # Retry-eligible — fall through to the post-attempt
            # decision below.
        except (URLError, TimeoutError) as exc:
            # ``socket.timeout`` aliases ``TimeoutError`` on Python
            # 3.10+, so the two-name pattern collapses to one. URLError
            # wraps an inner OSError whose ``repr`` may include
            # resolved hostnames, socket family info, or the original
            # ``Request`` via ``reason`` in some constructions —
            # suppressing the cause chain keeps that out of any
            # ``--verbose`` traceback (cf. spec invariant 2 redaction).
            last_status = None
            last_message = "connection failed"
            del exc  # explicit: do not let the local survive into the raise
            if attempt < max_retries:
                time.sleep(2**attempt)
                continue
            raise ResearchHTTPError(
                f"{last_message} after {max_retries} retries",
                status=None,
            ) from None

        # Retry-eligible HTTPError path. If retries remain, sleep and
        # try again; otherwise raise with the "after N retries" suffix.
        if attempt < max_retries:
            time.sleep(2**attempt)
            continue
        raise ResearchHTTPError(
            f"{last_message} after {max_retries} retries",
            status=last_status,
        ) from None

    # Unreachable: the loop either returns a dict or raises.
    raise AssertionError("retry loop terminated without result")
