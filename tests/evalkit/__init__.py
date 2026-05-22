"""Test-only harness for ``tests/evals/`` (RFC-0001 Task 20).

Drives the user's Claude Code CLI (``claude``) as a subprocess against
a fixture vault, then assists assertions on the resulting on-disk state
and journal. Lives under ``tests/`` — never imported from runtime code
in ``llm_wiki_kit/``.

Spec: ``docs/specs/task-20-eval-harness/spec.md``
Plan: ``docs/specs/task-20-eval-harness/plan.md``

The four pinned subprocess flags (``--max-budget-usd``,
``--no-session-persistence``, ``--output-format``, ``--verbose``)
require ``--print`` per ``claude --help``. Removing ``--print``
silently invalidates the harness's safety contract; the construction
tests in ``tests/unit/test_evalkit_runner.py`` pin ``--print`` as
present and ordered before ``--max-budget-usd``.

The harness deliberately does *not* pass ``--bare``: the trigger eval
exercises Claude Code's automatic SKILL discovery from the vault's
``AGENTS.md``, and ``--bare`` skips ``CLAUDE.md`` auto-discovery.
Isolation comes from ``cwd=vault`` + isolated ``$HOME``, not
``--bare``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import Event

# ---------------------------------------------------------------------------
# Constants (overridable via env vars)
# ---------------------------------------------------------------------------

EVAL_MODEL: str = os.environ.get("LLM_WIKI_KIT_EVAL_MODEL", "sonnet")
"""Model alias passed to ``claude --model``.

Default is the alias ``sonnet`` — not a pinned full name — so the
harness follows Anthropic's "latest" pointer when Sonnet ships a new
generation. See ``spec.md`` §Constraints for the alias-vs-pinned
trade-off.
"""

EVAL_MAX_BUDGET_USD: float = float(os.environ.get("LLM_WIKI_KIT_EVAL_MAX_BUDGET_USD", "0.25"))
"""Per-invocation cap passed to ``claude --max-budget-usd``.

Default $0.25 — small enough that one runaway eval costs under a
dollar, large enough that a typical scenario completes.
"""

EVAL_DEFAULT_TIMEOUT_S: float = 180.0
"""Default per-scenario wall-clock cap in seconds.

