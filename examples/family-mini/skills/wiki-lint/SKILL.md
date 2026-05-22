---
name: wiki-lint
description: "Run health checks on the vault. Combines structural checks (broken wikilinks, orphan pages, missing frontmatter fields, stale `modified` dates, tag synonyms, convergence debt in `raw/`, asset coverage) with semantic checks (contradiction detection across pages claiming the same fact). Use on request — \"lint the wiki\" / \"check wiki health\" — weekly or per-sprint, or after bulk ingestion of new raw sources."
license: MIT
---

# wiki-lint

> **⚠️ Not yet shipped in v2.0.0.dev.** This skill is invoked via
> `wiki run wiki-lint`, and `wiki run` is a stub in v2.0.0.dev: it
> prints `wiki run: not yet implemented (v2 migration in progress,
> see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec* — the structural checks it describes are not wired
> up. Tracked under retro-review concern C7 (issue #23).

Comprehensive health checks. Some are deterministic (Python scripts);
some are semantic (you decide). Run the deterministic ones first —
they're cheap and surface most of what's wrong.

## When to load this skill

- On request: "lint the wiki", "check wiki health", "run a lint".
- Scheduled: once a week, or per-sprint, by the user's request.
- After bulk ingestion of new raw sources.
- Before a big synthesis pass (a digest, a quarterly review) — start
  from a clean baseline.

## What the lint covers

| Check                          | Mode                | Cost      |
|--------------------------------|---------------------|-----------|
| Missing required frontmatter   | Script              | Cheap     |
| Tag hygiene (synonyms, typos)  | Script + your review| Cheap     |
| Broken wikilinks               | Script              | Cheap     |
| Orphan pages (no inbound links)| Script              | Cheap     |
| Stale `modified` (> N days)    | Script              | Cheap     |
| Asset coverage (assets without companion page) | Script | Cheap |
| Convergence debt (`raw/` files no page references) | Script | Cheap |
| Contradiction detection        | You read & reason   | Expensive |

## Order of operations

1. **Acquire the lock** (lint may write a report). Load
   `skills/wiki-lock/SKILL.md`.
2. **Run the structural checks.** A single command:

   ```bash
   wiki run wiki-lint --output log/lint-$(date +%Y-%m-%d).md
   ```

   Reads the vault, emits a markdown report, journals a `lint.run`
   event with the issue count.
3. **Read the report.** Section by section: frontmatter, links,
   orphans, staleness, assets, convergence debt. Summarize what
   you found into chat in 5-8 bullets.
4. **Run contradiction detection only if requested.** It's
   token-heavy — don't initiate it unless the user asks or the
   structural report flagged a specific topic to investigate.
5. **Auto-fix what's safe** (see below) after the user confirms.
6. **Surface what isn't auto-fixable** as a list of pages and what
   needs to happen.
7. **Release the lock.**

## Contradiction detection (the expensive one)

For a topic or domain the user names:

1. Use `wiki-search` to find pages tagged with the topic.
2. Read the synopsis of each (depth 1).
3. Identify pages that make claims about the same entity or
   decision.
4. Read those pages in full (depth 2).
5. Quote the conflicting claims back to the user with page
   references.

Don't decide the contradiction. The user owns reconciliation;
you surface it.

## Auto-fix (with confirmation)

After the user reviews the report, these are safe to fix
automatically — but always ask first:

- Add a missing `> [!warning] Outdated` callout to a stale page.
- Update a stale page's `modified` date (only if you also touched
  the content in this session).
- Backfill a missing `provenance` field (default to `mixed` for
  pages of unknown origin; ask if there's any doubt).
- Generate a missing `## Synopsis` section from existing content.

These always require user review — never auto-fix:

- Resolving contradictions.
- Merging synonym tags (one canonical tag, retag every page).
- Archiving orphan pages (the orphan may be a stub waiting for a
  link).
- Deleting convergence debt (the source might be intentionally
  unprocessed).

## Reading the report

The report is a markdown file the user reads. Summarize it in chat:

> Lint summary, 2026-05-15:
> - 3 pages missing `provenance` — fixable, with confirm.
> - 7 broken wikilinks — need user review (mostly typos in target
>   names).
> - 12 orphan pages — most look intentional, but `wiki/projects/x.md`
>   should probably link to `wiki/customers/x.md`.
> - 2 stale pages (> 90 days) flagged as `active` — confirm they
>   really are.
> - 0 contradictions (not run; ask if you want me to).

Don't dump the whole report into chat. The user can open it.

## Output

- The report file at `log/lint-YYYY-MM-DD.md`.
- A `lint.run` event in the journal with the issue count and status
  (`ok`, `issues`, `error`).
- A line in `log/changelog.md` summarizing what you ran.
