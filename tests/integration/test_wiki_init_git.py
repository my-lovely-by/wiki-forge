"""End-to-end tests for the ``wiki init`` git-initialization phase.

Drives ``cli.main`` directly so refusals surface as ``WIKI_ERROR_EXIT``
or as :class:`WikiError` instances captured by the CLI boundary. The
shipped ``personal`` recipe is the test target — it's the smallest
recipe that exercises a non-trivial closure.

Contract tests (see ``docs/specs/wiki-init-git/spec.md`` §Contract tests):

* #1 ``test_wiki_init_default_creates_git_repo``
* #2 ``test_wiki_init_no_git_skips_git``
* #3 ``test_wiki_init_no_git_matches_default_tree``
* #4 ``test_wiki_init_refuses_when_git_missing``
* #5 ``test_wiki_init_empty_check_fires_before_git_pre_flight``
* #6 ``test_wiki_init_surfaces_commit_failure``
* #7 ``test_wiki_init_surfaces_init_failure``
* #8 ``test_initialize_git_skips_when_dot_git_pre_exists`` (end-to-end
  counterpart to the direct-call test in ``test_git_init.py``)
* #9 ``test_wiki_init_initial_commit_excludes_proposed_sidecars``
* #10 ``test_rendered_gitignore_load_bearing_invariants``
* #11 ``test_wiki_doctor_clean_after_git_init``
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from llm_wiki_kit.cli import WIKI_ERROR_EXIT, main
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import (
    Event,
    VaultGitInitializedEvent,
    VaultInitEvent,
)


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk_files(root: Path, *, skip: set[str]) -> dict[str, str]:
    """Return ``{relative_path: sha256}`` for every regular file under ``root``.

    ``skip`` is a set of top-level directory names to omit (e.g.
    ``{".git"}``). Symlinks are followed; the kit's render path doesn't
    produce symlinks, so this is benign.
    """

    out: dict[str, str] = {}
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        try:
            rel = entry.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        if parts and parts[0] in skip:
            continue
        out[str(rel)] = _hash_file(entry)
    return out


def _event_kind(event: Event) -> tuple[str, str]:
    """Return the ``(type, by)`` pair that identifies an event's shape.

    Used in the two-run comparison test to align journals without
    caring about timestamps or per-line hashes.
    """

    return (event.type, event.by)


def test_wiki_init_default_creates_git_repo(tmp_path: Path) -> None:
    """Contract test #1 — default flags produce a git repo with one commit."""

    vault = tmp_path / "vault"
    assert main(["init", str(vault), "--recipe", "personal"]) == 0

    assert (vault / ".git").is_dir()

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    )
    log_lines = [line for line in log.stdout.splitlines() if line]
    assert len(log_lines) == 1
    assert "Initialize wiki vault from personal recipe" in log_lines[0]

    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    )
    assert porcelain.stdout == ""

    events = read_events(_journal_path(vault))
    git_events = [e for e in events if isinstance(e, VaultGitInitializedEvent)]
    assert len(git_events) == 1
    assert git_events[0].by == "wiki-init"


def test_wiki_init_no_git_skips_git(tmp_path: Path) -> None:
    """Contract test #2 — ``--no-git`` produces a vault without git state."""

    vault = tmp_path / "vault"
    assert main(["init", str(vault), "--recipe", "personal", "--no-git"]) == 0

    assert not (vault / ".git").exists()
    # Vault content is still present.
    assert (vault / "AGENTS.md").is_file()
    assert (vault / ".gitignore").is_file()

    events = read_events(_journal_path(vault))
    assert not any(isinstance(e, VaultGitInitializedEvent) for e in events)


