"""Tests for ``llm_wiki_kit.recipes``.

The module owns three surfaces named in the migration plan (RFC-0001 Task 9):

* ``load_recipe(path)`` — read a ``recipes/<name>.yaml`` and validate via
  the Pydantic ``Recipe`` model in ``models.py``.
* ``discover_recipes(recipes_dir)`` — walk ``recipes/*.yaml`` and load
  each, sorted alphabetically.
* ``resolve_recipe_primitives(recipe, catalog)`` — expand the recipe's
  ``primitives:`` list to its transitive closure under ``requires:``,
  prepend ``core`` if the recipe didn't already name it, hand the closed
  set to ``primitives.resolve_dependencies``, and return the
  install-ordered list.

The always-include-core policy lives in this module (recipe-level
concern, per ``primitives.py``'s docstring). The filename ↔ ``recipe.name``
coupling is lenient — ``load_recipe`` does not enforce it, so ``wiki
doctor`` (Task 12) can surface that as authoring drift rather than a
hard load failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki_kit.errors import RecipeError, ValidationError, WikiError
from llm_wiki_kit.models import Primitive, Recipe
from llm_wiki_kit.recipes import (
    discover_recipes,
    load_recipe,
    resolve_recipe_primitives,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_recipe(
    path: Path,
    *,
    name: str,
    version: str = "0.1.0",
    description: str = "Test recipe.",
    primitives: list[str] | None = None,
    variables: dict[str, str] | None = None,
) -> Path:
    """Write a minimal ``recipes/<name>.yaml`` and return the file path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"name: {name}",
        f"version: {version}",
        f"description: {description}",
        "primitives:",
    ]
    for primitive_name in primitives or []:
        lines.append(f"  - {primitive_name}")
    if variables:
        lines.append("variables:")
        for key, value in variables.items():
            lines.append(f"  {key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _make_primitive(name: str, requires: list[str] | None = None) -> Primitive:
    return Primitive.model_validate(
        {
            "name": name,
            "kind": "content-type" if name != "core" else "infrastructure",
            "version": "0.1.0",
            "description": f"{name} primitive.",
            "requires": requires or [],
        }
    )


# ---------------------------------------------------------------------------
# load_recipe
# ---------------------------------------------------------------------------


def test_load_recipe_parses_minimal_file(tmp_path: Path) -> None:
    path = _write_recipe(tmp_path / "family.yaml", name="family", primitives=["core"])
    recipe = load_recipe(path)
    assert recipe.name == "family"
    assert recipe.version == "0.1.0"
    assert recipe.primitives == ["core"]
    assert recipe.variables == {}


def test_load_recipe_parses_variables(tmp_path: Path) -> None:
    path = _write_recipe(
        tmp_path / "family.yaml",
        name="family",
        primitives=["core"],
        variables={"vault_name": "household", "recipe_name": "family"},
    )
    recipe = load_recipe(path)
    assert recipe.variables == {"vault_name": "household", "recipe_name": "family"}


def test_load_recipe_does_not_enforce_filename_name_match(tmp_path: Path) -> None:
    """Filename / ``recipe.name`` coupling is left to ``wiki doctor`` —
    keeping the loader lenient means the field stays authoritative."""
    path = _write_recipe(tmp_path / "household.yaml", name="family", primitives=["core"])
    recipe = load_recipe(path)
    assert recipe.name == "family"


def test_load_recipe_raises_recipeerror_when_file_missing(tmp_path: Path) -> None:
    with pytest.raises(RecipeError) as excinfo:
        load_recipe(tmp_path / "missing.yaml")
    assert isinstance(excinfo.value, WikiError)
    assert "missing.yaml" in str(excinfo.value)


def test_load_recipe_raises_recipeerror_on_malformed_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: ok\nversion: : invalid\n", encoding="utf-8")
    with pytest.raises(RecipeError):
        load_recipe(bad)


def test_load_recipe_raises_recipeerror_when_yaml_is_not_mapping(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(RecipeError):
        load_recipe(bad)


def test_load_recipe_raises_validationerror_on_bad_schema(tmp_path: Path) -> None:
    bad = _write_recipe(tmp_path / "bad.yaml", name="Bad Name", primitives=["core"])
    with pytest.raises(ValidationError):
        load_recipe(bad)


def test_load_recipe_raises_validationerror_on_missing_field(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: family\nversion: 0.1.0\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_recipe(bad)


def test_load_recipe_raises_validationerror_on_unknown_field(tmp_path: Path) -> None:
    """``extra=forbid`` on ``_StrictModel`` catches typos in hand-edited YAML."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: family\nversion: 0.1.0\ndescription: x\nprimitives: []\nextends: other\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_recipe(bad)


# ---------------------------------------------------------------------------
# discover_recipes
# ---------------------------------------------------------------------------


def test_discover_recipes_walks_yaml_files(tmp_path: Path) -> None:
    _write_recipe(tmp_path / "family.yaml", name="family", primitives=["core"])
    _write_recipe(tmp_path / "personal.yaml", name="personal", primitives=["core"])
    _write_recipe(tmp_path / "work-os.yaml", name="work-os", primitives=["core"])
    found = discover_recipes(tmp_path)
    assert [r.name for r in found] == ["family", "personal", "work-os"]


def test_discover_recipes_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    assert discover_recipes(tmp_path / "does-not-exist") == []


def test_discover_recipes_returns_empty_when_dir_empty(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert discover_recipes(tmp_path / "empty") == []


def test_discover_recipes_ignores_non_yaml_files(tmp_path: Path) -> None:
    _write_recipe(tmp_path / "family.yaml", name="family", primitives=["core"])
    (tmp_path / "README.md").write_text("not a recipe\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("scratch\n", encoding="utf-8")
    found = discover_recipes(tmp_path)
    assert [r.name for r in found] == ["family"]


def test_discover_recipes_sorts_alphabetically(tmp_path: Path) -> None:
    _write_recipe(tmp_path / "zeta.yaml", name="zeta", primitives=["core"])
    _write_recipe(tmp_path / "alpha.yaml", name="alpha", primitives=["core"])
    _write_recipe(tmp_path / "mu.yaml", name="mu", primitives=["core"])
    found = discover_recipes(tmp_path)
    assert [r.name for r in found] == ["alpha", "mu", "zeta"]


def test_discover_recipes_propagates_load_errors(tmp_path: Path) -> None:
    _write_recipe(tmp_path / "family.yaml", name="family", primitives=["core"])
    (tmp_path / "bad.yaml").write_text("name: : bad\n", encoding="utf-8")
    with pytest.raises(WikiError):
        discover_recipes(tmp_path)


def test_discover_recipes_skips_subdirectories(tmp_path: Path) -> None:
    _write_recipe(tmp_path / "family.yaml", name="family", primitives=["core"])
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "experimental.yaml").write_text(
        "name: experimental\nversion: 0.1.0\ndescription: x\nprimitives:\n  - core\n",
        encoding="utf-8",
    )
    found = discover_recipes(tmp_path)
    assert [r.name for r in found] == ["family"]


# ---------------------------------------------------------------------------
# resolve_recipe_primitives
# ---------------------------------------------------------------------------


def test_resolve_recipe_primitives_core_only(tmp_path: Path) -> None:
    recipe = Recipe.model_validate(
        {"name": "family", "version": "0.1.0", "description": "x", "primitives": ["core"]}
    )
    catalog = [_make_primitive("core")]
    ordered = resolve_recipe_primitives(recipe, catalog)
    assert [p.name for p in ordered] == ["core"]


def test_resolve_recipe_primitives_prepends_core_when_omitted() -> None:
    """Recipe-level always-include-core policy: a recipe that omits
    ``core`` still gets it as the first installed primitive."""
    recipe = Recipe.model_validate(
        {"name": "r", "version": "0.1.0", "description": "x", "primitives": ["people"]}
    )
    catalog = [_make_primitive("core"), _make_primitive("people", requires=["core"])]
    ordered = resolve_recipe_primitives(recipe, catalog)
    assert [p.name for p in ordered] == ["core", "people"]


def test_resolve_recipe_primitives_does_not_double_add_core() -> None:
    """An explicit ``core`` reference shouldn't produce a duplicate-name
    error from the underlying ``resolve_dependencies``."""
    recipe = Recipe.model_validate(
        {
            "name": "r",
            "version": "0.1.0",
            "description": "x",
            "primitives": ["core", "people"],
        }
    )
    catalog = [_make_primitive("core"), _make_primitive("people", requires=["core"])]
    ordered = resolve_recipe_primitives(recipe, catalog)
    assert [p.name for p in ordered] == ["core", "people"]


def test_resolve_recipe_primitives_expands_transitive_closure() -> None:
    """If a recipe names ``meeting`` and ``meeting`` requires ``people``
    (which in turn requires ``core``), all three should install — even
    though only ``meeting`` is listed."""
    recipe = Recipe.model_validate(
        {"name": "r", "version": "0.1.0", "description": "x", "primitives": ["meeting"]}
    )
    catalog = [
        _make_primitive("core"),
        _make_primitive("people", requires=["core"]),
        _make_primitive("meeting", requires=["people"]),
    ]
    ordered = resolve_recipe_primitives(recipe, catalog)
    assert [p.name for p in ordered] == ["core", "people", "meeting"]


def test_resolve_recipe_primitives_returns_install_order() -> None:
    """Output is install-ordered (deps before dependents)."""
    recipe = Recipe.model_validate(
        {
            "name": "r",
            "version": "0.1.0",
            "description": "x",
            "primitives": ["meeting", "people"],
        }
    )
    catalog = [
        _make_primitive("core"),
        _make_primitive("people", requires=["core"]),
        _make_primitive("meeting", requires=["core", "people"]),
    ]
    ordered = resolve_recipe_primitives(recipe, catalog)
    names = [p.name for p in ordered]
    assert names.index("core") < names.index("people") < names.index("meeting")


def test_resolve_recipe_primitives_omits_uninstalled_primitives() -> None:
    """A catalog primitive the recipe didn't ask for (directly or
    transitively) doesn't leak into the install set."""
    recipe = Recipe.model_validate(
        {"name": "r", "version": "0.1.0", "description": "x", "primitives": ["people"]}
    )
    catalog = [
        _make_primitive("core"),
        _make_primitive("people", requires=["core"]),
        _make_primitive("unused", requires=["core"]),
    ]
    ordered = resolve_recipe_primitives(recipe, catalog)
    assert [p.name for p in ordered] == ["core", "people"]
    assert "unused" not in {p.name for p in ordered}


def test_resolve_recipe_primitives_raises_when_recipe_names_unknown_primitive() -> None:
    """Recipes are authored content — naming a primitive the catalog
    lacks is a bug, not a warning. Hard-error so the failure surfaces at
    ``wiki init`` rather than waiting for runtime."""
    recipe = Recipe.model_validate(
        {"name": "r", "version": "0.1.0", "description": "x", "primitives": ["ghost"]}
    )
    catalog = [_make_primitive("core")]
    with pytest.raises(RecipeError) as excinfo:
        resolve_recipe_primitives(recipe, catalog)
    assert "ghost" in str(excinfo.value)
    assert isinstance(excinfo.value, WikiError)


def test_resolve_recipe_primitives_raises_when_transitive_requires_missing() -> None:
    """A primitive in the catalog that requires another primitive
    *not* in the catalog is also a hard error during closure expansion."""
    recipe = Recipe.model_validate(
        {"name": "r", "version": "0.1.0", "description": "x", "primitives": ["people"]}
    )
    catalog = [
        _make_primitive("core"),
        _make_primitive("people", requires=["nonexistent"]),
    ]
    with pytest.raises(RecipeError) as excinfo:
        resolve_recipe_primitives(recipe, catalog)
    assert "nonexistent" in str(excinfo.value)


def test_resolve_recipe_primitives_empty_recipe_still_gets_core() -> None:
    """A recipe with an empty ``primitives:`` list is degenerate but the
    always-include-core policy applies anyway — render still emits a
    legal vault."""
    recipe = Recipe.model_validate(
        {"name": "r", "version": "0.1.0", "description": "x", "primitives": []}
    )
    catalog = [_make_primitive("core")]
    ordered = resolve_recipe_primitives(recipe, catalog)
    assert [p.name for p in ordered] == ["core"]


def test_resolve_recipe_primitives_raises_when_core_missing_from_catalog() -> None:
    """``core`` is required for the always-include policy; if it's not
    in the catalog the recipe cannot resolve."""
    recipe = Recipe.model_validate(
        {"name": "r", "version": "0.1.0", "description": "x", "primitives": []}
    )
    with pytest.raises(RecipeError) as excinfo:
        resolve_recipe_primitives(recipe, [])
    assert "core" in str(excinfo.value)


def test_resolve_recipe_primitives_is_deterministic() -> None:
    """Re-running closure expansion over the same recipe + catalog
    produces the same install order — drift detection in CI fixtures
    depends on this."""
    recipe = Recipe.model_validate(
        {
            "name": "r",
            "version": "0.1.0",
            "description": "x",
            "primitives": ["meeting", "people"],
        }
    )
    catalog = [
        _make_primitive("core"),
        _make_primitive("people", requires=["core"]),
        _make_primitive("meeting", requires=["people"]),
    ]
    first = [p.name for p in resolve_recipe_primitives(recipe, catalog)]
    second = [p.name for p in resolve_recipe_primitives(recipe, list(reversed(catalog)))]
    assert first == second


# ---------------------------------------------------------------------------
# Integration: load the real ``recipes/`` files against the real ``core/``
# primitive shipped at the repo root. These are the only tests that touch
# files outside ``tmp_path``. Until Tasks 11+ ship more primitives, every
# recipe resolves to just ``core`` — the spec's "minimal Task 9 recipes"
# choice.
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = REPO_ROOT / "recipes"
CORE_DIR = REPO_ROOT / "core"
TEMPLATES_DIR = REPO_ROOT / "templates"
INITIAL_RECIPES: list[str] = ["family", "personal", "work-os"]


def test_initial_recipes_present() -> None:
    for name in INITIAL_RECIPES:
        assert (RECIPES_DIR / f"{name}.yaml").exists(), f"missing recipes/{name}.yaml"


def test_initial_recipes_load() -> None:
    for name in INITIAL_RECIPES:
        recipe = load_recipe(RECIPES_DIR / f"{name}.yaml")
        assert recipe.name == name


def test_discover_recipes_finds_three_initial_recipes() -> None:
    found = discover_recipes(RECIPES_DIR)
    assert [r.name for r in found] == INITIAL_RECIPES


def test_family_recipe_resolves_against_live_catalog() -> None:
    """The ``family`` recipe was expanded in Task 13. It must resolve
    against the full live catalog (core plus every primitive under
    ``templates/``) to a closure that includes every leaf primitive
    listed in the recipe plus the transitive ``requires:`` chain."""

    from llm_wiki_kit.primitives import discover_primitives, load_primitive

    catalog = [load_primitive(CORE_DIR), *discover_primitives(TEMPLATES_DIR)]
    recipe = load_recipe(RECIPES_DIR / "family.yaml")
    ordered = resolve_recipe_primitives(recipe, catalog)
    ordered_names = {p.name for p in ordered}

    # Every leaf primitive the recipe lists is in the closure, plus
    # ``core`` (always installed) and ``people`` (pulled in by both
    # ``meeting`` and ``trip-doc``).
    assert set(recipe.primitives).issubset(ordered_names)
    assert "core" in ordered_names
    assert "people" in ordered_names


def test_work_os_recipe_resolves_against_live_catalog() -> None:
    """The ``work-os`` recipe was expanded in Task 14 and must resolve
    against the full live catalog (core plus every primitive under
    ``templates/``) to its declared closure plus the transitive
    ``requires:`` of those primitives (``people`` and ``meeting``)."""

    from llm_wiki_kit.primitives import discover_primitives, load_primitive

    catalog = [load_primitive(CORE_DIR), *discover_primitives(TEMPLATES_DIR)]
    recipe = load_recipe(RECIPES_DIR / "work-os.yaml")
    ordered = resolve_recipe_primitives(recipe, catalog)

    expected = {
        "action-item-rollup",
        "core",
        "customer-feedback",
        "customers",
        "decision",
        "domains",
        "interview",
        "meeting",
        "onboarding-pack",
        "people",
        "projects",
        "renewal-reminders",
        "stakeholder-map-refresh",
        "stakeholder-update",
        "status-synthesis",
        "vendor-contract",
    }
    assert {p.name for p in ordered} == expected


def test_personal_recipe_resolves_against_live_catalog() -> None:
    """The ``personal`` recipe was expanded in Task 15. The closure is
    a deliberate composition of Task-11 / Task-13 / Task-14 primitives
    plus the new ``identity`` ontology — see the comment block in
    ``recipes/personal.yaml`` for the rationale."""

    from llm_wiki_kit.primitives import discover_primitives, load_primitive

    catalog = [load_primitive(CORE_DIR), *discover_primitives(TEMPLATES_DIR)]
    recipe = load_recipe(RECIPES_DIR / "personal.yaml")
    ordered = resolve_recipe_primitives(recipe, catalog)

    expected = {
        "action-item",
        "core",
        "decision",
        "follow-up-tracker",
        "food",
        "identity",
        "meal-planning",
        "meeting",
        "people",
        "recipe",
        "trip-doc",
        "trip-prep",
        "trips",
        "weekly-digest",
    }
    assert {p.name for p in ordered} == expected
