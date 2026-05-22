---
name: ingest-tax-document
description: "Ingest a tax form (W-2, 1099-DIV/INT/B/MISC/NEC, 1098, K-1, 5498, 1095-*, brokerage year-end statement, tax-prep summary) into a structured `tax-document` page under `wiki/tax/{year}/`. Load from the `ingest` skill when content-type routing identifies the source as a tax form (issuer letterhead, prominent form number). Critical: redacts SSNs before writing; the raw PDF stays in `raw/tax/`."
license: MIT
---

# ingest-tax-document

Capture a tax form and place it where end-of-year filing prep will find
it. Forms arrive January-March; some K-1s and amended forms arrive
later. Aggregate per tax year, not per calendar year.

## When you're loaded

The `ingest` skill routes here when:

- A tax-form PDF is dropped (W-2, 1099-DIV/INT/B/MISC/NEC, 1098, K-1,
  5498, 1095-A/B/C, brokerage year-end statement, etc.).
- A tax-software export PDF is dropped (TurboTax, H&R Block summary).
- The user says "save this tax form" / "ingest this 1099" / "track
  this W-2".

## Inputs you'll see

- A cleaned-up tax-form PDF (the `ingest` skill runs Docling for
  layout-aware OCR).
- The tax year — usually printed prominently; ask if ambiguous.
- The recipient — for joint or family filers, ask which family member
  the form is for if it isn't obvious.

You need to extract:

- **`tax_document_year`** — four-digit year as a string ("2025"). String
  rather than int to keep YAML round-trip predictable.
- **`tax_document_form`** — form type: `W-2`, `1099-DIV`, `1099-B`,
  `1099-INT`, `1099-NEC`, `1099-MISC`, `1099-R`, `1098`, `1098-E`,
  `1098-T`, `K-1`, `5498`, `1095-A`, `1095-B`, `1095-C`, or
  `brokerage-statement`.
- **`tax_document_issuer`** — the employer, brokerage, bank, or
  partnership that issued the form. Cross-reference against
  `wiki/vendors/`; stub a vendor page if new.
- **`tax_document_recipient`** — the family member named on the form.
- **`tax_document_amount`** — the headline figure for the form (W-2
  Box 1 wages, 1099-DIV Box 1a ordinary dividends, 1099-B total
  proceeds). String to preserve formatting.

## Page shape

Render from `_templates/tax-document.md`. Filename is
`wiki/tax/{year}/{form-slug}-{issuer-slug}.md`. Example:
`wiki/tax/2025/1099-div-vanguard.md`.

The body captures the key figures from each box (form-specific) and a
**Reconciliation** section for cross-references to brokerage holdings,
employer pages, or prior-year filings.

## SSN and sensitive data — critical

Tax forms include the recipient's SSN in plain text. **Never let an SSN
reach the wiki page.** Redact every SSN-shaped value (`NNN-NN-NNNN`)
from the page body and frontmatter before writing. The raw PDF stays at
`raw/tax/{year}/{form-slug}-{issuer-slug}.pdf` (which should be on the
gitignored side of the vault); the wiki page references it via a
companion link.

If your cleanup output appears to contain an SSN that you can't
confidently redact, **stop and surface the issue to the user** rather
than write a half-redacted page.

## Side-effects

- **Issuer as vendor.** If the issuer is new, stub a vendor page at
  `wiki/vendors/{slug}.md`.
- **Year index.** Append an entry to `wiki/tax/{year}/index.md` (stub
  the index on first form of the year).
- **Holdings cross-reference.** For 1099-B (sales) and 1099-DIV
  (dividends), append a note to the relevant page under
  `wiki/finances/holdings/` if one exists. Surface a contradiction
  callout if the reported figures don't match the user's holdings page.
- **Duplicate / corrected forms.** If a same-issuer same-form already
  exists for the year, surface as either a corrected form (W-2c, 1099
  corrected) or a duplicate; do not silently overwrite.

## When you can't extract cleanly

- **Issuer ambiguous.** A pass-through entity may issue a K-1 under a
  trade name and a tax ID that don't match. Ask.
- **Recipient ambiguous.** Especially for jointly-held brokerage
  accounts. Ask.
- **OCR garbled.** Tax forms have a lot of fine print. Verify the
  headline figure with the user before writing.

## After writing

- Save the raw PDF to `raw/tax/{year}/{slug}.pdf` and create a
  companion page per the kit's asset-management pattern.
- Append a one-line entry to the wiki's running activity.
