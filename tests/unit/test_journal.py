"""Tests for ``llm_wiki_kit.journal``.

ADR-0002 names the contract: an append-only JSONL file is the source of truth
for vault state, ``append_event`` validates and appends one line at a time,
``read_events`` parses every line through the discriminated ``Event`` union
and raises ``JournalCorruptError(line=N)`` on the first malformed line, and
``replay_state`` derives a ``VaultState`` from an ordered iterable of events.

These tests pin those four behaviors plus the ADR's acceptance criterion of
replaying 1000 events in under 100ms.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import multiprocessing
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from llm_wiki_kit.errors import JournalCorruptError
from llm_wiki_kit.journal import (
    Corruption,
    JournalReader,
    LockUnavailableError,
    append_event,
    read_events,
    read_events_lenient,
    replay_state,
    transaction,
    use_journal_cache,
)
from llm_wiki_kit.models import (
    ConfigSetEvent,
    Event,
    HeldLock,
    IngestRoutedEvent,
    LintRunEvent,
    LockAcquiredEvent,
    LockReleasedEvent,
    ManagedRegionWriteEvent,
    OperationRunEvent,
    PageConflictResolvedEvent,
    PageProposalEvent,
    PageWriteEvent,
    PrimitiveInstallEvent,
    PrimitiveRemoveEvent,
    PrimitiveUpgradeEvent,
    ResearchQueryEvent,
    SourceIngestEvent,
    VaultInitEvent,
)

NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return NOW + timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _reset_journal_module_state() -> Iterator[None]:
    """Reset ``_PERSISTED_FDS`` and ``_LOCK_FALLBACK_WARNED`` around every test.

    Two module-level mutable sets / dicts in ``llm_wiki_kit.journal``
    survive across tests: the persist-fd stash (populated by
    ``transaction(persist=True)`` clean exit; never popped except by
    ``_release_persisted_fd`` or process exit) and the fallback-warn-
    once registry (populated on the unsupported-flock path). Without
    teardown they'd accumulate entries and could mask test failures —
    a future test reusing a journal path would skip the warning, or
    hit the new ``persist=True`` re-entry guard for the wrong reason.
    The fixture pops the entries and closes any leaked fds.
    """

    import llm_wiki_kit.journal as _journal

    yield

    for fh in list(_journal._PERSISTED_FDS.values()):
        try:
            fh.close()
        except OSError:
            pass
    _journal._PERSISTED_FDS.clear()
    _journal._LOCK_FALLBACK_WARNED.clear()


# ---------------------------------------------------------------------------
# Multiprocessing workers for the flock-around-append_event tests (plan step 3)
#
# Workers must live at module top level so ``multiprocessing`` with the
# ``spawn`` start method can pickle and re-import them in the child process.
# ``spawn`` is the macOS default and the safer cross-platform choice — ``fork``
# would copy pytest's monkeypatched state into the child, which is exactly the
# kind of cross-process leakage these tests are meant to falsify.
# ---------------------------------------------------------------------------


def _appender_worker(journal_str: str, by: str, count: int) -> None:
    """Append ``count`` distinguishable ``VaultInitEvent``s as one subprocess."""

    from llm_wiki_kit.journal import append_event as _append_event
    from llm_wiki_kit.models import VaultInitEvent as _VaultInitEvent

    journal = Path(journal_str)
    for i in range(count):
        _append_event(
            journal,
            _VaultInitEvent(
                timestamp=NOW,
                by=by,
                vault_name=f"{by}-{i:03d}",
                recipe="family",
            ),
        )


def _flock_holder_worker(
    journal_str: str,
    ready_event: Any,
    release_event: Any,
) -> None:
    """Open the journal, take ``LOCK_EX``, signal ready, wait for release.

    Mirrors ``append_event``'s open/flock pattern so a second process's
    ``append_event`` is forced to block in the kernel until this fd closes.
    The lock is released implicitly at the ``with`` block exit; we never
    write to the journal here, so the file's line count is governed entirely
    by what the other process does.

    Note: ``open("a", encoding="utf-8")`` mirrors ``append_event`` exactly
    on purpose. Do not "simplify" to ``open(journal, "rb")`` — the lock
    semantics are tied to the open mode and the test power depends on the
    holder taking the same kind of fd ``append_event`` will try to take.
    """

    journal = Path(journal_str)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        ready_event.set()
        # 30s is well over any plausible test scheduling delay; if the
        # release signal never arrives the test has already failed elsewhere
        # and we just want the holder to exit cleanly so pytest can collect
        # the report rather than hang.
        release_event.wait(timeout=30)


def _blocked_appender_worker(
    journal_str: str,
    started_event: Any,
    done_event: Any,
) -> None:
    """Signal ``started``, call ``append_event``, signal ``done``."""

    from llm_wiki_kit.journal import append_event as _append_event
    from llm_wiki_kit.models import VaultInitEvent as _VaultInitEvent

    started_event.set()
    _append_event(
        Path(journal_str),
        _VaultInitEvent(
            timestamp=NOW,
            by="blocked-appender",
            vault_name="home",
            recipe="family",
        ),
    )
    done_event.set()


def _transaction_persist_holder_worker(
    journal_str: str,
    ready_event: Any,
    release_event: Any,
) -> None:
    """Hold the journal lock via ``transaction(persist=True)`` until released.

    The ``persist=True`` plumbing (plan step 4) keeps the OS-level lock
    alive across the context manager's ``__exit__`` so a subsequent CLI
    invocation (step 5) can return control to the shell with the lock
    still held. From a test fixture's perspective that means: the lock is
    taken on enter and released when *this worker process* exits, not
    when the ``with`` block ends. That makes ``transaction(persist=True)``
    swappable with the raw-``flock`` standalone holder in
    ``test_append_event_blocks_when_another_process_holds_lock`` — the
    second holder shape this PR back-fills per plan step 3 §Verification.
    """

    from llm_wiki_kit.journal import transaction as _transaction

    journal = Path(journal_str)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with _transaction(journal, by="persist-holder", reason="fixture", persist=True):
        ready_event.set()
        # Matches the raw-flock holder's 30s ceiling. If the release
        # signal never arrives the test has already failed elsewhere; we
        # just want the holder to exit cleanly so pytest can collect the
        # report rather than hang.
        release_event.wait(timeout=30)
    # ``persist=True`` clean exit leaves the fd open + lock held. The
    # lock auto-releases when this process exits — which happens as soon
    # as this function returns.


def _persist_post_exit_holder_worker(
    journal_str: str,
    post_exit_event: Any,
    release_event: Any,
) -> None:
    """Enter+exit a ``transaction(persist=True)`` block, then wait — lock must survive.

    Discriminating fixture for the Blocker the adversarial review caught:
    if ``persist=True`` leaks the fd at generator-frame teardown, the
    OS-level lock dies the instant ``__exit__`` runs, and the spec
    promise ("future flock attempts block until release runs") is empty.
    The worker enters the ``with`` block, exits it normally, signals
    ``post_exit``, and only then waits on ``release`` — so the main
    process's ``LOCK_EX | LOCK_NB`` probe is timed strictly *after*
    ``__exit__`` returned.
    """

    from llm_wiki_kit.journal import transaction as _transaction

    journal = Path(journal_str)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with _transaction(journal, by="post-exit-holder", reason="probe", persist=True):
        pass
    # ``__exit__`` has run. If persist=True is doing its job the fd is
    # still open at module level and the OS lock is still held.
    post_exit_event.set()
    release_event.wait(timeout=30)


# ---------------------------------------------------------------------------
# append_event
# ---------------------------------------------------------------------------


def test_append_event_creates_file_and_parent_dir(tmp_path: Path) -> None:
    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    assert not journal.exists()
    append_event(
        journal,
        VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family"),
    )
    assert journal.exists()
    assert journal.parent.is_dir()


def test_append_event_appends_one_json_line_with_trailing_newline(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    append_event(
        journal,
        PrimitiveInstallEvent(
            timestamp=NOW, by="recipe:family", primitive="people", version="0.1.0"
        ),
    )
    text = journal.read_text()
    assert text.endswith("\n")
    assert text.count("\n") == 1
    parsed = json.loads(text)
    assert parsed["type"] == "primitive.install"
    assert parsed["primitive"] == "people"


def test_append_event_accumulates_multiple_lines_in_order(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    append_event(
        journal, VaultInitEvent(timestamp=_at(0), by="core", vault_name="home", recipe="family")
    )
    append_event(
        journal,
        PrimitiveInstallEvent(timestamp=_at(1), by="core", primitive="core", version="0.1.0"),
    )
    append_event(
        journal,
        PrimitiveInstallEvent(timestamp=_at(2), by="core", primitive="people", version="0.1.0"),
    )

    lines = journal.read_text().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["type"] == "vault.init"
    assert json.loads(lines[1])["primitive"] == "core"
    assert json.loads(lines[2])["primitive"] == "people"


def test_append_event_fsyncs_before_returning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Spec invariant (docs/specs/journal-locking/spec.md §Durability, qB1):
    # the line is durable on disk before ``append_event`` returns. The
    # test intercepts ``os.fsync`` and, from inside the interceptor,
    # confirms (a) the write+flush has already propagated — a separate
    # reader can see the line — and (b) the counter ticks exactly once.
    # The order assertion is what makes this a contract test rather than
    # a "fsync was called" mock-shape check.
    journal = tmp_path / "journal.jsonl"
    line_type_at_fsync: list[str | None] = []
    real_fsync = os.fsync

    def counting_fsync(fd: int) -> None:
        # ``fh.flush()`` already ran; a separate reader can see the
        # line via the page cache without waiting for the kernel commit.
        if journal.exists():
            text = journal.read_text()
            line_type_at_fsync.append(json.loads(text.splitlines()[0])["type"] if text else None)
        else:
            line_type_at_fsync.append(None)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)
    append_event(
        journal,
        VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family"),
    )
    assert len(line_type_at_fsync) == 1, (
        f"expected exactly one fsync, got {len(line_type_at_fsync)}"
    )
    assert line_type_at_fsync == ["vault.init"], "fsync ran before the line was on disk"


def test_append_event_fsync_fileno_is_journal_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Plan acceptance criterion: the call passes the journal fd
    # specifically, not an unrelated fd. ``fstat(fd).st_ino`` is a
    # same-inode proof — sufficient today because ``append_event`` opens
    # exactly one handle per call; revisit when step 4's ``transaction``
    # introduces fd reuse via a ContextVar and a second handle on the
    # same file becomes plausible.
    journal = tmp_path / "journal.jsonl"
    captured_inodes: list[int] = []
    real_fsync = os.fsync

    def capturing_fsync(fd: int) -> None:
        captured_inodes.append(os.fstat(fd).st_ino)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", capturing_fsync)
    append_event(
        journal,
        VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family"),
    )
    assert captured_inodes == [os.stat(journal).st_ino]


def test_append_event_propagates_oserror_on_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Spec §Error cases (docs/specs/journal-locking/spec.md):
    # "``fsync`` failure (EIO) propagates as ``OSError``. Caller's
    # ``WikiError`` handler catches ``WikiError``, not ``OSError``; the
    # traceback surfaces — disk errors are not user-fixable through the
    # CLI." This test pins that contract: a future ``except OSError``
    # silently swallowing the failure would otherwise pass green.
    journal = tmp_path / "journal.jsonl"

    def failing_fsync(fd: int) -> None:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError) as excinfo:
        append_event(
            journal,
            VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family"),
        )
    assert excinfo.value.errno == errno.EIO
    # The write+flush ran before fsync; the bytes are kernel-side even
    # though fsync failed. The spec's "last successful line is fully
    # durable" claim is about *previous* events (each fsync'd before
    # returning); this event's durability is what failed.
    assert journal.exists()
    assert "vault.init" in journal.read_text()


def test_append_event_round_trips_through_read_events(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    events: list[Event] = [
        VaultInitEvent(timestamp=_at(0), by="core", vault_name="home", recipe="family"),
        PrimitiveInstallEvent(timestamp=_at(1), by="core", primitive="meeting", version="0.1.0"),
        PageWriteEvent(
            timestamp=_at(2), by="meeting", path="meetings/2026-05-15.md", hash="a" * 64
        ),
    ]
    for e in events:
        append_event(journal, e)

    loaded = read_events(journal)
    assert loaded == events


# ---------------------------------------------------------------------------
# read_events
# ---------------------------------------------------------------------------


def test_read_events_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert read_events(tmp_path / "absent.jsonl") == []


def test_read_events_returns_empty_when_file_empty(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.write_text("")
    assert read_events(journal) == []


def test_read_events_skips_blank_lines(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    append_event(
        journal, VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family")
    )
    # Trailing blank line is normal for an append-only file.
    with journal.open("a") as fh:
        fh.write("\n")
    events = read_events(journal)
    assert len(events) == 1
    assert isinstance(events[0], VaultInitEvent)


def test_read_events_raises_on_malformed_json_with_line_number(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    append_event(
        journal, VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family")
    )
    with journal.open("a") as fh:
        fh.write("{not json\n")
        fh.write(
            '{"type": "page.write", "timestamp": "2026-05-15T12:00:00+00:00",'
            ' "by": "x", "path": "p", "hash": "a"}\n'
        )

    with pytest.raises(JournalCorruptError) as excinfo:
        read_events(journal)
    assert excinfo.value.line == 2


def test_read_events_raises_on_unknown_event_type_with_line_number(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    append_event(
        journal, VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family")
    )
    append_event(
        journal, PrimitiveInstallEvent(timestamp=NOW, by="core", primitive="core", version="0.1.0")
    )
    with journal.open("a") as fh:
        fh.write('{"type": "made.up", "timestamp": "2026-05-15T12:00:00+00:00", "by": "core"}\n')

    with pytest.raises(JournalCorruptError) as excinfo:
        read_events(journal)
    assert excinfo.value.line == 3


def test_read_events_raises_on_missing_required_field(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    with journal.open("w") as fh:
        # page.write missing required `hash`
        fh.write(
            '{"type": "page.write", "timestamp": "2026-05-15T12:00:00+00:00",'
            ' "by": "x", "path": "p"}\n'
        )

    with pytest.raises(JournalCorruptError) as excinfo:
        read_events(journal)
    assert excinfo.value.line == 1


# ---------------------------------------------------------------------------
# replay_state
# ---------------------------------------------------------------------------


def test_replay_empty_returns_default_vault_state() -> None:
    state = replay_state([])
    assert state.vault_name is None
    assert state.recipe is None
    assert state.installed_primitives == {}
    assert state.page_writes == {}
    assert state.pending_proposals == {}
    assert state.ingested_sources == {}
    assert state.recent_operations == {}
    assert state.recent_research == []


def test_replay_vault_init_sets_name_and_recipe() -> None:
    state = replay_state(
        [VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family")]
    )
    assert state.vault_name == "home"
    assert state.recipe == "family"


def test_replay_primitive_install_adds_to_installed() -> None:
    state = replay_state(
        [
            PrimitiveInstallEvent(timestamp=_at(0), by="core", primitive="core", version="0.1.0"),
            PrimitiveInstallEvent(timestamp=_at(1), by="core", primitive="people", version="0.2.0"),
        ]
    )
    assert state.installed_primitives == {"core": "0.1.0", "people": "0.2.0"}


def test_replay_primitive_upgrade_changes_version() -> None:
    state = replay_state(
        [
            PrimitiveInstallEvent(timestamp=_at(0), by="core", primitive="people", version="0.1.0"),
            PrimitiveUpgradeEvent(
                timestamp=_at(1),
                by="core",
                primitive="people",
                from_version="0.1.0",
                to_version="0.2.0",
            ),
        ]
    )
    assert state.installed_primitives == {"people": "0.2.0"}


def test_replay_primitive_remove_drops_it() -> None:
    state = replay_state(
        [
            PrimitiveInstallEvent(timestamp=_at(0), by="core", primitive="people", version="0.1.0"),
            PrimitiveRemoveEvent(timestamp=_at(1), by="core", primitive="people"),
        ]
    )
    assert state.installed_primitives == {}


def test_replay_page_write_tracks_most_recent_per_path() -> None:
    earlier = PageWriteEvent(timestamp=_at(0), by="meeting", path="p.md", hash="a" * 64)
    later = PageWriteEvent(timestamp=_at(1), by="meeting", path="p.md", hash="b" * 64)
    state = replay_state([earlier, later])
    assert state.page_writes == {"p.md": later}


def test_replay_page_proposal_records_pending() -> None:
    proposal = PageProposalEvent(
        timestamp=_at(0),
        by="meeting",
        path="p.md",
        proposed_path="p.md.proposed",
        hash="a" * 64,
    )
    state = replay_state([proposal])
    assert state.pending_proposals == {"p.md": proposal}


def test_replay_page_write_clears_matching_pending_proposal() -> None:
    proposal = PageProposalEvent(
        timestamp=_at(0),
        by="meeting",
        path="p.md",
        proposed_path="p.md.proposed",
        hash="a" * 64,
    )
    resolved = PageWriteEvent(timestamp=_at(1), by="meeting", path="p.md", hash="b" * 64)
    state = replay_state([proposal, resolved])
    assert state.pending_proposals == {}
    assert state.page_writes == {"p.md": resolved}


def test_replay_conflict_resolved_clears_pending_proposal() -> None:
    proposal = PageProposalEvent(
        timestamp=_at(0),
        by="meeting",
        path="p.md",
        proposed_path="p.md.proposed",
        hash="a" * 64,
    )
    resolved = PageConflictResolvedEvent(timestamp=_at(1), by="user", path="p.md", hash="c" * 64)
    state = replay_state([proposal, resolved])
    assert state.pending_proposals == {}


def test_replay_source_ingest_indexes_by_source() -> None:
    ingest = SourceIngestEvent(
        timestamp=NOW,
        by="meeting",
        source="/tmp/t.txt",
        source_hash="h" * 64,
        content_type="meeting",
    )
    state = replay_state([ingest])
    assert state.ingested_sources == {"/tmp/t.txt": ingest}


def test_replay_operation_run_keeps_most_recent_per_operation() -> None:
    first = OperationRunEvent(
        timestamp=_at(0), by="core", operation="weekly-digest", status="dispatched"
    )
    second = OperationRunEvent(
        timestamp=_at(1), by="core", operation="weekly-digest", status="dispatched"
    )
    state = replay_state([first, second])
    assert state.recent_operations == {"weekly-digest": second}


def test_replay_research_query_accumulates_in_order() -> None:
    q1 = ResearchQueryEvent(timestamp=_at(0), by="user", query="a", provider="perplexity")
    q2 = ResearchQueryEvent(timestamp=_at(1), by="user", query="b", provider="gemini")
    state = replay_state([q1, q2])
    assert state.recent_research == [q1, q2]


def test_replay_ignores_events_that_dont_affect_state() -> None:
    state = replay_state(
        [
            ManagedRegionWriteEvent(
                timestamp=NOW, by="core", file="AGENTS.md", region="x", content_hash="a" * 64
            ),
            LintRunEvent(timestamp=NOW, by="core", status="ok"),
            ConfigSetEvent(timestamp=NOW, by="user", key="k", value="v"),
        ]
    )
    # No crash, no state contribution.
    assert state.installed_primitives == {}
    assert state.page_writes == {}


def test_ingest_routed_event_round_trips_through_journal(tmp_path: Path) -> None:
    """``IngestRoutedEvent`` survives append → read → replay unchanged (qC11).

    ``replay_state`` deliberately ignores ``IngestRoutedEvent`` today —
    the future ``journal explain`` is its consumer (Phase D). This pin
    test exists so a maintainer who notices "this field has no
    consumer" can't quietly drop the schema without the test going red.
    Round-tripping the field through the discriminated-union read path
    is enough to prove the journal still carries every field the
    routing event declares.
    """

    journal = tmp_path / "journal.jsonl"
    event = IngestRoutedEvent(
        timestamp=_at(0),
        by="wiki-ingest",
        source="https://allrecipes.com/recipe/12345/",
        content_type="recipe",
        candidates=["recipe"],
        via="auto",
        signals=["url_domains:allrecipes.com", "url_path_patterns:/recipe/*"],
    )
    append_event(journal, event)

    loaded = read_events(journal)
    assert loaded == [event]
    # Field-by-field guard so a silent schema narrowing (e.g. dropping
    # ``signals``) fails this test even if equality on the union stays
    # green by coincidence.
    routed = loaded[0]
    assert isinstance(routed, IngestRoutedEvent)
    assert routed.source == event.source
    assert routed.content_type == event.content_type
    assert routed.candidates == event.candidates
    assert routed.via == event.via
    assert routed.signals == event.signals

    # Replay still ignores the event (documented in journal.py
    # replay_state); the round trip is the contract, not derived state.
    state = replay_state(loaded)
    assert state.ingested_sources == {}
    assert state.page_writes == {}


# ---------------------------------------------------------------------------
# Lock event replay (journal-locking spec, plan step 1)
# ---------------------------------------------------------------------------


def test_replay_state_tracks_held_lock() -> None:
    """``LockAcquiredEvent`` snapshots the holder; ``LockReleasedEvent`` clears it."""

    acquired = LockAcquiredEvent(
        timestamp=_at(0),
        by="weekly-digest",
        reason="2026-W20 digest",
    )
    state = replay_state([acquired])
    assert state.held_lock == HeldLock(
        by="weekly-digest",
        acquired_at=_at(0),
        reason="2026-W20 digest",
    )

    released = LockReleasedEvent(timestamp=_at(1), by="weekly-digest")
    state = replay_state([acquired, released])
    assert state.held_lock is None


def test_replay_state_last_acquire_wins_when_release_is_missing() -> None:
    """Two ``LockAcquiredEvent``s without a release: replay records the latest holder.

    The stale-lock detection (spec step 6) catches the missing release;
    replay itself is permissive so a hand-edited journal doesn't make the
    kit unrunnable.
    """

    first = LockAcquiredEvent(timestamp=_at(0), by="weekly-digest")
    second = LockAcquiredEvent(timestamp=_at(1), by="bulk-ingest", reason="inbox")
    state = replay_state([first, second])
    assert state.held_lock is not None
    assert state.held_lock.by == "bulk-ingest"
    assert state.held_lock.reason == "inbox"


def test_replay_state_release_without_prior_acquire_keeps_lock_none() -> None:
    """A ``LockReleasedEvent`` against an unheld lock is harmless (matches SKILL.md)."""

    state = replay_state([LockReleasedEvent(timestamp=_at(0), by="weekly-digest")])
    assert state.held_lock is None


def test_replay_state_release_clears_holder_even_when_by_differs() -> None:
    """Mismatched-``by`` release clears the holder unconditionally.

    Pins the contract the spec's stale-lock-reclaim path (Edge cases)
    and the CLI's ``release --force`` flag (step 5) both depend on:
    replay treats every ``LockReleasedEvent`` as a clear, regardless of
    who held the lock. Step 4's ``transaction()`` and step 6's doctor
    will lean on this rule; pinning it now means rediscovery doesn't
    surface as a regression later.
    """

    acquired = LockAcquiredEvent(timestamp=_at(0), by="weekly-digest")
    released_by_other = LockReleasedEvent(timestamp=_at(1), by="wiki-doctor")
    state = replay_state([acquired, released_by_other])
    assert state.held_lock is None


def test_held_lock_acquired_at_is_the_acquire_events_timestamp() -> None:
    """``HeldLock.acquired_at`` carries the acquire event's wall-clock timestamp.

    Step 6's stale-lock check compares ``acquired_at`` against
    ``datetime.now() - WIKI_LOCK_STALE_HOURS``; pin the source-of-truth
    here so a refactor that re-derives ``acquired_at`` from "time replay
    ran" silently breaks the stale-lock semantics.
    """

    acquire_at = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    state = replay_state([LockAcquiredEvent(timestamp=acquire_at, by="weekly-digest")])
    assert state.held_lock is not None
    assert state.held_lock.acquired_at == acquire_at


def test_old_journal_without_lock_events_replays_cleanly(tmp_path: Path) -> None:
    """A journal written before this spec lands replays without raising.

    Acceptance criterion from journal-locking spec §"Schema evolution":
    additive schema changes must leave old journals readable.
    """

    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        '{"type":"vault.init","timestamp":"2026-05-01T00:00:00Z","by":"wiki-init",'
        '"vault_name":"home","recipe":"family","schema_version":1}\n'
        '{"type":"primitive.install","timestamp":"2026-05-01T00:00:00Z","by":"wiki-init",'
        '"primitive":"core","version":"0.1.0"}\n'
        '{"type":"page.write","timestamp":"2026-05-01T00:00:01Z","by":"core",'
        '"path":"AGENTS.md","hash":"' + "a" * 64 + '"}\n',
        encoding="utf-8",
    )
    events = read_events(journal)
    state = replay_state(events)
    assert state.held_lock is None
    assert state.vault_name == "home"
    assert state.installed_primitives == {"core": "0.1.0"}
    assert "AGENTS.md" in state.page_writes


# ---------------------------------------------------------------------------
# read_events_lenient (journal-locking spec, plan step 6)
# ---------------------------------------------------------------------------


def test_read_events_lenient_returns_none_corruption_on_clean_journal(tmp_path: Path) -> None:
    """A well-formed journal: lenient returns the same events as strict, corruption is None.

    The §Risks resolution in ``plan.md`` keeps ``read_events(path) -> list[Event]``
    strict and adds ``read_events_lenient`` as a separate function returning
    ``(events, Corruption | None)``. This test pins the "no corruption" branch:
    same parse output as strict, plus an explicit ``None`` sentinel.
    """

    journal = tmp_path / "journal.jsonl"
    append_event(
        journal, VaultInitEvent(timestamp=_at(0), by="core", vault_name="home", recipe="family")
    )
    append_event(
        journal,
        PrimitiveInstallEvent(timestamp=_at(1), by="core", primitive="core", version="0.1.0"),
    )

    events, corruption = read_events_lenient(journal)
    assert corruption is None
    assert events == read_events(journal)


def test_read_events_lenient_returns_partial_events_and_corruption_at_bad_line(
    tmp_path: Path,
) -> None:
    """A malformed line: lenient returns the valid prefix and a ``Corruption`` row.

    Doctor (plan step 6) consumes this shape: it surfaces the corruption as
    a ``journal-corrupt`` issue and runs the rest of its checks against the
    partial event list rather than crashing the whole pass. Strict
    ``read_events`` would raise ``JournalCorruptError`` at the same line.
    """

    journal = tmp_path / "journal.jsonl"
    append_event(
        journal, VaultInitEvent(timestamp=_at(0), by="core", vault_name="home", recipe="family")
    )
    append_event(
        journal,
        PrimitiveInstallEvent(timestamp=_at(1), by="core", primitive="core", version="0.1.0"),
    )
    with journal.open("a") as fh:
        fh.write("{not json\n")
    # A valid line after the bad one — proves lenient stops at the first bad
    # line rather than silently swallowing more corruption downstream.
    append_event(
        journal,
        PrimitiveInstallEvent(timestamp=_at(2), by="core", primitive="people", version="0.1.0"),
    )

    events, corruption = read_events_lenient(journal)
    assert corruption is not None
    assert corruption.line == 3
    assert "invalid JSON" in corruption.reason
    # Only the two pre-corruption events survive; the post-corruption line
    # is unread on purpose (the journal is append-only, so anything after
    # the bad line is in a state doctor can't trust).
    assert len(events) == 2
    assert isinstance(events[0], VaultInitEvent)
    assert isinstance(events[1], PrimitiveInstallEvent)


def test_corruption_is_frozen_dataclass() -> None:
    """``Corruption`` is a value, not an aggregate: immutable after construction.

    Pins the ``frozen=True`` invariant so a future refactor that
    promotes it to a mutable container (or removes the dataclass
    decorator) trips this test. Attribute storage itself is a
    ``@dataclass`` guarantee from the stdlib — no need to re-assert.
    """

    corruption = Corruption(line=42, reason="invalid JSON: Expecting value")
    with pytest.raises(AttributeError):
        corruption.line = 43  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Performance: ADR-0002 acceptance criterion
# ---------------------------------------------------------------------------


def test_replay_1000_events_under_100ms() -> None:
    events: list[Event] = [
        PageWriteEvent(
            timestamp=_at(i),
            by="meeting",
            path=f"pages/{i % 50}.md",
            hash=f"{i:064x}",
        )
        for i in range(1000)
    ]
    start = time.perf_counter()
    state = replay_state(events)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.1, f"replay of 1000 events took {elapsed * 1000:.1f}ms (budget: 100ms)"
    assert len(state.page_writes) == 50


# ---------------------------------------------------------------------------
# Mutual exclusion: fcntl.flock around append_event (journal-locking plan
# step 3 / spec §Mutual exclusion / qB2)
# ---------------------------------------------------------------------------


def test_append_event_takes_lock_ex_on_journal_fd_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``append_event`` calls ``fcntl.flock(LOCK_EX)`` on the journal fd
    before writing.

    Discriminating test for plan step 3: the load-style concurrent test
    above can pass even when locking is broken on macOS APFS with small
    lines (single-syscall atomic writes), and the standalone-holder test
    only asserts behavior under a *foreign* lock. A future refactor that
    skipped ``flock`` under some condition (e.g. step 4's planned
    ``ContextVar`` reuse) could regress per-call locking without either
    test failing. This counter-style probe pins "every ``append_event``
    call takes ``LOCK_EX`` on the journal fd it just opened" — the same
    pattern used by ``test_append_event_fsyncs_before_returning`` for
    fsync. Matched-inode check is the same proof shape as the fsync-fd
    test (``os.fstat(fd).st_ino``).
    """

    journal = tmp_path / "journal.jsonl"
    calls: list[tuple[int, int]] = []
    real_flock = fcntl.flock

    def capturing_flock(fd: int, operation: int) -> None:
        calls.append((os.fstat(fd).st_ino, operation))
        real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", capturing_flock)
    append_event(
        journal,
        VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family"),
    )

    journal_inode = os.stat(journal).st_ino
    assert calls == [(journal_inode, fcntl.LOCK_EX)], (
        f"expected exactly one flock(LOCK_EX) on the journal fd; got {calls}"
    )


