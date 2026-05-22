"""CI-workflow-shape unit tests (RFC-0001 Task 20).

Construction tests for Plan Step 6 — assert that
``.github/workflows/ci.yml`` excludes the ``eval`` marker and the
new ``.github/workflows/evals.yml`` runs the eval suite in its own
job. Reads the YAML rather than parsing pytest invocations because
the gate is mechanical-shape-of-the-config, not behavior of the
runner.

Spec: docs/specs/task-20-eval-harness/spec.md §AC3 + §AC4
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
EVALS_YML = REPO_ROOT / ".github" / "workflows" / "evals.yml"


def test_ci_yml_excludes_eval_marker() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    # The pytest step's `-m` filter must include `not eval`.
    assert "not eval" in text, (
        "ci.yml is missing the `not eval` filter — the eval suite would "
        "run in the fast lane and burn API budget on every PR."
    )


def test_evals_yml_runs_eval_marker() -> None:
    text = EVALS_YML.read_text(encoding="utf-8")
    assert "pytest tests/evals -m eval" in text


def test_evals_yml_pins_claude_version() -> None:
    """Pin to a version, not `@latest` — drift moves through PRs."""

    text = EVALS_YML.read_text(encoding="utf-8")
    match = re.search(
        r"npm install -g @anthropic-ai/claude-code@(\S+)",
        text,
    )
    assert match, "evals.yml does not pin the claude-code npm install"
    pinned = match.group(1)
    assert pinned != "latest", "claude-code is pinned to `@latest`; bump to a specific version"
    # A pinned version reads as something like `2.0.27` or `2.0.27-rc1`.
    assert re.match(r"^\d+\.\d+\.\d+", pinned), (
        f"unexpected pin format {pinned!r}; expected MAJOR.MINOR.PATCH"
    )


def test_evals_yml_uploads_junit_artifact() -> None:
    """AC12's skip reasons survive into the run artifact."""

    text = EVALS_YML.read_text(encoding="utf-8")
    assert "--junitxml" in text
    assert "actions/upload-artifact" in text


def test_evals_yml_has_no_nightly_cron() -> None:
    """User confirmation: no nightly cron — PR + main-merge cadence only."""

    text = EVALS_YML.read_text(encoding="utf-8")
    assert "schedule:" not in text, "evals.yml should NOT carry a nightly cron — see Spec §AC4."


def test_evals_yml_concurrency_cancels_in_progress() -> None:
    """Cancel in-progress runs on force-push so stale commits don't burn budget."""

    text = EVALS_YML.read_text(encoding="utf-8")
    assert "cancel-in-progress: true" in text


def test_ci_yml_still_runs_in_fast_lane() -> None:
    """The default CI suite stays green and fast; sanity check."""

    text = CI_YML.read_text(encoding="utf-8")
    assert "pytest -m" in text
    assert "not slow" in text
