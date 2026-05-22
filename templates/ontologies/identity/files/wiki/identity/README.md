# identity/

The `identity` ontology seeds exactly one page: `identity.md` at the
vault root. This folder exists to host the README you're reading, plus
any future per-owner attachments (an avatar, a signature graphic) that
don't belong on the main page itself.

## What `identity.md` is for

A single durable note about the person this vault belongs to. It's the
companion to `wiki/people/` — that folder is for everyone *else*; this
page is the owner.

Operations and ingester skills consult `identity.md` when they need a
stable answer to:

- Who owns this vault? (drafting summaries, signing off)
- What's their timezone? (date math, "next Tuesday")
- What are their stable preferences? (communication style, recurring
  constraints)

## Editing conventions

- **One page, durable.** Update the page when the underlying facts
  change. Do not log day-to-day events here — that's what meeting
  notes, action items, and the weekly digest are for.
- **Plain values, not template tokens.** The fields in `identity.md`
  are interpolated *once* at `wiki init` from the recipe's
  `variables:` defaults. After that, edit the values directly. Writing
  a literal `{owner_name}` back into the file will read as a stray
  placeholder, not as a token that gets re-filled.
- **No frontmatter.** Like `CORE.md` and `AGENTS.md`, `identity.md`
  lives at the vault root as an infrastructure page, not a node in the
  wikilink graph. It does not declare `type:` and is not part of the
  content-type schema.

## What does *not* go here

- People you interact with → `wiki/people/`.
- Day-to-day plans, decisions, or chores → meeting / action-item
  pages.
- Sensitive personal info (financial, medical, identification numbers)
  → out of the vault entirely, or in a separate gitignored vault.

## Schema

The `identity` ontology does not extend `frontmatter.schema.yaml`.
There is exactly one identity page per vault, and it's the kit's job
to seed it — not the ingester's job to mint new ones — so no
content-type registration is needed.
