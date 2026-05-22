# actions/

Standalone action-item pages — one per item worth tracking on its own,
rather than as a one-line follow-up callout on a meeting or medical
page.

## When to make an action-item page

Most action items live inline on their source page (a meeting's
follow-up list, a medical record's recheck callout). Promote an item to
its own page here when:

- It accumulates updates over time and would clutter the source page.
- It has sub-tasks the household wants to check off separately.
- It needs to be findable on its own (a multi-week household repair, a
  recurring administrative chore).

## Conventions

- **Filename:** `YYYY-MM-DD-<kebab-case-slug>.md` where the date is the
  action's creation date. Newest first when sorted by name.
- **Owner:** `action_item_owner` is the canonical name as it appears in
  `wiki/people/` (without the `@`). Use the household member or contact
  who actually has the action.
- **Due date:** Optional. Open-ended actions exist; mark a due date
  only when one is real.
- **State:** `open`, `in-progress`, `blocked`, `done`, `cancelled`.
  The `follow-up-tracker` operation surfaces open and in-progress
  items inside its scan window.
- **Source:** Wikilink to the page that spawned this action (a meeting,
  a medical record, etc.) when there is one.

## How the operations use these pages

- `follow-up-tracker` reads `action_item_state` and `action_item_due`
  to surface what's due, what's overdue, and what's open without a date.
- `weekly-digest` may include closed-this-week items if the user wires
  it in.
