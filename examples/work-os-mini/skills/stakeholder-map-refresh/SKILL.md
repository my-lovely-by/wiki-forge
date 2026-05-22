---
name: stakeholder-map-refresh
description: "Produce one stakeholder-map page per project, listing the audience members who receive updates plus their cadence. Load when the user asks 'who's on the distro for project X', when `wiki run stakeholder-map-refresh` invokes you, or on a scheduled monthly sweep. Writes one page per project to `outputs/stakeholder-maps/<project>.md`; idempotent per project — re-running overwrites the same page rather than producing a new one."
license: MIT
---

# stakeholder-map-refresh

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run stakeholder-map-refresh`, and `wiki run` is a stub in
> v2.0.0.dev: it prints `wiki run: not yet implemented (v2 migration
> in progress, see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Walk recent stakeholder-update pages, group by project, and write one
durable map page per project showing who actually receives updates
(distinct from who *should* — that's a planning question, not a
synthesis one).

## When to load

- The user asks "who's on the distro for project X?", "who do we
  brief on the apollo revamp?", or similar.
- `wiki run stakeholder-map-refresh` runs you with the contract from
  `contract.yaml`.
- A scheduled monthly invocation.

## Inputs

From the operation contract:

- **`window_days`** — how far back to look for stakeholder-updates.
  Default 90 days. A project with no update in the window does not
  get a map (the prior map page, if any, is left as-is — stale but
  honest).
- **`project`** — scope the run to one project (kebab-case page
  name). Default is all projects with at least one in-window update.

## Procedure

1. **Find the input pages.** Walk `wiki/stakeholder-updates/`. Filter
   by `update_date` inside the window. Use the `wiki-search` skill
   with `--type stakeholder-update` and `--frontmatter` filters; do
   not hand-grep.
2. **Group by project.** For each in-window update, read
   `update_project` and bucket the update there.
3. **Build the audience table.** For each project, walk its updates
   and aggregate `update_audience` across them. For each unique
   audience member, record:
   - The wikilink (or plain string for group labels).
   - The count of updates they appeared in.
   - The most-recent update date they appeared in.
4. **Compose the map page.** One page at
   `outputs/stakeholder-maps/<project>.md` with sections:
   - **Project** — wikilink back to `wiki/projects/<project>`.
   - **DRI** — pulled from the project page if available; otherwise
     "Not set on `[[projects/<project>]]`".
   - **Active recipients** — table of audience members who appeared
     in ≥2 of the last N updates, ordered by recency.
   - **Occasional recipients** — those who appeared once.
   - **Recent cadence** — count of updates in the window plus the
     date range.
5. **Idempotence.** If the map page for this project already exists,
   the kit's `safe_write` will hash-compare and either rewrite (if you
   and the kit agree on the content) or sidecar (if the user has
   edited the map by hand). Both cases are correct — don't bypass.

## Frontmatter for the map page

```yaml
type: stakeholder-map
status: active
provenance: synthesized
created: <today>
modified: <today>
tags: [stakeholder-map, <project>]
map_project: <project>
map_window_days: <window>
```

The `stakeholder-map` type may not yet exist in
`frontmatter.schema.yaml`'s managed `types` region — that's fine for
v0.1. A later task may ship a content-type primitive that registers
the type properly; until then, `wiki-lint` flags it as a known gap.

## When a project has no in-window updates

Skip the project. Do not write an empty map page. The absence of a
map page is itself a signal — "we haven't told anyone about this
project in 90 days" is information.

## When `update_audience` is mostly group labels

A page whose audience is `["leadership", "@channel"]` cannot resolve
individual recipients. Note this on the map page: "Audience is mostly
group-addressed; individual recipients not resolvable from
stakeholder-update pages alone." Suggest the user augment the project
page with an explicit recipient list if individual tracking matters.

## After writing

- Append a one-line summary to the running activity log.
- If a previously-named recipient stopped appearing in the window,
  surface it on the map page under "Recipients who dropped off" with
  the date of their last appearance. Drop-offs are the actionable
  signal — they're the question "should this person still be on the
  distro?"
