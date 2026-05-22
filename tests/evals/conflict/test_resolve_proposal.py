"""Conflict eval: `wiki-conflict` resolves a pending sidecar via `wiki resolve`.

Plan §5d. Tests the full three-way merge flow: prompt Claude about
a pending sidecar, expect it to load the `wiki-conflict` SKILL,
walk the user through a merge, and commit via `wiki resolve` —
which emits a ``PageConflictResolvedEvent`` and removes the
sidecar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki_kit.models import PageConflictResolvedEvent
from tests import evalkit
from tests.evals.conftest import CONFLICT_FIXTURE_PATH

pytestmark = pytest.mark.eval


def test_wiki_conflict_resolves_pending_sidecar(
    conflict_pending_vault: Path,
) -> None:
    evalkit.skip_if_env_unset("ANTHROPIC_API_KEY")
    evalkit.skip_if_no_claude()

    sidecar = conflict_pending_vault / (CONFLICT_FIXTURE_PATH + ".proposed")
    assert sidecar.is_file(), "fixture precondition: sidecar must exist"

    prompt = (
        f"There's a pending .proposed sidecar at {CONFLICT_FIXTURE_PATH}.proposed. "
        f"Walk me through resolving it — pick a sensible merge and commit it "
        f"through `wiki resolve`."
    )
    result = evalkit.run_claude(
        prompt=prompt,
        vault=conflict_pending_vault,
        # `wiki journal *` is intentionally NOT allowed: the
        # wiki-conflict SKILL flags `wiki journal explain` as
        # "not yet implemented" (see core/files/skills/wiki-conflict
        # /SKILL.md), so allowing it would invite claude to call
        # an unshipped subcommand and abandon the resolve flow.
        allowed_tools=[
            "Read",
            "Edit",
            "Bash(wiki resolve *)",
            "Bash(wiki doctor)",
        ],
        timeout_s=240.0,
    )
    if result.timed_out:
        pytest.fail(f"claude timed out: {evalkit.redact(result.stderr[:400])}")

    # Post-condition 1: sidecar is gone.
    assert not sidecar.exists(), (
        f"sidecar still present after resolve; "
        f"stdout[:1000]={evalkit.redact(result.stdout[:1000])!r}"
    )

    # Post-condition 2: journal has a PageConflictResolvedEvent.
    events = evalkit.read_journal_events(conflict_pending_vault)
    evalkit.assert_journal_has(
        events,
        kind=PageConflictResolvedEvent,
        path=CONFLICT_FIXTURE_PATH,
    )
