"""Integration tests for ``wiki run --exec`` CLI wiring (RFC-0003).

Drives ``python -m llm_wiki_kit`` as a subprocess against a real
tmp_path vault, asserting on stdout / stderr / exit code / journal
state. The unit-level orchestrator tests live in
``tests/unit/test_run_exec.py``; this file covers what the unit
tests can't — the argparse seam, environment-variable plumbing, and
CT-9 byte-identity for the non-``--exec`` flow.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import UTC
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# The subprocess `wiki run` will discover the kit via the lazy
# `_resolve_kit_root` fallback to the repo source tree, so we don't
# need to synthesize a kit fixture here — the real `weekly-digest`
# contract ships at `templates/operations/weekly-digest/`.


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    # Use the kit's CLI to journal a minimal state, then we hand-edit
    # to install weekly-digest. Simpler: hand-build the journal,
    # mirroring test_run_exec.py's fixture.
    from datetime import datetime

    from llm_wiki_kit.journal import append_event
    from llm_wiki_kit.models import PrimitiveInstallEvent, VaultInitEvent

    journal_dir = vault / ".wiki.journal"
    journal_dir.mkdir()
    journal_path = journal_dir / "journal.jsonl"
    now = datetime(2026, 5, 21, 9, 0, 0, tzinfo=UTC)
    append_event(
        journal_path,
        VaultInitEvent(
            timestamp=now,
            by="wiki-init",
            vault_name="test-vault",
            recipe="minimal",
        ),
    )
    append_event(
        journal_path,
        PrimitiveInstallEvent(
            timestamp=now,
            by="wiki-init",
            primitive="weekly-digest",
            version="0.1.0",
        ),
    )
    skill_dir = vault / ".claude" / "skills" / "weekly-digest"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# weekly-digest", encoding="utf-8")
    return vault


def _make_stub_claude(tmp_path: Path, exit_code: int = 0, name: str = "claude") -> Path:
    """Create an executable stub `claude` that writes its argv to a file."""

    argv_log = tmp_path / "argv.json"
    script = tmp_path / f"{name}_impl.py"
    script.write_text(
        "import sys, json\n"
        f"open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv))\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / name
    wrapper.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR | stat.S_IXGRP)
    return wrapper


def _run_wiki(
    args: list[str],
    *,
    cwd: Path,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    base_env = os.environ.copy()
    if env_overrides:
        base_env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "llm_wiki_kit", *args],
        cwd=str(cwd),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_run_without_exec_is_byte_identical(tmp_path: Path) -> None:
    # CT-9: existing `wiki run weekly-digest` path is unchanged.
    vault = _make_vault(tmp_path)
    proc = _run_wiki(
        ["run", "weekly-digest", "--window=2026-W20"],
        cwd=vault,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Dispatched weekly-digest" in proc.stdout
    assert "window=2026-W20" in proc.stdout
    # No exec activity.
    log_dir = vault / ".wiki.journal" / "exec-logs"
    assert not log_dir.exists()


def test_cli_run_with_exec_happy_path(tmp_path: Path) -> None:
    # CT-1 via CLI.
    vault = _make_vault(tmp_path)
    claude = _make_stub_claude(tmp_path, exit_code=0)

    proc = _run_wiki(
        ["run", "--exec", "--claude-binary", str(claude), "weekly-digest", "--window=2026-W20"],
        cwd=vault,
        env_overrides={"WIKI_CLAUDE_BINARY": ""},
    )
    assert proc.returncode == 0, proc.stderr
    assert "Dispatched weekly-digest" in proc.stdout
    assert "Exec succeeded" in proc.stdout
    # The stub recorded an argv that matches ADR-0009.
    argv_log = tmp_path / "argv.json"
    assert argv_log.exists()
    argv = json.loads(argv_log.read_text(encoding="utf-8"))
    assert argv[1] == "-p"
    assert "--permission-mode" in argv
    assert "dontAsk" in argv
    assert "--output-format" in argv
    assert "json" in argv


def test_cli_run_exec_binary_missing_exits_with_error(tmp_path: Path) -> None:
    # CT-3 via CLI: WikiError surfaces as one-line stderr message.
    vault = _make_vault(tmp_path)
    proc = _run_wiki(
        ["run", "--exec", "weekly-digest", "--window=2026-W20"],
        cwd=vault,
        # Empty PATH except a minimal one — guarantees `claude` is not on PATH.
        env_overrides={"PATH": "/usr/bin:/bin", "WIKI_CLAUDE_BINARY": ""},
    )
    # Dispatch event was journaled before the error.
    journal_lines = (
        (vault / ".wiki.journal" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert any('"type":"operation.run"' in line for line in journal_lines)
    # The CLI surfaced a non-zero exit and a single-line stderr message.
    assert proc.returncode != 0
    assert "claude" in proc.stderr.lower()


def test_cli_run_exec_set_but_invalid_override_fails_fast(tmp_path: Path) -> None:
    # CT-3a: a set-but-invalid `--claude-binary` raises at the CLI's
    # pre-dispatch _locate_claude call. The journal must contain ZERO
    # `operation.*` events — neither operation.run nor
    # operation.exec_failed — because the failure is pre-dispatch.
    vault = _make_vault(tmp_path)
    not_executable = tmp_path / "not-claude"
    not_executable.write_text("not a real binary", encoding="utf-8")
    # File exists but is NOT marked executable.

    proc = _run_wiki(
        [
            "run",
            "--exec",
            "--claude-binary",
            str(not_executable),
            "weekly-digest",
            "--window=2026-W20",
        ],
        cwd=vault,
        # Pin every env var the CLI's pre-dispatch validators read to a
        # known-good value so this test exercises the `_locate_claude`
        # raise path specifically. Without this, a polluted CI host
        # with (say) a malformed WIKI_EXEC_TIMEOUT would also produce
        # a zero-event failure for an unrelated reason, and a
        # regression that moved `_locate_claude` to *after* `dispatch()`
        # would still pass those assertions. WIKI_EXEC_TIMEOUT and
        # WIKI_EXEC_LOG_RETENTION_DAYS take the CLI defaults (empty
        # string would fail the integer parse and raise before
        # `_locate_claude`); WIKI_EXEC_MAX_BUDGET_USD="" is the
        # documented empty sentinel — `_validate_max_budget` returns
        # `None` for it.
        env_overrides={
            "WIKI_CLAUDE_BINARY": "",
            "WIKI_EXEC_TIMEOUT": "1800",
            "WIKI_EXEC_LOG_RETENTION_DAYS": "30",
            "WIKI_EXEC_MAX_BUDGET_USD": "",
        },
    )
    assert proc.returncode != 0
    # Pin the specific raise path: the error must come from
    # `_locate_claude`, not from one of the env-var validators that run
    # earlier in `_cmd_run_exec`.
    assert "not an executable file" in proc.stderr
    # Zero operation.* events in the journal — pre-dispatch raise means
    # no event was journaled.
    journal_lines = (
        (vault / ".wiki.journal" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert not any('"type":"operation.run"' in line for line in journal_lines)
    assert not any('"type":"operation.exec_failed"' in line for line in journal_lines)


def test_cli_run_exec_invalid_args_skips_exec(tmp_path: Path) -> None:
    # CT-2 via CLI: invalid_args path skips exec, prints stderr, exits non-zero.
    vault = _make_vault(tmp_path)
    claude = _make_stub_claude(tmp_path, exit_code=0)

    proc = _run_wiki(
        ["run", "--exec", "--claude-binary", str(claude), "weekly-digest", "--frobnicate=x"],
        cwd=vault,
        env_overrides={"WIKI_CLAUDE_BINARY": ""},
    )
    assert proc.returncode != 0
    # The argv log was never written — the subprocess wasn't spawned.
    argv_log = tmp_path / "argv.json"
    assert not argv_log.exists()


def test_cli_run_exec_success_line_carries_exit_duration_log(tmp_path: Path) -> None:
    """Spec §"Happy path" step 5a: success line includes exit, duration, log."""

    vault = _make_vault(tmp_path)
    claude = _make_stub_claude(tmp_path, exit_code=0)
    proc = _run_wiki(
        ["run", "--exec", "--claude-binary", str(claude), "weekly-digest", "--window=2026-W20"],
        cwd=vault,
        env_overrides={"WIKI_CLAUDE_BINARY": ""},
    )
    assert proc.returncode == 0, proc.stderr
    # The line should contain the literal segments the spec promises.
    assert "Exec succeeded for weekly-digest" in proc.stdout
    assert "exit 0" in proc.stdout
    assert "log: .wiki.journal/exec-logs/" in proc.stdout


def test_cli_run_exec_rejects_negative_timeout(tmp_path: Path) -> None:
    """``WIKI_EXEC_TIMEOUT=-1`` raises ``WikiError`` at CLI start."""

    vault = _make_vault(tmp_path)
    claude = _make_stub_claude(tmp_path, exit_code=0)
    proc = _run_wiki(
        ["run", "--exec", "--claude-binary", str(claude), "weekly-digest", "--window=2026-W20"],
        cwd=vault,
        env_overrides={"WIKI_CLAUDE_BINARY": "", "WIKI_EXEC_TIMEOUT": "-1"},
    )
    assert proc.returncode != 0
    assert "WIKI_EXEC_TIMEOUT" in proc.stderr
    # The dispatch event was NOT journaled — the CLI rejects the timeout
    # before invoking dispatch_and_exec at all.
    journal_lines = (
        (vault / ".wiki.journal" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert not any('"type":"operation.run"' in line for line in journal_lines)
