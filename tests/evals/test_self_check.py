"""Eval-suite self-check sentinel (RFC-0001 Task 20).

Always-passing sentinel that carries ``@pytest.mark.eval``. A
fully-skipped CI run still reports this as PASSED so a maintainer
can distinguish "harness intact, nothing exercised" from "harness
silently broken, no tests collected".

Spec: docs/specs/task-20-eval-harness/spec.md §AC14
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.eval


def test_eval_harness_self_check() -> None:
    """No env vars, no `claude`, no fixture — just confirms collection."""

    assert True
