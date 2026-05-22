---
name: ingest-interview
description: "Ingest an interview source (recording transcript, written notes, recorded-call summary) into a structured interview page. Load from the `ingest` skill when content-type routing identifies the source as a scheduled, structured conversation with a defined research goal — user research, customer-discovery, hiring, win-loss. Produces one page under `wiki/interviews/`, links the subject to `wiki/people/`, and registers the interview for downstream operations (status-synthesis, action-item-rollup)."
license: MIT
---

# ingest-interview

Convert an interview — a user-research session, a customer-discovery
conversation, a hiring loop, a win-loss call — into a clean, durable
wiki page. The user pastes a transcript, drops notes, or hands you a
recording summary; your job is to produce one interview page and the
linked-subject stub it needs.

## When you're loaded

The `ingest` skill routes here after it has classified the source as
an interview. You can also be loaded directly when the user says
"capture the user-research session" or "log the win-loss call."

If the source is ambiguous (could be an interview, could be a casual
meeting, could be customer feedback), check before assuming. An
interview has:

- A *defined* subject (one named person or pair, not a group meeting).
- A *defined* purpose declared up front.
- A question set or topic guide, even if loosely followed.

A meeting with peers about project planning is not an interview. A
support call where a customer happened to share product opinions is
customer-feedback, not an interview.

## Inputs you'll see

- A pasted transcript (Granola, Otter, Zoom, hand-typed).
- A written notes file with the interviewer's running commentary.
- A short "interview summary" the interviewer wrote post-hoc.
- A recording link + the interviewer's quick recap.

For each, extract:

- **`interview_date`** — when the interview *happened*, not today's
  date. Ask if the source doesn't name it.
- **`interview_subject`** — the named person being interviewed.
  Wikilink to `wiki/people/`. One person per interview page; a
  two-person interview gets two pages or one page that names both,
  but the subject field stays singular — pick the primary subject.
- **`interview_purpose`** — short noun phrase: `user-research`,
  `customer-discovery`, `hiring-screen`, `hiring-onsite`, `win-loss`,
  `expert-interview`. If the source names a different purpose,
  preserve its language.
- **`interview_questions`** — the asked questions (or the question
  guide), in order. Preserve wording when possible — the asked
  question is the source of meaning, not the paraphrase.
- **`interview_findings`** — the answers the interviewer found
  meaningful, phrased as declarative sentences. Map findings to
  questions when the mapping is clear; leave standalone findings
  separate.
- **`interview_follow_ups`** — concrete next steps. Format:
  `@owner: do the thing by YYYY-MM-DD`.

## Page shape

Render the page from `_templates/interview.md`. The filename
convention is `wiki/interviews/YYYY-MM-DD-<subject>-<purpose>.md`,
where `<subject>` is the kebab-case subject name and `<purpose>` is a
one-word purpose tag (`research`, `hiring`, `winloss`, `discovery`).

## Subject linking

The `interview_subject` field must resolve to a page under
`wiki/people/`:

1. Search `wiki/people/`. Tolerate full vs. shortened names.
2. Match → wikilink. No match → stub a new person page with
   `type: person`, `status: draft`, `provenance: synthesized`, and a
   one-line note "First seen in `[[interviews/<this-interview>]]`."

## Findings vs. transcript

The page body holds the durable summary, not the verbatim transcript.
If the source is a long transcript, keep the transcript path/link in
the `Source` section and extract findings into the body. Verbatim
quotes worth preserving go in the body as block quotes; do not paste
the entire transcript.

For hiring interviews specifically, be careful with feedback that
could carry bias signals. The `interview_findings` field is for
observations relevant to the interview's stated purpose; opinions
outside that scope belong elsewhere.

## When the source is thin

A two-sentence "talked to Alice, she liked the new flow" is not an
interview page — it's a journal entry. Don't promote it. Either
capture the actual interview or skip the page.

## After writing

- Append a one-line summary to the running activity log.
- If themes from this interview overlap with recent customer-feedback,
  note the pattern — `status-synthesis` will surface it on the next
  sweep.
- If the interview produced follow-ups with owners, remind the user
  that `wiki run action-item-rollup` will surface them.
