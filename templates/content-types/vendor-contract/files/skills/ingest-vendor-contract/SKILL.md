---
name: ingest-vendor-contract
description: "Ingest a vendor-contract source (signed contract PDF text, renewal notice, procurement summary, or order form) into a structured vendor-contract page. Load from the `ingest` skill when content-type routing identifies the source as a vendor contract — typically a counterparty name, a term window, an amount, and signed/effective dates. Produces one page under `wiki/vendor-contracts/` and registers the contract for downstream operations (renewal-reminders)."
license: MIT
---

# ingest-vendor-contract

Convert a vendor contract — a signed SaaS agreement, a renewal notice,
an order form, or a procurement summary — into a clean wiki page. The
user pastes terms, drops a PDF excerpt, or hands you a procurement
ticket; your job is to produce one durable contract page and the
linked-owner stub it needs.

## When you're loaded

The `ingest` skill routes here after it has classified the source as a
vendor contract. You can also be loaded directly when the user says
"log this vendor contract" or "we just renewed Datadog."

If the source is ambiguous (could be a vendor contract, could be a
customer contract), check before assuming. A vendor contract is one
where *you* are the customer; the counterparty is the vendor.

## Inputs you'll see

- A pasted contract section (Term, Pricing, Renewal).
- A renewal notice email from the vendor.
- A procurement-ticket summary with the negotiated terms.
- An order form (often the cleanest source for amounts and dates).

For each, extract:

- **`contract_vendor`** — the legal name of the counterparty, as it
  appears on the contract. Plain string; vendors do not live in
  `wiki/customers/` and the work-os recipe has no `vendors` ontology
  in v0.1 (a future recipe may add one).
- **`contract_start`** — the effective start date.
- **`contract_end`** — the end date or `null` if the contract is
  open-ended / month-to-month. Don't guess.
- **`contract_renewal_date`** — the date by which the user must decide
  to renew or cancel. Often `contract_end - <notice period>`; if the
  contract names this explicitly, use it; otherwise leave empty and
  flag it for the user.
- **`contract_amount`** — keep as a string with currency symbol and
  period (`"$24,000 / year"`, `"€500 / month"`). Don't strip units to
  a number — the next reader needs to know whether $24k is annual or
  per-seat-per-year.
- **`contract_owner`** — the internal DRI. Wikilink to `wiki/people/`.
- **`contract_terms_summary`** — short bullets covering non-standard
  clauses, auto-renewal language, notice periods, data-handling
  commitments. Skip the boilerplate.

## Page shape

Render the page from `_templates/vendor-contract.md`. The filename
convention is `wiki/vendor-contracts/<vendor>-<YYYY>.md`, where
`<vendor>` is the kebab-case vendor name and `<YYYY>` is the start
year. For multi-year contracts, the start year is sufficient; a
renewal gets a new page with the new start year.

## Owner linking

The `contract_owner` field must resolve to a page under `wiki/people/`:

1. Search `wiki/people/` for an existing page.
2. If a match exists, use its wikilink.
3. If no match, stub a new person page with `type: person`,
   `status: draft`, `provenance: synthesized`, and a one-line note
   "First seen in `[[vendor-contracts/<this-contract>]]`." Wikilink to
   the stub.

## Renewal dates are first-class

The `renewal-reminders` operation walks every vendor-contract page and
surfaces those whose `contract_renewal_date` falls inside a configured
look-ahead window. Get this date right — a missed auto-renewal is the
specific failure mode this primitive exists to prevent.

When the contract has auto-renewal and a notice period:

- `contract_end` is the term's nominal end.
- `contract_renewal_date` is `contract_end - notice_period` (when the
  decision must be *communicated*, not when the term ends).

Add a bullet to `contract_terms_summary` naming the auto-renewal
behaviour explicitly so a reader doesn't have to compute it.

## When the source is thin

A one-line "we renewed Datadog at the same rate" is a perfectly valid
input. Produce the page with whatever you can extract, mark
`provenance: extracted`, and add a TODO at the top of the body listing
the missing fields. A thin honest page beats a fabricated one.

## After writing

- Append a one-line summary to the running activity log.
- If `contract_renewal_date` is within 90 days, remind the user that
  `wiki run renewal-reminders` will surface it on the next sweep.
- If the contract is open-ended with no `contract_end`, flag it: those
  are the contracts that quietly accrue cost.
