"""Perplexity provider for ``wiki research`` (RFC-0001 Task 18).

POSTs the user's query to Perplexity's chat-completions endpoint with
the configured model and returns the assistant's content plus the
citations the API surfaces. The provider owns its own pre-conditions
(API-key env var presence and message-prefix wrapping) so the
dispatcher's contract stays provider-agnostic and Task 19's Gemini /
Semantic Scholar plug in by adding a sibling module here.

See ``docs/specs/task-18-research-perplexity/spec.md`` §"HTTP behavior
(Perplexity)".
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from llm_wiki_kit import __version__
from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.models import ProviderConfig
from llm_wiki_kit.research.http import ResearchHTTPError, request_json

PROVIDER_SLUG = "perplexity"
DEFAULT_ENDPOINT = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "sonar-pro"
DEFAULT_API_KEY_ENV = "PERPLEXITY_API_KEY"


@dataclass(frozen=True)
class PerplexityResult:
    """One Perplexity dispatch's parsed output.

    ``answer`` is the assistant's message content verbatim — the kit
    does not post-process or sanitize markdown. ``citations`` is the
    list of source URLs Perplexity surfaces (empty list when the API
    response omits the field). ``model`` is the resolved model name
    used for the call (so the renderer can record it in the page's
    frontmatter).
    """

    answer: str
    citations: list[str]
    model: str


def dispatch(config: ProviderConfig, query: str) -> PerplexityResult:
    """Call Perplexity with ``query`` and return the parsed result.

    Pre-conditions enforced here (not in the provider-agnostic HTTP
    helper):

    1. ``config.api_key_env`` must be set on the config block (or
       default to ``PERPLEXITY_API_KEY``) AND the corresponding
       environment variable must be exported. Missing env var raises
       ``WikiError(f"set {env_var} in the environment")`` *before*
       any HTTP attempt.
    2. ``ResearchHTTPError`` from the helper is caught and re-raised
       with a ``"perplexity: "`` message prefix so the user sees
       provider context. The helper itself stays provider-agnostic.

    Spec invariant 2 (API-key safety): the bearer token is composed
    inside this function, lives in the ``headers`` dict that gets
    handed to ``request_json``, and never leaks into the returned
    ``PerplexityResult`` or any raised exception's ``args`` /
    ``repr``. Verified by
    ``tests/unit/test_research_perplexity.py::test_perplexity_dispatch_key_redacted_in_errors``.
    """

    env_var = config.api_key_env or DEFAULT_API_KEY_ENV
    api_key = os.environ.get(env_var)
    if not api_key:
        raise WikiError(f"set {env_var} in the environment")

    endpoint = config.endpoint or DEFAULT_ENDPOINT
    model = config.model or DEFAULT_MODEL

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": f"llm-wiki-kit/{__version__}",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": query}],
    }

    try:
        response = request_json(
            method="POST",
            url=endpoint,
            headers=headers,
            json_body=body,
        )
    except ResearchHTTPError as exc:
        # Wrap with provider prefix so the user sees provenance in the
        # error message. The helper's exception already has redacted
        # message + status; we re-raise a fresh instance to avoid any
        # __cause__ chain (urllib HTTPError carries response headers
        # and a buffered fp whose repr could surface bytes — spec
        # invariant 2). ``raise … from None`` suppresses ``__context__``.
        raise ResearchHTTPError(f"perplexity: {exc}", status=exc.status) from None

    return _parse_response(response, model)


def _parse_response(payload: dict[str, object], model: str) -> PerplexityResult:
    """Extract ``answer``, ``citations``, and resolved ``model`` from the body.

    Defensive — Perplexity occasionally returns variants. The kit reads
    the documented shape (``choices[0].message.content`` plus
    top-level ``citations``) and raises ``ResearchHTTPError("perplexity:
    malformed response")`` if the keys aren't there. ``citations`` is
    absent on some Perplexity model variants — that case returns an
    empty list, not an error.
    """

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ResearchHTTPError("perplexity: malformed response", status=None)
    first = choices[0]
    if not isinstance(first, dict):
        raise ResearchHTTPError("perplexity: malformed response", status=None)
    message = first.get("message")
    if not isinstance(message, dict):
        raise ResearchHTTPError("perplexity: malformed response", status=None)
    content = message.get("content")
    if not isinstance(content, str):
        raise ResearchHTTPError("perplexity: malformed response", status=None)

    citations_raw = payload.get("citations", [])
    citations: list[str]
    if isinstance(citations_raw, list):
        citations = [c for c in citations_raw if isinstance(c, str)]
    else:
        citations = []

    return PerplexityResult(answer=content, citations=citations, model=model)
