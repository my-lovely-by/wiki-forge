---
name: ingest-action-item
description: "Promote a follow-up — pasted by the user, extracted from a meeting transcript, or surfaced from a medical record — into a standalone `action-item` page under `wiki/actions/`. Load when the user says \"track this as an action\", \"make this its own task\", or wants to promote a one-line follow-up to a page that can accumulate updates. Most follow-ups stay inline on their source page; this skill is for the ones that don't."
license: MIT
---

# ingest-action-item

Promote a follow-up to a standalone page when an inline callout isn't
enough. Most action items should stay inline on the meeting or medical
record they came from — the `follow-up-tracker` operation scans those
callouts directly. Use this skill only when the item is large enough
or long-running enough that its own page is the right home.

## When you're loaded

- The user pastes an action and asks you to track it as its own page.
- The user explicitly promotes a follow-up from a meeting or medical
  page ("make this its own task").
- An ingester (meeting, medical-record) surfaces an item that has
  sub-tasks and proposes promotion.

If the user pastes a list of follow-ups expecting many pages, ask
before fanning out — they may have meant a single rollup page.

## Inputs you'll see

- A short prose description of the action (one sentence to a few
  paragraphs).
- Optionally, the source page (a meeting, a medical record). Capture
  the wikilink in `action_item_source` and cross-link both ways.

You need to extract:

- **`action_item_owner`** — the canonical name from `wiki/people/`
  (without the `@`). Stub the person page if missing.
- **`action_item_due`** — date if one is given; omit otherwise.
- **`action_item_state`** — initial state: `open`, `in-progress`, or
  `blocked`. Default to `open`.
- **`action_item_source`** — wikilink to the page the action came from,
  when there is one.

## Page shape

Render from `_templates/action-item.md`. Filename is
`wiki/actions/YYYY-MM-DD-<slug>.md` where the date is today's date and
the slug is a 2-4 word kebab-case summary.

The body has four sections:

- **What needs doing** — specific, present-tense description.
- **Why** — context and link to source.
- **Sub-tasks** — checklist for items with multiple steps.
- **Updates** — append-only log; dated entries, don't overwrite.

## Side-effects

- **Person stub.** If the owner isn't yet a person page, stub one.
- **Source backlink.** On the source page (the meeting, medical record,
  or other), replace the inline follow-up callout with a wikilink to
  the new action page so the two pages reference each other.

## When you can't extract cleanly

- **Owner unclear.** Action items without an owner rot — ask before
  writing.
- **State unclear.** Default to `open`. The user can change it.
- **No real reason for a page.** If the item is "remember to buy milk",
  it shouldn't be its own page. Surface this and suggest the inline
  callout pattern instead.

## After writing

- Append a one-line entry to the wiki's running activity.
- The `follow-up-tracker` operation will pick this page up on its next
  sweep alongside inline callouts.
