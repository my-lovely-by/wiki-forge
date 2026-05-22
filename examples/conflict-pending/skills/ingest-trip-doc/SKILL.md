---
name: ingest-trip-doc
description: "Ingest a trip booking confirmation (hotel, flight, rental car, activity reservation) into a structured `trip-doc` page under `wiki/trips/upcoming/`. Load from the `ingest` skill when content-type routing identifies the source as a booking (confirmation email, PDF, or web page). Aggregates: a single trip typically gets several runs — each new booking appends to the same trip page. Pairs with the `trip-prep` operation (which writes packing + pre-departure tasks back to the same page 2-3 weeks before departure)."
license: MIT
---

# ingest-trip-doc

Capture a booking confirmation and start or extend a trip page. One
trip page accumulates all the bookings for that trip; expect to be run
several times for a single trip as confirmations arrive.

## When you're loaded

The `ingest` skill routes here when:

- A booking confirmation arrives — email, PDF, hotel/airline web page.
- The user says "save this trip booking" or "track this reservation".
- A new booking for an existing trip is dropped (aggregation case).

If the booking dates have already passed, assume the document belongs
to a past trip and write to `wiki/trips/past/` instead of `upcoming/`.

## Inputs you'll see

- A cleaned-up booking confirmation (the `ingest` skill runs source-type
  cleanup first — Docling for PDFs, defuddle for booking-site URLs, paste
  handling for forwarded emails).
- Existing pages under `wiki/trips/upcoming/` — to detect whether this
  booking belongs to an existing trip.

You need to extract:

- **`trip_destination`** — city, region, or short label ("Vermont",
  "Tokyo trip 2026").
- **`trip_start_date`** / **`trip_end_date`** — the trip envelope, not
  the booking's check-in/check-out (a hotel might be one slice of a
  longer trip).
- **`trip_travelers`** — names from the booking; default to the whole
  household if ambiguous. Stub missing person pages under
  `wiki/people/` on first reference.
- **`trip_status`** — `upcoming`, `active`, or `past`. Move the file
  between subfolders when status changes.

## Page shape

Render from `_templates/trip-doc.md`. Filename is
`YYYY-MM-DD-<destination-slug>.md` where the date is `trip_start_date`.
Place under `wiki/trips/upcoming/` (or `past/` for retrospective entries).

If a trip page already exists for these dates and destination, **append
the booking to the existing page** rather than creating a new one. The
Bookings section accumulates one entry per confirmation; the Itinerary
section grows day-by-day.

## Side-effects

- **People stubs.** Each new traveler name without an existing person
  page gets stubbed under `wiki/people/` with `status: draft` and
  `provenance: synthesized`.
- **Trip-prep readiness.** If the trip starts within three weeks,
  mention to the user that `wiki run trip-prep` is now useful.
- **International travel.** If the destination is outside the user's
  country, surface "check passport expiration" as a flag — passports
  typically need 6+ months validity at entry.

## When you can't extract cleanly

- **Travelers ambiguous.** Default to the household but surface the
  guess; if a subset is going, capture that explicitly.
- **Booking conflicts with existing trip.** Overlapping dates but a
  different destination — could be a side-trip, a typo, or a new trip.
  Ask.
- **Confirmation number missing.** Save what you have; flag the gap.

## After writing

- Save the raw confirmation to `raw/trips/{YYYY-MM-DD}-{slug}.md` (or
  as a PDF companion). Companion-page pattern from the kit's `AGENTS.md`.
- Append a one-line entry to the wiki's running activity.
- Pairs with `trip-prep`: closer to departure, the operation reads the
  populated trip page and writes the packing list and pre-trip task
  list back.
