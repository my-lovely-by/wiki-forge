---
name: wiki-search
description: "Search the vault by content and frontmatter (--type, --tag, --status, --top). Returns ranked pages with title, type, status, tags, and per-file match count — ready to read. Today's backend is ripgrep (literal substring, zero install); the SKILL was designed against a future SQLite FTS5 auto-upgrade tier (BM25, stemming, phrase/prefix) that has not yet shipped. Use for any *vault content* question; reserve the IDE's built-in Grep for regex, code search, or inspecting a known path."
license: MIT
---

# wiki-search

> **Tier 1 (ripgrep) ships in v2.0.0; tier 2 (FTS5 auto-upgrade) is
> future work.** `wiki search "<query>" [--type …] [--tag …]
> [--status …] [--top N]` is live: literal substring over `wiki/` with
> frontmatter filters, ranked by per-file match count. The FTS5 tier
> described below (stemming, phrase, prefix, snippet highlighting)
> remains a future spec — see `docs/specs/wiki-search/spec.md`
> §Non-goals. Until it lands, those backend-specific features are
> unavailable and the skill stays on tier 1 for all vaults regardless
> of size.

Two-tier search over the vault. The skill calls one entry point; the
backend is chosen automatically.

| Tier | Backend            | When                                                          |
|------|--------------------|---------------------------------------------------------------|
| 1    | **ripgrep**        | Default; ships in v2.0.0 and serves vaults of every size today. |
| 2    | **SQLite FTS5**    | *(Future spec.)* Will auto-enable once the vault crosses ~1000 pages or 50 MB. |

Today the kit uses tier 1 for every vault; the tier-2 entry above
documents the future direction the SKILL was designed against.

## When to use this skill (vs. the IDE's built-in Grep)

Use **wiki-search** for any query whose target is *vault content*
(anything under `wiki/`). Both backends return ranked page references
with frontmatter metadata and a synopsis — that's what you need to
decide which page to actually read. Specifically:

- Plain content queries ("find pages about *X*"). Tier 1 (today)
  is **literal substring** — `kafka` matches `kafka` exactly; word
  boundaries and case sensitivity are honored.
- Frontmatter filters (`--type meeting`, `--tag urgent`,
  `--status active`).
- *(FTS5 — future)* Stemming-aware queries (`running` matches
  `runs`).
- *(FTS5 — future)* Phrase, prefix, or NEAR queries (FTS5 syntax:
  `"value stream"`, `market*`, `kafka NEAR/5 lag`).

Use the IDE's **Grep** tool for:

- Regex queries (this skill's ripgrep tier is literal substring).
- Code search inside `skills/`, `scripts/`, or other infrastructure.
- Inspecting a known file by exact path.

Both tools are unrestricted; routing is your call. When the question is
"which wiki pages…", load this skill.

## Operations

```bash
# Basic search
wiki search "event driven architecture"

# With frontmatter filters
wiki search "compliance" --tag urgent --type meeting

# Limit results
wiki search "kafka" --top 20
```

Output is markdown the agent reads directly: a ranked list of pages,
each block with title, vault-relative path, frontmatter (type, status,
tags), and a per-file match count. *(Synopsis and highlighted snippets
ship with FTS5 in a future spec.)*

## Composing a good query

- **Start narrow.** Two or three words specific to the topic. Add
  filters before broadening the query.
- **Filter by frontmatter when you can.** `--type recipe` cuts the
  result set by an order of magnitude in food-heavy vaults.
- **Iterate.** If the first query returns nothing useful, broaden one
  word at a time. Don't dump the user's whole question as the query —
  vault content is terser than chat.

## Reading results

The skill returns the top N pages, each with:

- **Title** — the page's `# H1` or filename stem.
- **Path** — relative to the vault root.
- **Frontmatter** — type, status, tags.
- **Match count** — ripgrep's per-file match count; ordering key.
  *(FTS5 substitutes a BM25 score when the future tier ships.)*
- *(FTS5 — future)* **Synopsis** — the page's `## Synopsis` section
  if present, else the first paragraph.
- *(FTS5 — future)* **Snippet** — the matched lines, with the query
  terms highlighted.

Open the top 2-3 pages by exact path with your file-reading tool. Don't
re-run the search; the result set is enough to pick.

## Scaling

| Vault size       | Backend          | What to do                                    |
|------------------|------------------|-----------------------------------------------|
| < 100 pages      | ripgrep          | Nothing.                                      |
| 100–500 pages    | ripgrep          | Nothing.                                      |
| 500–1000 pages   | ripgrep          | Nothing.                                      |
| 1000+ pages      | ripgrep          | Still works; FTS5 auto-upgrade is future.     |

Search is **lexical**. Synonym matching (`car` ↔ `automobile`) and
conceptual matching (`pricing strategy` ↔ `go-to-market plan`) are
out of scope — that needs embeddings and is a different problem.

## Failure modes

The skill stays out of your way when nothing matches:

- **`rg` missing** → clear error pointing to the install command for
  the user's OS.
- **No results** → not an error; printed as `no matches.` Either the
  topic isn't in the wiki yet (suggest ingesting a source) or the
  query is too narrow (broaden a word).
- *(FTS5 — future)* **Index missing / schema drift** → rebuild on
  the next call; fall back to ripgrep if rebuild fails.

If a search returns zero results, it's not an error — it's a signal.
Either the topic isn't in the wiki yet (suggest ingesting a source) or
the query is too narrow (broaden a word).
