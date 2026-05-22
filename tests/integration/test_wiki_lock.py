"""End-to-end ``wiki lock acquire|release`` integration tests (journal-locking step 5).

Tests the CLI surface defined in
``docs/specs/journal-locking/spec.md`` §CLI surface acceptance criteria.
Vault construction reuses the kit-root threading pattern from
``test_wiki_doctor.py`` (qC8); ``cli.main(["lock", ...])`` exercises the
subcommands through their real argparse + handler path. ``wiki lock``
itself doesn't read kit assets, so its individual invocations don't
need ``kit_root=``; only the bootstrapping ``wiki init`` does.

Same-process test isolation matters: ``wiki lock acquire`` persists the
open journal fd in :data:`journal._PERSISTED_FDS` so the OS lock outlives
the CLI's ``__exit__``. An autouse fixture closes any persisted fd left
over from a previous test so the next test doesn't deadlock on its own
``fcntl.flock`` call.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit import journal as journal_module
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import LockAcquiredEvent, LockReleasedEvent

REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_kit(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    (kit / "templates").mkdir()
    recipes_dir = kit / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "minimal.yaml").write_text(
        "name: minimal\n"
        "version: 0.1.0\n"
        "description: Core-only recipe for wiki lock CLI tests.\n"
        "primitives:\n"
        "  - core\n"
        "variables:\n"
        "  recipe_name: minimal\n",
        encoding="utf-8",
    )
    return kit


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    return _install_kit(tmp_path)


@pytest.fixture
def vault(tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "vault"
    assert cli.main(["init", str(v), "--recipe", "minimal"], kit_root=kit_root) == 0
    monkeypatch.chdir(v)
    return v


def _drain_persisted_fds() -> None:
    for path, fh in list(journal_module._PERSISTED_FDS.items()):
        try:
            fh.close()
        except Exception:
            pass
        journal_module._PERSISTED_FDS.pop(path, None)


@pytest.fixture(autouse=True)
def _clear_persisted_fds() -> Iterator[None]:
    """Close any fd left in :data:`journal._PERSISTED_FDS` before and after each test.

    Pre-yield cleanup lets a developer running a single test with
    ``-k`` after a crashed prior run start from a clean stash. Post-yield
    cleanup catches the acquire tests that don't release. Both run
    because the operation is idempotent — a clean stash is still clean
    after iterating it.
    """

    _drain_persisted_fds()
    yield
    _drain_persisted_fds()


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _holder_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "lock"


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


def test_wiki_lock_acquire_exits_zero_on_first_acquire(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    exit_code = cli.main(["lock", "acquire", "--by", "alice", "--reason", "weekly digest"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""

    # Holder file format: <by>\n<iso-timestamp>\n[<reason>]
    holder = _holder_path(vault).read_text(encoding="utf-8").splitlines()
    assert holder[0] == "alice"
    # acquired_at parses as an ISO timestamp.
    datetime.fromisoformat(holder[1])
    assert holder[2] == "weekly digest"

    events = read_events(_journal_path(vault))
    acquired = [e for e in events if isinstance(e, LockAcquiredEvent)]
    assert len(acquired) == 1
    assert acquired[-1].by == "alice"
    assert acquired[-1].reason == "weekly digest"


def test_wiki_lock_acquire_exits_three_when_held(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["lock", "acquire", "--by", "alice"]) == 0
    capsys.readouterr()

    exit_code = cli.main(["lock", "acquire", "--by", "bob"])
    assert exit_code == cli.LOCK_HELD_EXIT == 3

    captured = capsys.readouterr()
    # Spec §Error cases format: ``lock held by <name> since <iso>``.
    assert "lock held by alice since " in captured.err
    # Extract the trailing timestamp and confirm it's a real ISO 8601 stamp,
    # not just any string containing "T".
    suffix = captured.err.split("lock held by alice since ", 1)[1].strip()
    datetime.fromisoformat(suffix)

    # Holder file unchanged — bob did not overwrite alice.
    holder = _holder_path(vault).read_text(encoding="utf-8").splitlines()
    assert holder[0] == "alice"


def test_wiki_lock_acquire_requires_by_argument(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """argparse refuses ``wiki lock acquire`` with no ``--by``."""

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["lock", "acquire"])
    # argparse exits 2 on argument errors.
    assert excinfo.value.code == 2
    assert "--by" in capsys.readouterr().err


def test_wiki_lock_acquire_reclaims_stale_holder(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Holder file present but OS lock free (dead PID) → reclaim audit pair."""

    # Simulate a prior crashed session: holder file on disk, no live lock.
    holder_path = _holder_path(vault)
    holder_path.write_text("dead-session\n2026-05-15T00:00:00+00:00\nold work\n", encoding="utf-8")

    capsys.readouterr()
    assert cli.main(["lock", "acquire", "--by", "bob", "--reason", "reclaim"]) == 0

    events = read_events(_journal_path(vault))
    # Order: ... LockReleasedEvent(wiki-doctor, "stale lock reclaimed"), LockAcquiredEvent(bob).
    lock_events = [e for e in events if isinstance(e, LockAcquiredEvent | LockReleasedEvent)]
    assert len(lock_events) >= 2
    released, acquired = lock_events[-2], lock_events[-1]
    assert isinstance(released, LockReleasedEvent)
    assert released.by == "wiki-doctor"
    assert released.reason == "stale lock reclaimed"
    assert isinstance(acquired, LockAcquiredEvent)
    assert acquired.by == "bob"

    # New holder file installed by transaction(persist=True).
    holder = holder_path.read_text(encoding="utf-8").splitlines()
    assert holder[0] == "bob"


