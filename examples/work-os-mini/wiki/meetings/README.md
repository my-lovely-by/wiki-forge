# meetings/

One page per meeting worth a durable note — standups, 1:1s, reviews,
external calls. Pages are created by the `ingest-meeting` skill (see
`skills/ingest-meeting/SKILL.md`) from a transcript, paste, or notes.

## Conventions

- **Filename:** `YYYY-MM-DD-<kebab-case-slug>.md`. Multiple meetings on
  the same day get a `-2`, `-3` suffix.
- **Template:** `_templates/meeting.md` is the seed. Open it in
  Obsidian via the Templater command or invoke `ingest-meeting`.
- **Linking:** attendees are wikilinks to `wiki/people/`. The
  `ingest-meeting` skill stubs missing people pages on first
  reference.
- **Frontmatter:** `type: meeting` plus the meeting-scoped fields
  declared in `frontmatter.schema.yaml`'s managed `fields` region
  (`meeting_date`, `meeting_attendees`, `meeting_decisions`,
  `meeting_follow_ups`).

## What downstream operations read

- `weekly-digest` walks meetings within the week's window and
  summarizes decisions + follow-ups.
- `follow-up-tracker` (later task) reads `meeting_follow_ups` and
  cross-references owners and due dates.
