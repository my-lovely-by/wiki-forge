---
name: trip-prep
description: "Assemble a per-person packing list and a pre-trip task checklist for an upcoming trip, reading the trip page, family member pages, past trip pages for lessons learned, and any relevant medical pages for medications and special needs. Load when the user says \"prep for the {trip} trip\" / \"what should we pack?\", or when `wiki run trip-prep <trip>` runs you. Writes back to the trip page itself under `## Packing list` and `## Pre-trip tasks` sections — augments rather than creating a separate page. For booking capture use `ingest-trip-doc`."
license: MIT
---

# trip-prep

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run trip-prep <trip>`, and `wiki run` is a stub in
> v2.0.0.dev: it prints `wiki run: not yet implemented (v2 migration
> in progress, see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Two-to-three weeks before a trip, walk the trip page, family member
pages, past trip pages, and medical pages — produce a per-person packing
list and a pre-trip task list, written back to the trip page itself.

## When to load

- The user says "prep for the {trip} trip", "what should we pack for
  {destination}?", or "trip prep".
- `wiki run trip-prep <trip>` runs you with the contract.
- 2-3 weeks before a major trip; 1 week before a shorter one.

If the trip date is already past, refuse — surface that this might have
been intended as a retrospective on a past trip, which is a different
operation.

## Inputs

From the operation contract:

- **`trip`** — the trip page (`wiki/trips/upcoming/<slug>.md`).
- **`theme`** — optional hint: "light packing", "extra-cold weather",
  "kid-friendly only", "Sarah away".

You also read:

- The trip page itself — destination, dates, accommodations, planned
  activities, travelers.
- Each traveler's person page (`wiki/people/<name>.md`) — sizes,
  allergies, preferences if recorded.
- The person's medical page (`wiki/medical/<name>-medical.md`) if it
  exists — medications, EpiPens, special needs.
- Past trip pages under `wiki/trips/past/` — for "what we wish we'd
  brought" notes.
- Reference pages — passport / ID expiration dates, immunization
  records (these may live on the person page).

## Procedure

1. **Read trip context.** Destination, season, expected weather,
   duration, planned activities (hiking, beach, urban).
2. **Per-person packing list.** For each traveler, generate clothing +
   toiletries + medications + activity-specific items.
3. **Shared household items.** Chargers, adapters, first-aid kit, snacks,
   beach gear, etc., as the trip warrants.
4. **Pre-trip task list.** Passport/ID expiration check, mail hold, pet
   care, transportation, eSIM/international plan, currency, in-home
   prep (turn down heating, empty fridge).
5. **Surface lessons from past trips.** For each past trip page that
   matches season or destination, pull "what we wish we'd brought" and
   "what was useless" notes into recommendations.

## Output

Augment the existing trip page (`wiki/trips/upcoming/<slug>.md`) — do
not create a separate page. Add or update these sections:

- **`## Packing list`** — sub-sections per traveler plus "Shared
  household". Use checklists (`- [ ] item`) so the family can tick off
  as they pack.
- **`## Pre-trip tasks`** — checklist with deadlines (book pet boarding
  by …, mail hold by …).

If those sections already exist on the page (e.g. from a prior run),
augment rather than overwrite. The `safe_write` flow protects user
edits; respect them.

## Side-effects

- **Passport expiration.** If any traveler's passport expires within 6
  months of the trip end date, surface as a `> [!danger]` callout on
  the trip page and flag for the `follow-up-tracker`.
- **Pet care, mail hold.** Emit `> [!important] Follow-up due by …`
  callouts for the tasks that have deadlines, so the `follow-up-tracker`
  picks them up.

## When you can't extract cleanly

- **Trip page minimal.** Surface: "trip details are sparse — the more
  populated the trip page, the better the prep. Want to fill in
  accommodations and activities first?"
- **No past trip pages.** Skip the lessons-learned section; produce a
  generic but usable list.
- **Person pages missing.** Default to general categories; suggest
  populating sizes and special-needs on person pages for next time.

## Cadence

- **Manual:** Run 2-3 weeks before each trip.
- **No scheduled cadence:** Trips are episodic. Trigger from the trip
  page when status flips to `upcoming` if integrated.