Override per-test via the ``timeout_s`` argument to :func:`run_claude`.
"""


_ENV_CLAUDE_BIN = "LLM_WIKI_KIT_EVAL_CLAUDE_BIN"

# Env vars whose values must never leak into pytest failure messages or
# stdout/stderr dumps — those land in junit XML and CI logs.
_SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY")

# Patterns that look like API keys regardless of which env var they live
# in. Conservative on false-positives — we'd rather over-redact a
# random hex blob than leak a key.
_KEY_LIKE_RE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|pplx-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{30,})")


def redact(text: str) -> str:
    """Strip live API-key values and key-shaped tokens from a string.

    Routed through every site that puts model-controlled or
    CLI-rendered output into a pytest failure message — those lines
    survive into junit XML, which is an uploaded artifact and
    therefore a public surface in CI.
    """

    if not text:
        return text
    redacted = text
    for var in _SECRET_ENV_VARS:
        value = os.environ.get(var)
        if value and len(value) >= 8:
            redacted = redacted.replace(value, f"<redacted-{var}>")
    redacted = _KEY_LIKE_RE.sub("<redacted-key>", redacted)
    return redacted


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ClaudeBinaryMissing(RuntimeError):
    """Raised by :func:`_resolve_claude_bin` when ``claude`` cannot be found.

    Fixtures translate this into ``pytest.skip(...)``; tests catch it
    only when they want to surface a different message.
    """


# ---------------------------------------------------------------------------
# Binary resolution + skip helpers
# ---------------------------------------------------------------------------


def _resolve_claude_bin() -> Path:
    """Locate the ``claude`` binary.

    Env var ``LLM_WIKI_KIT_EVAL_CLAUDE_BIN`` wins, then ``shutil.which``.
    Raises :class:`ClaudeBinaryMissing` if neither resolves — fixtures
    translate this into a clean skip.
    """

    env_path = os.environ.get(_ENV_CLAUDE_BIN)
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate
        raise ClaudeBinaryMissing(f"{_ENV_CLAUDE_BIN}={env_path!r} but no file at that path")
    resolved = shutil.which("claude")
    if resolved is None:
        raise ClaudeBinaryMissing(
            "claude binary not on PATH; set LLM_WIKI_KIT_EVAL_CLAUDE_BIN or "
            "install @anthropic-ai/claude-code"
        )
    return Path(resolved)


def skip_if_no_claude() -> Path:
    """Resolve the claude binary or call ``pytest.skip``.

    Returns the resolved path on success; fixtures pass this through to
    :class:`EvalkitClaudeRunner` so each invocation reuses the same
    binary.
    """

    try:
        return _resolve_claude_bin()
    except ClaudeBinaryMissing as exc:
        pytest.skip(str(exc))


def skip_if_env_unset(name: str) -> None:
    """Skip the calling test if ``name`` is unset or empty in ``os.environ``.

    Skip reason includes the env-var name so a maintainer reading the
    CI run sees which credential is missing without re-running.
    """

    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} unset; live eval skipped")


# ---------------------------------------------------------------------------
# Result + runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeRunResult:
    """Snapshot of one ``claude`` subprocess invocation.

    ``events`` is the parsed stream-json output — every line of stdout
    that successfully JSON-decoded. ``decode_failures`` is the count
    of lines that failed to decode; non-zero is a signal that the
    transcript was corrupted (truncation, embedded newline, partial
    chunk) and order-sensitive assertions like "first SKILL Claude
    Read" cannot be trusted. ``timed_out`` is True when the runner
    killed the subprocess for exceeding ``timeout_s``.
    """

    model: str
    stdout: str
    stderr: str
    returncode: int
    events: list[dict[str, Any]]
    decode_failures: int
    duration_s: float
    timed_out: bool


@dataclass(frozen=True)
class EvalkitClaudeRunner:
    """Pins the argv + env for one ``claude`` subprocess invocation.

    The vault path must resolve under ``tempfile.gettempdir()`` — this
    is the Spec §Invariants "evals never mutate the repo working tree"
    enforcement point. Fixtures that mistakenly hand the runner a repo
    path fail fast (``ValueError`` in ``__post_init__``) before any
    subprocess fires.

    Frozen so a runner instance can be safely shared across helpers
    without one mutating fields the other reads.
    """

    vault: Path
    model: str = EVAL_MODEL
    allowed_tools: tuple[str, ...] = ()
    budget_usd: float = EVAL_MAX_BUDGET_USD

    def __post_init__(self) -> None:
        tmp_root = Path(tempfile.gettempdir()).resolve()
        resolved_vault = self.vault.resolve()
        if not resolved_vault.is_relative_to(tmp_root):
            raise ValueError(f"vault must resolve under {tmp_root}, got {resolved_vault}")

    def _argv(self, claude_bin: str) -> list[str]:
        """Assemble the pinned ``claude`` argv.

        ``--print`` must precede ``--max-budget-usd``, ``--output-format``,
        and ``--no-session-persistence`` — those flags require ``--print``
        per ``claude --help``.
        """

        argv: list[str] = [
            claude_bin,
            "--print",
            "--model",
            self.model,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--no-session-persistence",
            "--max-budget-usd",
            f"{self.budget_usd:.4f}",
        ]
        if self.allowed_tools:
            # claude --help documents --allowed-tools as variadic
            # (`<tools...>`). Pass each tool as its own argv element so
            # patterns like `Bash(wiki resolve *)` (which contain
            # whitespace inside parens) survive without depending on
            # the CLI's internal comma-split tokenizer being
            # paren-aware. The space-separated form is what the
            # documented example (`"Bash(git *) Edit"`) showed.
            argv.append("--allowed-tools")
            argv.extend(self.allowed_tools)
        return argv


def _isolated_home(parent: Path) -> Path:
    """Create a fresh tmp dir to use as ``$HOME`` for one invocation.

    Both ``--no-session-persistence`` (blocks writes) and HOME isolation
    (blocks reads of the developer's ``~/.claude/settings.json``, agents,
    plugins, keychain) are required — they close different leak paths.
    See ``spec.md`` §Behavior step 5 for the rationale.
    """

    return Path(tempfile.mkdtemp(prefix="evalkit-home-", dir=parent))


def run_claude(
    prompt: str,
    vault: Path,
    *,
    allowed_tools: list[str],
    timeout_s: float = EVAL_DEFAULT_TIMEOUT_S,
    model: str | None = None,
    budget_usd: float | None = None,
) -> ClaudeRunResult:
    """Drive ``claude`` against ``vault`` and return a structured result.

    The prompt is delivered via stdin (closed after one write), not as
    a positional arg — keeps long prompts off the argv and avoids
    shell-quoting issues. The subprocess env is minimal: ``PATH``,
    ``ANTHROPIC_API_KEY``, and the isolated ``HOME`` only.

    ``allowed_tools`` must be explicit. An empty list is a programmer
    error — every scenario declares which tools it needs, even if
    that list is ``["Read"]``. Otherwise the eval falls back to the
    permission-mode default and silently widens scope.
    """

    if not allowed_tools:
        raise ValueError(
            "run_claude: allowed_tools must be non-empty; "
            "pass an explicit list (e.g. ['Read']) so the eval's tool "
            "scope is declared rather than falling back to the "
            "permission-mode default"
        )
    # `--allowed-tools` is variadic — commander collects elements
    # until the next `--flag`. Reject `--`-leading entries so a
    # future tool pattern doesn't get parsed as a flag boundary.
    bad = [t for t in allowed_tools if t.startswith("--")]
    if bad:
        raise ValueError(
            f"run_claude: allowed_tools may not contain `--`-leading entries; got {bad!r}"
        )

    claude_bin = _resolve_claude_bin()
    runner = EvalkitClaudeRunner(
        vault=vault,
        model=model or EVAL_MODEL,
        allowed_tools=tuple(allowed_tools),
        budget_usd=budget_usd if budget_usd is not None else EVAL_MAX_BUDGET_USD,
    )
    argv = runner._argv(str(claude_bin))

    home = _isolated_home(vault.parent)
    # PATH inheritance is parent-trust by design: the harness cannot
    # safely pin PATH to a fixed directory (would break developer
    # shells and CI's npm-global path). A compromised parent PATH is
    # out of scope for the harness; the user's shell environment is
    # the security boundary.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
    }
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key

    import time

    start = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            input=prompt,
            cwd=str(vault),
            env=env,
            timeout=timeout_s,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        if not stderr:
            stderr = f"claude exceeded {timeout_s} s timeout"
        returncode = -1
    duration_s = time.monotonic() - start

    events, decode_failures = _parse_stream_json(stdout)
    return ClaudeRunResult(
        model=runner.model,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        events=events,
        decode_failures=decode_failures,
        duration_s=duration_s,
        timed_out=timed_out,
    )


# ---------------------------------------------------------------------------
# Stream-json parsing
# ---------------------------------------------------------------------------


def _parse_stream_json(text: str) -> tuple[list[dict[str, Any]], int]:
    """Parse ``claude --output-format stream-json`` stdout.

    One JSON object per line. Returns ``(events, decode_failures)``:
    blank lines are ignored; non-blank lines that fail to decode are
    counted in ``decode_failures`` so order-sensitive callers can
    surface transcript corruption. If Anthropic ships a stream-json
    format change, this is the function to fix.
    """

    events: list[dict[str, Any]] = []
    decode_failures = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            decode_failures += 1
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events, decode_failures


def _iter_tool_uses(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Yield every ``tool_use`` content block across all assistant events."""

    out: list[dict[str, Any]] = []
    for ev in events:
        message = ev.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                out.append(block)
    return out


