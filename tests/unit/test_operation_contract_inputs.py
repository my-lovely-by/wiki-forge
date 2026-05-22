"""Tests for the tightened ``OperationContract.inputs`` schema.

RFC-0001 Task 17 (``docs/specs/task-17-wiki-run/spec.md``) tightens
``OperationContract.inputs`` from ``dict[str, object]`` to
``dict[str, OperationInputSpec]``. These tests pin the new shape:

- Every shipped ``templates/operations/*/contract.yaml`` continues
  to validate.
- The ``OperationInputSpec`` model captures the on-disk fields used
  across the catalog (``type``, ``description``, ``default``,
  ``optional``, ``items``).
- ``_StrictModel`` (``extra="forbid"``) rejects typos.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError as PydanticValidationError

from llm_wiki_kit.models import OperationContract, OperationInputSpec

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OPERATIONS_DIR = REPO_ROOT / "templates" / "operations"


def test_loads_weekly_digest_contract() -> None:
    payload = yaml.safe_load((OPERATIONS_DIR / "weekly-digest" / "contract.yaml").read_text())
    contract = OperationContract.model_validate(payload)
    assert contract.inputs["window"].type == "iso_week"
    assert contract.inputs["sources"].type == "list"
    assert contract.inputs["sources"].items == "content-type"
    assert contract.inputs["sources"].default == ["meeting"]


@pytest.mark.parametrize(
    "contract_path",
    sorted(OPERATIONS_DIR.glob("*/contract.yaml")),
    ids=lambda p: p.parent.name,
)
def test_loads_every_shipped_contract(contract_path: Path) -> None:
    payload = yaml.safe_load(contract_path.read_text())
    OperationContract.model_validate(payload)


def test_int_alias_is_accepted_on_inputspec() -> None:
    spec = OperationInputSpec.model_validate({"type": "int", "default": 30})
    assert spec.type == "int"
    assert spec.default == 30


def test_integer_spelling_is_accepted_on_inputspec() -> None:
    spec = OperationInputSpec.model_validate({"type": "integer", "default": 90})
    assert spec.type == "integer"
    assert spec.default == 90


def test_unknown_field_on_inputspec_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        OperationInputSpec.model_validate(
            {"type": "string", "frobnicate": True},
        )


def test_inputspec_defaults_are_none_when_omitted() -> None:
    spec = OperationInputSpec.model_validate({"type": "string"})
    assert spec.default is None
    assert spec.optional is False
    assert spec.items is None
    assert spec.description is None


def test_inputspec_optional_marker_round_trips() -> None:
    spec = OperationInputSpec.model_validate(
        {"type": "string", "optional": True, "description": "x"},
    )
    assert spec.optional is True
    assert spec.description == "x"


def test_inputs_dict_is_typed_not_object() -> None:
    """``OperationContract.inputs`` must validate as ``OperationInputSpec``."""

    contract = OperationContract.model_validate(
        {
            "name": "weekly-digest",
            "description": "x",
            "inputs": {"window": {"type": "iso_week"}},
        },
    )
    # Confirms tightening took: an OperationInputSpec, not a raw dict.
    assert isinstance(contract.inputs["window"], OperationInputSpec)
