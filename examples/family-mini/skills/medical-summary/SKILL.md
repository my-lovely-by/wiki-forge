---
name: medical-summary
description: "Produce a current-state medical summary for a person — active conditions, current medications, allergies, recent visits, outstanding follow-ups — suitable for a doctor visit, school form, or emergency reference. Load before a provider visit (especially a new specialist), for annual physical prep, when a form requires medical info, or via `wiki run medical-summary <person>`. Writes one versioned page per run; pairs with the `medical-record` ingester (which produces the input data) and the `follow-up-tracker` (which surfaces rechecks)."
license: MIT
---

# medical-summary

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run medical-summary <person>`, and `wiki run` is a stub in
> v2.0.0.dev: it prints `wiki run: not yet implemented (v2 migration
> in progress, see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Synthesizing operation. Walk a person's medical history and produce one
durable summary suitable for sharing with a provider, attaching to a
school form, or pulling up in an emergency. Tailor the emphasis to the
visit context — a dentist summary leads with bleeding risk and
medications affecting dental procedures; an ER summary leads with
allergies and chronic conditions.

## When to load

- Before a doctor visit, especially with a new specialist.
- Annual physical prep.
- When a school or camp requires a medical form.
- After an emergency, to capture what the family told the ER.
- `wiki run medical-summary <person>` runs you with the contract.

If the user wants an *aggregate* summary across the household (e.g. for
a babysitter), produce one summary per person rather than a fused
document — the per-person pages are easier to update independently.

## Inputs

From the operation contract:

- **`person`** — canonical name as it appears in `wiki/people/`. Match
  is case-sensitive; ask the user if ambiguous.
- **`context`** — tailoring hint: `annual-physical`, `new-specialist`,
  `school-form`, `er-intake`, `dental-visit`, …
- **`history_months`** — visit-history window. Defaults to 12 months.

You also read:

- `wiki/people/<person>.md` — basics: DOB, blood type, primary care.
- `wiki/medical/<person>-medical.md` — chronological visit history.
- `wiki/medical/medications.md` — filtered to this person.
- `wiki/medical/providers.md` — for follow-up coordination.
- `wiki/medical/insurance.md` — plan name, member ID, key contacts.
- Recent `medical-record` pages under `wiki/medical/records/` whose
  `medical_record_person` matches.

## Procedure

1. **Compile core medical identity.** Name, DOB, blood type if known,
   allergies, chronic conditions.
2. **Active medications.** Current prescriptions with dose, frequency,
   prescriber. Mark discontinued ones excluded (don't show
   discontinued unless context warrants — e.g. drug allergies).
3. **Recent visits.** Within `history_months`, dated, concise — one
   line per visit with provider + reason + outcome.
4. **Outstanding follow-ups.** Pull from the person's medical page
   callouts; include due date and provider.
5. **Insurance details.** Include where context warrants (ER summary
   yes, dentist summary maybe; annual physical summary not usually).
6. **Format for context.** Tailor section order and emphasis to
   `context`.

## Output

Write `outputs/medical-summaries/<person-slug>-<YYYY-MM-DD>.md`.
Versioned — previous summaries are marked outdated in their frontmatter
(`status: archived`) but not deleted; the user can compare two summaries
to see what changed between visits.

Frontmatter:

```yaml
type: medical-summary
status: active
provenance: synthesized
created: <today>
modified: <today>
tags: [medical-summary, <person-slug>]
medical_summary_person: <person>
medical_summary_context: <context>
medical_summary_history_months: <history_months>
```

Sections (reorder per context):

- **Synopsis** — 1-2 sentences the provider can read in 5 seconds.
- **Identity** — name, DOB, blood type, primary care.
- **Active conditions** — chronic diagnoses with date of diagnosis.
- **Current medications** — drug, dose, frequency, prescriber.
- **Allergies** — substance + reaction severity (especially for ER).
- **Recent visits** — last `history_months`, dated, with provider,
  reason, outcome.
- **Outstanding follow-ups** — what's due, by when, with whom.
- **Emergency contacts** — primary care, specialists, insurance member
  services.
- **Notes for this visit** — context-specific items.

The `medical-summary` type may not yet be in
`frontmatter.schema.yaml`'s managed types region; `wiki-lint` flags it
as a gap.

## Sensitive data

This page contains the person's medical history. Treat it accordingly:

- Keep `outputs/medical-summaries/` on the gitignored side of the
  vault if you sync to a hosted git remote.
- Do not include SSN, member ID, or account numbers in the page body
  unless the visit context explicitly requires it — and even then,
  prefer the `insurance.md` page as the source of truth and link to it.

## When you can't produce a meaningful summary

- **Person page missing.** Refuse; ask the user to create one first.
- **Medical history sparse.** Produce what you can and flag the gaps
  — e.g. "summary covers the last 6 months only; no earlier records
  ingested."
- **Conflicting medication entries.** Surface as a contradiction;
  refuse to produce a summary that may misrepresent meds to a
  prescriber.
- **DOB missing.** Block — DOB is required for any medical-summary
  context.

## Cadence

- **Manual:** Before each visit or when a form requires one.
- **Annually:** Refresh as part of the annual physical cycle.
- **No scheduled cadence:** Visit-driven, not calendar-driven.