def test_concurrent_append_does_not_interleave_lines(tmp_path: Path) -> None:
    """Two processes appending 100 events each produce 200 valid JSONL lines.

    Spec invariant (``docs/specs/journal-locking/spec.md`` §Mutual
    exclusion, qB2): two simultaneous ``append_event`` calls in different
    processes cannot interleave bytes within a single line. Order across
    processes is not asserted — only well-formedness, count, and that
    each process contributed its full 100.

    Note on test power: small JSONL lines on macOS/Linux APFS+ext4 happen
    to land in a single atomic ``os.write()`` syscall even without flock,
    so this test alone wouldn't catch a regression that dropped flock on
    this platform with these line sizes. It catches gross interleaving
    (multi-syscall lines, future longer events, looser-semantic
    filesystems) and pins the parses-to-200 invariant for the rest of
    the suite to rely on; the discriminating test for "flock is actually
    called" is ``test_append_event_blocks_when_another_process_holds_lock``
    below, which would fail under a no-op flock.
    """

    journal = tmp_path / "journal.jsonl"
    ctx = multiprocessing.get_context("spawn")
    p1 = ctx.Process(target=_appender_worker, args=(str(journal), "proc-a", 100))
    p2 = ctx.Process(target=_appender_worker, args=(str(journal), "proc-b", 100))
    p1.start()
    p2.start()
    p1.join(timeout=60)
    p2.join(timeout=60)
    assert p1.exitcode == 0, f"proc-a exited with {p1.exitcode}"
    assert p2.exitcode == 0, f"proc-b exited with {p2.exitcode}"

    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200, f"expected 200 lines, got {len(lines)}"
    counts = {"proc-a": 0, "proc-b": 0}
    for n, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"line {n} is not valid JSON (interleaved?): {exc}; line head={line[:80]!r}"
            )
        assert payload["type"] == "vault.init", f"line {n} unexpected type: {payload}"
        counts[payload["by"]] = counts.get(payload["by"], 0) + 1
    assert counts == {"proc-a": 100, "proc-b": 100}, f"per-process counts wrong: {counts}"


