"""Load recipes from disk and compose them against the primitive catalog.

The migration plan (RFC-0001 Task 9) names three surfaces:

* :func:`load_recipe` reads a single ``recipes/<name>.yaml`` and validates
  it against the Pydantic :class:`~llm_wiki_kit.models.Recipe` model.
  Pydantic errors flow through
  :class:`~llm_wiki_kit.errors.ValidationError`; everything else (missing
  file, malformed YAML, non-mapping top level) flows through
  :class:`~llm_wiki_kit.errors.RecipeError`.

* :func:`discover_recipes` walks ``recipes_dir/*.yaml`` (top-level only,
  alphabetical) and loads each. A bad file is fatal — silent skips would
  hide typos.

* :func:`resolve_recipe_primitives` is the composition step. It takes a
  recipe plus a flat catalog of available primitives (typically the
  union of ``primitives.discover_primitives(templates_dir)`` and the
  ``core`` primitive loaded separately), walks the transitive closure of
  the recipe's ``primitives:`` list under ``requires:``, prepends
  ``core`` if the recipe didn't already name it, and hands the closed
  set to :func:`~llm_wiki_kit.primitives.resolve_dependencies` for
  install ordering.

**Why the always-include-core policy lives here.** ``primitives.py``
deliberately stays recipe-agnostic — its
:func:`~llm_wiki_kit.primitives.resolve_dependencies` operates on a
*closed* set and does not synthesize entries. The "every vault gets
``core``" rule is a recipe-layer policy, so it belongs in this module's
closure step. If a recipe explicitly lists ``core``, we don't double-add
it; if it omits ``core``, we prepend it before closure expansion.

**Why a missing catalog primitive is a hard error.** Recipes are
authored content. A recipe naming a primitive the catalog doesn't ship
is either a typo or a missing primitive — both of which a user should
hear about at ``wiki init`` time, not at first ``wiki run``. The closure
step raises :class:`~llm_wiki_kit.errors.RecipeError` rather than
reusing :class:`~llm_wiki_kit.errors.PrimitiveError` because the failure
is a recipe-vs-catalog mismatch, not a primitive-loader concern: the
primitives all loaded fine; the recipe asked for something that doesn't
exist.

**Variables.** :attr:`~llm_wiki_kit.models.Recipe.variables` is a flat
``dict[str, str]`` of render-context defaults (``vault_name``,
``recipe_name``, recipe-specific overrides). Recipes may declare
defaults here; the installer (Task 10) composes them with CLI arguments
to produce the final render context. This module reads the field but
does not interpret it — composition is the installer's job.

**Filename ↔ recipe.name coupling.** :func:`load_recipe` does not
enforce that ``recipes/family.yaml`` declares ``name: family``. Keeping
the loader lenient leaves the field authoritative; ``wiki doctor``
(Task 12) is where authoring drift like that gets surfaced.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError as PydanticValidationError

from llm_wiki_kit.errors import RecipeError, ValidationError
from llm_wiki_kit.models import Primitive, Recipe
from llm_wiki_kit.primitives import resolve_dependencies

CORE_PRIMITIVE_NAME = "core"


def load_recipe(path: Path) -> Recipe:
    """Load and validate a single recipe file.

    ``path`` is the ``.yaml`` file itself (not a directory). The function
    does not cross-check the filename against the declared ``name``
    field — the field is authoritative.
    """

    if not path.exists():
        raise RecipeError(f"recipe file does not exist: {path}")
    if not path.is_file():
        raise RecipeError(f"recipe path is not a file: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecipeError(f"cannot read {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RecipeError(f"malformed YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RecipeError(f"{path} must contain a YAML mapping, got {type(data).__name__}")

    try:
        return Recipe.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationError(f"recipe at {path}", exc) from exc


def discover_recipes(recipes_dir: Path) -> list[Recipe]:
    """Load every ``recipes_dir/*.yaml`` (top-level only).

    Returns an empty list when ``recipes_dir`` doesn't exist (a fresh
    repo before any recipe has been authored is not an error). Files
    that aren't ``.yaml`` are skipped; subdirectories are not recursed
    into. A malformed recipe is fatal: silent skips would hide typos.
    """

    if not recipes_dir.exists():
        return []

    recipes: list[Recipe] = []
    for entry in sorted(recipes_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix != ".yaml":
            continue
        recipes.append(load_recipe(entry))

    recipes.sort(key=lambda r: r.name)
    return recipes


def resolve_recipe_primitives(
    recipe: Recipe,
    catalog: list[Primitive],
) -> list[Primitive]:
    """Expand ``recipe.primitives`` to its install-ordered closure.

    Behavior in order:

    1. Always-include-core: ``core`` is added to the requested set if the
       recipe didn't already list it. ``core`` must exist in
       ``catalog``; otherwise :class:`RecipeError` is raised.
    2. Closure expansion: starting from the requested names, walk every
       primitive's ``requires:`` (looked up in ``catalog``) until the
       set is closed under transitive ``requires:``. Any name that
       isn't in ``catalog`` raises :class:`RecipeError`.
    3. Install order: the closed primitive set is handed to
       :func:`~llm_wiki_kit.primitives.resolve_dependencies` for
       topological sort (alphabetical tiebreaker).
    """

    by_name: dict[str, Primitive] = {primitive.name: primitive for primitive in catalog}

    if CORE_PRIMITIVE_NAME not in by_name:
        raise RecipeError(
            f"recipe '{recipe.name}' cannot resolve: "
            f"primitive '{CORE_PRIMITIVE_NAME}' is missing from the catalog"
        )

    requested: list[str] = list(recipe.primitives)
    if CORE_PRIMITIVE_NAME not in requested:
        requested.insert(0, CORE_PRIMITIVE_NAME)

    closed: dict[str, Primitive] = {}
    pending: list[str] = list(requested)

    while pending:
        name = pending.pop()
        if name in closed:
            continue
        primitive = by_name.get(name)
        if primitive is None:
            raise RecipeError(
                f"recipe '{recipe.name}' references primitive '{name}' which is not in the catalog"
            )
        closed[name] = primitive
        for required in primitive.requires:
            if required not in closed:
                pending.append(required)

    return resolve_dependencies(list(closed.values()))
