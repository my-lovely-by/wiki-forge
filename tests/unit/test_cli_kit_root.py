"""Unit tests for the wheel-bundled-assets resolver and the qC8 grep guard.

See ``docs/specs/wheel-bundled-assets/spec.md`` §Acceptance criteria →
Resolver. The autouse ``_reset_lazy_kit_root`` fixture in
``tests/conftest.py`` handles per-test ``cli._KIT_ROOT = None`` reset,
so individual tests don't need to repeat it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from llm_wiki_kit import cli
from llm_wiki_kit.errors import WikiError


def _make_kit_layout(root: Path) -> Path:
    """Create the three required subdirs under ``root`` and return ``root``."""

    for subdir in cli._KIT_SUBDIRS:
        (root / subdir).mkdir(parents=True)
    return root


def test_resolve_kit_root_prefers_bundled_assets_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _make_kit_layout(tmp_path / "bundle")
    monkeypatch.setattr(cli, "_bundled_assets_path", lambda: bundle)
    # source-tree branch is fine; the bundled branch should win first.
    assert cli._resolve_kit_root() == bundle


def test_resolve_kit_root_validates_bundled_subdirs_before_returning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "core").mkdir(parents=True)
    (bundle / "templates").mkdir()
    # `recipes/` is missing on purpose.
    source = _make_kit_layout(tmp_path / "source")

    monkeypatch.setattr(cli, "_bundled_assets_path", lambda: bundle)
    monkeypatch.setattr(cli, "_source_tree_kit_root", lambda: source)

    # The half-valid bundle is rejected; resolver falls through to source.
    assert cli._resolve_kit_root() == source


def test_resolve_kit_root_falls_back_to_source_tree_when_no_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_kit_layout(tmp_path / "source")
    monkeypatch.setattr(cli, "_bundled_assets_path", lambda: None)
    monkeypatch.setattr(cli, "_source_tree_kit_root", lambda: source)

    assert cli._resolve_kit_root() == source


def test_resolve_kit_root_raises_wikierror_when_no_branch_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No bundled path; source-tree path is missing the three subdirs.
    empty_source = tmp_path / "empty"
    empty_source.mkdir()
    monkeypatch.setattr(cli, "_bundled_assets_path", lambda: None)
    monkeypatch.setattr(cli, "_source_tree_kit_root", lambda: empty_source)

    with pytest.raises(WikiError) as exc:
        cli._resolve_kit_root()
    msg = str(exc.value)
    # Names the missing subdirs and the two candidate paths it tried, so
    # the user can tell wheel-misconfigured apart from running-from-the-
    # wrong-Python.
    assert "recipes/" in msg
    assert "core/" in msg
    assert "templates/" in msg
    assert str(empty_source) in msg
    assert "(not present)" in msg  # bundled was None this run


def test_kit_root_helper_resolves_lazily_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_kit_layout(tmp_path / "source")
    monkeypatch.setattr(cli, "_bundled_assets_path", lambda: None)
    monkeypatch.setattr(cli, "_source_tree_kit_root", lambda: source)

    call_count: list[int] = []
    real_resolve = cli._resolve_kit_root

    def counting_resolve() -> Path:
        call_count.append(1)
        return real_resolve()

    monkeypatch.setattr(cli, "_resolve_kit_root", counting_resolve)

    # The autouse reset fixture already cleared _KIT_ROOT; assert explicit:
    assert cli._KIT_ROOT is None
    assert call_count == []

    first = cli._kit_root()
    assert first == source
    assert cli._KIT_ROOT == source
    assert len(call_count) == 1

    # Second call short-circuits via the `if _KIT_ROOT is None` check.
    second = cli._kit_root()
    assert second == source
    assert len(call_count) == 1


def test_kit_paths_uses_explicit_override_without_consulting_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qC8: ``_kit_paths(kit_root=...)`` does not fire the lazy resolver."""

    explicit = _make_kit_layout(tmp_path / "explicit")

    def boom() -> Path:
        raise AssertionError("_resolve_kit_root must not be called when kit_root= is passed")

    monkeypatch.setattr(cli, "_resolve_kit_root", boom)

    recipes, core, templates = cli._kit_paths(kit_root=explicit)
    assert recipes == explicit / "recipes"
    assert core == explicit / "core"
    assert templates == explicit / "templates"
    # The cache stayed clean — no module mutation through the override path.
    assert cli._KIT_ROOT is None