def test_append_event_blocks_when_another_process_holds_lock(tmp_path: Path) -> None:
    """``append_event`` returns only after a foreign ``LOCK_EX`` holder releases.

    Standalone-holder fixture (plan step 3 §Verification): a helper
    subprocess opens the journal, takes ``fcntl.flock(LOCK_EX)``, signals
    ``ready``, then waits on a ``multiprocessing.Event`` to release. A
    second subprocess calls ``append_event`` — it must remain blocked
    until the holder releases. We assert ordering through Events rather
    than a minimum-block wall-clock window (plan §Risks calls out the
    latter as CI-flaky): ``done`` must be unset before ``release`` fires
    and set within a generous timeout after.
    """

    journal = tmp_path / "journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    started = ctx.Event()
    done = ctx.Event()

    holder = ctx.Process(
        target=_flock_holder_worker,
        args=(str(journal), ready, release),
    )
    appender = ctx.Process(
        target=_blocked_appender_worker,
        args=(str(journal), started, done),
    )

    holder.start()
    try:
        try:
            assert ready.wait(timeout=10), "holder did not signal ready"
            appender.start()
            assert started.wait(timeout=10), "appender did not start"
            # The appender signaled ``started`` and is now inside
            # ``append_event``, blocked on ``fcntl.flock``. A 500ms grace
            # window is far more than enough for a JSON-line write + fsync
            # if the lock were broken; ``done`` firing in that window
            # means locking failed open. The check is generous on purpose
            # — we are not asserting a *minimum* block duration (the
            # flaky pattern), we are asserting that ``done`` never
            # precedes ``release``.
            assert not done.wait(timeout=0.5), (
                "appender completed before holder released its lock — flock is not blocking"
            )
            release.set()
            assert done.wait(timeout=10), "appender did not complete after holder released"
        finally:
            # Idempotent — guarantees the appender unblocks regardless of
            # which assert above failed, including assertions reached
            # before ``appender.start()`` (in which case ``pid`` is None
            # and there's nothing to join).
            release.set()
            if appender.pid is not None:
                appender.join(timeout=10)
                if appender.is_alive():
                    appender.kill()
                    appender.join(timeout=5)
        # After inner ``finally`` the appender has been joined; exitcode
        # is now meaningful. On any inner-try failure we never reach
        # here (the exception has already propagated through the inner
        # finally), so this only runs on the happy path.
        assert appender.exitcode == 0, f"appender exited with {appender.exitcode}"
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            # Wedged holder — kill so a subsequent test inheriting this
            # process tree doesn't see an orphaned flock holder.
            holder.kill()
            holder.join(timeout=5)

    # Sanity: the appender's line is the only line — the holder never wrote.
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["by"] == "blocked-appender"


