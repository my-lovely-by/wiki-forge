"""Unit tests for ``llm_wiki_kit.install``.

ADR-0006 is the contract this module enforces. Each test pins one
clause from the ADR so a regression points back at a specific decision.

Coverage:

* ``validate_contributions`` — missing-snippet and orphan-snippet
  fatal-fail paths, plus the no-op cases (no ``regions/`` and no
  ``contributes_to``).
* ``aggregate_region_contributions`` — install-order concatenation,
  trailing-newline normalisation, idempotent re-run, deterministic
  bucket ordering across multiple ``(file, region)`` pairs, and the
  zero-contributor short-circuit (no write, no journal event).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from llm_wiki_kit.errors import PrimitiveError
from llm_wiki_kit.install import aggregate_region_contributions, validate_contributions
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import (
    Contribution,
    ManagedRegionWriteEvent,
    Primitive,
    PrimitiveKind,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _primitive(
    name: str,
    *,
    kind: PrimitiveKind = PrimitiveKind.CONTENT_TYPE,
    contributes_to: list[Contribution] | None = None,
) -> Primitive:
    return Primitive(
        name=name,
        kind=kind,
        version="0.1.0",
        description=f"Test primitive {name}.",
        contributes_to=contributes_to or [],
    )


def _write_snippet(primitive_root: Path, file_: str, region: str, body: str) -> None:
    regions = primitive_root / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    (regions / f"{file_}.{region}").write_text(body, encoding="utf-8")


def _make_vault_with_seed(tmp_path: Path, region_seed: str = "  # seed body\n") -> Path:
    """Create a vault root with a shared file seeded by ``core``.

    Returns the vault root; the journal lives at
    ``<vault>/.wiki.journal/journal.jsonl``. The shared file
    ``frontmatter.schema.yaml`` exists with one managed region named
    ``types`` so :func:`safe_write_region` can find the markers.
    """

    vault = tmp_path / "vault"
    vault.mkdir()
    shared = vault / "frontmatter.schema.yaml"
    shared.write_text(
        "types:\n"
        "  # BEGIN MANAGED: types\n"
        f"{region_seed}"
        "  # END MANAGED: types\n"
        "fields:\n"
        "  # BEGIN MANAGED: fields\n"
        "  # seed fields\n"
        "  # END MANAGED: fields\n",
        encoding="utf-8",
    )
    (vault / ".wiki.journal").mkdir()
    return vault


def _journal(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# validate_contributions
# ---------------------------------------------------------------------------


def test_validate_passes_when_no_contributions(tmp_path: Path) -> None:
    primitive = _primitive("people", kind=PrimitiveKind.ONTOLOGY)
    # No regions/ directory at all — ontologies are the canonical case.
    validate_contributions(primitive, tmp_path)


def test_validate_passes_when_declared_snippets_match(tmp_path: Path) -> None:
    primitive = _primitive(
        "meeting",
        contributes_to=[
            Contribution(file="frontmatter.schema.yaml", region="types"),
            Contribution(file="frontmatter.schema.yaml", region="fields"),
        ],
    )
    _write_snippet(tmp_path, "frontmatter.schema.yaml", "types", "  - meeting\n")
    _write_snippet(tmp_path, "frontmatter.schema.yaml", "fields", "  meeting_x: 1\n")

    validate_contributions(primitive, tmp_path)


def test_validate_raises_on_missing_snippet(tmp_path: Path) -> None:
    primitive = _primitive(
        "meeting",
        contributes_to=[Contribution(file="frontmatter.schema.yaml", region="types")],
    )
    # No regions/ directory: declared contribution has no backing file.

    with pytest.raises(PrimitiveError) as info:
        validate_contributions(primitive, tmp_path)
    assert "frontmatter.schema.yaml:types" in str(info.value)
    assert "missing" in str(info.value)


def test_validate_raises_on_orphan_snippet(tmp_path: Path) -> None:
    primitive = _primitive("meeting", contributes_to=[])
    _write_snippet(tmp_path, "AGENTS.md", "installed-skills", "stray\n")

    with pytest.raises(PrimitiveError) as info:
        validate_contributions(primitive, tmp_path)
    assert "orphan" in str(info.value)
    assert "AGENTS.md.installed-skills" in str(info.value)


def test_validate_rejects_path_traversal_in_contribution(tmp_path: Path) -> None:
    primitive = _primitive(
        "evil",
        contributes_to=[Contribution(file="..", region="types")],
    )
    with pytest.raises(PrimitiveError):
        validate_contributions(primitive, tmp_path)


# ---------------------------------------------------------------------------
# aggregate_region_contributions
# ---------------------------------------------------------------------------


def test_aggregate_writes_nothing_when_no_contributors(tmp_path: Path) -> None:
    vault = _make_vault_with_seed(tmp_path)
    primitive = _primitive("people", kind=PrimitiveKind.ONTOLOGY)
    source = tmp_path / "people-src"
    source.mkdir()

    aggregate_region_contributions(
        primitives=[primitive],
        primitive_sources={"people": source},
        journal_path=_journal(vault),
        by="wiki-init",
    )

    # The seed body survives untouched and no managed_region.write event
    # was emitted — zero contributors short-circuits the write.
    assert "# seed body" in (vault / "frontmatter.schema.yaml").read_text(encoding="utf-8")
    assert not _journal(vault).exists() or read_events(_journal(vault)) == []


def test_aggregate_concatenates_in_install_order(tmp_path: Path) -> None:
    vault = _make_vault_with_seed(tmp_path)

    # Three primitives, each contributing to the same region. The
    # aggregator preserves install order — i.e. the order the caller
    # passes ``primitives`` in, which is the topologically-sorted
    # install order from ``primitives.resolve_dependencies``.
    sources: dict[str, Path] = {}
    for name in ("alpha", "bravo", "charlie"):
        src = tmp_path / f"{name}-src"
        src.mkdir()
        _write_snippet(src, "frontmatter.schema.yaml", "types", f"  - {name}\n")
        sources[name] = src

    primitives = [_primitive(name) for name in ("alpha", "bravo", "charlie")]
    contrib = Contribution(file="frontmatter.schema.yaml", region="types")
    for primitive in primitives:
        primitive.contributes_to.append(contrib)

    aggregate_region_contributions(
        primitives=primitives,
        primitive_sources=sources,
        journal_path=_journal(vault),
        by="wiki-init",
    )

    rendered = (vault / "frontmatter.schema.yaml").read_text(encoding="utf-8")
    # Body shows up in install order, between the markers, in place of
    # the seed body.
    assert "# seed body" not in rendered
    assert rendered.index("- alpha") < rendered.index("- bravo") < rendered.index("- charlie")


def test_aggregate_normalises_trailing_newlines(tmp_path: Path) -> None:
    vault = _make_vault_with_seed(tmp_path)

    src_a = tmp_path / "no-newline"
    src_a.mkdir()
    _write_snippet(src_a, "frontmatter.schema.yaml", "types", "  - no-newline")
    src_b = tmp_path / "many-newlines"
    src_b.mkdir()
    _write_snippet(src_b, "frontmatter.schema.yaml", "types", "  - many-newlines\n\n\n")

    contrib = Contribution(file="frontmatter.schema.yaml", region="types")
    primitives = [
        _primitive("no-newline", contributes_to=[contrib]),
        _primitive("many-newlines", contributes_to=[contrib]),
    ]
    sources = {"no-newline": src_a, "many-newlines": src_b}

    aggregate_region_contributions(
        primitives=primitives,
        primitive_sources=sources,
        journal_path=_journal(vault),
        by="wiki-init",
    )

    rendered = (vault / "frontmatter.schema.yaml").read_text(encoding="utf-8")
    # Exactly one blank line between the markers and each contributor
    # line (no doubled newlines from collapsing, no glued lines from
    # missing newlines).
    body = rendered.split("# BEGIN MANAGED: types\n", 1)[1].split("  # END MANAGED: types", 1)[0]
    assert body == "  - no-newline\n  - many-newlines\n"


def test_aggregate_emits_one_event_per_bucket(tmp_path: Path) -> None:
    vault = _make_vault_with_seed(tmp_path)

    src = tmp_path / "meeting-src"
    src.mkdir()
    _write_snippet(src, "frontmatter.schema.yaml", "types", "  - meeting\n")
    _write_snippet(src, "frontmatter.schema.yaml", "fields", "  meeting_x: 1\n")

    primitive = _primitive(
        "meeting",
        contributes_to=[
            Contribution(file="frontmatter.schema.yaml", region="types"),
            Contribution(file="frontmatter.schema.yaml", region="fields"),
        ],
    )

    aggregate_region_contributions(
        primitives=[primitive],
        primitive_sources={"meeting": src},
        journal_path=_journal(vault),
        by="wiki-init",
    )

    events = read_events(_journal(vault))
    region_events = [e for e in events if isinstance(e, ManagedRegionWriteEvent)]
    # Two regions, one event each. Buckets are written in alphabetical
    # order by (file, region) — ``fields`` before ``types``.
    assert [(e.file, e.region) for e in region_events] == [
        ("frontmatter.schema.yaml", "fields"),
        ("frontmatter.schema.yaml", "types"),
    ]
    assert all(e.by == "wiki-init" for e in region_events)


def test_aggregate_is_idempotent_on_rerun(tmp_path: Path) -> None:
    vault = _make_vault_with_seed(tmp_path)

    src = tmp_path / "meeting-src"
    src.mkdir()
    _write_snippet(src, "frontmatter.schema.yaml", "types", "  - meeting\n")

    contrib = Contribution(file="frontmatter.schema.yaml", region="types")
    primitive = _primitive("meeting", contributes_to=[contrib])

    aggregate_region_contributions(
        primitives=[primitive],
        primitive_sources={"meeting": src},
        journal_path=_journal(vault),
        by="wiki-init",
    )
    first_rendered = (vault / "frontmatter.schema.yaml").read_text(encoding="utf-8")
    first_hash = _hash(first_rendered)

    aggregate_region_contributions(
        primitives=[primitive],
        primitive_sources={"meeting": src},
        journal_path=_journal(vault),
        by="wiki-init",
    )
    second_rendered = (vault / "frontmatter.schema.yaml").read_text(encoding="utf-8")
    # The composed body is deterministic; re-running produces the same
    # bytes. (``safe_write_region`` does emit a fresh journal event on
    # each call — by design per ADR-0006 §Consequences.)
    assert _hash(second_rendered) == first_hash


def test_aggregate_raises_when_primitive_source_missing(tmp_path: Path) -> None:
    vault = _make_vault_with_seed(tmp_path)
    primitive = _primitive(
        "meeting",
        contributes_to=[Contribution(file="frontmatter.schema.yaml", region="types")],
    )

    with pytest.raises(PrimitiveError):
        aggregate_region_contributions(
            primitives=[primitive],
            primitive_sources={},  # name not registered
            journal_path=_journal(vault),
            by="wiki-init",
        )


# ---------------------------------------------------------------------------
# _warn_if_install_pipeline_uncached
# ---------------------------------------------------------------------------


def test_uncached_install_pipeline_warns_on_large_journal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An install run on a vault with >= threshold events warns when no cache scope is active."""
    import logging

    from llm_wiki_kit import install
    from llm_wiki_kit.install import _warn_if_install_pipeline_uncached
    from llm_wiki_kit.journal import append_event
    from llm_wiki_kit.models import VaultInitEvent

    install._UNCACHED_PIPELINE_WARNED.clear()
    journal_path = tmp_path / ".wiki.journal" / "journal.jsonl"
    journal_path.parent.mkdir(parents=True)
    # Seed the journal with enough events to trip the threshold.
    from datetime import UTC, datetime

    for i in range(install._UNCACHED_INSTALL_PIPELINE_WARN_THRESHOLD + 5):
        append_event(
            journal_path,
            VaultInitEvent(timestamp=datetime.now(UTC), by=f"seed-{i}", vault_name="v", recipe="r"),
        )

    with caplog.at_level(logging.WARNING, logger="llm_wiki_kit.install"):
        _warn_if_install_pipeline_uncached(journal_path)
    messages = [r.message for r in caplog.records]
    assert any("install pipeline running without" in m for m in messages)