def test_cli_main_threads_kit_root_through_to_kit_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qC8: ``cli.main(argv, kit_root=...)`` reaches ``_kit_paths`` with the override.

    Asserts the threaded value travels through ``args.kit_root`` and into
    ``_kit_paths``'s ``kit_root`` argument. Recording at the ``_kit_paths``
    seam avoids touching argparse internals — a recorded ``WikiError``
    short-circuits the handler before any vault rendering happens.
    """

    explicit = tmp_path / "explicit"
    explicit.mkdir()
    received: list[Path | None] = []

    def record(kit_root: Path | None = None) -> tuple[Path, Path, Path]:
        received.append(kit_root)
        raise WikiError("intentional short-circuit for the threading pin test")

    monkeypatch.setattr(cli, "_kit_paths", record)

    # Threaded value lands on args.kit_root and reaches _kit_paths.
    assert (
        cli.main(["init", str(tmp_path / "v"), "--recipe", "x"], kit_root=explicit)
        == cli.WIKI_ERROR_EXIT
    )
    assert received == [explicit]

    # Omitting kit_root produces None on the namespace (and at _kit_paths).
    received.clear()
    assert cli.main(["init", str(tmp_path / "v2"), "--recipe", "x"]) == cli.WIKI_ERROR_EXIT
    assert received == [None]


def test_cmd_doctor_threads_kit_root_into_run_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qC8/spec: ``_cmd_doctor`` reads ``args.kit_root`` and hands the resolved
    root to ``run_doctor``. ``_cmd_doctor`` is the one handler that bypasses
    ``_kit_paths`` (because ``run_doctor`` takes a single root, not three
    derived paths); without this pin a future refactor that dropped the
    threading would still pass the integration tests by accident — they'd
    fall back to the source tree, find different recipes, and fail with a
    confusing message instead of one tied to the contract.
    """

    vault = tmp_path / "vault"
    journal_dir = vault / ".wiki.journal"
    journal_dir.mkdir(parents=True)
    (journal_dir / "journal.jsonl").touch()
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    received: list[Path] = []

    def record(_vault: Path, kit_root: Path) -> list[object]:
        received.append(kit_root)
        return []

    monkeypatch.setattr(cli, "run_doctor", record)
    monkeypatch.chdir(vault)

    assert cli.main(["doctor"], kit_root=explicit) == 0
    assert received == [explicit]


def test_kit_root_is_not_referenced_outside_kit_paths_helper() -> None:
    """qC8 grep guard.

    The module attribute ``cli._KIT_ROOT`` is the lazy cache. Production
    code (every callsite outside the resolver block in ``cli.py``) reads
    the kit root through ``_kit_root()`` / ``_kit_paths()``. Tests pass
    ``kit_root=`` to ``cli.main``. Direct ``_KIT_ROOT`` references must
    therefore be limited to:

    * ``llm_wiki_kit/cli.py`` — the helper module itself.
    * ``tests/conftest.py`` — the per-test reset fixture.
    * ``tests/unit/test_cli_kit_root.py`` (this file) — resolver tests.

    Any other reference is the qC8 antipattern (monkey-patching module
    state to influence runtime behavior). Pin it here so a future test
    cannot quietly reintroduce it.
    """

    repo_root = Path(__file__).resolve().parents[2]
    allowed = {
        repo_root / "llm_wiki_kit" / "cli.py",
        repo_root / "tests" / "conftest.py",
        repo_root / "tests" / "unit" / "test_cli_kit_root.py",
    }
    scan_roots: Sequence[Path] = (repo_root / "llm_wiki_kit", repo_root / "tests")
    offenders: list[Path] = []
    for root in scan_roots:
        for path in root.rglob("*.py"):
            if path in allowed:
                continue
            if "_KIT_ROOT" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(repo_root))

    assert offenders == [], (
        "qC8 violation (see docs/specs/wheel-bundled-assets/spec.md §Invariants): "
        f"_KIT_ROOT referenced outside the kit-paths helper. Offending files: {offenders}. "
        "Use ``cli.main(argv, kit_root=...)`` or ``cli._kit_root()`` instead of "
        "touching the module attribute."
    )