def test_wiki_lock_acquire_unsupported_fs_skips_reclaim_audit(
    vault: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported-FS probe (returns ``None``) must not emit a reclaim audit.

    Spec §Edge cases distinguishes "lock free" from "lock not testable".
    On iCloud Drive / NFS / SMB the kit cannot tell whether a holder file
    represents a stale crash or a live cross-device hold; emitting
    ``LockReleasedEvent(by="wiki-doctor", reason="stale lock reclaimed")``
    on every invocation would be a lie. Monkeypatch the probe to ``None``
    and assert no reclaim event lands.
    """

    holder_path = _holder_path(vault)
    holder_path.write_text("dead-or-synced\n2026-05-15T00:00:00+00:00\nold\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_probe_lock_contention", lambda _p: None)

    capsys.readouterr()
    assert cli.main(["lock", "acquire", "--by", "alice"]) == 0

    events = read_events(_journal_path(vault))
    # No reclaim audit emitted: zero LockReleasedEvent(by="wiki-doctor").
    reclaims = [e for e in events if isinstance(e, LockReleasedEvent) and e.by == "wiki-doctor"]
    assert reclaims == []
    # But the real acquire still happened.
    acquired = [e for e in events if isinstance(e, LockAcquiredEvent)]
    assert acquired[-1].by == "alice"


def test_wiki_lock_acquire_raceloss_after_stale_reclaim_exits_three(
    vault: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-reclaim transaction race-loss path exits 3, doesn't hang.

    After a successful reclaim audit, a competitor could win the flock
    between our reclaim write and the transaction's own non-blocking
    flock. The CLI must catch ``LockUnavailableError``, re-read the
    holder file to name the *new* holder, and exit with the contention
    code instead of blocking. Monkeypatch ``journal.transaction`` to
    simulate the race directly.

    Limitation: simulates the race via monkeypatch; cross-process
    timing is not exercised here. The unit test
    ``test_transaction_nonblocking_raises_lockunavailable_when_held``
    in ``tests/unit/test_journal.py`` covers the real
    ``LockUnavailableError`` shape end-to-end via a subprocess holder.
    """

    holder_path = _holder_path(vault)
    holder_path.write_text("dead-session\n2026-05-15T00:00:00+00:00\n", encoding="utf-8")

    # Inject a "competitor wins between probe and transaction" by
    # rewriting the holder file *and* raising LockUnavailableError from
    # the transaction. The reclaim write happens first (real, into the
    # journal), then the transaction simulates the race-loss.
    real_transaction = journal_module.transaction
    calls: list[int] = []

    def racing_transaction(*a: object, **kw: object) -> object:
        calls.append(1)
        # Pin the CLI's contract with the journal: it must call the
        # transaction with both ``persist=True`` and ``nonblocking=True``.
        # If a future refactor drops either, the CLI would block on
        # contention; we want this test to fail at that boundary rather
        # than silently passing because the stub raises regardless.
        assert kw.get("persist") is True
        assert kw.get("nonblocking") is True
        # Simulate the competitor's win by updating the holder file
        # before raising — the CLI must read this new state.
        holder_path.write_text(
            "competitor\n2026-05-16T00:00:00+00:00\nwon the race\n",
            encoding="utf-8",
        )
        raise journal_module.LockUnavailableError(11, "simulated race loss")

    monkeypatch.setattr(journal_module, "transaction", racing_transaction)
    # cli.py imported `transaction` at module load, so patch the rebind too.
    monkeypatch.setattr(cli, "transaction", racing_transaction)

    capsys.readouterr()
    exit_code = cli.main(["lock", "acquire", "--by", "alice"])
    assert exit_code == cli.LOCK_HELD_EXIT == 3
    assert calls == [1]  # racing_transaction was reached

    err = capsys.readouterr().err
    assert "competitor" in err

    # Reclaim audit landed (real append_event call); no LockAcquiredEvent
    # for alice (the transaction failed before yielding).
    monkeypatch.setattr(journal_module, "transaction", real_transaction)
    events = read_events(_journal_path(vault))
    reclaims = [e for e in events if isinstance(e, LockReleasedEvent) and e.by == "wiki-doctor"]
    assert len(reclaims) == 1
    assert not any(isinstance(e, LockAcquiredEvent) and e.by == "alice" for e in events)


def test_wiki_lock_acquire_raceloss_with_missing_holder_file_exits_three(
    vault: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a race-loss happens and the holder file is gone, surface a fallback message.

    Exotic shape: the competitor took the lock via a path that doesn't
    write a holder file (e.g. raw ``append_event`` for a transient
    sequence), or the competitor's holder file write hasn't landed yet.
    The CLI must still exit ``LOCK_HELD_EXIT`` with a usable stderr
    line, not crash on the destructuring above.
    """

    real_holder_reader = cli._read_holder_file
    holder_path = _holder_path(vault)

    def raise_lockunavailable(*_a: object, **_kw: object) -> object:
        # Simulate the race: holder file vanishes between the CLI's
        # initial read and the transaction's flock failure.
        if holder_path.exists():
            holder_path.unlink()
        raise journal_module.LockUnavailableError(11, "simulated race loss")

    # Seed a holder file so the CLI enters the existing-holder branch.
    holder_path.write_text("ghost\n2026-05-15T00:00:00+00:00\n", encoding="utf-8")
    # Probe says "free" (stale) so the CLI proceeds to transaction; the
    # racing stub then erases the holder file and raises.
    monkeypatch.setattr(cli, "_probe_lock_contention", lambda _p: False)
    monkeypatch.setattr(cli, "transaction", raise_lockunavailable)
    # Re-read still uses the real implementation; it will return None.
    monkeypatch.setattr(cli, "_read_holder_file", real_holder_reader)

    capsys.readouterr()
    exit_code = cli.main(["lock", "acquire", "--by", "alice"])
    assert exit_code == cli.LOCK_HELD_EXIT == 3
    assert "holder file missing" in capsys.readouterr().err


def test_wiki_lock_acquire_prints_stdout_confirmation(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Happy path prints a one-line ack so an interactive user sees it landed.

    The acquire exits 0 silently otherwise — indistinguishable from a
    stub no-op. Reclaim path adds a suffix naming the prior holder so
    the kit isn't quietly reaping a session the user thought was live.
    """

    capsys.readouterr()
    assert cli.main(["lock", "acquire", "--by", "alice", "--reason", "demo"]) == 0
    out = capsys.readouterr().out
    assert "Acquired lock for alice" in out
    assert "reason: demo" in out
    assert "reclaimed" not in out  # no prior holder

    # Release to set up the reclaim case.
    cli.main(["lock", "release", "--by", "alice"])

    # Synthetic stale holder.
    _holder_path(vault).write_text("old-session\n2026-05-15T00:00:00+00:00\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["lock", "acquire", "--by", "bob"]) == 0
    out = capsys.readouterr().out
    assert "Acquired lock for bob" in out
    assert "reclaimed stale lock previously held by old-session" in out


@pytest.mark.parametrize("field", ["by", "reason"])
@pytest.mark.parametrize("sep", ["\n", "\r"])
def test_wiki_lock_acquire_rejects_newline_in_by_or_reason(
    vault: Path,
    capsys: pytest.CaptureFixture[str],
    field: str,
    sep: str,
) -> None:
    """Newlines in ``--by``/``--reason`` corrupt the holder file's line format.

    Covers all four (arg, separator) combinations so a future refactor
    that narrows the guard to ``--by``-only or drops the ``\\r`` arm
    fails this test instead of silently re-enabling holder-file
    corruption.
    """

    args = ["lock", "acquire", "--by", "alice"]
    if field == "by":
        args[3] = f"alice{sep}2050-01-01T00:00:00+00:00"
    else:
        args += ["--reason", f"first line{sep}second line"]

    capsys.readouterr()
    exit_code = cli.main(args)
    assert exit_code == cli.WIKI_ERROR_EXIT
    assert "newline" in capsys.readouterr().err

    # No state changes — neither holder file nor journal acquire event.
    assert not _holder_path(vault).exists()
    events = read_events(_journal_path(vault))
    assert not any(isinstance(e, LockAcquiredEvent) for e in events)


def test_wiki_lock_acquire_outside_a_vault_is_wiki_error(
    tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_a_vault = tmp_path / "elsewhere"
    not_a_vault.mkdir()
    monkeypatch.chdir(not_a_vault)
    assert cli.main(["lock", "acquire", "--by", "alice"]) == cli.WIKI_ERROR_EXIT


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


def test_wiki_lock_release_clears_holder_and_journals_release_event(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["lock", "acquire", "--by", "alice", "--reason", "x"]) == 0
    capsys.readouterr()

    assert cli.main(["lock", "release", "--by", "alice"]) == 0
    assert not _holder_path(vault).exists()

    events = read_events(_journal_path(vault))
    released = [e for e in events if isinstance(e, LockReleasedEvent)]
    # Last LockReleasedEvent is the one this command wrote.
    assert released[-1].by == "alice"


def test_wiki_lock_release_refuses_by_mismatch_without_force(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["lock", "acquire", "--by", "alice"]) == 0
    capsys.readouterr()

    exit_code = cli.main(["lock", "release", "--by", "bob"])
    assert exit_code == cli.WIKI_ERROR_EXIT == 2
    err = capsys.readouterr().err
    assert "alice" in err
    assert "--force" in err

    # Holder file untouched.
    assert _holder_path(vault).read_text(encoding="utf-8").splitlines()[0] == "alice"

    # Cleanup so the fixture's flock-leak guard doesn't trip on real teardown.
    assert cli.main(["lock", "release", "--by", "alice"]) == 0


def test_wiki_lock_release_with_force_overrides_holder(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["lock", "acquire", "--by", "alice"]) == 0
    capsys.readouterr()

    assert cli.main(["lock", "release", "--by", "bob", "--force"]) == 0
    assert not _holder_path(vault).exists()

    events = read_events(_journal_path(vault))
    released = [e for e in events if isinstance(e, LockReleasedEvent)]
    # The forced release records bob as the actor — the audit names whoever
    # ran the release, not the prior holder.
    assert released[-1].by == "bob"


def test_wiki_lock_release_on_unheld_is_silent_zero(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert not _holder_path(vault).exists()
    capsys.readouterr()

    assert cli.main(["lock", "release", "--by", "alice"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    # No LockReleasedEvent was appended for a no-op release.
    events_after = read_events(_journal_path(vault))
    assert not any(isinstance(e, LockReleasedEvent) for e in events_after)


def test_wiki_lock_release_outside_a_vault_is_wiki_error(
    tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_a_vault = tmp_path / "elsewhere"
    not_a_vault.mkdir()
    monkeypatch.chdir(not_a_vault)
    assert cli.main(["lock", "release", "--by", "alice"]) == cli.WIKI_ERROR_EXIT


def test_wiki_lock_release_without_by_uses_holder(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec: ``--by`` is optional on release. When omitted, audit records the holder's name."""

    assert cli.main(["lock", "acquire", "--by", "alice"]) == 0
    capsys.readouterr()

    assert cli.main(["lock", "release"]) == 0
    events = read_events(_journal_path(vault))
    released = [e for e in events if isinstance(e, LockReleasedEvent)]
    assert released[-1].by == "alice"
