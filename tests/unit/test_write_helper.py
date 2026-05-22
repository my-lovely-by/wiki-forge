"""Tests for ``llm_wiki_kit.write_helper``.

ADR-0004 names the contract: ``safe_write`` is the only sanctioned write path
for files inside a user vault. It hashes on-disk content, compares to the
most recent ``PageWrite`` event for that path in the journal, writes directly
on a match (or when there's no prior knowledge) and emits a ``PageWrite``
event, or writes a ``<path>.proposed`` sidecar and emits a ``PageProposal``
event when the hashes diverge. The sidecar flow also adds a ``\\.proposed$``
pattern to ``.obsidianignore`` so Obsidian doesn't index conflict files.

These tests pin every numbered step from ADR-0004 §Mechanics.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from llm_wiki_kit.errors import ManagedRegionError, WikiError
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import (
    Event,
    ManagedRegionWriteEvent,
    PageConflictResolvedEvent,
    PageProposalEvent,
    PageWriteEvent,
)
from llm_wiki_kit.write_helper import (
    OBSIDIAN_IGNORE_PROPOSED_PATTERN,
    OBSIDIANIGNORE_BYPASS_DOC,
    WriteResult,
    _ensure_obsidianignore,
    resolve_proposal,
    safe_write,
    safe_write_region,
)


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A vault root with the canonical journal path beneath it."""
    (tmp_path / ".wiki.journal").mkdir()
    return tmp_path


@pytest.fixture
def journal(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


# ---------------------------------------------------------------------------
# WriteResult
# ---------------------------------------------------------------------------


def test_write_result_has_written_and_proposal_members() -> None:
    assert {member.name for member in WriteResult} == {"WRITTEN", "PROPOSAL"}


# ---------------------------------------------------------------------------
# Direct write path: no prior knowledge / matching baseline
# ---------------------------------------------------------------------------


def test_first_write_creates_file_and_returns_written(vault: Path, journal: Path) -> None:
    target = vault / "meetings" / "2026-05-15.md"
    result = safe_write(target, "hello\n", by="meeting", journal_path=journal)
    assert result is WriteResult.WRITTEN
    assert target.read_text() == "hello\n"


def test_first_write_creates_parent_directories(vault: Path, journal: Path) -> None:
    target = vault / "a" / "b" / "c" / "page.md"
    safe_write(target, "x", by="core", journal_path=journal)
    assert target.exists()


def test_first_write_emits_page_write_event_with_sha256(vault: Path, journal: Path) -> None:
    target = vault / "page.md"
    safe_write(target, "hello", by="core", journal_path=journal)
    events = read_events(journal)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, PageWriteEvent)
    assert event.hash == _sha256("hello")
    assert event.hash_algo == "sha256"
    assert event.by == "core"


def test_first_write_stores_path_relative_to_vault(vault: Path, journal: Path) -> None:
    target = vault / "meetings" / "2026-05-15.md"
    safe_write(target, "x", by="meeting", journal_path=journal)
    events = read_events(journal)
    assert isinstance(events[0], PageWriteEvent)
    assert events[0].path == "meetings/2026-05-15.md"


def test_repeated_write_with_no_drift_overwrites_and_appends_new_event(
    vault: Path, journal: Path
) -> None:
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    result = safe_write(target, "v2", by="core", journal_path=journal)
    assert result is WriteResult.WRITTEN
    assert target.read_text() == "v2"
    events = read_events(journal)
    assert len(events) == 2
    assert all(isinstance(e, PageWriteEvent) for e in events)
    page_writes = [e for e in events if isinstance(e, PageWriteEvent)]
    assert page_writes[-1].hash == _sha256("v2")


def test_no_op_write_of_identical_content_still_records_event(vault: Path, journal: Path) -> None:
    target = vault / "page.md"
    safe_write(target, "same", by="core", journal_path=journal)
    safe_write(target, "same", by="core", journal_path=journal)
    events = read_events(journal)
    assert len(events) == 2  # journal records every kit write attempt


# ---------------------------------------------------------------------------
# Drift path: sidecar + proposal event + .obsidianignore
# ---------------------------------------------------------------------------


def test_drift_writes_sidecar_and_leaves_original_untouched(vault: Path, journal: Path) -> None:
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")  # simulate the user editing the file
    result = safe_write(target, "v2 from kit", by="core", journal_path=journal)
    assert result is WriteResult.PROPOSAL
    assert target.read_text() == "user edits"
    assert (vault / "page.md.proposed").read_text() == "v2 from kit"


def test_drift_emits_page_proposal_event(vault: Path, journal: Path) -> None:
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="weekly-digest", journal_path=journal)

    events = read_events(journal)
    proposal = events[-1]
    assert isinstance(proposal, PageProposalEvent)
    assert proposal.path == "page.md"
    assert proposal.proposed_path == "page.md.proposed"
    assert proposal.hash == _sha256("v2")
    assert proposal.by == "weekly-digest"


def test_drift_creates_obsidianignore_with_proposed_pattern(vault: Path, journal: Path) -> None:
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="core", journal_path=journal)

    ignore = (vault / ".obsidianignore").read_text()
    assert OBSIDIAN_IGNORE_PROPOSED_PATTERN in ignore.splitlines()


def test_drift_does_not_duplicate_obsidianignore_pattern(vault: Path, journal: Path) -> None:
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="core", journal_path=journal)
    safe_write(target, "v3", by="core", journal_path=journal)

    lines = (vault / ".obsidianignore").read_text().splitlines()
    assert lines.count(OBSIDIAN_IGNORE_PROPOSED_PATTERN) == 1


def test_drift_appends_to_existing_obsidianignore(vault: Path, journal: Path) -> None:
    (vault / ".obsidianignore").write_text("# user patterns\nscratch/\n")
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="core", journal_path=journal)

    text = (vault / ".obsidianignore").read_text()
    assert "# user patterns" in text
    assert "scratch/" in text
    assert OBSIDIAN_IGNORE_PROPOSED_PATTERN in text.splitlines()


def test_drift_twice_overwrites_proposed_file_and_logs_new_event(
    vault: Path, journal: Path
) -> None:
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="core", journal_path=journal)
    safe_write(target, "v3", by="core", journal_path=journal)

    assert (vault / "page.md.proposed").read_text() == "v3"
    proposals = [e for e in read_events(journal) if isinstance(e, PageProposalEvent)]
    assert len(proposals) == 2
    assert proposals[-1].hash == _sha256("v3")


def test_drift_does_not_emit_a_page_write_event(vault: Path, journal: Path) -> None:
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="core", journal_path=journal)

    page_writes = [e for e in read_events(journal) if isinstance(e, PageWriteEvent)]
    assert len(page_writes) == 1  # only the original; the drifted write is a proposal


# ---------------------------------------------------------------------------
# resolve_proposal: the documented bypass per ADR-0004 step 6 (2026-05-15
# revision). Vault-side `wiki-conflict` skill calls this with the user's
# confirmed merge; it writes content directly, deletes the sidecar, and
# emits PageWrite + PageConflictResolved.
# ---------------------------------------------------------------------------


def _drive_to_proposal(vault: Path, journal: Path) -> Path:
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="core", journal_path=journal)
    return target


def test_resolve_proposal_writes_content_bypassing_drift(vault: Path, journal: Path) -> None:
    target = _drive_to_proposal(vault, journal)
    resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)
    assert target.read_text() == "merged"


def test_resolve_proposal_deletes_the_sidecar(vault: Path, journal: Path) -> None:
    target = _drive_to_proposal(vault, journal)
    sidecar = vault / "page.md.proposed"
    assert sidecar.exists()
    resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)
    assert not sidecar.exists()


