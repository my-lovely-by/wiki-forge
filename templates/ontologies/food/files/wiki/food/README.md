# food/

Recipes the household cooks, dietary notes per person, and the meal plans
that pull from both. Pages here are linked from `wiki/people/` (allergens,
preferences) and from operation outputs under `outputs/`.

## Conventions

- **One page per recipe.** Filename is the recipe in kebab-case:
  `sheet-pan-chicken-tacos.md`. Group long-lived favourites under
  `family-favorites/` and quick weeknight meals under `weeknight/`; the
  `recipe` content-type primitive owns the page schema.
- **Dietary notes.** `dietary-notes.md` lists per-person allergens and
  preferences. Ingester skills cross-reference it when flagging recipes;
  keep it short and current.
- **Meal plans.** `meal-plans/YYYY-MM-DD-week.md` (Sunday or Monday of
  the week). Owned by the `meal-planning` operation.

## What goes on a recipe page

- Title, source attribution, servings, prep/cook time, dietary tags.
- Ingredients as a bulleted list, instructions numbered.
- Notes section for family modifications — the recipe page becomes
  more useful over time.

## Created by other primitives

- `recipe` ingester writes new pages from URLs, photos, or pasted text.
- `meal-planning` operation reads the library and `dietary-notes.md`,
  writes one plan per week.
