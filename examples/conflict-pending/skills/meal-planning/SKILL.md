---
name: meal-planning
description: "Produce a weekly family meal plan with shopping list, reading the recipe library under `wiki/food/`, dietary notes, last week's plan, and family calendar context if available. Load when the user asks for a weekly plan, when `wiki run meal-planning` invokes you, or on a scheduled Sunday sweep. Writes one page to `outputs/meal-plans/<window>.md`; idempotent for a given window. For ad-hoc \"what's for dinner tonight?\" use a recipe recommendation skill instead — this operation is the weekly cadence."
license: MIT
---

# meal-planning

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run meal-planning`, and `wiki run` is a stub in v2.0.0.dev:
> it prints `wiki run: not yet implemented (v2 migration in progress,
> see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Walk the recipe library, honour the household's dietary constraints,
and write one weekly plan with shopping list. This is the gateway
operation that keeps the food side of the vault alive — every meal-plan
run is a chance for the household to surface new recipes worth
capturing.

## When to load

- The user asks for "the meal plan", "this week's meals", "what are we
  cooking this week".
- `wiki run meal-planning` runs you with the contract from
  `contract.yaml`.
- A scheduled invocation (typically Sunday afternoon).

If the user wants tonight's dinner specifically, that's a different
problem (a one-shot recommendation, not a weekly plan). Surface the
distinction.

## Inputs

From the operation contract:

- **`window`** — ISO week. Defaults to the *next upcoming* week
  (Monday → Sunday in the vault's timezone). Past weeks can be
  reproduced for archive purposes.
- **`theme`** — optional hint: "easy week — kids' sports", "trying a
  new cuisine", "Sarah away Mon-Wed". Bias the plan accordingly.
- **`household`** — optional subset (e.g. just the parents for a week
  the kids are at camp). Defaults to everyone listed in
  `wiki/food/dietary-notes.md`.

You also read:

- All recipe pages under `wiki/food/` — typically `wiki/food/` itself
  plus any subfolders (`family-favorites/`, `weeknight/`).
- `wiki/food/dietary-notes.md` — per-person allergens and preferences.
- The most recent plan under `outputs/meal-plans/` — for repetition
  avoidance and leftover continuity.
- A pantry inventory page if the household keeps one.

## Procedure

1. **Filter by hard constraints.** Drop recipes that violate any
   person's dietary restrictions for the week (no gluten if Jake's
   eating; no shellfish if Sarah is).
2. **Spread cuisines.** Don't pick Italian three nights running.
3. **Match prep time to context.** ≤30 min on busy nights, more
   ambitious on flexible ones. If `theme` mentions tight evenings, bias
   harder.
4. **Avoid recent repetition.** Skip recipes cooked in the last two
   weeks unless they're top family favourites.
5. **One stretch recipe per week.** Ideally Saturday — something the
   household hasn't made before, or a seasonal fit.
6. **Aggregate the shopping list.** Combine ingredients across the
   week, minus pantry staples.

## Output

Write `outputs/meal-plans/<window>.md` (e.g.
`outputs/meal-plans/2026-W21.md`). Idempotent — re-running for the same
window overwrites or sidecars per the kit's `safe_write` flow.

Frontmatter:

```yaml
type: meal-plan
status: active
provenance: synthesized
created: <today>
modified: <today>
tags: [meal-plan, <window>]
meal_plan_window: <window>
```

Sections:

- **Synopsis** — 2-3 sentences: count of planned meals, average prep,
  dietary fit.
- **Schedule** — day-by-day: recipe wikilink, prep time, one-line
  "why" (matches calendar, uses leftover, stretch).
- **Shopping list** — aggregated ingredients with pantry staples
  subtracted.
- **Notes for next week's planner** — what to capture (new recipes
  tried, family reactions, recipes that didn't fit).

The `meal-plan` type may not yet exist in `frontmatter.schema.yaml`'s
managed `types` region. That's fine for v0.1; `wiki-lint` flags it as a
known gap. A later content-type primitive can register the type.

## When you can't produce a meaningful plan

- **Recipe library too thin.** Fewer than ~10 recipes makes weekly
  rotation impossible — produce the plan but flag the gap and suggest
  `wiki ingest <recipe-url>` for capture.
- **Dietary notes missing or stale.** Refuse and ask for a refresh;
  silently allergen-violating a meal plan is one of the worst possible
  errors.
- **Every candidate cooked in the last two weeks.** Relax the rule and
  flag, or ask the user.
