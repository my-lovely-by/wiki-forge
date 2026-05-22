"""Thin subprocess wrapper for the ``wiki init`` git-initialization phase.

Spec: ``docs/specs/wiki-init-git/spec.md``.

The module exports one public function, :func:`initialize_git`, which
the CLI's ``_cmd_init`` calls after ``install_primitives`` returns
inside the open ``journal.use_journal_cache`` scope. The function:

1. Short-circuits if ``target/.git/`` already exists — the kit treats
   a pre-existing repo as user territory and never modifies it.
2. Runs ``git init`` via :mod:`subprocess`, argv-list form,
   ``shell=False``.
3. Recomputes ``now_git`` and appends :class:`VaultGitInitializedEvent`
   to the journal **before** staging, so the journaled line is captured
   by the initial commit's tree (the load-bearing append-before-stage
   ordering — see spec §Behavior step 6 and §Invariants).
4. Runs ``git add -A`` then ``git -c commit.gpgsign=false commit -m
   "Initialize wiki vault from <recipe> recipe"``. GPG signing is
   disabled inline so a user with a misconfigured signing key still
   gets a working vault; subsequent user-made commits honor their
   normal config.

Failure surfaces:

* ``git init`` non-zero: raise :class:`WikiError` with git's stderr,
  no kit-authored hint. The event has not yet been appended at this
  point, so the journal stays consistent.
* ``git commit`` non-zero (typically missing global ``user.name`` /
  ``user.email``): raise :class:`WikiError` with git's stderr plus
  the literal substring ``pass --no-git to skip git initialization``
  (load-bearing — the integration test's negative assertion anchors
  on this string). The event IS journaled at this point — recovery
  is the user running ``git add -A && git commit`` manually.

No ``git rev-parse`` captures: :class:`VaultGitInitializedEvent`
deliberately carries no SHA or branch (see spec §Outputs).
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.journal import append_event
from llm_wiki_kit.models import VaultGitInitializedEvent

# Load-bearing anchor for the integration tests:
# ``test_initialize_git_surfaces_init_failure`` asserts this substring
# is *absent* from a `git init` failure message; the commit-failure
# branch asserts it is *present*. Pin the literal so a future
# error-message refactor catches the contract.
_NO_GIT_HINT = "pass --no-git to skip git initialization"


def initialize_git(
    target: Path,
    *,
    recipe_name: str,
    journal_path: Path,
    _now: datetime,
) -> None:
    """Initialize a git repository in ``target`` and make one initial commit.

    Skips silently when ``target/.git/`` already exists; appends a
    :class:`VaultGitInitializedEvent` to the journal between ``git
    init`` and ``git commit`` (the append-before-stage ordering pinned
    by ``docs/specs/wiki-init-git/spec.md`` §Behavior step 6).

    The ``_now`` argument is accepted for call-site symmetry with
    ``_cmd_init``'s ``now`` (other init-time helpers consume it), but
    :func:`initialize_git` recomputes its own timestamp immediately
    before the journal append so a long ``git init`` on slow disks
    doesn't journal a stale timestamp (precedent: safe-write-ordering
    spec's adopt fast-path). The underscore prefix signals "do not
    rely on this value reaching the journal."

    Raises :class:`WikiError` when ``git init`` or ``git commit``
    returns a non-zero exit code.
    """

    # The "target already contains ``.git/``" variant (spec §Behavior)
    # covers both the directory shape (a normal repo) AND the gitfile
    # shape (a regular file containing ``gitdir: <path>``, used by
    # worktrees and submodules). Treating either as "git territory" is
    # the safer default: ``.exists()`` short-circuits both. A plain
    # non-git file named ``.git`` is rare; the empty-target refusal
    # in ``_cmd_init`` blocks it from the CLI today, and a direct
    # caller passing a degenerate state still gets a clean no-op.
    if (target / ".git").exists():
        return

    _run_git(["git", "init"], cwd=target, failure_prefix="git init failed", hint=False)

    now_git = datetime.now(UTC)
    append_event(
        journal_path,
        VaultGitInitializedEvent(timestamp=now_git, by="wiki-init"),
    )

    # ``git add`` failure is bucketed with `git init` failure for
    # error-shape purposes: the most plausible causes (disk full,
    # permission, filesystem oddity) aren't config-shaped, so the
    # `pass --no-git` hint would be misleading.
    _run_git(["git", "add", "-A"], cwd=target, failure_prefix="git add failed", hint=False)
    _run_git(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            f"Initialize wiki vault from {recipe_name} recipe",
        ],
        cwd=target,
        failure_prefix="git commit failed",
        hint=True,
    )


def _run_git(args: list[str], *, cwd: Path, failure_prefix: str, hint: bool) -> None:
    """Run a git subprocess; raise :class:`WikiError` on non-zero exit.

    The argv-list form with ``shell=False`` is load-bearing: recipe
    names flow into the commit-message argv as a single element, so
    quoting concerns are git's, not the kit's (spec §Invariants
    "All subprocess invocations are ``shell=False``").

    When ``hint`` is true, the error message appends the kit-authored
    ``pass --no-git`` recovery hint. The init-failure path passes
    ``hint=False`` because the failure shape isn't config-driven —
    the user typically can't recover by setting git config. The
    integration tests anchor on this distinction.
    """

    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return

    stderr = (result.stderr or "").rstrip()
    parts = [f"{failure_prefix}: {stderr}" if stderr else failure_prefix]
    if hint:
        parts.append(
            'set `git config --global user.name "Your Name"` and '
            '`git config --global user.email "you@example.com"`, then re-run; '
            f"or {_NO_GIT_HINT}."
        )
    raise WikiError("\n".join(parts))
