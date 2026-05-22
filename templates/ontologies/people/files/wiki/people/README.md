# people/

One page per person you interact with often enough to want a single
durable note for. Pages here are linked by name from meetings,
interviews, stakeholder updates, follow-ups, and any other content-type
that names individuals.

## Conventions

- **One page per person.** Filename is the person's display name in
  kebab-case: `jane-doe.md`, `j-park.md`. Initials are fine when a full
  name would be ambiguous or sensitive.
- **Frontmatter.** Every person page declares `type: person` and the
  baseline frontmatter fields (`status`, `provenance`, `created`,
  `modified`, `tags`). The `person` type is added to
  `frontmatter.schema.yaml` when an ontology contributor needs it (the
  `people` ontology itself does not extend the schema — content-type
  primitives that *create* people pages do that work).
- **Aliases as wikilinks.** If a person is known by multiple names
  ("Jane Doe", "JD", "Jane"), pick the canonical filename and reference
  the aliases inside the page body. The vault-side `wiki-search` skill
  resolves common aliases when ranking results.
- **Sensitive details.** Personal information beyond what's needed to
  do your work belongs in a separate, gitignored vault or out of the
  vault entirely. Default to the minimum useful page.

## What goes on a person page

Short, durable framing — not a chat log:

- Role / affiliation
- How you know them
- Stable preferences, constraints, recurring topics
- Links to the meetings, interviews, or threads that mention them

Avoid duplicating content that already lives on a meeting or interview
page — wikilink instead.

## Created by other primitives

Most person pages are *created on first reference* by content-type
ingesters. A meeting ingester that sees a new attendee name will stub
a person page (with `status: draft`, `provenance: synthesized`) and
link to it. You promote the stub to a real page when you have something
worth writing.
