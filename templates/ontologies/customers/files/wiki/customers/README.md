# customers/

One page per customer — the named external entities (companies,
accounts, individual users when applicable) you do work for or with.
Pages here are linked from customer feedback, vendor contracts (when
the customer is also a counterparty), interviews, stakeholder updates,
and any operation that walks the customer roster.

## Conventions

- **One page per customer.** Filename is the customer's name in
  kebab-case: `acme-corp.md`, `north-river-bank.md`. Use the legal name
  when there's any chance of confusion with another tenant.
- **Frontmatter.** Every customer page declares `type: customer` and
  the baseline frontmatter fields (`status`, `provenance`, `created`,
  `modified`, `tags`). The `customer` type is added to
  `frontmatter.schema.yaml` when an ontology contributor needs it.
- **Account team.** Name the AE / CSM / DRI at the top, wikilinked to
  `wiki/people/`.
- **Sensitive details.** Contractual terms, internal pricing notes, and
  anything covered by an NDA belong in a separate, access-controlled
  vault — not here, unless the whole vault is appropriately scoped.

## What goes on a customer page

Short, durable framing — not a CRM log:

- One-line description: who they are, what they buy from you.
- Account team (wikilinks).
- Active projects and engagements (wikilinks to `wiki/projects/`).
- Renewal date and current ARR tier when relevant (high-level only).
- Recent feedback themes, linked to the source pages.
- Known constraints (procurement quirks, hard internal contacts, SLAs
  that matter).

Avoid duplicating content that already lives on a feedback, interview,
or contract page — wikilink instead.

## Created by other primitives

Content-type ingesters stub customer pages on first reference:

- `customer-feedback` stubs a customer when feedback names a previously
  unknown account.
- `vendor-contract` may stub a customer when the contract counterparty
  is also a customer.
- `interview` stubs a customer when the interviewee's affiliation is
  a customer org.

Stubs ship with `status: draft` and `provenance: synthesized`. Promote
them when the relationship becomes load-bearing.