def test_append_event_falls_back_when_flock_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``EOPNOTSUPP`` from ``fcntl.flock`` logs a warning and writes anyway.

    Spec §Edge cases ("NFS / iCloud Drive / SMB"): on a filesystem that
    rejects advisory locking the kit falls back to pre-spec behavior —
    no concurrent-writer protection, but the journal still gets written.
    Plan §Risks names this fallback explicitly and points at ADR-0002 as
    the contract the warning should cite, so a user reading logs has a
    pointer to "why locking is not in effect here".
    """

    journal = tmp_path / "journal.jsonl"

    calls: list[int] = []

    def failing_flock(fd: int, operation: int) -> None:
        calls.append(operation)
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    monkeypatch.setattr(fcntl, "flock", failing_flock)

    with caplog.at_level(logging.WARNING, logger="llm_wiki_kit.journal"):
        append_event(
            journal,
            VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family"),
        )

    assert calls, "fcntl.flock was not called"
    assert calls[0] == fcntl.LOCK_EX, f"expected LOCK_EX, got operation={calls[0]}"
    assert journal.exists()
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["type"] == "vault.init"

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ADR-0002" in msg for msg in warning_msgs), (
        f"expected a warning mentioning ADR-0002, got: {warning_msgs}"
    )


def test_append_event_warns_once_per_journal_path_on_unsupported_flock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Repeated unsupported-FS appends emit one warning, not one per event.

    Spec §Edge cases pins the once-per-path-per-process gate so a
    ``wiki run`` on iCloud doesn't spam the same paragraph dozens of
    times. Without the gate (and without this test) a future refactor
    that dropped the suppression would slip through both prior tests:
    ``test_append_event_falls_back_when_flock_unsupported`` only
    exercises the first call.
    """

    # ``_LOCK_FALLBACK_WARNED`` is module-global and not cleared between
    # tests by design — tmp_path gives a unique journal per test, so
    # cross-test contamination doesn't happen in practice. Still, reset
    # it here so the second-call assertion is unambiguous when the test
    # is run with ``--count`` or under ``pytest-repeat``.
    import llm_wiki_kit.journal as _journal_mod

    monkeypatch.setattr(_journal_mod, "_LOCK_FALLBACK_WARNED", set())

    journal = tmp_path / "journal.jsonl"

    def failing_flock(fd: int, operation: int) -> None:
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    monkeypatch.setattr(fcntl, "flock", failing_flock)

    with caplog.at_level(logging.WARNING, logger="llm_wiki_kit.journal"):
        for i in range(3):
            append_event(
                journal,
                VaultInitEvent(timestamp=NOW, by="core", vault_name=f"home-{i}", recipe="family"),
            )

    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, "all three events should still be written despite no locking"

    adr_warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "ADR-0002" in r.getMessage()
    ]
    assert len(adr_warnings) == 1, (
        f"expected exactly one ADR-0002 warning across three appends; got {len(adr_warnings)}"
    )


