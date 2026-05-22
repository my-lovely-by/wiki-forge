# interviews/

One page per *interview* — a scheduled, structured conversation with
a defined research goal. User research, customer-discovery, hiring
loops, win-loss calls, expert interviews. Pages are created by the
`ingest-interview` skill (see `skills/ingest-interview/SKILL.md`) from
a transcript, notes, or post-hoc summary.

## Conventions

- **Filename:** `YYYY-MM-DD-<subject>-<purpose>.md` where `<subject>`
  is the kebab-case subject name and `<purpose>` is a one-word tag
  (`research`, `hiring`, `winloss`, `discovery`).
- **Template:** `_templates/interview.md` is the seed.
- **Linking:** `interview_subject` is a wikilink to `wiki/people/`.
- **Frontmatter:** `type: interview` plus the interview-scoped fields
  declared in `frontmatter.schema.yaml`'s managed `fields` region
  (`interview_date`, `interview_subject`, `interview_purpose`,
  `interview_questions`, `interview_findings`,
  `interview_follow_ups`).

## Interview vs. meeting vs. customer-feedback

- **Interview** — defined subject, defined purpose, question guide.
  One person being interviewed, one interviewer (or pair).
- **Meeting** — multiple participants, shared decisions and
  follow-ups, no single "subject."
- **Customer-feedback** — customer-originated commentary on the
  product or relationship, often unsolicited (ticket, survey, NPS).

When in doubt, pick the type that matches the source's primary
intent: were you *gathering input from a subject* (interview), or
*deciding together* (meeting), or *recording what the customer told
you* (feedback)?

## What downstream operations read

- `status-synthesis` walks interviews within a window and surfaces
  theme clusters across subjects.
- `action-item-rollup` reads `interview_follow_ups`.
