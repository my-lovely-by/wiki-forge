---
name: ingest-recipe
description: "Ingest a recipe from a URL, photo, scan, or pasted text into a structured page under `wiki/food/`. Load from the `ingest` skill when content-type routing identifies the source as a recipe (food-blog or recipe-site URL, photo of a card, ingredient+instructions shape in pasted text). Output is one page using the `_templates/recipe.md` schema, cross-linked to `wiki/food/dietary-notes.md` for per-person allergen flags. Pairs with the `meal-planning` operation, which reads the library."
license: MIT
---

# ingest-recipe

Capture a recipe and produce one clean, durable wiki page using the
recipe schema. The user gives you a URL, a photo of a card, or pasted
text; you produce one page under `wiki/food/` plus any allergen flags
the household will need at meal-plan time.

## When you're loaded

The `ingest` skill routes here after it has classified the source as a
recipe. You can also be loaded directly when the user says "save this
recipe", "ingest this recipe", or shares a food-blog link.

If the source could be a recipe *or* a casual food note ("we made pasta
last night"), confirm before assuming. A recipe has ingredients and
instructions, even if loosely structured.

## Inputs you'll see

- A URL to a food blog, NYT Cooking, Bon Appétit, AllRecipes, Serious
  Eats, etc. Most of these embed `schema.org/Recipe` microdata — prefer
  the structured data over heuristic parsing of the prose.
- A photo or scan of a handwritten or printed card. OCR is lossy; if the
  output is garbled, surface the raw text and confirm with the user
  before saving.
- Pasted text — usually a recipe shared in chat, an email, or a DM.

For each, you need to extract:

- **`recipe_servings`** — number of servings (string, since some recipes
  read "4-6"). Leave empty if absent.
- **`recipe_prep_time`** / **`recipe_cook_time`** — minutes as a short
  string (e.g. "15 min"). Helpful for meal-planning fit.
- **`recipe_dietary`** — tags the household cares about (e.g.
  `gluten-free`, `vegetarian`, `dairy-free`). Detect from ingredients
  when possible.
- **`source`** — URL, person who shared it, or `family-favorite` if the
  recipe has no external source.

## Page shape

Render from `_templates/recipe.md`. Filename is the recipe's title in
kebab-case: `sheet-pan-chicken-tacos.md`. Place under `wiki/food/` —
the household may group long-lived favourites under a `family-favorites/`
subfolder; honour that convention if it's already in use.

## Allergen and dietary flags

Read `wiki/food/dietary-notes.md` (if it exists) and check ingredients
against each person's restrictions. Flag matches as callouts in the
recipe's **Notes** section, e.g.:

```markdown
> [!warning] Contains gluten — @jake-doe
> Soy sauce in the marinade is wheat-derived. Substitute tamari or
> coconut aminos.
```

If `dietary-notes.md` doesn't exist yet, skip the cross-check and add
a one-line TODO at the top of the page suggesting the user create it.

## Duplicate detection

Search `wiki/food/` for an existing page with the same title or source
URL. If you find one, surface both pages and ask whether to overwrite,
version, or merge — do not silently clobber. The kit's `safe_write`
also catches drift, but a duplicate caught up-front is cleaner than a
sidecar after the fact.

## When you can't extract cleanly

Recipe sites without schema microdata, or photos with poor OCR, often
yield partial data. Produce the page with what you can extract honestly,
mark `provenance: mixed`, and leave a TODO at the top of the page asking
the user to fill the gaps. Better an honestly thin recipe page than a
hallucinated thick one.

## After writing

- If `wiki/food/dietary-notes.md` exists, append a one-line entry under
  the recipe's name listing any new dietary tags you detected.
- Append a one-line summary to the wiki's running activity so
  `wiki-lint` picks it up on its next run.
- Remind the user that `wiki run meal-planning` will pick this recipe
  up on the next weekly plan if it fits the week's constraints.
