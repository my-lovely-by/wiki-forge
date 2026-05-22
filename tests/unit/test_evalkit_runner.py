"""Unit tests for ``tests/evalkit`` (RFC-0001 Task 20).

Construction tests for Step 1 of the eval-harness plan. These tests
target ``evalkit``'s pure helpers and the subprocess invocation shape
— the harness must construct the argv correctly, isolate ``$HOME``,
pin ``cwd`` to the vault, and refuse a non-tmp vault path before
ever shelling out.

The subprocess tests stub out ``subprocess.run`` with a capturing
fake so no real ``claude`` invocation fires; the binary-resolution
tests use a tmp shell script. The whole module runs under
``pytest -m 'not slow and not eval'``.

Spec: docs/specs/task-20-eval-harness/spec.md
Plan: docs/specs/task-20-eval-harness/plan.md
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests import evalkit


def _make_claude_script(tmp_path: Path, *, name: str = "claude") -> Path:
    """Create a tmp shell script that mimics ``claude --print``.

    Echoes a stream-json transcript fragment so the resolver and the
    full ``run_claude`` path can exercise without a real binary.
    """

    script = tmp_path / name
    script.write_text(
        '#!/bin/sh\necho \'{"type":"system","subtype":"init"}\'\nexit 0\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def test_run_claude_resolves_binary_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _make_claude_script(tmp_path)
    monkeypatch.setenv("LLM_WIKI_KIT_EVAL_CLAUDE_BIN", str(script))
    resolved = evalkit._resolve_claude_bin()
    assert resolved == script


def test_run_claude_falls_back_to_which_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _make_claude_script(tmp_path)
    monkeypatch.delenv("LLM_WIKI_KIT_EVAL_CLAUDE_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    resolved = evalkit._resolve_claude_bin()
    assert resolved == script


def test_run_claude_raises_skip_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_WIKI_KIT_EVAL_CLAUDE_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # no `claude` here
    with pytest.raises(evalkit.ClaudeBinaryMissing):
        evalkit._resolve_claude_bin()


# ---------------------------------------------------------------------------
# Argv shape — every pinned flag has a test
# ---------------------------------------------------------------------------


def test_argv_starts_with_print_flag(tmp_path: Path) -> None:
    runner = evalkit.EvalkitClaudeRunner(vault=_make_vault(tmp_path))
    argv = runner._argv(claude_bin="/usr/bin/claude")
    # `--print` must be present and must precede `--max-budget-usd`
    # (the latter requires the former per `claude --help`).
    assert "--print" in argv
    assert argv.index("--print") < argv.index("--max-budget-usd")


def test_argv_pins_permission_mode_accept_edits(tmp_path: Path) -> None:
    runner = evalkit.EvalkitClaudeRunner(vault=_make_vault(tmp_path))
    argv = runner._argv(claude_bin="/usr/bin/claude")
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_argv_pins_output_format_stream_json(tmp_path: Path) -> None:
    runner = evalkit.EvalkitClaudeRunner(vault=_make_vault(tmp_path))
    argv = runner._argv(claude_bin="/usr/bin/claude")
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    # Concern 1 from round-2 review: do not pass --include-partial-messages.
    assert "--include-partial-messages" not in argv


def test_argv_pins_no_session_persistence(tmp_path: Path) -> None:
    runner = evalkit.EvalkitClaudeRunner(vault=_make_vault(tmp_path))
    argv = runner._argv(claude_bin="/usr/bin/claude")
    assert "--no-session-persistence" in argv


def test_argv_does_not_pass_bare_flag(tmp_path: Path) -> None:
    """--bare would skip CLAUDE.md auto-discovery, breaking trigger evals."""

    runner = evalkit.EvalkitClaudeRunner(vault=_make_vault(tmp_path))
    argv = runner._argv(claude_bin="/usr/bin/claude")
    assert "--bare" not in argv


def test_argv_pins_budget_cap(tmp_path: Path) -> None:
    runner = evalkit.EvalkitClaudeRunner(vault=_make_vault(tmp_path), budget_usd=0.25)
    argv = runner._argv(claude_bin="/usr/bin/claude")
    assert "--max-budget-usd" in argv
    value = argv[argv.index("--max-budget-usd") + 1]
    assert float(value) == pytest.approx(0.25)


def test_argv_uses_allowed_tools_space_pattern(tmp_path: Path) -> None:
    """Each tool is its own argv element; Bash(<cmd> *) preserves whitespace."""

    runner = evalkit.EvalkitClaudeRunner(
        vault=_make_vault(tmp_path),
        allowed_tools=("Read", "Bash(wiki resolve *)"),
    )
    argv = runner._argv(claude_bin="/usr/bin/claude")
    start = argv.index("--allowed-tools")
    # The two tools follow `--allowed-tools` as separate argv elements
    # — this is what survives whitespace inside `Bash(<cmd> *)` patterns
    # without depending on the CLI's comma-split tokenizer being paren-aware.
    assert argv[start + 1] == "Read"
    assert argv[start + 2] == "Bash(wiki resolve *)"
    # No comma-joined string anywhere in argv (a regression would
    # reintroduce `Read,Bash(wiki resolve *)`).
    assert not any("," in token and "Bash" in token for token in argv)


def test_argv_omits_allowed_tools_when_empty(tmp_path: Path) -> None:
    """No allowed_tools → no `--allowed-tools` flag in argv at all."""

    runner = evalkit.EvalkitClaudeRunner(vault=_make_vault(tmp_path), allowed_tools=())
    argv = runner._argv(claude_bin="/usr/bin/claude")
    assert "--allowed-tools" not in argv


def test_argv_passes_model_alias(tmp_path: Path) -> None:
    runner = evalkit.EvalkitClaudeRunner(vault=_make_vault(tmp_path), model="sonnet")
    argv = runner._argv(claude_bin="/usr/bin/claude")
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_default_model_is_sonnet_alias() -> None:
    assert evalkit.EVAL_MODEL == "sonnet"


def test_default_budget_is_small() -> None:
    """The default cap exists so a runaway eval costs under a dollar."""

    assert 0 < evalkit.EVAL_MAX_BUDGET_USD < 1.0


def test_default_timeout_is_180s() -> None:
    assert evalkit.EVAL_DEFAULT_TIMEOUT_S == 180.0


# ---------------------------------------------------------------------------
# Vault-under-tmp invariant — Spec §Invariants
# ---------------------------------------------------------------------------


def test_runner_rejects_non_tmp_vault() -> None:
    """A fixture that hands the runner a repo path must fail fast."""

    repo_path = Path(__file__).resolve().parent
    with pytest.raises(ValueError, match="under"):
        evalkit.EvalkitClaudeRunner(vault=repo_path)


def test_runner_accepts_tmp_path_vault(tmp_path: Path) -> None:
    # No error: tmp_path is under tempfile.gettempdir().
    evalkit.EvalkitClaudeRunner(vault=_make_vault(tmp_path))


# ---------------------------------------------------------------------------
# Subprocess invocation shape — captures argv + env + cwd
# ---------------------------------------------------------------------------


def test_run_claude_passes_pinned_flags_to_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Captures subprocess.run; asserts argv, env, and cwd at the invocation site.

    Closes AC11 + AC13 against the load-bearing call (not just _argv).
    """

    script = _make_claude_script(tmp_path)
    monkeypatch.setenv("LLM_WIKI_KIT_EVAL_CLAUDE_BIN", str(script))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-for-test")
    vault = _make_vault(tmp_path)

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        captured["timeout"] = kwargs["timeout"]
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evalkit.run_claude(
        prompt="hello",
        vault=vault,
        allowed_tools=["Read"],
        timeout_s=10.0,
    )

    argv = captured["argv"]
    assert argv[0] == str(script)
    assert "--max-budget-usd" in argv
    assert "--permission-mode" in argv
    assert "--no-session-persistence" in argv

    env = captured["env"]
    # HOME isolated, ANTHROPIC_API_KEY forwarded, PATH carried, nothing else
    # of the developer's environment leaks through.
    assert "HOME" in env
    home = Path(env["HOME"])
    assert home.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert home != Path(os.path.expanduser("~"))
    assert env["ANTHROPIC_API_KEY"] == "sk-fake-for-test"
    assert "PATH" in env
    # No leakage of arbitrary env vars from the host.
    assert "USER" not in env or env.get("USER") == os.environ.get("USER", "")

    assert Path(captured["cwd"]) == vault
    assert captured["timeout"] == 10.0
    # Prompt is delivered via stdin, not as a positional arg.
    assert captured["input"] == "hello"
    assert "hello" not in argv


