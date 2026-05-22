"""Tests for ``llm_wiki_kit.run.dispatch``.

End-to-end-ish tests against a synthesised tmp-kit + tmp-vault. The
kit fixture ships:

- ``core/`` (copied from the repo so the catalog and journal pieces
  exist).
- ``templates/operations/<op>/`` for two operations:
  - ``weekly-digest`` — copied verbatim from the repo's catalog
    (kept so CT-1's `period == "weekly"` claim stays load-bearing).
  - ``no-skill-op`` — a synth operation with no `skill:` field so
    the spec's "skill fallback to operation name" rule has a real
    fixture (CT-13).
  - ``no-period-op`` — a synth operation with no `period:` field.
- ``templates/content-types/meeting/`` — a content-type primitive
  also `installed` in the vault so the kind-mismatch path (CT-7)
  has a concrete name to point at.

Construction-test coverage from the plan:

- ``test_happy_path_dispatched``
- ``test_missing_arg_is_dispatched_not_failed``
- ``test_type_mismatch_invalid_args``
- ``test_unknown_arg_invalid_args``
- ``test_kebab_snake_kind_mismatch_routes_to_same_field``
- ``test_operation_not_installed``
- ``test_operation_kind_mismatch``
- ``test_contract_yaml_missing``
- ``test_at_most_one_event_per_invocation``
- ``test_period_threaded_from_contract``
- ``test_skill_fallback_to_operation_name``
- ``test_by_field_is_wiki_run``
- ``test_dispatch_result_invariant``
- ``test_error_precedence_first_token_wins``
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.journal import append_event, read_events
from llm_wiki_kit.models import (
    OperationRunEvent,
    PrimitiveInstallEvent,
    VaultInitEvent,
)
from llm_wiki_kit.run import RUN_VEHICLE, DispatchResult, dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixture: tmp kit + tmp vault
# ---------------------------------------------------------------------------


_NO_SKILL_CONTRACT = """\
name: no-skill-op
description: Op whose contract omits the skill field.
inputs:
  topic:
    type: string
outputs:
  page:
    type: page
    path_pattern: outputs/{topic}.md
    description: One page per topic.
"""

_EMPTY_SKILL_CONTRACT = """\
name: empty-skill-op
description: Op whose skill field is the empty string.
skill: ''
inputs: {}
outputs:
  page:
    type: page
    path_pattern: outputs/page.md
    description: One page.
"""

_NO_PERIOD_CONTRACT = """\
name: no-period-op
description: Op whose contract omits the period field.
skill: no-period-op
inputs:
  flag:
    type: boolean
    default: true
outputs:
  page:
    type: page
    path_pattern: outputs/page.md
    description: One page.
"""

_RENEWAL_LIKE_CONTRACT = """\
name: renewal-like
description: Mirrors renewal-reminders' include_open_ended field for kebab/snake tests.
period: monthly
skill: renewal-like
inputs:
  include_open_ended:
    type: boolean
    default: true
    description: Open-ended contract toggle.
outputs:
  page:
    type: page
    path_pattern: outputs/page.md
    description: One page.
"""


def _write_op(templates_dir: Path, name: str, contract_yaml: str) -> None:
    target = templates_dir / "operations" / name
    target.mkdir(parents=True)
    (target / "contract.yaml").write_text(contract_yaml, encoding="utf-8")
    (target / "primitive.yaml").write_text(
        f"name: {name}\n"
        "kind: operation\n"
        "version: 0.1.0\n"
        f"description: Test operation primitive {name}.\n",
        encoding="utf-8",
    )
    (target / "files").mkdir()


def _write_content_type(templates_dir: Path, name: str) -> None:
    target = templates_dir / "content-types" / name
    target.mkdir(parents=True)
    (target / "primitive.yaml").write_text(
        f"name: {name}\n"
        "kind: content-type\n"
        "version: 0.1.0\n"
        f"description: Test content-type primitive {name}.\n",
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
    # Copy the real weekly-digest contract so CT-1's period assertion
    # ("weekly") rides the shipped artifact.
    shutil.copytree(
        REPO_ROOT / "templates" / "operations" / "weekly-digest",
        templates / "operations" / "weekly-digest",
    )
    _write_op(templates, "no-skill-op", _NO_SKILL_CONTRACT)
    _write_op(templates, "empty-skill-op", _EMPTY_SKILL_CONTRACT)
    _write_op(templates, "no-period-op", _NO_PERIOD_CONTRACT)
    _write_op(templates, "renewal-like", _RENEWAL_LIKE_CONTRACT)
    _write_content_type(templates, "meeting")
    return kit


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A vault with a hand-built journal claiming several primitives are installed.

    Avoids running `wiki init` so each test starts from the same
    deterministic journal — no transitive dependency on `init`'s
    side-effects (managed-region writes, AGENTS.md rendering, etc.).
    """

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
    for name in (
        "weekly-digest",
        "no-skill-op",
        "empty-skill-op",
        "no-period-op",
        "renewal-like",
        "meeting",
    ):
        append_event(
            journal_path,
            PrimitiveInstallEvent(
                timestamp=NOW,
                by="wiki-init",
                primitive=name,
                version="0.1.0",
            ),
        )
    return v


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _count_run_events(vault: Path) -> int:
    return sum(1 for e in read_events(_journal_path(vault)) if isinstance(e, OperationRunEvent))


