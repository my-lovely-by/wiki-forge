---
name: ingest-receipt
description: "Ingest a receipt (photo, PDF, statement entry, emailed receipt) into a structured `receipt` page and route the contents to the right domain — vehicle service history, appliance warranty tracking, vendor service log, tax-relevant expense list. Load from the `ingest` skill when content-type routing identifies the source as a receipt. Stubs vendor pages under `wiki/vendors/` on first reference."
license: MIT
---

# ingest-receipt

Capture a receipt and place it where the household will look later —
warranty record for an appliance, service history for a vehicle, vendor
history for a contractor, tax-relevant flag for a deductible expense.

## When you're loaded

The `ingest` skill routes here when:

- A receipt photo, PDF, or emailed receipt is dropped.
- A statement-entry line is pasted ("Quick Lube Express $89.50 04/15").
- The user says "save this receipt" or "track this expense".

## Inputs you'll see

- A cleaned-up receipt (the `ingest` skill runs source-type cleanup —
  Docling with OCR for photos, defuddle for forwarded emails).
- `wiki/vendors/` — to identify recurring vendors and stub new ones.

You need to extract:

- **`receipt_vendor`** — the merchant or service provider as it should
  appear in `wiki/vendors/`. If new, stub a vendor page.
- **`receipt_date`** — the transaction date (not today).
- **`receipt_amount`** — the total as a string ("$89.50"). String, not
  number, because some receipts carry currency suffixes and partial
  amounts that aren't worth parsing into a typed field for v0.1.
- **`receipt_category`** — short label. Common values: `grocery`,
  `dining`, `vehicle-service`, `appliance`, `home-maintenance`,
  `medical`, `travel`, `tax-deductible`.

## Page shape

Render from `_templates/receipt.md`. Filename is
`wiki/receipts/YYYY-MM-DD-{vendor-slug}.md`. The page itself is the
durable record; cross-reference it from the relevant domain page.

## Routing the contents

The receipt page is the source of truth, but the *useful* signal often
belongs somewhere else:

- **Vehicle service.** Append a dated entry to
  `wiki/vehicles/{vehicle}-service-history.md` (or stub it if missing)
  with mileage, items, and next-service window. Surface as a
  `> [!important] Follow-up due by …` callout for the `follow-up-tracker`.
- **Appliance purchase.** Update `wiki/home/appliances.md` (or create
  it) with warranty period, purchase date, and a wikilink to the
  receipt page.
- **Tax-relevant.** Tag charitable donations, medical expenses,
  business expenses, and similar. The `tax-document` content-type owns
  year-end aggregation; here you just flag the receipt.
- **Recurring vendor.** If the receipt is the third+ transaction with
  the same vendor, surface that the vendor page might deserve more
  detail (account number, contact, service history summary).

## Side-effects

- **Vendor pages.** Stub new vendors at `wiki/vendors/{slug}.md` with
  `status: active` and `provenance: synthesized`; cross-reference from
  the receipt.
- **Companion page.** If the original was a PDF or image worth keeping
  for warranty or tax purposes, follow the kit's asset companion-page
  pattern (`AGENTS.md` describes it).
- **Follow-ups.** Service receipts often imply a next-service date —
  emit a `> [!important] Follow-up due by …` callout so the
  `follow-up-tracker` can pick it up.

## When you can't extract cleanly

- **OCR garbled.** Surface the extracted vendor, amount, and date;
  confirm with the user before writing.
- **Category ambiguous.** A hardware-store receipt could be home repair
  or a kid's project — ask.
- **Multi-domain receipt** (Costco run with groceries, vehicle supplies,
  and home goods). Either decompose into multiple receipt pages or save
  one page with cross-references into each affected domain.

## After writing

- Save the raw receipt to `raw/receipts/{YYYY-MM-DD}-{vendor-slug}.md`
  (or as a PDF/image with companion page).
- Append a one-line entry to the wiki's running activity.
