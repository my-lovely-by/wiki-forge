# trips/

One page per trip — upcoming, active, or past. A trip page accumulates
booking confirmations, an itinerary, planned activities, and the packing
and pre-trip task list that `trip-prep` writes 2-3 weeks ahead of
departure.

## Conventions

- **Filename:** `YYYY-MM-DD-<destination-slug>.md` where the date is the
  trip's start date. Subfolders `upcoming/` and `past/` keep the
  current-state view fast.
- **Status as a property, not a folder.** `status: upcoming | active |
  past` lives in frontmatter. Move the file between `upcoming/` and
  `past/` when the trip ends — both the file move and the status flip
  are journaled.
- **Travelers as wikilinks.** Each name in `trip_travelers` resolves
  to a page under `wiki/people/`. The `trip-doc` ingester stubs new
  people pages on first reference.

## What goes on a trip page

- Synopsis, dates, destination, travelers.
- Bookings (flights, hotels, rentals) with confirmation numbers.
- Itinerary day-by-day, populated as more bookings arrive.
- Packing list + pre-trip tasks (filled in by `trip-prep`).
- Cross-references to past trips to similar destinations.

## Created by other primitives

- `trip-doc` ingester captures booking confirmations and starts or
  appends to a trip page.
- `trip-prep` operation reads the populated trip page and writes the
  packing list and pre-trip task list back to the same page.
