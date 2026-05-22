# decisions/

One page per decision worth a durable record — architecture choices,
contract terms, pricing changes, hiring outcomes, anything where a
future reader will ask "why did we do that?" Pages are created by the
`ingest-decision` skill (see `skills/ingest-decision/SKILL.md`) from a
meeting callout, an email thread, or a written rationale.

## Conventions

- **Filename:** `YYYY-MM-DD-<slug>.md` where `<slug>` is a
  two-to-four-word summary of the decision itself.
- **Template:** `_templates/decision.md` is the seed.
- **Linking:** `decision_owner` is a wikilink to `wiki/people/`;
  `decision_supersedes`, when set, is a wikilink to the prior decision
  page this one replaces.
- **Frontmatter:** `type: decision` plus the decision-scoped fields
  declared in `frontmatter.schema.yaml`'s managed `fields` region
  (`decision_date`, `decision_owner`, `decision_status`,
  `decision_context`, `decision_alternatives`,
  `decision_supersedes`).

## Decision vs. ADR

ADRs are the kit's *engineering* decision records — they live under
`docs/adr/` in this repo and are versioned with the codebase. The
`decision` content-type is the *operational* equivalent for a user's
vault: business decisions, contract terms, hiring outcomes,
prioritisation choices. The shape is similar (context, decision,
alternatives, consequences) but the audience and scope are different.

If you're recording an engineering decision *about the kit itself*,
write an ADR under `docs/adr/`. If you're recording a decision your
team or org made about the work, use this content-type.

## Cross-cutting links

Decisions naturally connect to projects, customers, and domains. The
page body should wikilink the relevant pages explicitly — there's no
dedicated frontmatter field for these because the cross-cutting set
varies per decision.

## What downstream operations read

- `status-synthesis` walks recent decisions and surfaces what closed
  in the window vs. what's still open / proposed.
- `onboarding-pack` (when scoped to a project or customer) reads
  decisions tagged or linked to that scope.

## Supersession

A decision that replaces an earlier one sets `decision_supersedes` to
the prior page's wikilink. The prior page's `decision_status` should
be updated to `superseded` and the body should link forward to the
new decision. The audit trail — two pages, one superseded, one active
— is the point; never edit a decision page silently after it's been
referenced.
