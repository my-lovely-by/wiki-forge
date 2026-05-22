"""Invariant tests for the vault-side wiki-research SKILL.md.

The SKILL.md is documentation, not code, so it bypasses ruff/mypy. The
risk it carries is *drift* — a future CLI change (a new flag, a new
provider slug, a renamed frontmatter field) leaves the SKILL.md
documenting a surface that no longer exists. These tests grep-check
the SKILL body against the kit's actual surface (argparse subparser,
the dispatcher's frozen frontmatter key set, ``_PROVIDER_REGISTRY``,
each provider's ``DEFAULT_MODEL``) so the drift surfaces in CI rather
than in a confused user's chat transcript.

Spec: ``docs/specs/wiki-research-skill/spec.md`` §Invariants.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / "core" / "files" / "skills" / "wiki-research" / "SKILL.md"

# The dispatcher's frontmatter contract — frozen, hand-pinned in this
# test rather than introspected from ``_render_markdown`` so a future
# private refactor of that helper does not falsely flag SKILL drift.
# ``tests/unit/test_research_dispatch.py`` carries the dispatch side of
# the same fence (``test_dispatch_query_frontmatter_key_set_is_exactly_five``);
# this constant is the SKILL side. Both must change in the same PR.
EXPECTED_FRONTMATTER_KEYS: frozenset[str] = frozenset(
    {"provider", "model", "query", "fetched_at", "citations"}
)

# Provider slugs the kit explicitly does NOT ship. A SKILL mention of
# any of these would route Claude to a slug the dispatcher rejects.
# Heuristic for inclusion: well-known third-party research APIs across
# the two axes the kit's registry covers — web-search engines (Tavily,
# Exa, Bing, …) and academic-graph / paper-search APIs (Consensus,
# Elicit, Scite). Additions are deliberate, not maintenance churn:
# adding `cost_signal` to a SKILL revision does not affect this list.
FORBIDDEN_PROVIDER_SLUGS: frozenset[str] = frozenset(
    {
        # Web-search axis.
        "bing",
        "brave-search",
        "claude-research",
        "duckduckgo",
        "exa",
        "kagi",
        "openai",
        "tavily",
        "you-com",
        # Academic-graph / paper-search axis.
        "consensus",
        "elicit",
        "scite",
    }
)


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _frontmatter_and_body(text: str) -> tuple[dict[str, object], str]:
    parts = text.split("---", 2)
    assert len(parts) >= 3, "SKILL.md has no YAML frontmatter delimiters"
    loaded = yaml.safe_load(parts[1]) or {}
    assert isinstance(loaded, dict)
    return loaded, parts[2]


def test_skill_file_exists() -> None:
    assert SKILL_PATH.is_file(), f"missing SKILL.md at {SKILL_PATH}"


def test_frontmatter_is_valid() -> None:
    """`name:` is the SKILL slug; `description:` is 100..800 chars; license MIT.

    The `name:` field is the trigger surface for plugin-style loaders
    and the lookup key the trigger eval uses.
    """

    frontmatter, _ = _frontmatter_and_body(_read_skill())
    assert frontmatter.get("name") == "wiki-research"
    assert frontmatter.get("license") == "MIT"
    description = frontmatter.get("description")
    assert isinstance(description, str)
    assert 100 <= len(description) <= 800, (
        f"description length {len(description)} outside [100, 800]; matches "
        "wiki-search / wiki-conflict precedent"
    )


def test_skill_body_documents_only_shipped_flags() -> None:
    """Every `--flag` substring in the body is one the CLI actually accepts.

    Drift mode: a future CLI gains `--budget` without a SKILL update,
    or vice versa — the SKILL invents a flag the CLI rejects. The
    accepted set is read from argparse, not hand-coded, so a future
    addition to `_cmd_research` automatically widens the allowlist.
    """

    import argparse

    from llm_wiki_kit.cli import build_parser

    _, body = _frontmatter_and_body(_read_skill())

    parser = build_parser()
    research_subparser: argparse.ArgumentParser | None = None
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and "research" in choices:
            candidate = choices["research"]
            if isinstance(candidate, argparse.ArgumentParser):
                research_subparser = candidate
                break
    assert research_subparser is not None, "could not locate `research` subparser"

    accepted_flags: set[str] = set()
    for action in research_subparser._actions:
        for option_string in action.option_strings:
            if option_string.startswith("--"):
                accepted_flags.add(option_string)

    body_flags = set(re.findall(r"--[a-z][a-z0-9-]*", body))
    invented = body_flags - accepted_flags
    assert not invented, (
        f"SKILL.md documents flag(s) the CLI does not accept: {sorted(invented)!r}. "
        f"Accepted: {sorted(accepted_flags)!r}. "
        "Either the SKILL invented a flag (drop it) or `cli.py`'s research subparser "
        "stopped accepting one (restore it or update the SKILL)."
    )


def test_skill_body_documents_only_shipped_frontmatter_fields() -> None:
    """The body's `## Reading results` field-table names exactly the dispatcher's keys.

    The dispatcher emits a frozen key set — pinned at the top of this
    module as `EXPECTED_FRONTMATTER_KEYS` and verified from the
    dispatch side by
    `tests/unit/test_research_dispatch.py::test_dispatch_query_frontmatter_key_set_is_exactly_five`.
    The SKILL must not promise extra fields like `source_kind` or
    `verification_strength` as kit-emitted; those are vault-side
    conventions for downstream pages and live under §"Provenance and
    the Two-Source Rule".

    The check is *exact equality*: missing a key fails (drift toward
    fewer-than-shipped), and extra ``| `field` | ... |`` rows fail
    (drift toward more-than-shipped).
    """

    _, body = _frontmatter_and_body(_read_skill())

    marker = "## Reading results"
    assert marker in body, "SKILL.md missing the `## Reading results` section"
    section_start = body.index(marker)
    next_section = body.find("\n## ", section_start + len(marker))
    if next_section >= 0:
        section_text = body[section_start:next_section]
    else:
        section_text = body[section_start:]

    # Each frontmatter-field row of the table opens `| \`<key>\` |`.
    # Extract those keys so we can assert exact equality with the
    # contract — extra rows fail loudly.
    table_keys = set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section_text, flags=re.MULTILINE))
    assert table_keys == set(EXPECTED_FRONTMATTER_KEYS), (
        f"SKILL.md §'Reading results' field-table keys = {sorted(table_keys)!r}; "
        f"expected exactly {sorted(EXPECTED_FRONTMATTER_KEYS)!r}. "
        f"Drop any extra rows, restore any missing rows, and make the dispatch-side "
        f"contract pin match (tests/unit/test_research_dispatch.py)."
    )


def test_skill_body_references_no_forbidden_provider_slugs() -> None:
    """The SKILL body never names an unsupported third-party research API.

    Inverts the earlier "allow-list" approach: instead of policing
    every backticked noun the SKILL author writes, this test fails
    only when the SKILL mentions a slug the kit explicitly does NOT
    ship. The forbidden set is bounded and curated; adding domain
    vocabulary to the SKILL does not affect it.

    Substring search (case-insensitive, word-bounded) — catches the
    slug whether it appears backticked (`` `tavily` ``), as an
    un-backticked pipe-table cell (`| tavily | low |`), or as a flag
    value (`--provider tavily`).
    """

    _, body = _frontmatter_and_body(_read_skill())
    body_lower = body.lower()
    leaked = []
    for slug in sorted(FORBIDDEN_PROVIDER_SLUGS):
        # Word boundary on the LHS rules out `excellent` matching
        # `exa`; the RHS character class catches both backticked
        # (`tavily``), bare (`tavily `), and table-cell (`tavily|`)
        # forms. Hyphens in slugs are part of the token so we use a
        # custom boundary rather than `\b`.
        pattern = rf"(?:^|[^a-z0-9-]){re.escape(slug)}(?:[^a-z0-9-]|$)"
        if re.search(pattern, body_lower):
            leaked.append(slug)
    assert not leaked, (
        f"SKILL.md mentions provider slug(s) the kit does not ship: {leaked!r}. "
        f"Either route Claude to a registered slug or drop the reference. "
        f"Registered slugs are loaded at dispatch time from `_PROVIDER_REGISTRY`."
    )


def test_skill_body_names_each_registered_provider() -> None:
    """Each `_PROVIDER_REGISTRY` slug appears at least once in the SKILL body.

    The picker decision table is the SKILL's load-bearing logic for
    multi-provider vaults. A SKILL that silently drops a shipped
    provider strands users of that provider with no routing guidance.
    """

    from llm_wiki_kit.research.dispatch import _PROVIDER_REGISTRY

    _, body = _frontmatter_and_body(_read_skill())
    missing = [slug for slug in _PROVIDER_REGISTRY if slug not in body]
    assert not missing, (
        f"SKILL.md does not mention registered provider slug(s): {missing!r}. "
        f"Add a row to the picker decision table in §'Picking a provider'."
    )


def test_skill_body_mentions_each_providers_current_default_model() -> None:
    """Each provider's `DEFAULT_MODEL` literal appears verbatim in the SKILL body.

    The SKILL's "Reading results" table gives concrete examples of the
    `model:` frontmatter field (`sonar-pro`, `gemini-2.5-pro`,
    `graph-v1`). When the kit renames a default — `gemini-2.5-pro` →
    `gemini-3-pro`, say — the SKILL's example silently drifts.
    """

    from llm_wiki_kit.research.providers import gemini, perplexity, semantic_scholar

    _, body = _frontmatter_and_body(_read_skill())
    for provider_module in (perplexity, gemini, semantic_scholar):
        default_model = provider_module.DEFAULT_MODEL
        assert default_model in body, (
            f"{provider_module.__name__}.DEFAULT_MODEL = {default_model!r} but the "
            f"SKILL body does not mention it. Bisect via `git log`: if "
            f"`llm_wiki_kit/research/providers/{provider_module.__name__.rsplit('.', 1)[-1]}.py` "
            f"changed recently, update the SKILL.md §Reading results examples; if "
            f"the SKILL.md changed recently, restore the model literal."
        )


def test_skill_body_has_no_kit_side_paths() -> None:
    """Vault-side audience — never name kit-side paths.

    A user's vault doesn't contain `llm_wiki_kit/`, `docs/`, `tests/`,
    `templates/`, or the v1 `.claude/research-providers.yaml`
    location. A SKILL.md mentioning those routes Claude to read
    nothing.
    """

    _, body = _frontmatter_and_body(_read_skill())
    forbidden = (
        "llm_wiki_kit/",
        "docs/",
        "tests/",
        "templates/",
        ".claude/research-providers.yaml",
    )
    leaked = [s for s in forbidden if s in body]
    assert not leaked, f"SKILL.md leaks kit-side path(s) into vault-side audience: {leaked!r}"


def test_skill_body_names_each_graceful_degradation_case() -> None:
    """All provider-count cases are documented (plus the unknown-slug error).

    Drift mode: a future SKILL refactor drops the empty-provider case
    and accidentally leaves Claude stranded when no provider is
    installed.
    """

    _, body = _frontmatter_and_body(_read_skill())
    required_substrings = (
        "No provider installed",
        "One provider installed",
        "More than one provider installed",
        "has no implementation in this kit version",
        "infrastructure:research not installed",
        "no research providers installed",
    )
    missing = [s for s in required_substrings if s not in body]
    assert not missing, f"SKILL.md missing graceful-degradation case string(s): {missing!r}"


def test_skill_body_no_key_leak() -> None:
    """No real-looking API-key substring, no `<your-api-key>`-shaped placeholder.

    A SKILL that includes a placeholder Claude might copy verbatim
    (`PERPLEXITY_API_KEY=sk-xxxxxxxx`) is a security regression — the
    user's eyes glaze over and the placeholder lands in their shell.
    Env-var *names* are fine; values and value-shaped placeholders are
    not.
    """

    text = _read_skill()
    forbidden_patterns = (
        # Vendor key prefixes — kit-side `_KEY_LIKE_RE` shape.
        r"sk-[A-Za-z0-9_-]{12,}",
        r"pplx-[A-Za-z0-9_-]{12,}",
        r"AIza[A-Za-z0-9_-]{20,}",
        r"gk-[A-Za-z0-9_-]{12,}",
        r"ss-[A-Za-z0-9_-]{12,}",
        # Placeholders Claude might copy verbatim.
        r"<your[-_]?api[-_]?key>",
        r"<api[-_]?key>",
    )
    for pattern in forbidden_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        assert match is None, (
            f"SKILL.md contains key-shaped substring matching {pattern!r}: {match.group(0)!r}"
            if match
            else ""
        )