def test_wiki_init_no_git_matches_default_tree(tmp_path: Path) -> None:
    """Contract test #3 — default vs ``--no-git`` produce the same tree.

    Two-run comparison inside the same test: hash every file (excluding
    ``.git/``) and assert equality. Journal events match on
    ``(type, by)`` modulo ``VaultGitInitializedEvent``, which appears
    only in the default run.
    """

    # Both vaults share the same basename so the {vault_name}
    # interpolation produces identical bytes — anything else would
    # diff in `.gitignore`, `AGENTS.md`, `CORE.md`, etc.
    default_vault = tmp_path / "default" / "vault"
    no_git_vault = tmp_path / "no-git" / "vault"

    assert main(["init", str(default_vault), "--recipe", "personal"]) == 0
    assert main(["init", str(no_git_vault), "--recipe", "personal", "--no-git"]) == 0

    # Journal content varies (timestamps, plus the git event); compare
    # the file separately. Every other file should be byte-identical.
    default_tree = _walk_files(default_vault, skip={".git", ".wiki.journal"})
    no_git_tree = _walk_files(no_git_vault, skip={".git", ".wiki.journal"})
    assert default_tree == no_git_tree

    # Journal events align on (type, by) modulo the git event.
    default_events = read_events(_journal_path(default_vault))
    no_git_events = read_events(_journal_path(no_git_vault))
    default_kinds = [_event_kind(e) for e in default_events]
    no_git_kinds = [_event_kind(e) for e in no_git_events]

    # Strip the one git event from the default run.
    stripped = [k for k in default_kinds if k[0] != "vault.git_initialized"]
    assert stripped == no_git_kinds

    # And the git event appears exactly once in default.
    assert sum(1 for k in default_kinds if k[0] == "vault.git_initialized") == 1


def test_wiki_init_refuses_when_git_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Contract test #4 — ``git`` missing on ``$PATH`` refuses before mutation."""

    original_which = shutil.which

    def _which(name: str, mode: int = os.F_OK | os.X_OK, path: str | None = None) -> str | None:
        if name == "git":
            return None
        return original_which(name, mode, path)

    # ``cli.py`` calls ``shutil.which("git")`` directly; patching
    # the ``shutil`` module's attribute reaches that call site.
    monkeypatch.setattr(shutil, "which", _which)

    vault = tmp_path / "vault"
    exit_code = main(["init", str(vault), "--recipe", "personal"])

    assert exit_code == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "git" in err.lower()
    assert "--no-git" in err

    # Target untouched (no directory, no journal).
    assert not vault.exists()


def test_wiki_init_empty_check_fires_before_git_pre_flight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Contract test #5 — non-empty target refuses before the git check.

    With both conditions failing, the user must see the "not empty"
    refusal, not a misleading "git missing" one. Pins the order
    documented in spec §Behavior step 1-2.
    """

    original_which = shutil.which

    def _which(name: str, mode: int = os.F_OK | os.X_OK, path: str | None = None) -> str | None:
        if name == "git":
            return None
        return original_which(name, mode, path)

    # ``cli.py`` calls ``shutil.which("git")`` directly; patching
    # the ``shutil`` module's attribute reaches that call site.
    monkeypatch.setattr(shutil, "which", _which)

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "stray.md").write_text("user content", encoding="utf-8")

    exit_code = main(["init", str(vault), "--recipe", "personal"])

    assert exit_code == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "not empty" in err
    # The git refusal must NOT fire — its message would mention --no-git.
    assert "--no-git" not in err


def test_wiki_init_surfaces_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Contract test #6 — commit failure surfaces stderr + the config hint.

    Forces failure by stripping git config; asserts ``VaultGitInitializedEvent``
    IS journaled (append-before-stage ordering) and ``.git/`` exists
    with no commits.
    """

    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)

    vault = tmp_path / "vault"
    exit_code = main(["init", str(vault), "--recipe", "personal"])

    assert exit_code == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "pass --no-git to skip git initialization" in err

    # `.git/` was created by `git init` but holds no commits.
    assert (vault / ".git").is_dir()
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=vault,
        capture_output=True,
        text=True,
    )
    assert log.returncode != 0

    # Event IS journaled per the append-before-stage ordering.
    events = read_events(_journal_path(vault))
    assert any(isinstance(e, VaultGitInitializedEvent) for e in events)

    # The non-git vault content was rendered.
    assert (vault / "AGENTS.md").is_file()


