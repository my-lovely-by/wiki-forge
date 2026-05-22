"""Research eval: live Perplexity HTTP — skip-with-status on upstream error.

Plan §5f and Spec AC12. Hits the real Perplexity API when
``PERPLEXITY_API_KEY`` is set; skips with a reason naming the HTTP
status and body excerpt when the provider returns a 5xx or
rate-limit. Never xfail.

Budget note: this scenario is *not* gated by ``EVAL_MAX_BUDGET_USD``
(no ``claude`` subprocess); Perplexity's own billing is the cap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.models import ResearchQueryEvent
from tests import evalkit

pytestmark = pytest.mark.eval

# AC12: skip reason must carry "the numeric HTTP status."
# `ResearchHTTPError.__str__` renders the kit's documented prefix —
# pin against the numeric form, not a bare "HTTP" substring (which
# could match a config error mentioning "HTTP-only TLS" or similar).
_HTTP_STATUS_RE = re.compile(r"HTTP\s+\d{3}\b")


def test_live_perplexity_journals_event_with_real_citations(
    research_dispatch_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evalkit.skip_if_env_unset("PERPLEXITY_API_KEY")
    monkeypatch.chdir(research_dispatch_vault)

    # `cli.main` catches every WikiError (including ResearchHTTPError)
    # internally and returns the kit's error exit code, writing the
    # redacted error message to stderr. The skip reason captures that
    # stderr line, which carries "HTTP <status>" plus the redacted
    # message — AC12's "numeric HTTP status and the kit's redacted
    # error message" contract is satisfied by stderr scraping.
    # `ResearchHTTPError` itself never escapes the CLI; no except
    # block can catch it here.
    rc = cli.main(["research", "what does the llm-wiki-kit Python package do"])

    if rc != 0:
        captured = capsys.readouterr()
        # AC12 requires the skip reason to carry the numeric HTTP
        # status. If stderr doesn't name an HTTP <NNN> status, the
        # kit exited non-zero for a reason that isn't a provider-side
        # transient failure — that's a harness/contract regression,
        # not a flake. Fail loud instead of silently skipping.
        match = _HTTP_STATUS_RE.search(captured.err)
        if not match:
            # Redact the stderr dump on this fail path — a future
            # cli.main error-rendering change might leak more than
            # we expect into stderr, and junit XML is a public CI
            # artifact.
            pytest.fail(
                f"live perplexity returned non-zero ({rc}) but stderr "
                f"does not name an HTTP <NNN> status — non-WikiError "
                f"exit path? stderr={evalkit.redact(captured.err[:400])!r}"
            )
        # AC12 requires the skip reason to carry the HTTP status. We
        # deliberately do NOT include the full stderr here — the
        # kit's ResearchHTTPError is narrow today (status + redacted
        # message), but a future change could broaden it. The
        # matched HTTP-NNN token is the documented diagnostic.
        pytest.skip(f"live perplexity returned non-zero ({rc}); {match.group(0)}")

    captured = capsys.readouterr()
    assert captured.out.startswith("---\nprovider: perplexity\n"), (
        f"unexpected markdown prefix: {captured.out[:120]!r}"
    )
    # Real Perplexity responses contain at least one citation in the
    # frontmatter list. If none, surface as a real failure.
    assert "https://" in captured.out, (
        f"no URL-shaped citation in rendered output:\n{captured.out[:600]}"
    )

    events = evalkit.read_journal_events(research_dispatch_vault)
    event = evalkit.assert_journal_has(
        events,
        kind=ResearchQueryEvent,
        provider="perplexity",
        status="ok",
    )
    assert isinstance(event, ResearchQueryEvent)
    assert event.model, "live event should carry a real model name"
