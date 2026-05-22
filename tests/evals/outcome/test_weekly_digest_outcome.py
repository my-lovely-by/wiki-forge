"""Outcome eval: `wiki run weekly-digest` + SKILL produces the contracted digest page.

Plan §5b. Two-stage assertion: dispatch journals an
``OperationRunEvent``, then the SKILL writes the digest page. The
pre-condition (digest path absent) is also asserted so a passing
test cannot be faked by the dispatcher alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from llm_wiki_kit.models import OperationRunEvent
from tests import evalkit

pytestmark = pytest.mark.eval

EXPECTED_WINDOW = "2026-W20"


class WeeklyDigestFrontmatter(BaseModel):
    """Contract assertion for the digest page's YAML frontmatter.

    Mirrors the SKILL's frontmatter contract documented at
    ``templates/operations/weekly-digest/files/skills/weekly-digest/SKILL.md``.
    Defined inline because it's a test-only assertion, not a
    kit-public type. Tight on values, not just types — a digest
    page with the wrong window/type/tags shouldn't pass the eval.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["digest"]
    digest_window: str
    tags: list[str]

    @field_validator("digest_window")
    @classmethod
    def _window_matches(cls, v: str) -> str:
        if v != EXPECTED_WINDOW:
            raise ValueError(f"digest_window must be {EXPECTED_WINDOW!r}, got {v!r}")
        return v

    @field_validator("tags")
    @classmethod
    def _tags_exactly_required(cls, v: list[str]) -> list[str]:
        """Pin tags to the SKILL's documented set.

        The weekly-digest SKILL frontmatter contract names exactly
        `weekly-digest` and the window. A digest with extra tags is
        either a documented ontology change (which should tighten
        this test in the same PR) or a Claude misread of the
        template — both want to be visible.
        """

        required = {"weekly-digest", EXPECTED_WINDOW}
        if set(v) != required:
            raise ValueError(f"tags must equal {sorted(required)} (any extras count); got {v!r}")
        return v


def _parse_frontmatter(text: str) -> dict[str, Any]:
    parts = text.split("---", 2)
    assert len(parts) >= 3, "page missing YAML frontmatter"
    return yaml.safe_load(parts[1]) or {}


def test_weekly_digest_produces_expected_page(
    weekly_digest_vault: Path,
) -> None:
    evalkit.skip_if_env_unset("ANTHROPIC_API_KEY")
    evalkit.skip_if_no_claude()

    digest_path = weekly_digest_vault / "outputs" / "digests" / "2026-W20.md"
    # Pre-assertion: digest doesn't exist yet — without this, a test
    # that pre-existing-passes could mask a failure where claude
    # writes nothing.
    assert not digest_path.exists()

    prompt = (
        "Run `wiki run weekly-digest --window=2026-W20` and then follow "
        "the SKILL it points you at to actually write the digest page "
        "at outputs/digests/2026-W20.md. Summarize the meeting at "
        "meetings/2026-05-12-q2-planning-kickoff.md."
    )
    result = evalkit.run_claude(
        prompt=prompt,
        vault=weekly_digest_vault,
        allowed_tools=["Read", "Write", "Edit", "Bash(wiki run *)"],
        timeout_s=240.0,
    )
    if result.timed_out:
        pytest.fail(f"claude timed out: {evalkit.redact(result.stderr[:400])}")

    # Post-assertion 1: digest page exists.
    assert digest_path.is_file(), (
        f"claude did not write the digest page; "
        f"stdout[:1000]={evalkit.redact(result.stdout[:1000])!r}"
    )

    # Post-assertion 2: frontmatter validates against the SKILL's contract.
    body = digest_path.read_text(encoding="utf-8")
    try:
        WeeklyDigestFrontmatter.model_validate(_parse_frontmatter(body))
    except ValidationError as exc:
        pytest.fail(
            f"digest frontmatter invalid: {exc}\nfile: {digest_path}\n"
            f"first 400 chars of body:\n{body[:400]}"
        )

    # Post-assertion 3: journal has the dispatch event.
    events = evalkit.read_journal_events(weekly_digest_vault)
    evalkit.assert_journal_has(
        events,
        kind=OperationRunEvent,
        operation="weekly-digest",
        status="dispatched",
    )