def _skill_name_from_tool_use(block: dict[str, Any]) -> str | None:
    """Extract the SKILL name from a ``Skill(...)`` tool_use block.

    The CLI's stream-json shape:
        {"type":"tool_use","name":"Skill","input":{"skill":"<name>"}}
    """

    if block.get("name") != "Skill":
        return None
    input_payload = block.get("input")
    if not isinstance(input_payload, dict):
        return None
    name = input_payload.get("skill")
    if isinstance(name, str):
        return name
    return None


def _read_paths_from_tool_use(block: dict[str, Any]) -> list[str]:
    """Extract file paths from a ``Read(...)`` tool_use block."""

    if block.get("name") != "Read":
        return []
    input_payload = block.get("input")
    if not isinstance(input_payload, dict):
        return []
    path = input_payload.get("file_path")
    return [path] if isinstance(path, str) else []


def ordered_skill_reads(result: ClaudeRunResult) -> list[str]:
    """Return SKILL dir-names Claude Read, in transcript (emission) order.

    Used by trigger evals to assert which SKILL Claude reached for
    *first*, distinguishing "found the right SKILL" from "scanned
    every SKILL and is still confused." The harness owns this
    transcript-traversal primitive; tests own their interpretation
    of the result (first-touch, set membership, etc.).
    """

    pattern = re.compile(r"skills/([^/]+)/SKILL\.md")
    names: list[str] = []
    for block in _iter_tool_uses(result.events):
        for path in _read_paths_from_tool_use(block):
            match = pattern.search(path)
            if match:
                names.append(match.group(1))
    return names