def test_wiki_init_surfaces_init_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Contract test #7 — init failure surfaces stderr without the kit hint.

    Pre-creating ``target/.git`` as a file would trip the empty-target
    refusal before any state mutation, so this test takes the other
    failure mode the spec names: a broken ``git`` wrapper. A shim
    binary on the test's ``$PATH`` exits non-zero, so ``shutil.which``
    finds it (pre-flight passes) and ``subprocess.run(["git", "init"],
    ...)`` runs the shim and fails.

    The vault is rendered (steps 4-5 completed) but ``.git/`` doesn't
    land and no ``VaultGitInitializedEvent`` is journaled (the event
    is appended only after a successful ``git init``).
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

    vault = tmp_path / "vault"
    exit_code = main(["init", str(vault), "--recipe", "personal"])

    assert exit_code == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    # Negative anchor: the kit-authored hint is the load-bearing
    # substring (spec §Error cases). Git's own stderr may contain
    # incidental config-related words on other failure modes; the kit
    # promises only that THIS substring is absent.
    assert "pass --no-git to skip git initialization" not in err

    # No `.git/` — the broken shim never created it.
    assert not (vault / ".git").exists()
    # Vault content was rendered before initialize_git ran.
    assert (vault / "AGENTS.md").is_file()

    # No git-event journaled.
    events = read_events(_journal_path(vault))
    assert any(isinstance(e, VaultInitEvent) for e in events)
    assert not any(isinstance(e, VaultGitInitializedEvent) for e in events)


def test_wiki_init_initial_commit_excludes_proposed_sidecars(tmp_path: Path) -> None:
    """Contract test #9 — initial commit includes ``.gitignore``, excludes ``*.proposed``.

    Pins the "install before git" ordering: ``install_primitives``
    (which renders ``.gitignore``) finishes before ``git add -A`` runs.
    """

    vault = tmp_path / "vault"
    assert main(["init", str(vault), "--recipe", "personal"]) == 0

    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = tree.stdout.splitlines()
    assert ".gitignore" in tracked
    proposed = [p for p in tracked if p.endswith(".proposed")]
    assert proposed == []


def test_wiki_doctor_clean_after_git_init(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Contract test #11 — ``wiki doctor`` reports clean on both vault shapes.

    Runs `wiki init` (default) and `wiki init --no-git` against fresh
    targets, then `wiki doctor` against each. Both must exit 0 AND
    produce no issue output on stdout — a doctor regression that
    returned 0 while printing spurious "drift" or "orphan" lines
    would slip past an exit-code-only check, so we pin both. On the
    default-flag run, also asserts `git status --porcelain` is empty
    — pins the "initial commit captures the full kit shape; nothing
    landed after" invariant.

    Doctor needs no code change for this PR: the orphan check derives
    owned-directory roots from journaled paths, and `.git/` is never
    journaled, so it can't surface as orphan.
    """

    default_vault = tmp_path / "default" / "vault"
    no_git_vault = tmp_path / "no-git" / "vault"

    assert main(["init", str(default_vault), "--recipe", "personal"]) == 0
    assert main(["init", str(no_git_vault), "--recipe", "personal", "--no-git"]) == 0

    # Flush any stdout from the init runs so capsys only catches doctor.
    capsys.readouterr()

    # `wiki doctor` operates on Path.cwd(); chdir into each vault.
    monkeypatch.chdir(default_vault)
    assert main(["doctor"]) == 0
    default_out = capsys.readouterr().out
    assert default_out == "", f"doctor printed issues on a clean default vault: {default_out!r}"

    monkeypatch.chdir(no_git_vault)
    assert main(["doctor"]) == 0
    no_git_out = capsys.readouterr().out
    assert no_git_out == "", f"doctor printed issues on a clean --no-git vault: {no_git_out!r}"

    # On the default run, the kit's initial commit captured everything —
    # nothing should be staged or untracked afterward.
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=default_vault,
        check=True,
        capture_output=True,
        text=True,
    )
    assert porcelain.stdout == ""


def test_rendered_gitignore_load_bearing_invariants(tmp_path: Path) -> None:
    """Contract test #10 — rendered ``.gitignore`` carries the load-bearing patterns.

    Asserts only the two invariants this spec depends on: contains
    ``*.proposed`` (so the commit-excludes-sidecars test has something
    to anchor on) and contains no unsubstituted ``{vault_name}``
    (``.gitignore`` is in ``render.INTERPOLATED_FILES``).
    """

    vault = tmp_path / "vault"
    assert main(["init", str(vault), "--recipe", "personal", "--no-git"]) == 0

    rendered = (vault / ".gitignore").read_text(encoding="utf-8")
    assert "*.proposed" in rendered
    assert "{vault_name}" not in rendered
