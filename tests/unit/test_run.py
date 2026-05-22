"""Tests for the pure helpers in ``llm_wiki_kit.run``.

The plan splits ``run.py`` into one orchestration function
(``dispatch``, tested separately in ``test_run_dispatch.py``) and
three pure helpers:

- ``_parse_op_args`` — raw CLI tokens → name/value dict.
- ``_coerce_input`` — raw value + ``OperationInputSpec`` → typed
  Python value.
- ``_load_contract`` — kit root + operation name → ``OperationContract``.

This module covers the first two; ``_load_contract`` is exercised
end-to-end in ``test_run_dispatch.py``.
"""

from __future__ import annotations

import pytest

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.models import OperationInputSpec
from llm_wiki_kit.run import ArgCoercionError, _coerce_input, _parse_op_args

# ---------------------------------------------------------------------------
# _parse_op_args
# ---------------------------------------------------------------------------


class TestParseOpArgs:
    def test_name_value_pairs(self) -> None:
        assert _parse_op_args(["--window=2026-W20", "--theme=summer"]) == {
            "window": "2026-W20",
            "theme": "summer",
        }

    def test_bare_flag_is_true_sentinel(self) -> None:
        # Kebab→snake normalisation applied; bare flag becomes the
        # sentinel string "true" so the journal records semantic
        # equivalence with --flag=true.
        assert _parse_op_args(["--include-open-ended"]) == {"include_open_ended": "true"}

    def test_kebab_and_snake_both_normalise(self) -> None:
        assert _parse_op_args(["--include-open-ended=false", "--theme=summer"]) == {
            "include_open_ended": "false",
            "theme": "summer",
        }
        assert _parse_op_args(["--include_open_ended=false"]) == {"include_open_ended": "false"}

    def test_uppercase_in_name_is_lower_cased(self) -> None:
        assert _parse_op_args(["--Window=x"]) == {"window": "x"}

    def test_repeated_name_last_wins(self) -> None:
        assert _parse_op_args(["--window=a", "--window=b"]) == {"window": "b"}
        # Mixed kebab/snake collapse.
        assert _parse_op_args(["--include-open-ended=a", "--include_open_ended=b"]) == {
            "include_open_ended": "b"
        }
        # Bare-then-value: value-form wins.
        assert _parse_op_args(["--include-open-ended", "--include-open-ended=false"]) == {
            "include_open_ended": "false"
        }
        # Value-then-bare: bare's sentinel wins.
        assert _parse_op_args(["--include-open-ended=false", "--include-open-ended"]) == {
            "include_open_ended": "true"
        }

    def test_empty_value(self) -> None:
        assert _parse_op_args(["--sources="]) == {"sources": ""}

    def test_value_with_equals_inside(self) -> None:
        # Split on the FIRST `=` only.
        assert _parse_op_args(["--query=a=b"]) == {"query": "a=b"}

    def test_positional_token_raises(self) -> None:
        with pytest.raises(WikiError) as excinfo:
            _parse_op_args(["banana", "--window=x"])
        assert "banana" in str(excinfo.value)

    def test_lone_dash_dash_raises(self) -> None:
        with pytest.raises(WikiError):
            _parse_op_args(["--"])

    def test_empty_name_raises(self) -> None:
        with pytest.raises(WikiError):
            _parse_op_args(["--=value"])

    def test_first_position_preserved_on_last_wins(self) -> None:
        """Last-wins on value, first-position on iteration order."""

        result = _parse_op_args(["--window=2026-W20", "--frobnicate=x", "--window=banana"])
        # `window` first appeared at position 1, so it stays at
        # index 0 in dict iteration order.
        assert list(result.keys()) == ["window", "frobnicate"]
        # Last-wins-on-value:
        assert result["window"] == "banana"
        assert result["frobnicate"] == "x"


# ---------------------------------------------------------------------------
# _coerce_input
# ---------------------------------------------------------------------------


def _spec(type_: str, **kw: object) -> OperationInputSpec:
    payload: dict[str, object] = {"type": type_, **kw}
    return OperationInputSpec.model_validate(payload)


class TestCoerceInput:
    def test_string(self) -> None:
        assert _coerce_input("foo", _spec("string")) == "foo"

    def test_integer_spelling(self) -> None:
        assert _coerce_input("30", _spec("integer")) == 30

    def test_int_alias(self) -> None:
        assert _coerce_input("30", _spec("int")) == 30

    def test_integer_invalid_raises(self) -> None:
        with pytest.raises(ArgCoercionError):
            _coerce_input("banana", _spec("integer"))

    def test_boolean_truthy(self) -> None:
        # Mixed-case variants pinned so a future refactor that drops
        # the explicit ``.lower()`` fails loudly.
        for value in ("true", "TRUE", "True", "yes", "YES", "Yes", "1", "on", "ON"):
            assert _coerce_input(value, _spec("boolean")) is True

    def test_boolean_falsy(self) -> None:
        for value in ("false", "FALSE", "False", "no", "NO", "No", "0", "off", "OFF"):
            assert _coerce_input(value, _spec("boolean")) is False

    def test_boolean_invalid_raises(self) -> None:
        with pytest.raises(ArgCoercionError):
            _coerce_input("banana", _spec("boolean"))

    def test_iso_week_valid(self) -> None:
        assert _coerce_input("2026-W20", _spec("iso_week")) == "2026-W20"
        # Calendar-validity not checked: W53 in a non-53 year is accepted.
        assert _coerce_input("2025-W53", _spec("iso_week")) == "2025-W53"

    def test_iso_week_invalid_format_raises(self) -> None:
        for bad in ("2026-20", "2026-W3", "banana", "2026-W00", "2026-W54", "2026-W99"):
            with pytest.raises(ArgCoercionError):
                _coerce_input(bad, _spec("iso_week"))

    def test_list_comma_split(self) -> None:
        assert _coerce_input("a,b,c", _spec("list")) == ["a", "b", "c"]
        assert _coerce_input("a", _spec("list")) == ["a"]
        assert _coerce_input("", _spec("list")) == []

    def test_list_strips_whitespace(self) -> None:
        assert _coerce_input("a , b ,c", _spec("list")) == ["a", "b", "c"]

    def test_list_preserves_empty_mid_elements(self) -> None:
        # Per spec: only fully empty input collapses to []; mid-string
        # empties survive as empty strings.
        assert _coerce_input("a,,b", _spec("list")) == ["a", "", "b"]

    def test_unknown_type_passes_through_as_str(self) -> None:
        # Forward-compat: type: page (in trip-prep) coerces to str.
        assert _coerce_input("alice", _spec("page")) == "alice"
        assert _coerce_input("foo", _spec("frobnicate")) == "foo"