def test_run_claude_threads_budget_override_to_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC11: the budget_usd argument reaches the wire, not just the default."""

    script = _make_claude_script(tmp_path)
    monkeypatch.setenv("LLM_WIKI_KIT_EVAL_CLAUDE_BIN", str(script))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-for-test")
    vault = _make_vault(tmp_path)

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evalkit.run_claude(
        prompt="x",
        vault=vault,
        allowed_tools=["Read"],
        timeout_s=10.0,
        budget_usd=0.99,
    )
    argv = captured["argv"]
    flag_idx = argv.index("--max-budget-usd")
    assert float(argv[flag_idx + 1]) == pytest.approx(0.99)


def test_run_claude_isolates_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two consecutive run_claude calls get different HOME dirs."""

    script = _make_claude_script(tmp_path)
    monkeypatch.setenv("LLM_WIKI_KIT_EVAL_CLAUDE_BIN", str(script))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-for-test")
    vault = _make_vault(tmp_path)

    homes: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        homes.append(kwargs["env"]["HOME"])
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evalkit.run_claude(prompt="a", vault=vault, allowed_tools=["Read"], timeout_s=1.0)
    evalkit.run_claude(prompt="b", vault=vault, allowed_tools=["Read"], timeout_s=1.0)

    assert len(homes) == 2
    assert homes[0] != homes[1], "each invocation gets a fresh tmp HOME"


