"""End-to-end ``pip install <wheel> && wiki init`` smoke (B5 acceptance).

Builds + installs the wheel into a tmp prefix, then drives ``wiki init``
through ``python -m llm_wiki_kit`` in a subprocess. Asserts the journal
exists and parses, and that at least one ``VaultInitEvent`` and one
``PrimitiveInstallEvent`` were appended. The whole flow runs in
isolation from the source checkout: ``PYTHONPATH`` points only at the
install prefix.

Marked ``@pytest.mark.slow`` because it builds a wheel and shells out a
subprocess; the wheel-acceptance CI workflow runs it on every PR that
touches packaging or asset trees.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from llm_wiki_kit.journal import read_events
from llm_wiki_kit.models import PrimitiveInstallEvent, VaultInitEvent

pytestmark = pytest.mark.slow


def test_pip_install_wheel_then_wiki_init_renders_a_vault(
    built_wheel: Path, tmp_path: Path
) -> None:
    prefix = tmp_path / "prefix"
    prefix.mkdir()

    # ``--no-deps`` keeps the install closed against the network and
    # avoids pulling in pyyaml/pydantic, which the test env already has.
    # ``--no-index`` is defense-in-depth: the wheel is a local file, so
    # pip should never need the index, and a future pip that grew an
    # "always check the index" step would otherwise flake on slow CI.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(prefix),
            "--no-deps",
            "--no-index",
            "--no-cache-dir",
            str(built_wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Verify the wheel's relocated asset prefix landed in the install.
    bundle = prefix / "llm_wiki_kit" / "_assets"
    assert (bundle / "recipes" / "family.yaml").is_file()
    assert (bundle / "core" / "primitive.yaml").is_file()

    vault = tmp_path / "vault"
    env = {**os.environ, "PYTHONPATH": str(prefix)}

    # Confirm the subprocess loads ``llm_wiki_kit`` from the wheel-install
    # prefix and not from the developer's editable install in the parent
    # env. Without this, a regression that silently bypasses ``PYTHONPATH``
    # would still pass the journal assertions below by hitting the
    # source-tree fallback in ``_resolve_kit_root``.
    where = subprocess.run(
        [sys.executable, "-c", "import llm_wiki_kit, sys; sys.stdout.write(llm_wiki_kit.__file__)"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    # Resolve both before comparing: macOS realizes pytest's ``tmp_path``
    # via ``/private/...`` symlinks, which ``parents`` would otherwise
    # fail to match against the unresolved prefix.
    loaded_from = Path(where.stdout).resolve()
    resolved_prefix = prefix.resolve()
    assert resolved_prefix in loaded_from.parents, (
        f"subprocess loaded llm_wiki_kit from {loaded_from}, expected under {resolved_prefix}"
    )
    result = subprocess.run(
        [sys.executable, "-m", "llm_wiki_kit", "init", str(vault), "--recipe", "family"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, (
        f"wiki init failed (exit {result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    journal_path = vault / ".wiki.journal" / "journal.jsonl"
    assert journal_path.is_file()

    events = read_events(journal_path)
    assert any(isinstance(e, VaultInitEvent) for e in events), (
        f"no VaultInitEvent in journal:\n{[type(e).__name__ for e in events]}"
    )
    assert any(isinstance(e, PrimitiveInstallEvent) for e in events), (
        f"no PrimitiveInstallEvent in journal:\n{[type(e).__name__ for e in events]}"
    )
