---
name: action-item-rollup
description: "Roll up open action items across meeting follow-ups, stakeholder-update asks, customer-feedback follow-ups, and interview follow-ups into one owner-grouped digest. Load when the user asks 'what does <owner> have on their plate?', when `wiki run action-item-rollup` invokes you, or on a scheduled weekly sweep. Writes one page to `outputs/action-items/<window_end>.md`; re-running the same window overwrites the same page."
license: MIT
---

# action-item-rollup

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run action-item-rollup`, and `wiki run` is a stub in
> v2.0.0.dev: it prints `wiki run: not yet implemented (v2 migration
> in progress, see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Walk the last N days of follow-up-bearing content, extract every
named-owner action item, and write one durable rollup page grouped by
owner.

## When to load

- The user asks "what's open?", "what does alice have on her plate?",
  "any open asks I should chase?", etc.
- `wiki run action-item-rollup` runs you with the contract from
  `contract.yaml`.
- A scheduled weekly invocation.

## Inputs

From the operation contract:

- **`window_days`** — number of past days to scan. Default 30. Older
  items are excluded unless explicitly named in a *recent* page; the
  rollup is not a permanent open-items tracker.
- **`sources`** — content-types to include. v0.1:
  `[meeting, stakeholder-update, customer-feedback, interview]`.

## Procedure

1. **Find the input pages.** For each content-type in `sources`,
   walk its directory and filter by the type's date field
   (`meeting_date`, `update_date`, `feedback_date`, `interview_date`)
   inside the window. Use the `wiki-search` skill.
2. **Extract follow-up-like fields.** Per source type:
   - `meeting` → `meeting_follow_ups`.
   - `stakeholder-update` → `update_asks`.
   - `customer-feedback` → `feedback_follow_ups`.
   - `interview` → `interview_follow_ups`.
3. **Parse each item.** The convention across types is
   `@owner: do the thing by YYYY-MM-DD`. Parse `@owner`, the action
   text, and the due date. Be tolerant of variants:
   - Missing `@owner` → bucket under `unassigned`.
   - Missing due date → bucket under `no-due-date`.
   - Multiple owners (`@alice, @bob`) → duplicate the item under each.
4. **Group by owner.** One section per `@owner`, sorted by due date
   ascending (overdue first, then upcoming).
5. **Compose the rollup page.** One page at
   `outputs/action-items/<window_end>.md` with sections:
   - **Overdue** — items with a past due date, grouped by owner.
   - **Due this week** — items due in the next 7 days.
   - **Upcoming** — due dates beyond 7 days.
   - **No due date** — items without a parseable date.
   - **Unassigned** — items without a named owner.
   Each item lists the action text, the due date, and a wikilink to
   the source page.

## Frontmatter for the rollup page

```yaml
type: action-rollup
status: active
provenance: synthesized
created: <today>
modified: <today>
tags: [action-items, <window_end>]
rollup_window_days: <window>
rollup_window_end: <window_end>
```

The `action-rollup` type may not yet exist in
`frontmatter.schema.yaml`'s managed `types` region — that's fine for
v0.1.

## Owner normalization

Tolerate common variants: `@alice`, `@alice-park`, `@Alice Park`. Map
to the canonical person page if one exists in `wiki/people/`; otherwise
use the source's spelling and note the unresolved name in a "Names not
matched to people pages" appendix on the rollup page.

## When an item appears in multiple sources

A follow-up named in a meeting and re-asked in a stakeholder-update
is the *same* action item, not two. De-duplicate when the action text
is identical or near-identical; cite both source pages in the rollup
entry. If you're unsure whether two items are the same, list them
separately — false splits are recoverable; false merges aren't.

## When the window is empty

Produce a minimal page noting "no in-scope action items for
<window_end>." A run with no items is itself journaled and the
absence of items is information.

## After writing

- Append a one-line summary to the running activity log.
- If an owner has more than 5 overdue items, surface that on the page
  as a "Heads up" callout. The rollup is meant to *prompt* action, not
  just enumerate it.
