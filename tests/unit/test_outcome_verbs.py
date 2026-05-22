"""Unit tests for outcome-named entry points — PR-1.

Pins:

- ``OperationContract.outcomes`` schema (spec §Inputs §1, AC
  "Schema").
- ``RESERVED_OUTCOME_VERBS`` matches the set of registered
  top-level ``wiki`` subcommands plus the standard discovery
  aliases (spec §Inputs §2 rule 3).
- ``OUTCOME_VERB_STEMS`` carries the illustrative stem list the
  spec names (spec §Inputs §2 rule 4).
- ``is_well_formed_outcome_verb`` enforces rules 1-4 and 6 (spec
  §Inputs §2, AC "Well-formed verb").
- ``check_outcome_verb_uniqueness`` enforces rule 5 plus the
  verb-vs-operation-name shadow check (spec §Edge case "Verb
  collision within the catalog", AC "Catalog uniqueness", AC
  "Verb does not shadow any operation name").
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from llm_wiki_kit.cli import build_parser
from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.models import OperationContract
from llm_wiki_kit.primitives import (
    OUTCOME_VERB_STEMS,
    RESERVED_OUTCOME_VERBS,
    check_outcome_verb_uniqueness,
    is_well_formed_outcome_verb,
)

# ---------------------------------------------------------------------------
# Step 1 — ``OperationContract.outcomes`` schema
# ---------------------------------------------------------------------------


def _base_contract_payload() -> dict[str, object]:
    """Minimal valid ``OperationContract`` payload (no outcomes)."""

    return {
        "name": "weekly-digest",
        "description": "Summarize the week.",
    }


def test_operation_contract_accepts_outcomes_list() -> None:
    payload = _base_contract_payload() | {"outcomes": ["digest"]}
    contract = OperationContract.model_validate(payload)
    assert contract.outcomes == ["digest"]


def test_operation_contract_defaults_outcomes_to_empty_list() -> None:
    contract = OperationContract.model_validate(_base_contract_payload())
    assert contract.outcomes == []


def test_operation_contract_outcomes_accepts_empty_explicitly() -> None:
    payload = _base_contract_payload() | {"outcomes": []}
    contract = OperationContract.model_validate(payload)
    assert contract.outcomes == []


def test_operation_contract_rejects_unknown_field() -> None:
    payload = _base_contract_payload() | {"extras": "foo"}
    with pytest.raises(PydanticValidationError):
        OperationContract.model_validate(payload)


# ---------------------------------------------------------------------------
# Step 2 — ``RESERVED_OUTCOME_VERBS`` and ``OUTCOME_VERB_STEMS`` constants
# ---------------------------------------------------------------------------


def _registered_subcommands() -> set[str]:
    """Walk ``build_parser()`` and collect every top-level subcommand."""

    parser = build_parser()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            return set(choices.keys())
    raise AssertionError("build_parser() exposes no top-level subparsers")


def test_reserved_outcome_verbs_matches_subcommand_set() -> None:
    """The constant is exactly the static subcommands plus discovery aliases.

    Pins spec §Inputs §2 rule 3 ("literal enumeration of the
    current ``wiki`` subcommand set as registered in ``cli.py``
    argparse plus the standard discovery aliases"). Three
    independent assertions catch every direction the reviewer
    can break:

    1. Every discovery alias is reserved (catches a future PR
       that drops ``"outcomes"`` from the set after PR-5 makes
       it a real subcommand — the discovery aliases never go
       away).
    2. Every registered subcommand is reserved (catches a new
       subcommand added in ``cli.py`` without an update here).
    3. Nothing else is reserved (catches a stale entry that
       neither corresponds to a subcommand nor a discovery
       alias).
    """

    subcommands = _registered_subcommands()
    discovery_aliases = {"help", "version", "outcomes"}

    # 1. Discovery aliases never disappear.
    assert discovery_aliases <= RESERVED_OUTCOME_VERBS
    # 2. Every registered subcommand is reserved.
    assert subcommands <= RESERVED_OUTCOME_VERBS
    # 3. No stale entries beyond the union of subcommands and discovery aliases.
    assert RESERVED_OUTCOME_VERBS <= subcommands | discovery_aliases


def test_outcome_verb_stems_contains_bare_and_prefix_forms() -> None:
    """Pins the illustrative stem list spec §Inputs §2 rule 4 names.

    Containment-only (not set-equality), because spec rule 4
    treats the listed stems as illustrative — adding new stems
    is expected. **Removing a spec-listed stem requires either
    updating the spec's illustrative list in the same PR, or
    moving the example to a still-current spec entry** —
    silently dropping a stem the spec still cites is the
    failure mode this test catches.
    """

    # Bare-verb entries.
    assert "digest" in OUTCOME_VERB_STEMS
    assert "roll-up" in OUTCOME_VERB_STEMS
    # Prefix entries (a stem followed by a trailing hyphen).
    for prefix in (
        "plan-",
        "refresh-",
        "log-",
        "summarize-",
        "prep-",
        "review-",
        "track-",
        "synthesize-",
        "pack-",
        "remind-",
        "map-",
    ):
        assert prefix in OUTCOME_VERB_STEMS, prefix


# ---------------------------------------------------------------------------
# Step 3 — ``is_well_formed_outcome_verb``
# ---------------------------------------------------------------------------


_WELL_FORMED_VERBS: tuple[str, ...] = (
    "digest",
    "plan-meals",
    "refresh-stakeholders",
    "summarize-week",
    "track-budget",
)


@pytest.mark.parametrize("verb", _WELL_FORMED_VERBS)
def test_is_well_formed_outcome_verb_accepts(verb: str) -> None:
    # Must not raise.
    is_well_formed_outcome_verb(verb)


@pytest.mark.parametrize(
    ("verb", "expected_phrase"),
    [
        ("a--b", "consecutive hyphens"),
        ("ab-", "trailing hyphen"),
        ("1ab", "leading digit"),
        # 3+ chars so the length rule does not pre-empt the case rule.
        ("Abc", "ASCII lowercase"),
        ("ab", "3-24"),
        ("a" * 25, "3-24"),
        ("wiki-foo", "wiki-"),
        ("meals", "verb-stem"),
        ("weekly-summary", "verb-stem"),
        ("doctor", "reserved"),
        ("digést", "ASCII"),
    ],
)
def test_is_well_formed_outcome_verb_rejects(verb: str, expected_phrase: str) -> None:
    with pytest.raises(WikiError) as excinfo:
        is_well_formed_outcome_verb(verb)
    # Each rejection message names the rule that triggered it.
    assert expected_phrase in str(excinfo.value), (verb, str(excinfo.value))


# ---------------------------------------------------------------------------
# Step 4 — ``check_outcome_verb_uniqueness``
# ---------------------------------------------------------------------------


def _contract(name: str, outcomes: list[str] | None = None) -> OperationContract:
    return OperationContract.model_validate(
        {
            "name": name,
            "description": f"{name} operation.",
            "outcomes": outcomes or [],
        }
    )


def test_uniqueness_passes_with_disjoint_verbs() -> None:
    contracts = [
        _contract("weekly-digest", ["digest"]),
        _contract("meal-planning", ["plan-meals"]),
    ]
    # Must not raise.
    check_outcome_verb_uniqueness(contracts)


def test_uniqueness_passes_with_empty_outcomes() -> None:
    contracts = [
        _contract("weekly-digest"),
        _contract("meal-planning"),
        _contract("stakeholder-map-refresh"),
    ]
    check_outcome_verb_uniqueness(contracts)


def test_uniqueness_fails_on_collision() -> None:
    contracts = [
        _contract("weekly-digest", ["digest"]),
        _contract("other-digest", ["digest"]),
    ]
    with pytest.raises(WikiError) as excinfo:
        check_outcome_verb_uniqueness(contracts)
    msg = str(excinfo.value)
    assert "digest" in msg
    assert "weekly-digest" in msg
    assert "other-digest" in msg


def test_uniqueness_fails_on_verb_equals_operation_name_cross_operation() -> None:
    # Operation ``weekly-digest`` exists; a different operation claims it
    # as its own outcome verb — disallowed even though no verb-vs-verb
    # collision exists, because ``wiki <verb>`` would shadow
    # ``wiki run weekly-digest``'s alias resolution.
    contracts = [
        _contract("weekly-digest"),
        _contract("other-op", ["weekly-digest"]),
    ]
    with pytest.raises(WikiError) as excinfo:
        check_outcome_verb_uniqueness(contracts)
    msg = str(excinfo.value)
    assert "weekly-digest" in msg
    assert "other-op" in msg


def test_uniqueness_fails_on_verb_equals_own_operation_name() -> None:
    # The declaring operation cannot claim its own name as a verb either —
    # the disjoint-sets invariant covers both cases (spec Invariant 8).
    contracts = [_contract("weekly-digest", ["weekly-digest"])]
    with pytest.raises(WikiError) as excinfo:
        check_outcome_verb_uniqueness(contracts)
    msg = str(excinfo.value)
    assert "weekly-digest" in msg
