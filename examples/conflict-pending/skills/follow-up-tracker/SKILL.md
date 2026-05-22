---
name: follow-up-tracker
description: "Scan the vault for follow-up callouts (`> [!important] Follow-up due by YYYY-MM-DD`) and standalone `action-item` pages, then surface what's overdue and what's due in the next 60 days, grouped by owner and domain. Load when the user says \"what follow-ups are due?\", on a weekly sweep, or via `wiki run follow-up-tracker`. Writes one report page per run; pairs with `medical-summary` (which produces visit-specific summaries) and the content-type ingesters that emit the callouts in the first place."
license: MIT
---

# follow-up-tracker

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run follow-up-tracker`, and `wiki run` is a stub in
> v2.0.0.dev: it prints `wiki run: not yet implemented (v2 migration
> in progress, see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Reminding operation. Find every follow-up across the vault — inline
callouts on meeting / medical / receipt pages, plus standalone
`action-item` pages — and produce one report grouped by urgency and
owner. Keeps the household ahead of recheck windows, vehicle service,
home maintenance, and miscellaneous chores.

## When to load

- The user asks "what's due?", "what follow-ups are coming?", "anything
  overdue?".
- `wiki run follow-up-tracker` runs you with the contract.
- A weekly scheduled sweep (typically Sunday morning).
- After an ingester logs a new follow-up — the user may want to see it
  in context.

## Inputs

From the operation contract:

- **`window_days`** — how far ahead to look. Defaults to 60. Overdue
  items are always surfaced regardless of window.
- **`filter`** — optional restriction. Substring matches against source
  path or `action_item_owner` (e.g. `"medical"`, `"jake-doe"`,
  `"vehicle"`).
- **`scope`** — optional subset of content-types to scan.

You also read:

- Every page under `wiki/` for inline `> [!important] Follow-up due by
  YYYY-MM-DD` callouts (use `wiki-search` with `--callout` or grep —
  do not hand-walk the tree).
- Every page under `wiki/actions/` for standalone `action-item` pages
  with `action_item_state` in `{open, in-progress, blocked}`.
- `wiki/medical/medications.md` for refill due dates.
- `wiki/vendors/` and any vehicle / appliance pages for service
  intervals.

## Procedure

1. **Collect every callout.** For each inline callout, capture the
   source page (wikilink), the due date, the surrounding paragraph (so
   the report can say what the follow-up is *about*), and the implied
   owner (from `meeting_attendees`, the medical record's patient, etc.).
2. **Collect every standalone action-item.** Read `action_item_owner`,
   `action_item_due`, `action_item_state`, and `action_item_source`.
3. **Compute time-based dues.** For receipts that imply a next-service
   window (oil change every 5,000 mi, HVAC filter every 90 days),
   compute the implied due date.
4. **Bucket by urgency.**
   - **Overdue** — `due < today`.
   - **Due in 30 days** — `today <= due <= today + 30`.
   - **Due 30-60 days** — `today + 30 < due <= today + window_days`.
   - **Open without a date** — surface separately at the bottom.
5. **Group within each bucket.** By owner where applicable, then by
   domain (medical, vehicle, home, household, other).
6. **Apply `filter`** if provided.

## Output

Write `outputs/follow-ups/<YYYY-MM-DD>.md` (today's date). The page is
versioned per run — the user can compare two runs to see what's been
closed.

Frontmatter:

```yaml
type: follow-up-report
status: active
provenance: synthesized
created: <today>
modified: <today>
tags: [follow-ups, <window_days>d]
follow_up_window_days: <window_days>
```

Sections:

- **Synopsis** — counts (N overdue, M in next 30, K in 30-60, plus
  open-without-date).
- **Overdue** — most prominent. Each item links to its source page,
  shows the due date, and includes a recommended action (call provider,
  schedule service, refill).
- **Due in 30 days** — next-most-prominent.
- **Due 30-60 days** — looking-ahead view.
- **Open without a date** — items that may need a due date attached.
- **By person / domain** — cross-grouped view for the household to
  scan by owner.

The `follow-up-report` type may not yet exist in
`frontmatter.schema.yaml`. That's fine; `wiki-lint` flags it as a gap.

## When the scan finds nothing

If the window is empty (no callouts, no open action items), produce a
minimal page noting "no follow-ups due in the next <window_days> days
as of <today>". Two reasons this might happen:

1. The household is fully caught up. Great.
2. Callouts aren't being logged. If ingester history shows medical
   records or meetings with no callouts, surface the gap and suggest
   "consider adding `> [!important] Follow-up due by YYYY-MM-DD`
   callouts when ingesting, so this operation can do its job".

## Cadence

- **Manual:** Run weekly on review day.
- **Scheduled:** A Sunday-morning sweep is a good default; the
  household reads the report over coffee before the week begins.
