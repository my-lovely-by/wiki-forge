"""Marker registration + collection-shape unit tests (RFC-0001 Task 20).

Construction tests for plan Step 2: the `eval` marker is registered,
the addopts flip excludes it from the fast lane, and an explicit
`-m eval` invocation collects every eval scenario.

Spec: docs/specs/task-20-eval-harness/spec.md §AC2
Plan: docs/specs/task-20-eval-harness/plan.md Step 2 + Step 3
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
EVALS_DIR = REPO_ROOT / "tests" / "evals"


def _pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_eval_marker_registered() -> None:
    cfg = _pyproject()
    markers = cfg["tool"]["pytest"]["ini_options"]["markers"]
    eval_marker = next((m for m in markers if m.startswith("eval:")), None)
    assert eval_marker is not None, f"eval marker missing from {markers}"


def test_addopts_excludes_eval_and_slow() -> None:
    cfg = _pyproject()
    addopts = cfg["tool"]["pytest"]["ini_options"]["addopts"]
    assert "not eval" in addopts
    assert "not slow" in addopts


def test_addopts_excludes_eval_dir_from_bare_pytest() -> None:
    """Path-based selection inherits the addopts `-m` filter; this is by design.

    Spec §Invariants pins this behavior so the fast `pytest`
    invocation stays fast. The trade-off is that a developer
    running `pytest tests/evals/x.py` without `-m eval` sees
    "0 tests collected" — documented in the authoring guide.

    If a future PR resolves this foot-gun (e.g. via a
    `pytest_collection_modifyitems` hook that strips `-m` on
    explicit selection), delete this test and update the guide.
    """

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/evals/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # pytest's `-q` summary line on a zero-collection run reads
    # `no tests ran in Xs` or `0 tests collected`. Either is fine
    # as long as the collected count is zero — and explicitly NOT
    # one or more, which would be a regression.
    combined = proc.stdout + proc.stderr
    # Acceptable outputs on a fully-excluded run:
    #   `no tests collected (N deselected) in Xs`
    #   `no tests ran in Xs`
    # Unacceptable (regression): any `M tests collected` line where
    # the rest of the line doesn't say "N deselected" with M remaining
    # available. The simplest check is "no tests collected" or "no
    # tests ran" appears.
    assert "no tests collected" in combined or "no tests ran" in combined, (
        f"bare pytest collected eval tests — addopts filter regressed:\n{combined}"
    )


def test_marker_select_collects_at_least_five_eval_tests() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/evals", "-m", "eval"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = proc.stdout + proc.stderr
    # Some pytest versions report `N tests collected`; others
    # write the count on the summary line. Either way the count
    # is in the output and must be >= 5.
    match = re.search(r"(\d+)\s+tests?\s+collected", combined)
    assert match, f"could not parse collection count from output:\n{combined}"
    count = int(match.group(1))
    assert count >= 5, f"expected at least 5 eval tests collected, got {count}"


def _iter_eval_test_files() -> list[Path]:
    return sorted(EVALS_DIR.rglob("test_*.py"))


def test_every_eval_file_carries_module_level_pytestmark() -> None:
    """A developer running `pytest tests/evals/x.py` sees the marker at the top.

    Mitigates the addopts foot-gun: explicit-path selection without
    `-m eval` reports zero collected, but reading the file shows why.
    """

    offenders: list[Path] = []
    for file in _iter_eval_test_files():
        text = file.read_text(encoding="utf-8")
        if "pytestmark = pytest.mark.eval" not in text:
            offenders.append(file)
    assert not offenders, f"missing pytestmark in: {[str(p) for p in offenders]}"


def test_five_eval_family_dirs_present() -> None:
    expected = {"trigger", "outcome", "provenance", "conflict", "research"}
    actual = {p.name for p in EVALS_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")}
    assert expected <= actual, f"missing family dirs: {expected - actual}"
    for family in expected:
        family_dir = EVALS_DIR / family
        scenarios = list(family_dir.glob("test_*.py"))
        assert scenarios, f"family {family!r} has no test_*.py scenarios"


def test_python_version_supports_tomllib() -> None:
    """tomllib landed in 3.11; the kit requires 3.11+."""

    assert sys.version_info >= (3, 11)
