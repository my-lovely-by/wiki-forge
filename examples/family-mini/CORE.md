# CORE.md — what this wiki *is*

> This vault was created with the `family` recipe. The name on
> disk is `family-mini`.

## Purpose

An LLM-maintained markdown wiki. The user drops raw material into `raw/`
(or pastes/links/uploads to an agent that calls `wiki ingest`). The
agent turns that material into structured pages under `wiki/`, links
them to neighboring pages, and keeps the corpus current. Periodic
operations (digests, follow-ups, reviews) read across the wiki and write
summaries to `outputs/`.

The wiki is the source of truth for what the user knows about their
world. The journal at `.wiki.journal/journal.jsonl` is the source of
truth for what *the kit* did to the wiki.

## Folder layout

| Folder            | Purpose                                                                 |
|-------------------|-------------------------------------------------------------------------|
| `wiki/`           | The synthesized knowledge base. Every page has frontmatter and wikilinks. |
| `raw/`            | Untouched sources — PDFs, transcripts, clippings, screenshots.          |
| `outputs/`        | Generated artifacts — digests, summaries, exports. Regenerable.         |
| `log/`            | Agent-written changelog and lint reports.                               |
| `skills/`         | Skill packages the agent loads. Read-only from the user's perspective.  |
| `.wiki.journal/`  | The kit's append-only event log. Do not hand-edit.                      |

Ontology primitives drop additional folders under `wiki/` (`people/`,
`projects/`, `food/`, etc.) when installed.

## The pages

Every wiki page is a markdown file with YAML frontmatter. The minimum
frontmatter contract is in `frontmatter.schema.yaml`; content-type
primitives extend it via managed regions in that file.

Three properties matter for every page:

- **type** — what kind of page this is (meeting, recipe, person,
  decision, …). Determined by the ingester at write time.
- **status** — where this page is in its lifecycle (active, archived,
  draft, …).
- **provenance** — `extracted`, `synthesized`, or `mixed`. Tells future
  readers (and future agents) how much of this page is verbatim from a
  source vs. agent inference.

Pages link to each other with `[[wikilinks]]`. The graph the links form
is the wiki — folder structure is convenience, not contract.

## Three layers of write safety

The kit never overwrites a file the user has edited without warning.
Every write goes through one of three paths:

1. **No prior write** — write directly (a new page).
2. **Prior write, no drift** — write directly (the on-disk hash matches
   what the journal recorded).
3. **Drift detected** — write to `<path>.proposed`. The user resolves
   via the `wiki-conflict` skill.

For shared infrastructure files (`AGENTS.md`, `frontmatter.schema.yaml`,
…) drift detection is scoped to the managed region, not the whole file:
edits outside the markers always survive.

## What changes vs. what doesn't

You (the agent) write to `wiki/`, `raw/`, `outputs/`, and `log/` freely.
You do not write to `.wiki.journal/` — the kit owns that. You do not
write to `skills/<name>/SKILL.md` files at runtime — they are part of
the installed primitive and are managed by `wiki upgrade`.

When a user asks for a new skill or a new content type, propose adding
the corresponding primitive rather than dropping a one-off SKILL.md into
the vault. Primitives upgrade together; one-off skills drift.
