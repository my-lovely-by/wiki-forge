"""Tests for ``wiki run --exec`` helpers (RFC-0003 + ADR-0009).

Contract-test coverage from ``docs/specs/wiki-run-exec/spec.md`` lives
here at the helper layer (slice 2 of the implementation). The
orchestrator ``dispatch_and_exec`` and the CLI wiring land in
follow-on slices with their own tests.

Pure-function helpers:

- ``_locate_claude`` — binary resolution order
- ``_locate_skill`` — SKILL path resolution with fallback (CT-16)
- ``_walk_proposed_sidecars`` — bounded walk (CT-6, CT-6a, CT-6b)
- ``_read_obsidianignore`` — ignore-file subset parser
- ``_validate_max_budget`` — env-var shape check (CT-13a helper)
- ``_build_prompt`` — prompt template (CT-13 substring assertion)
- ``_build_argv`` — ADR-0009 argv shape (CT-13)
- ``_rotate_logs`` — mtime-based deletion (CT-15)
- ``_run_subprocess`` — returncode, timeout, stderr_tail bounding
- ``_render_failure_file`` — two templates by reason (CT-11)
- ``_append_failure_event`` — reserved-reason guard
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.journal import append_event, read_events
from llm_wiki_kit.models import (
    OperationContract,
    OperationExecFailedEvent,
    OperationInputSpec,
    OperationRunEvent,
    PageWriteEvent,
    PrimitiveInstallEvent,
    VaultInitEvent,
)
from llm_wiki_kit.run import (
    EXEC_VEHICLE,
    _append_failure_event,
    _build_argv,
    _build_prompt,
    _locate_claude,
    _locate_skill,
    _read_obsidianignore,
    _render_failure_file,
    _rotate_logs,
    _run_subprocess,
    _validate_max_budget,
    _walk_proposed_sidecars,
    dispatch_and_exec,
)

NOW = datetime(2026, 5, 21, 9, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixture: a vault with a journal, no kit needed
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / ".wiki.journal").mkdir(parents=True)
    journal_path = v / ".wiki.journal" / "journal.jsonl"
    append_event(
        journal_path,
        VaultInitEvent(
            timestamp=NOW,
            by="wiki-init",
            vault_name="test-vault",
            recipe="minimal",
        ),
    )
    return v


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _make_executable(tmp_path: Path, name: str = "claude") -> Path:
    """Create a stub executable file under ``tmp_path``."""

    binary = tmp_path / name
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR | stat.S_IXGRP)
    return binary


def _make_contract(name: str, skill: str | None = None) -> OperationContract:
    return OperationContract(
        name=name,
        description="test contract",
        period=None,
        skill=skill,
        inputs={"window": OperationInputSpec(type="iso_week")},
    )


# ---------------------------------------------------------------------------
# _locate_claude — resolution order
# ---------------------------------------------------------------------------


def test_locate_claude_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WIKI_CLAUDE_BINARY", raising=False)
    binary = _make_executable(tmp_path, "claude")
    other = _make_executable(tmp_path, "other-claude")
    # Even with env var set, override wins.
    monkeypatch.setenv("WIKI_CLAUDE_BINARY", str(binary))
    resolved = _locate_claude(override=other)
    assert resolved == other


def test_locate_claude_env_var_when_no_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _make_executable(tmp_path)
    monkeypatch.setenv("WIKI_CLAUDE_BINARY", str(binary))
    monkeypatch.setattr("shutil.which", lambda _: "/should/not/be/used")
    resolved = _locate_claude(override=None)
    assert resolved == binary


def test_locate_claude_which_when_no_override_or_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WIKI_CLAUDE_BINARY", raising=False)
    binary = _make_executable(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: str(binary) if name == "claude" else None)
    resolved = _locate_claude(override=None)
    assert resolved == binary


def test_locate_claude_returns_none_when_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WIKI_CLAUDE_BINARY", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert _locate_claude(override=None) is None


def test_locate_claude_override_must_be_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WIKI_CLAUDE_BINARY", raising=False)
    not_executable = tmp_path / "claude"
    not_executable.write_text("hello", encoding="utf-8")
    with pytest.raises(WikiError, match="not an executable file"):
        _locate_claude(override=not_executable)


def test_locate_claude_env_var_must_be_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_executable = tmp_path / "claude"
    not_executable.write_text("hello", encoding="utf-8")
    monkeypatch.setenv("WIKI_CLAUDE_BINARY", str(not_executable))
    with pytest.raises(WikiError, match="WIKI_CLAUDE_BINARY"):
        _locate_claude(override=None)


# ---------------------------------------------------------------------------
# _locate_skill — explicit override + fallback to operation name (CT-16)
# ---------------------------------------------------------------------------


def test_locate_skill_uses_explicit_override(tmp_path: Path) -> None:
    override = tmp_path / "custom" / "SKILL.md"
    override.parent.mkdir(parents=True)
    override.write_text("# skill", encoding="utf-8")
    contract = _make_contract("weekly-digest", skill="weekly-digest")
    resolved = _locate_skill(skill_path_override=override, contract=contract, vault_root=tmp_path)
    assert resolved == override


def test_locate_skill_default_uses_contract_skill_field(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    skill_dir = vault_root / ".claude" / "skills" / "weekly-digest"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# skill", encoding="utf-8")
    contract = _make_contract("weekly-digest", skill="weekly-digest")
    resolved = _locate_skill(skill_path_override=None, contract=contract, vault_root=vault_root)
    assert resolved == skill_file


def test_locate_skill_fallback_to_operation_when_contract_skill_absent(
    tmp_path: Path,
) -> None:
    # CT-16: contract.skill is None → falls back to operation name.
    vault_root = tmp_path / "vault"
    skill_dir = vault_root / ".claude" / "skills" / "no-skill-op"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# skill", encoding="utf-8")
    contract = _make_contract("no-skill-op", skill=None)
    resolved = _locate_skill(skill_path_override=None, contract=contract, vault_root=vault_root)
    assert resolved == skill_file


def test_locate_skill_fallback_to_operation_when_contract_skill_empty_string(
    tmp_path: Path,
) -> None:
    vault_root = tmp_path / "vault"
    skill_dir = vault_root / ".claude" / "skills" / "empty-skill-op"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# skill", encoding="utf-8")
    contract = _make_contract("empty-skill-op", skill="")
    resolved = _locate_skill(skill_path_override=None, contract=contract, vault_root=vault_root)
    assert resolved == skill_file


def test_locate_skill_raises_when_default_missing(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    contract = _make_contract("weekly-digest", skill="weekly-digest")
    with pytest.raises(WikiError, match="SKILL file not found"):
        _locate_skill(skill_path_override=None, contract=contract, vault_root=vault_root)


def test_locate_skill_raises_when_override_missing(tmp_path: Path) -> None:
    override = tmp_path / "missing" / "SKILL.md"
    contract = _make_contract("weekly-digest", skill="weekly-digest")
    with pytest.raises(WikiError, match="SKILL file not found"):
        _locate_skill(skill_path_override=override, contract=contract, vault_root=tmp_path)


# ---------------------------------------------------------------------------
# _read_obsidianignore — exact-prefix lines
# ---------------------------------------------------------------------------


def test_read_obsidianignore_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert _read_obsidianignore(tmp_path) == ()


def test_read_obsidianignore_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    (tmp_path / ".obsidianignore").write_text(
        "# header\n\nignored-dir/\n  \n# another comment\nother-prefix\n",
        encoding="utf-8",
    )
    assert _read_obsidianignore(tmp_path) == ("ignored-dir/", "other-prefix")


def test_read_obsidianignore_skips_kit_proposed_regex(tmp_path: Path) -> None:
    # The kit emits a literal regex `\.proposed$` via _ensure_obsidianignore;
    # we don't want that to also affect the conflict walk.
    (tmp_path / ".obsidianignore").write_text("\\.proposed$\nuser/dir/\n", encoding="utf-8")
    assert _read_obsidianignore(tmp_path) == ("user/dir/",)


# ---------------------------------------------------------------------------
# _walk_proposed_sidecars — scope rules (CT-6, CT-6a, CT-6b)
# ---------------------------------------------------------------------------


def test_walk_proposed_finds_sidecar_in_content_dir(tmp_path: Path) -> None:
    (tmp_path / "wiki" / "notes").mkdir(parents=True)
    sidecar = tmp_path / "wiki" / "notes" / "foo.md.proposed"
    sidecar.write_text("body", encoding="utf-8")
    walk = _walk_proposed_sidecars(tmp_path)
    assert walk.paths == ["wiki/notes/foo.md.proposed"]
    assert walk.total == 1
    assert walk.over_cap is False


def test_walk_proposed_excludes_dot_prefixed_directories(tmp_path: Path) -> None:
    # CT-6a — dot-prefix excludes by the Included rule alone.
    for path_str in (
        ".wiki.journal/exec-logs/01.md.proposed",
        ".git/HEAD.proposed",
        ".obsidian/workspace.proposed",
        ".claude/skills/x.md.proposed",
    ):
        path = tmp_path / path_str
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("body", encoding="utf-8")
    # Plus one in-scope sidecar to prove the walk still finds non-excluded paths.
    (tmp_path / "wiki").mkdir()
    inscope = tmp_path / "wiki" / "x.md.proposed"
    inscope.write_text("body", encoding="utf-8")

    walk = _walk_proposed_sidecars(tmp_path)
    assert walk.paths == ["wiki/x.md.proposed"]


def test_walk_proposed_excludes_inbox_scheduled_failures(tmp_path: Path) -> None:
    # Explicit nested exclusion: kit's own scratch.
    (tmp_path / "inbox" / "scheduled-failures").mkdir(parents=True)
    scratch = tmp_path / "inbox" / "scheduled-failures" / "abc.md.proposed"
    scratch.write_text("body", encoding="utf-8")
    inbox_user = tmp_path / "inbox" / "user.md.proposed"
    inbox_user.write_text("body", encoding="utf-8")

    walk = _walk_proposed_sidecars(tmp_path)
    assert walk.paths == ["inbox/user.md.proposed"]


def test_walk_proposed_honors_obsidianignore(tmp_path: Path) -> None:
    # CT-6b
    (tmp_path / ".obsidianignore").write_text("attachments/\n", encoding="utf-8")
    (tmp_path / "attachments").mkdir()
    ignored = tmp_path / "attachments" / "x.md.proposed"
    ignored.write_text("body", encoding="utf-8")
    (tmp_path / "wiki").mkdir()
    inscope = tmp_path / "wiki" / "y.md.proposed"
    inscope.write_text("body", encoding="utf-8")

    walk = _walk_proposed_sidecars(tmp_path)
    assert walk.paths == ["wiki/y.md.proposed"]


def test_walk_proposed_obsidianignore_requires_segment_boundary(tmp_path: Path) -> None:
    # `att` must NOT match `attachments/x.md.proposed` — only `att/`
    # or `attachments/` (or the literal path) does.
    (tmp_path / ".obsidianignore").write_text("att\n", encoding="utf-8")
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "x.md.proposed").write_text("body", encoding="utf-8")
    walk = _walk_proposed_sidecars(tmp_path)
    assert walk.paths == ["attachments/x.md.proposed"]


def test_walk_proposed_caps_at_20_and_reports_total(tmp_path: Path) -> None:
    (tmp_path / "wiki").mkdir()
    for i in range(25):
        sidecar = tmp_path / "wiki" / f"file-{i:02d}.md.proposed"
        sidecar.write_text("body", encoding="utf-8")
    walk = _walk_proposed_sidecars(tmp_path)
    assert len(walk.paths) == 20
    assert walk.total == 25
    assert walk.over_cap is True


def test_walk_proposed_returns_empty_on_clean_vault(tmp_path: Path) -> None:
    (tmp_path / "wiki" / "notes").mkdir(parents=True)
    (tmp_path / "wiki" / "notes" / "foo.md").write_text("body", encoding="utf-8")
    walk = _walk_proposed_sidecars(tmp_path)
    assert walk.paths == []
    assert walk.total == 0


# ---------------------------------------------------------------------------
# _validate_max_budget — shape check (CT-13a helper)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["5", "5.00", "0", "0.50", "100000.123456"])
def test_validate_max_budget_accepts_valid_values(value: str) -> None:
    assert _validate_max_budget(value) == value


def test_validate_max_budget_returns_none_for_empty_and_none() -> None:
    assert _validate_max_budget(None) is None
    assert _validate_max_budget("") is None


@pytest.mark.parametrize(
    "value",
    [
        "not-a-number",
        "5; rm -rf ~",
        "5\n",
        "5\x00",
        "-1",
        "1e10",
        " 5 ",
        "5.5.5",
        ".5",
        "5.",
    ],
)
def test_validate_max_budget_rejects_invalid_values(value: str) -> None:
    with pytest.raises(WikiError, match="WIKI_EXEC_MAX_BUDGET_USD"):
        _validate_max_budget(value)


# ---------------------------------------------------------------------------
# _build_prompt — substring contract (CT-13)
# ---------------------------------------------------------------------------


def test_build_prompt_contains_dispatch_event_id_and_operation(tmp_path: Path) -> None:
    skill_path = tmp_path / ".claude" / "skills" / "weekly-digest" / "SKILL.md"
    prompt = _build_prompt(
        operation="weekly-digest",
        skill_path=skill_path,
        dispatch_event_id="0123456789ab",
    )
    assert "weekly-digest" in prompt
    assert "0123456789ab" in prompt
    assert str(skill_path) in prompt


# ---------------------------------------------------------------------------
# _build_argv — ADR-0009 exact shape (CT-13)
# ---------------------------------------------------------------------------


def test_build_argv_matches_adr_0009_shape(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    prompt = "run the operation"
    argv = _build_argv(
        claude_binary=binary,
        vault_root=vault_root,
        prompt=prompt,
        max_budget_usd=None,
    )
    assert argv == [
        str(binary),
        "-p",
        "--add-dir",
        str(vault_root),
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "json",
        prompt,
    ]


def test_build_argv_inserts_max_budget_when_set(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    argv = _build_argv(
        claude_binary=binary,
        vault_root=vault_root,
        prompt="x",
        max_budget_usd="5.00",
    )
    assert argv[-3:] == ["--max-budget-usd", "5.00", "x"]


def test_build_argv_never_emits_agent_at_v1(tmp_path: Path) -> None:
    # ADR-0010 deferred to v2 — v1's _build_argv never adds --agent.
    binary = _make_executable(tmp_path)
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    argv = _build_argv(
        claude_binary=binary,
        vault_root=vault_root,
        prompt="x",
        max_budget_usd=None,
    )
    assert "--agent" not in argv


# ---------------------------------------------------------------------------
# _rotate_logs — mtime-based deletion (CT-15)
# ---------------------------------------------------------------------------


def _set_mtime(path: Path, days_ago: int) -> None:
    when = (NOW - timedelta(days=days_ago)).timestamp()
    os.utime(path, (when, when))


def test_rotate_logs_deletes_old_files_keeps_fresh(tmp_path: Path) -> None:
    log_dir = tmp_path / ".wiki.journal" / "exec-logs"
    log_dir.mkdir(parents=True)
    old = log_dir / "old.log"
    fresh = log_dir / "fresh.log"
    old.write_text("body", encoding="utf-8")
    fresh.write_text("body", encoding="utf-8")
    _set_mtime(old, days_ago=31)
    _set_mtime(fresh, days_ago=29)

    _rotate_logs(vault_root=tmp_path, retention_days=30, now=NOW)

    assert not old.exists()
    assert fresh.exists()


def test_rotate_logs_no_op_when_retention_zero(tmp_path: Path) -> None:
    log_dir = tmp_path / ".wiki.journal" / "exec-logs"
    log_dir.mkdir(parents=True)
    old = log_dir / "ancient.log"
    old.write_text("body", encoding="utf-8")
    _set_mtime(old, days_ago=365)
    _rotate_logs(vault_root=tmp_path, retention_days=0, now=NOW)
    assert old.exists()


def test_rotate_logs_no_op_when_directory_missing(tmp_path: Path) -> None:
    # Should not raise.
    _rotate_logs(vault_root=tmp_path, retention_days=30, now=NOW)


def test_rotate_logs_only_touches_log_extension(tmp_path: Path) -> None:
    log_dir = tmp_path / ".wiki.journal" / "exec-logs"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "old.log"
    other_file = log_dir / "old.txt"
    log_file.write_text("body", encoding="utf-8")
    other_file.write_text("body", encoding="utf-8")
    _set_mtime(log_file, days_ago=31)
    _set_mtime(other_file, days_ago=31)

    _rotate_logs(vault_root=tmp_path, retention_days=30, now=NOW)

    assert not log_file.exists()
    assert other_file.exists()


# ---------------------------------------------------------------------------
# _run_subprocess — return codes, timeout, stderr_tail
# ---------------------------------------------------------------------------


def _make_python_stub(tmp_path: Path, script: str, name: str = "stub") -> Path:
    """Create a wrapper shell script that invokes Python inline."""

    python_bin = os.environ.get("PYTHON", "python3")
    script_file = tmp_path / f"{name}.py"
    script_file.write_text(script, encoding="utf-8")
    wrapper = tmp_path / name
    wrapper.write_text(
        f'#!/bin/sh\nexec "{python_bin}" "{script_file}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXUSR | stat.S_IXGRP)
    return wrapper


def test_run_subprocess_zero_exit(tmp_path: Path) -> None:
    stub = _make_python_stub(tmp_path, "import sys; sys.exit(0)")
    log_path = tmp_path / "log.log"
    result = _run_subprocess(
        argv=[str(stub)],
        cwd=tmp_path,
        log_path=log_path,
        timeout_seconds=10,
    )
    assert result.returncode == 0
    assert result.timed_out is False
    assert log_path.exists()


def test_run_subprocess_captures_stderr_tail(tmp_path: Path) -> None:
    stub = _make_python_stub(
        tmp_path,
        'import sys; sys.stderr.write("error line\\n"); sys.exit(137)',
    )
    log_path = tmp_path / "log.log"
    result = _run_subprocess(
        argv=[str(stub)],
        cwd=tmp_path,
        log_path=log_path,
        timeout_seconds=10,
    )
    assert result.returncode == 137
    assert "error line" in result.stderr_tail


def test_run_subprocess_stderr_tail_bounded_to_4kb(tmp_path: Path) -> None:
    # CT-12 (helper layer): 100 KB of stderr produces a 4 KB tail.
    stub = _make_python_stub(
        tmp_path,
        'import sys; sys.stderr.write("x" * 100000); sys.exit(1)',
    )
    log_path = tmp_path / "log.log"
    result = _run_subprocess(
        argv=[str(stub)],
        cwd=tmp_path,
        log_path=log_path,
        timeout_seconds=10,
    )
    assert result.returncode == 1
    # Tail is the last 4 KB.
    assert len(result.stderr_tail.encode("utf-8")) <= 4096


def test_run_subprocess_timeout(tmp_path: Path) -> None:
    # CT-8 (helper layer): subprocess sleeping past timeout returns exit_code=-2.
    stub = _make_python_stub(
        tmp_path,
        "import time; time.sleep(10)",
    )
    log_path = tmp_path / "log.log"
    result = _run_subprocess(
        argv=[str(stub)],
        cwd=tmp_path,
        log_path=log_path,
        timeout_seconds=1,
    )
    assert result.returncode == -2
    assert result.timed_out is True


# ---------------------------------------------------------------------------
# _render_failure_file — two templates by reason (CT-11)
# ---------------------------------------------------------------------------


def test_render_failure_file_non_zero_exit_template() -> None:
    body = _render_failure_file(
        operation="weekly-digest",
        dispatch_event_id="0123456789ab",
        dispatched_at=NOW,
        failed_at=NOW + timedelta(seconds=1798),
        reason="non-zero-exit",
        exit_code=137,
        stderr_tail="rate limit exceeded; retry after 60s",
        log_path=".wiki.journal/exec-logs/0123456789ab.log",
        conflict_sidecars=[],
    )
    assert "weekly-digest" in body
    assert "0123456789ab" in body
    assert "non-zero-exit" in body
    assert "137" in body
    assert ".wiki.journal/exec-logs/0123456789ab.log" in body
    assert "rate limit exceeded" in body
    assert "duration 1798s" in body


def test_render_failure_file_conflict_refused_template() -> None:
    body = _render_failure_file(
        operation="weekly-digest",
        dispatch_event_id="0123456789ab",
        dispatched_at=NOW,
        failed_at=NOW + timedelta(seconds=1),
        reason="conflict-refused",
        exit_code=-1,
        stderr_tail="",
        log_path=None,
        conflict_sidecars=["wiki/notes/foo.md.proposed", "wiki/food/bar.md.proposed"],
    )
    assert "Scheduled exec refused" in body
    assert "conflict-refused" in body
    assert "wiki/notes/foo.md.proposed" in body
    assert "wiki/food/bar.md.proposed" in body
    # No log link, no duration in this template.
    assert "exec-logs" not in body
    assert "duration" not in body


def test_render_failure_file_last_stderr_line_picks_last_non_empty() -> None:
    body = _render_failure_file(
        operation="op",
        dispatch_event_id="abcdef012345",
        dispatched_at=NOW,
        failed_at=NOW + timedelta(seconds=2),
        reason="non-zero-exit",
        exit_code=1,
        stderr_tail="first\n\nlast line\n\n",
        log_path="x.log",
        conflict_sidecars=[],
    )
    assert "Last non-empty stderr line:** `last line`" in body


# ---------------------------------------------------------------------------
# _append_failure_event — reserved-reason guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["non-zero-exit", "timeout", "conflict-refused"])
def test_append_failure_event_emits_supported_reasons(vault: Path, reason: str) -> None:
    event = _append_failure_event(
        journal_path=_journal_path(vault),
        now=NOW,
        operation="weekly-digest",
        dispatch_event_id="0123456789ab",
        exit_code=1 if reason != "conflict-refused" else -1,
        reason=reason,
        stderr_tail="oops",
    )
    assert event.reason == reason
    assert event.by == EXEC_VEHICLE
    # Round-trip through the journal so the discriminated-union dispatch
    # selects OperationExecFailedEvent.
    events = [
        e for e in read_events(_journal_path(vault)) if isinstance(e, OperationExecFailedEvent)
    ]
    assert len(events) == 1
    assert events[0].reason == reason


@pytest.mark.parametrize("reason", ["binary-missing", "skill-missing"])
def test_append_failure_event_rejects_reserved_reasons(vault: Path, reason: str) -> None:
    with pytest.raises(RuntimeError, match="reserved"):
        _append_failure_event(
            journal_path=_journal_path(vault),
            now=NOW,
            operation="weekly-digest",
            dispatch_event_id="0123456789ab",
            exit_code=-3,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Slice 3 — dispatch_and_exec orchestrator
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[2]

_WEEKLY_DIGEST_CONTRACT = """\
name: weekly-digest
description: Weekly digest test contract.
period: weekly
skill: weekly-digest
inputs:
  window:
    type: iso_week
