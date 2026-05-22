---
name: ingest-decision
description: "Ingest a decision source (meeting decision callout, email thread that landed on a choice, written rationale or short ADR-style doc) into a structured decision page. Load from the `ingest` skill when content-type routing identifies the source as a durable choice with context, alternatives, and consequences — distinct from a meeting (which captures *what happened*) or a stakeholder-update (which captures *what to communicate*). Produces one page under `wiki/decisions/`, links the owner to `wiki/people/`, and is read by downstream operations (status-synthesis, onboarding-pack)."
license: MIT
---

# ingest-decision

Convert a decision into a clean, durable wiki page. The user pastes a
meeting-decision callout, an email thread that landed on a choice, or
a written rationale; your job is to produce one decision page that a
future reader (and a future you) can rely on.

## When you're loaded

The `ingest` skill routes here after it has classified the source as
a decision. You can also be loaded directly when the user says
"record this decision" or "log what we just agreed on."

If the source is ambiguous, distinguish:

- **Decision** — a choice that closes a question, with context,
  alternatives considered, and consequences. Durable over months.
- **Meeting** — the record of a conversation, which may *contain*
  decisions but also carries unrelated discussion and follow-ups.
- **Stakeholder-update** — outbound communication, often citing
  decisions but not the place to record their rationale.

When a meeting produces a decision worth durable mention, both pages
exist: the meeting page lists the decision in `meeting_decisions`, and
a separate decision page captures the rationale.

## Inputs you'll see

- A "Decision:" callout copied from a meeting note.
- An email thread where the participants converged on a choice.
- A short written rationale (a doc, a Slack thread summary, a paste).
- A retro item that closed with a commitment.

For each, extract:

- **`decision_date`** — when the decision was *made*, not today's
  date. Ask if unclear.
- **`decision_owner`** — the person accountable for the decision and
  its execution. Wikilink to `wiki/people/`. Decisions without an
  owner rot; if the source doesn't name one, ask the user.
- **`decision_status`** — `proposed`, `accepted`, `superseded`,
  `rejected`. A decision that is "we'll decide next week" is
  `proposed`, not `accepted`.
- **`decision_context`** — one-sentence summary of the problem the
  decision addresses. The body's `## Context` section is the
  long-form version.
- **`decision_alternatives`** — short bullets naming the alternatives
  considered. "We considered X but rejected because Y." If the source
  records only the chosen path, the field stays empty — but flag the
  absence in the body: a decision page with no alternatives is a
  weaker decision page.
- **`decision_supersedes`** — wikilink to the prior decision this one
  replaces, when applicable. The prior decision's
  `decision_status` should be moved to `superseded` on the same pass
  (ask the user first; don't silently mutate prior pages).

## Page shape

Render the page from `_templates/decision.md`. The filename
convention is `wiki/decisions/YYYY-MM-DD-<slug>.md` where the slug is
a two-to-four-word summary of the decision itself
(`2026-05-16-default-region-us-east.md`). Multiple decisions on the
same day get `-2`, `-3` suffixes.

## Owner linking

The `decision_owner` field must resolve to a page under
`wiki/people/`:

1. Search `wiki/people/`. Tolerate common variants.
2. Match → wikilink. No match → stub a person page with
   `type: person`, `status: draft`, `provenance: synthesized`, and a
   one-line note "First seen in `[[decisions/<this-decision>]]`."

## Decisions are first-class for synthesis

`status-synthesis` walks recent decisions to surface what closed in
the window vs. what's still open. Be specific: a decision phrased as
"we should consider X" is not a decision — that's a discussion. A
decision is "we chose X because Y." If the source isn't decisive,
push back: ask the user whether this is really a decision or a
pending discussion.

## Superseding

When this decision supersedes a prior one:

1. Set `decision_supersedes` to the prior page's wikilink.
2. Ask the user before editing the prior page; if approved, change
   its `decision_status` to `superseded` and add a line at the top
   of its body: "Superseded by `[[decisions/<this-decision>]]` on
   YYYY-MM-DD."

Never silently mutate a prior decision page — the audit trail is the
point.

## When the source is thin

A two-line decision callout is a perfectly valid input. Produce the
page with the available signal, mark `provenance: extracted`, and add
TODOs at the top of the body for the missing context / alternatives /
consequences. A thin honest page is more useful than a fabricated
thick one — the user can fill it in later.

## After writing

- Append a one-line summary to the running activity log.
- If the decision is connected to a project, suggest the user link to
  the project page (and vice versa from the project page).
- If the decision supersedes a prior one, confirm the prior page's
  status update happened before considering the ingest complete.
