# AGENTS.md — family-mini

> This file is the canonical contract for any agent (Claude, Codex, Cursor,
> Copilot) working inside this vault. `CLAUDE.md` is a symlink to it.
> The wiki was created with the `family` recipe.

## What this vault is

An LLM-maintained markdown wiki. You ingest raw sources (paste, URL, file,
photo, transcript), synthesize them into structured pages, and keep the
knowledge base current, cross-linked, and navigable. Pages live under
`wiki/`. Sources live under `raw/`. Operations (digests, summaries,
follow-ups) write to `outputs/`.

The wiki is **Obsidian-compatible**. Pages use `[[wikilinks]]`, YAML
frontmatter, and Obsidian Templater syntax (`{date}`, `{title}`) inside
templates. Open the folder in Obsidian or read it as plain markdown — both
work.

## The journal — read-only

Every state-changing thing the kit does (a page write, a managed-region
update, an ingest, an operation run) is recorded in
`.wiki.journal/journal.jsonl` before it touches disk. The journal is how
the kit answers four questions:

- What's installed in this vault?
- Has this source already been ingested?
- Did the user edit a kit-written file since the last write?
- Did this operation already run for this period?

**Do not hand-edit `.wiki.journal/`.** If a journal line is wrong, run
`wiki doctor` (see `skills/wiki-doctor/`). If the kit and disk disagree
about a file's contents, the kit writes a `<path>.proposed` sidecar
rather than clobbering your edits — load `skills/wiki-conflict/` and walk
the merge.

## Available skills

This vault ships with seven baseline skills. Load the SKILL.md and follow
it — don't reinvent the workflow. Each lives under `skills/<name>/`.

- **`ingest`** — unified entry point for any source. Detects source-type
  (URL, document, transcript, paste) and content-type (recipe, meeting,
  receipt, etc.), then routes. Use this whenever a user drops or pastes
  anything for ingestion.
- **`wiki-search`** — search the vault by content and frontmatter
  (`--type`, `--tag`, `--status`). Prefer this over generic Grep for
  questions about *vault content* — it returns ranked pages with
  synopses, not just raw matches.
- **`wiki-lock`** — coordinate concurrent writes. Acquire the lock before
  starting a multi-file operation (a digest, a re-sync, a bulk ingest)
  so two agents working on the vault at the same time don't trample
  each other.
- **`wiki-lint`** — health checks: broken wikilinks, orphan pages,
  missing frontmatter fields, stale `modified` dates, synonym tag pairs.
  Run on request ("lint the wiki") or after bulk ingestion.
- **`wiki-conflict`** — resolve `<path>.proposed` sidecars. The kit
  writes a sidecar when it tried to update a file you'd edited; this
  skill walks you through reconciling the two and commits the merge
  back through `wiki resolve`.
- **`wiki-doctor`** — validate vault state against the journal. Reports
  drift, orphan files (on disk but not in the journal), missing files
  (in the journal but not on disk), and managed-region damage.
- **`wiki-research`** — dispatch a research query to a configured HTTP
  provider (Perplexity, Gemini Deep Research, or Semantic Scholar) via
  `wiki research`. Load whenever the user asks for external information
  the vault doesn't already contain, and before invoking the CLI — the
  skill teaches the provider picker and the Two-Source Rule for
  load-bearing claims. Providers are opt-in; install with
  `wiki add infrastructure:research-<name>`.

Additional skills (per-content-type ingesters, operation skills) are
installed by the primitives this recipe brings in. They live alongside
the baseline skills under `skills/`.

## Conventions

### File naming

- File names use **kebab-case**: `2026-05-15-quarterly-review.md`.
- Dates are **ISO 8601**: `2026-05-15`.
- Tags are kebab-case: `#project-management`, `#follow-up`.
- Internal links use Obsidian wikilink syntax: `[[note-name]]`.
- Asset embeds: `![[filename.ext]]`.

### Frontmatter

