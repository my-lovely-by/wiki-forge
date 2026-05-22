# domains/

One page per *domain* — a durable subject area of expertise or
responsibility (e.g. `billing`, `compliance`, `developer-experience`,
`onboarding`). Domains are the slow-moving axis that projects,
customers, and decisions cut across.

## When to add a domain

A domain earns a page when:

- You return to the same concepts across multiple projects or
  customers.
- New hires need an orientation to the area.
- Decisions in the area depend on context that doesn't fit on any one
  project page.

Don't pre-create domains for every team or every Jira component — let
the need surface. A domain that has zero linked content for a quarter
is probably noise; archive it.

## Conventions

- **Filename:** kebab-case domain name (`billing.md`,
  `developer-experience.md`).
- **Frontmatter:** `type: domain` plus the baseline frontmatter fields
  (`status`, `provenance`, `created`, `modified`, `tags`). The `domain`
  type is added to `frontmatter.schema.yaml` when an ontology
  contributor needs it.
- **Owner.** Name the DRI or steward at the top. Wikilink them to
  `wiki/people/`.
- **Reading list.** A short list of the source-of-truth links
  (runbooks, dashboards, policy docs) at the bottom of the page. Avoid
  duplicating their content — link out.

## What goes on a domain page

- Working definition of the domain in 1–3 sentences.
- DRI / steward.
- Key open questions or active debates.
- Links to the projects, decisions, interviews, and customer feedback
  that touch this domain.
- Pointers to the canonical external references.

## Created by other primitives

Content-type ingesters (interview, decision, customer-feedback) may
stub a domain page on first reference when they see a previously-
unknown domain mentioned in the source. Stubs ship with
`status: draft` and `provenance: synthesized`. Promote them when the
domain becomes load-bearing.
