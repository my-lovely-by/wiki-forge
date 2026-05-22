"""End-to-end ``wiki research`` integration tests (RFC-0001 Task 18).

Drives the CLI against a tmp kit + tmp vault. The kit ships the real
``core`` plus a minimal recipe and the two new infrastructure
primitives (``research`` and ``research-perplexity``). Perplexity's
``dispatch`` is monkeypatched in tests that exercise the happy path so
no real HTTP fires; the API-key-safety tests run against the unpatched
provider with ``urllib.request.urlopen`` mocked at the very bottom of
the stack so the real env-var read and header construction execute.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import (
    LockAcquiredEvent,
    LockReleasedEvent,
    PageProposalEvent,
    PageWriteEvent,
    PrimitiveInstallEvent,
    ResearchQueryEvent,
)
from llm_wiki_kit.research import http as http_module
from llm_wiki_kit.research.providers import perplexity
from llm_wiki_kit.research.providers.perplexity import PerplexityResult

REPO_ROOT = Path(__file__).resolve().parents[2]


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    """A tmp kit with ``core`` + the two research primitives + minimal recipe."""

    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    shutil.copytree(REPO_ROOT / "templates", kit / "templates")

    recipes_dir = kit / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "minimal.yaml").write_text(
        "name: minimal\n"
        "version: 0.1.0\n"
        "description: Core only — research primitives added via wiki add.\n"
        "primitives: []\n"
        "variables:\n"
        "  recipe_name: minimal\n",
        encoding="utf-8",
    )
    return kit


@pytest.fixture
def fresh_vault(tmp_path: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault with only the core primitive installed (no research wiring yet)."""

    v = tmp_path / "vault"
    assert cli.main(["init", str(v), "--recipe", "minimal"], kit_root=kit_root) == 0
    monkeypatch.chdir(v)
    return v