def assert_skill_loaded(result: ClaudeRunResult, skill_name: str) -> None:
    """Assert that ``claude`` engaged with the named SKILL during the run.

    The kit's vault-side skills are markdown documents the agent
    consults when AGENTS.md directs it — they are *not* loaded via
    Claude Code's first-class ``Skill`` tool. So "the skill is
    loaded" maps to one of two observable signals:

    1. A ``Skill`` tool_use call naming ``skill_name`` (for users
       who set up ``.claude/skills/`` plugin-style); OR
    2. A ``Read`` tool_use call against a file path that contains
       ``skills/<skill_name>/SKILL.md`` — the kit's design.

    Either counts. The failure message surfaces what the run
    actually loaded plus the first 1000 chars of stdout when the
    event list is empty.
    """

    skill_calls: list[str] = []
    read_paths: list[str] = []
    for block in _iter_tool_uses(result.events):
        name = _skill_name_from_tool_use(block)
        if name is not None:
            skill_calls.append(name)
        read_paths.extend(_read_paths_from_tool_use(block))

    if skill_name in skill_calls:
        return
    # Require a leading `/` before `skills/` so a model that returns
    # an attacker-shaped `file_path` like `/etc/skills/wiki-conflict/SKILL.md`
    # can't false-positive on the substring check. The vault is at a
    # tmp path; real skill reads land under absolute paths ending in
    # `/skills/<name>/SKILL.md`.
    needle = f"/skills/{skill_name}/SKILL.md"
    if any(needle in p for p in read_paths):
        return

    if not skill_calls and not read_paths:
        head = redact(result.stdout[:1000])
        raise AssertionError(
            f"expected SKILL {skill_name!r} to be loaded; "
            f"no Skill(...) or Read(...) tool calls in transcript. "
            f"stdout[:1000]={head!r}"
        )
    raise AssertionError(
        f"expected SKILL {skill_name!r} to be loaded; "
        f"Skill(...) tool calls saw: {skill_calls!r}; "
        f"Read(...) tool calls saw: {read_paths!r}"
    )


# ---------------------------------------------------------------------------
# Journal helpers
# ---------------------------------------------------------------------------


def journal_path(vault: Path) -> Path:
    """Canonical journal location inside a vault."""

    return vault / ".wiki.journal" / "journal.jsonl"


def read_journal_events(vault: Path) -> list[Event]:
    """Convenience re-export — just calls :func:`llm_wiki_kit.journal.read_events`."""

    return read_events(journal_path(vault))


def assert_journal_has(
    events: list[Event],
    *,
    kind: type[Event],
    **filters: Any,
) -> Event:
    """Assert at least one event of ``kind`` matching all ``filters`` exists.

    Returns the first matching event so a caller can chain assertions
    on its fields. Failure message lists the offending event types.
    """

    matches: list[Event] = []
    for ev in events:
        if not isinstance(ev, kind):
            continue
        if all(getattr(ev, k, None) == v for k, v in filters.items()):
            matches.append(ev)
    if matches:
        return matches[0]
    seen = sorted({type(e).__name__ for e in events})
    raise AssertionError(
        f"no {kind.__name__} matching {filters!r} found in journal; event kinds present: {seen!r}"
    )


__all__ = [
    "EVAL_DEFAULT_TIMEOUT_S",
    "EVAL_MAX_BUDGET_USD",
    "EVAL_MODEL",
    "ClaudeBinaryMissing",
    "ClaudeRunResult",
    "EvalkitClaudeRunner",
    "assert_journal_has",
    "assert_skill_loaded",
    "journal_path",
    "ordered_skill_reads",
    "read_journal_events",
    "redact",
    "run_claude",
    "skip_if_env_unset",
    "skip_if_no_claude",
]