outputs:
  digest:
    type: page
    path_pattern: outputs/digests/{window}.md
"""


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    templates = kit / "templates"
    templates.mkdir()
    op_dir = templates / "operations" / "weekly-digest"
    op_dir.mkdir(parents=True)
    (op_dir / "contract.yaml").write_text(_WEEKLY_DIGEST_CONTRACT, encoding="utf-8")
    (op_dir / "primitive.yaml").write_text(
        "name: weekly-digest\n"
        "kind: operation\n"
        "version: 0.1.0\n"
        "description: weekly-digest test primitive.\n",
        encoding="utf-8",
    )
    (op_dir / "files").mkdir()
    return kit


@pytest.fixture
def exec_vault(tmp_path: Path) -> Path:
    """A vault with weekly-digest installed and a SKILL file in place."""

    v = tmp_path / "exec-vault"
    (v / ".wiki.journal").mkdir(parents=True)
    journal_path = v / ".wiki.journal" / "journal.jsonl"
    append_event(
        journal_path,
        VaultInitEvent(
            timestamp=NOW,
            by="wiki-init",
            vault_name="test-vault",
            recipe="minimal",
        ),
    )
    append_event(
        journal_path,
        PrimitiveInstallEvent(
            timestamp=NOW,
            by="wiki-init",
            primitive="weekly-digest",
            version="0.1.0",
        ),
    )
    skill_dir = v / ".claude" / "skills" / "weekly-digest"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# weekly-digest", encoding="utf-8")
    return v


def _ensure_python_claude(tmp_path: Path, script: str) -> Path:
    return _make_python_stub(tmp_path, script, name="claude")


def _failure_events(vault: Path) -> list[OperationExecFailedEvent]:
    return [
        e
        for e in read_events(vault / ".wiki.journal" / "journal.jsonl")
        if isinstance(e, OperationExecFailedEvent)
    ]


def _page_write_events(vault: Path) -> list[PageWriteEvent]:
    return [
        e
        for e in read_events(vault / ".wiki.journal" / "journal.jsonl")
        if isinstance(e, PageWriteEvent)
    ]


def test_orch_happy_path_succeeded(exec_vault: Path, kit_root: Path, tmp_path: Path) -> None:
    # CT-1: subprocess exits 0; one OperationRunEvent journaled; no exec event.
    claude = _ensure_python_claude(tmp_path, "import sys; sys.exit(0)")
    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=exec_vault / ".wiki.journal" / "journal.jsonl",
        now=NOW,
        claude_binary=claude,
    )
    assert result.exec_status == "succeeded"
    assert result.dispatch.status == "dispatched"
    assert len(result.dispatch.dispatch_event_id) == 12
    # No failure event journaled (CT-14: no exec_succeeded either).
    assert _failure_events(exec_vault) == []
    # Log file exists, named after the dispatch event id.
    log_path = (
        exec_vault / ".wiki.journal" / "exec-logs" / f"{result.dispatch.dispatch_event_id}.log"
    )
    assert log_path.exists()


def test_orch_invalid_args_skips_exec(exec_vault: Path, kit_root: Path, tmp_path: Path) -> None:
    # CT-2: invalid_args path returns exec_status="skipped" without spawning.
    claude = _ensure_python_claude(tmp_path, "import sys; sys.exit(0)")
    result = dispatch_and_exec(
        "weekly-digest",
        ["--frobnicate=x"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=exec_vault / ".wiki.journal" / "journal.jsonl",
        now=NOW,
        claude_binary=claude,
    )
    assert result.exec_status == "skipped"
    assert result.dispatch.status == "invalid_args"
    assert _failure_events(exec_vault) == []
    # No log file written.
    log_dir = exec_vault / ".wiki.journal" / "exec-logs"
    assert not log_dir.exists() or list(log_dir.iterdir()) == []


def test_orch_binary_not_found_raises_after_dispatch(
    exec_vault: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CT-3: no claude on PATH, no override, no env var → WikiError.
    # The dispatch event is journaled; no exec event.
    monkeypatch.delenv("WIKI_CLAUDE_BINARY", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"

    with pytest.raises(WikiError, match="claude binary"):
        dispatch_and_exec(
            "weekly-digest",
            ["--window=2026-W20"],
            vault_root=exec_vault,
            kit_root=kit_root,
            journal_path=journal_path,
            now=NOW,
            claude_binary=None,
        )
    # Dispatch event journaled; no exec failure.
    op_events = [e for e in read_events(journal_path) if isinstance(e, OperationRunEvent)]
    assert len(op_events) == 1
    assert op_events[0].status == "dispatched"
    assert _failure_events(exec_vault) == []


def test_orch_skill_missing_raises(
    exec_vault: Path,
    kit_root: Path,
    tmp_path: Path,
) -> None:
    # CT-4: remove the SKILL file → WikiError; no exec event.
    (exec_vault / ".claude" / "skills" / "weekly-digest" / "SKILL.md").unlink()
    claude = _ensure_python_claude(tmp_path, "import sys; sys.exit(0)")
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"
    with pytest.raises(WikiError, match="SKILL file not found"):
        dispatch_and_exec(
            "weekly-digest",
            ["--window=2026-W20"],
            vault_root=exec_vault,
            kit_root=kit_root,
            journal_path=journal_path,
            now=NOW,
            claude_binary=claude,
        )
    assert _failure_events(exec_vault) == []


def test_orch_conflict_refused(exec_vault: Path, kit_root: Path, tmp_path: Path) -> None:
    # CT-6: a .proposed sidecar in scope causes a conflict-refused failure.
    (exec_vault / "wiki" / "notes").mkdir(parents=True)
    (exec_vault / "wiki" / "notes" / "x.md.proposed").write_text("body", encoding="utf-8")
    claude = _ensure_python_claude(tmp_path, 'raise SystemExit("should not be called")')
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"

    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW,
        claude_binary=claude,
    )
    assert result.exec_status == "failed_conflict"
    failure = _failure_events(exec_vault)
    assert len(failure) == 1
    assert failure[0].reason == "conflict-refused"
    assert failure[0].exit_code == -1
    assert failure[0].stderr_tail == ""
    assert failure[0].conflict_sidecars == ["wiki/notes/x.md.proposed"]
    assert failure[0].dispatch_event_id == result.dispatch.dispatch_event_id

    failure_file = (
        exec_vault / "inbox" / "scheduled-failures" / f"{result.dispatch.dispatch_event_id}.md"
    )
    assert failure_file.exists()
    body = failure_file.read_text(encoding="utf-8")
    assert "Scheduled exec refused" in body
    assert "wiki/notes/x.md.proposed" in body

    # A PageWriteEvent was journaled for the failure file (spec
    # §Invariants — "plus any PageWriteEvent/PageProposalEvent the
    # per-failure file write through safe_write produces").
    page_writes = _page_write_events(exec_vault)
    failure_file_writes = [
        e for e in page_writes if e.path.endswith(".md") and "scheduled-failures" in e.path
    ]
    assert len(failure_file_writes) == 1


def test_orch_no_refusal_loop_from_failure_file_write(
    exec_vault: Path, kit_root: Path, tmp_path: Path
) -> None:
    # CT-17: a per-failure file write under inbox/scheduled-failures/
    # must NOT cause the next --exec invocation to refuse.
    (exec_vault / "wiki").mkdir()
    (exec_vault / "wiki" / "first.md.proposed").write_text("x", encoding="utf-8")
    claude = _ensure_python_claude(tmp_path, "import sys; sys.exit(0)")
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"

    # First call: refuses due to the user sidecar.
    first = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW,
        claude_binary=claude,
    )
    assert first.exec_status == "failed_conflict"

    # Remove the user sidecar; the failure file the kit wrote remains.
    (exec_vault / "wiki" / "first.md.proposed").unlink()

    # Second call: should NOT refuse — the kit's own per-failure file
    # lives under inbox/scheduled-failures/ which is walk-scope-excluded.
    second = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W21"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW,
        claude_binary=claude,
    )
    assert second.exec_status == "succeeded"


def test_orch_non_zero_exit_journals_failure_event(
    exec_vault: Path, kit_root: Path, tmp_path: Path
) -> None:
    # CT-7: subprocess exits non-zero → failure event + failure file.
    claude = _ensure_python_claude(
        tmp_path, 'import sys; sys.stderr.write("boom\\n"); sys.exit(137)'
    )
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"

    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW,
        claude_binary=claude,
        timeout_seconds=10,
    )
    assert result.exec_status == "failed_exit"
    failure = _failure_events(exec_vault)
    assert len(failure) == 1
    assert failure[0].reason == "non-zero-exit"
    assert failure[0].exit_code == 137
    assert "boom" in failure[0].stderr_tail
    assert failure[0].dispatch_event_id == result.dispatch.dispatch_event_id
    assert failure[0].log_path is not None
    assert failure[0].log_path.endswith(".log")

    failure_file = (
        exec_vault / "inbox" / "scheduled-failures" / f"{result.dispatch.dispatch_event_id}.md"
    )
    assert failure_file.exists()
    body = failure_file.read_text(encoding="utf-8")
    assert "non-zero-exit" in body
    assert "137" in body
    # Q4: last-non-empty-stderr line is rendered in the failure file.
    assert "`boom`" in body

    # Q2: the log file on disk actually carries the captured stderr,
    # not just that it exists.
    log_file = (
        exec_vault / ".wiki.journal" / "exec-logs" / f"{result.dispatch.dispatch_event_id}.log"
    )
    assert log_file.exists()
    log_contents = log_file.read_text(encoding="utf-8")
    assert "boom" in log_contents


def test_orch_timeout(exec_vault: Path, kit_root: Path, tmp_path: Path) -> None:
    # CT-8: subprocess sleeps past timeout → exec_status="failed_timeout".
    claude = _ensure_python_claude(tmp_path, "import time; time.sleep(10)")
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"

    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW,
        claude_binary=claude,
        timeout_seconds=1,
    )
    assert result.exec_status == "failed_timeout"
    failure = _failure_events(exec_vault)
    assert len(failure) == 1
    assert failure[0].reason == "timeout"
    assert failure[0].exit_code == -2


def test_orch_argv_shape_matches_adr_0009(
    exec_vault: Path,
    kit_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CT-13: argv structure matches ADR-0009 with the dispatch event id
    # inlined into the prompt.
    argv_log = tmp_path / "argv.log"
    claude = _make_python_stub(
        tmp_path,
        "import sys, json\n"
        f'open({str(argv_log)!r}, "w").write(json.dumps(sys.argv))\n'
        "sys.exit(0)\n",
        name="claude",
    )
    monkeypatch.delenv("WIKI_EXEC_MAX_BUDGET_USD", raising=False)
    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=exec_vault / ".wiki.journal" / "journal.jsonl",
        now=NOW,
        claude_binary=claude,
    )
    assert result.exec_status == "succeeded"
    import json as _json

    raw = argv_log.read_text(encoding="utf-8")
    argv = _json.loads(raw)
    # argv[0] is the stub itself.
    assert argv[1] == "-p"
    assert argv[2] == "--add-dir"
    assert argv[3] == str(exec_vault)
    assert argv[4] == "--permission-mode"
    assert argv[5] == "dontAsk"
    assert argv[6] == "--output-format"
    assert argv[7] == "json"
    # Last element is the prompt; must contain the dispatch event id.
    prompt = argv[-1]
    assert result.dispatch.dispatch_event_id in prompt
    assert "weekly-digest" in prompt
    # --agent must NOT appear at v1 (ADR-0010 deferred).
    assert "--agent" not in argv


def test_orch_max_budget_inserts_pair(
    exec_vault: Path,
    kit_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CT-13a: WIKI_EXEC_MAX_BUDGET_USD valid value emits --max-budget-usd.
    argv_log = tmp_path / "argv.log"
    claude = _make_python_stub(
        tmp_path,
        "import sys, json\n"
        f"open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv))\n"
        "sys.exit(0)\n",
        name="claude",
    )
    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=exec_vault / ".wiki.journal" / "journal.jsonl",
        now=NOW,
        claude_binary=claude,
        max_budget_usd="5.00",
    )
    assert result.exec_status == "succeeded"
    import json as _json

    argv = _json.loads(argv_log.read_text(encoding="utf-8"))
    # --max-budget-usd <value> appears immediately before the prompt positional.
    assert argv[-3:-1] == ["--max-budget-usd", "5.00"]


def test_orch_max_budget_invalid_raises_after_dispatch(
    exec_vault: Path, kit_root: Path, tmp_path: Path
) -> None:
    # CT-13a: invalid budget shape raises WikiError; no subprocess; no exec event.
    claude = _ensure_python_claude(tmp_path, 'raise SystemExit("should not be called")')
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"
    with pytest.raises(WikiError, match="WIKI_EXEC_MAX_BUDGET_USD"):
        dispatch_and_exec(
            "weekly-digest",
            ["--window=2026-W20"],
            vault_root=exec_vault,
            kit_root=kit_root,
            journal_path=journal_path,
            now=NOW,
            claude_binary=claude,
            max_budget_usd="not-a-number",
        )
    # Dispatch event journaled with status="dispatched"; no exec failure.
    op_events = [e for e in read_events(journal_path) if isinstance(e, OperationRunEvent)]
    assert len(op_events) == 1
    assert op_events[0].status == "dispatched"
    assert _failure_events(exec_vault) == []


def test_orch_log_rotation_runs(
    exec_vault: Path,
    kit_root: Path,
    tmp_path: Path,
) -> None:
    # CT-15: old logs in .wiki.journal/exec-logs/ are deleted at exec start.
    log_dir = exec_vault / ".wiki.journal" / "exec-logs"
    log_dir.mkdir(parents=True)
    old = log_dir / "old.log"
    old.write_text("body", encoding="utf-8")
    _set_mtime(old, days_ago=31)

    claude = _ensure_python_claude(tmp_path, "import sys; sys.exit(0)")
    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=exec_vault / ".wiki.journal" / "journal.jsonl",
        now=NOW,
        claude_binary=claude,
        log_retention_days=30,
    )
    assert result.exec_status == "succeeded"
    assert not old.exists()


# ---------------------------------------------------------------------------
# Post-review additions (REVIEW iteration 1)
# ---------------------------------------------------------------------------


def test_orch_conflict_walk_runs_before_binary_resolution(
    exec_vault: Path, kit_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sidecar refusal fires even when claude binary is missing.

    Per spec §"What this is" the conflict walk precedes binary
    resolution, so a vault in conflict surfaces ``failed_conflict``
    even without a usable ``claude`` on PATH.
    """

    (exec_vault / "wiki").mkdir()
    (exec_vault / "wiki" / "x.md.proposed").write_text("body", encoding="utf-8")
    monkeypatch.delenv("WIKI_CLAUDE_BINARY", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"

    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW,
        claude_binary=None,
    )
    assert result.exec_status == "failed_conflict"
    failures = [e for e in read_events(journal_path) if isinstance(e, OperationExecFailedEvent)]
    assert len(failures) == 1
    assert failures[0].reason == "conflict-refused"


