"""Semantic Scholar Graph provider for ``wiki research`` (RFC-0001 Task 19).

GETs the user's query against the Semantic Scholar Graph API's
``paper/search`` endpoint, returns the top ``DEFAULT_LIMIT`` papers as
a deterministic markdown list (so the answer body is human-scannable
and the snapshot test stays meaningful), plus the per-paper URLs as
citations.

Unlike Perplexity and Gemini, Semantic Scholar offers a **keyless
tier** (~100 reqs / 5 min / IP). The provider passes ``json_body=None``
to the HTTP helper so the wire request is an honest GET, sends the
``x-api-key`` header only when the env var is set to a non-empty
string, and bumps ``max_retries`` from 3 to 5 in keyless mode to soak
the more aggressive rate-limit variance.

See ``docs/specs/task-19-research-gemini-semscholar/spec.md`` §"HTTP
behavior (Semantic Scholar)".
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass

from llm_wiki_kit import __version__
from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.models import ProviderConfig
from llm_wiki_kit.research.http import ResearchHTTPError, request_json

PROVIDER_SLUG = "semantic-scholar"
DEFAULT_API_KEY_ENV = "SEMANTIC_SCHOLAR_API_KEY"
DEFAULT_ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"
DEFAULT_MODEL = "graph-v1"
DEFAULT_FIELDS = "title,authors,year,abstract,url,venue"
DEFAULT_LIMIT = 10
KEYLESS_MAX_RETRIES = 5
KEYED_MAX_RETRIES = 3

_NO_METADATA_LINE = "*(no metadata)*"
_UNKNOWN_AUTHORS = "unknown authors"

_ENDPOINT_REJECT_MESSAGE = (
    "research-providers.yaml: semantic-scholar endpoint must be a bare "
    "scheme://host/path (no query, fragment, or userinfo)"
)


@dataclass(frozen=True)
class SemanticScholarResult:
    """One Semantic Scholar dispatch's parsed output.

    ``answer`` is the kit-rendered numbered list of papers (or the
    literal ``"No papers found.\\n"`` when the API returned no
    results). The renderer is deterministic given the API payload —
    pinned by the snapshot test in spec invariant 8.

    ``citations`` is the deduplicated list of per-paper ``url``
    values; papers missing a ``url`` are included in the body but
    excluded from this list.

    ``model`` is the literal ``"graph-v1"`` — Semantic Scholar's
    Graph API exposes no per-model knob, so this is a stable
    namespace identifier the journal records for audit, not a real
    model name.
    """

    answer: str
    citations: list[str]
    model: str


def dispatch(config: ProviderConfig, query: str) -> SemanticScholarResult:
    """Call Semantic Scholar's Graph paper-search and return the parsed result.

    The provider:

    1. Validates ``config.endpoint`` if set — bare ``scheme://host/path``
       only; ``?query``, ``#fragment``, and ``user:pass@`` userinfo
       are rejected with :class:`WikiError` before any HTTP attempt.
    2. Resolves the env var (defaulting to ``SEMANTIC_SCHOLAR_API_KEY``)
       and reads ``os.environ.get(env_var)``. An empty or unset value
       is **not** an error — Semantic Scholar has a keyless tier; the
       provider adapts (omit ``x-api-key`` header, bump retries).
    3. Composes the GET URL via ``urllib.parse.urlencode`` over an
       ordered tuple-list so the query string is byte-for-byte stable.
    4. Calls ``request_json(method="GET", json_body=None, ...)`` so
       the wire request has no body.
    5. Parses the response into a markdown list via
       :func:`_render_paper`; falls back to ``"No papers found.\\n"``
       when ``data`` is empty.

    The kit-side body rendering is the kit's, not Semantic Scholar's —
    the provider takes structured paper metadata and emits a
    human-readable list. The renderer is deterministic and
    locale-independent (spec invariant 8).
    """

    _validate_endpoint(config.endpoint)
    endpoint = config.endpoint or DEFAULT_ENDPOINT

    env_var = config.api_key_env or DEFAULT_API_KEY_ENV
    api_key = os.environ.get(env_var) or None

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": f"llm-wiki-kit/{__version__}",
    }
    max_retries = KEYED_MAX_RETRIES if api_key else KEYLESS_MAX_RETRIES
    if api_key:
        headers["x-api-key"] = api_key

    query_string = urllib.parse.urlencode(
        [
            ("query", query),
            ("limit", str(DEFAULT_LIMIT)),
            ("fields", DEFAULT_FIELDS),
        ],
        quote_via=urllib.parse.quote_plus,
    )
    url = f"{endpoint}?{query_string}"

    try:
        response = request_json(
            method="GET",
            url=url,
            headers=headers,
            json_body=None,
            max_retries=max_retries,
        )
    except ResearchHTTPError as exc:
        raise ResearchHTTPError(f"semantic-scholar: {exc}", status=exc.status) from None

    return _parse_response(response)


def _validate_endpoint(endpoint: str | None) -> None:
    """Reject endpoint overrides that carry query / fragment / userinfo.

    The provider owns the full query string; merging an override's
    existing params or fragments is out of scope. A clean bare URL
    keeps the substring-based URL assertions in the test suite honest
    and prevents credential leakage from
    ``https://user:tok@host/...`` overrides.
    """

    if endpoint is None:
        return
    split = urllib.parse.urlsplit(endpoint)
    # ``urlsplit("https://:tok@host/p")`` has ``username == ""`` (falsy)
    # but ``password == "tok"`` — check both to catch the userinfo
    # form regardless of which half is populated.
    if split.query or split.fragment or split.username or split.password:
        raise WikiError(_ENDPOINT_REJECT_MESSAGE)


def _parse_response(payload: object) -> SemanticScholarResult:
    """Build a :class:`SemanticScholarResult` from the API payload."""

    if not isinstance(payload, dict):
        raise ResearchHTTPError("semantic-scholar: malformed response", status=None)
    data = payload.get("data")
    if not isinstance(data, list):
        raise ResearchHTTPError("semantic-scholar: malformed response", status=None)

    if not data:
        return SemanticScholarResult(
            answer="No papers found.\n",
            citations=[],
            model=DEFAULT_MODEL,
        )

    lines: list[str] = []
    citations: list[str] = []
    seen_urls: set[str] = set()

    for index, paper in enumerate(data, start=1):
        if not isinstance(paper, dict):
            lines.append(f"{index}. {_NO_METADATA_LINE}\n")
            continue
        lines.append(_render_paper(index, paper))
        url = paper.get("url")
        if isinstance(url, str) and url and url not in seen_urls:
            seen_urls.add(url)
            citations.append(url)

    return SemanticScholarResult(
        answer="".join(lines),
        citations=citations,
        model=DEFAULT_MODEL,
    )


def _render_paper(index: int, paper: dict[str, object]) -> str:
    """Render one paper as a numbered markdown list item.

    Empty / missing scalar fields render as the empty string in their
    slot; an empty author list renders as ``unknown authors``. The
    fully-empty paper (no title, year, venue, abstract, url, *and* no
    string authors) renders as ``<n>. *(no metadata)*\\n`` so the
    line stays scannable rather than emitting a paragraph of stray
    punctuation.
    """

    title = _scalar(paper.get("title"))
    year = _scalar(paper.get("year"))
    venue = _scalar(paper.get("venue"))
    abstract = _scalar(paper.get("abstract"))
    url = _scalar(paper.get("url"))

    authors_raw = paper.get("authors")
    author_names: list[str] = []
    if isinstance(authors_raw, list):
        for entry in authors_raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name:
                author_names.append(name)
    authors_str = ", ".join(author_names) if author_names else _UNKNOWN_AUTHORS

    no_scalars = not (title or year or venue or abstract or url)
    if no_scalars and authors_str == _UNKNOWN_AUTHORS:
        return f"{index}. {_NO_METADATA_LINE}\n"

    return f"{index}. **{title}** ({year}) — {authors_str}. *{venue}*. {abstract}\n   {url}\n"


def _scalar(value: object) -> str:
    """Coerce a paper field to a string slot value.

    Strings pass through; integers / floats stringify (Semantic
    Scholar returns ``year`` as an integer); anything else (``None``,
    nested objects) becomes the empty string. This keeps the renderer
    deterministic against the documented response shape without
    inventing defensive logic for cases the API doesn't motivate.
    """

    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""
