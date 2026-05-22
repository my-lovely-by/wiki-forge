"""Trigger eval: prompting about a `.proposed` sidecar loads `wiki-conflict`.

Plan §5a. Reads the SKILL name from the vault's wiki-conflict
SKILL.md frontmatter at fixture time so a future rename doesn't
silently flake the assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests import evalkit

pytestmark = pytest.mark.eval


def _skill_name(vault: Path, skill_dir_name: str) -> str:
    """Read the SKILL's frontmatter `name:` field at fixture-build time."""

    skill_md = vault / "skills" / skill_dir_name / "SKILL.md"
    if not skill_md.is_file():
        pytest.skip(f"SKILL.md not found at {skill_md}")
    text = skill_md.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        pytest.skip(f"SKILL.md at {skill_md} has no YAML frontmatter")
    meta = yaml.safe_load(parts[1]) or {}
    name = meta.get("name")
    if not isinstance(name, str):
        pytest.skip(f"SKILL.md at {skill_md} has no string `name:` field")
    return name


def test_prompting_about_sidecar_loads_wiki_conflict_skill(
    conflict_pending_vault: Path,
) -> None:
    evalkit.skip_if_env_unset("ANTHROPIC_API_KEY")
    evalkit.skip_if_no_claude()
    skill_name = _skill_name(conflict_pending_vault, "wiki-conflict")

    # The prompt describes the user's situation in natural language —
    # it does NOT name the SKILL path. The eval tests that Claude
    # discovers the right SKILL by reading the vault's AGENTS.md (which
    # documents `skills/wiki-conflict/`) or by globbing the skills/
    # directory. Naming the path in the prompt would reduce the eval
    # to "does Claude follow direct instructions" — a tautology.
    prompt = (
        "There's a .proposed sidecar at meetings/2026-05-12-q2.md.proposed "
        "in this vault. How do I deal with it? Look at the vault's docs "
        "first to figure out the right approach."
    )
    result = evalkit.run_claude(
        prompt=prompt,
        vault=conflict_pending_vault,
        allowed_tools=["Read", "Glob"],
        timeout_s=180.0,
    )
    if result.timed_out:
        pytest.fail(f"claude timed out: {evalkit.redact(result.stderr[:400])}")
    # Decode failures invalidate "first SKILL" ordering — surface
    # rather than silently passing on a truncated transcript.
    if result.decode_failures:
        pytest.fail(
            f"transcript had {result.decode_failures} undecodable lines; "
            f"first-SKILL ordering is not trustworthy"
        )

    # Assert wiki-conflict was loaded *and* was the FIRST SKILL Claude
    # touched — distinguishes "found the right SKILL" from "scanned
    # every SKILL and is still confused" (the Read-path branch alone
    # accepts either).
    skills_read = evalkit.ordered_skill_reads(result)
    if not skills_read:
        # Falls through to assert_skill_loaded, which surfaces the
        # transcript head when no SKILL was touched at all.
        evalkit.assert_skill_loaded(result, skill_name)
        return
    assert skills_read[0] == skill_name, (
        f"first SKILL Claude loaded was {skills_read[0]!r}, expected {skill_name!r}. "
        f"All SKILLs read in order: {skills_read!r}"
    )
