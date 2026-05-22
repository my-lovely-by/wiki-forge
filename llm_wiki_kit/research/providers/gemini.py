"""Gemini Deep Research provider for ``wiki research`` (RFC-0001 Task 19).

POSTs the user's query to Google's Generative Language API
``generateContent`` endpoint with the ``google_search`` tool enabled,
returning the assistant's grounded synthesis plus the citation URIs
the API attached via ``groundingMetadata``. Plugs into the dispatch
contract Task 18 established — same shape as Perplexity, different
wire format.

The "Deep Research" surface is Google's consumer-product feature
built on top of grounded generation; the closest API equivalent is
``gemini-2.5-pro`` + ``google_search`` tool, which is what this
provider drives. A future dedicated Deep Research API endpoint can
land via a `config.model` / `config.endpoint` override without a
kit release.

See ``docs/specs/task-19-research-gemini-semscholar/spec.md`` §"HTTP
behavior (Gemini)".
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from llm_wiki_kit import __version__
from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.models import ProviderConfig
from llm_wiki_kit.research.http import ResearchHTTPError, request_json

PROVIDER_SLUG = "gemini"
DEFAULT_API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_MODEL = "gemini-2.5-pro"
DEFAULT_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


@dataclass(frozen=True)
class GeminiResult:
    """One Gemini dispatch's parsed output.

    ``answer`` is the concatenated text from
    ``candidates[0].content.parts[*].text``, joined with the empty
    string — no separator, no normalisation, no trim. The verbatim
    rule is a spec contract: a future template tweak (``.join("\\n")``,
    ``.strip()``) is a contract break, not an implementation detail.

    ``citations`` is the deduplicated list of URIs Gemini attached via
    ``groundingMetadata.groundingChunks[*].web.uri``. Non-``web`` chunk
    shapes (e.g. ``retrievedContext`` pointing at a corpus) are skipped
    without raising — they are part of the documented Gemini response
    shape, not an error.

    ``model`` is the resolved model name used for the call.
    """

    answer: str
    citations: list[str]
    model: str


def dispatch(config: ProviderConfig, query: str) -> GeminiResult:
    """Call Gemini with ``query`` and return the parsed grounded answer.

    Pre-conditions enforced here (not in the provider-agnostic HTTP
    helper):

    1. The env var named by ``config.api_key_env`` (defaulting to
       ``GEMINI_API_KEY``) must be set to a non-empty string. Missing
       raises ``WikiError(f"set {env_var} in the environment")`` —
       the *resolved* env-var name, so an ``api_key_env: MY_KEY``
       override surfaces ``MY_KEY`` in the message rather than the
       default literal. Raised *before* any HTTP attempt.
    2. ``ResearchHTTPError`` from the helper is caught and re-raised
       with a ``"gemini: "`` message prefix. ``raise … from None``
       suppresses the cause chain so the underlying ``HTTPError``
       (which carries response headers and a buffered fp) does not
       leak into a ``--verbose`` traceback's exception ``repr``.

    The API key is sent in the ``x-goog-api-key`` header — never as
    a URL query parameter. Spec invariant 4 pins this with a contract
    test that asserts the literal key value isn't a substring of the
    composed URL.
    """

    env_var = config.api_key_env or DEFAULT_API_KEY_ENV
    api_key = os.environ.get(env_var)
    if not api_key:
        raise WikiError(f"set {env_var} in the environment")

    model = config.model or DEFAULT_MODEL
    endpoint = config.endpoint or DEFAULT_ENDPOINT_TEMPLATE.format(model=model)

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
        "User-Agent": f"llm-wiki-kit/{__version__}",
    }
    body = {
        "contents": [{"role": "user", "parts": [{"text": query}]}],
        "tools": [{"google_search": {}}],
    }

    try:
        response = request_json(
            method="POST",
            url=endpoint,
            headers=headers,
            json_body=body,
        )
    except ResearchHTTPError as exc:
        # ``raise … from None`` suppresses ``__context__`` so an
        # HTTPError chain carrying response headers / body bytes does
        # not surface via the traceback formatter. Spec invariant 2.
        raise ResearchHTTPError(f"gemini: {exc}", status=exc.status) from None

    return _parse_response(response, model)


def _parse_response(payload: dict[str, object], model: str) -> GeminiResult:
    """Extract ``answer`` and ``citations`` from a Gemini ``generateContent`` body.

    Defensive against the documented variants:

    - ``parts`` may contain non-``text`` entries (``inlineData``,
      ``functionCall``, ``thoughtSignature``) which are skipped.
    - ``groundingMetadata`` may be absent (Gemini omits it for queries
      answered without grounding) — yields an empty citations list,
      not a malformed-response error.
    - ``groundingChunks`` entries may be ``retrievedContext`` (corpus
      sources) or empty dicts — those are skipped silently, not
      raised. Only chunks with a string ``web.uri`` are kept.
    - If *no* part carries a string ``text``, raise — the kit cannot
      synthesize an answer body without text.
    """

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ResearchHTTPError("gemini: malformed response", status=None)
    first = candidates[0]
    if not isinstance(first, dict):
        raise ResearchHTTPError("gemini: malformed response", status=None)
    content = first.get("content")
    if not isinstance(content, dict):
        raise ResearchHTTPError("gemini: malformed response", status=None)
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise ResearchHTTPError("gemini: malformed response", status=None)

    answer_chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            answer_chunks.append(text)

    if not answer_chunks:
        raise ResearchHTTPError("gemini: malformed response", status=None)

    answer = "".join(answer_chunks)

    citations: list[str] = []
    grounding = first.get("groundingMetadata")
    if isinstance(grounding, dict):
        chunks = grounding.get("groundingChunks")
        if isinstance(chunks, list):
            seen: set[str] = set()
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                web = chunk.get("web")
                if not isinstance(web, dict):
                    continue
                uri = web.get("uri")
                if not isinstance(uri, str) or uri in seen:
                    continue
                seen.add(uri)
                citations.append(uri)

    return GeminiResult(answer=answer, citations=citations, model=model)
