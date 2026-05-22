"""Tests for ``llm_wiki_kit.errors``.

These pin the contract that ADR-0005 names: every kit error inherits from
``WikiError`` so the CLI boundary can catch one base, and ``ValidationError``
reformats Pydantic's structured errors into the
``Invalid <thing> at <path>: <human message>`` shape that the CLI prints.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from llm_wiki_kit.errors import (
    JournalCorruptError,
    ValidationError,
    WikiError,
)


class _Sample(BaseModel):
    name: str
    count: int


def _pydantic_error(data: object) -> PydanticValidationError:
    try:
        _Sample.model_validate(data)
    except PydanticValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


def test_wiki_error_is_exception_subclass() -> None:
    assert issubclass(WikiError, Exception)


def test_validation_error_inherits_from_wiki_error() -> None:
    assert issubclass(ValidationError, WikiError)


def test_journal_corrupt_error_inherits_from_wiki_error() -> None:
    assert issubclass(JournalCorruptError, WikiError)


def test_validation_error_formats_one_field() -> None:
    pyd = _pydantic_error({"name": "ok"})
    err = ValidationError("primitive", pyd)
    text = str(err)
    assert "Invalid primitive" in text
    assert "count" in text


def test_validation_error_formats_multiple_fields() -> None:
    pyd = _pydantic_error({"name": 5, "count": "two"})
    err = ValidationError("recipe", pyd)
    text = str(err)
    assert text.count("Invalid recipe") == 2
    assert "name" in text
    assert "count" in text


def test_validation_error_renders_nested_loc_with_dotted_path() -> None:
    class Outer(BaseModel):
        inner: _Sample

    try:
        Outer.model_validate({"inner": {"name": "ok"}})
    except PydanticValidationError as exc:
        pyd = exc
    else:
        raise AssertionError("expected a ValidationError")

    err = ValidationError("contract", pyd)
    text = str(err)
    assert "inner.count" in text


def test_validation_error_includes_the_offending_input_value() -> None:
    """Retro-review qC2: ``ValidationError`` surfaces Pydantic's ``input`` field.

    The previous formatter dropped it, leaving messages like "Invalid
    recipe at name: Input should be a valid string" — actionable only
    after opening the source file. Including the value as ``(got: ...)``
    makes the error self-contained.
    """

    pyd = _pydantic_error({"name": 5, "count": "two"})
    err = ValidationError("recipe", pyd)
    text = str(err)
    # ``!r`` quoting keeps strings and integers visually distinct.
    assert "got: 5" in text
    assert "got: 'two'" in text


def test_validation_error_caps_long_input_values() -> None:
    """A whole-object validation failure does not produce a wall of text.

    Pydantic surfaces the full input in ``input`` when validation fails
    at the top level. Without a cap, the rendered ``(got: ...)`` line
    on stderr is hundreds of characters wide. The cap truncates with
    an ellipsis so the format stays one-line-per-error.
    """

    class Outer(BaseModel):
        name: str

    # A non-dict input causes Pydantic to surface the entire value as
    # ``input`` on the top-level error row — the worst case for
    # rendering length.
    big_input = "x" * 500
    try:
        Outer.model_validate(big_input)
    except PydanticValidationError as exc:
        pyd = exc
    else:
        raise AssertionError("expected a ValidationError")

    err = ValidationError("primitive", pyd)
    text = str(err)
    # Loose upper bound captures intent — "doesn't blow up stderr" —
    # without coupling the test to the exact format string.
    for line in text.splitlines():
        assert len(line) < 250
    # Truncation marker appears only when the input was actually
    # truncated, which it must be for a 500-char input.
    assert "..." in text


def test_validation_error_preserves_original_pydantic_error() -> None:
    pyd = _pydantic_error({"name": "ok"})
    err = ValidationError("primitive", pyd)
    assert err.pydantic_error is pyd
    assert err.thing == "primitive"


def test_journal_corrupt_error_carries_line_number() -> None:
    err = JournalCorruptError(line=7, reason="missing discriminator")
    assert err.line == 7
    assert "7" in str(err)
    assert "missing discriminator" in str(err)


def test_wiki_error_is_catchable_as_a_single_base() -> None:
    pyd = _pydantic_error({"name": "ok"})
    errors_raised: list[Exception] = [
        ValidationError("primitive", pyd),
        JournalCorruptError(line=1, reason="bad json"),
    ]
    for e in errors_raised:
        with pytest.raises(WikiError):
            raise e
