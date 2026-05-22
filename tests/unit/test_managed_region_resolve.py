"""Tests for retro-review #F-B1: managed-region resolve must re-baseline.

ADR-0004 §Mechanics step 6 promises that after ``resolve_proposal``,
"subsequent ``safe_write`` calls against ``path`` see no drift." For a
plain page write this holds because ``resolve_proposal`` emits a
``PageWriteEvent`` that ``_baseline_hash`` reads.

For a *managed-region* write the baseline lookup is region-scoped
(``_managed_region_baseline_hash`` walks ``ManagedRegionWriteEvent``s).
Before this fix, ``resolve_proposal`` emitted only ``PageWriteEvent`` +
``PageConflictResolvedEvent``, so a follow-up ``safe_write_region`` saw
the *pre-drift* region hash and re-proposed forever. These tests pin
the corrected behaviour: when the resolved file has prior managed-region
history, ``resolve_proposal`` also emits a fresh
``ManagedRegionWriteEvent`` per known region.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import (
    ManagedRegionWriteEvent,
    PageConflictResolvedEvent,
    PageWriteEvent,
)
from llm_wiki_kit.write_helper import (
    WriteResult,
    resolve_proposal,
    safe_write_region,
)


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / ".wiki.journal").mkdir()
    return tmp_path


@pytest.fixture
def journal(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _seed_agents_md(vault: Path, body: str = "v1") -> Path:
    target = vault / "AGENTS.md"
    target.write_text(
        "# AGENTS.md\n\n"
        "user prose outside the region.\n\n"
        "<!-- BEGIN MANAGED: content-types -->\n"
        f"{body}\n"
        "<!-- END MANAGED: content-types -->\n"
        "\n"
        "trailing user notes\n"
    )
    return target


def _drive_to_region_proposal(vault: Path, journal: Path) -> Path:
    """Seed a region write, then drift the region, then re-write to produce a sidecar."""

    target = _seed_agents_md(vault, body="v1")
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)
    target.write_text(target.read_text().replace("v1", "user override"))
    result = safe_write_region(target, "content-types", "v2", by="core", journal_path=journal)
    assert result is WriteResult.PROPOSAL
    assert (vault / "AGENTS.md.proposed").is_file()
    return target


def test_resolve_proposal_with_managed_region_history_emits_region_write(
    vault: Path, journal: Path
) -> None:
    """After resolving a region-proposal, the journal carries a fresh
    ``ManagedRegionWriteEvent`` so subsequent region writes have a baseline.
    """

    target = _drive_to_region_proposal(vault, journal)
    merged = target.read_text().replace("user override", "merged region body")

    resolve_proposal(target, merged, by="wiki-conflict", journal_path=journal)

    events = read_events(journal)
    region_writes = [e for e in events if isinstance(e, ManagedRegionWriteEvent)]
    # One from the initial successful write, one from the resolve.
    assert len(region_writes) == 2
    assert region_writes[-1].file == "AGENTS.md"
    assert region_writes[-1].region == "content-types"
    # Canonical region body adds a trailing newline before hashing.
    assert region_writes[-1].content_hash == _sha256("merged region body\n")
    assert region_writes[-1].by == "wiki-conflict"


def test_safe_write_region_after_resolve_sees_no_drift(vault: Path, journal: Path) -> None:
    """The core promise of ADR-0004 step 6 for managed regions: a follow-up
    kit write of the same region writes in place, not into a sidecar.
    """

    target = _drive_to_region_proposal(vault, journal)
    merged = target.read_text().replace("user override", "merged region body")
    resolve_proposal(target, merged, by="wiki-conflict", journal_path=journal)

    result = safe_write_region(
        target, "content-types", "v3-from-kit", by="core", journal_path=journal
    )

    assert result is WriteResult.WRITTEN
    assert "v3-from-kit" in target.read_text()
    assert not (vault / "AGENTS.md.proposed").exists()


def test_resolve_proposal_without_region_history_does_not_emit_region_write(
    vault: Path, journal: Path
) -> None:
    """Pure page resolves (no prior region write) keep the v1 behaviour.

    Regression guard so the F-B1 fix doesn't bleed into plain-page flows.
    """

    target = vault / "page.md"
    from llm_wiki_kit.write_helper import safe_write

    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="core", journal_path=journal)

    resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)

    events = read_events(journal)
    region_writes = [e for e in events if isinstance(e, ManagedRegionWriteEvent)]
    assert region_writes == []
    # Page-level events still emitted as before.
    assert any(isinstance(e, PageWriteEvent) and e.by == "wiki-conflict" for e in events)
    assert any(isinstance(e, PageConflictResolvedEvent) for e in events)


def test_resolve_proposal_with_multiple_regions_emits_one_event_per_region(
    vault: Path, journal: Path
) -> None:
    """A shared file with two managed regions sees both re-baselined on resolve."""

    target = vault / "AGENTS.md"
    target.write_text(
        "# AGENTS.md\n\n"
        "<!-- BEGIN MANAGED: content-types -->\n"
        "v1\n"
        "<!-- END MANAGED: content-types -->\n"
        "\n"
        "<!-- BEGIN MANAGED: ontologies -->\n"
        "o1\n"
        "<!-- END MANAGED: ontologies -->\n"
    )
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)
    safe_write_region(target, "ontologies", "o1", by="core", journal_path=journal)
    target.write_text(target.read_text().replace("v1", "user-c").replace("o1", "user-o"))
    safe_write_region(target, "content-types", "v2", by="core", journal_path=journal)

    merged = (
        "# AGENTS.md\n\n"
        "<!-- BEGIN MANAGED: content-types -->\n"
        "merged-c\n"
        "<!-- END MANAGED: content-types -->\n"
        "\n"
        "<!-- BEGIN MANAGED: ontologies -->\n"
        "merged-o\n"
        "<!-- END MANAGED: ontologies -->\n"
    )
    resolve_proposal(target, merged, by="wiki-conflict", journal_path=journal)

    region_writes = [e for e in read_events(journal) if isinstance(e, ManagedRegionWriteEvent)]
    # 1 (content-types init) + 1 (ontologies init) + 1 (content-types pre-resolve)
    # + 2 fresh resolves
    by_region = {(e.region, e.content_hash) for e in region_writes if e.by == "wiki-conflict"}
    assert by_region == {
        ("content-types", _sha256("merged-c\n")),
        ("ontologies", _sha256("merged-o\n")),
    }