@pytest.fixture
def vault_with_research(fresh_vault: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault with both research primitives installed via ``wiki add``."""

    assert cli.main(["add", "infrastructure:research"], kit_root=kit_root) == 0
    assert cli.main(["add", "infrastructure:research-perplexity"], kit_root=kit_root) == 0
    return fresh_vault


def _install_fake_perplexity(
    monkeypatch: pytest.MonkeyPatch,
    answer: str = "Answer body.",
    citations: list[str] | None = None,
) -> None:
    """Replace ``perplexity.dispatch`` with a deterministic fake.

    Uses ``setattr`` against the provider module so the dispatcher's
    re-binding wrapper sees the patched function at call time. Tests
    that need the *real* env-var read or HTTP layer don't use this
    fixture.
    """

    citations = citations if citations is not None else ["https://example/a"]

    def _fake(config: Any, query: str) -> PerplexityResult:
        return PerplexityResult(
            answer=answer, citations=list(citations), model=config.model or "sonar-pro"
        )

    monkeypatch.setattr(perplexity, "dispatch", _fake)


def _research_events(vault: Path) -> list[ResearchQueryEvent]:
    events = read_events(_journal_path(vault))
    return [e for e in events if isinstance(e, ResearchQueryEvent)]


# ---------------------------------------------------------------------------
# Pre-condition errors — no vault, no providers
# ---------------------------------------------------------------------------


def test_wiki_research_no_vault_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["research", "q"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "not a wiki vault" in err


def test_wiki_research_no_providers_yaml_exits_2(
    fresh_vault: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fresh vault with no ``infrastructure:research`` installed surfaces clearly."""

    assert cli.main(["research", "q"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "infrastructure:research not installed" in err
    assert _research_events(fresh_vault) == []


def test_wiki_research_seed_only_no_providers_installed(
    fresh_vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Seed file present but managed region empty surfaces 'no providers'."""

    assert cli.main(["add", "infrastructure:research"], kit_root=kit_root) == 0
    assert cli.main(["research", "q"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "no research providers installed" in err


def test_wiki_research_typo_in_config_message_quotes_field(
    fresh_vault: Path, kit_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A hand-edited typo in the managed-region body surfaces with the bad field name."""

    assert cli.main(["add", "infrastructure:research"], kit_root=kit_root) == 0
    config = fresh_vault / "research-providers.yaml"
    config.write_text(
        "# BEGIN MANAGED: providers\n"
        "perplexity:\n"
        "  api_key_env: PERPLEXITY_API_KEY\n"
        "  endpiont: https://x\n"
        "# END MANAGED: providers\n",
        encoding="utf-8",
    )

    assert cli.main(["research", "q"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "research-providers.yaml" in err
    assert "endpiont" in err


# ---------------------------------------------------------------------------
# Happy path — stdout flow
# ---------------------------------------------------------------------------


def test_wiki_research_happy_path_stdout(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_perplexity(monkeypatch, answer="The answer.")

    assert cli.main(["research", "what is X"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("---\n")
    assert "provider: perplexity\n" in out
    assert "model: sonar-pro\n" in out
    assert "query: what is X\n" in out
    assert "The answer." in out

    events = _research_events(vault_with_research)
    assert len(events) == 1
    e = events[0]
    assert e.provider == "perplexity"
    assert e.model == "sonar-pro"
    assert e.status == "ok"
    assert e.result_path is None
    assert e.query == "what is X"


def test_wiki_research_two_invocations_journal_two_events(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No deduplication — audit-trail-first per spec invariant 3."""

    _install_fake_perplexity(monkeypatch)

    assert cli.main(["research", "same query"]) == 0
    assert cli.main(["research", "same query"]) == 0

    events = _research_events(vault_with_research)
    assert len(events) == 2
    assert all(e.query == "same query" for e in events)


# ---------------------------------------------------------------------------
# Happy path — --out flow
# ---------------------------------------------------------------------------


def test_wiki_research_happy_path_out_writes_via_safe_write(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_perplexity(monkeypatch, answer="Out body.")

    assert cli.main(["research", "what is X", "--out", "research/x.md"]) == 0

    out_file = vault_with_research / "research" / "x.md"
    assert out_file.is_file()
    content = out_file.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "Out body." in content

    events = read_events(_journal_path(vault_with_research))
    research_events = [e for e in events if isinstance(e, ResearchQueryEvent)]
    assert len(research_events) == 1
    assert research_events[0].result_path == "research/x.md"

    page_writes = [e for e in events if isinstance(e, PageWriteEvent) and e.path == "research/x.md"]
    assert len(page_writes) == 1


def test_wiki_research_out_wraps_events_in_transaction(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``--out`` event pair is bracketed by ``lock.acquired``/``released``.

    Pins the multi-event grouping spec §Behavior names so a future
    refactor that drops the ``journal.transaction`` wrap surfaces as
    a contract test failure.
    """

    _install_fake_perplexity(monkeypatch)

    pre_events = read_events(_journal_path(vault_with_research))
    assert cli.main(["research", "q", "--out", "research/x.md"]) == 0
    post_events = read_events(_journal_path(vault_with_research))

    new_events = post_events[len(pre_events) :]
    types = [e.type for e in new_events]
    assert types == [
        "lock.acquired",
        "research.query",
        "page.write",
        "lock.released",
    ]

    acquired = new_events[0]
    assert isinstance(acquired, LockAcquiredEvent)
    assert acquired.by == "wiki-research"
    assert acquired.reason == "research perplexity"

    released = new_events[-1]
    assert isinstance(released, LockReleasedEvent)
    assert released.by == "wiki-research"


def test_wiki_research_out_drift_routes_to_proposal(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pre-existing on-disk content lands as a ``.proposed`` sidecar.

    The ``research.query`` event records the *requested* path (the
    user's intent); the sidecar path lives on the ``page.proposal``
    event's ``proposed_path``.
    """

    _install_fake_perplexity(monkeypatch, answer="Kit answer.")

    out_path = vault_with_research / "research" / "x.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("user's pre-existing file\n", encoding="utf-8")

    assert cli.main(["research", "q", "--out", "research/x.md"]) == 0

    sidecar = out_path.with_name(out_path.name + ".proposed")
    assert sidecar.is_file()
    notice = capsys.readouterr().out
    assert "research/x.md.proposed" in notice or "drift" in notice

    events = read_events(_journal_path(vault_with_research))
    research_events = [e for e in events if isinstance(e, ResearchQueryEvent)]
    assert len(research_events) == 1
    assert research_events[0].result_path == "research/x.md"  # requested path

    proposals = [
        e for e in events if isinstance(e, PageProposalEvent) and e.path == "research/x.md"
    ]
    assert len(proposals) == 1
    assert proposals[0].proposed_path == "research/x.md.proposed"


# ---------------------------------------------------------------------------
# --out path validation
# ---------------------------------------------------------------------------


def test_wiki_research_out_absolute_path_rejected(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_perplexity(monkeypatch)
    pre = read_events(_journal_path(vault_with_research))

    assert cli.main(["research", "q", "--out", "/etc/passwd"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "--out" in err

    post = read_events(_journal_path(vault_with_research))
    assert post == pre  # no events appended


def test_wiki_research_out_traversal_path_rejected(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_perplexity(monkeypatch)
    pre = read_events(_journal_path(vault_with_research))

    assert cli.main(["research", "q", "--out", "../outside.md"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "--out" in err

    post = read_events(_journal_path(vault_with_research))
    assert post == pre


def test_wiki_research_out_symlink_escape_rejected(
    vault_with_research: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A symlink whose resolved target lives outside the vault is rejected.

    Spec invariant 8: ``--out`` paths must resolve under the vault
    root. The CLI's `_resolve_out_path` uses ``Path.resolve(strict=False)``
    which follows symlinks during traversal, so a parent symlink to
    ``/tmp/outside`` makes ``research/x.md`` resolve outside even
    though the user spelled a vault-relative path.
    """

    _install_fake_perplexity(monkeypatch)
    pre = read_events(_journal_path(vault_with_research))

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    # Symlink inside the vault that points at outside_dir.
    link = vault_with_research / "escape"
    link.symlink_to(outside_dir)

    assert cli.main(["research", "q", "--out", "escape/leak.md"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "--out" in err

    post = read_events(_journal_path(vault_with_research))
    assert post == pre


def test_wiki_research_missing_env_var_not_journaled(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Spec §"Error paths" row 7: env-var-unset is config-shaped, not journaled.

    With the unpatched provider and ``PERPLEXITY_API_KEY`` not set,
    ``wiki research`` exits 2 and adds zero ``research.query`` events.
    The audit trail records only requests the user's config licensed
    the kit to make.
    """

    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    pre = read_events(_journal_path(vault_with_research))

    assert cli.main(["research", "q"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "set PERPLEXITY_API_KEY" in err

    post = read_events(_journal_path(vault_with_research))
    assert post == pre


# ---------------------------------------------------------------------------
# Error paths — HTTP failure with status="error" journaling
# ---------------------------------------------------------------------------


def test_wiki_research_http_error_journals_error_event(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Provider raises after retries — CLI journals a status='error' event."""

    from llm_wiki_kit.research.http import ResearchHTTPError

    def _fail(config: Any, query: str) -> Any:
        raise ResearchHTTPError("perplexity: HTTP 401", status=401)

    monkeypatch.setattr(perplexity, "dispatch", _fail)

    assert cli.main(["research", "q"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "perplexity: HTTP 401" in err

    events = _research_events(vault_with_research)
    assert len(events) == 1
    assert events[0].status == "error"
    assert events[0].result_path is None


# ---------------------------------------------------------------------------
# API-key safety
# ---------------------------------------------------------------------------


def test_wiki_research_apikey_never_in_journal_or_stdout(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: real env-var read + real http helper (urlopen mocked).

    The recognisable key must not appear in any of stdout, stderr, or
    the journal file.
    """

    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-DO-NOT-LOG")

    response_body = b'{"choices":[{"message":{"content":"hi"}}],"citations":[]}'

    def _fake_urlopen(request: Any, timeout: float = 0.0) -> Any:
        class _Response:
            def read(self) -> bytes:
                return response_body

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

        return _Response()

    monkeypatch.setattr(http_module, "urlopen", _fake_urlopen)

    assert cli.main(["research", "q"]) == 0
    captured = capsys.readouterr()
    journal_text = _journal_path(vault_with_research).read_text(encoding="utf-8")

    assert "sk-DO-NOT-LOG" not in captured.out
    assert "sk-DO-NOT-LOG" not in captured.err
    assert "sk-DO-NOT-LOG" not in journal_text


def test_wiki_research_apikey_not_in_verbose_traceback(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--verbose`` traceback path also redacts the key.

    The underlying ``HTTPError`` carries a body string that includes the
    bearer prefix; ``ResearchHTTPError`` constructor + ``raise … from
    None`` keep the chain clean.
    """

    monkeypatch.setenv("PERPLEXITY_API_KEY", "sk-DO-NOT-LOG")

    def _fake_urlopen(request: Any, timeout: float = 0.0) -> Any:
        raise HTTPError(
            url="https://api.perplexity.ai/x",
            code=401,
            msg="Authorization: Bearer sk-DO-NOT-LOG",
            hdrs={"Content-Type": "application/json"},  # type: ignore[arg-type]
            fp=io.BytesIO(b"Bearer sk-DO-NOT-LOG"),
        )

    monkeypatch.setattr(http_module, "urlopen", _fake_urlopen)

    assert cli.main(["--verbose", "research", "q"]) == cli.WIKI_ERROR_EXIT
    captured = capsys.readouterr()
    journal_text = _journal_path(vault_with_research).read_text(encoding="utf-8")

    assert "sk-DO-NOT-LOG" not in captured.out
    assert "sk-DO-NOT-LOG" not in captured.err
    assert "sk-DO-NOT-LOG" not in journal_text


def test_wiki_research_journal_append_failure_preserves_dispatch_error(
    vault_with_research: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If the error-path journal append itself fails, the dispatch error wins.

    Spec invariant 10: ``ResearchDispatchError`` is re-raised with the
    journal exception as ``__cause__`` — never the other way around.
    """

    from llm_wiki_kit.research.http import ResearchHTTPError

    def _fail(config: Any, query: str) -> Any:
        raise ResearchHTTPError("perplexity: HTTP 401", status=401)

    monkeypatch.setattr(perplexity, "dispatch", _fail)

    from llm_wiki_kit import cli as cli_module
    from llm_wiki_kit.journal import append_event as original_append

    def _failing_append(journal_path: Any, event: Any, **kwargs: Any) -> None:
        if isinstance(event, ResearchQueryEvent):
            raise OSError("fsync failed")
        return original_append(journal_path, event, **kwargs)

    monkeypatch.setattr(cli_module, "append_event", _failing_append)

    assert cli.main(["research", "q"]) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    # The user sees the dispatch error message, not the OSError text.
    assert "perplexity: HTTP 401" in err


# ---------------------------------------------------------------------------
# Recipes — primitives must NOT be auto-added
# ---------------------------------------------------------------------------


def test_wiki_research_recipes_do_not_include_primitives() -> None:
    """``family``, ``work-os``, and ``personal`` recipes are opt-in.

    The spec invariant: a `wiki init --recipe family` produces a vault
    with no ``research-providers.yaml``. Verified by loading each
    recipe and asserting membership.
    """

    from llm_wiki_kit.recipes import load_recipe

    for name in ("family", "work-os", "personal"):
        path = REPO_ROOT / "recipes" / f"{name}.yaml"
        recipe = load_recipe(path)
        assert "research" not in recipe.primitives, f"{name}.yaml unexpectedly includes 'research'"
        for opt_in in (
            "research-perplexity",
            "research-gemini",
            "research-semantic-scholar",
        ):
            assert opt_in not in recipe.primitives, f"{name}.yaml unexpectedly includes '{opt_in}'"


# ---------------------------------------------------------------------------
# Requires-closure: wiki add research-perplexity pulls research
# ---------------------------------------------------------------------------


def test_wiki_add_research_perplexity_pulls_research_via_requires(
    fresh_vault: Path, kit_root: Path
) -> None:
    """``wiki add infrastructure:research-perplexity`` installs both, atomically.

    Spec invariant 5 (cannot half-install). Journal shows two
    ``primitive.install`` events with ``research`` strictly before
    ``research-perplexity``.
    """

    assert cli.main(["add", "infrastructure:research-perplexity"], kit_root=kit_root) == 0

    events = read_events(_journal_path(fresh_vault))
    installs = [e for e in events if isinstance(e, PrimitiveInstallEvent)]
    installed_in_order = [e.primitive for e in installs if e.by == "wiki-add"]
    assert "research" in installed_in_order
    assert "research-perplexity" in installed_in_order
    assert installed_in_order.index("research") < installed_in_order.index("research-perplexity")


# ---------------------------------------------------------------------------
# Task 19 — Gemini + Semantic Scholar end-to-end
# ---------------------------------------------------------------------------

from llm_wiki_kit.research.providers import gemini, semantic_scholar  # noqa: E402
from llm_wiki_kit.research.providers.gemini import GeminiResult  # noqa: E402
from llm_wiki_kit.research.providers.semantic_scholar import (  # noqa: E402
    SemanticScholarResult,
)


@pytest.fixture
def vault_with_gemini(fresh_vault: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault with research seed + Gemini provider installed."""

    assert cli.main(["add", "infrastructure:research-gemini"], kit_root=kit_root) == 0
    return fresh_vault


@pytest.fixture
def vault_with_semantic_scholar(
    fresh_vault: Path, kit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """A vault with research seed + Semantic Scholar provider installed."""

    assert cli.main(["add", "infrastructure:research-semantic-scholar"], kit_root=kit_root) == 0
    return fresh_vault


def _install_fake_gemini(
    monkeypatch: pytest.MonkeyPatch,
    answer: str = "Gemini answer.",
    citations: list[str] | None = None,
) -> None:
    citations = citations if citations is not None else ["https://g.example/a"]

    def _fake(config: Any, query: str) -> GeminiResult:
        return GeminiResult(
            answer=answer,
            citations=list(citations),
            model=config.model or "gemini-2.5-pro",
        )

    monkeypatch.setattr(gemini, "dispatch", _fake)


def _install_fake_semantic_scholar(
    monkeypatch: pytest.MonkeyPatch,
    answer: str = "1. **T** (2024) — A. *V*. abs\n   https://s.example/a\n",
    citations: list[str] | None = None,
) -> None:
    citations = citations if citations is not None else ["https://s.example/a"]

    def _fake(config: Any, query: str) -> SemanticScholarResult:
        return SemanticScholarResult(answer=answer, citations=list(citations), model="graph-v1")

    monkeypatch.setattr(semantic_scholar, "dispatch", _fake)


def test_wiki_research_gemini_happy_path_stdout(
    vault_with_gemini: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``wiki research --provider gemini`` prints markdown + journals one event."""

    monkeypatch.setenv("GEMINI_API_KEY", "gk-DO-NOT-LOG")
    _install_fake_gemini(monkeypatch)

    assert cli.main(["research", "what is X", "--provider", "gemini"]) == 0

    stdout = capsys.readouterr().out
    assert "---" in stdout
    assert "provider: gemini" in stdout
    assert "model: gemini-2.5-pro" in stdout
    assert "Gemini answer." in stdout

    events = _research_events(vault_with_gemini)
    assert len(events) == 1
    assert events[0].provider == "gemini"
    assert events[0].model == "gemini-2.5-pro"
    assert events[0].status == "ok"
    assert events[0].result_path is None


def test_wiki_research_semantic_scholar_happy_path_stdout(
    vault_with_semantic_scholar: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``wiki research --provider semantic-scholar`` works **without** an env var."""

    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    _install_fake_semantic_scholar(monkeypatch)

    assert cli.main(["research", "q", "--provider", "semantic-scholar"]) == 0

    stdout = capsys.readouterr().out
    assert "provider: semantic-scholar" in stdout
    assert "model: graph-v1" in stdout
    assert "**T** (2024)" in stdout

    events = _research_events(vault_with_semantic_scholar)
    assert len(events) == 1
    assert events[0].provider == "semantic-scholar"
    assert events[0].model == "graph-v1"
    assert events[0].status == "ok"


def test_wiki_research_gemini_missing_env_var_exits_2_no_event(
    vault_with_gemini: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing ``GEMINI_API_KEY`` is a config-shaped error — no journal entry."""

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # No fake patched: the real ``gemini.dispatch`` runs and the env-var
    # pre-condition fires.
    events_before = _research_events(vault_with_gemini)

    assert cli.main(["research", "q", "--provider", "gemini"]) == 2

    stderr = capsys.readouterr().err
    assert "set GEMINI_API_KEY in the environment" in stderr

    events_after = _research_events(vault_with_gemini)
    assert events_after == events_before  # no new research.query event


def test_wiki_research_semantic_scholar_keyless_real_request_succeeds(
    vault_with_semantic_scholar: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keyless mode through the real ``request_json`` (with ``urlopen`` mocked).

    Verifies the wire-level GET-with-no-body path doesn't break and
    the keyless-tier code path runs end-to-end.
    """

    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    def _fake_urlopen(request: Any, timeout: float = 0.0) -> Any:
        assert request.get_method() == "GET"
        assert request.data is None

        class _Response:
            def read(self) -> bytes:
                return b'{"data": [{"title":"T","year":2024,"url":"https://s/a"}]}'

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

        return _Response()

    monkeypatch.setattr(http_module, "urlopen", _fake_urlopen)

    assert cli.main(["research", "q", "--provider", "semantic-scholar"]) == 0

    events = _research_events(vault_with_semantic_scholar)
    assert len(events) == 1
    assert events[0].status == "ok"
    assert events[0].provider == "semantic-scholar"


def test_wiki_research_three_providers_pass_provider_required(
    fresh_vault: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Three providers installed and no ``--provider`` → exit 2, all slugs listed."""

    assert cli.main(["add", "infrastructure:research-perplexity"], kit_root=kit_root) == 0
    assert cli.main(["add", "infrastructure:research-gemini"], kit_root=kit_root) == 0
    assert cli.main(["add", "infrastructure:research-semantic-scholar"], kit_root=kit_root) == 0

    assert cli.main(["research", "q"]) == 2

    stderr = capsys.readouterr().err
    assert "pass --provider" in stderr
    # Sorted: gemini, perplexity, semantic-scholar.
    g_idx = stderr.index("gemini")
    p_idx = stderr.index("perplexity")
    s_idx = stderr.index("semantic-scholar")
    assert g_idx < p_idx < s_idx


def test_wiki_research_apikey_never_in_journal_or_stdout_gemini(
    vault_with_gemini: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Spec invariant 2: ``GEMINI_API_KEY`` value never reaches journal or stderr.

    Fixture injects the key string into the *answer body bytes* — the
    real Gemini API doesn't echo headers, but this defends against a
    future variant that might. The body lands in stdout (it is the
    answer, after all), so the assertions check **journal + stderr
    cleanliness only**, not stdout.
    """

    key = "gk-DO-NOT-LOG"
    monkeypatch.setenv("GEMINI_API_KEY", key)

    def _fake_urlopen(request: Any, timeout: float = 0.0) -> Any:
        # Header check: the key is in ``x-goog-api-key``, not the URL.
        assert key not in request.full_url

        class _Response:
            def read(self) -> bytes:
                # Inject the key into the response body to simulate a
                # future server-side echo bug.
                text = f"{key} present in answer"
                body = f'{{"candidates":[{{"content":{{"parts":[{{"text":"{text}"}}]}}}}]}}'
                return body.encode("utf-8")

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

        return _Response()

    monkeypatch.setattr(http_module, "urlopen", _fake_urlopen)

    assert cli.main(["research", "q", "--provider", "gemini"]) == 0

    captured = capsys.readouterr()
    # Stdout WILL contain the key (it's in the answer body) — that is
    # the fixture's whole point. Other surfaces must not.
    assert key not in captured.err
    assert key not in _journal_path(vault_with_gemini).read_text(encoding="utf-8")


def test_wiki_research_http_error_journals_error_event_gemini(
    vault_with_gemini: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gemini HTTP error → one ``research.query`` event with ``status='error'``."""

    monkeypatch.setenv("GEMINI_API_KEY", "gk-DO-NOT-LOG")

    from llm_wiki_kit.research.http import ResearchHTTPError

    def _fail(config: Any, query: str) -> Any:
        raise ResearchHTTPError("gemini: HTTP 401", status=401)

    monkeypatch.setattr(gemini, "dispatch", _fail)

    assert cli.main(["research", "q", "--provider", "gemini"]) == 2

    events = _research_events(vault_with_gemini)
    assert len(events) == 1
    assert events[0].status == "error"
    assert events[0].provider == "gemini"
    assert events[0].model == "gemini-2.5-pro"
    assert events[0].result_path is None


def test_wiki_add_research_gemini_pulls_research_via_requires(
    fresh_vault: Path, kit_root: Path
) -> None:
    """``wiki add research-gemini`` installs both primitives atomically."""

    assert cli.main(["add", "infrastructure:research-gemini"], kit_root=kit_root) == 0

    events = read_events(_journal_path(fresh_vault))
    installs = [e for e in events if isinstance(e, PrimitiveInstallEvent)]
    installed = [e.primitive for e in installs if e.by == "wiki-add"]
    assert "research" in installed
    assert "research-gemini" in installed
    assert installed.index("research") < installed.index("research-gemini")


def test_wiki_add_research_semantic_scholar_pulls_research_via_requires(
    fresh_vault: Path, kit_root: Path
) -> None:
    assert cli.main(["add", "infrastructure:research-semantic-scholar"], kit_root=kit_root) == 0

    events = read_events(_journal_path(fresh_vault))
    installs = [e for e in events if isinstance(e, PrimitiveInstallEvent)]
    installed = [e.primitive for e in installs if e.by == "wiki-add"]
    assert "research" in installed
    assert "research-semantic-scholar" in installed
    assert installed.index("research") < installed.index("research-semantic-scholar")


def test_wiki_add_both_task19_providers_aggregates_blocks(
    fresh_vault: Path, kit_root: Path
) -> None:
    """Installing both Gemini and Semantic Scholar produces a two-key region."""

    import yaml

    from llm_wiki_kit import managed_regions
    from llm_wiki_kit.models import ResearchProvidersConfig

    assert cli.main(["add", "infrastructure:research-gemini"], kit_root=kit_root) == 0
    assert cli.main(["add", "infrastructure:research-semantic-scholar"], kit_root=kit_root) == 0

    content = (fresh_vault / "research-providers.yaml").read_text(encoding="utf-8")
    region_body = managed_regions.parse(content)["providers"]
    loaded = yaml.safe_load(region_body)
    config = ResearchProvidersConfig.model_validate(loaded)

    assert sorted(config.root.keys()) == ["gemini", "semantic-scholar"]
    assert config.root["gemini"].api_key_env == "GEMINI_API_KEY"
    assert config.root["gemini"].model == "gemini-2.5-pro"
    assert config.root["gemini"].cost_signal == "medium"
    assert config.root["semantic-scholar"].api_key_env == "SEMANTIC_SCHOLAR_API_KEY"
    assert config.root["semantic-scholar"].model == "graph-v1"
    assert config.root["semantic-scholar"].cost_signal == "free"