def test_resolve_proposal_handles_missing_sidecar(vault: Path, journal: Path) -> None:
    target = _drive_to_proposal(vault, journal)
    (vault / "page.md.proposed").unlink()  # user manually removed it before resolving
    resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)
    assert target.read_text() == "merged"


def test_resolve_proposal_emits_page_write_and_conflict_resolved(
    vault: Path, journal: Path
) -> None:
    target = _drive_to_proposal(vault, journal)
    resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)

    events = read_events(journal)
    write_event = events[-2]
    audit_event = events[-1]
    assert isinstance(write_event, PageWriteEvent)
    assert isinstance(audit_event, PageConflictResolvedEvent)
    assert write_event.hash == _sha256("merged")
    assert audit_event.hash == _sha256("merged")
    assert write_event.path == "page.md"
    assert audit_event.path == "page.md"
    assert write_event.by == "wiki-conflict"
    assert audit_event.by == "wiki-conflict"


def test_after_resolve_subsequent_safe_write_sees_no_drift(vault: Path, journal: Path) -> None:
    target = _drive_to_proposal(vault, journal)
    resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)

    result = safe_write(target, "next kit version", by="core", journal_path=journal)
    assert result is WriteResult.WRITTEN
    assert target.read_text() == "next kit version"


def test_resolve_proposal_accepts_the_kit_version_unchanged(vault: Path, journal: Path) -> None:
    """User chose 'accept proposed' — content is the sidecar's content verbatim."""
    target = _drive_to_proposal(vault, journal)
    proposed_content = (vault / "page.md.proposed").read_text()
    resolve_proposal(target, proposed_content, by="wiki-conflict", journal_path=journal)
    assert target.read_text() == "v2"


def test_resolve_proposal_keeps_the_user_version(vault: Path, journal: Path) -> None:
    """User chose 'keep mine' — content is the user's current on-disk content."""
    target = _drive_to_proposal(vault, journal)
    user_content = target.read_text()
    resolve_proposal(target, user_content, by="wiki-conflict", journal_path=journal)
    assert target.read_text() == "user edits"

    result = safe_write(target, "kit again", by="core", journal_path=journal)
    assert result is WriteResult.WRITTEN


def test_resolve_proposal_creates_baseline_when_no_prior_writes(vault: Path, journal: Path) -> None:
    """resolve_proposal works even without a preceding safe_write history."""
    target = vault / "page.md"
    resolve_proposal(target, "fresh", by="wiki-conflict", journal_path=journal)
    assert target.read_text() == "fresh"
    events = read_events(journal)
    assert len(events) == 2
    assert isinstance(events[0], PageWriteEvent)
    assert isinstance(events[1], PageConflictResolvedEvent)


# ---------------------------------------------------------------------------
# Path-resolution contract for _relative_to_vault (retro-review qB3 + qC9).
# ---------------------------------------------------------------------------


def test_safe_write_outside_vault_raises_wikierror(
    vault: Path, journal: Path, tmp_path: Path
) -> None:
    """A path outside the vault root surfaces as ``WikiError``, not bare ``ValueError``.

    Retro-review qB3: the CLI boundary catches ``WikiError`` and renders
    one line; a bare ``ValueError`` from ``Path.relative_to`` leaks a
    Python traceback to the user.
    """

    outside = tmp_path.parent / "outside-the-vault.md"
    with pytest.raises(WikiError) as excinfo:
        safe_write(outside, "stray", by="core", journal_path=journal)
    # Lexically-outside path: short message form, no resolved-detail
    # branch (lexical equals resolved when the target's parents
    # already match their resolved form).
    assert "not inside the vault" in str(excinfo.value)
    assert "resolves to" not in str(excinfo.value)


def test_safe_write_region_outside_vault_raises_wikierror(
    vault: Path, journal: Path, tmp_path: Path
) -> None:
    """The qB3 wrapping holds for ``safe_write_region`` too.

    ``_relative_to_vault`` is the shared helper; all three callers
    must surface ``WikiError`` rather than a bare ``ValueError``.
    """

    outside = tmp_path.parent / "outside-the-vault.md"
    with pytest.raises(WikiError, match="not inside the vault"):
        safe_write_region(outside, "fields", "body", by="core", journal_path=journal)


def test_resolve_proposal_outside_vault_raises_wikierror(
    vault: Path, journal: Path, tmp_path: Path
) -> None:
    """The qB3 wrapping holds for ``resolve_proposal`` too.

    Same shared-helper contract as the two siblings above; a future
    refactor that bypasses ``_relative_to_vault`` for this call site
    must not leak a bare ``ValueError``.
    """

    outside = tmp_path.parent / "outside-the-vault.md"
    with pytest.raises(WikiError, match="not inside the vault"):
        resolve_proposal(outside, "merged", by="core", journal_path=journal)


def test_safe_write_normalizes_parent_dir_segments(vault: Path, journal: Path) -> None:
    """``..`` segments resolve to the same canonical key as a direct path.

    Retro-review qC9: drift detection keys off the journaled relative
    path. Writing ``meetings/2026-05-15.md`` and ``meetings/sub/../2026-05-15.md``
    must journal the same key, otherwise the second write looks like a
    first-write and silently overwrites.
    """

    target_direct = vault / "meetings" / "2026-05-15.md"
    safe_write(target_direct, "first", by="meeting", journal_path=journal)

    target_via_parent = vault / "meetings" / "sub" / ".." / "2026-05-15.md"
    safe_write(target_via_parent, "first", by="meeting", journal_path=journal)

    events = read_events(journal)
    page_writes = [e for e in events if isinstance(e, PageWriteEvent)]
    # Both writes should journal under the same canonical relative path.
    assert {e.path for e in page_writes} == {"meetings/2026-05-15.md"}


def test_safe_write_journals_same_key_when_symlink_and_real_paths_mix(
    tmp_path: Path,
) -> None:
    """Symlinked and real paths to the same file resolve to one journaled key.

    Retro-review qC9: a write whose ``journal_path`` traverses the real
    filesystem path and whose target traverses a symlink (or vice
    versa) must journal under the same canonical relative path —
    otherwise the second write looks like a first-write and silently
    overwrites. The previous lexical comparison would diverge here;
    only ``resolve()`` on both sides reconciles.
    """

    real_vault = tmp_path / "real-vault"
    real_vault.mkdir()
    (real_vault / ".wiki.journal").mkdir()
    symlink_vault = tmp_path / "symlinked-vault"
    symlink_vault.symlink_to(real_vault)

    journal_real = real_vault / ".wiki.journal" / "journal.jsonl"
    target_via_symlink = symlink_vault / "notes" / "x.md"
    safe_write(target_via_symlink, "v1", by="core", journal_path=journal_real)

    # Second write: journal via symlink, target via real path. The
    # canonical relative path is the same, so the second write must
    # see a matching baseline and write directly (not a proposal).
    journal_via_symlink = symlink_vault / ".wiki.journal" / "journal.jsonl"
    target_real = real_vault / "notes" / "x.md"
    result = safe_write(target_real, "v2", by="core", journal_path=journal_via_symlink)
    assert result is WriteResult.WRITTEN

    events = read_events(journal_real)
    page_writes = [e for e in events if isinstance(e, PageWriteEvent)]
    assert [e.path for e in page_writes] == ["notes/x.md", "notes/x.md"]


