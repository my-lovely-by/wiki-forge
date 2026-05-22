"""Cross-cutting layout + grep guards for the eval harness (RFC-0001 Task 20).

Plan Step 8. The unit-test floor that enforces:

- AC9: `evalkit` lives under `tests/`, never imported from runtime.
- AC12: no `pytest.xfail` lands in `tests/evals/`.

Spec: docs/specs/task-20-eval-harness/spec.md
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT_PKG = REPO_ROOT / "llm_wiki_kit"
EVALS_DIR = REPO_ROOT / "tests" / "evals"


def _iter_kit_files() -> list[Path]:
    return sorted(KIT_PKG.rglob("*.py"))


def _iter_eval_files() -> list[Path]:
    return sorted(EVALS_DIR.rglob("*.py"))


def test_runtime_does_not_import_evalkit() -> None:
    """`evalkit` is test-only; runtime code must never reach into tests/."""

    offenders: list[tuple[Path, str]] = []
    for file in _iter_kit_files():
        text = file.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "from tests." in stripped or "import evalkit" in stripped:
                offenders.append((file.relative_to(REPO_ROOT), stripped))
                break
    assert not offenders, (
        f"runtime imports test-only helpers — this would ship in the "
        f"wheel and is an architectural smell:\n{offenders!r}"
    )


def test_evals_dir_has_no_xfail() -> None:
    """AC12: `xfail` masks regressions; upstream errors use `pytest.skip`."""

    offenders: list[tuple[Path, str]] = []
    for file in _iter_eval_files():
        text = file.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "pytest.xfail" in stripped or "@pytest.mark.xfail" in stripped:
                offenders.append((file.relative_to(REPO_ROOT), stripped))
                break
    assert not offenders, (
        f"`pytest.xfail` in tests/evals/ — AC12 says use `pytest.skip` "
        f"with a surfaced reason instead:\n{offenders!r}"
    )


def test_evals_self_check_sentinel_present() -> None:
    """AC14: a sentinel test that always passes keeps the suite reportable."""

    sentinel = EVALS_DIR / "test_self_check.py"
    assert sentinel.is_file()
    text = sentinel.read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.eval" in text
    assert "assert True" in text


def test_pyproject_eval_marker_documented() -> None:
    """A spot-check that the eval marker description names the suite path."""

    import tomllib

    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = cfg["tool"]["pytest"]["ini_options"]["markers"]
    eval_marker = next((m for m in markers if m.startswith("eval:")), None)
    assert eval_marker is not None
    assert "tests/evals/" in eval_marker