def test_append_event_fallback_warns_once_across_path_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Two ``Path`` spellings of the same on-disk journal collapse to one warning.

    Spec §Edge cases keys the once-per-path gate on the resolved path, not
    on ``Path``-object identity. A caller invoking ``append_event`` once
    via a symlinked directory and once via the real directory points at
    the same file with different ``Path`` instances; the suppression must
    collapse them. Without ``.resolve()`` keying the set, each spelling
    would hash to a different key and the warning would re-fire — exactly
    the per-event noise the gate exists to prevent.
    """

    import llm_wiki_kit.journal as _journal_mod

    monkeypatch.setattr(_journal_mod, "_LOCK_FALLBACK_WARNED", set())

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)

    journal_via_real = real_dir / "journal.jsonl"
    journal_via_link = link_dir / "journal.jsonl"
    assert journal_via_real != journal_via_link, "test setup: paths should not be == as objects"
    assert journal_via_real.resolve() == journal_via_link.resolve(), (
        "test setup: paths should resolve to the same file"
    )

    def failing_flock(fd: int, operation: int) -> None:
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    monkeypatch.setattr(fcntl, "flock", failing_flock)

    with caplog.at_level(logging.WARNING, logger="llm_wiki_kit.journal"):
        append_event(
            journal_via_real,
            VaultInitEvent(timestamp=NOW, by="core", vault_name="home-1", recipe="family"),
        )
        append_event(
            journal_via_link,
            VaultInitEvent(timestamp=NOW, by="core", vault_name="home-2", recipe="family"),
        )

    adr_warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "ADR-0002" in r.getMessage()
    ]
    assert len(adr_warnings) == 1, (
        f"expected one warning across two spellings of the same file; got {len(adr_warnings)}"
    )


def test_append_event_propagates_oserror_eintr_from_flock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``OSError(EINTR)`` from ``fcntl.flock`` propagates — not in the fallback set.

    PEP 475 auto-retries ``EINTR`` on CPython for native ``fcntl`` calls,
    so in production the caller never sees it. This test injects ``EINTR``
    through a monkeypatched ``fcntl.flock`` to pin the *userspace* boundary:
    a future refactor (e.g. step 4) that broadens the fallback errno set
    must not silently swallow ``EINTR`` as "filesystem unsupported".
    """

    journal = tmp_path / "journal.jsonl"

    def eintr_flock(fd: int, operation: int) -> None:
        raise OSError(errno.EINTR, "Interrupted system call")

    monkeypatch.setattr(fcntl, "flock", eintr_flock)

    with pytest.raises(OSError) as excinfo:
        append_event(
            journal,
            VaultInitEvent(timestamp=NOW, by="core", vault_name="home", recipe="family"),
        )
    assert excinfo.value.errno == errno.EINTR


# ---------------------------------------------------------------------------
# Transaction context manager (journal-locking plan step 4 / spec §Transaction)
# ---------------------------------------------------------------------------


def test_transaction_emits_lock_acquired_and_released_on_clean_exit(
    tmp_path: Path,
) -> None:
    """``transaction()`` brackets the body's events with lock.acquired/released.

    Spec §Behavior happy-path multi-event: a multi-event operation enters
    ``transaction``, the runner appends N events inside the block, and
    the manager emits a single ``LockAcquiredEvent`` before yielding and
    a single ``LockReleasedEvent`` on clean exit. The two events must
    bracket the body's events in journal order; otherwise replay can't
    derive "the lock was held while these N events were written."
    """

    journal = tmp_path / "journal.jsonl"
    with transaction(journal, by="weekly-digest", reason="2026-W20"):
        append_event(
            journal,
            PageWriteEvent(timestamp=_at(1), by="weekly-digest", path="digest.md", hash="a" * 64),
        )
        append_event(
            journal,
            PageWriteEvent(timestamp=_at(2), by="weekly-digest", path="index.md", hash="b" * 64),
        )

    events = read_events(journal)
    types = [type(e).__name__ for e in events]
    assert types == [
        "LockAcquiredEvent",
        "PageWriteEvent",
        "PageWriteEvent",
        "LockReleasedEvent",
    ], f"transaction did not bracket body events: {types}"
    acquired = events[0]
    released = events[-1]
    assert isinstance(acquired, LockAcquiredEvent)
    assert isinstance(released, LockReleasedEvent)
    assert acquired.by == "weekly-digest"
    assert acquired.reason == "2026-W20"
    assert released.by == "weekly-digest"


def test_transaction_emits_lock_released_on_exception(tmp_path: Path) -> None:
    """Body raises: ``LockReleasedEvent`` is still last; the exception re-raises.

    Spec §Invariants: ``LockAcquiredEvent`` always pairs with a
    ``LockReleasedEvent`` in the same process lifetime, except on a hard
    crash. A Python exception inside the block must not break the pair —
    the ``finally`` is the kit's contract. Pinning this here means a
    future refactor that swaps the ``finally`` for ``except`` (and
    silently swallows on success) trips the test.
    """

    journal = tmp_path / "journal.jsonl"
    boom = RuntimeError("body failed mid-sequence")

    with pytest.raises(RuntimeError) as excinfo:
        with transaction(journal, by="bulk-ingest", reason="inbox"):
            append_event(
                journal,
                PageWriteEvent(timestamp=_at(1), by="bulk-ingest", path="doc.md", hash="c" * 64),
            )
            raise boom

    assert excinfo.value is boom, "transaction must re-raise the original exception"

    events = read_events(journal)
    types = [type(e).__name__ for e in events]
    assert types == [
        "LockAcquiredEvent",
        "PageWriteEvent",
        "LockReleasedEvent",
    ], f"exception path did not emit terminating release: {types}"