def test_orch_failure_duration_is_nonzero(exec_vault: Path, kit_root: Path, tmp_path: Path) -> None:
    """Per-failure file duration must reflect actual elapsed time.

    The orchestrator accepts a ``failure_clock`` callable so tests
    can inject a deterministic ``failed_at = now + 17s``. The
    rendered duration must read ``duration 17s``, not ``0s``.
    """

    claude = _ensure_python_claude(tmp_path, "import sys; sys.exit(1)")
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"
    failed_at = NOW + timedelta(seconds=17)

    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW,
        claude_binary=claude,
        timeout_seconds=10,
        failure_clock=lambda: failed_at,
    )
    assert result.exec_status == "failed_exit"
    failure_file = (
        exec_vault / "inbox" / "scheduled-failures" / f"{result.dispatch.dispatch_event_id}.md"
    )
    body = failure_file.read_text(encoding="utf-8")
    assert "duration 17s" in body
    # And the failure event's journaled timestamp matches failed_at,
    # not dispatch's now — verifies the clock drove both surfaces.
    failures = [e for e in read_events(journal_path) if isinstance(e, OperationExecFailedEvent)]
    assert len(failures) == 1
    assert failures[0].timestamp == failed_at


def test_orch_over_cap_sidecars_render_n_more(
    exec_vault: Path, kit_root: Path, tmp_path: Path
) -> None:
    """When >20 sidecars in scope, the failure file lists 20 plus ``(…N more)``."""

    (exec_vault / "wiki").mkdir()
    for i in range(25):
        (exec_vault / "wiki" / f"f-{i:02d}.md.proposed").write_text("x", encoding="utf-8")
    claude = _ensure_python_claude(tmp_path, "import sys; sys.exit(0)")
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"

    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW,
        claude_binary=claude,
    )
    assert result.exec_status == "failed_conflict"
    failure_file = (
        exec_vault / "inbox" / "scheduled-failures" / f"{result.dispatch.dispatch_event_id}.md"
    )
    body = failure_file.read_text(encoding="utf-8")
    assert "(…5 more)" in body
    # Q3: the journaled event's conflict_sidecars carries up to 20
    # paths, not the full 25 — the 20-path bound is the contract.
    failures = _failure_events(exec_vault)
    assert len(failures) == 1
    assert len(failures[0].conflict_sidecars) == 20


