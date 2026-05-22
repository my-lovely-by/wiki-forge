# vendors/

One page per business or service provider the household does enough
business with to want a single durable note for: the plumber, the
pediatric clinic, the car insurer, the brokerage that issues your 1099s,
the contractor who redid the kitchen.

## Conventions

- **One page per vendor.** Filename is the vendor name in kebab-case:
  `quick-lube-express.md`, `vanguard.md`. A vendor with both consumer
  and commercial arms can get separate pages — disambiguate in the
  filename (`acme-residential.md` vs. `acme-commercial.md`).
- **Frontmatter.** `type: vendor`, `status` (active / archived),
  baseline fields. The `vendors` ontology itself does not extend the
  schema — content-type primitives that *create* vendor pages on first
  reference do that work.
- **Aliases as wikilinks.** A vendor may go by several names (legal
  name vs. trade name). Pick the canonical filename and list aliases
  in the page body; the `wiki-search` skill resolves them.

## What goes on a vendor page

- Service category, address, phone, account/customer number, login
  hints (without secrets — keep credentials elsewhere).
- Service history wikilinks (receipt pages, maintenance records).
- Renewal/term dates if the relationship is contractual.
- Notes on quality, pricing, who to ask for.

## Created by other primitives

- `receipt` ingester stubs a vendor page on first reference and
  appends to service history.
- `tax-document` ingester treats the issuer as a vendor and links
  back to the issuing entity.
