---
name: ingest-stakeholder-update
description: "Ingest a stakeholder-update source (status email, slide bullets, paste from a doc) into a structured stakeholder-update page. Load from the `ingest` skill when content-type routing identifies the source as a project status update — typically a short narrative addressed to a named audience with highlights/risks/asks structure. Produces one page under `wiki/stakeholder-updates/`, links the project to `wiki/projects/`, audience members to `wiki/people/`, and registers the page for downstream operations (status-synthesis, action-item-rollup)."
license: MIT
---

# ingest-stakeholder-update

Convert a project status update — typically a Friday recap, exec email,
or weekly slide — into a clean wiki page. The user pastes the text or
drops the source file; your job is to produce one update page and the
linked-project / linked-people stubs it needs.

## When you're loaded

The `ingest` skill routes here after it has classified the source as a
stakeholder update. You can also be loaded directly when the user says
"capture this Friday update" or "log the weekly to leadership."

If the source is ambiguous (could be a stakeholder update, could be a
meeting note), check before assuming. A stakeholder update is *outbound
communication* with a defined audience, not a meeting record.

## Inputs you'll see

- A pasted email body or slide bullets.
- A "weekly status" doc with explicit highlights/risks/asks sections.
- A Slack message to a stakeholder channel.

For each, extract:

- **`update_date`** — the date the update was *sent*, not today's date.
  If the source doesn't name a date, ask before falling back to today.
- **`update_project`** — the project this update is about. Wikilink to
  `wiki/projects/`. If the project page doesn't exist, stub it.
- **`update_audience`** — the named recipients or audience group
  ("leadership", "@channel", explicit names). Wikilink individuals to
  `wiki/people/`; leave group labels as plain strings.
- **`update_status`** — overall traffic-light read. Common values:
  `green`, `yellow`, `red`. If the source uses a different convention
  (e.g. "on-track", "at-risk", "off-track"), preserve the source's
  language verbatim.
- **`update_highlights`** — what shipped or moved this week. Phrase
  each as a past-tense declarative sentence.
- **`update_risks`** — known risks. Phrase as "risk: impact / mitigation"
  when both are present.
- **`update_asks`** — explicit asks of the audience. Format:
  `@owner: do the thing by YYYY-MM-DD`.

## Page shape

Render the page from `_templates/stakeholder-update.md`. The filename
convention is `wiki/stakeholder-updates/YYYY-MM-DD-<project>-<slug>.md`
where `<project>` is the kebab-case project name and `<slug>` is a
two-to-three-word descriptor. Multiple updates on the same project on
the same day get `-2`, `-3` suffixes.

## Project linking

The `update_project` field must resolve to a page under
`wiki/projects/`:

1. Search `wiki/projects/` for an existing page that matches the project
   name (tolerate codename vs. public-name variants).
2. If a match exists, use its wikilink (`[[apollo-revamp]]`).
3. If no match, stub a new project page with `type: project`,
   `status: draft`, `provenance: synthesized`, and a one-line note
   "First seen in `[[stakeholder-updates/<this-update>]]`." Wikilink to
   the stub.

## Person linking

For each name in `update_audience`:

1. Search `wiki/people/` for an existing page.
2. If a match exists, use its wikilink.
3. If no match, stub a new person page (see `ingest-meeting` for the
   person-stub convention — same shape).

## Asks are first-class

The `action-item-rollup` operation reads `update_asks` across all
stakeholder updates and pairs them with `meeting_follow_ups` to produce
a single owner-grouped view. Be specific: "follow up with legal" is
not an ask; "@alice: confirm the legal sign-off by 2026-05-23" is.

## When the source is thin

A real-world status update is sometimes one sentence: "Apollo: green,
no changes." That's fine. Produce the page with the available signal,
mark `provenance: extracted`, and don't pad with hallucinated risks or
asks. An honestly thin update is more useful than a fabricated thick
one — `status-synthesis` will note the absence of detail.

## After writing

- Append a one-line summary to the running activity log.
- If the update introduced new asks, remind the user that
  `wiki run action-item-rollup` will surface them on the next sweep.
- If the status changed colour vs. the previous update for the same
  project, mention it so the user can decide whether to escalate.