def test_orch_skill_path_override_in_argv(exec_vault: Path, kit_root: Path, tmp_path: Path) -> None:
    """``--skill-path <override>`` flows into the prompt the kit sends to claude."""

    override = tmp_path / "custom-skills" / "custom-SKILL.md"
    override.parent.mkdir()
    override.write_text("# override SKILL", encoding="utf-8")
    argv_log = tmp_path / "argv.log"
    claude = _make_python_stub(
        tmp_path,
        "import sys, json\n"
        f"open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv))\n"
        "sys.exit(0)\n",
        name="claude",
    )
    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=exec_vault / ".wiki.journal" / "journal.jsonl",
        now=NOW,
        claude_binary=claude,
        skill_path_override=override,
    )
    assert result.exec_status == "succeeded"
    argv = json.loads(argv_log.read_text(encoding="utf-8"))
    prompt = argv[-1]
    assert str(override) in prompt


def test_orch_two_failures_produce_two_distinct_files(
    exec_vault: Path, kit_root: Path, tmp_path: Path
) -> None:
    """CT-11 invariant — distinct dispatches keep distinct file names."""

    claude = _ensure_python_claude(tmp_path, "import sys; sys.exit(1)")
    journal_path = exec_vault / ".wiki.journal" / "journal.jsonl"

    first = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW,
        claude_binary=claude,
        timeout_seconds=10,
    )
    second = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W21"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=journal_path,
        now=NOW + timedelta(seconds=60),
        claude_binary=claude,
        timeout_seconds=10,
    )
    assert first.dispatch.dispatch_event_id != second.dispatch.dispatch_event_id
    failures_dir = exec_vault / "inbox" / "scheduled-failures"
    files = sorted(p.name for p in failures_dir.iterdir())
    assert files == sorted(
        [
            f"{first.dispatch.dispatch_event_id}.md",
            f"{second.dispatch.dispatch_event_id}.md",
        ]
    )


