"""Routing logic for ``wiki ingest`` (RFC-0001 Task 16).

The orchestrator picks a content-type primitive for a given source and
records the decision in the journal. It does **not** fetch URLs, parse
PDFs, or invoke an LLM — those are the user's Claude session's job,
which runs the chosen ``ingest-<name>/SKILL.md`` after the route is in
the journal. The kit ships no LLM (charter principle:
library-not-application), so ``wiki ingest`` is a routing decision plus
a journal append, no more.

Detection in v0.1 covers four signal kinds, all declared per-primitive
in ``primitive.yaml``'s optional ``routing:`` block:

* ``file_extensions`` — case-insensitive suffix match (``.pdf``).
* ``filename_patterns`` — ``fnmatch`` glob against the basename
  (``EOB-*``, ``*receipt*``).
* ``url_domains`` — ``fnmatch`` glob against the URL host
  (``allrecipes.com``, ``*.bonappetit.com``).
* ``url_path_patterns`` — ``fnmatch`` glob against the URL path
  (``/recipe/*``).

Magic bytes, mimetype sniffing, paste-content heuristics, and any kind
of network I/O are deliberately out of scope for v0.1 — they either
need new runtime dependencies (ADR territory) or open the door to
brittle silent routing. The "I cannot tell" path always exists and
points the user at ``--as <name>``.

All functions here are pure with one exception that does no I/O:
``classify_source`` does string parsing only.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlparse

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.models import Primitive, PrimitiveKind


class SourceKind(StrEnum):
    URL = "url"
    FILE = "file"
    STDIN = "stdin"


@dataclass(frozen=True)
class ClassifiedSource:
    """The structured view of a CLI ``<source>`` argument.

    ``kind`` decides which routing rules may fire; the other fields are
    pre-computed once so :func:`route` can fnmatch without re-parsing
    per candidate primitive.
    """

    kind: SourceKind
    raw: str
    url_host: str = ""
    url_path: str = ""
    filename: str = ""
    suffix: str = ""  # lower-cased, includes the leading dot


@dataclass(frozen=True)
class Routed:
    """The source resolved to one content-type primitive."""

    content_type: str
    signals: list[str]
    via: Literal["auto", "as_flag"]


@dataclass(frozen=True)
class Ambiguous:
    """Two or more content-type primitives matched the source."""

    candidates: list[str]


@dataclass(frozen=True)
class NoMatch:
    """No rule fired and ``--as`` was not supplied.

    ``available`` is the sorted list of installed content-type primitives,
    suitable for printing as a hint.
    """

    available: list[str] = field(default_factory=list)


RouteResult = Routed | Ambiguous | NoMatch


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------


def classify_source(source: str) -> ClassifiedSource:
    """Split ``source`` into ``kind`` plus the fields routing rules read.

    Three kinds: ``url`` (``http(s)://...``), ``stdin`` (``-``), or
    ``file`` (anything else). No filesystem access — existence checks
    live at the CLI boundary because the routing logic itself is pure.
    """

    if source == "-":
        return ClassifiedSource(kind=SourceKind.STDIN, raw=source)

    lower = source.lower()
    if lower.startswith(("http://", "https://")):
        parsed = urlparse(source)
        return ClassifiedSource(
            kind=SourceKind.URL,
            raw=source,
            url_host=parsed.hostname or "",
            url_path=parsed.path or "",
        )

    # Use PurePosixPath so Windows-style paths still produce a sensible
    # basename if a user passes one in. Routing rules are case-insensitive
    # so we lower-case the suffix once here.
    path = PurePosixPath(source.replace("\\", "/"))
    return ClassifiedSource(
        kind=SourceKind.FILE,
        raw=source,
        filename=path.name,
        suffix=path.suffix.lower(),
    )


# ---------------------------------------------------------------------------
# Rule compilation + match
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    """A single compiled routing entry: primitive + kind + pattern.

    Stored flat (rather than per-primitive) so :func:`route` is one
    deduplicating loop, and so the journaled ``signals`` list reads
    naturally as ``<rule_kind>:<pattern>``.
    """

    primitive: str
    rule_kind: str  # "file_extension" | "filename_pattern" | "url_domain" | "url_path"
    pattern: str


def _compile_table(primitives: list[Primitive]) -> list[_Rule]:
    rules: list[_Rule] = []
    for primitive in primitives:
        if primitive.kind is not PrimitiveKind.CONTENT_TYPE:
            continue
        if primitive.routing is None:
            continue
        for ext in primitive.routing.file_extensions:
            rules.append(_Rule(primitive.name, "file_extension", ext))
        for pat in primitive.routing.filename_patterns:
            rules.append(_Rule(primitive.name, "filename_pattern", pat))
        for dom in primitive.routing.url_domains:
            rules.append(_Rule(primitive.name, "url_domain", dom))
        for path_pat in primitive.routing.url_path_patterns:
            rules.append(_Rule(primitive.name, "url_path", path_pat))
    return rules


def _rule_matches(rule: _Rule, source: ClassifiedSource) -> bool:
    if rule.rule_kind == "file_extension":
        return source.kind is SourceKind.FILE and source.suffix == rule.pattern.lower()
    if rule.rule_kind == "filename_pattern":
        return source.kind is SourceKind.FILE and fnmatch.fnmatchcase(
            source.filename.lower(), rule.pattern.lower()
        )
    if rule.rule_kind == "url_domain":
        return source.kind is SourceKind.URL and fnmatch.fnmatchcase(
            source.url_host.lower(), rule.pattern.lower()
        )
    if rule.rule_kind == "url_path":
        return source.kind is SourceKind.URL and fnmatch.fnmatchcase(source.url_path, rule.pattern)
    return False


def _content_type_names(primitives: list[Primitive]) -> list[str]:
    return sorted(p.name for p in primitives if p.kind is PrimitiveKind.CONTENT_TYPE)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def route(
    source: str,
    primitives: list[Primitive],
    *,
    as_override: str | None,
) -> RouteResult:
    """Pick a content-type primitive for ``source``.

    ``primitives`` is the full installed catalog (any kind); non-content
    -type primitives are ignored at the routing layer. ``as_override``,
    when set, bypasses detection and resolves to that primitive verbatim
    — an unknown name or a non-content-type name raises
    :class:`WikiError` so the CLI surfaces the failure as a one-liner.
    """

    if as_override is not None:
        target = next((p for p in primitives if p.name == as_override), None)
        if target is None:
            raise WikiError(
                f"--as: no installed primitive named '{as_override}'. "
                f"Available content-types: {', '.join(_content_type_names(primitives)) or '(none)'}"
            )
        if target.kind is not PrimitiveKind.CONTENT_TYPE:
            raise WikiError(
                f"--as: primitive '{as_override}' has kind "
                f"'{target.kind.value}', not 'content-type'"
            )
        return Routed(content_type=as_override, signals=[], via="as_flag")

    classified = classify_source(source)
    if classified.kind is SourceKind.STDIN:
        # No auto-routing for stdin/paste in v0.1 — Claude-side detection
        # already covers the "user pasted some text" case; the CLI's job
        # is the deterministic-route step that needs a concrete source.
        return NoMatch(available=_content_type_names(primitives))

    table = _compile_table(primitives)
    matched: dict[str, list[str]] = {}
    for rule in table:
        if _rule_matches(rule, classified):
            matched.setdefault(rule.primitive, []).append(f"{rule.rule_kind}:{rule.pattern}")

    if not matched:
        return NoMatch(available=_content_type_names(primitives))
    if len(matched) > 1:
        return Ambiguous(candidates=sorted(matched))

    [(name, signals)] = matched.items()
    return Routed(content_type=name, signals=signals, via="auto")