def test_safe_write_rejects_symlink_that_escapes_vault(tmp_path: Path) -> None:
    """A vault-internal symlink pointing outside the vault raises ``WikiError``.

    Retro-review qC9 §rejects-symlink-escape: the journal must not
    record a path that resolves outside the vault. A subsequent
    ``safe_write`` against the same lexical path would diverge from the
    resolved target, splitting the baseline silently. Surfacing as
    ``WikiError`` keeps the rejection visible.
    """

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".wiki.journal").mkdir()
    journal = vault / ".wiki.journal" / "journal.jsonl"

    # An external directory the user has symlinked into the vault.
    external = tmp_path / "external"
    external.mkdir()
    (external / "leaked.md").write_text("leaked", encoding="utf-8")
    (vault / "linked").symlink_to(external)

    target_through_symlink = vault / "linked" / "leaked.md"
    with pytest.raises(WikiError) as excinfo:
        safe_write(target_through_symlink, "kit content", by="core", journal_path=journal)
    # Divergent-resolved branch must surface both forms so the user
    # sees the escape path, not just a confusing "X is not inside Y"
    # when X lexically does live under Y.
    message = str(excinfo.value)
    assert "resolves to" in message
    assert "(resolved:" in message


# ---------------------------------------------------------------------------
# safe_write_region: per ADR-0003, writes into a kit-owned managed region of
# a shared infra file. Emits ManagedRegionWriteEvent on success. Intra-region
# drift falls through to the same proposal-sidecar flow as safe_write
# (writing the whole shared file with the region applied), and emits a
# PageProposalEvent. Drift outside any managed region is invisible by design.
# ---------------------------------------------------------------------------


def _seed_agents_md(vault: Path, body: str = "first entry") -> Path:
    """Plant a shared file with one empty managed region and one prose block."""
    target = vault / "AGENTS.md"
    target.write_text(
        "# AGENTS.md\n\n"
        "user note that should be invisible to the kit.\n\n"
        "<!-- BEGIN MANAGED: content-types -->\n"
        f"{body}\n"
        "<!-- END MANAGED: content-types -->\n"
        "\n"
        "trailing user notes\n"
    )
    return target


def test_safe_write_region_first_write_returns_written(vault: Path, journal: Path) -> None:
    target = _seed_agents_md(vault)
    result = safe_write_region(
        target, "content-types", "- meeting\n- recipe", by="core", journal_path=journal
    )
    assert result is WriteResult.WRITTEN


def test_safe_write_region_updates_region_body_in_place(vault: Path, journal: Path) -> None:
    target = _seed_agents_md(vault)
    safe_write_region(
        target, "content-types", "- meeting\n- recipe", by="core", journal_path=journal
    )
    text = target.read_text()
    assert "- meeting\n- recipe\n" in text
    assert "user note that should be invisible to the kit." in text
    assert "trailing user notes" in text


def test_safe_write_region_emits_managed_region_write_event(vault: Path, journal: Path) -> None:
    _seed_agents_md(vault)
    safe_write_region(
        vault / "AGENTS.md",
        "content-types",
        "- meeting",
        by="meeting",
        journal_path=journal,
    )
    events = read_events(journal)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, ManagedRegionWriteEvent)
    assert event.file == "AGENTS.md"
    assert event.region == "content-types"
    # Canonical region body adds a trailing newline before hashing —
    # matches the form ``install._normalise_snippet`` writes.
    assert event.content_hash == _sha256("- meeting\n")
    assert event.hash_algo == "sha256"
    assert event.by == "meeting"


def test_safe_write_region_repeated_no_drift_overwrites_and_appends(
    vault: Path, journal: Path
) -> None:
    target = _seed_agents_md(vault)
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)
    result = safe_write_region(target, "content-types", "v2", by="core", journal_path=journal)
    assert result is WriteResult.WRITTEN
    assert "v2" in target.read_text()
    events = read_events(journal)
    assert len(events) == 2
    assert all(isinstance(e, ManagedRegionWriteEvent) for e in events)


def test_safe_write_region_drift_inside_region_writes_proposal(vault: Path, journal: Path) -> None:
    target = _seed_agents_md(vault)
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)

    # User edits inside the region.
    drifted = target.read_text().replace("v1", "user override")
    target.write_text(drifted)

    result = safe_write_region(target, "content-types", "v2", by="core", journal_path=journal)
    assert result is WriteResult.PROPOSAL
    # On-disk file is untouched.
    assert "user override" in target.read_text()
    assert "v2" not in target.read_text()
    # Sidecar carries the kit's intended state: on-disk file + region update.
    sidecar = vault / "AGENTS.md.proposed"
    assert sidecar.exists()
    proposed_text = sidecar.read_text()
    assert "v2" in proposed_text
    # Unmanaged content on disk (user's edit was only inside region) flows
    # through unchanged.
    assert "user note that should be invisible to the kit." in proposed_text


def test_safe_write_region_drift_emits_page_proposal_event(vault: Path, journal: Path) -> None:
    target = _seed_agents_md(vault)
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)
    target.write_text(target.read_text().replace("v1", "user override"))
    safe_write_region(target, "content-types", "v2", by="weekly-digest", journal_path=journal)

    events = read_events(journal)
    proposal = events[-1]
    assert isinstance(proposal, PageProposalEvent)
    assert proposal.path == "AGENTS.md"
    assert proposal.proposed_path == "AGENTS.md.proposed"
    assert proposal.by == "weekly-digest"


def test_safe_write_region_drift_creates_obsidianignore(vault: Path, journal: Path) -> None:
    target = _seed_agents_md(vault)
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)
    target.write_text(target.read_text().replace("v1", "user override"))
    safe_write_region(target, "content-types", "v2", by="core", journal_path=journal)

    ignore = (vault / ".obsidianignore").read_text()
    assert OBSIDIAN_IGNORE_PROPOSED_PATTERN in ignore.splitlines()


def test_safe_write_region_ignores_drift_outside_managed_regions(
    vault: Path, journal: Path
) -> None:
    """ADR-0003: drift outside managed regions is invisible to the kit."""
    target = _seed_agents_md(vault)
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)

    # User edits *outside* the managed region.
    text = target.read_text()
    text = text.replace("trailing user notes", "user added new prose down here")
    target.write_text(text)

    result = safe_write_region(target, "content-types", "v2", by="core", journal_path=journal)
    assert result is WriteResult.WRITTEN
    # Region was updated; user's unmanaged edit survives.
    final = target.read_text()
    assert "v2" in final
    assert "user added new prose down here" in final


def test_safe_write_region_first_write_with_drifted_baseline_is_written(
    vault: Path, journal: Path
) -> None:
    """No prior managed_region.write event means there's no baseline to diverge from."""
    target = _seed_agents_md(vault, body="something the user dropped in")
    result = safe_write_region(target, "content-types", "kit v1", by="core", journal_path=journal)
    assert result is WriteResult.WRITTEN
    assert "kit v1" in target.read_text()


def test_safe_write_region_drift_twice_overwrites_proposal_and_logs(
    vault: Path, journal: Path
) -> None:
    target = _seed_agents_md(vault)
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)
    target.write_text(target.read_text().replace("v1", "user override"))
    safe_write_region(target, "content-types", "v2", by="core", journal_path=journal)
    safe_write_region(target, "content-types", "v3", by="core", journal_path=journal)

    proposed = (vault / "AGENTS.md.proposed").read_text()
    assert "v3" in proposed
    assert "v2" not in proposed
    proposals = [e for e in read_events(journal) if isinstance(e, PageProposalEvent)]
    assert len(proposals) == 2


def test_safe_write_region_raises_when_file_missing(vault: Path, journal: Path) -> None:
    with pytest.raises(FileNotFoundError):
        safe_write_region(
            vault / "AGENTS.md",
            "content-types",
            "body",
            by="core",
            journal_path=journal,
        )


def test_safe_write_region_raises_when_region_missing(vault: Path, journal: Path) -> None:
    target = _seed_agents_md(vault)
    with pytest.raises(ManagedRegionError):
        safe_write_region(target, "nonexistent", "body", by="core", journal_path=journal)


