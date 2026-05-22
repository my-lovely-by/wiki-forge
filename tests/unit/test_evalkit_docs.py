"""Authoring-guide presence checks (RFC-0001 Task 20).

Plan Step 7. Asserts the explanation guide exists, links to the spec,
mentions the marker, and is indexed from the explanation README. The
contents of the guide are reviewed by humans, not asserted in detail
— this test only catches outright deletion or unlinked drift.

Spec: docs/specs/task-20-eval-harness/spec.md §AC7
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "docs" / "guides" / "explanation" / "evals.md"
INDEX = REPO_ROOT / "docs" / "guides" / "explanation" / "README.md"
EVALS_README = REPO_ROOT / "tests" / "evals" / "README.md"


def test_authoring_guide_exists() -> None:
    assert GUIDE.is_file()


def test_authoring_guide_references_spec() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    # The guide must point readers at the canonical spec.
    assert "task-20-eval-harness/spec.md" in text


def test_authoring_guide_documents_eval_marker() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.eval" in text


def test_authoring_guide_documents_addopts_foot_gun() -> None:
    """The marker addopts foot-gun is the most likely contributor papercut."""

    text = GUIDE.read_text(encoding="utf-8")
    assert "addopts" in text.lower()
    assert "-m eval" in text


def test_explanation_index_links_to_evals_guide() -> None:
    text = INDEX.read_text(encoding="utf-8")
    assert "evals.md" in text


def test_evals_dir_readme_exists_and_points_to_spec() -> None:
    text = EVALS_README.read_text(encoding="utf-8")
    assert "task-20-eval-harness/spec.md" in text
    assert "evals.md" in text
