"""Tests for the additively-extended ``OperationRunEvent`` schema.

RFC-0001 Task 17 (``docs/specs/task-17-wiki-run/spec.md``) extends
``OperationRunEvent`` with two additive fields:

- ``args: dict[str, str] = Field(default_factory=dict)`` — the
  user-supplied tokens (post-``_parse_op_args``, pre-coercion),
  keyed by snake_case-normalised name.
- ``error: str | None = None`` — the failure message; non-None iff
  ``status == "invalid_args"``.

It also tightens ``status`` from ``str`` to
``Literal["dispatched", "invalid_args"]``.

Backward-compat with ADR-0002's additive-schema rule: any pre-
extension JSON line whose payload omits both new fields must still
validate.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from llm_wiki_kit.models import Event, OperationRunEvent

_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)
NOW = datetime(2026, 5, 15, 0, 0, 0, tzinfo=UTC)


def test_legacy_event_replays() -> None:
    """A pre-extension journal line still validates (CT-15)."""

    legacy = (
        '{"type":"operation.run",'
        '"timestamp":"2026-05-15T00:00:00+00:00",'
        '"by":"wiki-run",'
        '"operation":"weekly-digest",'
        '"status":"dispatched",'
        '"period":"weekly",'
        '"produced_pages":[]}'
    )
    event = _EVENT_ADAPTER.validate_json(legacy)
    assert isinstance(event, OperationRunEvent)
    assert event.args == {}
    assert event.error is None
    assert event.status == "dispatched"


def test_legacy_event_round_trips_after_reparse() -> None:
    """CT-15: legacy-shaped event survives a dump→reparse cycle."""

    legacy = (
        '{"type":"operation.run",'
        '"timestamp":"2026-05-15T00:00:00+00:00",'
        '"by":"wiki-run",'
        '"operation":"weekly-digest",'
        '"status":"dispatched",'
        '"period":"weekly",'
        '"produced_pages":[]}'
    )
    event = _EVENT_ADAPTER.validate_json(legacy)
    assert _EVENT_ADAPTER.validate_json(event.model_dump_json()) == event


def test_extended_fields_round_trip() -> None:
    event = OperationRunEvent(
        timestamp=NOW,
        by="wiki-run",
        operation="weekly-digest",
        status="invalid_args",
        period="weekly",
        produced_pages=[],
        args={"window": "banana"},
        error="--window: expected iso_week (YYYY-Www), got 'banana'",
    )
    blob = event.model_dump_json()
    reparsed = _EVENT_ADAPTER.validate_json(blob)
    assert isinstance(reparsed, OperationRunEvent)
    assert reparsed == event


def test_status_literal_rejects_typo() -> None:
    """A status value that's neither 'dispatched' nor 'invalid_args' fails."""

    with pytest.raises(PydanticValidationError):
        _EVENT_ADAPTER.validate_python(
            {
                "type": "operation.run",
                "timestamp": NOW.isoformat(),
                "by": "wiki-run",
                "operation": "weekly-digest",
                "status": "dispached",  # typo
            },
        )


def test_error_field_accepts_none_on_dispatched() -> None:
    """The Pydantic model itself permits any (status, error) pair.

    The "error is non-None iff status==invalid_args" invariant is
    enforced at the ``DispatchResult`` boundary, not on the on-disk
    event — older lines must keep replaying with ``error is None``
    even on a hypothetical ``status="dispatched"`` value.
    """

    event = OperationRunEvent(
        timestamp=NOW,
        by="wiki-run",
        operation="weekly-digest",
        status="dispatched",
        period=None,
        produced_pages=[],
        args={"window": "2026-W20"},
        error=None,
    )
    assert event.error is None
