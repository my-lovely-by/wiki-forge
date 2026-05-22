"""Regression tests for ``tools/install-skill.py`` dependency closures.

Pins the contract from issue #60 lane 2 task 1: installing the
``bug-fix`` skill must not drag in the Ralph harness. Ralph is the AFK
variant of ``work-loop`` — adopters who only want the bug-fix flow
should not have to take ``tools/ralph.sh`` and ``tools/RALPH.md``
along for the ride.

The originating PR (#24) shipped the closure that did drag Ralph in,
flagged as a known follow-up. This test exists so the trimmed closure
stays trimmed.

The installer is a stdlib Python script (``tools/install-skill.py``)
guarded by ``if __name__ == "__main__":``; its ``build_plan`` and
``Plan`` are ordinary callables. Loading it via ``importlib`` keeps
this an in-process unit test — no subprocess, no tmp_path, no
filesystem — and lets assertions land directly on the planned copy
list so closure-walker regressions point at the right place.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_PATH = REPO_ROOT / "tools" / "install-skill.py"


def _load_installer() -> ModuleType:
    """Import ``tools/install-skill.py`` as the module ``install_skill``.

    The file name uses a hyphen (not a valid Python identifier), so
    ``importlib.util.spec_from_file_location`` is the supported route.
    """
    spec = importlib.util.spec_from_file_location("install_skill", INSTALLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec — the installer defines a
    # @dataclass, and dataclasses resolve forward refs via
    # sys.modules[cls.__module__]. Without registration that lookup
    # returns None and the first access blows up with AttributeError.
    sys.modules["install_skill"] = module
    spec.loader.exec_module(module)
    return module


def _bugfix_copy_targets() -> set[str]:
    """Build the install plan for ``bug-fix`` and return the dest-relative
    paths the installer would write."""
    install_skill = _load_installer()
    plan = install_skill.Plan()
    target = install_skill.resolve_target("bug-fix")
    target_rel = target.relative_to(install_skill.REPO_ROOT).as_posix()
    install_skill.build_plan(target_rel, owning_skill="bug-fix", plan=plan)
    return {dest for (_src, dest) in plan.copies}


def test_bugfix_closure_excludes_ralph() -> None:
    """The ``bug-fix`` install closure must not contain Ralph artefacts.

    Adopters who only want the bug-fix flow should not see the AFK
    harness land in their repo. Ralph is reachable from the ``work-loop``
    skill's prose; pulling it in is an opt-in act, not a transitive
    side effect.
    """
    targets = _bugfix_copy_targets()
    leaked = {"tools/ralph.sh", "tools/RALPH.md"} & targets
    assert not leaked, (
        f"bug-fix install closure should not contain Ralph artefacts; found {sorted(leaked)}"
    )


def test_bugfix_closure_keeps_workloop_non_ralph_deps() -> None:
    """Trimming Ralph from ``work-loop``'s manifest must not collateral-
    damage the rest of the closure. Pin the non-Ralph dependency set so
    an accidental over-trim shows up here, not in eval flakes."""
    targets = _bugfix_copy_targets()
    expected = {
        # Entry skill and its direct dep.
        ".claude/skills/bug-fix/SKILL.md",
        ".claude/skills/work-loop/SKILL.md",
        # Skills work-loop depends on.
        ".claude/skills/new-spec/SKILL.md",
        # Subagents work-loop drives.
        ".claude/agents/adversarial-reviewer.md",
        ".claude/agents/security-reviewer.md",
        ".claude/agents/quality-engineer.md",
        ".claude/agents/implementer.md",
        # Loop machinery and knowledge base.
        "tools/check-done.py",
        "tools/hooks/session-start.sh",
        "tools/hooks/pre-pr.sh",
        "docs/_templates/state.json",
        "docs/_templates/spec.md",
        "docs/_templates/plan.md",
        "docs/knowledge/README.md",
        "docs/knowledge/patterns.jsonl",
    }
    missing = expected - targets
    assert not missing, (
        f"work-loop closure for bug-fix is missing expected entries: {sorted(missing)}"
    )
