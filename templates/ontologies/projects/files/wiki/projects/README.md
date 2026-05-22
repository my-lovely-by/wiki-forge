# projects/

One page per project — the durable, named pieces of work you steer.
Pages here are linked by name from stakeholder updates, decisions,
status synthesis runs, customer feedback, and any other content-type
that names projects.

## Conventions

- **One page per project.** Filename is the project's short name in
  kebab-case: `migrate-billing.md`, `apollo-revamp.md`. Codenames are
  fine when the public name is too long or ambiguous.
- **Frontmatter.** Every project page declares `type: project` and the
  baseline frontmatter fields (`status`, `provenance`, `created`,
  `modified`, `tags`). The `project` type is added to
  `frontmatter.schema.yaml` when an ontology contributor needs it (the
  `projects` ontology itself does not extend the schema — content-type
  primitives that *create* project pages do that work).
- **Status outside the page body.** Status changes, owners, and
  milestones live on the page body itself. The `status-synthesis`
  operation produces a *separate* digest page; it does not edit the
  project page.
- **One owner, many contributors.** Name the directly-responsible
  individual (DRI) at the top of the page. Wikilink them to
  `wiki/people/`.

## What goes on a project page

Short, durable framing — not a running journal:

- One-line summary of the project's goal.
- DRI and key stakeholders (wikilinks to `wiki/people/`).
- Linked customers, domains, decisions, and recent stakeholder updates.
- Open risks and current status (high-level, not granular).
- Out-of-scope items so future readers know what this project is *not*.

Avoid duplicating content that already lives on a meeting, decision, or
status-synthesis page — wikilink instead.

## Created by other primitives

Most project pages are *created on first reference* by content-type
ingesters. A stakeholder-update ingester that sees a new project name
will stub a project page (with `status: draft`,
`provenance: synthesized`) and link to it. You promote the stub to a
real page when you have something worth writing.
