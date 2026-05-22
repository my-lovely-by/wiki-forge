"""Research eval: `wiki research` dispatcher contract (no `claude` invocation).

Plan §5e. Runs ``cli.main(["research", ...])`` directly and asserts
on the dispatcher's contract: a ``ResearchQueryEvent`` is journaled,
the rendered markdown matches the documented frontmatter shape, and
the rendered output starts with the canonical ``---\\nprovider:\\n``
prefix. This eval *does not* invoke ``claude`` — provider-agnostic
contract checks are also CLI-driven; that's why this scenario carries
``@pytest.mark.eval`` despite skipping the subprocess.

Provider-agnosticism (every Task 19 provider stays registered) is
checked separately in
``tests/unit/test_evalkit_research_registry.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.models import ResearchQueryEvent
from llm_wiki_kit.research.providers import perplexity
from llm_wiki_kit.research.providers.perplexity import PerplexityResult
from tests import evalkit

pytestmark = pytest.mark.eval


def test_research_dispatch_journals_event_and_renders_markdown(
    research_dispatch_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Same-process monkeypatch works here — we run cli.main, not claude."""

    def _fake(config: Any, query: str) -> PerplexityResult:
        return PerplexityResult(
            answer="Test answer.",
            citations=["https://example.invalid/dispatch"],
            model=config.model or "sonar-pro",
        )

    monkeypatch.setattr(perplexity, "dispatch", _fake)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-fake-for-dispatch-test")
    monkeypatch.chdir(research_dispatch_vault)

    assert cli.main(["research", "what is the kit"]) == 0
    captured = capsys.readouterr()
    # Frontmatter shape from llm_wiki_kit/research/dispatch.py:_render_markdown.
    assert captured.out.startswith("---\nprovider: perplexity\n"), (
        f"unexpected markdown prefix: {captured.out[:120]!r}"
    )
    assert "citations:" in captured.out

    events = evalkit.read_journal_events(research_dispatch_vault)
    evalkit.assert_journal_has(
        events,
        kind=ResearchQueryEvent,
        provider="perplexity",
        status="ok",
    )
