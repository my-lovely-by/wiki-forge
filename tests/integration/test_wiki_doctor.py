"""End-to-end ``wiki doctor`` integration tests (RFC-0001 Task 12).

Drives four vault states through the CLI:

* **clean** — a freshly-initted core-only vault has no issues.
* **page-drift** — a user edit to ``AGENTS.md`` with no pending proposal.
* **pending-proposal** — a ``safe_write`` against an edited file
  triggers the proposal sidecar; doctor surfaces the sidecar path.
* **orphan** — a stray file under ``skills/`` with no journal event.

Vault construction reuses the kit-root threading pattern from
``test_wiki_init_primitives.py`` (qC8); doctor is invoked via
``cli.main(["doctor"], kit_root=kit_root)`` after
``monkeypatch.chdir(vault)``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.write_helper import safe_write

REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_kit(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    (kit / "templates").mkdir()
    recipes_dir = kit / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "minimal.yaml").write_text(
        "name: minimal\n"
        "version: 0.1.0\n"
        "description: Core-only recipe for wiki doctor tests.\n"
        "primitives:\n"
        "  - core\n"
        "variables:\n"
        "  recipe_name: minimal\n",
        encoding="utf-8",
    )
    return kit


@pytest.fixture
def kit_root(tmp_path: Path) -> Path:
    return _install_kit(tmp_path)


def _init_vault(tmp_path: Path, kit_root: Path) -> Path:
    vault = tmp_path / "v"
    assert cli.main(["init", str(vault), "--recipe", "minimal"], kit_root=kit_root) == 0
    return vault


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------


def test_doctor_clean_vault_exits_zero(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    monkeypatch.chdir(vault)
    capsys.readouterr()

    assert cli.main(["doctor"], kit_root=kit_root) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_doctor_clean_after_multi_provider_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``wiki doctor`` is clean after installing multiple research providers.

    Regression test for the Task 18 / Task 19 boundary: the aggregator
    rewrites ``research-providers.yaml`` in place via
    ``safe_write_region`` after the seed primitive's ``safe_write``
    landed the empty-region seed bytes. Without the canonical-hash
    fix in ``write_helper`` and ``doctor``, this scenario produced
    spurious ``page-drift`` + ``managed-region-drift`` issues. The
    fix is migration-safe (Task 18 vaults stay clean) because the
    canonical form matches the bytes the aggregator was already
    emitting.
    """

    # Use the real kit (with the full templates/ catalog) so the
    # install pipeline runs against the actual primitives.
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copytree(REPO_ROOT / "core", kit / "core")
    shutil.copytree(REPO_ROOT / "templates", kit / "templates")
    recipes_dir = kit / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "minimal.yaml").write_text(
        "name: minimal\n"
        "version: 0.1.0\n"
        "description: Core-only recipe for wiki doctor regression test.\n"
        "primitives: []\n"
        "variables:\n"
        "  recipe_name: minimal\n",
        encoding="utf-8",
    )

    vault = tmp_path / "v"
    assert cli.main(["init", str(vault), "--recipe", "minimal"], kit_root=kit) == 0
    monkeypatch.chdir(vault)

    assert cli.main(["add", "infrastructure:research-perplexity"], kit_root=kit) == 0
    assert cli.main(["add", "infrastructure:research-gemini"], kit_root=kit) == 0
    assert cli.main(["add", "infrastructure:research-semantic-scholar"], kit_root=kit) == 0

    capsys.readouterr()
    assert cli.main(["doctor"], kit_root=kit) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ---------------------------------------------------------------------------
# page-drift
# ---------------------------------------------------------------------------


