"""Orchestrator for ``wiki research`` (RFC-0001 Task 18).

Loads ``<vault_root>/research-providers.yaml``, picks the provider, calls
its in-process ``dispatch(config, query)``, and composes the markdown
document the CLI emits to stdout or writes via ``safe_write``. The CLI
owns the journal append + transaction bracketing — the dispatcher
returns a ``DispatchResult(markdown, event)`` on success or raises
``ResearchDispatchError(message, event=...)`` on a runtime failure.

The provider registry is module-private. Gemini and Semantic Scholar
are registered alongside Perplexity (RFC-0001 Task 19); new providers
join by adding a re-binding wrapper above and one entry to
``_PROVIDER_REGISTRY``. Tests inject fakes via
``monkeypatch.setattr`` on the provider module — the registry holds
re-binding wrappers that re-read the provider's ``dispatch`` at call
time, so the patch is seen.

See ``docs/specs/task-18-research-perplexity/spec.md`` §"Dispatcher
return-and-raise contract".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from llm_wiki_kit import managed_regions
from llm_wiki_kit.errors import ValidationError, WikiError
from llm_wiki_kit.models import (
    ProviderConfig,
    ResearchProvidersConfig,
    ResearchQueryEvent,
)
from llm_wiki_kit.research.http import ResearchHTTPError
from llm_wiki_kit.research.providers import gemini, perplexity, semantic_scholar

CONFIG_FILENAME = "research-providers.yaml"
PROVIDERS_REGION_ID = "providers"
RESEARCH_VEHICLE = "wiki-research"


@dataclass(frozen=True)
class _ProviderOutput:
    """The shape the registry's callables return.

    Each provider's result dataclass (e.g. ``PerplexityResult``) supplies
    these three fields. The dispatcher consumes them generically rather
    than reaching into provider-specific types — Task 19's providers
    each define their own result class, normalized to this shape via
    the registry wrapper.
    """

    answer: str
    citations: list[str]
    model: str


def _call_perplexity(config: ProviderConfig, query: str) -> _ProviderOutput:
    """Re-binding wrapper around :func:`perplexity.dispatch`.

    Late-reads ``perplexity.dispatch`` at call time so
    ``monkeypatch.setattr(perplexity, "dispatch", fake)`` in tests is
    seen by the dispatcher. Direct binding (``"perplexity":
    perplexity.dispatch``) would freeze the function object at
    import time and the monkeypatch would silently miss.
    """

    result = perplexity.dispatch(config, query)
    return _ProviderOutput(answer=result.answer, citations=result.citations, model=result.model)


def _call_gemini(config: ProviderConfig, query: str) -> _ProviderOutput:
    """Re-binding wrapper around :func:`gemini.dispatch` (Task 19)."""

    result = gemini.dispatch(config, query)
    return _ProviderOutput(answer=result.answer, citations=result.citations, model=result.model)


def _call_semantic_scholar(config: ProviderConfig, query: str) -> _ProviderOutput:
    """Re-binding wrapper around :func:`semantic_scholar.dispatch` (Task 19)."""

    result = semantic_scholar.dispatch(config, query)
    return _ProviderOutput(answer=result.answer, citations=result.citations, model=result.model)


_PROVIDER_REGISTRY: dict[str, Callable[[ProviderConfig, str], _ProviderOutput]] = {
    perplexity.PROVIDER_SLUG: _call_perplexity,
    gemini.PROVIDER_SLUG: _call_gemini,
    semantic_scholar.PROVIDER_SLUG: _call_semantic_scholar,
}


@dataclass(frozen=True)
class DispatchResult:
    """Successful dispatch's payload.

    ``markdown`` is the full document the CLI prints or writes;
    ``event`` is the journal event the CLI appends — ``status="ok"``
    on success, ``status="error"`` when carried by a
    :class:`ResearchDispatchError`.
    """

    markdown: str
    event: ResearchQueryEvent


class ResearchDispatchError(WikiError):
    """Provider-side failure carrying the prepared ``status="error"`` event.

    ``_cmd_research`` catches this, appends ``exc.event`` to the
    journal *before* re-raising, then the CLI boundary in ``main()``
    catches it as a ``WikiError`` and exits 2. Spec invariant 10 pins
    the journal-append-failure chaining: if the append itself raises,
    the original ``ResearchDispatchError`` is re-raised with the
    journal exception as ``__cause__``, never the other way around.
    """

    def __init__(self, message: str, *, event: ResearchQueryEvent) -> None:
        super().__init__(message)
        self.event = event


def dispatch_query(
    query: str,
    provider_slug: str | None,
    vault_root: Path,
    *,
    now: datetime,
) -> DispatchResult:
    """Route ``query`` to the configured provider; return rendered markdown + event.

    Resolution order:

    1. Read ``<vault_root>/research-providers.yaml`` (the seed file the
       ``infrastructure:research`` primitive ships). Missing file →
       ``WikiError("infrastructure:research not installed")``.
    2. Parse the ``providers`` managed-region body via
       ``managed_regions.parse``; YAML-load the slice into a
       ``ResearchProvidersConfig`` (RootModel of slug → ProviderConfig).
       An empty mapping → ``WikiError("no research providers installed")``.
    3. Pick the provider: explicit ``provider_slug`` wins; otherwise
       the only installed slug is used. Multiple installed without an
       explicit pick → ``WikiError("pass --provider <name>; installed:
       ...")``.
    4. Look up the slug in ``_PROVIDER_REGISTRY``. A slug present in
       config but absent from the registry (e.g. a hand-edited
       ``gemini:`` block before Task 19 ships) →
       ``WikiError(f"provider '{name}' has no implementation in this kit
       version")``.
    5. Call the registry wrapper (which re-reads the provider module's
       ``dispatch``). Provider failures wrap as
       :class:`ResearchDispatchError` with a ``status="error"`` event
       so the caller can journal the attempt.
    6. Render the markdown and return ``DispatchResult(markdown, event)``.
    """

    config_path = vault_root / CONFIG_FILENAME
    if not config_path.is_file():
        raise WikiError("infrastructure:research not installed")

    config = _load_config(config_path)
    if not config.root:
        raise WikiError("no research providers installed")

    slug = _pick_provider(config, provider_slug)
    provider_config = config.root[slug]

    if slug not in _PROVIDER_REGISTRY:
        raise WikiError(f"provider '{slug}' has no implementation in this kit version")

    caller = _PROVIDER_REGISTRY[slug]
    try:
        provider_output = caller(provider_config, query)
    except ResearchHTTPError as exc:
        # Runtime-shaped provider failure (HTTP error, malformed
        # response, network) wraps as ``ResearchDispatchError``
        # carrying the prepared error event — the CLI journals
        # ``exc.event`` then re-raises. Plain ``WikiError`` from the
        # provider (e.g. missing env var) is config-shaped and
        # propagates unwrapped per spec §"Error paths" — no audit
        # event for a request the user's config never licensed.
        error_event = ResearchQueryEvent(
            timestamp=now,
            by=RESEARCH_VEHICLE,
            query=query,
            provider=slug,
            result_path=None,
            model=provider_config.model,
            status="error",
        )
        raise ResearchDispatchError(str(exc), event=error_event) from None

    event = ResearchQueryEvent(
        timestamp=now,
        by=RESEARCH_VEHICLE,
        query=query,
        provider=slug,
        result_path=None,
        model=provider_output.model,
        status="ok",
    )
    markdown = _render_markdown(query, slug, provider_output, now)
    return DispatchResult(markdown=markdown, event=event)


def _load_config(config_path: Path) -> ResearchProvidersConfig:
    """Parse the managed-region body of ``research-providers.yaml``.

    The file's outside-region text (heading comments, BEGIN/END
    markers) is preserved on disk by ADR-0003's contract; the
    dispatcher only consumes the slice between the markers. A missing
    region surfaces as "no research providers installed" — same UX as
    an empty region body.
    """

    text = config_path.read_text(encoding="utf-8")
    regions = managed_regions.parse(text)
    region_body = regions.get(PROVIDERS_REGION_ID, "")
    if not region_body.strip():
        # Empty (or whitespace-only) region — caller's "no providers"
        # path handles it by checking ``config.root``.
        return ResearchProvidersConfig.model_validate({})

    try:
        loaded = yaml.safe_load(region_body) or {}
    except yaml.YAMLError as exc:
        raise WikiError(f"invalid research-providers.yaml: {exc}") from None

    if not isinstance(loaded, dict):
        raise WikiError("invalid research-providers.yaml: providers region must be a mapping")

    try:
        return ResearchProvidersConfig.model_validate(loaded)
    except PydanticValidationError as exc:
        # Reuse the kit's ``ValidationError`` for consistent formatting,
        # then re-wrap as ``WikiError`` so the CLI boundary surfaces a
        # one-line message rather than the multi-line Pydantic dump.
        formatted = ValidationError("research-providers.yaml", exc)
        raise WikiError(str(formatted)) from None


def _pick_provider(config: ResearchProvidersConfig, requested: str | None) -> str:
    """Resolve which provider slug to use, given the config and CLI flag."""

    installed = config.slugs()
    if requested is not None:
        if requested not in config.root:
            raise WikiError(
                f"provider '{requested}' not installed; installed: {', '.join(installed)}"
            )
        return requested
    if len(installed) == 1:
        return installed[0]
    raise WikiError(f"pass --provider <name>; installed: {', '.join(installed)}")


def _render_markdown(
    query: str,
    provider_slug: str,
    result: _ProviderOutput,
    fetched_at: datetime,
) -> str:
    """Compose the frontmatter + body markdown the CLI emits.

    Frontmatter is rendered via ``yaml.safe_dump`` over an ``OrderedDict``-
    like dict (Python 3.7+ preserves insertion order). The ``fetched_at``
    field is passed as an ``.isoformat()`` string — passing a
    ``datetime`` would emit YAML's space-separated ``!!timestamp`` form
    and the spec acceptance test pins the literal ``T`` separator.

    The body is appended verbatim. A blank line separates the closing
    frontmatter delimiter from the body so naive frontmatter parsers
    that scan up-to-blank-line correctly identify the boundary even
    when the body itself contains a line of three dashes.
    """

    if fetched_at.tzinfo is None:
        raise TypeError("fetched_at must be timezone-aware (use datetime.now(UTC))")
    frontmatter_data: dict[str, Any] = {
        "provider": provider_slug,
        "model": result.model,
        "query": query,
        "fetched_at": fetched_at.isoformat(),
        "citations": result.citations,
    }
    # ``yaml.safe_dump`` with ``sort_keys=False`` preserves the
    # caller-supplied order; ``default_flow_style=False`` keeps the
    # block style (one key per line). ``allow_unicode=True`` keeps
    # non-ASCII queries human-readable.
    fm_text = yaml.safe_dump(
        frontmatter_data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    # Ensure exactly one trailing newline on the body — and the boundary
    # blank line is rendered explicitly so the body's own leading lines
    # are preserved verbatim. A body that starts with three dashes does
    # not collide with the frontmatter closer.
    body = result.answer
    if not body.endswith("\n"):
        body += "\n"
    return f"---\n{fm_text}---\n\n{body}"