def test_nested_append_event_reuses_held_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``append_event`` inside an open ``transaction()`` does not re-flock.

    Plan step 4 §Verification names the contract: the ContextVar-held fd
    short-circuits the open + flock per nested call so an N-event
    sequence takes ``LOCK_EX`` exactly once, not N+1 times. A
    monkeypatched ``fcntl.flock`` counter is the discriminating probe —
    a regression that drops the ContextVar check would land N additional
    locks here.
    """

    journal = tmp_path / "journal.jsonl"
    calls: list[int] = []
    real_flock = fcntl.flock

    def counting_flock(fd: int, operation: int) -> None:
        calls.append(operation)
        real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", counting_flock)

    with transaction(journal, by="runner"):
        for i in range(3):
            append_event(
                journal,
                PageWriteEvent(
                    timestamp=_at(i + 1),
                    by="runner",
                    path=f"p{i}.md",
                    hash=f"{i:064x}",
                ),
            )

    # "Exactly one flock per transaction, and it's LOCK_EX." Splitting the
    # invariant into two asserts catches the silent-regression case where
    # a refactor adds a LOCK_SH + LOCK_EX pair (count goes to 2; an
    # ``op == LOCK_EX`` filter would still see one and pass).
    assert len(calls) == 1, (
        f"expected exactly one flock call across a transaction with 3 nested "
        f"appends; got {len(calls)} (full ops: {calls})"
    )
    assert calls[0] == fcntl.LOCK_EX, f"expected LOCK_EX; got {calls[0]}"

    # Sanity: all five events landed (acquire + 3 writes + release).
    events = read_events(journal)
    assert len(events) == 5
    assert isinstance(events[0], LockAcquiredEvent)
    assert isinstance(events[-1], LockReleasedEvent)


def test_transaction_blocks_concurrent_append_via_persist_holder(
    tmp_path: Path,
) -> None:
    """A ``transaction(persist=True)`` holder blocks an outside ``append_event``.

    Plan step 3 §Verification anticipated this back-fill: step 3's
    blocked-appender test used a standalone raw-``flock`` holder because
    ``transaction`` did not yet exist; step 4 introduces it, and the
    contract is "the two holder shapes are interchangeable." If
    ``persist=True`` regresses to releasing the lock on ``__exit__`` (or
    never takes one), the appender returns before ``release`` fires and
    the assertion catches it.
    """

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    started = ctx.Event()
    done = ctx.Event()

    holder = ctx.Process(
        target=_transaction_persist_holder_worker,
        args=(str(journal), ready, release),
    )
    appender = ctx.Process(
        target=_blocked_appender_worker,
        args=(str(journal), started, done),
    )

    holder.start()
    try:
        try:
            assert ready.wait(timeout=10), "persist holder did not signal ready"
            # The persist holder has taken the lock AND written the holder
            # file. Both invariants matter for step 5's CLI; pin the
            # holder-file half here so a regression that drops the
            # holder-file write would also surface.
            holder_file = journal.parent / "lock"
            assert holder_file.exists(), (
                "transaction(persist=True) did not write .wiki.journal/lock"
            )

            appender.start()
            assert started.wait(timeout=10), "appender did not start"
            assert not done.wait(timeout=0.5), (
                "appender completed before holder released — transaction(persist=True) "
                "did not actually hold the lock past the with-block"
            )
            release.set()
            assert done.wait(timeout=10), "appender did not complete after holder released"
        finally:
            release.set()
            if appender.pid is not None:
                appender.join(timeout=10)
                if appender.is_alive():
                    appender.kill()
                    appender.join(timeout=5)
        assert appender.exitcode == 0, f"appender exited with {appender.exitcode}"
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.kill()
            holder.join(timeout=5)

    # The holder emitted LockAcquiredEvent on enter; persist=True leaves
    # the journal without a paired LockReleasedEvent (that's the
    # stale-lock recovery surface in spec step 6, not a bug here). The
    # appender's line is the third event.
    events = read_events(journal)
    types = [type(e).__name__ for e in events]
    assert types[0] == "LockAcquiredEvent", f"first event should be acquire, got {types}"
    assert "LockReleasedEvent" not in types, (
        f"persist=True must NOT emit LockReleasedEvent on clean exit (events: {types})"
    )
    assert any(getattr(e, "by", None) == "blocked-appender" for e in events), (
        f"appender's event missing from journal: {types}"
    )


def test_transaction_persist_true_writes_holder_file(tmp_path: Path) -> None:
    """``transaction(persist=True)`` writes ``.wiki.journal/lock`` on enter.

    Spec §Behavior "Claude-session manual hold" names the format:
    ``<by>\\n<iso-timestamp>\\n[<reason>]``. Step 5's CLI consumes this
    file; step 4 ships the writer. Pin the format here so the CLI's
    parser written in step 5 can target a stable on-disk contract.
    """

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    with transaction(journal, by="claude-session", reason="weekly digest", persist=True):
        holder_file = journal.parent / "lock"
        assert holder_file.exists()
        lines = holder_file.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "claude-session", f"holder line 0 must be <by>; got {lines}"
        # Line 1 is an ISO-8601 timestamp. Cheap shape check: parses.
        parsed = datetime.fromisoformat(lines[1])
        assert parsed.tzinfo is not None, "timestamp must be timezone-aware"
        assert lines[2] == "weekly digest", f"holder line 2 must be <reason>; got {lines}"


def test_transaction_persist_true_omits_reason_when_none(tmp_path: Path) -> None:
    """``persist=True`` without a reason: holder file has two lines, not three.

    Spec §Behavior writes the reason as an *optional* third line — a
    file with three lines means a reason is recorded, a file with two
    lines means none. Pin the absent-reason shape so step 5's parser
    doesn't get a phantom empty-string reason on a no-reason hold.
    """

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    with transaction(journal, by="claude-session", persist=True):
        holder_file = journal.parent / "lock"
        lines = holder_file.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "claude-session"
        datetime.fromisoformat(lines[1])
        assert len(lines) == 2, f"no-reason hold must have 2 lines; got {lines}"


def test_transaction_persist_true_leaves_holder_file_on_clean_exit(
    tmp_path: Path,
) -> None:
    """``persist=True`` clean exit: holder file persists, no LockReleasedEvent.

    Spec §Behavior: the CLI returns 0 *without releasing*; the holder
    file persists so a second ``wiki lock acquire`` can detect it.
    Pinning this means a future refactor that "tidies up" by deleting
    the holder file on ``__exit__`` would break the manual-hold flow.
    """

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    with transaction(journal, by="claude-session", reason="manual", persist=True):
        pass

    holder_file = journal.parent / "lock"
    assert holder_file.exists(), "persist=True clean exit must leave holder file in place"

    events = read_events(journal)
    types = [type(e).__name__ for e in events]
    assert types == ["LockAcquiredEvent"], (
        f"persist=True clean exit must NOT emit LockReleasedEvent; got {types}"
    )


def test_transaction_persist_true_cleans_up_holder_file_on_exception(
    tmp_path: Path,
) -> None:
    """``persist=True`` body raises: holder file is removed; LockReleasedEvent emitted.

    Quality-engineer concern (and spec §Edge cases by extension): a
    half-acquired persist that crashes mid-body leaves a phantom
    holder if cleanup runs only on clean exit. The persist=True path
    must distinguish "clean exit, keep the lock alive" from "exception,
    roll back" — otherwise stale-lock detection ends up firing on
    transactions the user never meant to leave open.
    """

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    boom = RuntimeError("body failed during persist transaction")

    with pytest.raises(RuntimeError) as excinfo:
        with transaction(journal, by="claude-session", reason="manual", persist=True):
            raise boom

    assert excinfo.value is boom
    holder_file = journal.parent / "lock"
    assert not holder_file.exists(), (
        "exception inside persist=True transaction must clean up the holder file"
    )

    events = read_events(journal)
    types = [type(e).__name__ for e in events]
    assert types == ["LockAcquiredEvent", "LockReleasedEvent"], (
        f"exception path must still emit LockReleasedEvent; got {types}"
    )


def test_nested_transaction_raises_runtime_error(tmp_path: Path) -> None:
    """Transaction-in-transaction raises ``RuntimeError``.

    Spec §Invariants: ``fcntl.flock`` is non-recursive at the OS level.
    The ContextVar guard makes nested ``append_event`` safe (the held
    fd is reused), but a *second* ``transaction()`` entry has ambiguous
    semantics: which ``by`` records the audit event? Whose
    ``LockReleasedEvent`` closes the bracket? Raising forces the caller
    to refactor rather than letting two acquire/release pairs interleave
    silently. The outer transaction must still emit a clean
    acquire/release pair so a caller catching the inner ``RuntimeError``
    doesn't leave the journal half-bracketed.
    """

    journal = tmp_path / "journal.jsonl"

    with pytest.raises(RuntimeError, match="non-recursive"):
        with transaction(journal, by="outer"):
            with transaction(journal, by="inner"):
                pass  # pragma: no cover - the second enter raises

    events = read_events(journal)
    types = [type(e).__name__ for e in events]
    assert types == [
        "LockAcquiredEvent",
        "LockReleasedEvent",
    ], f"outer transaction should still bracket cleanly; got {types}"


def test_transaction_clears_context_var_on_exit(tmp_path: Path) -> None:
    """After ``transaction()`` exits, ``append_event`` returns to per-call locking.

    Ensures the ContextVar is reset on the way out so a post-transaction
    ``append_event`` re-opens + re-locks the journal as usual. Without
    this, a subsequent append in the same process would write to a
    closed fd (or worse, a recycled fd) and silently corrupt the
    journal.
    """

    journal = tmp_path / "journal.jsonl"
    with transaction(journal, by="runner"):
        append_event(
            journal,
            PageWriteEvent(timestamp=_at(1), by="runner", path="in.md", hash="a" * 64),
        )

    # After exit: a fresh append should land cleanly.
    append_event(
        journal,
        PageWriteEvent(timestamp=_at(2), by="runner", path="out.md", hash="b" * 64),
    )
    events = read_events(journal)
    types = [type(e).__name__ for e in events]
    assert types == [
        "LockAcquiredEvent",
        "PageWriteEvent",
        "LockReleasedEvent",
        "PageWriteEvent",
    ], f"post-transaction append did not land outside the bracket: {types}"


def test_transaction_persist_true_os_lock_survives_with_block_exit(
    tmp_path: Path,
) -> None:
    """``persist=True``: OS-level lock is still held *after* ``__exit__`` returns.

    The contract that distinguishes ``persist=True`` from ``persist=False``
    is exactly this: spec §Behavior lines 127-128 promise "future
    ``fcntl.flock`` attempts from other processes block until ``wiki lock
    release`` runs." A naive implementation that keeps the fd as a
    generator-frame local would let CPython close it at ``__exit__``,
    silently releasing the OS lock — every other test in this file could
    still pass because they all assert *inside* the ``with`` block.

    This test pins the post-``__exit__`` state by spawning a holder
    worker that enters+exits the ``with`` block (signals ``post_exit``
    only *after* ``__exit__`` returned) and then waits. The main process
    attempts ``fcntl.flock(LOCK_EX | LOCK_NB)`` on the same journal; that
    must fail with ``EAGAIN`` / ``EWOULDBLOCK`` because the holder's fd
    is supposed to still be open in the module-level stash.
    """

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    ctx = multiprocessing.get_context("spawn")
    post_exit = ctx.Event()
    release = ctx.Event()

    holder = ctx.Process(
        target=_persist_post_exit_holder_worker,
        args=(str(journal), post_exit, release),
    )
    holder.start()
    try:
        assert post_exit.wait(timeout=10), (
            "holder did not signal post-__exit__ within 10s — the worker "
            "may be stuck or transaction(persist=True) raised on exit"
        )
        # Holder is past __exit__ and waiting on ``release``. Probe the
        # OS lock from the main process: a non-blocking exclusive flock
        # attempt must fail because persist=True must have kept the
        # holder's fd open.
        with journal.open("a", encoding="utf-8") as probe_fh:
            with pytest.raises(OSError) as excinfo:
                fcntl.flock(probe_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert excinfo.value.errno in (errno.EAGAIN, errno.EWOULDBLOCK), (
                f"expected EAGAIN/EWOULDBLOCK; got errno={excinfo.value.errno} "
                f"({errno.errorcode.get(excinfo.value.errno or 0, '?')}) — "
                f"persist=True did not keep the OS lock alive past __exit__"
            )
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.kill()
            holder.join(timeout=5)


def test_transaction_persist_true_reentry_without_release_raises(tmp_path: Path) -> None:
    """Same-path ``persist=True`` re-entry refuses loudly.

    Quality-engineer concern: the runtime guard at the top of
    ``transaction()`` (``persist and resolved in _PERSISTED_FDS``)
    prevents the silent failure mode where a second ``persist=True``
    acquire overwrites the stashed fd, leaking the prior fd and then
    deadlocking on its own ``flock`` call. Without this test, a
    regression that removes the guard would land with every other test
    still passing. After ``_release_persisted_fd`` runs, the slot is
    free and a fresh persist acquire succeeds — proving the lock-step
    contract step 5's CLI will lean on.
    """

    from llm_wiki_kit.journal import _release_persisted_fd

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    with transaction(journal, by="claude-session", persist=True):
        pass

    with pytest.raises(RuntimeError, match="_release_persisted_fd"):
        with transaction(journal, by="other-session", persist=True):
            pass  # pragma: no cover — enter raises before yield

    # Release the prior stashed fd; a fresh persist acquire must now work.
    _release_persisted_fd(journal)
    with transaction(journal, by="next-session", persist=True):
        pass

    # Cleanup for the autouse fixture's benefit; not an assertion.
    _release_persisted_fd(journal)


def test_transaction_emits_best_effort_release_when_acquire_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed ``LockAcquiredEvent`` append: best-effort release is still attempted.

    Quality-engineer concern: the inner ``try/except`` around the
    acquire-event append is the load-bearing branch that keeps a
    journal from carrying a lone ``LockAcquiredEvent`` when fsync
    fails. Without this test, commenting it out would pass every other
    test in the file. The contract: the body never runs (exception
    bubbles past the yield), the caller sees ``OSError``, and the
    journal carries a paired ``LockReleasedEvent``. The half-written
    ``LockAcquiredEvent`` line is already flushed (write+flush ran
    before fsync), so a subsequent reader sees both events.
    """

    journal = tmp_path / "journal.jsonl"

    fsync_calls = [0]
    real_fsync = os.fsync

    def flaky_fsync(fd: int) -> None:
        fsync_calls[0] += 1
        # Fail only the first fsync — the one inside the
        # LockAcquiredEvent append. Subsequent fsyncs (the best-effort
        # LockReleasedEvent) must succeed so we can observe the
        # contract from the outside.
        if fsync_calls[0] == 1:
            raise OSError(errno.EIO, "I/O error")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", flaky_fsync)

    body_ran = False

    with caplog.at_level(logging.WARNING, logger="llm_wiki_kit.journal"):
        with pytest.raises(OSError) as excinfo:
            with transaction(journal, by="weekly-digest"):
                body_ran = True  # pragma: no cover — yield never reached

    assert not body_ran, "transaction body must not run when acquire-event append fails"
    assert excinfo.value.errno == errno.EIO

    # Both lines should be on disk: write+flush ran before each fsync.
    events = read_events(journal)
    types = [type(e).__name__ for e in events]
    assert types == [
        "LockAcquiredEvent",
        "LockReleasedEvent",
    ], f"best-effort release was not attempted after failed acquire append; got {types}"


