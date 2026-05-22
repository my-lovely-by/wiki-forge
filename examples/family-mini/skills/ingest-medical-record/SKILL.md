---
name: ingest-medical-record
description: "Ingest a medical document (visit summary, EOB, lab result, prescription note) into a structured `medical-record` page, append a dated entry to the per-person medical page, and update medications and providers. Load from the `ingest` skill when content-type routing identifies the source as medical (clinic or insurer letterhead, EOB layout, lab-result format). Pairs with the `medical-summary` operation (visit prep) and `follow-up-tracker` (recheck reminders)."
license: MIT
---

# ingest-medical-record

Capture a medical document and append it to the household's record in a
way the `medical-summary` and `follow-up-tracker` operations can use.

## When you're loaded

The `ingest` skill routes here when:

- A medical PDF or photo is dropped (visit summary, EOB, lab result,
  prescription note).
- A patient-portal screenshot or PDF is provided.
- A summary from a visit is pasted.
- The user says "ingest this medical record" or "save this visit".

If the patient isn't obvious from the document, ask before assigning.
A misfiled medical record is much worse than a slow ingest.

## Inputs you'll see

- A cleaned-up medical document (the `ingest` skill runs source-type
  cleanup first — Docling for PDFs and photos, paste handling for text).
- The person whose record this is — confirm against `wiki/people/`.

You need to extract:

- **`medical_record_person`** — the patient's canonical name as it
  appears in `wiki/people/`. If no person page exists, stub one.
- **`medical_record_date`** — the date of service (not today). EOBs
  sometimes span multiple visits; if so, ask the user to pick one.
- **`medical_record_provider`** — the provider name and (where known)
  the practice. Add the provider to `wiki/medical/providers.md` if new.
- **`medical_record_kind`** — short label: `visit-summary`, `eob`,
  `lab-result`, `prescription`, `vaccination`, etc.

## Page shape

Render from `_templates/medical-record.md`. Filename is
`wiki/medical/records/YYYY-MM-DD-{person-slug}-{kind}.md` (e.g.
`2026-04-15-jake-doe-visit-summary.md`). Then **also append a dated
entry to the per-person medical page** at
`wiki/medical/{person-slug}-medical.md` (reverse-chronological — newest
at the top) so the summary skill has one place to read from.

## Side-effects

- **Medications.** If the document mentions a new prescription, a dose
  change, or a discontinuation, update `wiki/medical/medications.md`.
  Surface the change before writing; do not silently overwrite an
  existing medication entry that conflicts. Use a `> [!danger]
  Contradiction` callout on a conflict.
- **Providers.** Add new providers to `wiki/medical/providers.md` with
  their name, practice, phone, and the date you first saw them.
- **Follow-ups.** Every recheck or next-visit note becomes a callout on
  the per-person medical page:
  ```markdown
  > [!important] Follow-up due by 2026-10-15
  > Allergy panel recheck (Dr. Chen, Riverdale Pediatrics).
  ```
  The `follow-up-tracker` operation scans for these.

## Sensitive data

Medical documents often include SSNs, account numbers, and member IDs.
**Never propagate any of these into a wiki page.** Redact them on the
way in; the raw PDF stays in `raw/medical/` (which should be on the
gitignored side of the vault). If you find that a value slipped through
into the page text, surface it and stop before writing.

## When you can't extract cleanly

- **OCR garbled.** Surface the extracted text; ask the user to confirm
  date, provider, diagnoses, and prescriptions before writing.
- **Patient ambiguous.** Multiple family members are plausible — ask.
- **Conflicting prescription data.** Surface as a contradiction; let
  the user reconcile.
- **No clear date of service.** Ask the user.

## After writing

- Append a one-line entry to the wiki's running activity.
- If the person hasn't had a `medical-summary` produced in the last six
  months, mention that one is due — `wiki run medical-summary` reads
  exactly what you just wrote.
