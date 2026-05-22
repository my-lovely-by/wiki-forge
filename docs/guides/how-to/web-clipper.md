# Obsidian Web Clipper

The Obsidian Web Clipper extension (Chrome / Firefox / Safari / Edge) clips web pages to your vault as clean markdown using [defuddle](https://github.com/kepano/defuddle) — the same engine the kit's `ingest-website` skill uses. Two configurations are supported: a **recommended** setup that writes directly into the kit's ingestion path, and a **fallback** for users running Web Clipper with its default settings.

## Recommended: write into `raw/web-clips/`

The kit's convention is that source documents live in `raw/` and are immutable once committed. Configure Web Clipper to drop clips directly there so they need no relocation:

1. Install the [Obsidian Web Clipper](https://obsidian.md/clipper) extension and open its settings.
2. Open **Templates** → choose your default template (or create a new one called `wiki-kit`).
3. Set **Note location** to:
   ```
   raw/web-clips
   ```
4. Set **Note name** to:
   ```
   {{date|YYYY-MM-DD}}-{{title|slug}}
   ```
5. Set **Properties** (frontmatter):
   ```yaml
   source_url: {{url}}
   fetched_via: obsidian-web-clipper
   fetched_at: {{date|YYYY-MM-DD}}
   type: raw-source
   provenance: extracted
   ```
6. **Note content** — leave the default `{{content}}` so the article body becomes the page body.
7. Save the template and use it for all clips going forward.

After this, clips land at `raw/web-clips/<YYYY-MM-DD>-<slug>.md` and the kit's `ingest` orchestrator picks them up like any other `raw/` source — running scope check, content-type schema, contradiction check, and wiki update.

## Fallback: default `Clippings/` location

If you don't change Web Clipper's default settings, clips land at `Clippings/{title}.md` in the vault root. The kit handles this **without any extra setup** — `ingest` treats `Clippings/` as a transient inbox.

When you ask the agent to process clippings (or when it encounters them during another operation), the flow is:

1. **Detect** new files in `Clippings/`.
2. **Skip source-type cleanup** — Web Clipper already produced clean markdown.
3. **Route to content-type schema** — the appropriate content-type primitive (see [`templates/content-types/`](../../../templates/content-types/) for what ships) or a generic article, based on URL pattern + content shape, then run the standard scope / contradiction / wiki-update flow.
4. **Relocate after processing** — on success, the clipping is moved to `raw/web-clips/<YYYY-MM-DD>-<slug>.md` so it joins the canonical immutable source store. The wiki page footnote points to the relocated path.
5. **Leave on failure** — if the user rejects the routing, the source falls out of scope, or the routing is ambiguous and the user defers, the file stays in `Clippings/` for retry.

`Clippings/` itself is **never deleted** — only the processed file is relocated. The folder stays empty after a successful batch and re-fills as new clips arrive.

## How to trigger processing

- **One at a time:** "ingest the latest clipping" / "process Clippings/{title}.md"
- **Batch:** "process my clippings inbox" — the orchestrator iterates everything in `Clippings/`, surfaces a routing plan per file, and processes after confirmation.
- **Automatic on a related ingest:** if you ask "ingest this URL" and the same URL is already in `Clippings/`, the orchestrator uses the existing clipping rather than re-fetching.

## Conventions reminder

- **Never delete a clipping without confirmation** — the kit's safety rule applies. Relocation to `raw/web-clips/` is the default; deletion is an explicit user action only.
- **Don't modify clippings after relocation** — `raw/` is immutable. If a clipping is wrong (bad extraction, partial page), re-clip the URL and let the orchestrator detect the duplicate.
- **`raw/web-clips/` is not auto-created** — it appears the first time a clipping is relocated or saved there. Web Clipper's recommended config also creates it on first clip.

## Why prefer the recommended setup

Two reasons:

1. **No relocation step** — clips are in their canonical home from the start, the wiki page's source footnote points to the same path forever, and there's no chance a clipping is missed in `Clippings/` and forgotten.
2. **Frontmatter consistency** — the kit's `ingest-website` writes specific frontmatter (`source_url`, `fetched_via`, `fetched_at`). The recommended Web Clipper template matches that schema; the default template does not (the orchestrator backfills missing fields during relocation, but starting consistent is cleaner).

The fallback exists so users who haven't configured the extension still benefit from the kit's ingestion flow — and so existing vaults with accumulated `Clippings/` content can be brought into the kit without manual migration.
