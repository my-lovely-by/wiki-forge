---
name: ingest-meeting
description: "Ingest a meeting source (transcript, notes paste, recording link) into a structured meeting page. Load from the `ingest` skill when content-type routing identifies the source as a meeting (calendar invite text, transcript turns, agenda+notes shape). Produces one page under `wiki/meetings/`, links attendees to `wiki/people/`, and registers decisions and follow-ups for downstream operations (weekly-digest, follow-up-tracker)."
license: MIT
---

# ingest-meeting

Convert a meeting source into a clean, durable wiki page. The user
either pastes a transcript or notes, drops a recording transcript file,
or hands you a calendar invite + chat log — your job is to produce one
meeting page and the linked-person stubs it needs.

## When you're loaded

The `ingest` skill routes here after it has classified the source as
a meeting. You can also be loaded directly when the user says
"capture the meeting I just had" or similar.

If the user's input is ambiguous (could be a meeting, could be a
casual note about a conversation), check before assuming. A meeting
has a defined start/end, an agenda or set of attendees, and decisions
or follow-ups worth tracking.

## Inputs you'll see

- A pasted or attached transcript (Zoom, Otter, Granola, hand-typed).
- A meeting agenda and notes from a calendar invite.
- A combination: agenda from one source, notes/transcript from another.

For each, you need to extract:

- **`meeting_date`** — the actual meeting date, not today's date. If
  the source doesn't name a date, ask the user before falling back to
  today.
- **`meeting_attendees`** — people present. Names exactly as they
  appear in the source on first pass; you'll canonicalize against
  existing person pages in the next step.
- **`meeting_decisions`** — choices the group made. Phrase each as a
  declarative sentence in past tense.
- **`meeting_follow_ups`** — action items, owner-tagged when known.
  Format: `[ ] @owner: do the thing by YYYY-MM-DD`.

## Page shape

Render the page from `_templates/meeting.md`. The filename convention
is `wiki/meetings/YYYY-MM-DD-<slug>.md` where the slug is a kebab-case
two-to-four-word summary (e.g. `2026-05-16-q2-planning-kickoff.md`).
Multiple meetings on the same day get `-2`, `-3` suffixes.

## Person linking

For each name in `meeting_attendees`:

1. Search `wiki/people/` for an existing page. Tolerate common
   variants (full vs. shortened names, initials).
2. If a match exists, use its wikilink (`[[jane-doe]]`).
3. If no match, stub a new person page under `wiki/people/` with
   `type: person`, `status: draft`, `provenance: synthesized`, and a
   one-line note "First seen in `[[meetings/<this-meeting>]]`."
   Wikilink to the stub.

## Decisions and follow-ups

These are first-class data for downstream operations:

- `weekly-digest` reads decisions across the week.
- `follow-up-tracker` (Task 13) reads follow-ups across all sources.

Be specific. "We discussed pricing" is not a decision; "We agreed to
ship at $X for Q3" is. "Talk to legal" is not a follow-up; "@alice
will draft the legal memo by 2026-05-23" is.

## When you can't extract cleanly

If the source is too noisy (a long unstructured transcript with no
clear decisions), do not invent. Produce the page with whatever you
can extract honestly, mark `provenance: mixed`, and add a TODO comment
at the top asking the user to fill the gaps. Better an honestly thin
page than a hallucinated thick one.

## After writing

- Append a one-line summary to the wiki's running activity (the
  `wiki-lint` skill picks these up on its next run).
- If the meeting introduced new follow-ups with owners and dates,
  remind the user that `wiki run follow-up-tracker` will surface them
  on the next sweep.