Every wiki page **must** have YAML frontmatter with at minimum:

```yaml
---
type: <page-type>
status: <status>
provenance: extracted | synthesized | mixed
created: YYYY-MM-DD
modified: YYYY-MM-DD
tags: [tag1, tag2]
---
```

Valid `type` and `status` values come from `frontmatter.schema.yaml`.
Content-type primitives extend the schema via managed regions in that
file. Never strip frontmatter from an existing note. When you edit a
page, update `modified` to today's date.

### Provenance

- `extracted` — content lifted verbatim from a source (a clipped article,
  a transcript). Cite the source in a footnote at the bottom of the page.
- `synthesized` — content you wrote based on multiple sources or prior
  pages. Link the inputs as `[[wikilinks]]`.
- `mixed` — both. Default for most ingested pages — extraction plus your
  framing.

### Wikilinks

Prefer `[[note-name]]` over file paths. If a target doesn't exist yet,
the link is "unresolved" — that's fine; `wiki-lint` surfaces unresolved
links periodically so they can be promoted to stub pages or fixed.

## Managed regions — the kit owns the inside

Shared infrastructure files (`AGENTS.md`, `frontmatter.schema.yaml`,
`.gitignore`, and a handful more depending on recipe) contain blocks
like:

```
<!-- BEGIN MANAGED: content-types -->
... kit-generated content ...
<!-- END MANAGED: content-types -->
```

The kit owns the content **between** the markers. Your edits **outside**
the markers survive untouched; your edits **inside** the markers trigger
a `.proposed` sidecar on the next `wiki upgrade` (the kit doesn't
clobber, but it also doesn't merge — load `wiki-conflict`).

If you genuinely need to change kit-managed content, edit the primitive
that produces it (or contribute a new one) rather than the rendered
output.

## Operations

The `wiki` CLI is the kit's surface. The user runs it; you can suggest
the right invocation in chat.

- `wiki ingest <source>` — route a source through the right ingester
  (the `ingest` skill is the agent-side counterpart).
- `wiki run <operation>` — execute a contract-driven operation
  (`weekly-digest`, `meal-planning`, etc.) installed by primitives.
  *Phase D — not yet shipped in v2.0.0.dev; the CLI exits with `wiki
  run: not yet implemented (v2 migration in progress, see RFC-0001).`
  Read the operation's `SKILL.md` header before suggesting it. Tracked
  under retro-review concern C7 (issue #23).*
- `wiki search <query>` — search the vault (or load the `wiki-search`
  skill directly to compose richer filters). Tier 1 (ripgrep, literal
  substring with `--type` / `--tag` / `--status` / `--top` flags)
  ships in v2.0.0; the FTS5 auto-upgrade tier remains future work.
- `wiki research <query>` — dispatch to a configured research provider
  if one is installed. Load the `wiki-research` skill for picker
  logic, provenance handling, and the Two-Source Rule before
  invoking.
- `wiki doctor` — validate state; load the `wiki-doctor` skill for the
  reasoning workflow. *(The `--strict` flag mentioned in older notes
  is not yet accepted by argparse; the base command works.)*
- `wiki journal {tail,grep,explain}` — read recent kit activity, e.g.
  "what changed today?". *Phase D — all three subcommands are stubs
  in v2.0.0.dev; read `.wiki.journal/journal.jsonl` directly until
  they ship. (C7 / issue #23.)*

## When in doubt

- For repeating tasks: load the matching SKILL.md before improvising.
- For ambiguous routing during ingest: ask the user to confirm content
  type before writing the page. Default routing decisions are listed in
  `skills/ingest/SKILL.md`.
- For anything that would touch many files at once: acquire the
  lock via `skills/wiki-lock/SKILL.md` first.
- For a `.proposed` sidecar: load `skills/wiki-conflict/SKILL.md`.
- For a discrepancy between the journal and disk: load
  `skills/wiki-doctor/SKILL.md`.
