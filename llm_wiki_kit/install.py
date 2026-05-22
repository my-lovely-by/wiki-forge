"""Region-contribution aggregator used by ``wiki init`` and ``wiki add``.

ADR-0006 pins the contract: when N primitives declare a contribution to
the same managed region of a shared file, the installer concatenates
their snippet files in install order and calls
:func:`write_helper.safe_write_region` exactly once for that region.

This module exposes two surfaces:

* :func:`validate_contributions` — fail-before-writing check that every
  ``contributes_to`` entry on a primitive has a matching snippet file
  under ``<primitive_root>/regions/<file>.<region>``, and every snippet
  file in that directory has a matching ``contributes_to`` entry.
* :func:`aggregate_region_contributions` — the second pass of the
  install pipeline. Walks the installed primitives, groups their
  contributions by ``(file, region)``, concatenates snippets in
  install order, and writes each region once.

The two-pass split exists because ADR-0006 §Mechanics step 5 mandates
that primitives' ``files/`` trees land *before* their region
contributions are applied — so seed shared files (e.g.
``core/files/frontmatter.schema.yaml``) are on disk by the time
:func:`safe_write_region` looks for their region markers.

The aggregator's ``by`` attribution is the install vehicle
(``"wiki-init"``, ``"wiki-add"``), not any one contributing primitive.
A composed region body has multiple authors and naming any one of them
in the journal would be arbitrary; the contributing primitives are
already journaled by their own ``primitive.install`` events.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from llm_wiki_kit import journal
from llm_wiki_kit.errors import PrimitiveError
from llm_wiki_kit.journal import append_event
from llm_wiki_kit.models import Contribution, Primitive, PrimitiveInstallEvent
from llm_wiki_kit.render import render_tree
from llm_wiki_kit.write_helper import safe_write_region

_logger = logging.getLogger(__name__)

# Above this many journal events, an install pipeline that runs
# without an active ``use_journal_cache`` scope is operating on the
# wrong perf curve (O(events * writes)). The warning surfaces the
# discipline gap at the same severity as the journal-locking
# fallback warning in ``journal.py``.
_UNCACHED_INSTALL_PIPELINE_WARN_THRESHOLD = 50

# One warning per resolved journal path per process, so a multi-invocation
# session (or a test that drives install_primitives many times) doesn't
# carpet-bomb the log.
_UNCACHED_PIPELINE_WARNED: set[Path] = set()


def _warn_if_install_pipeline_uncached(journal_path: Path) -> None:
    """Emit one WARNING per resolved journal path if no cache scope is active.

    The cache is opt-in at the handler boundary; a new install-style
    handler that forgets the ``with journal.use_journal_cache(...):``
    wrapper falls back to O(events * writes) baseline lookups silently
    — no test fails, the install is just slow. This warning is the
    runtime signal that turns "the install feels slow" into a
    grep-able log line naming the spec.

    Below ``_UNCACHED_INSTALL_PIPELINE_WARN_THRESHOLD`` events, the
    perf cliff is negligible; we don't warn. The threshold is
    intentionally lenient — the warning is for an honest forgot-the-
    wrapper bug on a real-sized vault, not a one-off small write.
    """

    if journal._CURRENT_READER.get() is not None:
        return
    if not journal_path.exists():
        return  # fresh vault; nothing yet to count
    try:
        event_count = sum(1 for _ in journal_path.open("r", encoding="utf-8"))
    except OSError:
        return  # don't crash on a transient read failure
    if event_count < _UNCACHED_INSTALL_PIPELINE_WARN_THRESHOLD:
        return
    resolved = journal_path.resolve()
    if resolved in _UNCACHED_PIPELINE_WARNED:
        return
    _UNCACHED_PIPELINE_WARNED.add(resolved)
    _logger.warning(
        "install pipeline running without a journal.use_journal_cache() "
        "scope on a vault with %d events at %s — baseline lookups will "
        "be O(events * writes). See docs/specs/journal-reader-cache/spec.md.",
        event_count,
        resolved,
    )


_REGIONS_SUBDIR = "regions"


@dataclass(frozen=True)
class _Bucket:
    """One ``(file, region)`` pair and its contributors in install order."""

    file: str
    region: str
    contributors: tuple[tuple[str, Path], ...]  # (primitive_name, snippet_path)


def _snippet_filename(contribution: Contribution) -> str:
    """Return the on-disk filename for ``contribution`` under ``regions/``.

    Per ADR-0006 §Mechanics step 1, the filename is the literal ``file``
    value joined to the literal ``region`` value with a single ``.``.
    Path traversal is forbidden in either component — both come from a
    Pydantic-validated manifest, but defence in depth catches a future
    refactor that loosens the schema.
    """

    if "/" in contribution.file or "/" in contribution.region:
        raise PrimitiveError(
            f"contribution file/region must not contain '/': "
            f"file={contribution.file!r} region={contribution.region!r}"
        )
    if contribution.file.startswith(".."):
        raise PrimitiveError(f"contribution file must not start with '..': {contribution.file!r}")
    return f"{contribution.file}.{contribution.region}"


def _declared_snippets(primitive: Primitive) -> dict[str, Contribution]:
    return {_snippet_filename(c): c for c in primitive.contributes_to}


def validate_contributions(primitive: Primitive, primitive_root: Path) -> None:
    """Raise :class:`PrimitiveError` if a primitive's contribution shape is wrong.

    ADR-0006 §Mechanics step 6 names two fatal mismatches:

    * **Missing snippet:** a ``contributes_to`` entry with no
      corresponding file under ``regions/``.
    * **Orphan snippet:** a file under ``regions/`` whose name is not
      declared in ``contributes_to``.

    A primitive with no ``contributes_to`` entries and no ``regions/``
    directory is valid and a no-op here.
    """

    declared = _declared_snippets(primitive)
    regions_dir = primitive_root / _REGIONS_SUBDIR

    for snippet_name, contribution in declared.items():
        snippet_path = regions_dir / snippet_name
        if not snippet_path.is_file():
            raise PrimitiveError(
                f"primitive '{primitive.name}' declares contribution to "
                f"{contribution.file}:{contribution.region} but snippet file "
                f"{snippet_path} is missing"
            )

    if not regions_dir.is_dir():
        # No regions/ directory at all: declared must also be empty
        # (the loop above would have raised). Nothing more to check.
        return

    for entry in sorted(regions_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name not in declared:
            raise PrimitiveError(
                f"primitive '{primitive.name}' has orphan snippet "
                f"{entry} with no matching contributes_to entry"
            )


def _normalise_snippet(text: str) -> str:
    """Return ``text`` with exactly one trailing newline (ADR-0006 step 4)."""

    return text.rstrip("\n") + "\n"


def _plan(
    primitives: Sequence[Primitive],
    primitive_sources: Mapping[str, Path],
) -> list[_Bucket]:
    """Group contributions across ``primitives`` into install-ordered buckets.

    ``primitives`` is assumed to already be in install order
    (topologically sorted by ``requires:``, alphabetical tiebreaker —
    see :func:`primitives.resolve_dependencies`). The aggregator
    preserves that order within each bucket. Buckets themselves are
    emitted in alphabetical order by ``(file, region)`` so the install
    pipeline is reproducible.
    """

    grouped: dict[tuple[str, str], list[tuple[str, Path]]] = {}
    for primitive in primitives:
        root = primitive_sources.get(primitive.name)
        if root is None:
            raise PrimitiveError(
                f"install: no source directory recorded for primitive '{primitive.name}'"
            )
        for contribution in primitive.contributes_to:
            snippet_path = root / _REGIONS_SUBDIR / _snippet_filename(contribution)
            key = (contribution.file, contribution.region)
            grouped.setdefault(key, []).append((primitive.name, snippet_path))

    buckets: list[_Bucket] = []
    for file, region in sorted(grouped):
        buckets.append(
            _Bucket(file=file, region=region, contributors=tuple(grouped[(file, region)]))
        )
    return buckets


def aggregate_region_contributions(
    primitives: Sequence[Primitive],
    primitive_sources: Mapping[str, Path],
    journal_path: Path,
    by: str,
) -> None:
    """Apply every region contribution across ``primitives`` to the vault.

    Reads each contributor's snippet from disk, normalises trailing
    newlines, concatenates in install order, and calls
    :func:`safe_write_region` exactly once per ``(file, region)``
    bucket. Idempotent on re-run: a body whose hash matches the most
    recent ``managed_region.write`` event is a no-op write (the kit
    still emits the event, by design — the audit trail records every
    composed body, not just the ones that changed bytes).

    Pre-condition: every primitive's ``files/`` tree has already been
    rendered into the vault, so the shared file the region lives in is
    on disk. ``safe_write_region`` raises :class:`FileNotFoundError`
    otherwise.
    """

    buckets = _plan(primitives, primitive_sources)
    vault_root = journal_path.parent.parent

    for bucket in buckets:
        body_parts: list[str] = []
        for _primitive_name, snippet_path in bucket.contributors:
            text = snippet_path.read_text(encoding="utf-8")
            body_parts.append(_normalise_snippet(text))
        composed = "".join(body_parts)
        safe_write_region(
            file_path=vault_root / bucket.file,
            region_id=bucket.region,
            new_content=composed,
            by=by,
            journal_path=journal_path,
        )


def install_primitives(
    *,
    to_install: Sequence[Primitive],
    all_installed: Sequence[Primitive],
    sources: Mapping[str, Path],
    journal_path: Path,
    context: Mapping[str, str],
    install_vehicle: str,
    now: datetime,
) -> None:
    """Render ``to_install`` and run the region aggregator over ``all_installed``.

    Shared between ``wiki init`` and ``wiki add``. The split between the
    two sequences is what lets ``wiki add`` re-aggregate every region
    over the full installed set (so existing bodies survive) without
    re-rendering primitives that have already landed (which would emit
    duplicate ``page.write`` events).

    Pre-condition: every primitive in ``to_install`` has already passed
    :func:`validate_contributions`. The caller (``_cmd_init``,
    ``_cmd_add``) owns that pre-flight so a malformed primitive cannot
    leak into the half-installed state this function would produce.
    ``sources`` must cover every primitive in ``all_installed``.

    ``install_vehicle`` is the ``by`` attribution recorded on the
    aggregator's ``managed_region.write`` events — ``"wiki-init"`` for
    initial vault creation, ``"wiki-add"`` for subsequent ``wiki add``
    installs. Per-primitive ``files/`` renders attribute to the
    primitive name itself, matching the existing render contract.
    """

    _warn_if_install_pipeline_uncached(journal_path)
    vault_root = journal_path.parent.parent
    for primitive in to_install:
        append_event(
            journal_path,
            PrimitiveInstallEvent(
                timestamp=now,
                by=install_vehicle,
                primitive=primitive.name,
                version=primitive.version,
            ),
        )
        render_tree(
            sources[primitive.name] / "files",
            vault_root,
            context,
            journal_path,
            by=primitive.name,
        )

    aggregate_region_contributions(
        all_installed,
        sources,
        journal_path,
        by=install_vehicle,
    )
