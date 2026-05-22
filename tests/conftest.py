"""Shared pytest fixtures across the kit's test suite.

The autouse fixtures below run on every test in the suite:

* ``_reset_lazy_kit_root`` resets the lazy-cache module attribute that
  ``cli._kit_root()`` writes to. Without per-test reset, a unit test
  that monkeypatches ``cli._bundled_assets_path`` to a tmp directory
  would leave ``cli._KIT_ROOT`` pointing at a deleted tmp path for
  subsequent tests.

  See ``docs/specs/wheel-bundled-assets/spec.md`` §Invariants for the
  lazy resolution contract and ``tests/unit/test_cli_kit_root.py`` for
  the grep guard that keeps direct ``_KIT_ROOT`` reads contained.

* ``_git_author_identity`` injects a fixed ``GIT_AUTHOR_*`` /
  ``GIT_COMMITTER_*`` identity into the env so the default
  ``wiki init`` path (which makes one initial commit) succeeds on a
  hermetic CI runner that lacks ``~/.gitconfig``. Tests that exercise
  the missing-identity failure surface call ``monkeypatch.delenv``
  for these vars locally; pytest's monkeypatch stacking gives the
  delete precedence over this fixture's set.

  Defined at the suite root (not under ``tests/integration/``) so
  unit-level tests that call ``cli.main(["init", …])`` to seed a
  vault — ``tests/unit/test_upgrade.py`` and ``tests/evals/conftest.py``
  do this — are also covered. See
  ``docs/specs/wiki-init-git/spec.md`` §Error cases.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from llm_wiki_kit import cli


@pytest.fixture(autouse=True)
def _reset_lazy_kit_root() -> Iterator[None]:
    """Reset ``cli._KIT_ROOT`` to ``None`` before and after each test."""

    cli._KIT_ROOT = None
    yield
    cli._KIT_ROOT = None


@pytest.fixture(autouse=True)
def _git_author_identity(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Inject a fixed ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` identity."""

    monkeypatch.setenv("GIT_AUTHOR_NAME", "kit-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "kit-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "kit-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "kit-test@example.invalid")
    yield
