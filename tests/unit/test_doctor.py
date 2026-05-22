"""Unit tests for the pure checks behind ``wiki doctor``.

Each test pins one issue-kind contract from the Task-12 spec:

* ``page-drift`` — on-disk hash diverges, no pending proposal.
* ``managed-region-drift`` — region body diverges from the latest event.
* ``pending-proposal`` — the proposed sidecar surfaces by its path.
* ``orphan`` — kit-owned paths with no journal event are flagged;
  user-owned territory is invisible.
* ``missing`` — a journaled write whose file is gone.
* ``primitive-missing`` — a recorded install the catalog no longer has.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from llm_wiki_kit import doctor
from llm_wiki_kit.doctor import (
    _DEFAULT_STALE_HOURS,
    Issue,
    check_managed_region_drift,
    check_missing,
    check_orphans,
    check_page_drift,
    check_pending_proposals,
    check_primitive_missing,
    check_stale_lock,
    format_issue,
    run_doctor,
)
from llm_wiki_kit.journal import append_event
from llm_wiki_kit.models import (
    HeldLock,
    LockAcquiredEvent,
    ManagedRegionWriteEvent,
    PageProposalEvent,
    PageWriteEvent,
    PrimitiveInstallEvent,
    SourceIngestEvent,
    VaultInitEvent,
    VaultState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


NOW = datetime(2026, 5, 16, tzinfo=UTC)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _state_with_page(path: str, content: str) -> VaultState:
    event = PageWriteEvent(timestamp=NOW, by="core", path=path, hash=_hash(content))
    return VaultState(page_writes={path: event})


def _vault(tmp_path: Path) -> Path:
    (tmp_path / ".wiki.journal").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# format_issue
# ---------------------------------------------------------------------------


def test_format_issue_without_detail_omits_parentheses() -> None:
    assert format_issue(Issue("page-drift", "AGENTS.md")) == "page-drift: AGENTS.md"


def test_format_issue_with_detail_renders_parens() -> None:
    issue = Issue("managed-region-drift", "x.yaml:fields", "region missing")
    assert format_issue(issue) == "managed-region-drift: x.yaml:fields (region missing)"


# ---------------------------------------------------------------------------
# check_page_drift
# ---------------------------------------------------------------------------


def test_page_drift_clean_match(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "AGENTS.md").write_text("hello", encoding="utf-8")
    state = _state_with_page("AGENTS.md", "hello")

    assert check_page_drift(state, vault, []) == []


def test_page_drift_reports_edited_file(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "AGENTS.md").write_text("user edit", encoding="utf-8")
    state = _state_with_page("AGENTS.md", "hello")

    assert check_page_drift(state, vault, []) == [Issue("page-drift", "AGENTS.md")]


def test_page_drift_skipped_when_proposal_pending(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "AGENTS.md").write_text("user edit", encoding="utf-8")
    write_event = PageWriteEvent(timestamp=NOW, by="core", path="AGENTS.md", hash=_hash("hello"))
    proposal_event = PageProposalEvent(
        timestamp=NOW,
        by="core",
        path="AGENTS.md",
        proposed_path="AGENTS.md.proposed",
        hash=_hash("kit version"),
    )
    state = VaultState(
        page_writes={"AGENTS.md": write_event},
        pending_proposals={"AGENTS.md": proposal_event},
    )

    # Page-drift defers to the pending-proposal check; one issue per path, not two.
    assert check_page_drift(state, vault, []) == []


def test_page_drift_skips_missing_files(tmp_path: Path) -> None:
    # check_missing owns "file is gone"; page-drift should not double-report.
    vault = _vault(tmp_path)
    state = _state_with_page("AGENTS.md", "hello")

    assert check_page_drift(state, vault, []) == []


def test_page_drift_skipped_when_latest_write_is_region_write(tmp_path: Path) -> None:
    """A file last written by ``safe_write_region`` is not flagged at the page level.

    Contract: the install pipeline's aggregator mutates managed-region
    files in place after the seed primitive's ``safe_write``. The seed's
    ``PageWriteEvent`` baseline goes stale by design once any region
    write happens; the file's *region-level* drift is what
    ``check_managed_region_drift`` exists to catch, and double-reporting
    at the page level would surface every region-bearing file as
    drifted after every install (the Task-19 regression Blocker 2
    fixed). This test pins the skip so a future contributor doesn't
    revert the fix by "tightening" page-drift coverage. The
    surrounding-text-survives contract this enables is also recorded
    in AGENTS.md (user-vault content outside managed markers is the
    user's territory; the kit does not police it).
    """

    vault = _vault(tmp_path)
    # The on-disk file diverges from what the original ``PageWriteEvent``
    # journaled (the aggregator rewrote it in place); without the skip,
    # ``check_page_drift`` would report ``page-drift``.
    (vault / "AGENTS.md").write_text("rewritten by aggregator", encoding="utf-8")

    page_event = PageWriteEvent(
        timestamp=NOW, by="core", path="AGENTS.md", hash=_hash("seed bytes")
    )
    region_event = ManagedRegionWriteEvent(
        timestamp=NOW,
        by="wiki-init",
        file="AGENTS.md",
        region="content-types",
        content_hash=_hash("body\n"),
    )
    state = VaultState(page_writes={"AGENTS.md": page_event})

    assert check_page_drift(state, vault, [page_event, region_event]) == []


def test_page_drift_resumes_after_later_page_write(tmp_path: Path) -> None:
    """A later ``PageWriteEvent`` re-baselines and re-enables page-drift.

    Sequence: ``PageWrite (seed)`` → ``ManagedRegionWrite`` (skip
    kicks in) → ``PageWrite (resolve_proposal merge)`` → page-drift
    coverage is restored against the new baseline. Pins the
    ``_files_with_managed_region_write_after_page_write`` "latest
    wins" contract — a third event in either direction flips the
    decision.
    """

    vault = _vault(tmp_path)
    (vault / "AGENTS.md").write_text("user edit", encoding="utf-8")

    seed = PageWriteEvent(timestamp=NOW, by="core", path="AGENTS.md", hash=_hash("seed"))
    region = ManagedRegionWriteEvent(
        timestamp=NOW,
        by="wiki-init",
        file="AGENTS.md",
        region="content-types",
        content_hash=_hash("body\n"),
    )
    resolved = PageWriteEvent(
        timestamp=NOW, by="wiki-conflict", path="AGENTS.md", hash=_hash("merged")
    )
    state = VaultState(page_writes={"AGENTS.md": resolved})

    # On-disk content (``"user edit"``) diverges from the latest
    # ``PageWriteEvent``'s hash (``_hash("merged")``); page-drift fires.
    assert check_page_drift(state, vault, [seed, region, resolved]) == [
        Issue("page-drift", "AGENTS.md")
    ]


# ---------------------------------------------------------------------------
# check_managed_region_drift
# ---------------------------------------------------------------------------


SCHEMA_TEMPLATE = "types:\n  # BEGIN MANAGED: types\n{types_body}  # END MANAGED: types\n"


def test_managed_region_drift_clean_match(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    body = "  - meeting\n"
    schema = SCHEMA_TEMPLATE.format(types_body=body)
    (vault / "frontmatter.schema.yaml").write_text(schema, encoding="utf-8")

    event = ManagedRegionWriteEvent(
        timestamp=NOW,
        by="wiki-init",
        file="frontmatter.schema.yaml",
        region="types",
        # Hash matches the canonical (trailing-newline-appended) form
        # the write paths emit — see ``managed_regions.canonical_region_body``.
        content_hash=_hash("  - meeting\n"),
    )

    assert check_managed_region_drift([event], vault, VaultState()) == []


def test_managed_region_drift_reports_edited_body(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    schema = SCHEMA_TEMPLATE.format(types_body="  - meeting\n  - injected\n")
    (vault / "frontmatter.schema.yaml").write_text(schema, encoding="utf-8")

    event = ManagedRegionWriteEvent(
        timestamp=NOW,
        by="wiki-init",
        file="frontmatter.schema.yaml",
        region="types",
        content_hash=_hash("  - meeting"),
    )

    issues = check_managed_region_drift([event], vault, VaultState())
    assert issues == [Issue("managed-region-drift", "frontmatter.schema.yaml:types")]


def test_managed_region_drift_reports_removed_region(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "frontmatter.schema.yaml").write_text("types: []\n", encoding="utf-8")

    event = ManagedRegionWriteEvent(
        timestamp=NOW,
        by="wiki-init",
        file="frontmatter.schema.yaml",
        region="types",
        content_hash=_hash("  - meeting"),
    )

    issues = check_managed_region_drift([event], vault, VaultState())
    assert issues == [
        Issue("managed-region-drift", "frontmatter.schema.yaml:types", "region missing")
    ]


def test_managed_region_drift_uses_latest_event_per_region(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    schema = SCHEMA_TEMPLATE.format(types_body="  - meeting\n")
    (vault / "frontmatter.schema.yaml").write_text(schema, encoding="utf-8")

    stale = ManagedRegionWriteEvent(
        timestamp=NOW,
        by="wiki-init",
        file="frontmatter.schema.yaml",
        region="types",
        content_hash=_hash("  - obsolete"),
    )
    latest = ManagedRegionWriteEvent(
        timestamp=NOW,
        by="wiki-add",
        file="frontmatter.schema.yaml",
        region="types",
        # Canonical hash form, matching what the write paths emit.
        content_hash=_hash("  - meeting\n"),
    )

    # The second event shadows the first — no drift.
    assert check_managed_region_drift([stale, latest], vault, VaultState()) == []


def test_managed_region_drift_skips_files_with_pending_proposal(tmp_path: Path) -> None:
    """Retro-review #B6: a file with an open ``.proposed`` sidecar has
    already been flagged as ``pending-proposal``; reporting it again as
    ``managed-region-drift`` is double-counting the same user-actionable
    state. Pairs with #F-B1's resolve fix.
    """

    from llm_wiki_kit.models import PageProposalEvent

    vault = _vault(tmp_path)
    schema = SCHEMA_TEMPLATE.format(types_body="  - injected by the user\n")
    (vault / "frontmatter.schema.yaml").write_text(schema, encoding="utf-8")

    event = ManagedRegionWriteEvent(
        timestamp=NOW,
        by="wiki-init",
        file="frontmatter.schema.yaml",
        region="types",
        content_hash=_hash("  - meeting"),
    )
    proposal = PageProposalEvent(
        timestamp=NOW,
        by="core",
        path="frontmatter.schema.yaml",
        proposed_path="frontmatter.schema.yaml.proposed",
        hash=_hash("anything"),
    )
    state = VaultState(pending_proposals={"frontmatter.schema.yaml": proposal})

    assert check_managed_region_drift([event], vault, state) == []


# ---------------------------------------------------------------------------
# check_pending_proposals
# ---------------------------------------------------------------------------


def test_pending_proposals_surfaces_sidecar_paths() -> None:
    proposal = PageProposalEvent(
        timestamp=NOW,
        by="core",
        path="AGENTS.md",
        proposed_path="AGENTS.md.proposed",
        hash=_hash("kit version"),
    )
    state = VaultState(pending_proposals={"AGENTS.md": proposal})

    assert check_pending_proposals(state) == [Issue("pending-proposal", "AGENTS.md.proposed")]


def test_pending_proposals_empty_state() -> None:
    assert check_pending_proposals(VaultState()) == []


# ---------------------------------------------------------------------------
# check_orphans
# ---------------------------------------------------------------------------


def test_orphan_reports_file_under_kit_path_with_no_event(tmp_path: Path) -> None:
    """Retro-review qC10+C6: a top-level dir is kit territory once any
    journaled write lives under it; strays inside that dir surface as
    orphans.
    """

    vault = _vault(tmp_path)
    skills = vault / "skills" / "ingest"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("stray", encoding="utf-8")

    # One journaled path under ``skills/`` makes ``skills`` kit-owned.
    state = VaultState(page_writes=_state_with_page("skills/known.md", "k").page_writes)
    assert check_orphans(state, vault) == [Issue("orphan", "skills/ingest/SKILL.md")]


def test_orphan_ignores_user_owned_paths(tmp_path: Path) -> None:
    """A directory the kit has never written to is user territory."""

    vault = _vault(tmp_path)
    (vault / "personal").mkdir()
    (vault / "personal" / "notes.md").write_text("my notes", encoding="utf-8")

    # Seed an unrelated kit-owned dir so the state isn't degenerate.
    state = VaultState(page_writes=_state_with_page("skills/known.md", "k").page_writes)
    assert check_orphans(state, vault) == []


def test_orphan_does_not_derive_territory_from_non_page_write_events(tmp_path: Path) -> None:
    """Pin the doctrine: only ``page.write`` extends orphan territory.

    ``check_orphans`` documents that managed-region writes and
    ingest's produced pages are *not* folded in — those events
    reference paths that flow through ``safe_write``, which already
    emits a paired ``PageWriteEvent``. A future contributor who
    silently folds in another event type would silently expand
    territory; this test fails if that happens.

    Both event types named in the docstring are exercised — the
    ``ManagedRegionWriteEvent`` half is the more plausible misstep
    because managed-region writes look like "kit writes" to a casual
    reader.
    """

    vault = _vault(tmp_path)
    (vault / "_templates").mkdir()
    (vault / "_templates" / "rogue.yaml").write_text("user file", encoding="utf-8")

    # 1) SourceIngestEvent.produced_pages alone does not extend territory.
    state_with_ingest = VaultState(
        ingested_sources={
            "/some/source.txt": SourceIngestEvent(
                timestamp=NOW,
                by="wiki-ingest",
                source="/some/source.txt",
                source_hash="a" * 64,
                content_type="meeting",
                produced_pages=["_templates/synthesized.md"],
            )
        }
    )
    assert check_orphans(state_with_ingest, vault) == []

    # 2) ``pending_proposals`` is consulted only to skip ``.proposed``
    #    sidecars; it must never *extend* territory. A state with a
    #    proposal under ``_templates/`` but no ``page.write`` under it
    #    leaves the rogue invisible — pinning that the candidate-list
    #    derivation reads only ``state.page_writes``. This catches a
    #    regression where a future contributor adds a different state
    #    field (e.g. a ``managed_region_writes`` projection) and folds
    #    it into territory: the new field would land alongside
    #    ``pending_proposals``, and this assertion would force the
    #    contributor to update the test in the same PR.
    proposal = PageProposalEvent(
        timestamp=NOW,
        by="core",
        path="_templates/synthesized.md",
        proposed_path="_templates/synthesized.md.proposed",
        hash=_hash("kit version"),
    )
    state_with_proposal_only = VaultState(pending_proposals={"_templates/synthesized.md": proposal})
    assert check_orphans(state_with_proposal_only, vault) == []


def test_orphan_silent_when_no_paths_journaled(tmp_path: Path) -> None:
    """Retro-review qC10+C6: an empty journal claims no territory.

    A vault with no page-writes has no derived kit-owned dirs or files,
    so even files that used to be in the static ``KIT_OWNED_DIRS``
    (``skills/``, ``_templates/``, ``wiki/``) are invisible to the
    orphan check. The check fires only after the kit has written
    something — which is the install pipeline's job.
    """

    vault = _vault(tmp_path)
    (vault / "skills" / "rogue").mkdir(parents=True)
    (vault / "skills" / "rogue" / "SKILL.md").write_text("stray", encoding="utf-8")

    assert check_orphans(VaultState(), vault) == []


def test_orphan_ignores_proposed_sidecars(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "AGENTS.md").write_text("baseline", encoding="utf-8")
    (vault / "AGENTS.md.proposed").write_text("kit version", encoding="utf-8")

    proposal = PageProposalEvent(
        timestamp=NOW,
        by="core",
        path="AGENTS.md",
        proposed_path="AGENTS.md.proposed",
        hash=_hash("kit version"),
    )
    state = VaultState(
        page_writes={
            "AGENTS.md": PageWriteEvent(
                timestamp=NOW, by="core", path="AGENTS.md", hash=_hash("baseline")
            )
        },
        pending_proposals={"AGENTS.md": proposal},
    )

    # AGENTS.md is journaled; AGENTS.md.proposed surfaces via pending-proposal,
    # not orphan, even though no event names ``proposed_path`` directly.
    assert check_orphans(state, vault) == []


# ---------------------------------------------------------------------------
# check_missing
# ---------------------------------------------------------------------------


def test_missing_reports_vanished_file(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    state = _state_with_page("AGENTS.md", "hello")

    assert check_missing(state, vault) == [Issue("missing", "AGENTS.md")]


def test_missing_silent_when_file_present(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / "AGENTS.md").write_text("hello", encoding="utf-8")
    state = _state_with_page("AGENTS.md", "hello")

    assert check_missing(state, vault) == []


# ---------------------------------------------------------------------------
# check_primitive_missing
# ---------------------------------------------------------------------------


def test_primitive_missing_when_catalog_lacks_recorded_install(tmp_path: Path) -> None:
    kit = tmp_path / "kit"
    (kit / "core").mkdir(parents=True)
    (kit / "core" / "primitive.yaml").write_text(
        "name: core\nkind: infrastructure\nversion: 0.1.0\ndescription: Core primitive.\n",
        encoding="utf-8",
    )

    state = VaultState(installed_primitives={"core": "0.1.0", "ghost": "0.1.0"})

    assert check_primitive_missing(state, kit) == [Issue("primitive-missing", "ghost")]


def test_primitive_missing_silent_when_catalog_carries_everything(tmp_path: Path) -> None:
    kit = tmp_path / "kit"
    (kit / "core").mkdir(parents=True)
    (kit / "core" / "primitive.yaml").write_text(
        "name: core\nkind: infrastructure\nversion: 0.1.0\ndescription: Core primitive.\n",
        encoding="utf-8",
    )

    state = VaultState(installed_primitives={"core": "0.1.0"})

    assert check_primitive_missing(state, kit) == []


# ---------------------------------------------------------------------------
# check_stale_lock (journal-locking spec, plan step 6)
# ---------------------------------------------------------------------------
#
# The pure-function contract is tested directly against synthetic
# ``VaultState`` values (no journal file, no env var). The end-to-end
# wiring — env-var read, ``read_events_lenient``, ``replay_state``, sort —
# is pinned by ``test_doctor_reports_stale_lock_after_threshold_via_run_doctor``
# below and by the integration test in ``tests/integration/test_wiki_doctor.py``.


def _state_with_held_lock(by: str, acquired_at: datetime, reason: str | None = None) -> VaultState:
    return VaultState(held_lock=HeldLock(by=by, acquired_at=acquired_at, reason=reason))


def test_check_stale_lock_returns_issue_when_acquired_at_older_than_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held lock older than ``threshold_hours`` emits one stale-lock issue.

    Detail carries the acquire-time ISO so the user can decide whether
    to ``--force`` the release (spec §Doctor outputs).
    """

    fixed_now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(doctor, "_now", lambda: fixed_now)

    acquired_at = fixed_now - timedelta(hours=2)
    state = _state_with_held_lock("weekly-digest", acquired_at, "2026-W20 digest")

    issues = check_stale_lock(state, threshold_hours=1)
    assert issues == [Issue("stale-lock", "weekly-digest", f"acquired {acquired_at.isoformat()}")]


def test_check_stale_lock_returns_empty_when_within_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recent held lock is the in-progress case, not stale."""

    fixed_now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(doctor, "_now", lambda: fixed_now)

    state = _state_with_held_lock("weekly-digest", fixed_now - timedelta(hours=23))
    assert check_stale_lock(state, threshold_hours=24) == []


def test_check_stale_lock_returns_empty_when_held_lock_is_none() -> None:
    """A cleared ``state.held_lock`` produces no issue regardless of age.

    ``replay_state`` clears ``held_lock`` on any ``LockReleasedEvent``
    (``test_replay_state_release_clears_holder_even_when_by_differs``);
    this test pins that the stale-lock check trusts that projection.
    """

    assert check_stale_lock(VaultState(), threshold_hours=1) == []


def test_check_stale_lock_coerces_naive_acquired_at_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tz-naive ``HeldLock.acquired_at`` is coerced to UTC before the age subtraction.

    The kit's own writers emit tz-aware timestamps, but a hand-edited
    or externally produced journal line may carry a naive one. Without
    coercion, ``_now() - acquired_at`` raises ``TypeError`` and crashes
    the whole doctor pass — defeating the lenient-read path that was
    added in this PR specifically to keep doctor running on damaged
    journals.
    """

    fixed_now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(doctor, "_now", lambda: fixed_now)

    naive_acquire = datetime(2026, 5, 14, 12, 0, 0)  # 48h ago, no tzinfo
    state = _state_with_held_lock("weekly-digest", naive_acquire)

    issues = check_stale_lock(state, threshold_hours=1)
    assert len(issues) == 1
    assert issues[0].path == "weekly-digest"
    # The ISO detail carries the coerced (now-aware) acquire timestamp.
    assert "+00:00" in issues[0].detail


def test_doctor_reports_stale_lock_after_threshold_via_run_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end wiring check: env-var read + journal replay + check_stale_lock + sort.

    The pure-function contract is tested above against synthetic
    ``VaultState`` values. This test exists to catch a wiring
    regression — e.g. a future refactor that stops piping
    ``_stale_threshold_hours()`` through to ``check_stale_lock``, or
    forgets to call the check at all.
    """

    monkeypatch.setenv("WIKI_LOCK_STALE_HOURS", "1")
    fixed_now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(doctor, "_now", lambda: fixed_now)

    vault = _vault(tmp_path)
    journal = vault / ".wiki.journal" / "journal.jsonl"
    append_event(
        journal, VaultInitEvent(timestamp=NOW, by="wiki-init", vault_name="v", recipe="family")
    )
    acquired_at = fixed_now - timedelta(hours=2)
    append_event(
        journal,
        LockAcquiredEvent(timestamp=acquired_at, by="weekly-digest", reason="2026-W20 digest"),
    )
    kit = tmp_path / "kit"
    kit.mkdir()

    issues = run_doctor(vault, kit)
    stale = [i for i in issues if i.kind == "stale-lock"]
    assert len(stale) == 1
    assert stale[0].path == "weekly-digest"
    assert acquired_at.isoformat() in stale[0].detail


def test_doctor_warns_and_falls_back_when_env_var_unparseable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``WIKI_LOCK_STALE_HOURS=abc`` emits a stderr warning and uses the default.

    Silent fall-back was the original anti-pattern — a user who typoed
    the value would get the 24-hour default with no signal that their
    config was ignored. The warning surfaces once, then doctor proceeds
    normally so a mistyped env var never blocks the diagnostic command.
    """

    monkeypatch.setenv("WIKI_LOCK_STALE_HOURS", "not-a-number")
    fixed_now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(doctor, "_now", lambda: fixed_now)

    # Acquire just over the default threshold so the "fell back" branch
    # is observable in the issue list, not just in stderr.
    vault = _vault(tmp_path)
    journal = vault / ".wiki.journal" / "journal.jsonl"
    append_event(
        journal, VaultInitEvent(timestamp=NOW, by="wiki-init", vault_name="v", recipe="family")
    )
    acquired_at = fixed_now - timedelta(hours=_DEFAULT_STALE_HOURS + 1)
    append_event(journal, LockAcquiredEvent(timestamp=acquired_at, by="weekly-digest"))
    kit = tmp_path / "kit"
    kit.mkdir()

    capsys.readouterr()
    issues = run_doctor(vault, kit)
    err = capsys.readouterr().err

    assert "WIKI_LOCK_STALE_HOURS" in err
    # Import the constant rather than literal-matching so a future
    # default change updates the test by one symbol, not by editing
    # string assertions across the suite.
    assert f"default {_DEFAULT_STALE_HOURS}" in err
    assert any(i.kind == "stale-lock" for i in issues)


# ---------------------------------------------------------------------------
# run_doctor
# ---------------------------------------------------------------------------


def test_run_doctor_returns_sorted_issues(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    journal = vault / ".wiki.journal" / "journal.jsonl"
    # A barely-valid vault: init + core install, plus one journaled
    # write under ``skills/`` so that directory becomes kit-owned
    # territory (retro-review qC10 + C6 derive ownership from
    # ``state.page_writes``).
    append_event(
        journal,
        VaultInitEvent(timestamp=NOW, by="wiki-init", vault_name="v", recipe="family"),
    )
    append_event(
        journal,
        PrimitiveInstallEvent(timestamp=NOW, by="wiki-init", primitive="core", version="0.1.0"),
    )
    (vault / "skills").mkdir()
    (vault / "skills" / "known.md").write_text("k", encoding="utf-8")
    append_event(
        journal,
        PageWriteEvent(timestamp=NOW, by="core", path="skills/known.md", hash=_hash("k")),
    )

    # Add a stray under skills/ so the orphan check fires.
    (vault / "skills" / "stray.md").write_text("stray", encoding="utf-8")

    # And an empty kit so the primitive-missing check fires for ``core``.
    kit = tmp_path / "kit"
    kit.mkdir()

    issues = run_doctor(vault, kit)
    kinds = [issue.kind for issue in issues]
    # Sorted: orphan, primitive-missing (alphabetical on kind).
    assert kinds == sorted(kinds)
    assert Issue("orphan", "skills/stray.md") in issues
    assert Issue("primitive-missing", "core") in issues
