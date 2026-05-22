# receipts/

One page per receipt the household wants to keep — warranty proof,
service history, tax-relevant expense, recurring vendor record.

## Conventions

- **Filename:** `YYYY-MM-DD-<vendor-slug>.md`. Date is the transaction
  date, not the ingest date.
- **Routing.** The receipt page is the durable record, but the *useful*
  signal often belongs elsewhere — vehicle service history under
  `wiki/vehicles/`, appliance warranty under `wiki/home/`, vendor
  activity under `wiki/vendors/`. The `ingest-receipt` skill cross-
  references both directions.
- **Raw PDFs.** Originals live at `raw/receipts/`. If you keep the
  binary for warranty or tax purposes, follow the kit's asset
  companion-page pattern.

## Created by other primitives

- `ingest-receipt` writes new pages from photos, PDFs, statement
  entries, or pasted text.
- The `follow-up-tracker` operation reads service-receipt callouts to
  surface next-service reminders.
