"""Tests for ``llm_wiki_kit.primitives``.

The module owns three surfaces named in the migration plan:

* ``load_primitive(path)`` — read a ``primitive.yaml`` and validate via the
  Pydantic ``Primitive`` model from ``models.py``.
* ``discover_primitives(templates_dir)`` — walk ``templates_dir/<kind>/<name>/``
  and load every primitive it finds, sorted alphabetically by name.
* ``resolve_dependencies(primitives)`` — topologically sort a closed list of
  primitives by ``requires:``, raising on cycles and missing dependencies.

The kit's ``core/`` primitive sits at the repo root rather than under
``templates/``; the loader treats it like any other directory, so the
``always-include-core`` policy lives in the installer (Task 10), not here.
That keeps ``primitives.py`` filesystem-aware but recipe-agnostic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki_kit.errors import PrimitiveError, ValidationError, WikiError
from llm_wiki_kit.models import Contribution, Primitive, PrimitiveKind
from llm_wiki_kit.primitives import (
    discover_primitives,
    load_primitive,
    resolve_dependencies,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_primitive(
    root: Path,
    name: str,
    *,
    kind: str = "content-type",
    version: str = "0.1.0",
    description: str = "Test primitive.",
    requires: list[str] | None = None,
    contributes_to: list[dict[str, str]] | None = None,
) -> Path:
    """Write a minimal ``primitive.yaml`` and return the primitive directory."""

    root.mkdir(parents=True, exist_ok=True)
    lines = [
        f"name: {name}",
        f"kind: {kind}",
        f"version: {version}",
        f"description: {description}",
    ]
    if requires:
        lines.append("requires:")
        lines.extend(f"  - {r}" for r in requires)
    if contributes_to:
        lines.append("contributes_to:")
        for contribution in contributes_to:
            lines.append(f"  - file: {contribution['file']}")
            lines.append(f"    region: {contribution['region']}")
    (root / "primitive.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _make(name: str, requires: list[str] | None = None) -> Primitive:
    return Primitive.model_validate(
        {
            "name": name,
            "kind": "content-type",
            "version": "0.1.0",
            "description": f"{name} primitive.",
            "requires": requires or [],
        }
    )


# ---------------------------------------------------------------------------
# load_primitive
# ---------------------------------------------------------------------------


def test_load_primitive_parses_minimal_manifest(tmp_path: Path) -> None:
    _write_primitive(tmp_path / "meeting", name="meeting")
    primitive = load_primitive(tmp_path / "meeting")
    assert primitive.name == "meeting"
    assert primitive.kind is PrimitiveKind.CONTENT_TYPE
    assert primitive.version == "0.1.0"
    assert primitive.requires == []
    assert primitive.contributes_to == []


def test_load_primitive_parses_requires_and_contributions(tmp_path: Path) -> None:
    _write_primitive(
        tmp_path / "meeting",
        name="meeting",
        requires=["core", "people"],
        contributes_to=[
            {"file": "AGENTS.md", "region": "content-types"},
            {"file": "frontmatter.schema.yaml", "region": "fields"},
        ],
    )
    primitive = load_primitive(tmp_path / "meeting")
    assert primitive.requires == ["core", "people"]
    assert primitive.contributes_to == [
        Contribution(file="AGENTS.md", region="content-types"),
        Contribution(file="frontmatter.schema.yaml", region="fields"),
    ]


def test_load_primitive_raises_wikierror_when_directory_missing(tmp_path: Path) -> None:
    with pytest.raises(PrimitiveError) as excinfo:
        load_primitive(tmp_path / "missing")
    assert isinstance(excinfo.value, WikiError)
    assert "missing" in str(excinfo.value)


def test_load_primitive_raises_wikierror_when_manifest_missing(tmp_path: Path) -> None:
    (tmp_path / "noyaml").mkdir()
    with pytest.raises(PrimitiveError) as excinfo:
        load_primitive(tmp_path / "noyaml")
    assert "primitive.yaml" in str(excinfo.value)


def test_load_primitive_raises_wikierror_on_malformed_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "primitive.yaml").write_text("name: ok\nkind: : invalid\n", encoding="utf-8")
    with pytest.raises(PrimitiveError):
        load_primitive(bad)


def test_load_primitive_raises_wikierror_when_yaml_is_not_a_mapping(tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "primitive.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(PrimitiveError):
        load_primitive(bad)


def test_load_primitive_raises_validationerror_on_bad_schema(tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "primitive.yaml").write_text(
        "name: Bad Name\nkind: content-type\nversion: 0.1.0\ndescription: x\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_primitive(bad)


def test_load_primitive_raises_validationerror_on_unknown_kind(tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "primitive.yaml").write_text(
        "name: ok\nkind: skill\nversion: 0.1.0\ndescription: x\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_primitive(bad)


# ---------------------------------------------------------------------------
# discover_primitives
# ---------------------------------------------------------------------------


def test_discover_primitives_walks_kind_subdirectories(tmp_path: Path) -> None:
    _write_primitive(tmp_path / "ontologies" / "people", name="people", kind="ontology")
    _write_primitive(tmp_path / "content-types" / "meeting", name="meeting")
    _write_primitive(
        tmp_path / "operations" / "weekly-digest",
        name="weekly-digest",
        kind="operation",
    )
    found = discover_primitives(tmp_path)
    assert [p.name for p in found] == ["meeting", "people", "weekly-digest"]


def test_discover_primitives_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    assert discover_primitives(tmp_path / "does-not-exist") == []


def test_discover_primitives_returns_empty_when_dir_empty(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert discover_primitives(tmp_path / "empty") == []


def test_discover_primitives_ignores_directories_without_manifest(tmp_path: Path) -> None:
    (tmp_path / "ontologies" / "stray").mkdir(parents=True)
    _write_primitive(tmp_path / "ontologies" / "people", name="people", kind="ontology")
    found = discover_primitives(tmp_path)
    assert [p.name for p in found] == ["people"]


def test_discover_primitives_sorts_alphabetically(tmp_path: Path) -> None:
    _write_primitive(tmp_path / "ontologies" / "zeta", name="zeta", kind="ontology")
    _write_primitive(tmp_path / "ontologies" / "alpha", name="alpha", kind="ontology")
    _write_primitive(tmp_path / "ontologies" / "mu", name="mu", kind="ontology")
    found = discover_primitives(tmp_path)
    assert [p.name for p in found] == ["alpha", "mu", "zeta"]


def test_discover_primitives_propagates_load_errors(tmp_path: Path) -> None:
    """A single bad primitive should fail discovery loudly — silent skips
    would hide typos in hand-edited YAML."""
    _write_primitive(tmp_path / "ontologies" / "people", name="people", kind="ontology")
    bad = tmp_path / "ontologies" / "bad"
    bad.mkdir()
    (bad / "primitive.yaml").write_text("kind: nope\n", encoding="utf-8")
    with pytest.raises(WikiError):
        discover_primitives(tmp_path)


def test_discover_primitives_skips_non_kind_directories(tmp_path: Path) -> None:
    """Discovery walks ``<templates_dir>/<kind>/<name>/`` — a stray directory
    at the top level (e.g. ``templates/README.md``-style debris) shouldn't
    be mistaken for a primitive kind."""

    (tmp_path / "README.md").write_text("not a primitive\n", encoding="utf-8")
    _write_primitive(tmp_path / "content-types" / "meeting", name="meeting")
    found = discover_primitives(tmp_path)
    assert [p.name for p in found] == ["meeting"]


# ---------------------------------------------------------------------------
# resolve_dependencies
# ---------------------------------------------------------------------------


def test_resolve_dependencies_empty_list() -> None:
    assert resolve_dependencies([]) == []


def test_resolve_dependencies_no_requires_sorts_alphabetically() -> None:
    p1 = _make("meeting")
    p2 = _make("core")
    p3 = _make("people")
    assert [p.name for p in resolve_dependencies([p1, p2, p3])] == [
        "core",
        "meeting",
        "people",
    ]


def test_resolve_dependencies_orders_deps_before_dependents() -> None:
    core = _make("core")
    people = _make("people", requires=["core"])
    meeting = _make("meeting", requires=["core", "people"])
    ordered = resolve_dependencies([meeting, people, core])
    assert [p.name for p in ordered] == ["core", "people", "meeting"]


def test_resolve_dependencies_alphabetical_tiebreaker_for_independent_nodes() -> None:
    """Two primitives with the same dependency set should install in
    alphabetical order — predictable installs are reproducible installs."""
    core = _make("core")
    zeta = _make("zeta", requires=["core"])
    alpha = _make("alpha", requires=["core"])
    mu = _make("mu", requires=["core"])
    ordered = resolve_dependencies([zeta, alpha, mu, core])
    assert [p.name for p in ordered] == ["core", "alpha", "mu", "zeta"]


def test_resolve_dependencies_handles_diamond() -> None:
    core = _make("core")
    a = _make("a", requires=["core"])
    b = _make("b", requires=["core"])
    top = _make("top", requires=["a", "b"])
    ordered = resolve_dependencies([top, b, a, core])
    names = [p.name for p in ordered]
    assert names.index("core") < names.index("a")
    assert names.index("core") < names.index("b")
    assert names.index("a") < names.index("top")
    assert names.index("b") < names.index("top")
    # Alphabetical tiebreaker between a and b
    assert names.index("a") < names.index("b")


def test_resolve_dependencies_is_deterministic_regardless_of_input_order() -> None:
    core = _make("core")
    a = _make("a", requires=["core"])
    b = _make("b", requires=["core"])
    first = [p.name for p in resolve_dependencies([core, a, b])]
    second = [p.name for p in resolve_dependencies([b, a, core])]
    third = [p.name for p in resolve_dependencies([a, core, b])]
    assert first == second == third


def test_resolve_dependencies_raises_on_missing_dep() -> None:
    meeting = _make("meeting", requires=["people"])
    with pytest.raises(PrimitiveError) as excinfo:
        resolve_dependencies([meeting])
    assert "people" in str(excinfo.value)
    assert "meeting" in str(excinfo.value)


def test_resolve_dependencies_raises_on_self_cycle() -> None:
    self_ref = _make("loop", requires=["loop"])
    with pytest.raises(PrimitiveError) as excinfo:
        resolve_dependencies([self_ref])
    assert "cycle" in str(excinfo.value).lower()


def test_resolve_dependencies_raises_on_two_node_cycle() -> None:
    a = _make("a", requires=["b"])
    b = _make("b", requires=["a"])
    with pytest.raises(PrimitiveError) as excinfo:
        resolve_dependencies([a, b])
    assert "cycle" in str(excinfo.value).lower()


def test_resolve_dependencies_raises_on_three_node_cycle() -> None:
    a = _make("a", requires=["b"])
    b = _make("b", requires=["c"])
    c = _make("c", requires=["a"])
    with pytest.raises(PrimitiveError):
        resolve_dependencies([a, b, c])


def test_resolve_dependencies_raises_wikierror_subclass() -> None:
    """Per the migration plan: every error from this module is a WikiError
    subclass so the CLI boundary catches one type."""
    with pytest.raises(WikiError):
        resolve_dependencies([_make("meeting", requires=["people"])])


def test_resolve_dependencies_rejects_duplicate_names() -> None:
    a1 = _make("a")
    a2 = _make("a")
    with pytest.raises(PrimitiveError):
        resolve_dependencies([a1, a2])


# ---------------------------------------------------------------------------
# Integration: load the real ``core/`` primitive shipped at the repo root.
# This is the only test that touches files outside ``tmp_path``; everything
# else is fully isolated. If this fails after a content edit, the primitive
# itself (manifest, skills) is the right place to look.
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "core"
BASELINE_SKILLS: list[str] = [
    "ingest",
    "wiki-search",
    "wiki-lock",
    "wiki-lint",
    "wiki-conflict",
    "wiki-doctor",
]


def test_core_primitive_loads() -> None:
    primitive = load_primitive(CORE_DIR)
    assert primitive.name == "core"
    assert primitive.kind is PrimitiveKind.INFRASTRUCTURE
    assert primitive.version == "0.1.0"
    assert primitive.requires == []
    assert primitive.contributes_to == []


def test_core_primitive_ships_six_baseline_skills() -> None:
    for skill_name in BASELINE_SKILLS:
        skill_md = CORE_DIR / "files" / "skills" / skill_name / "SKILL.md"
        assert skill_md.exists(), f"missing {skill_md}"
        body = skill_md.read_text(encoding="utf-8")
        assert body.startswith("---\n"), f"{skill_md} must start with YAML frontmatter"
        assert f"name: {skill_name}" in body, f"{skill_md} missing matching name field"


def test_core_primitive_ships_baseline_shared_files() -> None:
    """The four files the migration plan calls out are present, so the
    renderer has something to interpolate and copy."""
    files_dir = CORE_DIR / "files"
    for name in ("AGENTS.md", "CORE.md", "frontmatter.schema.yaml", ".gitignore"):
        assert (files_dir / name).exists(), f"missing core/files/{name}"


def test_core_primitive_resolves_alone() -> None:
    """Resolution of just ``core`` returns just ``core`` — it has no deps,
    which is the precondition for the installer always-include policy."""
    primitive = load_primitive(CORE_DIR)
    assert resolve_dependencies([primitive]) == [primitive]


# ---------------------------------------------------------------------------
# Task 18 — infrastructure:research and infrastructure:research-perplexity
# ---------------------------------------------------------------------------


def test_research_primitive_loads() -> None:
    """Seed primitive: kind ``infrastructure``, no contributes_to."""

    from llm_wiki_kit.install import validate_contributions

    primitive = load_primitive(REPO_ROOT / "templates" / "infrastructure" / "research")
    assert primitive.name == "research"
    assert primitive.kind is PrimitiveKind.INFRASTRUCTURE
    assert primitive.contributes_to == []
    assert primitive.requires == []
    # Seed ships the shared file with empty managed region.
    seed = (
        REPO_ROOT
        / "templates"
        / "infrastructure"
        / "research"
        / "files"
        / "research-providers.yaml"
    )
    assert seed.is_file()
    body = seed.read_text(encoding="utf-8")
    assert "# BEGIN MANAGED: providers" in body
    assert "# END MANAGED: providers" in body
    validate_contributions(primitive, seed.parent.parent)


def test_research_perplexity_primitive_loads() -> None:
    """Provider primitive: kind ``infrastructure``, requires ``research``,
    contributes to ``research-providers.yaml:providers``."""

    from llm_wiki_kit.install import validate_contributions

    root = REPO_ROOT / "templates" / "infrastructure" / "research-perplexity"
    primitive = load_primitive(root)
    assert primitive.name == "research-perplexity"
    assert primitive.kind is PrimitiveKind.INFRASTRUCTURE
    assert primitive.requires == ["research"]
    assert primitive.contributes_to == [
        Contribution(file="research-providers.yaml", region="providers")
    ]
    snippet = root / "regions" / "research-providers.yaml.providers"
    assert snippet.is_file()
    snippet_body = snippet.read_text(encoding="utf-8")
    assert "perplexity:" in snippet_body
    assert "api_key_env: PERPLEXITY_API_KEY" in snippet_body
    validate_contributions(primitive, root)


def test_research_primitives_resolve_in_dependency_order() -> None:
    """``resolve_dependencies`` puts ``research`` before ``research-perplexity``."""

    research = load_primitive(REPO_ROOT / "templates" / "infrastructure" / "research")
    perplexity_primitive = load_primitive(
        REPO_ROOT / "templates" / "infrastructure" / "research-perplexity"
    )
    ordered = resolve_dependencies([perplexity_primitive, research])
    names = [p.name for p in ordered]
    assert names.index("research") < names.index("research-perplexity")


# ---------------------------------------------------------------------------
# Task 19 — infrastructure:research-gemini and :research-semantic-scholar
# ---------------------------------------------------------------------------


def test_research_gemini_primitive_loads() -> None:
    """Provider primitive: kind ``infrastructure``, requires ``research``,
    contributes one block to ``research-providers.yaml:providers``."""

    from llm_wiki_kit.install import validate_contributions

    root = REPO_ROOT / "templates" / "infrastructure" / "research-gemini"
    primitive = load_primitive(root)
    assert primitive.name == "research-gemini"
    assert primitive.kind is PrimitiveKind.INFRASTRUCTURE
    assert primitive.requires == ["research"]
    assert primitive.contributes_to == [
        Contribution(file="research-providers.yaml", region="providers")
    ]
    snippet = root / "regions" / "research-providers.yaml.providers"
    assert snippet.is_file()
    snippet_body = snippet.read_text(encoding="utf-8")
    assert "gemini:" in snippet_body
    assert "api_key_env: GEMINI_API_KEY" in snippet_body
    assert "model: gemini-2.5-pro" in snippet_body
    validate_contributions(primitive, root)


def test_research_semantic_scholar_primitive_loads() -> None:
    """Provider primitive: kind ``infrastructure``, requires ``research``,
    contributes one block. The api_key_env is declared but optional at
    dispatch time (keyless tier supported)."""

    from llm_wiki_kit.install import validate_contributions

    root = REPO_ROOT / "templates" / "infrastructure" / "research-semantic-scholar"
    primitive = load_primitive(root)
    assert primitive.name == "research-semantic-scholar"
    assert primitive.kind is PrimitiveKind.INFRASTRUCTURE
    assert primitive.requires == ["research"]
    assert primitive.contributes_to == [
        Contribution(file="research-providers.yaml", region="providers")
    ]
    snippet = root / "regions" / "research-providers.yaml.providers"
    assert snippet.is_file()
    snippet_body = snippet.read_text(encoding="utf-8")
    assert "semantic-scholar:" in snippet_body
    assert "api_key_env: SEMANTIC_SCHOLAR_API_KEY" in snippet_body
    assert "model: graph-v1" in snippet_body
    validate_contributions(primitive, root)


def test_recipes_do_not_include_research_gemini_or_semantic_scholar() -> None:
    """Static guard: the new Task 19 primitives are opt-in.

    Spec invariant 5: neither ``infrastructure:research-gemini`` nor
    ``infrastructure:research-semantic-scholar`` may appear in any
    shipped recipe's ``primitives:`` list. The integration suite has
    its own end-to-end version of this assertion; this is the unit-
    level static guard that catches the regression at the cheapest
    layer.
    """

    import yaml

    for name in ("family", "work-os", "personal"):
        path = REPO_ROOT / "recipes" / f"{name}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        primitives = data.get("primitives", [])
        assert "research-gemini" not in primitives, (
            f"{name}.yaml unexpectedly auto-installs 'research-gemini'"
        )
        assert "research-semantic-scholar" not in primitives, (
            f"{name}.yaml unexpectedly auto-installs 'research-semantic-scholar'"
        )


def test_research_task19_primitives_resolve_in_dependency_order() -> None:
    """Both new providers depend on ``research`` — topological order respected."""

    research = load_primitive(REPO_ROOT / "templates" / "infrastructure" / "research")
    gemini_primitive = load_primitive(
        REPO_ROOT / "templates" / "infrastructure" / "research-gemini"
    )
    ss_primitive = load_primitive(
        REPO_ROOT / "templates" / "infrastructure" / "research-semantic-scholar"
    )
    ordered = resolve_dependencies([gemini_primitive, ss_primitive, research])
    names = [p.name for p in ordered]
    assert names.index("research") < names.index("research-gemini")
    assert names.index("research") < names.index("research-semantic-scholar")
