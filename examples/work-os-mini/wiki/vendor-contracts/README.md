# vendor-contracts/

One page per active or historical vendor contract — SaaS subscriptions,
service agreements, contractor SoWs. Pages are created by the
`ingest-vendor-contract` skill (see
`skills/ingest-vendor-contract/SKILL.md`) from contract text, renewal
notices, order forms, or procurement summaries.

## Conventions

- **Filename:** `<vendor>-<YYYY>.md` where `<vendor>` is the kebab-case
  vendor name and `<YYYY>` is the contract's start year. A renewal
  starts a new page with the new start year.
- **Template:** `_templates/vendor-contract.md` is the seed. Open it
  via the Templater command or invoke `ingest-vendor-contract`.
- **Linking:** `contract_owner` is a wikilink to `wiki/people/`. The
  vendor itself is a plain string — work-os v0.1 doesn't ship a
  `vendors` ontology; if a recipe in the future does, the convention
  becomes a wikilink.
- **Frontmatter:** `type: vendor-contract` plus the contract-scoped
  fields declared in `frontmatter.schema.yaml`'s managed `fields`
  region (`contract_vendor`, `contract_start`, `contract_end`,
  `contract_renewal_date`, `contract_amount`, `contract_owner`,
  `contract_terms_summary`).

## What downstream operations read

- `renewal-reminders` walks every vendor-contract page and surfaces
  those whose `contract_renewal_date` falls inside the configured
  look-ahead window. Open-ended contracts (no `contract_end`) are
  flagged separately.
