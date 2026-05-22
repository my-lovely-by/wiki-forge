"""Provenance eval: citations propagate from research result into the consuming note.

Plan §5c. Tests *propagation* — given a research page with citations
frontmatter, does Claude read it and cite it in the consuming note?
The dispatch side (``wiki research`` invocation, real provider HTTP)
is covered by 5e (contract) and 5f (live).

A pytest ``monkeypatch.setattr`` can't reach across the process
boundary into the subprocess ``claude`` would spawn for ``wiki
research`` — so this scenario pre-populates ``research/deployment.md``
in the seed factory and asserts on the consuming note.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import evalkit

pytestmark = pytest.mark.eval


def test_research_citations_appear_in_consuming_note(
    research_cited_vault: Path,
) -> None:
    evalkit.skip_if_env_unset("ANTHROPIC_API_KEY")
    evalkit.skip_if_no_claude()

    source = research_cited_vault / "research" / "deployment.md"
    assert source.is_file(), "research_cited fixture missing pre-populated research page"
    source_before = source.read_text(encoding="utf-8")

    prompt = (
        "Read research/deployment.md and write "
        "meetings/notes-on-deployment.md summarizing the finding and "
        "citing the research page so a reader can navigate back to it."
    )
    result = evalkit.run_claude(
        prompt=prompt,
        vault=research_cited_vault,
        allowed_tools=["Read", "Write", "Edit"],
        timeout_s=180.0,
    )
    if result.timed_out:
        pytest.fail(f"claude timed out: {evalkit.redact(result.stderr[:400])}")

    note = research_cited_vault / "meetings" / "notes-on-deployment.md"
    assert note.is_file(), (
        f"claude did not write the consuming note; "
        f"stdout[:1000]={evalkit.redact(result.stdout[:1000])!r}"
    )
    body = note.read_text(encoding="utf-8")
    # Substring on the file path — not pinned to wikilink form
    # because the citation-format contract isn't pinned in the
    # SKILL yet.
    assert "research/deployment" in body, (
        f"consuming note doesn't cite research/deployment; body:\n{body[:600]}"
    )

    # Source unchanged (the eval reads-and-cites, doesn't mutate).
    assert source.read_text(encoding="utf-8") == source_before