def test_release_persisted_fd_closes_stashed_fd_and_clears_entry(
    tmp_path: Path,
) -> None:
    """``_release_persisted_fd`` pops the entry and closes the fd.

    Quality-engineer concern: this is the contract surface step 5's
    ``wiki lock release`` will call. Shipping it untested means a
    refactor between now and step 5 (e.g. raising ``KeyError`` on
    missing, or forgetting to close) lands silently. Pin the
    "pops + closes" pair here so step 5 has a stable target.
    """

    from llm_wiki_kit.journal import _PERSISTED_FDS, _release_persisted_fd

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    with transaction(journal, by="claude-session", persist=True):
        pass

    resolved = journal.resolve()
    assert resolved in _PERSISTED_FDS, "persist=True clean exit must stash the fd"
    stashed_fh = _PERSISTED_FDS[resolved]
    assert not stashed_fh.closed

    _release_persisted_fd(journal)
    assert resolved not in _PERSISTED_FDS, "release must pop the entry"
    assert stashed_fh.closed, "release must close the stashed fd"


def test_release_persisted_fd_is_noop_when_no_entry(tmp_path: Path) -> None:
    """``_release_persisted_fd`` on an unheld journal is a silent no-op.

    Spec §Outputs ("release on an unheld lock") commits to silent zero;
    the helper carries the same contract. A regression that raised
    ``KeyError`` here would mean step 5's CLI has to special-case the
    "lock already released" path, which the spec deliberately puts on
    the kit instead.
    """

    from llm_wiki_kit.journal import _PERSISTED_FDS, _release_persisted_fd

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    # No transaction has ever run for this path; the stash is empty.
    assert journal.resolve() not in _PERSISTED_FDS

    _release_persisted_fd(journal)  # must not raise

    assert journal.resolve() not in _PERSISTED_FDS


def _hold_lock_blocking_worker(
    journal_str: str,
    ready_event: Any,
    release_event: Any,
) -> None:
    """Hold the journal's OS lock via a raw flock so a sibling process can probe.

    Step 5's ``transaction(nonblocking=True)`` test uses this to keep
    the lock held across the parent's call without involving the
    higher-level ``transaction()`` machinery (which has its own
    teardown that would interfere with what we're trying to assert).
    """

    import fcntl as _fcntl
    from pathlib import Path as _Path

    journal = _Path(journal_str)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as fh:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
        ready_event.set()
        release_event.wait(timeout=30)


