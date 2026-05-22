"""Unit tests for the pure helpers behind ``wiki init``.

The full end-to-end behaviour lives under
``tests/integration/test_wiki_init.py``; this file only covers the
context-builder and the source-dir lookup, both of which are pure
functions worth pinning independently of any temp-dir fixture.
"""

from __future__ import annotations

from pathlib import Path

from llm_wiki_kit.cli import _build_context, _kit_paths, _primitive_source_dir
from llm_wiki_kit.models import Primitive, PrimitiveKind, Recipe


def _recipe(name: str = "family", variables: dict[str, str] | None = None) -> Recipe:
    return Recipe(
        name=name,
        version="0.1.0",
        description="Test recipe.",
        primitives=["core"],
        variables=variables or {},
    )


def _primitive(name: str, kind: PrimitiveKind = PrimitiveKind.INFRASTRUCTURE) -> Primitive:
    return Primitive(
        name=name,
        kind=kind,
        version="0.1.0",
        description="Test primitive.",
    )


def test_build_context_seeds_vault_and_recipe_name() -> None:
    context = _build_context(_recipe(name="family"), vault_name="my-vault")
    assert context["vault_name"] == "my-vault"
    assert context["recipe_name"] == "family"


def test_build_context_cli_values_override_recipe_variables() -> None:
    # A recipe author cannot redefine the kit-owned ``vault_name`` /
    # ``recipe_name`` keys; CLI-derived values always win.
    recipe = _recipe(
        name="family",
        variables={"vault_name": "wrong", "recipe_name": "wrong", "tagline": "households"},
    )
    context = _build_context(recipe, vault_name="actual-vault")
    assert context["vault_name"] == "actual-vault"
    assert context["recipe_name"] == "family"
    # Domain-specific variables flow through.
    assert context["tagline"] == "households"


def test_primitive_source_dir_routes_core_to_core_dir(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    templates_dir = tmp_path / "templates"
    assert _primitive_source_dir(_primitive("core"), core_dir, templates_dir) == core_dir


def test_primitive_source_dir_routes_templated_kinds(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    templates_dir = tmp_path / "templates"
    cases = {
        PrimitiveKind.ONTOLOGY: "ontologies",
        PrimitiveKind.CONTENT_TYPE: "content-types",
        PrimitiveKind.OPERATION: "operations",
        PrimitiveKind.INFRASTRUCTURE: "infrastructure",
    }
    for kind, plural in cases.items():
        prim = _primitive("meeting", kind=kind)
        assert (
            _primitive_source_dir(prim, core_dir, templates_dir)
            == templates_dir / plural / "meeting"
        )


def test_kit_paths_resolves_repo_root_assets() -> None:
    recipes_dir, core_dir, templates_dir = _kit_paths()
    # The editable-install layout puts these next to ``llm_wiki_kit/``.
    assert recipes_dir.name == "recipes"
    assert core_dir.name == "core"
    assert templates_dir.name == "templates"
    # We don't assert ``exists()`` for ``templates`` — that dir lands in a
    # later task and may not yet exist at test time.
    assert recipes_dir.exists()
    assert core_dir.exists()