def test_uncached_install_pipeline_warning_suppressed_under_active_cache(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Inside ``use_journal_cache``, the warning is silent — the discipline is satisfied."""
    import logging
    from datetime import UTC, datetime

    from llm_wiki_kit import install
    from llm_wiki_kit.install import _warn_if_install_pipeline_uncached
    from llm_wiki_kit.journal import append_event, use_journal_cache
    from llm_wiki_kit.models import VaultInitEvent

    install._UNCACHED_PIPELINE_WARNED.clear()
    journal_path = tmp_path / ".wiki.journal" / "journal.jsonl"
    journal_path.parent.mkdir(parents=True)
    for i in range(install._UNCACHED_INSTALL_PIPELINE_WARN_THRESHOLD + 5):
        append_event(
            journal_path,
            VaultInitEvent(timestamp=datetime.now(UTC), by=f"seed-{i}", vault_name="v", recipe="r"),
        )

    with use_journal_cache(journal_path):
        with caplog.at_level(logging.WARNING, logger="llm_wiki_kit.install"):
            _warn_if_install_pipeline_uncached(journal_path)
    messages = [r.message for r in caplog.records]
    assert not any("install pipeline running without" in m for m in messages)


def test_uncached_install_pipeline_warning_suppressed_below_threshold(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A small journal doesn't trigger the warning — the perf cliff isn't reached."""
    import logging
    from datetime import UTC, datetime

    from llm_wiki_kit import install
    from llm_wiki_kit.install import _warn_if_install_pipeline_uncached
    from llm_wiki_kit.journal import append_event
    from llm_wiki_kit.models import VaultInitEvent

    install._UNCACHED_PIPELINE_WARNED.clear()
    journal_path = tmp_path / ".wiki.journal" / "journal.jsonl"
    journal_path.parent.mkdir(parents=True)
    # Far below the threshold.
    for i in range(5):
        append_event(
            journal_path,
            VaultInitEvent(timestamp=datetime.now(UTC), by=f"seed-{i}", vault_name="v", recipe="r"),
        )

    with caplog.at_level(logging.WARNING, logger="llm_wiki_kit.install"):
        _warn_if_install_pipeline_uncached(journal_path)
    messages = [r.message for r in caplog.records]
    assert not any("install pipeline running without" in m for m in messages)