def test_doctor_reports_page_drift(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    # Simulate a user edit outside the kit's write path.
    (vault / "AGENTS.md").write_text("user override\n", encoding="utf-8")

    monkeypatch.chdir(vault)
    capsys.readouterr()

    exit_code = cli.main(["doctor"], kit_root=kit_root)
    out = capsys.readouterr().out.strip().splitlines()

    assert exit_code == cli.DOCTOR_ISSUES_EXIT
    assert out == ["page-drift: AGENTS.md"]


# ---------------------------------------------------------------------------
# pending-proposal
# ---------------------------------------------------------------------------


def test_doctor_reports_pending_proposal(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    # Drift the file, then drive ``safe_write`` again so it falls through
    # to a ``.proposed`` sidecar + ``page.proposal`` event — the exact
    # state ``wiki doctor`` is designed to surface.
    (vault / "AGENTS.md").write_text("user override\n", encoding="utf-8")
    safe_write(
        Path("AGENTS.md"),
        "kit's next version\n",
        by="core",
        journal_path=_journal_path(vault),
    )
    assert (vault / "AGENTS.md.proposed").is_file()

    monkeypatch.chdir(vault)
    capsys.readouterr()

    exit_code = cli.main(["doctor"], kit_root=kit_root)
    out = capsys.readouterr().out.strip().splitlines()

    assert exit_code == cli.DOCTOR_ISSUES_EXIT
    # The pending-proposal swallows the page-drift for the same path —
    # doctor reports the actionable thing (resolve the sidecar), not the
    # underlying drift.
    assert "pending-proposal: AGENTS.md.proposed" in out
    assert not any(line.startswith("page-drift:") for line in out)


# ---------------------------------------------------------------------------
# orphan
# ---------------------------------------------------------------------------


def test_doctor_reports_orphan_under_kit_path(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    stray = vault / "skills" / "rogue" / "SKILL.md"
    stray.parent.mkdir(parents=True)
    stray.write_text("not from any primitive", encoding="utf-8")

    monkeypatch.chdir(vault)
    capsys.readouterr()

    exit_code = cli.main(["doctor"], kit_root=kit_root)
    out = capsys.readouterr().out.strip().splitlines()

    assert exit_code == cli.DOCTOR_ISSUES_EXIT
    assert "orphan: skills/rogue/SKILL.md" in out


def test_doctor_does_not_flag_user_owned_paths(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    vault = _init_vault(tmp_path, kit_root)
    # A user-created folder outside the kit-owned roots must be invisible.
    (vault / "journal").mkdir()
    (vault / "journal" / "2026-05-16.md").write_text("daily note", encoding="utf-8")

    monkeypatch.chdir(vault)
    capsys.readouterr()

    assert cli.main(["doctor"], kit_root=kit_root) == 0


# ---------------------------------------------------------------------------
# CLI error path
# ---------------------------------------------------------------------------


def test_doctor_does_not_double_report_pending_managed_region_proposal(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Retro-review #B6: managed-region drift on a file with an open
    ``.proposed`` sidecar must surface as ``pending-proposal`` only,
    not also as ``managed-region-drift``. Pairs with #F-B1's resolve fix.
    """

    from llm_wiki_kit.write_helper import safe_write_region

    vault = _init_vault(tmp_path, kit_root)
    journal_path = _journal_path(vault)

    # Seed a managed-region baseline so subsequent drift is detectable.
    # ``wiki init`` writes AGENTS.md as a whole page (no other primitives
    # contribute regions in the minimal recipe), so we plant one here.
    agents = vault / "AGENTS.md"
    safe_write_region(
        agents,
        "content-types",
        "kit-baseline\n",
        by="core",
        journal_path=journal_path,
    )

    # User edits inside the kit-owned region, then a follow-up region
    # write produces a sidecar + a PageProposalEvent for AGENTS.md.
    edited = agents.read_text(encoding="utf-8").replace("kit-baseline", "user override")
    agents.write_text(edited, encoding="utf-8")

    safe_write_region(
        agents,
        "content-types",
        "kit-next\n",
        by="core",
        journal_path=journal_path,
    )
    assert (vault / "AGENTS.md.proposed").is_file()

    monkeypatch.chdir(vault)
    capsys.readouterr()

    exit_code = cli.main(["doctor"], kit_root=kit_root)
    out = capsys.readouterr().out.strip().splitlines()

    assert exit_code == cli.DOCTOR_ISSUES_EXIT
    assert "pending-proposal: AGENTS.md.proposed" in out
    assert not any(line.startswith("managed-region-drift: AGENTS.md") for line in out)


def test_doctor_runs_against_corrupt_journal_and_reports_journal_corrupt(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plan step 6 §Recovery: a malformed journal line surfaces as
    ``journal-corrupt`` and the remaining checks run against the
    valid-events prefix instead of crashing the doctor pass.

    Before this step, ``read_events`` raised on the first bad line and
    blew up the whole ``wiki doctor`` invocation, hiding every other
    issue. The lenient read path (added in this PR) preserves doctor's
    "report everything, decide nothing" stance even when the journal
    is partially corrupt.
    """

    vault = _init_vault(tmp_path, kit_root)
    journal = _journal_path(vault)

    # Snapshot the valid-event count so the assertion can name the
    # corrupted line precisely. The journal was just written by
    # ``wiki init``; appending one bad line puts corruption at line
    # ``valid_lines + 1`` (1-based, the convention ``JournalCorruptError``
    # already uses).
    valid_lines = len(journal.read_text(encoding="utf-8").splitlines())
    with journal.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    corrupt_line = valid_lines + 1

    # Plant a stray so ``check_orphans`` still has something to find;
    # the partial-events checks must still produce its issue, proving
    # doctor didn't abort after the corruption row.
    stray = vault / "skills" / "rogue" / "SKILL.md"
    stray.parent.mkdir(parents=True)
    stray.write_text("not from any primitive", encoding="utf-8")

    monkeypatch.chdir(vault)
    capsys.readouterr()

    exit_code = cli.main(["doctor"], kit_root=kit_root)
    out = capsys.readouterr().out.strip().splitlines()

    assert exit_code == cli.DOCTOR_ISSUES_EXIT
    journal_corrupt = [line for line in out if line.startswith("journal-corrupt:")]
    assert len(journal_corrupt) == 1, f"expected one journal-corrupt issue, got: {out}"
    # ``format_issue`` renders ``<kind>: <path> (<detail>)``. The
    # ``journal-corrupt`` issue intentionally overloads ``Issue.path``
    # with the 1-based line number — there's no vault file that "owns"
    # a torn JSONL line. The shim is documented on ``Issue``'s
    # docstring; if a future refactor splits the field, this assertion
    # changes in lockstep.
    assert journal_corrupt[0].startswith(f"journal-corrupt: {corrupt_line} (invalid JSON:")
    # Partial-events prefix still feeds the orphan check.
    assert "orphan: skills/rogue/SKILL.md" in out


def test_doctor_refuses_when_cwd_is_not_a_vault(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)

    assert cli.main(["doctor"], kit_root=kit_root) == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "not a wiki vault" in err


# ---------------------------------------------------------------------------
# safe-write-ordering spec — recovery family
#
# Pre-seed the journal manually with a "kit wrote an event but the file
# never materialized" state and assert ``run_doctor`` surfaces it. These
# pass against today's code; their job is to pin the §Edge cases recovery
# contract so a future refactor can't drop the reconciliation hook.
# ---------------------------------------------------------------------------


def test_doctor_surfaces_orphan_page_event_as_missing(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from datetime import UTC, datetime

    from llm_wiki_kit.journal import append_event
    from llm_wiki_kit.models import PageWriteEvent

    vault = _init_vault(tmp_path, kit_root)
    # Journal a write for a path that does not exist on disk.
    append_event(
        _journal_path(vault),
        PageWriteEvent(
            timestamp=datetime.now(UTC),
            by="meeting",
            path="meetings/2026-05-15.md",
            hash="a" * 64,
        ),
    )

    monkeypatch.chdir(vault)
    capsys.readouterr()

    exit_code = cli.main(["doctor"], kit_root=kit_root)
    out = capsys.readouterr().out.strip().splitlines()

    assert exit_code == cli.DOCTOR_ISSUES_EXIT
    assert "missing: meetings/2026-05-15.md" in out


def test_doctor_surfaces_orphan_managed_region_event_as_drift(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from datetime import UTC, datetime

    from llm_wiki_kit.journal import append_event
    from llm_wiki_kit.models import ManagedRegionWriteEvent

    vault = _init_vault(tmp_path, kit_root)
    # Append a managed-region event whose content_hash doesn't match
    # the on-disk region body (``wiki init`` already seeded AGENTS.md
    # with empty region buckets).
    append_event(
        _journal_path(vault),
        ManagedRegionWriteEvent(
            timestamp=datetime.now(UTC),
            by="core",
            file="AGENTS.md",
            region="content-types",
            content_hash="b" * 64,
        ),
    )

    monkeypatch.chdir(vault)
    capsys.readouterr()

    exit_code = cli.main(["doctor"], kit_root=kit_root)
    out = capsys.readouterr().out.strip().splitlines()

    assert exit_code == cli.DOCTOR_ISSUES_EXIT
    assert "managed-region-drift: AGENTS.md:content-types" in out


def test_doctor_surfaces_orphan_resolve_events(
    tmp_path: Path,
    kit_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from datetime import UTC, datetime

    from llm_wiki_kit.journal import append_event
    from llm_wiki_kit.models import PageConflictResolvedEvent, PageWriteEvent

    vault = _init_vault(tmp_path, kit_root)
    # Two events: PageWrite + PageConflictResolved, file never written.
    now = datetime.now(UTC)
    append_event(
        _journal_path(vault),
        PageWriteEvent(
            timestamp=now,
            by="wiki-conflict",
            path="orphan-resolve.md",
            hash="c" * 64,
        ),
    )
    append_event(
        _journal_path(vault),
        PageConflictResolvedEvent(
            timestamp=now,
            by="wiki-conflict",
            path="orphan-resolve.md",
            hash="c" * 64,
        ),
    )

    monkeypatch.chdir(vault)
    capsys.readouterr()

    exit_code = cli.main(["doctor"], kit_root=kit_root)
    out = capsys.readouterr().out.strip().splitlines()

    assert exit_code == cli.DOCTOR_ISSUES_EXIT
    assert "missing: orphan-resolve.md" in out
