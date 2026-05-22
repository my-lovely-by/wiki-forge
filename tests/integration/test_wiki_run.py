"""End-to-end ``wiki run`` integration tests (RFC-0001 Task 17).

Uses the tmp-kit threading pattern shared with ``test_wiki_ingest.py``
(qC8): a tmp kit holds the real ``core`` plus a small set of synth
operation primitives and one content-type. The vault is built by
appending events directly so we don't depend transitively on
``wiki init``'s side-effects.

CT coverage from ``docs/specs/task-17-wiki-run/spec.md``:

* CT-1: dispatch happy path
* CT-2: default-fill
* CT-3: missing argument is dispatched, not failed
* CT-4: type-mismatch → invalid_args
* CT-5: unknown argument → invalid_args
* CT-6: unknown operation rejected before any journal write
* CT-7: kind mismatch
* CT-8: not a vault
* CT-9: malformed `--arg` token
* CT-11: boolean coercion + kebab/snake
* CT-12: list coercion
* CT-13: skill fallback
* CT-14: `--help` short-circuit (incl. malformed positional alongside)
* CT-16: error-precedence first-token wins
* Renderer: booleans lowercase, integers decimal, lists comma-joined.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.cli import _render_dispatch_value
from llm_wiki_kit.journal import append_event, read_events
from llm_wiki_kit.models import (
    OperationRunEvent,
    PrimitiveInstallEvent,
    VaultInitEvent,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


_WEEKLY_DIGEST_CONTRACT = """\
name: weekly-digest
description: Weekly synth.
period: weekly
skill: weekly-digest
inputs:
  window:
    type: iso_week
    description: ISO week.
  sources:
    type: list
    items: content-type
    default:
      - meeting
    description: Content-types contributing to the digest.
outputs:
  digest:
    type: page
    path_pattern: outputs/digests/{window}.md
    description: One page per week.
"""

_RENEWAL_CONTRACT = """\
name: renewal-like
description: Like renewal-reminders — has integer + boolean defaults.
period: monthly
skill: renewal-like
inputs:
  lookahead_days:
    type: integer
    default: 90
    description: Days ahead.
  include_open_ended:
    type: boolean
    default: true
    description: Toggle.
outputs:
  page:
    type: page
    path_pattern: outputs/page.md
    description: One page.
"""

_NO_SKILL_CONTRACT = """\
name: no-skill-op
description: Op with no skill field.
inputs:
  topic:
    type: string
outputs:
  page:
    type: page
    path_pattern: outputs/{topic}.md
    description: One page.