def test_safe_write_region_drift_event_carries_correct_hashes(vault: Path, journal: Path) -> None:
    target = _seed_agents_md(vault)
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)
    target.write_text(target.read_text().replace("v1", "user override"))
    safe_write_region(target, "content-types", "v2", by="core", journal_path=journal)

    events = read_events(journal)
    page_writes = [e for e in events if isinstance(e, PageWriteEvent)]
    region_writes = [e for e in events if isinstance(e, ManagedRegionWriteEvent)]
    # No PageWrite (would be a baseline-establishing whole-file write).
    assert page_writes == []
    # Only the first (no-drift) write produced a region event.
    assert len(region_writes) == 1
    assert region_writes[0].content_hash == _sha256("v1\n")


# ---------------------------------------------------------------------------
# safe-write-ordering spec — event-before-disk (qC3)
# ---------------------------------------------------------------------------
#
# Failure-injection family + happy-path snapshot family pin the two halves
# of the event-before-disk contract: "event durable when disk write fails"
# and "event durable BEFORE the disk write happens." Together a future
# refactor that flipped the order in the happy path (and left the failure
# path intact) cannot pass both.
#
# Path-scoped monkeypatches: ``journal.append_event`` and the holder-file
# helpers also call ``Path.write_text`` / ``Path.write_bytes``; a blanket
# raise would break them and produce misleading test failures.
# ---------------------------------------------------------------------------


def _patch_path_method(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    target: Path,
    *,
    raise_exc: BaseException | None = None,
    on_call: Callable[..., None] | None = None,
) -> None:
    """Path-scoped ``Path.<method_name>`` shim.

    On a call against ``target``, invokes ``on_call(self, *args, **kwargs)``
    if given, then raises ``raise_exc`` if given; otherwise delegates to
    the original implementation. Compares by absolute path strings so
    ``tmp_path``'s ``/private/var`` quirk on macOS doesn't desync.
    """
    original = getattr(Path, method_name)
    target_abs = str(target.absolute())

    def wrapper(self: Path, *args: object, **kwargs: object) -> object:
        if str(self.absolute()) == target_abs:
            if on_call is not None:
                on_call(self, *args, **kwargs)
            if raise_exc is not None:
                raise raise_exc
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, method_name, wrapper)


# --- failure-injection family ----------------------------------------------


