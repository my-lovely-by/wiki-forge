"""Example-vault and regenerator gates (RFC-0001 Task 21).

Covers the committed ``examples/family-mini/``, ``examples/work-os-mini/``,
and ``examples/conflict-pending/`` vaults plus the regenerator that
produces them.

The tests:

* AC1 (test #1) — ``wiki doctor`` per vault: family-mini and work-os-mini
  exit 0; conflict-pending exits non-zero with the literal
  ``pending-proposal`` token in stdout (from
  ``llm_wiki_kit.doctor.PENDING_PROPOSAL``) and a ``PageProposalEvent``
  in its journal.
* AC2 (test #2) — every recipe-created ``wiki/<area>/`` directory in the
  family-mini and work-os-mini vaults contains at least one
  hand-authored markdown page beyond the kit's ``README.md``.
* AC6 (test #3) — ``python examples/regenerate.py --check`` exits 0.
* AC7 (tests #4, #5) — ``regenerate.build_vault`` is idempotent under
  the AC6 normalization rules, and ``--apply`` is crash-safe (a failed
  swap leaves the committed tree untouched).
* AC13 (test #11) — ``pyproject.toml``'s ``[project].dependencies``
  hasn't grown.
* Invariant guardrail (test #10) — no unexpected new top-level
  directories beyond the pre-task set plus ``{"examples"}``.

Spec: ``docs/specs/task-21-examples-tutorials/spec.md``.
Plan:  ``docs/specs/task-21-examples-tutorials/plan.md`` §Steps T2/T3.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import types
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _import_regenerate() -> types.ModuleType:
    """Lazy import so test collection doesn't crash before T2 has landed.

    `examples/` is not a wheel package; extend sys.path at call time.
    """

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from examples import regenerate

    return regenerate


EXAMPLES_DIR = REPO_ROOT / "examples"
FAMILY_MINI = EXAMPLES_DIR / "family-mini"
WORK_OS_MINI = EXAMPLES_DIR / "work-os-mini"
CONFLICT_PENDING = EXAMPLES_DIR / "conflict-pending"

# Pre-task top-level directory set, refreshed after Task 22 deleted
# the v1 `vault-templates/` and `shared/` trees. This is the
# load-bearing literal for test #10 — change it only when a new
# top-level directory is intentionally introduced (and the spec /
# AGENTS.md authorize it). Mirrors `git ls-tree --name-only HEAD`
# (dirs only) at the v2.0.0 release-cut HEAD.
PRE_TASK_TOP_LEVEL_DIRS = frozenset(
    {
        ".claude",
        ".github",
        "core",
        "docs",
        "llm_wiki_kit",
        "recipes",
        "skills",
        "templates",
        "tests",
        "tools",
    }
)

# Volatile / dev-environment / VCS dirs the test must ignore. None of
# these are git-tracked at plan-time HEAD; filtering them out here
# means the test passes on a fresh checkout AND in a worktree or
# Conductor session that leaves scratch dirs in place.
IGNORED_TOP_LEVEL_DIRS = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".worktrees",
        ".context",  # Conductor workspace-scratch dir (gitignored)
        ".ruff_cache",
        ".mypy_cache",
        # Build / packaging artifacts a contributor may produce locally.
        # None of these are committed.
        "dist",
        "build",
        "llm_wiki_kit.egg-info",
        "node_modules",
        ".venv",
        "venv",
    }
)


# ---------------------------------------------------------------------------
# AC1 — `wiki doctor` per-vault outcome (construction test #1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("vault", "expect_clean"),
    [
        pytest.param(FAMILY_MINI, True, id="family-mini"),
        pytest.param(WORK_OS_MINI, True, id="work-os-mini"),
        pytest.param(CONFLICT_PENDING, False, id="conflict-pending"),
    ],
)
def test_example_vaults_doctor_per_vault(vault: Path, expect_clean: bool) -> None:
    proc = subprocess.run(
        ["wiki", "doctor"],
        cwd=str(vault),
        check=False,
        capture_output=True,
    )
    if expect_clean:
        assert proc.returncode == 0, (
            f"{vault.name}: expected `wiki doctor` exit 0, got {proc.returncode}.\n"
            f"stdout: {proc.stdout.decode(errors='replace')}\n"
            f"stderr: {proc.stderr.decode(errors='replace')}"
        )
    else:
        assert proc.returncode != 0, (
            f"{vault.name}: expected `wiki doctor` to flag pending-proposal (non-zero exit), got 0."
        )
        assert b"pending-proposal" in proc.stdout, (
            f"{vault.name}: expected `pending-proposal` token in stdout. "
            f"Got: {proc.stdout.decode(errors='replace')[:500]}"
        )
        # Also verify the journal carries a PageProposalEvent.
        from llm_wiki_kit.journal import read_events
        from llm_wiki_kit.models import PageProposalEvent

        journal = vault / ".wiki.journal" / "journal.jsonl"
        events = list(read_events(journal))
        assert any(isinstance(e, PageProposalEvent) for e in events), (
            f"{vault.name}: journal should carry PageProposalEvent"
        )


# ---------------------------------------------------------------------------
# AC2 — seeded vaults (construction test #2)
# ---------------------------------------------------------------------------


def _user_pages(area: Path) -> list[Path]:
    return [p for p in area.iterdir() if p.suffix == ".md" and p.name != "README.md"]


@pytest.mark.parametrize(
    "vault",
    [
        pytest.param(FAMILY_MINI, id="family-mini"),
        pytest.param(WORK_OS_MINI, id="work-os-mini"),
    ],
)
def test_example_vaults_are_seeded(vault: Path) -> None:
    wiki = vault / "wiki"
    assert wiki.is_dir(), f"{vault.name}: missing wiki/ directory"
    empty_areas: list[str] = []
    for area in sorted(wiki.iterdir()):
        if not area.is_dir():
            continue
        pages = _user_pages(area)
        if not pages:
            empty_areas.append(area.name)
    assert not empty_areas, (
        f"{vault.name}: areas with no hand-authored user-content pages "
        f"(beyond README.md): {empty_areas}"
    )


# ---------------------------------------------------------------------------
# AC6 — regenerator `--check` is clean (construction test #3)
# ---------------------------------------------------------------------------


def test_regenerate_check_mode_clean() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "examples" / "regenerate.py"), "--check"],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
    )
    assert proc.returncode == 0, (
        f"`regenerate.py --check` reported divergence (exit {proc.returncode}).\n"
        f"stdout: {proc.stdout.decode(errors='replace')}\n"
        f"stderr: {proc.stderr.decode(errors='replace')}"
    )


# ---------------------------------------------------------------------------
# AC7 — idempotence (construction test #4)
# ---------------------------------------------------------------------------


def _iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in {".DS_Store", "Thumbs.db"}:
            yield path


def _normalize_for_compare(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix == ".jsonl":
        return cast(bytes, _import_regenerate().normalize_journal(data))
    return data


def _assert_trees_equal(a: Path, b: Path) -> None:
    a_files = {p.relative_to(a): _normalize_for_compare(p) for p in _iter_files(a)}
    b_files = {p.relative_to(b): _normalize_for_compare(p) for p in _iter_files(b)}
    assert set(a_files) == set(b_files), (
        f"file-set mismatch: only in {a.name}: {set(a_files) - set(b_files)}; "
        f"only in {b.name}: {set(b_files) - set(a_files)}"
    )
    for rel, content_a in a_files.items():
        assert content_a == b_files[rel], f"{rel}: byte mismatch after normalization"


def test_regenerate_is_idempotent_family(tmp_path: Path) -> None:
    regenerate = _import_regenerate()
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    regenerate.build_vault("family", one / "family-mini")
    regenerate.build_vault("family", two / "family-mini")
    _assert_trees_equal(one / "family-mini", two / "family-mini")


def test_regenerate_is_idempotent_conflict_pending(tmp_path: Path) -> None:
    regenerate = _import_regenerate()
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    regenerate.build_conflict_pending(one / "conflict-pending")
    regenerate.build_conflict_pending(two / "conflict-pending")
    _assert_trees_equal(one / "conflict-pending", two / "conflict-pending")


# ---------------------------------------------------------------------------
# AC7 — crash safety (construction test #5)
# ---------------------------------------------------------------------------


def test_apply_vault_replaces_existing_committed_tree(tmp_path: Path) -> None:
    """Happy path: apply_vault must replace a populated destination.

    POSIX `rename(2)` returns ENOTEMPTY on non-empty directory targets,
    so an `os.replace(staged, committed)`-only implementation would
    silently fail every regeneration that ran against the committed
    examples — this test catches that regression class.
    """

    regenerate = _import_regenerate()
    committed = tmp_path / "examples" / "family-mini"
    committed.parent.mkdir(parents=True)
    # Pre-populate the committed location with a placeholder file
    # that must be gone after the swap.
    committed.mkdir()
    (committed / "STALE.md").write_text("stale", encoding="utf-8")

    regenerate.apply_vault("family", committed)

    assert not (committed / "STALE.md").exists(), (
        "apply_vault did not replace pre-existing committed tree"
    )
    assert (committed / ".wiki.journal" / "journal.jsonl").is_file(), (
        "apply_vault produced an empty/incorrect tree"
    )
    # Sanity-check the new tree carries the kit's rendered files.
    assert (committed / "AGENTS.md").is_file()
    # Make sure no staging dir leaked.
    leaked = [p for p in committed.parent.iterdir() if p.name.startswith(".staging-")]
    assert not leaked, f"apply_vault leaked staging dirs: {leaked}"


def test_regenerate_crash_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a crash mid-apply; the committed vault must be restored.

    Copy `examples/family-mini/` into `tmp_path`, point the regenerator
    at it as the "committed" tree, then make the *second* `os.rename`
    (the staged → committed swap) raise. `apply_vault`'s rollback path
    renames the backup back into place, so the committed bytes are
    unchanged after the failed apply. The staging directory is cleaned
    up too.
    """

    regenerate = _import_regenerate()
    committed = tmp_path / "examples" / "family-mini"
    committed.parent.mkdir(parents=True)
    shutil.copytree(FAMILY_MINI, committed)
    before_snapshot = {
        p.relative_to(committed): p.read_bytes() for p in committed.rglob("*") if p.is_file()
    }

    original_rename = regenerate.os.rename
    call_count = {"n": 0}

    def flaky_rename(src: str, dst: str) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated crash during staged → committed rename")
        original_rename(src, dst)

    monkeypatch.setattr(regenerate.os, "rename", flaky_rename)
    with pytest.raises(OSError, match="simulated crash"):
        regenerate.apply_vault("family", committed)

    after_snapshot = {
        p.relative_to(committed): p.read_bytes() for p in committed.rglob("*") if p.is_file()
    }
    assert before_snapshot == after_snapshot, (
        "crash mid-apply left the committed tree mutated; --apply rollback is broken"
    )
    leaked = [p for p in committed.parent.iterdir() if p.name.startswith(".staging-")]
    assert not leaked, f"apply_vault leaked staging dirs after failure: {leaked}"


