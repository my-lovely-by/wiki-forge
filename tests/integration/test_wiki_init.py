"""End-to-end integration tests for ``wiki init`` (RFC-0001 Task 10).

The CLI handler is exercised by calling ``cli.main`` directly so failures
surface as test assertions rather than subprocess exit codes. The kit's
bundled ``recipes/`` and ``core/`` directories are picked up from the
editable-install layout via ``cli._kit_paths()`` — the test suite runs
against the same on-disk assets a user would render at install time.

After Tasks 13/14/15, every shipped recipe (``family``, ``work-os``,
``personal``) installs more than just ``core``. Each recipe has its
own integration suite — see ``test_family_recipe.py``,
``test_work_os_recipe.py``, and ``test_personal_recipe.py`` — and the
generic init-shape assertions here run against an in-test minimal
recipe so they keep covering the core-only code path without being
coupled to whatever the shipped recipes' closures look like today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki_kit.cli import WIKI_ERROR_EXIT, main
from llm_wiki_kit.journal import read_events, replay_state
from llm_wiki_kit.models import (
    PageWriteEvent,
    PrimitiveInstallEvent,
    VaultInitEvent,
)


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


@pytest.fixture
def core_only_kit(tmp_path: Path) -> Path:
    """Build a kit root with a 'core-only' recipe symlinked back to the real assets.

    The shipped recipes have all grown past the core-only shape, so we
    drop a temporary ``recipes/core-only.yaml`` into a tmp kit tree that
    symlinks back to the real ``core/`` and ``templates/`` directories.
    The temporary recipe lists no primitives; the loader auto-prepends
    ``core``.

    Tests pass the returned path as ``cli.main(argv, kit_root=...)`` —
    the post-qC8 threading pattern, no module-level monkeypatch.
    """

    from llm_wiki_kit import cli

    kit_root = tmp_path / "kit-root"
    (kit_root / "recipes").mkdir(parents=True)
    (kit_root / "recipes" / "core-only.yaml").write_text(
        "name: core-only\n"
        "version: 0.1.0\n"
        "description: >-\n"
        "  Test-only recipe that installs nothing past core.\n"
        "primitives: []\n"
        "variables:\n"
        "  recipe_name: core-only\n",
        encoding="utf-8",
    )
    repo_root = cli._kit_root()
    (kit_root / "core").symlink_to(repo_root / "core")
    (kit_root / "templates").symlink_to(repo_root / "templates")

    return kit_root


def test_init_renders_core_only_vault(tmp_path: Path, core_only_kit: Path) -> None:
    vault = tmp_path / "my-vault"

    exit_code = main(["init", str(vault), "--recipe", "core-only"], kit_root=core_only_kit)

    assert exit_code == 0

    # (a) Expected file tree: AGENTS.md, CORE.md, frontmatter.schema.yaml,
    # .gitignore at the root; seven baseline skills under skills/.
    expected_top_level = {
        "AGENTS.md",
        "CORE.md",
        "frontmatter.schema.yaml",
        ".gitignore",
    }
    for name in expected_top_level:
        assert (vault / name).is_file(), f"expected {name} at vault root"

    expected_skills = {
        "ingest",
        "wiki-search",
        "wiki-lock",
        "wiki-lint",
        "wiki-conflict",
        "wiki-doctor",
        "wiki-research",
    }
    skills_dir = vault / "skills"
    assert skills_dir.is_dir()
    assert {p.name for p in skills_dir.iterdir() if p.is_dir()} == expected_skills
    for skill in expected_skills:
        assert (skills_dir / skill / "SKILL.md").is_file()

    # The journal is present with at least the init + install events.
    journal = _journal_path(vault)
    assert journal.is_file()


def test_init_journal_state_replays_cleanly(tmp_path: Path, core_only_kit: Path) -> None:
    vault = tmp_path / "another-vault"

    # `--no-git` keeps the journal shape this test pins narrow to
    # render events; the git-init path lives under its own coverage
    # in `tests/integration/test_wiki_init_git.py`.
    assert (
        main(
            ["init", str(vault), "--recipe", "core-only", "--no-git"],
            kit_root=core_only_kit,
        )
        == 0
    )

    events = read_events(_journal_path(vault))

    # (b) The first event is VaultInit; the second is PrimitiveInstall(core).
    assert isinstance(events[0], VaultInitEvent)
    assert events[0].vault_name == "another-vault"
    assert events[0].recipe == "core-only"
    assert events[0].by == "wiki-init"

    assert isinstance(events[1], PrimitiveInstallEvent)
    assert events[1].primitive == "core"
    assert events[1].by == "wiki-init"

    # Every subsequent event in a core-only install is a PageWrite.
    for event in events[2:]:
        assert isinstance(event, PageWriteEvent), f"unexpected event type: {event!r}"

    state = replay_state(events)
    assert state.vault_name == "another-vault"
    assert state.recipe == "core-only"
    assert state.installed_primitives == {"core": "0.1.0"}

    # Each top-level file we rendered shows up in the state's page_writes.
    for path in (
        "AGENTS.md",
        "CORE.md",
        "frontmatter.schema.yaml",
        ".gitignore",
        "skills/ingest/SKILL.md",
    ):
        assert path in state.page_writes, f"{path} missing from replayed state"
        assert state.page_writes[path].by == "core"


def test_init_interpolates_vault_and_recipe_name(tmp_path: Path) -> None:
    vault = tmp_path / "household-knowledge"

    assert main(["init", str(vault), "--recipe", "family"]) == 0

    # (c) AGENTS.md and CORE.md are on the INTERPOLATED_FILES allowlist
    # and reference {vault_name} / {recipe_name}.
    agents = (vault / "AGENTS.md").read_text(encoding="utf-8")
    assert "household-knowledge" in agents
    assert "family" in agents
    # The tokens themselves must not survive (otherwise interpolation broke).
    assert "{vault_name}" not in agents
    assert "{recipe_name}" not in agents

    core_md = (vault / "CORE.md").read_text(encoding="utf-8")
    assert "household-knowledge" in core_md
    assert "family" in core_md

    # .gitignore is also interpolated and seeds the vault name in a comment.
    gitignore = (vault / ".gitignore").read_text(encoding="utf-8")
    assert "household-knowledge" in gitignore


def test_init_refuses_non_empty_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = tmp_path / "existing"
    vault.mkdir()
    (vault / "stray.md").write_text("user content", encoding="utf-8")

    exit_code = main(["init", str(vault), "--recipe", "family"])

    assert exit_code == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "not empty" in err
    # The user's file is untouched and nothing was journaled.
    assert (vault / "stray.md").read_text(encoding="utf-8") == "user content"
    assert not (vault / ".wiki.journal").exists()


def test_init_refuses_when_target_is_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "vault.txt"
    target.write_text("not a directory", encoding="utf-8")

    exit_code = main(["init", str(target), "--recipe", "family"])

    assert exit_code == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "file" in err
    assert target.read_text(encoding="utf-8") == "not a directory"


def test_init_surfaces_missing_recipe_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = tmp_path / "ghost-vault"

    exit_code = main(["init", str(vault), "--recipe", "ghost"])

    assert exit_code == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "ghost" in err
    # No partial vault: the handler refuses before any disk write.
    assert not vault.exists()


def test_init_creates_target_directory(tmp_path: Path) -> None:
    # The handler should create the target if it doesn't exist (the
    # common case for ``wiki init ~/new-vault --recipe family``).
    vault = tmp_path / "nested" / "child" / "vault"
    assert not vault.exists()

    assert main(["init", str(vault), "--recipe", "family"]) == 0
    assert vault.is_dir()
    assert _journal_path(vault).is_file()


def test_init_is_idempotently_refused_on_rerun(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = tmp_path / "rerun-vault"

    assert main(["init", str(vault), "--recipe", "family"]) == 0
    capsys.readouterr()  # drain

    exit_code = main(["init", str(vault), "--recipe", "family"])

    assert exit_code == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "not empty" in err


def test_wiki_init_install_pipeline_reads_journal_once_via_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qC4: the install pipeline reads the journal exactly once.

    Without the cache, every ``safe_write`` / ``safe_write_region``
    call in the install pipeline calls ``read_events`` for its
    baseline lookup — O(events * writes) on every fresh-vault render.
    With the cache scope wrapping ``_cmd_init``, the read happens
    once and ``append_event`` extends the in-memory list for the rest
    of the handler.
    """
    import llm_wiki_kit.journal as _journal

    vault = tmp_path / "v"
    journal_target = vault / ".wiki.journal" / "journal.jsonl"
    reads = {"n": 0}
    original = _journal.read_events

    def counting_read_events(p: Path) -> object:
        if p == journal_target:
            reads["n"] += 1
        return original(p)

    monkeypatch.setattr(_journal, "read_events", counting_read_events)

    assert main(["init", str(vault), "--recipe", "family"]) == 0
    # The cache absorbs every baseline lookup after the first load.
    assert reads["n"] == 1, (
        f"expected one read of the vault journal under the cache scope, got {reads['n']}"
    )