def test_run_claude_kills_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A claude that hangs past the timeout produces a clear error."""

    sleeper = tmp_path / "claude"
    sleeper.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    sleeper.chmod(0o755)
    monkeypatch.setenv("LLM_WIKI_KIT_EVAL_CLAUDE_BIN", str(sleeper))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-for-test")
    vault = _make_vault(tmp_path)

    result = evalkit.run_claude(
        prompt="x",
        vault=vault,
        allowed_tools=["Read"],
        timeout_s=0.3,
    )
    assert result.timed_out is True
    assert result.returncode != 0


def test_run_claude_rejects_empty_allowed_tools(tmp_path: Path) -> None:
    """An eval that forgot to declare its tool scope fails fast."""

    vault = _make_vault(tmp_path)
    with pytest.raises(ValueError, match="allowed_tools must be non-empty"):
        evalkit.run_claude(prompt="x", vault=vault, allowed_tools=[], timeout_s=1.0)


@pytest.mark.parametrize(
    "tools",
    [
        ["Read", "--malformed"],
        ["--just-a-flag"],
        ["Read", "Write", "--late"],
    ],
)
def test_run_claude_rejects_flag_leading_allowed_tools(tmp_path: Path, tools: list[str]) -> None:
    """A tool name starting with `--` would be mis-parsed by commander."""

    vault = _make_vault(tmp_path)
    with pytest.raises(ValueError, match=r"`--`-leading"):
        evalkit.run_claude(prompt="x", vault=vault, allowed_tools=tools, timeout_s=1.0)


# ---------------------------------------------------------------------------
# stream-json transcript parsing + assert_skill_loaded
# ---------------------------------------------------------------------------


_TRANSCRIPT_WITH_SKILL = """\
{"type":"system","subtype":"init"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"wiki-conflict"}}]}}
{"type":"result","subtype":"success"}
"""


_TRANSCRIPT_NO_SKILL = """\
{"type":"system","subtype":"init"}
{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}
{"type":"result","subtype":"success"}
"""


def _make_result(stdout: str) -> evalkit.ClaudeRunResult:
    events, decode_failures = evalkit._parse_stream_json(stdout)
    return evalkit.ClaudeRunResult(
        model="sonnet",
        stdout=stdout,
        stderr="",
        returncode=0,
        events=events,
        decode_failures=decode_failures,
        duration_s=0.0,
        timed_out=False,
    )


def test_parse_stream_json_handles_blank_and_invalid_lines() -> None:
    """Robust to interleaved progress chatter and trailing blank lines."""

    text = '{"type":"system"}\n\nnot json\n{"type":"result"}\n'
    events, decode_failures = evalkit._parse_stream_json(text)
    # Only the two valid JSON objects survive.
    assert len(events) == 2
    assert events[0]["type"] == "system"
    assert events[1]["type"] == "result"
    # The non-blank invalid line is counted; the blank line isn't.
    assert decode_failures == 1


def test_ordered_skill_reads_pulls_skills_in_emission_order() -> None:
    transcript = (
        '{"type":"system"}\n'
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","name":"Read","input":{"file_path":"/v/skills/wiki-conflict/SKILL.md"}},'
        '{"type":"tool_use","name":"Read","input":{"file_path":"/v/skills/wiki-search/SKILL.md"}}]}}\n'
    )
    result = _make_result(transcript)
    assert evalkit.ordered_skill_reads(result) == ["wiki-conflict", "wiki-search"]


def test_ordered_skill_reads_ignores_non_skill_reads() -> None:
    transcript = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","name":"Read","input":{"file_path":"/v/AGENTS.md"}},'
        '{"type":"tool_use","name":"Read","input":{"file_path":"/v/skills/wiki-conflict/SKILL.md"}}]}}\n'
    )
    result = _make_result(transcript)
    assert evalkit.ordered_skill_reads(result) == ["wiki-conflict"]


def test_assert_skill_loaded_finds_skill_tool_use() -> None:
    result = _make_result(_TRANSCRIPT_WITH_SKILL)
    evalkit.assert_skill_loaded(result, "wiki-conflict")


def test_assert_skill_loaded_fails_when_skill_missing() -> None:
    result = _make_result(_TRANSCRIPT_NO_SKILL)
    with pytest.raises(AssertionError, match="wiki-conflict"):
        evalkit.assert_skill_loaded(result, "wiki-conflict")


def test_assert_skill_loaded_lists_actual_skills_in_failure() -> None:
    result = _make_result(_TRANSCRIPT_WITH_SKILL)
    with pytest.raises(AssertionError, match="wiki-conflict"):
        # We asked for a different skill — failure message must
        # surface the actual one for debugging.
        evalkit.assert_skill_loaded(result, "wiki-search")


_TRANSCRIPT_WITH_READ_OF_SKILL_MD = """\
{"type":"system","subtype":"init"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/tmp/v/skills/wiki-conflict/SKILL.md"}}]}}
{"type":"result","subtype":"success"}
"""


def test_assert_skill_loaded_accepts_read_of_skill_md() -> None:
    """The kit's vault-side skills load via Read(skills/<name>/SKILL.md)."""

    result = _make_result(_TRANSCRIPT_WITH_READ_OF_SKILL_MD)
    evalkit.assert_skill_loaded(result, "wiki-conflict")


def test_assert_skill_loaded_fails_when_read_path_is_unrelated() -> None:
    transcript = (
        '{"type":"system"}\n'
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","name":"Read",'
        '"input":{"file_path":"/tmp/v/AGENTS.md"}}]}}\n'
    )
    result = _make_result(transcript)
    with pytest.raises(AssertionError, match="wiki-conflict"):
        evalkit.assert_skill_loaded(result, "wiki-conflict")


# ---------------------------------------------------------------------------
# Journal helpers
# ---------------------------------------------------------------------------


def test_assert_journal_has_filters_by_kind_and_field() -> None:
    from datetime import UTC, datetime

    from llm_wiki_kit.models import (
        Event,
        OperationRunEvent,
        ResearchQueryEvent,
    )

    now = datetime(2026, 5, 18, tzinfo=UTC)
    events: list[Event] = [
        OperationRunEvent(
            timestamp=now,
            by="wiki-run",
            operation="weekly-digest",
            status="dispatched",
        ),
        ResearchQueryEvent(
            timestamp=now,
            by="wiki-research",
            query="x",
            provider="perplexity",
            result_path=None,
            model="sonar",
            status="ok",
        ),
    ]
    # Match by kind alone.
    match = evalkit.assert_journal_has(events, kind=OperationRunEvent)
    assert match is events[0]
    # Match by kind + field.
    match = evalkit.assert_journal_has(events, kind=ResearchQueryEvent, status="ok")
    assert match is events[1]
    # Mismatched field fails.
    with pytest.raises(AssertionError):
        evalkit.assert_journal_has(events, kind=OperationRunEvent, status="invalid_args")


# ---------------------------------------------------------------------------
# Skip helper — fixture-side translation
# ---------------------------------------------------------------------------


def test_skip_if_env_unset_skips_with_named_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_WIKI_KIT_TEST_SENTINEL", raising=False)
    with pytest.raises(pytest.skip.Exception, match="LLM_WIKI_KIT_TEST_SENTINEL"):
        evalkit.skip_if_env_unset("LLM_WIKI_KIT_TEST_SENTINEL")


def test_skip_if_env_unset_returns_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_KIT_TEST_SENTINEL", "x")
    # Does not raise.
    evalkit.skip_if_env_unset("LLM_WIKI_KIT_TEST_SENTINEL")


def test_python_version_supports_is_relative_to() -> None:
    """Path.is_relative_to landed in 3.9; the kit requires 3.11+."""

    assert sys.version_info >= (3, 9)


# ---------------------------------------------------------------------------
# Redaction — junit XML is a public artifact, never leak secrets into it
# ---------------------------------------------------------------------------


def test_redact_strips_active_anthropic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-very-secret-value-12345")
    text = "Error: Authorization: Bearer sk-ant-very-secret-value-12345 not accepted"
    out = evalkit.redact(text)
    assert "sk-ant-very-secret-value-12345" not in out
    assert "<redacted-ANTHROPIC_API_KEY>" in out


def test_redact_strips_key_shaped_tokens_even_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense in depth: redact common key shapes regardless of env state."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Patterns the redactor catches by shape: sk-…, pplx-…, AIza….
    text = "key=sk-1234567890abcdef1234 and pplx-abcdef1234567890abcd and AIza" + "x" * 35
    out = evalkit.redact(text)
    assert "sk-1234567890abcdef1234" not in out
    assert "pplx-abcdef1234567890abcd" not in out
    assert out.count("<redacted-key>") >= 3


def test_redact_passes_empty_input_through() -> None:
    assert evalkit.redact("") == ""


def test_redact_leaves_non_secrets_intact() -> None:
    out = evalkit.redact("just regular log output, no secrets here")
    assert out == "just regular log output, no secrets here"
