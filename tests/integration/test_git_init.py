"""Integration tests for ``llm_wiki_kit.git_init.initialize_git``.

Drive the real ``git`` binary inside ``tmp_path`` and assert on resulting
state — never on subprocess call counts. The spec at
``docs/specs/wiki-init-git/spec.md`` §Behavior step 6 pins:

* The function runs ``git init`` and then a single ``git commit``,
  appending ``VaultGitInitializedEvent`` to the journal **between**
  those two subprocess calls so the event's journal line is captured
  by the initial commit's tree.
* ``git init`` failure surfaces stderr verbatim through ``WikiError``
  without the kit-authored ``pass --no-git to skip git initialization``
  hint (the failure is not config-shaped).
* ``git commit`` failure surfaces stderr AND that literal hint
  substring; the event IS journaled because step ordering puts the
  append before staging.
* A pre-existing ``.git/`` short-circuits the function: no commit
  lands, no event is journaled, and the existing repo's HEAD is
  byte-identical to its pre-call state.

Tests build a partial vault tree (one journal directory plus a couple
of regular files) in ``tmp_path``, then call ``initialize_git``
directly. The tests do not exercise the CLI handler — that's covered
by ``tests/integration/test_wiki_init_git.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.git_init import initialize_git
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import VaultGitInitializedEvent

NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


def _stage_partial_vault(target: Path) -> Path:
    """Lay down a tiny vault tree with a journal directory and one file.

    Returns the journal-path the kit would compute. The shape is the
    minimum ``initialize_git`` needs to operate on: a non-empty target
    so ``git add -A`` has something to stage, plus the
    ``.wiki.journal/`` directory the kit owns.
    """

    target.mkdir(parents=True, exist_ok=True)
    journal_dir = target / ".wiki.journal"
    journal_dir.mkdir()
    journal_path = journal_dir / "journal.jsonl"
    journal_path.write_text("", encoding="utf-8")
    (target / "AGENTS.md").write_text("# vault\n", encoding="utf-8")
    (target / ".gitignore").write_text("*.proposed\n", encoding="utf-8")
    return journal_path


def test_initialize_git_creates_repo_with_one_commit(tmp_path: Path) -> None:
    """Happy path — after the call, the vault is a git repo with one commit.

    Asserts the spec's observable contract: ``.git/`` exists, exactly
    one commit with the documented message, ``VaultGitInitializedEvent``
    in the journal, and ``git status --porcelain`` empty.
    """

    target = tmp_path / "vault"
    journal_path = _stage_partial_vault(target)

    initialize_git(
        target,
        recipe_name="personal",
        journal_path=journal_path,
        _now=NOW,
    )

    assert (target / ".git").is_dir()
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    log_lines = [line for line in log.stdout.splitlines() if line]
    assert len(log_lines) == 1, f"expected exactly one commit, got {log_lines!r}"
    assert "Initialize wiki vault from personal recipe" in log_lines[0]

    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    assert porcelain.stdout == "", f"expected clean porcelain, got: {porcelain.stdout!r}"

    events = read_events(journal_path)
    git_events = [e for e in events if isinstance(e, VaultGitInitializedEvent)]
    assert len(git_events) == 1
    assert git_events[0].by == "wiki-init"


def test_initialize_git_surfaces_init_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git init`` failure surfaces stderr without the kit-authored hint.

    Forces failure with a broken ``git`` shim on ``$PATH`` so the
    subprocess returns non-zero. (Pre-creating ``target/.git`` as a
    regular file was the original approach, but the kit now treats
    any pre-existing ``.git`` — directory or gitfile — as user
    territory and short-circuits, so that route no longer exercises
    the init-failure code path.)

    The spec pins the negative assertion on the literal kit-authored
    substring ``pass --no-git to skip git initialization`` — git's
    own stderr is out of the kit's control and may use words like
    "config" or "user" incidentally, so anchoring on the kit
    substring is the only safe negative assertion.
    """

    shim_dir = tmp_path / "broken-git-bin"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        '#!/bin/sh\necho "git: broken wrapper for test" >&2\nexit 1\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")

    target = tmp_path / "vault"
    journal_path = _stage_partial_vault(target)

    with pytest.raises(WikiError) as excinfo:
        initialize_git(
            target,
            recipe_name="personal",
            journal_path=journal_path,
            _now=NOW,
        )

    message = str(excinfo.value)
    assert "pass --no-git to skip git initialization" not in message
    assert "git init" in message.lower()

    events = read_events(journal_path)
    assert not any(isinstance(e, VaultGitInitializedEvent) for e in events)


