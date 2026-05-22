# tax/

One folder per tax year. Inside each year folder, one page per form
received — W-2s, 1099s, 1098s, K-1s, brokerage year-end statements,
tax-prep summaries.

## Conventions

- **Year folders.** `wiki/tax/2024/`, `wiki/tax/2025/`. Forms for tax
  year 2025 land in `2025/`, regardless of when they physically arrive
  (some K-1s and amendments slip into the next calendar year).
- **Filename:** `<form-slug>-<issuer-slug>.md` — e.g.
  `1099-div-vanguard.md`, `w-2-acme-corp.md`. Multiple forms from the
  same issuer in one year get a `-2`, `-3` suffix.
- **SSNs are never written here.** The `ingest-tax-document` skill
  redacts SSN-shaped values from every page; the raw PDF in
  `raw/tax/{year}/` (gitignored) is the source of truth for fields the
  wiki must not carry.
- **Index per year.** Each year folder has an `index.md` listing the
  forms received, expected forms still outstanding, and reconciliation
  notes. The ingester appends; the user curates.

## Created by other primitives

- `ingest-tax-document` writes one page per form, redacts SSNs, and
  stubs the issuer as a vendor under `wiki/vendors/`.
- For year-end aggregation, run a manual reconciliation pass against
  the year's `index.md`.