"""


def _write_op(templates_dir: Path, name: str, contract_yaml: str) -> None:
    target = templates_dir / "operations" / name
    target.mkdir(parents=True)
    (target / "contract.yaml").write_text(contract_yaml, encoding="utf-8")
    (target / "primitive.yaml").write_text(
        f"name: {name}\nkind: operation\nversion: 0.1.0\ndescription: Test op {name}.\n",
        encoding="utf-8",
    )
    (target / "files").mkdir()


def _write_content_type(templates_dir: Path, name: str) -> None:
    target = templates_dir / "content-types" / name
    target.mkdir(parents=True)
    (target / "primitive.yaml").write_text(
        f"name: {name}\nkind: content-type\nversion: 0.1.0\ndescription: Test ct {name}.\n",
        encoding="utf-8",
    )
    (target / "files").mkdir()


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    templates = kit / "templates"
    templates.mkdir()
    _write_op(templates, "weekly-digest", _WEEKLY_DIGEST_CONTRACT)
    _write_op(templates, "renewal-like", _RENEWAL_CONTRACT)
    _write_op(templates, "no-skill-op", _NO_SKILL_CONTRACT)
    _write_content_type(templates, "meeting")
    return kit


def _make_vault(tmp_path: Path, installed: list[str]) -> Path:
    from datetime import UTC, datetime

    v = tmp_path / "vault"
    (v / ".wiki.journal").mkdir(parents=True)
    journal_path = v / ".wiki.journal" / "journal.jsonl"
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    append_event(
        journal_path,
        VaultInitEvent(timestamp=now, by="wiki-init", vault_name="v", recipe="minimal"),
    )
    for name in installed:
        append_event(
            journal_path,
            PrimitiveInstallEvent(timestamp=now, by="wiki-init", primitive=name, version="0.1.0"),
        )
    return v


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = _make_vault(
        tmp_path,
        ["weekly-digest", "renewal-like", "no-skill-op", "meeting"],
    )
    monkeypatch.chdir(v)
    return v


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _run_events(vault: Path) -> list[OperationRunEvent]:
    return [e for e in read_events(_journal_path(vault)) if isinstance(e, OperationRunEvent)]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_cli_dispatch_exits_zero_and_prints_skill_pointer(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["run", "weekly-digest", "--window=2026-W20"], kit_root=kit_root)
    assert exit_code == 0
    out = capsys.readouterr().out
    # Pin the literal dispatch line (including the backtick-wrapped
    # skill name) so the assertion fails if either piece drifts.
    assert "Dispatched weekly-digest. Run `weekly-digest` in your Claude session." in out
    assert "  window=2026-W20" in out
    assert "  sources=meeting" in out

    events = _run_events(vault)
    assert len(events) == 1
    assert events[0].status == "dispatched"
    assert events[0].period == "weekly"
    assert events[0].args == {"window": "2026-W20"}


def test_cli_renders_typed_values_canonically(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # All defaults applied: lookahead_days=90 (int), include_open_ended=true (bool).
    assert cli.main(["run", "renewal-like"], kit_root=kit_root) == 0
    out = capsys.readouterr().out
    assert "  lookahead_days=90" in out
    assert "  include_open_ended=true" in out

    # Override the boolean with a kebab spelling; assert lowercase render.
    assert cli.main(["run", "renewal-like", "--include-open-ended=false"], kit_root=kit_root) == 0
    out = capsys.readouterr().out
    assert "  include_open_ended=false" in out


def test_cli_missing_arg_is_dispatched_not_failed(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # weekly-digest.window has no default; running with no args should still dispatch.
    assert cli.main(["run", "weekly-digest"], kit_root=kit_root) == 0
    events = _run_events(vault)
    assert events[-1].status == "dispatched"
    out = capsys.readouterr().out
    # `window` should not appear in the echo (not user-supplied, no default).
    assert "  window=" not in out
    # `sources` default-filled.
    assert "  sources=meeting" in out


# ---------------------------------------------------------------------------
# invalid_args
# ---------------------------------------------------------------------------


def test_cli_invalid_args_exits_two(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["run", "weekly-digest", "--window=banana"], kit_root=kit_root)
    assert exit_code == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "--window" in err
    assert "iso_week" in err
    events = _run_events(vault)
    assert events[-1].status == "invalid_args"
    assert events[-1].args == {"window": "banana"}


def test_cli_unknown_argument_exits_two(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["run", "weekly-digest", "--frobnicate=x"], kit_root=kit_root)
    assert exit_code == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "--frobnicate" in err
    assert "unknown argument" in err
    events = _run_events(vault)
    assert events[-1].args == {"frobnicate": "x"}


def test_cli_first_token_wins_on_error(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # frobnicate first → unknown wins.
    cli.main(
        ["run", "weekly-digest", "--frobnicate=x", "--window=banana"],
        kit_root=kit_root,
    )
    err = capsys.readouterr().err
    assert "--frobnicate" in err

    # window first → coercion-fail wins.
    cli.main(
        ["run", "weekly-digest", "--window=banana", "--frobnicate=x"],
        kit_root=kit_root,
    )
    err = capsys.readouterr().err
    assert "--window" in err
    assert "iso_week" in err


# ---------------------------------------------------------------------------
# Pre-load failures (no journal write)
# ---------------------------------------------------------------------------


def test_cli_unknown_operation_exits_two_no_journal(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = len(_run_events(vault))
    exit_code = cli.main(["run", "non-existent"], kit_root=kit_root)
    assert exit_code == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "non-existent" in err
    assert len(_run_events(vault)) == before


def test_cli_kind_mismatch_exits_two_no_journal(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = len(_run_events(vault))
    exit_code = cli.main(["run", "meeting"], kit_root=kit_root)
    assert exit_code == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "meeting" in err
    assert "content-type" in err
    assert len(_run_events(vault)) == before


def test_cli_not_a_vault(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    exit_code = cli.main(["run", "weekly-digest"], kit_root=kit_root)
    assert exit_code == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "not a wiki vault" in err


def test_cli_malformed_token(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = len(_run_events(vault))
    exit_code = cli.main(["run", "weekly-digest", "banana", "--window=2026-W20"], kit_root=kit_root)
    assert exit_code == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "banana" in err
    # Malformed-token aborts parse before any journal write.
    assert len(_run_events(vault)) == before


# ---------------------------------------------------------------------------
# --help short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "weekly-digest", "--help"],
        ["run", "weekly-digest", "-h"],
        ["run", "weekly-digest", "--window=2026-W20", "--help"],
        ["run", "weekly-digest", "banana", "--help"],
    ],
    ids=lambda a: " ".join(a),
)
def test_cli_help_short_circuit_exits_zero_no_journal(
    vault: Path,
    kit_root: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    before = len(_run_events(vault))
    exit_code = cli.main(argv, kit_root=kit_root)
    assert exit_code == 0
    out = capsys.readouterr().out
    # argparse help mentions the operation positional.
    assert "operation" in out.lower()
    assert len(_run_events(vault)) == before


def test_cli_help_value_form_does_not_short_circuit(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["run", "weekly-digest", "--help=false"], kit_root=kit_root)
    assert exit_code == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "--help" in err
    assert "unknown argument" in err
    events = _run_events(vault)
    assert events[-1].status == "invalid_args"
    assert events[-1].args == {"help": "false"}


# ---------------------------------------------------------------------------
# list coercion via CLI
# ---------------------------------------------------------------------------


def test_cli_list_coercion(vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli.main(
            ["run", "weekly-digest", "--window=2026-W20", "--sources=a,b,c"],
            kit_root=kit_root,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "  sources=a,b,c" in out


# ---------------------------------------------------------------------------
# Skill fallback via CLI
# ---------------------------------------------------------------------------


def test_cli_skill_fallback_to_operation_name(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["run", "no-skill-op", "--topic=foo"], kit_root=kit_root)
    assert exit_code == 0
    out = capsys.readouterr().out
    # contract.skill is absent, so the dispatch line should name the
    # operation itself.
    assert "`no-skill-op`" in out


# ---------------------------------------------------------------------------
# Kit-root override is honoured
# ---------------------------------------------------------------------------


def test_render_dispatch_value_pins_bool_before_int_ordering() -> None:
    """Pin the bool-before-int branch ordering in ``_render_dispatch_value``.

    ``bool`` is a subclass of ``int`` in Python; if a future refactor
    swaps the isinstance branches, ``True``/``False`` would render as
    ``1``/``0``. The integration tests assert ``include_open_ended=true``
    over the CLI surface, but a direct call here makes the ordering
    contract explicit at the renderer level.
    """

    assert _render_dispatch_value(True) == "true"
    assert _render_dispatch_value(False) == "false"
    assert _render_dispatch_value(42) == "42"
    # Recursive list rendering applies the same rules per element.
    assert _render_dispatch_value([True, False, 1]) == "true,false,1"
    assert _render_dispatch_value(["a", "b"]) == "a,b"
    assert _render_dispatch_value([]) == ""


def test_cli_uses_kit_root_override(
    vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The vault was built against this kit_root; if we passed a different
    # kit_root that didn't ship `weekly-digest`, the operation lookup
    # would fail. Sanity: pass it explicitly and confirm dispatch.
    assert cli.main(["run", "weekly-digest", "--window=2026-W20"], kit_root=kit_root) == 0