def test_safe_write_event_durable_when_disk_write_raises(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure-injected ``Path.write_bytes`` leaves the journal entry durable.

    Under today's write→append order, the raise lands before the
    ``append_event`` call, so the journal stays empty and this fails red.
    After the reorder, the ``PageWriteEvent`` is durable and the file
    is absent (recoverable via ``wiki doctor``'s ``missing`` check).
    """
    target = vault / "meetings" / "2026-05-15.md"
    _patch_path_method(
        monkeypatch,
        "write_bytes",
        target,
        raise_exc=OSError("simulated disk failure"),
    )

    with pytest.raises(OSError, match="simulated disk failure"):
        safe_write(target, "hello\n", by="meeting", journal_path=journal)

    page_writes = [e for e in read_events(journal) if isinstance(e, PageWriteEvent)]
    assert [e.path for e in page_writes] == ["meetings/2026-05-15.md"]
    assert page_writes[0].hash == _sha256("hello\n")
    assert not target.exists()


def test_safe_write_drift_event_durable_when_sidecar_write_raises(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape as above, drift path: sidecar write raises after event.

    The proposal event is durable; the sidecar does not exist. The
    original on-disk file is untouched.
    """
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")

    proposed = vault / "page.md.proposed"
    _patch_path_method(
        monkeypatch,
        "write_bytes",
        proposed,
        raise_exc=OSError("simulated sidecar failure"),
    )

    with pytest.raises(OSError, match="simulated sidecar failure"):
        safe_write(target, "v2", by="core", journal_path=journal)

    proposals = [e for e in read_events(journal) if isinstance(e, PageProposalEvent)]
    assert [e.path for e in proposals] == ["page.md"]
    assert proposals[0].hash == _sha256("v2")
    assert not proposed.exists()
    assert target.read_text() == "user edits"


def test_safe_write_region_event_durable_when_disk_write_raises(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qC3 for ``safe_write_region``'s happy path."""
    target = _seed_agents_md(vault)
    pre_text = target.read_text()
    _patch_path_method(
        monkeypatch,
        "write_text",
        target,
        raise_exc=OSError("simulated write_text failure"),
    )

    with pytest.raises(OSError, match="simulated write_text failure"):
        safe_write_region(target, "content-types", "kit v1", by="core", journal_path=journal)

    region_writes = [e for e in read_events(journal) if isinstance(e, ManagedRegionWriteEvent)]
    assert len(region_writes) == 1
    assert region_writes[0].content_hash == _sha256("kit v1\n")
    assert target.read_text() == pre_text  # untouched


def test_safe_write_region_drift_event_durable_when_sidecar_write_raises(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qC3 for the region drift path — pairs with the page-level drift pin.

    A future refactor that flipped order specifically in the region
    drift branch (write sidecar → if-succeed append) would otherwise
    pass every existing test. This pin closes that gap.
    """
    target = _seed_agents_md(vault)
    safe_write_region(target, "content-types", "v1", by="core", journal_path=journal)
    # User edits inside the region — drives the next call to drift.
    drifted = target.read_text().replace("v1", "user override")
    target.write_text(drifted)
    pre_text = target.read_text()

    proposed = vault / "AGENTS.md.proposed"
    _patch_path_method(
        monkeypatch,
        "write_text",
        proposed,
        raise_exc=OSError("simulated region sidecar failure"),
    )

    with pytest.raises(OSError, match="simulated region sidecar failure"):
        safe_write_region(target, "content-types", "v2", by="core", journal_path=journal)

    proposals = [e for e in read_events(journal) if isinstance(e, PageProposalEvent)]
    assert [e.path for e in proposals] == ["AGENTS.md"]
    assert not proposed.exists()
    assert target.read_text() == pre_text  # original untouched


def test_resolve_proposal_events_durable_when_disk_write_raises(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qC3 for ``resolve_proposal``.

    Both ``PageWriteEvent`` and ``PageConflictResolvedEvent`` are
    durable; the target file is unchanged (matches pre-call content).
    """
    target = _drive_to_proposal(vault, journal)
    pre_text = target.read_text()
    _patch_path_method(
        monkeypatch,
        "write_bytes",
        target,
        raise_exc=OSError("simulated resolve failure"),
    )

    with pytest.raises(OSError, match="simulated resolve failure"):
        resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)

    events = read_events(journal)
    # Final two events from this call: PageWriteEvent then
    # PageConflictResolvedEvent, in spec §Behavior ``resolve_proposal``
    # step order.
    assert isinstance(events[-2], PageWriteEvent)
    assert isinstance(events[-1], PageConflictResolvedEvent)
    assert events[-2].hash == _sha256("merged")
    assert target.read_text() == pre_text


# --- happy-path snapshot family --------------------------------------------


def test_safe_write_event_in_journal_at_moment_of_disk_write(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At the moment ``Path.write_bytes`` runs, the event is already in the journal.

    Catches a future refactor that did ``write → if write_succeeded:
    append_event`` (which the failure-injection family would not).
    """
    target = vault / "page.md"
    snapshots: list[list[object]] = []

    def snapshot(self: Path, *_args: object, **_kwargs: object) -> None:
        snapshots.append(list(read_events(journal)))

    _patch_path_method(monkeypatch, "write_bytes", target, on_call=snapshot)
    safe_write(target, "hello", by="core", journal_path=journal)

    assert len(snapshots) == 1
    page_writes = [e for e in snapshots[0] if isinstance(e, PageWriteEvent)]
    assert [e.path for e in page_writes] == ["page.md"]
    assert page_writes[0].hash == _sha256("hello")


def test_safe_write_region_event_in_journal_at_moment_of_disk_write(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _seed_agents_md(vault)
    snapshots: list[list[object]] = []

    def snapshot(self: Path, *_args: object, **_kwargs: object) -> None:
        snapshots.append(list(read_events(journal)))

    _patch_path_method(monkeypatch, "write_text", target, on_call=snapshot)
    safe_write_region(target, "content-types", "kit v1", by="core", journal_path=journal)

    assert len(snapshots) == 1
    region_writes = [e for e in snapshots[0] if isinstance(e, ManagedRegionWriteEvent)]
    assert len(region_writes) == 1
    assert region_writes[0].content_hash == _sha256("kit v1\n")


def test_resolve_proposal_events_in_journal_at_moment_of_disk_write(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _drive_to_proposal(vault, journal)
    snapshots: list[list[object]] = []

    def snapshot(self: Path, *_args: object, **_kwargs: object) -> None:
        snapshots.append(list(read_events(journal)))

    _patch_path_method(monkeypatch, "write_bytes", target, on_call=snapshot)
    resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)

    assert len(snapshots) == 1
    journal_at_write = snapshots[0]
    # Both resolution events present at the moment of the disk write.
    assert any(
        isinstance(e, PageWriteEvent) and e.hash == _sha256("merged") for e in journal_at_write
    )
    assert any(
        isinstance(e, PageConflictResolvedEvent) and e.hash == _sha256("merged")
        for e in journal_at_write
    )


# --- construction (call-sequence snapshot, plan-level) ---------------------
#
# These observe disk state at journal-append time (legitimate, not
# mock-shape). They're explicitly classified as construction-grade in
# the plan and pair with the happy-path snapshot family above: that
# family observes the journal state at disk-write time, this family
# observes the disk state at append-event time. Together they pin both
# halves of the event-before-disk contract. Deleting either family
# alone loses the contract — keep them paired.
# ---------------------------------------------------------------------------


def test_safe_write_calls_append_event_before_write_bytes(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = vault / "page.md"
    target_existed_at_event: list[bool] = []

    from llm_wiki_kit.journal import append_event as journal_append_event

    def recording_append(j: Path, event: Event) -> None:
        target_existed_at_event.append(target.exists())
        journal_append_event(j, event)

    monkeypatch.setattr("llm_wiki_kit.write_helper.append_event", recording_append)
    safe_write(target, "hello", by="core", journal_path=journal)

    assert target_existed_at_event == [False]


def test_safe_write_drift_calls_append_event_before_proposed_write_bytes(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")

    proposed = vault / "page.md.proposed"
    proposed_existed_at_event: list[bool] = []

    from llm_wiki_kit.journal import append_event as journal_append_event

    def recording_append(j: Path, event: Event) -> None:
        if isinstance(event, PageProposalEvent):
            proposed_existed_at_event.append(proposed.exists())
        journal_append_event(j, event)

    monkeypatch.setattr("llm_wiki_kit.write_helper.append_event", recording_append)
    safe_write(target, "v2", by="core", journal_path=journal)

    assert proposed_existed_at_event == [False]


def test_safe_write_region_calls_append_event_before_write_text(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _seed_agents_md(vault, body="seed-body")
    body_at_event: list[str] = []

    from llm_wiki_kit.journal import append_event as journal_append_event

    def recording_append(j: Path, event: Event) -> None:
        if isinstance(event, ManagedRegionWriteEvent):
            body_at_event.append(target.read_text(encoding="utf-8"))
        journal_append_event(j, event)

    monkeypatch.setattr("llm_wiki_kit.write_helper.append_event", recording_append)
    safe_write_region(target, "content-types", "kit v1", by="core", journal_path=journal)

    assert len(body_at_event) == 1
    # File body at append-event time is still the seed text — region
    # has not been rewritten yet.
    assert "seed-body" in body_at_event[0]
    assert "kit v1" not in body_at_event[0]


def test_resolve_proposal_calls_append_events_before_rewrite_and_unlink(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _drive_to_proposal(vault, journal)
    sidecar = vault / "page.md.proposed"

    target_at_event: list[bytes] = []
    sidecar_present_at_event: list[bool] = []

    from llm_wiki_kit.journal import append_event as journal_append_event

    def recording_append(j: Path, event: Event) -> None:
        if isinstance(event, PageWriteEvent | PageConflictResolvedEvent):
            target_at_event.append(target.read_bytes())
            sidecar_present_at_event.append(sidecar.exists())
        journal_append_event(j, event)

    monkeypatch.setattr("llm_wiki_kit.write_helper.append_event", recording_append)
    resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)

    # At least two snapshots (PageWriteEvent + PageConflictResolvedEvent).
    assert len(target_at_event) >= 2
    # Both events fire while the target still carries the pre-resolve
    # content (user edits) and the sidecar still exists.
    assert all(snap == b"user edits" for snap in target_at_event)
    assert all(sidecar_present_at_event)


# --- crash-recovery retry (predicate disjunct lands in step 2) -------------
#
# These three pin the predicate refactor landed in plan step 2. They are
# RED until step 2 lands; after step 2 they pin the §Edge cases recovery
# contract. Fixture isolation: each test seeds the journal inline so a
# regression in one sub-case cannot be masked by a shared helper.
# ---------------------------------------------------------------------------


def test_safe_write_recovers_missing_file_when_baseline_journaled(
    vault: Path, journal: Path
) -> None:
    """Event durable, file absent → re-run takes the direct-write branch.

    Pre-state: a ``PageWriteEvent`` for ``page.md`` is journaled, but
    ``page.md`` was never (or no longer) on disk — the §Edge cases
    "Crash between event append and disk write, ``safe_write`` happy
    path, fresh file" case. The second-pass call must take the
    direct-write branch (not proposal); ``check_missing`` clears on the
    next doctor pass.
    """
    from datetime import UTC, datetime

    from llm_wiki_kit.journal import append_event

    seed_hash = _sha256("hello")
    append_event(
        journal,
        PageWriteEvent(
            timestamp=datetime.now(UTC),
            by="core",
            path="page.md",
            hash=seed_hash,
        ),
    )

    target = vault / "page.md"
    assert not target.exists()  # explicit pre-state assertion (fixture isolation)
    assert len(read_events(journal)) == 1

    result = safe_write(target, "hello", by="core", journal_path=journal)

    assert result is WriteResult.WRITTEN
    assert target.read_text() == "hello"
    assert not (vault / "page.md.proposed").exists()
    events = read_events(journal)
    page_writes = [e for e in events if isinstance(e, PageWriteEvent)]
    # Seed + recovery write.
    assert len(page_writes) == 2
    assert all(e.path == "page.md" for e in page_writes)


def test_resolve_proposal_crash_recovery_produces_idempotent_state(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash between events durable and disk write → retry produces same state."""
    from llm_wiki_kit.journal import replay_state

    target = _drive_to_proposal(vault, journal)

    # Crash on the FIRST call against ``target``; subsequent calls
    # delegate to the original. The family-(a) raise is unconditional
    # on the target path and would crash the retry too.
    original = Path.write_bytes
    target_abs = str(target.absolute())
    call_count = {"n": 0}

    def fragile_write_bytes(self: Path, data: bytes) -> int:
        if str(self.absolute()) == target_abs:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("first-call crash")
        return original(self, data)

    monkeypatch.setattr(Path, "write_bytes", fragile_write_bytes)

    with pytest.raises(OSError, match="first-call crash"):
        resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)
    # Second invocation succeeds.
    resolve_proposal(target, "merged", by="wiki-conflict", journal_path=journal)

    state = replay_state(read_events(journal))
    # The path's last-write-wins page-write hash is the merged content,
    # and the proposal has been resolved.
    assert state.page_writes["page.md"].hash == _sha256("merged")
    assert "page.md" not in state.pending_proposals
    assert target.read_text() == "merged"


def _drive_region_file_to_proposal(vault: Path, journal: Path) -> Path:
    """Seed a managed-region file and drive it to a pending proposal.

    Used by the two marker-destruction tests below to share setup
    without sharing assertions.
    """
    target = _seed_agents_md(vault, body="seed-body")
    safe_write_region(target, "content-types", "kit v1", by="core", journal_path=journal)
    drifted = target.read_text().replace("kit v1", "user override")
    target.write_text(drifted)
    safe_write_region(target, "content-types", "kit v2", by="core", journal_path=journal)
    assert (vault / "AGENTS.md.proposed").exists()
    return target


def _assert_resolve_proposal_marker_destruction_observables(
    vault: Path, journal: Path, target: Path, expected_content: str
) -> None:
    """Shared assertions for the two marker-destruction tests below.

    Spec §Behavior ``resolve_proposal`` step 5 contract: page-level
    events landed, the region-event loop was skipped, the user's
    merged content lands on disk, sidecar removed.
    """
    events = read_events(journal)
    page_writes_to_target = [
        e for e in events if isinstance(e, PageWriteEvent) and e.path == "AGENTS.md"
    ]
    resolves = [
        e for e in events if isinstance(e, PageConflictResolvedEvent) and e.path == "AGENTS.md"
    ]
    assert len(page_writes_to_target) == 1  # the resolve event
    assert len(resolves) == 1
    region_writes = [
        e for e in events if isinstance(e, ManagedRegionWriteEvent) and e.file == "AGENTS.md"
    ]
    # The seed wrote one region event (no-drift path). The destroyed
    # markers must not produce a second one.
    assert len(region_writes) == 1
    assert target.read_text() == expected_content
    assert not (vault / "AGENTS.md.proposed").exists()


def test_resolve_proposal_no_markers_skips_region_events_but_still_writes_disk(
    vault: Path, journal: Path
) -> None:
    """Spec §Behavior ``resolve_proposal`` step 5 — empty-parse arm.

    The user's merge has no BEGIN/END markers at all; ``managed_regions.parse``
    returns ``{}`` (no exception). The region-event loop iterates the
    known regions but every ``.get()`` returns ``None`` so no
    ``ManagedRegionWriteEvent`` is emitted; the disk write proceeds
    and preserves the user's resolution.
    """
    target = _drive_region_file_to_proposal(vault, journal)
    destroyed = "no markers at all — just plain prose\n"
    resolve_proposal(target, destroyed, by="wiki-conflict", journal_path=journal)
    _assert_resolve_proposal_marker_destruction_observables(vault, journal, target, destroyed)


def test_resolve_proposal_malformed_markers_skips_region_events_but_still_writes_disk(
    vault: Path, journal: Path
) -> None:
    """Spec §Behavior ``resolve_proposal`` step 5 — ``except ManagedRegionError`` arm.

    The user's merge has an unclosed ``BEGIN MANAGED`` marker;
    ``managed_regions.parse`` raises ``ManagedRegionError``. The
    implementation's ``try/except`` arm catches it, sets
    ``resolved_regions = None``, skips the region-event loop, and the
    disk write still proceeds — same end state as the empty-parse arm.
    A regression that removed the ``try/except`` (letting the error
    propagate and thus skipping the disk write) would fail this test
    on the ``target.read_text() == destroyed`` assertion.
    """
    target = _drive_region_file_to_proposal(vault, journal)
    # Unclosed BEGIN block — parses as malformed (raises ManagedRegionError).
    destroyed = "<!-- BEGIN MANAGED: content-types -->\nunclosed body\n"
    resolve_proposal(target, destroyed, by="wiki-conflict", journal_path=journal)
    _assert_resolve_proposal_marker_destruction_observables(vault, journal, target, destroyed)


def test_safe_write_region_crash_recovery_routes_to_proposal(vault: Path, journal: Path) -> None:
    """Event durable, region body diverges → recovery is proposal, not direct retry.

    Spec §Edge cases sub-case 2: pre-seed a managed-region file,
    journal a ``ManagedRegionWriteEvent`` whose ``content_hash``
    differs from the on-disk region body (simulating "event durable,
    write partial"); second call routes through proposal.
    """
    from datetime import UTC, datetime

    from llm_wiki_kit.journal import append_event

    target = _seed_agents_md(vault, body="seed-body")
    # Journal a ``ManagedRegionWriteEvent`` whose hash does NOT match
    # the current on-disk region body — simulates "event durable, write
    # partial or missed".
    append_event(
        journal,
        ManagedRegionWriteEvent(
            timestamp=datetime.now(UTC),
            by="core",
            file="AGENTS.md",
            region="content-types",
            content_hash=_sha256("would-have-been-written"),
        ),
    )
    assert len(read_events(journal)) == 1

    result = safe_write_region(
        target, "content-types", "would-have-been-written", by="core", journal_path=journal
    )

    assert result is WriteResult.PROPOSAL
    sidecar = vault / "AGENTS.md.proposed"
    assert sidecar.exists()
    assert "would-have-been-written" in sidecar.read_text()
    events = read_events(journal)
    assert isinstance(events[-1], PageProposalEvent)


# ---------------------------------------------------------------------------
# safe-write-ordering spec — unjournaled-existing → drift; byte-identical →
# adopt fast-path (qC6)
# ---------------------------------------------------------------------------


def test_safe_write_to_unjournaled_existing_file_writes_proposal(
    vault: Path, journal: Path
) -> None:
    """Inverts the obsolete ``test_first_write_overwrites_existing_file_without_journal_entry``.

    A user-authored file the kit has never journaled, with content that
    differs from the kit's proposed content, must route through
    ``.proposed`` — not silently overwrite.
    """
    target = vault / "page.md"
    target.write_text("user's pre-existing content")
    result = safe_write(target, "kit content", by="core", journal_path=journal)

    assert result is WriteResult.PROPOSAL
    sidecar = vault / "page.md.proposed"
    assert sidecar.exists()
    assert sidecar.read_text() == "kit content"
    proposals = [e for e in read_events(journal) if isinstance(e, PageProposalEvent)]
    assert len(proposals) == 1
    assert proposals[0].hash == _sha256("kit content")


def test_safe_write_to_unjournaled_existing_file_does_not_touch_original(
    vault: Path, journal: Path
) -> None:
    """Pinned explicitly so a regression that touches the user file fails red."""
    target = vault / "page.md"
    pre_text = "user's pre-existing content"
    target.write_text(pre_text)
    pre_mtime = target.stat().st_mtime

    safe_write(target, "kit content", by="core", journal_path=journal)

    assert target.read_text() == pre_text
    # mtime stability is a weaker signal than content equality (coarse-mtime
    # filesystems exist), but useful as a cross-check that the file was
    # not opened-for-write.
    assert target.stat().st_mtime == pre_mtime


def test_safe_write_first_write_to_absent_file_still_writes_directly(
    vault: Path, journal: Path
) -> None:
    """Guard against over-broadening: no-journal AND no-disk stays on direct-write.

    ``wiki init`` lands every primitive's first render through this
    branch; if qC6 broadened to include it, every fresh-vault write
    would propose.
    """
    target = vault / "fresh.md"
    result = safe_write(target, "first kit write", by="core", journal_path=journal)
    assert result is WriteResult.WRITTEN
    assert target.read_text() == "first kit write"
    page_writes = [e for e in read_events(journal) if isinstance(e, PageWriteEvent)]
    assert len(page_writes) == 1


def test_safe_write_adopt_fastpath_byte_identical_existing_file_writes_no_sidecar(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adopt fast-path: bytes already match → journal baseline, skip the disk write.

    Inode preservation is the load-bearing observable (Obsidian /
    inotify consumers react to inode changes; mtime resolution varies
    by filesystem). A path-scoped ``Path.write_bytes`` recorder pins
    "the write was actually skipped" cross-platform.
    """
    target = vault / "page.md"
    content = "byte-identical content\n"
    target.write_text(content)
    pre_ino = target.stat().st_ino

    write_bytes_calls: list[Path] = []
    original_write_bytes = Path.write_bytes
    target_abs = str(target.absolute())

    def recording_write_bytes(self: Path, data: bytes) -> int:
        if str(self.absolute()) == target_abs:
            write_bytes_calls.append(self)
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", recording_write_bytes)

    result = safe_write(target, content, by="core", journal_path=journal)

    assert result is WriteResult.WRITTEN
    assert target.read_text() == content
    assert target.stat().st_ino == pre_ino
    assert not (vault / "page.md.proposed").exists()
    assert write_bytes_calls == []  # the disk write was skipped

    page_writes = [e for e in read_events(journal) if isinstance(e, PageWriteEvent)]
    assert len(page_writes) == 1
    assert page_writes[0].hash == _sha256(content)


def test_safe_write_adopt_fastpath_baseline_becomes_journaled(vault: Path, journal: Path) -> None:
    """After the adopt fast-path, a repeat write sees no drift; drift routes proposal."""
    target = vault / "page.md"
    content = "adopt me"
    target.write_text(content)

    safe_write(target, content, by="core", journal_path=journal)
    # Repeat write: byte-equal — direct write, two events total.
    result2 = safe_write(target, content, by="core", journal_path=journal)
    assert result2 is WriteResult.WRITTEN
    assert len([e for e in read_events(journal) if isinstance(e, PageWriteEvent)]) == 2

    # Drift: user edits file, kit writes again — proposal.
    target.write_text("user edits over the adopted baseline")
    result3 = safe_write(target, "kit v2", by="core", journal_path=journal)
    assert result3 is WriteResult.PROPOSAL


def test_safe_write_adopt_fastpath_abandons_when_disk_changes_between_reads(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent editor between the original read and the re-read abandons adopt.

    The route-to-``PROPOSAL`` + ``PageProposalEvent``-tail assertions
    catch the headline regression (an implementer who drops the
    re-read), without which a stale "byte-identical" snapshot at the
    top of ``safe_write`` could journal a baseline that no longer
    matches disk.

    The trailing ``events[-1].hash`` assertion separately pins the
    abandon branch's hash semantics: a regression that journaled the
    *post-flip re-read hash* instead of the kit's ``new_hash`` would
    fail. (The stale-snapshot regression is covered by the route-to-
    PROPOSAL assertion above, not the hash assertion — in this
    fixture, the pre-flip ``on_disk_hash`` equals ``new_hash`` by
    construction.)
    """
    target = vault / "page.md"
    target.write_text("original bytes")

    saved_write_bytes = Path.write_bytes
    saved_read_bytes = Path.read_bytes
    target_abs = str(target.absolute())
    read_count = {"n": 0}

    def flipping_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        if str(self.absolute()) == target_abs:
            read_count["n"] += 1
            if read_count["n"] == 2:
                # Adopt-branch re-read: flip disk content so the
                # re-read hash diverges from new_hash.
                saved_write_bytes(self, b"concurrent edit landed")
        return saved_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", flipping_read_bytes)

    result = safe_write(target, "original bytes", by="core", journal_path=journal)

    assert result is WriteResult.PROPOSAL
    sidecar = vault / "page.md.proposed"
    assert sidecar.exists()
    events = read_events(journal)
    assert isinstance(events[-1], PageProposalEvent)
    # The proposal carries the kit's new_hash, not anything derived
    # from the stale top-of-function snapshot.
    assert events[-1].hash == _sha256("original bytes")


def test_safe_write_adopt_fastpath_records_post_reread_timestamp(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §Behavior Adopt fast-path step 3: timestamp is post-re-read, not call-entry."""
    from datetime import UTC, datetime

    target = vault / "page.md"
    content = "adopt me"
    target.write_text(content)

    timestamps = [
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 2, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 3, 3, 0, 0, tzinfo=UTC),
    ]
    now_calls: list[datetime] = []

    def fake_now() -> datetime:
        result = timestamps[len(now_calls)]
        now_calls.append(result)
        return result

    monkeypatch.setattr("llm_wiki_kit.write_helper._now", fake_now)

    result = safe_write(target, content, by="core", journal_path=journal)
    assert result is WriteResult.WRITTEN

    # Two calls: call-entry, then post-re-read recompute. A future
    # refactor that drops or adds a now() call fails this.
    assert len(now_calls) == 2

    page_writes = [e for e in read_events(journal) if isinstance(e, PageWriteEvent)]
    assert len(page_writes) == 1
    # Journaled timestamp matches the post-re-read recompute, not the
    # call-entry now.
    assert page_writes[0].timestamp == timestamps[1]


def test_safe_write_region_unjournaled_region_byte_identical_still_writes_directly(
    vault: Path, journal: Path
) -> None:
    """Byte-identical region complement to the existing drifted-baseline pin.

    Pairs with
    ``test_safe_write_region_first_write_with_drifted_baseline_is_written``;
    together they pin the page-vs-region distinction per spec §Non-goals
    "Why qC6 is page-scoped". A future "mirror the page-level adopt
    fast-path into regions" refactor would fail this — the region's
    direct-write semantics are load-bearing for
    ``install.aggregate_region_contributions``.
    """
    target = _seed_agents_md(vault, body="kit v1")
    result = safe_write_region(target, "content-types", "kit v1", by="core", journal_path=journal)
    assert result is WriteResult.WRITTEN
    region_writes = [e for e in read_events(journal) if isinstance(e, ManagedRegionWriteEvent)]
    assert len(region_writes) == 1
    assert region_writes[0].content_hash == _sha256("kit v1\n")


# ---------------------------------------------------------------------------
# safe-write-ordering spec — ``.obsidianignore`` documented non-journaled
# bypass (C2)
# ---------------------------------------------------------------------------


def test_ensure_obsidianignore_does_not_journal(vault: Path, journal: Path) -> None:
    """First drift produces zero ``PageWriteEvent``s whose path is ``.obsidianignore``.

    Pins the spec's contract: the bypass is the explicit choice, not an
    oversight. A future maintainer who routes ``.obsidianignore``
    through ``safe_write`` would re-introduce the page-drift-on-user-edit
    UX the spec rejects.
    """
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2 from kit", by="core", journal_path=journal)

    # ``.obsidianignore`` was created (drift path appended the pattern).
    assert (vault / ".obsidianignore").exists()
    # …but no journal event names it.
    page_writes = [e for e in read_events(journal) if isinstance(e, PageWriteEvent)]
    assert not any(e.path == ".obsidianignore" for e in page_writes)


def test_ensure_obsidianignore_remains_idempotent_via_pattern_check(
    vault: Path, journal: Path
) -> None:
    """Subsequent drift events do not rewrite ``.obsidianignore`` once the pattern is present."""
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="core", journal_path=journal)

    first_mtime = (vault / ".obsidianignore").stat().st_mtime
    # Drive a second drift on a different file.
    target2 = vault / "other.md"
    safe_write(target2, "v1", by="core", journal_path=journal)
    target2.write_text("user edits")
    safe_write(target2, "v2", by="core", journal_path=journal)

    # ``.obsidianignore`` is untouched by the second drift — the
    # additive-merge body's pattern-already-present check short-circuited.
    assert (vault / ".obsidianignore").stat().st_mtime == first_mtime


def test_obsidianignore_bypass_doc_constant_points_at_this_spec() -> None:
    """The load-bearing pin: any change to the bypass authority is grep-discoverable."""
    assert OBSIDIANIGNORE_BYPASS_DOC == "docs/specs/safe-write-ordering/spec.md"


def test_ensure_obsidianignore_docstring_references_bypass_constant() -> None:
    """Paired with the constant test so a docstring paraphrase fails red.

    Intentionally brittle — the paraphrase failure mode is what this
    test catches. A maintainer who legitimately renames the constant
    updates both tests together.
    """
    doc = _ensure_obsidianignore.__doc__ or ""
    assert "OBSIDIANIGNORE_BYPASS_DOC" in doc


def test_doctor_does_not_flag_obsidianignore_as_orphan(vault: Path, journal: Path) -> None:
    """``.obsidianignore`` with no journal entry must not surface as ``orphan``.

    Post qC10 + C6 the orphan check derives its kit-owned set from
    journaled writes, so an unjournaled ``.obsidianignore`` is never a
    candidate. This test pins that absence so a future maintainer
    doesn't add a special-case claim back in.
    """
    from llm_wiki_kit.doctor import ORPHAN, run_doctor

    # Drive a proposal so ``.obsidianignore`` lands on disk.
    target = vault / "page.md"
    safe_write(target, "v1", by="core", journal_path=journal)
    target.write_text("user edits")
    safe_write(target, "v2", by="core", journal_path=journal)
    assert (vault / ".obsidianignore").exists()

    issues = run_doctor(vault, kit_root=Path("."))  # kit_root unused by orphan check
    orphan_issues = [i for i in issues if i.kind == ORPHAN]
    assert not any(i.path == ".obsidianignore" for i in orphan_issues)


# ---------------------------------------------------------------------------
# journal-reader-cache spec (qC4) — write_helper consumes the cache
# ---------------------------------------------------------------------------


def test_safe_write_inside_cache_scope_reads_journal_once(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five distinct safe_write calls inside the scope hit the disk once."""
    import llm_wiki_kit.journal as _journal
    from llm_wiki_kit.journal import use_journal_cache

    reads = {"n": 0}
    original = _journal.read_events

    def counting_read_events(p: Path) -> list[Event]:
        if p == journal:
            reads["n"] += 1
        return original(p)

    # Patch at module of origin so both write_helper's `read_events` and
    # `JournalReader.events()` (which calls `read_events(self.journal_path)`
    # inside `journal.py`) go through the counter.
    monkeypatch.setattr(_journal, "read_events", counting_read_events)

    with use_journal_cache(journal):
        for i in range(5):
            safe_write(vault / f"p{i}.md", f"v{i}", by="core", journal_path=journal)
    assert reads["n"] == 1


def test_safe_write_outside_cache_scope_unchanged(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a cache scope, every safe_write re-reads the journal (today's behavior)."""
    import llm_wiki_kit.journal as _journal

    reads = {"n": 0}
    original = _journal.read_events

    def counting_read_events(p: Path) -> list[Event]:
        if p == journal:
            reads["n"] += 1
        return original(p)

    monkeypatch.setattr(_journal, "read_events", counting_read_events)

    for i in range(5):
        safe_write(vault / f"p{i}.md", f"v{i}", by="core", journal_path=journal)
    # write_helper._read_events_cached routes the no-cache fall-through
    # through journal.read_events (the patched function). Today's
    # safe_write makes exactly one _baseline_hash call per write, so
    # the count is exactly 5. Tighten to `==` so a future refactor
    # that doubles read count per call (e.g. a second journal walk)
    # is caught here.
    assert reads["n"] == 5


def test_safe_write_region_inside_cache_scope_reads_journal_once(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import llm_wiki_kit.journal as _journal
    from llm_wiki_kit.journal import use_journal_cache

    target = _seed_agents_md(vault, body="seed")
    reads = {"n": 0}
    original = _journal.read_events

    def counting_read_events(p: Path) -> list[Event]:
        if p == journal:
            reads["n"] += 1
        return original(p)

    monkeypatch.setattr(_journal, "read_events", counting_read_events)

    with use_journal_cache(journal):
        for body in ["v1", "v2", "v3"]:
            safe_write_region(target, "content-types", body, by="core", journal_path=journal)
    assert reads["n"] == 1


def test_resolve_proposal_inside_cache_scope_reads_journal_once(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resolve_proposal's _known_regions_for_file walk hits the cache."""
    import llm_wiki_kit.journal as _journal
    from llm_wiki_kit.journal import use_journal_cache

    # Seed: drive a region file to proposal.
    target = _drive_region_file_to_proposal(vault, journal)

    reads = {"n": 0}
    original = _journal.read_events

    def counting_read_events(p: Path) -> list[Event]:
        if p == journal:
            reads["n"] += 1
        return original(p)

    monkeypatch.setattr(_journal, "read_events", counting_read_events)

    with use_journal_cache(journal):
        resolve_proposal(target, target.read_text(), by="wiki-conflict", journal_path=journal)
    # The cache loaded once; the known-regions walk and any baseline
    # lookups all consulted it.
    assert reads["n"] == 1


def test_safe_write_inside_cache_sees_just_appended_event(
    vault: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second safe_write of byte-identical content takes repeat-write, not adopt.

    Because the cache reflects the first call's PageWriteEvent, the
    second call's baseline lookup finds it and routes to direct-write.
    Without the cache invalidation hook, the second call would see an
    empty cache and route to the adopt fast-path.

    Direct-write reuses the call-entry ``_now()`` for its event;
    adopt recomputes ``_now()`` post-re-read (spec §Behavior step 3).
    Injecting a deterministic ``_now`` and asserting the total call
    count is exactly 2 (one per safe_write, NOT three from an
    adopt-recompute) pins the branch discrimination — without this, an
    "event count == 2" assertion alone would pass under either branch.
    """
    from datetime import UTC, datetime

    from llm_wiki_kit.journal import use_journal_cache

    target = vault / "page.md"

    timestamps = [
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 2, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 3, 3, 0, 0, tzinfo=UTC),
    ]
    now_calls: list[datetime] = []

    def fake_now() -> datetime:
        result = timestamps[len(now_calls)]
        now_calls.append(result)
        return result

    monkeypatch.setattr("llm_wiki_kit.write_helper._now", fake_now)

    with use_journal_cache(journal):
        first = safe_write(target, "v1", by="core", journal_path=journal)
        assert first is WriteResult.WRITTEN
        second = safe_write(target, "v1", by="core", journal_path=journal)
        assert second is WriteResult.WRITTEN

    # Direct-write uses one _now() per call; adopt uses two. Total
    # across two direct-write calls is 2. If the second call entered
    # adopt (cache hook broken → empty cache → no_history=True →
    # bytes_match=True → adopt), len(now_calls) would be 3.
    assert len(now_calls) == 2, (
        f"second call must take direct-write (1 _now), not adopt (2 _now); "
        f"got {len(now_calls)} total _now() calls across both writes"
    )

    page_writes = [e for e in read_events(journal) if isinstance(e, PageWriteEvent)]
    assert len(page_writes) == 2
    assert page_writes[0].timestamp == timestamps[0]
    assert page_writes[1].timestamp == timestamps[1]
    assert target.read_text() == "v1"
