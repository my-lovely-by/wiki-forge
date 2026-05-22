"""Tutorial-drift gate (RFC-0001 Task 21).

Walks the published markdown for tutorial 1, tutorial 2, and the
resolve-a-conflict how-to. Each tutorial's executable `$`-prefixed
lines (inside ``` ```bash ``` fences) are concatenated into a single
``bash -c`` invocation with ``set -euo pipefail`` so shell state (cwd,
env vars) persists across lines — matching spec
``docs/specs/task-21-examples-tutorials/spec.md`` §Behavior
"Shell-state continuity across `$` lines".

The how-to substitutes ``<repo-root>`` in its ``cp -R`` step with the
test's REPO_ROOT before executing; AC5 also asserts on journal
event shapes after step 1 and step 5.

No ``ANTHROPIC_API_KEY``/``PERPLEXITY_API_KEY``/``GEMINI_API_KEY`` is
forwarded into the subprocess; the test fails fast if any is set
in the runner env so a developer who happens to have a key locally
doesn't get a different result than CI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

if shutil.which("wiki") is None:
    pytest.skip(
        "The tutorial gate requires the `wiki` CLI on PATH (install via "
        "`pip install -e '.[dev]'` from the repo root).",
        allow_module_level=True,
    )

REPO_ROOT = Path(__file__).resolve().parents[2]

TUTORIAL_1 = REPO_ROOT / "docs/guides/tutorials/tutorial-1-first-vault.md"
TUTORIAL_2 = REPO_ROOT / "docs/guides/tutorials/tutorial-2-work-os-walkthrough.md"
HOWTO_CONFLICT = REPO_ROOT / "docs/guides/how-to/resolve-a-conflict.md"

ENV_WHITELIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "SSL_CERT_FILE",
    "VIRTUAL_ENV",
)
ENV_BLACKLIST = (
    "ANTHROPIC_API_KEY",
    "PERPLEXITY_API_KEY",
    "GEMINI_API_KEY",
)


# ---------------------------------------------------------------------------
# Parser — load-bearing per spec §Behavior "Fence and prefix rules"
# ---------------------------------------------------------------------------


def _iter_bash_fence_lines(md_path: Path) -> list[tuple[int, str]]:
    """Return ``(line_number, raw_line)`` for every line inside a ```bash``` fence.

    Only fences whose opening info-string is exactly ``bash`` count;
    any other info-string (``sh``, ``text``, none, etc.) is ignored.
    Lines outside any fence are ignored regardless of prefix.
    """

    out: list[tuple[int, str]] = []
    inside_bash = False
    for i, line in enumerate(md_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            info = stripped[3:].strip()
            if not inside_bash and info == "bash":
                inside_bash = True
            elif inside_bash and info == "":
                inside_bash = False
            # any other ``` toggling (e.g. opening a non-bash fence) is
            # a no-op as far as our gate is concerned.
            continue
        if inside_bash:
            out.append((i, line))
    return out


def iter_executable_lines(md_path: Path) -> list[tuple[int, str]]:
    """Return ``(line_number, command)`` pairs for each ``$ ``-prefixed line."""

    return [(n, line[2:]) for (n, line) in _iter_bash_fence_lines(md_path) if line.startswith("$ ")]


def iter_claude_prompt_lines(md_path: Path) -> list[int]:
    """Return line numbers of ``> ``-prefixed Claude-prompt lines."""

    return [n for (n, line) in _iter_bash_fence_lines(md_path) if line.startswith("> ")]


# ---------------------------------------------------------------------------
# Runner — concatenate `$`-lines into one bash -c, pin env, pin cwd
# ---------------------------------------------------------------------------


def _check_runner_env() -> None:
    """Fail loudly if a blacklisted API key is set in the runner env.

    A developer with ``ANTHROPIC_API_KEY`` exported would otherwise see
    different behavior from CI — the gate is supposed to prove tutorials
    work without any LLM credentials.
    """

    leaked = [k for k in ENV_BLACKLIST if os.environ.get(k)]
    if leaked:
        pytest.fail(
            "Refusing to run tutorial gate with blacklisted env vars set: "
            f"{leaked}. Unset them or run in a clean shell."
        )


def _safe_env(home: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in ENV_WHITELIST if k in os.environ and k != "HOME"}
    env["HOME"] = str(home)
    return env


def _seed_git_identity(home: Path) -> None:
    """Drop a minimal ``~/.gitconfig`` into the tutorial's isolated HOME.

    The tutorials' opening ``wiki init`` command initializes git and
    makes one initial commit by default (see
    ``docs/specs/wiki-init-git/spec.md``); ``git commit`` needs a
    ``user.name`` / ``user.email`` to attribute the commit to. The
    real-user path is "run `git config --global ...` once"; the test
    sandbox bypasses that one-time setup so the tutorial's commands
    can run unmodified.
    """

    (home / ".gitconfig").write_text(
        "[user]\n\tname = Tutorial Test\n\temail = tutorial-test@example.com\n",
        encoding="utf-8",
    )


def _run_tutorial_script(
    md_path: Path,
    cwd: Path,
    home: Path,
    substitutions: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    _check_runner_env()
    commands = [cmd for (_, cmd) in iter_executable_lines(md_path)]
    if substitutions:
        commands = [_substitute(cmd, substitutions) for cmd in commands]
    script = "set -euo pipefail\n" + "\n".join(commands) + "\n"
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd),
        env=_safe_env(home),
        capture_output=True,
        check=False,
    )


def _substitute(cmd: str, subs: dict[str, str]) -> str:
    for placeholder, value in subs.items():
        cmd = cmd.replace(placeholder, value)
    return cmd


def _fmt_failure(md_path: Path, proc: subprocess.CompletedProcess[bytes]) -> str:
    return (
        f"Tutorial {md_path.name} failed (exit {proc.returncode}).\n"
        f"--- stdout ---\n{proc.stdout.decode(errors='replace')}\n"
        f"--- stderr ---\n{proc.stderr.decode(errors='replace')}"
    )


# ---------------------------------------------------------------------------
# Tutorial 1 (AC3 / construction test #6)
# ---------------------------------------------------------------------------


def test_tutorial_1_runs_end_to_end(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_git_identity(home)
    work = tmp_path / "work"
    work.mkdir()
    proc = _run_tutorial_script(TUTORIAL_1, cwd=work, home=home)
    assert proc.returncode == 0, _fmt_failure(TUTORIAL_1, proc)


# ---------------------------------------------------------------------------
# Tutorial 2 (AC4 / construction test #7)
# ---------------------------------------------------------------------------


def test_tutorial_2_runs_end_to_end(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_git_identity(home)
    work = tmp_path / "work"
    work.mkdir()
    proc = _run_tutorial_script(TUTORIAL_2, cwd=work, home=home)
    assert proc.returncode == 0, _fmt_failure(TUTORIAL_2, proc)


# ---------------------------------------------------------------------------
# Resolve-a-conflict how-to (AC5 / construction test #8)
# ---------------------------------------------------------------------------


def test_resolve_a_conflict_runs_end_to_end(tmp_path: Path) -> None:
    from llm_wiki_kit.journal import read_events
    from llm_wiki_kit.models import PageConflictResolvedEvent, PageProposalEvent

    home = tmp_path / "home"
    home.mkdir()
    _seed_git_identity(home)
    work = tmp_path / "work"
    work.mkdir()
    # The how-to's literal text writes to `/tmp/conflict-demo`. The test
    # tolerates a leftover from a Ctrl-C'd prior run because the how-to's
    # step 1 begins with `rm -rf /tmp/conflict-demo`, but we still clean
    # up after ourselves so back-to-back runs (and any future
    # pytest-xdist enablement that doesn't share `/tmp`) leave no scratch
    # behind.
    demo_path = Path("/tmp/conflict-demo")
    try:
        proc = _run_tutorial_script(
            HOWTO_CONFLICT,
            cwd=work,
            home=home,
            substitutions={"<repo-root>": str(REPO_ROOT)},
        )
        assert proc.returncode == 0, _fmt_failure(HOWTO_CONFLICT, proc)

        # AC5: the how-to's `cp -R` lands the vault at /tmp/conflict-demo.
        # After step 5's `wiki resolve --accept`, the journal carries both
        # a PageProposalEvent (from the committed conflict-pending vault)
        # and a PageConflictResolvedEvent (appended by resolve).
        journal = demo_path / ".wiki.journal" / "journal.jsonl"
        assert journal.is_file(), "how-to should produce /tmp/conflict-demo journal"
        events = list(read_events(journal))
        assert any(isinstance(e, PageProposalEvent) for e in events), (
            "expected PageProposalEvent from the conflict-pending baseline"
        )
        assert any(isinstance(e, PageConflictResolvedEvent) for e in events), (
            "expected PageConflictResolvedEvent after `wiki resolve --accept`"
        )
    finally:
        import shutil as _shutil

        _shutil.rmtree(demo_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Two-surface block counts and positions (AC10 / construction test #9)
# ---------------------------------------------------------------------------


def _first_index(md_path: Path, prefix: str) -> int:
    """Return the line index of the first executable line whose command starts with ``prefix``."""

    for line_no, cmd in iter_executable_lines(md_path):
        if cmd.startswith(prefix):
            return line_no
    raise AssertionError(f"{md_path.name} has no `$ {prefix}` line")


def test_tutorial_1_claude_prompts_follow_dispatches() -> None:
    """Tutorial 1: at least one `>` line at a strictly greater line index
    than the first `$ wiki ingest`, and another at a strictly greater index
    than the first `$ wiki run`.
    """

    ingest_idx = _first_index(TUTORIAL_1, "wiki ingest")
    run_idx = _first_index(TUTORIAL_1, "wiki run")
    prompt_indices = iter_claude_prompt_lines(TUTORIAL_1)
    assert any(i > ingest_idx for i in prompt_indices), (
        f"tutorial 1 has no `>` line after the first `$ wiki ingest` at line {ingest_idx}"
    )
    assert any(i > run_idx for i in prompt_indices), (
        f"tutorial 1 has no `>` line after the first `$ wiki run` at line {run_idx}"
    )


def test_tutorial_2_claude_prompt_follows_ingest() -> None:
    """Tutorial 2: at least one `>` line at a strictly greater line index
    than the first `$ wiki ingest --as stakeholder-update ` line.
    """

    ingest_idx = _first_index(TUTORIAL_2, "wiki ingest --as stakeholder-update ")
    prompt_indices = iter_claude_prompt_lines(TUTORIAL_2)
    assert any(i > ingest_idx for i in prompt_indices), (
        f"tutorial 2 has no `>` line after the first stakeholder-update ingest at line {ingest_idx}"
    )


def test_howto_has_no_claude_prompts() -> None:
    """The how-to is the without-Claude walk: zero `>` lines inside `bash` fences."""

    assert len(iter_claude_prompt_lines(HOWTO_CONFLICT)) == 0
