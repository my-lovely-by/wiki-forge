"""Integration-test fixtures shared between the wheel-acceptance suites.

The session-scoped ``built_wheel`` fixture builds one wheel from the
source tree once per ``pytest`` invocation and hands its path to every
slow test that needs it. Wheel builds take a few seconds; doing them
once amortizes that cost across the wheel-contents and end-to-end
install tests.

See ``docs/specs/wheel-bundled-assets/plan.md`` step 3 §"What you'll
change" for the contract.

The ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` autouse fixture that the
default ``wiki init`` path relies on lives in ``tests/conftest.py``
so unit-level and eval-level tests that call ``cli.main(["init", …])``
to seed a vault are covered too.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the kit wheel once per session and return its path.

    The build runs via ``python -m build --wheel`` against the repo root.
    The ``build`` package is declared in ``[project.optional-dependencies].dev``
    so ``pip install -e .[dev]`` makes it available; if it's missing the
    test fails loudly with a one-line marker rather than the opaque
    ``ModuleNotFoundError`` traceback.
    """

    out_dir = tmp_path_factory.mktemp("wheel-build")
    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        pytest.fail(
            f"`python -m build` not available: {exc}. Install dev extras (pip install -e '.[dev]')."
        )
    except subprocess.CalledProcessError as exc:
        pytest.fail(f"wheel build failed:\nstdout:\n{exc.stdout}\nstderr:\n{exc.stderr}")

    wheels = list(out_dir.glob("llm_wiki_kit-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    return wheels[0]