def test_transaction_nonblocking_raises_lockunavailable_when_held(tmp_path: Path) -> None:
    """``transaction(nonblocking=True)`` against a held lock raises without side effects.

    Pins the four invariants that the CLI's race-loss catch path
    depends on:

    1. The raise is a :class:`LockUnavailableError`, not a plain
       ``OSError`` — so the CLI's narrow ``except`` doesn't swallow
       real disk errors.
    2. ``_PERSISTED_FDS`` is empty afterward — the failed acquire
       never installs a persisted entry.
    3. ``_HELD_FD`` is back to ``None`` — the ContextVar token was
       never set (the flock raise happens before the set).
    4. The holder file is not written — the rollback isn't required
       because the holder write happens *after* a successful flock.
    """

    import multiprocessing as mp

    from llm_wiki_kit.journal import _HELD_FD, _PERSISTED_FDS, _lock_holder_path

    journal = tmp_path / ".wiki.journal" / "journal.jsonl"
    holder_path = _lock_holder_path(journal)

    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(
        target=_hold_lock_blocking_worker,
        args=(str(journal), ready, release),
    )
    holder.start()
    try:
        assert ready.wait(timeout=10)

        with pytest.raises(LockUnavailableError):
            with transaction(
                journal,
                by="alice",
                reason="probe",
                persist=True,
                nonblocking=True,
            ):
                pytest.fail("transaction body must not run when flock fails")

        assert _PERSISTED_FDS == {}, "failed acquire must not stash a persisted fd"
        assert _HELD_FD.get() is None, "ContextVar must not survive a failed acquire"
        assert not holder_path.exists(), "holder file must not be written before flock succeeds"
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)


# ---------------------------------------------------------------------------
# journal-reader-cache spec (qC4)
# ---------------------------------------------------------------------------


def _seed_event(by: str = "core") -> VaultInitEvent:
    return VaultInitEvent(timestamp=_at(0), by=by, vault_name="v", recipe="minimal")


def test_journal_reader_caches_events_within_scope(tmp_path: Path) -> None:
    """``events()`` returns the same list object on every call after first load."""
    journal = tmp_path / "journal.jsonl"
    append_event(journal, _seed_event())
    reader = JournalReader(journal)
    first = reader.events()
    second = reader.events()
    assert first is second  # identity, not just equality — the load-bearing pin


def test_journal_reader_lazy_loads_only_when_events_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``JournalReader`` does not call ``read_events`` until ``events()`` fires."""
    import llm_wiki_kit.journal as _journal

    journal = tmp_path / "journal.jsonl"
    append_event(journal, _seed_event())

    calls = {"n": 0}
    original = _journal.read_events

    def counting_read_events(p: Path) -> list[Event]:
        calls["n"] += 1
        return original(p)

    monkeypatch.setattr(_journal, "read_events", counting_read_events)
    reader = JournalReader(journal)
    assert calls["n"] == 0  # constructor does NOT read
    reader.events()
    assert calls["n"] == 1  # first call reads
    reader.events()
    assert calls["n"] == 1  # second call does not


def test_journal_reader_notify_appended_extends_list_after_load(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    append_event(journal, _seed_event(by="seed"))
    reader = JournalReader(journal)
    loaded = reader.events()
    assert len(loaded) == 1

    later = _seed_event(by="later")
    reader.notify_appended(later)
    assert reader.events()[-1] is later
    assert len(reader.events()) == 2


def test_journal_reader_notify_appended_is_noop_before_load(tmp_path: Path) -> None:
    """Lazy-load contract: notify_appended is no-op until events() fires.

    Without this, a notify_appended call before the first events()
    would conjure an in-memory state for events we never observed by
    reading — divergent from disk in a way that's hard to catch.
    """
    journal = tmp_path / "journal.jsonl"
    append_event(journal, _seed_event(by="on-disk"))
    reader = JournalReader(journal)

    ghost = _seed_event(by="ghost")
    reader.notify_appended(ghost)  # no-op (events() not yet called)

    loaded = reader.events()
    assert [e.by for e in loaded if isinstance(e, VaultInitEvent)] == ["on-disk"]


def test_use_journal_cache_installs_and_resets_contextvar(tmp_path: Path) -> None:
    import llm_wiki_kit.journal as _journal

    journal = tmp_path / "journal.jsonl"
    assert _journal._CURRENT_READER.get() is None
    with use_journal_cache(journal) as reader:
        assert _journal._CURRENT_READER.get() is reader
    assert _journal._CURRENT_READER.get() is None


def test_use_journal_cache_resets_on_exception(tmp_path: Path) -> None:
    import llm_wiki_kit.journal as _journal

    journal = tmp_path / "journal.jsonl"
    with pytest.raises(RuntimeError, match="body raised"):
        with use_journal_cache(journal):
            raise RuntimeError("body raised")
    assert _journal._CURRENT_READER.get() is None


def test_use_journal_cache_non_recursive_raises(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    with use_journal_cache(journal):
        with pytest.raises(RuntimeError, match="non-recursive"):
            with use_journal_cache(journal):
                pass  # pragma: no cover — guard fires before this line


def test_append_event_notifies_active_reader_on_matching_journal(tmp_path: Path) -> None:
    """Inside a cache scope, append_event extends the in-memory cache."""
    journal = tmp_path / "journal.jsonl"
    append_event(journal, _seed_event(by="seed"))

    with use_journal_cache(journal) as reader:
        loaded = reader.events()
        assert len(loaded) == 1
        append_event(journal, _seed_event(by="appended"))
        assert len(reader.events()) == 2
        assert reader.events()[-1].by == "appended"


def test_append_event_does_not_notify_reader_for_different_journal(tmp_path: Path) -> None:
    journal_a = tmp_path / "a.jsonl"
    journal_b = tmp_path / "b.jsonl"
    append_event(journal_a, _seed_event(by="a-seed"))
    append_event(journal_b, _seed_event(by="b-seed"))

    with use_journal_cache(journal_a) as reader_a:
        loaded = reader_a.events()
        assert len(loaded) == 1
        append_event(journal_b, _seed_event(by="b-appended"))
        # Reader for A unchanged — the append was to B.
        assert reader_a.events() is loaded
        assert len(reader_a.events()) == 1


def test_append_event_does_nothing_to_cache_without_scope(tmp_path: Path) -> None:
    """A reader for journal A is untouched when a scope-less append fires.

    Pins the cross-path-without-scope contract: outside a
    ``use_journal_cache`` block, ``_notify_reader`` looks up
    ``_CURRENT_READER.get()``, sees ``None``, and does nothing.
    A regression that crashed inside ``_notify_reader`` on
    ``None.notify_appended`` (the AttributeError shape) would surface
    here as an exception, not as a silent journal append.
    """
    import llm_wiki_kit.journal as _journal

    journal = tmp_path / "journal.jsonl"
    assert _journal._CURRENT_READER.get() is None
    append_event(journal, _seed_event(by="alice"))
    append_event(journal, _seed_event(by="bob"))
    assert _journal._CURRENT_READER.get() is None
    events = read_events(journal)
    assert [e.by for e in events if isinstance(e, VaultInitEvent)] == ["alice", "bob"]


def test_journal_reader_returns_empty_list_for_missing_journal(tmp_path: Path) -> None:
    """Spec §Edge cases "Journal does not yet exist."

    The cache scope opens *before* ``VaultInitEvent`` lands in
    ``_cmd_init`` — on entry the journal file does not exist yet, so
    ``events()`` must return ``[]`` cleanly. A future refactor that
    pre-checked existence in ``__init__`` and raised would break the
    fresh-vault flow.
    """
    journal = tmp_path / "absent.jsonl"
    assert not journal.exists()
    reader = JournalReader(journal)
    assert reader.events() == []

    # notify_appended after the empty load extends the cache normally.
    event = _seed_event(by="post-empty")
    reader.notify_appended(event)
    assert reader.events() == [event]


def test_append_event_inside_transaction_still_notifies_reader(tmp_path: Path) -> None:
    """Held-fd branch of append_event must also notify the reader.

    Otherwise multi-event operation runners (``journal.transaction``)
    would see cache-vs-disk divergence inside the transaction body —
    a subtle bug that this pin makes visible.
    """
    journal = tmp_path / "journal.jsonl"
    append_event(journal, _seed_event(by="seed"))

    with use_journal_cache(journal) as reader:
        reader.events()  # force load
        with transaction(journal, by="alice", reason="batch"):
            append_event(journal, _seed_event(by="inside-tx"))
            # LockAcquiredEvent + inside-tx + seed = 3 events
        # …+ LockReleasedEvent = 4 events
        cached_bys = [e.by for e in reader.events()]
        # Acquire and release events also flow through append_event, so
        # the cache should be aware of every event the transaction
        # emitted, not just inside-tx.
        assert "seed" in cached_bys
        assert "inside-tx" in cached_bys
        # Cross-check: on-disk file must agree.
        disk_bys = [e.by for e in read_events(journal)]
        assert cached_bys == disk_bys


def test_use_journal_cache_nested_inside_transaction_is_allowed(tmp_path: Path) -> None:
    """The two ContextVars are orthogonal — one is held-fd, one is cache."""
    journal = tmp_path / "journal.jsonl"
    with transaction(journal, by="alice", reason="outer"):
        with use_journal_cache(journal) as reader:
            append_event(journal, _seed_event(by="inside-both"))
            cached_bys = [e.by for e in reader.events()]
            assert "inside-both" in cached_bys