def test_regenerate_crash_safety_no_existing_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `committed` doesn't exist yet, a crash on the lone rename
    must still leave no staging directories behind.

    This is the `had_existing=False` branch of `apply_vault`'s
    rollback path — exercised only when running against a fresh
    destination. The function should re-raise without attempting any
    rollback (there's nothing to restore) and the outer `finally`
    must clean up the staging parent.
    """

    regenerate = _import_regenerate()
    committed = tmp_path / "examples" / "family-mini"
    committed.parent.mkdir(parents=True)
    # NOTE: `committed` does *not* exist yet.

    original_rename = regenerate.os.rename

    def fail_first_rename(src: str, dst: str) -> None:
        raise OSError("simulated crash on fresh-destination rename")

    monkeypatch.setattr(regenerate.os, "rename", fail_first_rename)
    with pytest.raises(OSError, match="simulated crash"):
        regenerate.apply_vault("family", committed)

    # Restore for cleanup.
    monkeypatch.setattr(regenerate.os, "rename", original_rename)

    leaked = [p for p in committed.parent.iterdir() if p.name.startswith(".staging-")]
    assert not leaked, f"apply_vault leaked staging dirs on fresh-destination crash: {leaked}"
    # The committed tree still does not exist (we never had one to start).
    assert not committed.exists()


# ---------------------------------------------------------------------------
# Guardrail — no unexpected new top-level directories (construction test #10)
# ---------------------------------------------------------------------------


def test_no_new_top_level_dirs_beyond_examples() -> None:
    actual = {
        p.name for p in REPO_ROOT.iterdir() if p.is_dir() and p.name not in IGNORED_TOP_LEVEL_DIRS
    }
    allowed = PRE_TASK_TOP_LEVEL_DIRS | {"examples"}
    unexpected = actual - allowed
    assert not unexpected, (
        f"Unexpected new top-level directories: {sorted(unexpected)}. "
        f"AGENTS.md requires an RFC for new top-level paths."
    )


# ---------------------------------------------------------------------------
# AC13 — no new runtime dependency (construction test #11)
# ---------------------------------------------------------------------------


def test_no_new_runtime_dep() -> None:
    pyproject = REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert deps == ["pyyaml>=6", "pydantic>=2"], (
        f"runtime dependencies changed: {deps!r}. "
        "Adding a runtime dep requires an ADR per AGENTS.md."
    )