def test_initialize_git_surfaces_add_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git add`` failure surfaces stderr without the kit-authored hint.

    The kit's spec puts ``git add -A`` *after* the
    ``VaultGitInitializedEvent`` append but before ``git commit``, so
    an add failure leaves the same partial state as a commit failure:
    event journaled, no commit, vault rendered. The error message
    shape is bucketed with ``git init`` failure — no hint — because
    the plausible causes (disk full, permission, index corruption)
    aren't config-shaped.

    Pinned with a selective shim that delegates to the real ``git``
    for everything except ``git add``, which exits non-zero.
    """

    real_git = shutil.which("git")
    assert real_git is not None, "test sandbox lacks git on PATH"

    shim_dir = tmp_path / "add-failing-bin"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "add" ]; then\n'
        '  echo "git: simulated add failure" >&2\n'
        "  exit 1\n"
        "fi\n"
        f'exec {real_git} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")

    target = tmp_path / "vault"
    journal_path = _stage_partial_vault(target)

    with pytest.raises(WikiError) as excinfo:
        initialize_git(
            target,
            recipe_name="personal",
            journal_path=journal_path,
            _now=NOW,
        )

    message = str(excinfo.value)
    # `git add` failure is bucketed with init-failure — no config hint.
    assert "pass --no-git to skip git initialization" not in message
    assert "git add" in message.lower()

    # `.git/` exists (real `git init` ran via the shim's exec-through).
    assert (target / ".git").is_dir()

    # Event WAS journaled (append-before-stage ordering); the partial
    # state is identical to the commit-failure recovery story.
    events = read_events(journal_path)
    git_events = [e for e in events if isinstance(e, VaultGitInitializedEvent)]
    assert len(git_events) == 1


def test_initialize_git_surfaces_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git commit`` failure surfaces stderr AND the kit-authored config hint.

    Forces commit failure by pointing ``GIT_CONFIG_GLOBAL`` at
    ``/dev/null`` and ``HOME`` at an empty tmp dir, so git has no
    ``user.name`` / ``user.email`` to attribute the commit to. The
    event IS journaled because step ordering puts the append before
    staging (spec §Behavior step 6).
    """

    target = tmp_path / "vault"
    journal_path = _stage_partial_vault(target)

    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    # Unset author/committer env vars that would otherwise satisfy git.
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(WikiError) as excinfo:
        initialize_git(
            target,
            recipe_name="personal",
            journal_path=journal_path,
            _now=NOW,
        )

    message = str(excinfo.value)
    assert "pass --no-git to skip git initialization" in message

    # `.git/` exists from the successful `git init` but has no commit.
    assert (target / ".git").is_dir()
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    assert log.returncode != 0  # No commits → `git log` exits non-zero.

    # Event IS journaled per the append-before-stage ordering.
    events = read_events(journal_path)
    git_events = [e for e in events if isinstance(e, VaultGitInitializedEvent)]
    assert len(git_events) == 1


def test_initialize_git_skips_when_dot_git_exists(tmp_path: Path) -> None:
    """A pre-existing ``.git/`` short-circuits the function.

    Pre-create a valid git repo with one seed commit. Snapshot HEAD
    before the call; after the call, HEAD bytes are unchanged, the
    log lists only the seed commit, and no ``VaultGitInitializedEvent``
    was appended. Outcome-shape only.
    """

    target = tmp_path / "vault"
    journal_path = _stage_partial_vault(target)

    # Build the seed repo.
    subprocess.run(["git", "init"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "seed@example.com"],
        cwd=target,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Seed"], cwd=target, check=True, capture_output=True
    )
    (target / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed commit"],
        cwd=target,
        check=True,
        capture_output=True,
    )

    head_bytes_before = (target / ".git" / "HEAD").read_bytes()
    sha_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    events_before = read_events(journal_path)

    initialize_git(
        target,
        recipe_name="personal",
        journal_path=journal_path,
        _now=NOW,
    )

    # HEAD content + SHA unchanged.
    assert (target / ".git" / "HEAD").read_bytes() == head_bytes_before
    sha_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert sha_after == sha_before

    # Log lists only the seed commit.
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    log_lines = [line for line in log.stdout.splitlines() if line]
    assert len(log_lines) == 1
    assert "seed commit" in log_lines[0]

    # No new event appended.
    events_after = read_events(journal_path)
    assert events_after == events_before
    assert not any(isinstance(e, VaultGitInitializedEvent) for e in events_after)