def _latest_run_event(vault: Path) -> OperationRunEvent:
    events = [e for e in read_events(_journal_path(vault)) if isinstance(e, OperationRunEvent)]
    assert events, "expected at least one OperationRunEvent"
    return events[-1]


def _dispatch(
    vault: Path,
    kit_root: Path,
    operation: str,
    raw_args: list[str],
) -> DispatchResult:
    return dispatch(
        operation,
        raw_args,
        vault_root=vault,
        kit_root=kit_root,
        journal_path=_journal_path(vault),
        now=NOW,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_dispatched(vault: Path, kit_root: Path) -> None:
    result = _dispatch(vault, kit_root, "weekly-digest", ["--window=2026-W20"])
    assert result.status == "dispatched"
    assert result.operation == "weekly-digest"
    assert result.skill == "weekly-digest"
    assert result.period == "weekly"
    assert result.parsed == {"window": "2026-W20", "sources": ["meeting"]}
    assert result.error is None
    assert result.args_raw == {"window": "2026-W20"}

    event = _latest_run_event(vault)
    assert event.status == "dispatched"
    assert event.operation == "weekly-digest"
    assert event.period == "weekly"
    assert event.produced_pages == []
    assert event.by == RUN_VEHICLE
    assert event.args == {"window": "2026-W20"}
    assert event.error is None


def test_by_field_is_wiki_run(vault: Path, kit_root: Path) -> None:
    _dispatch(vault, kit_root, "weekly-digest", ["--window=2026-W20"])
    assert _latest_run_event(vault).by == "wiki-run"


def test_period_threaded_from_contract(vault: Path, kit_root: Path) -> None:
    weekly = _dispatch(vault, kit_root, "weekly-digest", ["--window=2026-W20"])
    assert weekly.period == "weekly"

    no_period = _dispatch(vault, kit_root, "no-period-op", [])
    assert no_period.period is None

    monthly = _dispatch(vault, kit_root, "renewal-like", [])
    assert monthly.period == "monthly"


def test_skill_fallback_to_operation_name(vault: Path, kit_root: Path) -> None:
    omitted = _dispatch(vault, kit_root, "no-skill-op", ["--topic=foo"])
    assert omitted.skill == "no-skill-op"

    empty = _dispatch(vault, kit_root, "empty-skill-op", [])
    assert empty.skill == "empty-skill-op"


# ---------------------------------------------------------------------------
# Missing arg is NOT failed
# ---------------------------------------------------------------------------


def test_missing_arg_is_dispatched_not_failed(vault: Path, kit_root: Path) -> None:
    # weekly-digest has `window` with no default; sources defaults to [meeting].
    result = _dispatch(vault, kit_root, "weekly-digest", [])
    assert result.status == "dispatched"
    # window is absent; sources default-fills.
    assert "window" not in result.parsed
    assert result.parsed == {"sources": ["meeting"]}
    assert result.args_raw == {}
    assert _latest_run_event(vault).status == "dispatched"


# ---------------------------------------------------------------------------
# Validation failures (post-load)
# ---------------------------------------------------------------------------


def test_type_mismatch_invalid_args(vault: Path, kit_root: Path) -> None:
    result = _dispatch(vault, kit_root, "weekly-digest", ["--window=banana"])
    assert result.status == "invalid_args"
    assert result.error is not None
    assert "--window" in result.error
    assert "iso_week" in result.error
    event = _latest_run_event(vault)
    assert event.status == "invalid_args"
    assert event.args == {"window": "banana"}
    assert event.error == result.error


def test_unknown_arg_invalid_args(vault: Path, kit_root: Path) -> None:
    result = _dispatch(vault, kit_root, "weekly-digest", ["--frobnicate=x"])
    assert result.status == "invalid_args"
    assert result.error is not None
    assert "--frobnicate" in result.error
    assert "unknown argument" in result.error
    event = _latest_run_event(vault)
    assert event.args == {"frobnicate": "x"}


def test_kebab_snake_normalises_to_same_field(vault: Path, kit_root: Path) -> None:
    result = _dispatch(vault, kit_root, "renewal-like", ["--include-open-ended=false"])
    assert result.status == "dispatched"
    assert result.parsed["include_open_ended"] is False
    event = _latest_run_event(vault)
    assert event.args == {"include_open_ended": "false"}


def test_error_precedence_first_token_wins(vault: Path, kit_root: Path) -> None:
    # Case A: unknown-arg fires first.
    a = _dispatch(vault, kit_root, "weekly-digest", ["--frobnicate=x", "--window=banana"])
    assert a.status == "invalid_args"
    assert a.error is not None and "--frobnicate" in a.error
    event_a = _latest_run_event(vault)
    assert event_a.args == {"frobnicate": "x", "window": "banana"}

    # Case B: reverse order — type-mismatch fires first.
    b = _dispatch(vault, kit_root, "weekly-digest", ["--window=banana", "--frobnicate=x"])
    assert b.status == "invalid_args"
    assert b.error is not None
    assert "--window" in b.error and "iso_week" in b.error
    event_b = _latest_run_event(vault)
    assert event_b.args == {"window": "banana", "frobnicate": "x"}

    # Case C: last-wins-on-value, first-position-on-name.
    c = _dispatch(
        vault,
        kit_root,
        "weekly-digest",
        ["--window=2026-W20", "--frobnicate=x", "--window=banana"],
    )
    assert c.status == "invalid_args"
    assert c.error is not None
    assert "--window" in c.error and "iso_week" in c.error
    event_c = _latest_run_event(vault)
    assert event_c.args == {"window": "banana", "frobnicate": "x"}


# ---------------------------------------------------------------------------
# Pre-load failures (no journal write)
# ---------------------------------------------------------------------------


def test_operation_not_installed(vault: Path, kit_root: Path) -> None:
    before = _count_run_events(vault)
    with pytest.raises(WikiError) as excinfo:
        _dispatch(vault, kit_root, "non-existent", [])
    assert "non-existent" in str(excinfo.value)
    assert _count_run_events(vault) == before


def test_operation_kind_mismatch(vault: Path, kit_root: Path) -> None:
    before = _count_run_events(vault)
    with pytest.raises(WikiError) as excinfo:
        _dispatch(vault, kit_root, "meeting", [])
    msg = str(excinfo.value)
    assert "meeting" in msg
    assert "content-type" in msg
    assert _count_run_events(vault) == before


def test_contract_yaml_missing(vault: Path, kit_root: Path) -> None:
    # Delete the weekly-digest contract.yaml; the operation is still
    # in the journal AND in the catalog (primitive.yaml stays).
    contract_path = kit_root / "templates" / "operations" / "weekly-digest" / "contract.yaml"
    contract_path.unlink()
    before = _count_run_events(vault)
    with pytest.raises(WikiError) as excinfo:
        _dispatch(vault, kit_root, "weekly-digest", [])
    msg = str(excinfo.value)
    assert "weekly-digest" in msg
    assert str(contract_path) in msg
    assert _count_run_events(vault) == before


# ---------------------------------------------------------------------------
# Event-count invariant
# ---------------------------------------------------------------------------


def test_at_most_one_event_per_invocation(vault: Path, kit_root: Path) -> None:
    # Happy path — +1.
    before = _count_run_events(vault)
    _dispatch(vault, kit_root, "weekly-digest", ["--window=2026-W20"])
    assert _count_run_events(vault) == before + 1

    # invalid_args type-mismatch — +1.
    before = _count_run_events(vault)
    _dispatch(vault, kit_root, "weekly-digest", ["--window=banana"])
    assert _count_run_events(vault) == before + 1

    # invalid_args unknown-arg — +1.
    before = _count_run_events(vault)
    _dispatch(vault, kit_root, "weekly-digest", ["--frobnicate=x"])
    assert _count_run_events(vault) == before + 1

    # Pre-load failure (unknown operation) — +0.
    before = _count_run_events(vault)
    with pytest.raises(WikiError):
        _dispatch(vault, kit_root, "no-such-op", [])
    assert _count_run_events(vault) == before

    # Pre-load failure (kind mismatch) — +0.
    before = _count_run_events(vault)
    with pytest.raises(WikiError):
        _dispatch(vault, kit_root, "meeting", [])
    assert _count_run_events(vault) == before


# ---------------------------------------------------------------------------
# DispatchResult invariant
# ---------------------------------------------------------------------------


_VALID_EVENT_ID = "0123456789ab"  # 12 lowercase hex chars — valid shape


def test_dispatch_result_invariant_rejects_invalid_args_without_error() -> None:
    with pytest.raises(ValueError):
        DispatchResult(
            status="invalid_args",
            operation="weekly-digest",
            parsed={},
            args_raw={},
            period=None,
            skill="weekly-digest",
            dispatch_event_id=_VALID_EVENT_ID,
            error=None,
        )


def test_dispatch_result_invariant_rejects_dispatched_with_error() -> None:
    with pytest.raises(ValueError):
        DispatchResult(
            status="dispatched",
            operation="weekly-digest",
            parsed={},
            args_raw={},
            period=None,
            skill="weekly-digest",
            dispatch_event_id=_VALID_EVENT_ID,
            error="not allowed",
        )


def test_dispatch_result_invariant_rejects_malformed_event_id() -> None:
    # Too short.
    with pytest.raises(ValueError, match="12 lowercase hex"):
        DispatchResult(
            status="dispatched",
            operation="weekly-digest",
            parsed={},
            args_raw={},
            period=None,
            skill="weekly-digest",
            dispatch_event_id="abc",
        )
    # Uppercase hex — must be lowercase.
    with pytest.raises(ValueError, match="12 lowercase hex"):
        DispatchResult(
            status="dispatched",
            operation="weekly-digest",
            parsed={},
            args_raw={},
            period=None,
            skill="weekly-digest",
            dispatch_event_id="ABCDEF012345",
        )
    # Non-hex characters.
    with pytest.raises(ValueError, match="12 lowercase hex"):
        DispatchResult(
            status="dispatched",
            operation="weekly-digest",
            parsed={},
            args_raw={},
            period=None,
            skill="weekly-digest",
            dispatch_event_id="ghijklmnopqr",
        )


# ---------------------------------------------------------------------------
# Sanity: the journal lines are JSON-valid and round-trip cleanly
# ---------------------------------------------------------------------------


def test_journal_event_is_valid_jsonl(vault: Path, kit_root: Path) -> None:
    _dispatch(vault, kit_root, "weekly-digest", ["--window=2026-W20"])
    lines = _journal_path(vault).read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    assert last["type"] == "operation.run"
    assert last["status"] == "dispatched"
    assert last["args"] == {"window": "2026-W20"}


# ---------------------------------------------------------------------------
# CT-1a: dispatch event_id round-trip (wiki-run-exec spec)
# ---------------------------------------------------------------------------


def test_dispatch_event_id_is_12_lowercase_hex(vault: Path, kit_root: Path) -> None:
    result = _dispatch(vault, kit_root, "weekly-digest", ["--window=2026-W20"])
    assert isinstance(result.dispatch_event_id, str)
    assert len(result.dispatch_event_id) == 12
    assert all(c in "0123456789abcdef" for c in result.dispatch_event_id)


def test_dispatch_event_id_matches_journaled_event(vault: Path, kit_root: Path) -> None:
    result = _dispatch(vault, kit_root, "weekly-digest", ["--window=2026-W20"])
    events = list(read_events(_journal_path(vault)))
    op_run_events = [e for e in events if isinstance(e, OperationRunEvent)]
    assert len(op_run_events) == 1
    assert op_run_events[0].event_id == result.dispatch_event_id


def test_two_consecutive_dispatches_produce_distinct_event_ids(vault: Path, kit_root: Path) -> None:
    first = _dispatch(vault, kit_root, "weekly-digest", ["--window=2026-W20"])
    second = _dispatch(vault, kit_root, "weekly-digest", ["--window=2026-W21"])
    assert first.dispatch_event_id != second.dispatch_event_id


def test_invalid_args_dispatch_also_carries_event_id(vault: Path, kit_root: Path) -> None:
    # CT-2: invalid_args path still journals an event with a fresh event_id.
    result = _dispatch(vault, kit_root, "weekly-digest", ["--frobnicate=x"])
    assert result.status == "invalid_args"
    assert len(result.dispatch_event_id) == 12
    events = list(read_events(_journal_path(vault)))
    op_run_events = [e for e in events if isinstance(e, OperationRunEvent)]
    assert len(op_run_events) == 1
    assert op_run_events[0].event_id == result.dispatch_event_id
    assert op_run_events[0].status == "invalid_args"


def test_legacy_journal_line_without_event_id_replays_as_none() -> None:
    # CT-10 (model layer): a pre-extension line with no event_id key
    # replays cleanly with event.event_id is None.
    from pydantic import TypeAdapter

    from llm_wiki_kit.models import Event

    adapter: TypeAdapter[Event] = TypeAdapter(Event)
    legacy_line = (
        '{"type":"operation.run","timestamp":"2026-05-15T00:00:00Z",'
        '"by":"wiki-run","operation":"weekly-digest","status":"dispatched",'
        '"period":"weekly","produced_pages":[]}'
    )
    event = adapter.validate_json(legacy_line)
    assert isinstance(event, OperationRunEvent)
    assert event.event_id is None
    assert event.args == {}
    assert event.error is None