def test_orch_subprocess_runs_with_vault_cwd_and_inherits_env(
    exec_vault: Path,
    kit_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §Invariants line 436-445: ``cwd=vault_root``, env unchanged at v1.

    Captures both surfaces from a python-stub claude that writes
    ``os.getcwd()`` and a marker env var to a fixture file. Pins
    the contract so a future refactor that filters env or changes
    cwd can't regress silently.
    """

    capture = tmp_path / "capture.json"
    script = (
        "import os, json, sys\n"
        "data = {'cwd': os.getcwd(),"
        " 'marker': os.environ.get('WIKI_EXEC_TEST_MARKER')}\n"
        f"json.dump(data, open({str(capture)!r}, 'w'))\n"
        "sys.exit(0)\n"
    )
    claude = _make_python_stub(tmp_path, script, name="claude")
    monkeypatch.setenv("WIKI_EXEC_TEST_MARKER", "sentinel-value")
    result = dispatch_and_exec(
        "weekly-digest",
        ["--window=2026-W20"],
        vault_root=exec_vault,
        kit_root=kit_root,
        journal_path=exec_vault / ".wiki.journal" / "journal.jsonl",
        now=NOW,
        claude_binary=claude,
    )
    assert result.exec_status == "succeeded"
    captured = json.loads(capture.read_text(encoding="utf-8"))
    # cwd resolves to the same path under macOS /private symlink — compare
    # via Path.resolve so /var vs /private/var don't trip the assertion.
    assert Path(captured["cwd"]).resolve() == exec_vault.resolve()
    # Parent env passes through unchanged (the marker we set is visible).
    assert captured["marker"] == "sentinel-value"
