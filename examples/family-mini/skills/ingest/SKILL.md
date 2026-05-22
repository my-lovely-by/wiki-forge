---
name: ingest
description: "Unified entry point for ingesting any source (paste, URL, file, photo, transcript) into the vault. Routes on two axes — source-type (clean the input via the document/website/inline path) and content-type (apply schema and routing via a content-type primitive's ingester). Use whenever a user drops, pastes, or links any source for ingestion."
license: MIT
---

# Ingest

This is the routing skill. Most ingests compose two specialized steps —
**source-type** (clean the input) and **content-type** (apply schema,
decide where the page lands) — and end with the shared validation +
write flow.

## When to load this skill

Load it whenever a user is putting *anything* into the vault that isn't
already a finished wiki page. Examples:

- "Save this recipe / article / paper: <URL>"
- "Process the PDF I dropped in `raw/`"
- "Here's the transcript from today's standup" (paste)
- "Add this person" (LinkedIn URL, vCard, business card photo)
- "Pull in all the clippings in `Clippings/`"

If the user just wants to read or query content already in `wiki/`,
load `wiki-search` instead.

## The kit-side route step (`wiki ingest`)

When the user runs `wiki ingest <source>` from a shell, the CLI does
deterministic routing on filename, extension, URL host, and URL path
(plus `--as <name>` for an explicit override), and appends an
`ingest.routed` event to the journal. Three outcomes:

| Outcome | Exit | What it means |
|---|---|---|
| single match | 0 | One content-type primitive's `routing:` rules fired. Load `skills/ingest-<name>/SKILL.md` and run its synthesis flow. |
| ambiguous | 2 | Two or more primitives' rules matched. The CLI refuses to pick. Re-run with `--as <name>` after deciding. |
| no match | 2 | Nothing claimed the source. Re-run with `--as <name>`, or capture the source manually. |

See the latest decision any time with `wiki journal tail -n 5`. The
kit's CLI never fetches the URL, parses the PDF, or invokes you — that
content-type SKILL.md does, once the route is in the journal.

When the user just *describes* a source in chat (no shell), there is no
journaled route. Follow the detection guidance below to pick the
content-type yourself, then run the same shared flow.

## The two axes

```
Source arrives (paste, URL, file, photo)
        │
        ▼
  ┌────────────────────────┐
  │  Source-type ingester  │   "Clean it up."
  │                        │
  │  website   (URL)       │   URL  → fetch + strip to clean markdown
  │  document  (file)      │   PDF / DOCX / image → text extraction
  │  inline    (text)      │   text → direct
  └─────────┬──────────────┘
            ▼
       clean markdown
            │
            ▼
  ┌────────────────────────┐
  │ Content-type ingester  │   "Apply schema, route."
  │                        │
  │  recipe                │   wiki/food/{slug}.md
  │  meeting               │   wiki/meetings/{date}-{slug}.md
  │  medical-record        │   wiki/health/...
  │  receipt               │   wiki/finances/...
  │  person                │   wiki/people/{name}.md
  │  ... (recipe-specific) │
  └─────────┬──────────────┘
            ▼
     structured wiki page
            │
            ▼
  ┌────────────────────────┐
  │  Shared flow           │
  │  - scope check         │   (does this belong in the wiki?)
  │  - contradiction check │   (does it conflict with existing pages?)
  │  - write page          │   (via `safe_write` — drift-protected)
  │  - extract tasks/facts │
  │  - log to changelog    │
  └────────────────────────┘
```

Source-type handlers come from the core primitive. Content-type
ingesters come from content-type primitives the recipe installs — each
ships its own `skills/<name>/SKILL.md`. Load that skill's instructions
once you've routed to it.

## Source-type detection

| Signal                                              | Source-type    |
|-----------------------------------------------------|----------------|
| URL to a web page                                   | website        |
| File: `.pdf`, `.docx`, `.pptx`, `.xlsx`, image      | document       |
| File already in `raw/`                              | already clean — go straight to content-type |
| File in `Clippings/` (Obsidian Web Clipper inbox)   | already clean — go straight to content-type, then relocate to `raw/web-clips/` |
| Pasted text starting with `http(s)://`              | website        |
| Pasted text (no URL)                                | inline         |

## Content-type detection

When the user states the content type ("ingest this *recipe*"), route
directly to that content-type ingester. When the type is ambiguous,
clean the source first, then **ask the user** before writing a page —
silent default routing is the most common way ingest goes wrong.

The content-type ingesters available to you depend on the primitives
installed in this vault. List them with:

```
ls skills/ | grep -v '^wiki-' | grep -v '^ingest$'
```

For each candidate, read its SKILL.md `description` and route by intent.

## The shared flow

After the content-type ingester returns a structured page, *every*
ingest runs these steps:

1. **Scope check.** Does this page belong in the wiki? If it's outside
   the user's stated scope (see their personal/project docs), surface
   that and ask before writing.
2. **Contradiction check.** Search the vault for pages making
   conflicting claims about the same entity / decision. Use
   `wiki-search` with a tight query. If found, surface the conflict
   and ask whether to update the old page, merge, or note divergence.
3. **Write.** The content-type ingester produces the structured page
   body; emit it through the kit's drift-protected write path so the
   journal records the page. `wiki ingest <source>` only handles the
   *routing* decision (see the kit-side route step above) — the page
   write itself happens via the same `safe_write`-backed path every
   primitive uses.
4. **Extract facts / tasks.** Anything the page asserts about a person,
   project, or domain should propagate: pull facts into the relevant
   wiki pages, push action items into the appropriate tasks list.
5. **Update the changelog.** Append a line to `log/changelog.md` in
   the form `YYYY-MM-DD HH:MM <by> ingest <path>` so the user can see
   what happened.

## Web Clipper inbox

The Obsidian Web Clipper defaults to saving at `Clippings/<title>.md`.
The vault treats this folder as a **transient inbox**:

1. Clippings are already clean — skip source-type cleanup.
2. Route to content-type detection like any other source.
3. After the page is written successfully, move
   `Clippings/<title>.md` to `raw/web-clips/YYYY-MM-DD-<slug>.md`
   (using the clipping's `fetched_at` or file mtime). The page's
   source footnote uses the post-relocation path.
4. On failure / deferral / rejection, **leave the file in place**.
   Never delete an unprocessed clipping.

"Process my clippings inbox" iterates every file in `Clippings/`,
surfaces a routing plan per file, and processes after confirmation.

## When to ask, when to act

Ask before writing when:

- The content type isn't obvious from the source.
- The proposed page conflicts with an existing page.
- The source is outside the user's stated wiki scope.
- The user has not asked you to act yet — they may have pasted to
  *show* you, not to ingest.

Act without asking when:

- The user explicitly named the content type.
- The page is new, scoped, and non-conflicting.
- The source is a clipping in the inbox (the user already opted in by
  clipping it).

## Anti-patterns

- Don't write to `wiki/` outside the `wiki ingest` flow. The journal
  is the source of truth for what's in the vault, and a hand-written
  page bypasses drift detection.
- Don't fan-out fact extraction without a sanity check. If a single
  ingest would touch ten pages, ask first.
- Don't merge contradictions silently. The user owns those calls.
- Don't delete files in `raw/` or `Clippings/`. The kit's safety rule
  is that ingest **relocates**; deletion requires explicit confirmation.
