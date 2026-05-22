---
name: status-synthesis
description: "Produce a cross-project status snapshot for an ISO-week window by walking stakeholder-updates, decisions, and customer-feedback. Load when the user asks 'what's the state of the world this week?', when `wiki run status-synthesis` invokes you, or on a scheduled weekly sweep. Writes one page to `outputs/status/<window>.md`; re-running the same window overwrites. Distinct from `weekly-digest`, which is meeting-centric — `status-synthesis` is project / customer / decision-centric."
license: MIT
---

# status-synthesis

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run status-synthesis`, and `wiki run` is a stub in
> v2.0.0.dev: it prints `wiki run: not yet implemented (v2 migration
> in progress, see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Walk the week's stakeholder-updates, decisions, and customer-feedback;
pull out what mattered; write one durable status page citing the
sources. The audience is the team itself (and any cross-functional
peer who wants a single read on "where are we?"), not external
stakeholders.

## When to load

- The user asks "what's the state of the world?", "any patterns this
  week?", "what's at risk?"
- `wiki run status-synthesis` runs you with the contract from
  `contract.yaml`.
- A scheduled weekly invocation (often Friday afternoon).

## When *not* to load

- The user wants the meeting-centric summary →
  `weekly-digest` is the right operation.
- The user wants a single-owner action list →
  `action-item-rollup`.
- The user wants the per-project audience map →
  `stakeholder-map-refresh`.

## Inputs

From the operation contract:

- **`window`** — ISO week (e.g. `2026-W20`). Defaults to the most
  recent *complete* week.
- **`sources`** — content-types to include. v0.1:
  `[stakeholder-update, decision, customer-feedback]`.

## Procedure

1. **Find the input pages.** For each content-type, filter by the
   type's date field (`update_date`, `decision_date`,
   `feedback_date`) inside the window. Use the `wiki-search` skill.
2. **Group stakeholder-updates by project.** For each project that
   has an in-window update, record:
   - `update_status` (colour) — did it change vs. the previous
     in-vault update for that project?
   - The headline highlight (first item of `update_highlights`).
   - Active risks (`update_risks`).
   - Open asks (`update_asks` count).
3. **Group decisions by status.** Bucket into:
   - **Decided this week** — `decision_status: accepted` with
     `decision_date` in window.
   - **Proposed this week** — `decision_status: proposed` with
     `decision_date` in window.
   - **Superseded this week** — `decision_status: superseded`,
     surface the new decision that replaced it.
4. **Aggregate feedback themes.** Across in-window
   `customer-feedback` pages, count `feedback_themes` occurrences.
   Surface themes that appeared in ≥2 distinct customers — those
   are the cross-account patterns worth team attention. Single-
   customer themes are still on the per-customer page; the synthesis
   layer is for patterns.
5. **Compose the synthesis page** at `outputs/status/<window>.md`
   with sections:
   - **Headlines.** Up to 5 one-line items the user could literally
     paste into a stand-up.
   - **Project status board.** Table: project | status | trend
     vs. previous | open asks | risks. One row per project with an
     in-window update.
   - **Decisions.** "Decided this week", "Proposed this week",
     "Superseded this week."
   - **Customer themes.** Cross-account themes from feedback, each
     with its source wikilinks.
   - **What's missing.** Projects with *no* update in the window
     (named on the project's page but not present in
     stakeholder-updates). The point of this section is to flag
     dark corners, not to shame them.

## Frontmatter for the synthesis page

```yaml
type: status-synthesis
status: active
provenance: synthesized
created: <today>
modified: <today>
tags: [status-synthesis, <window>]
synthesis_window: <window>
```

The `status-synthesis` type may not yet exist in
`frontmatter.schema.yaml`'s managed `types` region — that's fine for
v0.1.

## Detecting trend vs. previous

For each project that had an update in the window:

1. Find the most-recent in-vault update for the same project *before*
   the window.
2. Compare `update_status` colours. Three outcomes:
   - **Improved** — e.g. `red → yellow`, `yellow → green`.
   - **Held** — same colour.
   - **Worsened** — e.g. `green → yellow`, `yellow → red`.
3. **Worsened** is the actionable signal; surface those at the top of
   the project status board with a clear marker.

## When the window is empty

Produce a minimal page noting "no in-scope activity for <window>."
Empty weeks are themselves a signal — they often coincide with
holidays or merge freezes, both worth recording.

## Avoid restating per-page detail

The synthesis is a *summary layer*. Don't re-paragraph the
content already on each source page. The synthesis page's job is to
make the pattern visible; the wikilinks carry the reader back to
the detail.

## After writing

- Append a one-line summary to the running activity log.
- If any project worsened in the window, surface that in your
  post-run output, not just on the page.
- If three or more decisions are still `proposed` after a week,
  suggest the user revisit them — `proposed` is a state that should
  resolve quickly.
