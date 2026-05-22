# Identity

One durable page about the person this vault belongs to. Operations and
ingester skills read this page when they need a stable answer to "who
owns this vault?" — timezone for date math, pronouns for drafted
summaries, role for framing.

This page is *not* a journal. Update it when the underlying facts
change (a move, a new role, a new pronoun) — not on every life event.

## Basics

- **Name:** 
- **Pronouns:** 
- **Role:** 
- **Timezone:** 

Blank fields above are intentional — the personal recipe ships with
empty-string defaults so a fresh `wiki init` produces a visibly
unfilled page. Fill them in by hand; do not wrap the values in
`{braces}` (a literal `{name}` here would be re-interpreted on the next
`wiki upgrade`).

## Context

A few sentences about how you think of yourself in this vault: what
you're optimising for, what you keep here, what you keep elsewhere. The
`weekly-digest` and `follow-up-tracker` operations may quote this
framing when summarising for you.

## Stable preferences

Things that almost never change and that you want every Claude session
to remember without re-stating:

- Communication style
- Decision-making heuristics
- Recurring constraints (recurring obligations, health, accessibility)

## Out of scope

- Day-to-day plans → meeting notes, action items, weekly digest.
- People you interact with → `wiki/people/`.
- Sensitive personal information → keep out of the vault, or in a
  separate gitignored vault.
