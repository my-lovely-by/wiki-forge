# File Formats

What formats the kit supports, how Claude handles non-markdown files, and the companion-page convention that keeps deliverables visible in Obsidian.

## Format support matrix

| Format | Claude support | Notes |
|---|---|---|
| Markdown (`.md`) | Native | Primary wiki format. Syncs perfectly on all drives. |
| Word (`.docx`) | Full read/write | Required for Claude Cowork file editing. Works on Google Drive but auto-converts in Google Docs for collaborative viewing. |
| Excel (`.xlsx`) | Full read/write | Use `.xlsx` instead of Google Sheets — Claude can't edit Sheets format directly. |
| PowerPoint (`.pptx`) | Full read/write | Same pattern — use native Office format, not Google Slides. |
| PDF (`.pdf`) | Read + create | Claude reads and generates PDFs. Use [[ingest]] to convert PDFs to markdown for the wiki. |
| Images (`.png`, `.jpg`, `.tif`) | Read + OCR | Use [[ingest]] for OCR via Docling. |
| Google Docs / Sheets / Slides | Read-only (indirect) | Claude cannot write to native Google formats. Always save as Office equivalents. |

**Recommendation.** If your team is in Google Workspace, keep the wiki itself as markdown (no format issues) and use `.docx`/`.xlsx`/`.pptx` for any deliverables Claude needs to create or edit. If you're on Microsoft 365, the format alignment is seamless.

## Why companion pages

Obsidian's graph view, search, and backlinks work only on markdown. Drop a `.docx` into a wiki folder and it becomes invisible to the knowledge graph — it can't participate in cross-linking, and team members can't find it through normal navigation.

The kit solves this with the **companion page pattern**: every non-markdown file gets a sibling `.md` page that contains its metadata, summary, and wikilinks. The companion page is the index card Obsidian sees; the binary file is the polished deliverable.

This also gives Claude a markdown breadcrumb to find the file in a future session. A prompt like "Update the approach doc based on yesterday's design review" leads Claude to the companion page → the deliverable path → the file itself.

## Where files live

Source files (your inputs) and small assets attached to wiki pages live in `_assets/` subfolders next to the wiki page that owns them:

```
wiki/projects/order-platform/
├── design/
│   ├── data-pipeline-architecture.md      # Wiki page
│   └── _assets/
│       ├── system-diagram.png             # The image
│       └── system-diagram.png.md          # Companion page
```

Claude-generated deliverables live in `outputs/` rather than `_assets/`, so they don't clutter the wiki tree:

```
outputs/order-platform/
├── approach-doc-v1.docx
└── architecture-presentation-v1.pptx
```

The wiki-side companion page references the deliverable via frontmatter:

```yaml
---
title: "Data Pipeline Approach Document"
type: proposal
project: order-platform
deliverable: "[[outputs/order-platform/approach-doc-v1.docx]]"
format: docx
---
```

## Companion page rules

When producing a non-markdown deliverable:

1. Save the file to `outputs/<project-slug>/` (or `outputs/team/` for cross-project).
2. Use descriptive filenames: `<topic>-<type>-v<N>.<ext>` — e.g., `data-pipeline-approach-v1.docx`.
3. Create a companion `.md` page in the appropriate wiki location (`proposals/`, `design/`, `research/`, etc.).
4. The companion page must include:
   - Full frontmatter with `deliverable:` field pointing to the file
   - A 2-3 sentence summary
   - Key decisions or findings extracted into wiki-linkable form
   - Wikilinks to related domain/tool/project pages
5. When updating a deliverable, create a new version file and update the companion page.
6. The companion page is the authoritative wiki entry; the deliverable file is the formatted output.

## When to produce markdown vs. an Office format

| Situation | Format | Why |
|---|---|---|
| Internal wiki content (design notes, meeting syntheses, domain pages) | Markdown | Lives natively in Obsidian, full graph/search/backlink support |
| Approach documents and proposals for stakeholder review | Word (`.docx`) | Professional formatting, track changes, shareable outside the team |
| Architecture presentations, project reviews | PowerPoint (`.pptx`) | Slide format for meetings |
| Research reports with charts/tables for distribution | PDF (`.pdf`) | Fixed layout, universally readable, archival |
| Technical specs that Claude will iterate on | Word (`.docx`) | Claude reads and revises `.docx` directly |
| Quick reference, internal checklists, runbooks | Markdown | No need for Office overhead |

The wiki markdown layer is where knowledge lives and compounds. The `outputs/` layer is where polished deliverables live for consumption. The companion page bridges them — extracting the knowledge from the deliverable back into the wiki graph so nothing is trapped in a binary file that only Claude can read.

## Large files

Files larger than 50 MB should stay external (cloud drive link, S3, etc.). Create a stub companion page with an `external_path` field instead of inlining the file:

```yaml
---
type: asset
asset_type: video
external_path: "https://drive.google.com/file/d/xxxxx"
title: "Q1 Architecture Review Recording"
---

Recording of the Q1 architecture review. 2h 15m. Stored externally due to file size (1.2 GB).
```
