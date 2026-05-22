# ADR-0003: Managed regions for shared infrastructure files

- **Status:** Accepted
- **Date:** 2026-05-15
- **Deciders:** maintainer
- **Related:** RFC-0001, ADR-0002, ADR-0004, `docs/architecture/overview.md` ("Three layers of write safety")

## Context

Some files in a user's vault are jointly contributed to by multiple
primitives. Examples:

- `AGENTS.md` — every primitive can register itself (its kind, its skill,
  its detection signals) so a Claude session in the vault knows what's
  installed.
- `frontmatter.schema.yaml` — every content-type primitive adds its
  schema block.
- `.claude/research-providers.yaml` — every research provider primitive
  registers its config block.
- `.gitignore` — primitives may add ignored paths.

These files have two simultaneous owners: the kit (which installs and
upgrades primitives that need to write to them) and the user (who may
want to add comments, reorder sections, or add their own entries).

If the kit rewrites these files on every primitive install or upgrade,
user edits get clobbered silently. If the kit refuses to touch them
after first write, primitive installs / upgrades stop working.

Industry patterns surveyed:

- **Comment markers** (`# BEGIN MANAGED BY X` / `# END MANAGED BY X`) —
  used by ssh authorized_keys tooling, Ansible, Chef, .gitignore
  generators. Simple, plain text, robust under user edits outside the
  markers.
- **Separate include files** (`.d` directories) — used by systemd,
  cron, Apache. Each primitive drops a fragment in a `conf.d/`
  directory. Robust, but introduces directory plumbing for every shared
  file and doesn't work for files like `AGENTS.md` that need to read as
  a single document.
- **JSON/YAML merge** (kustomize, helm overlays) — works for structured
  data but not for prose documents like `AGENTS.md`.
- **Templated rewrite** (rerun template engine, lose user edits) —
  simpler to implement but violates the "Don't auto-write to user-edited
  content" charter principle.

## Decision

> **Shared infrastructure files use `<!-- BEGIN MANAGED: id --> ... <!-- END MANAGED: id -->`
> delimiters to mark kit-owned regions. The kit only writes inside its
> declared regions. User edits outside any managed region survive
> untouched.**

Mechanics:

- `managed_regions.py` exposes three functions:
  - `parse(content) -> {region_id: region_content}` extracts every
    managed region by id.
  - `update(content, region_id, new_content) -> str` rewrites one
    region in place, preserving everything outside.
  - `extract_unmanaged(content) -> str` returns the content with all
    managed regions stripped — used for drift detection on the user-
    editable parts.
- Markdown comment style (`<!-- ... -->`) is used for `.md` files.
  YAML comment style (`# BEGIN MANAGED: id` / `# END MANAGED: id`) is
  used for YAML files. Both are silently parseable by their host
  formats.
- A primitive's contributions live under `regions/` in its directory.
  Each file is named `<target-file>.<region-id>` and contains the
  literal text that will be inserted between the BEGIN/END markers.
- The `primitive.yaml` declares each contribution explicitly:
  ```yaml
  contributes_to:
    - file: AGENTS.md
      region: content-types
  ```
- Every region write produces a `managed_region.write` journal event.
- Drift inside a managed region falls through to the proposal-and-
  conflict flow (ADR-0004); drift outside managed regions is invisible
  to the kit by design.

This decision applies *only* to shared infrastructure files. Wiki pages
(under the user's content folders) are treated as user-ambiguous on
every write — no managed regions, just direct drift detection.

## Consequences

### Positive

- **User edits outside regions are safe.** A user adding a personal
  note to `AGENTS.md` between two managed regions, or below all of
  them, will never lose it.
- **Primitives compose without coordination.** `meeting` and `recipe`
  both contribute to `AGENTS.md`'s `content-types` region, but the
  region-write logic merges them deterministically based on installed
  order from the journal.
- **Plain-text friendly.** The markers are visible. A user opening the
  file can see what's managed and what's theirs.
- **No directory plumbing.** Files like `AGENTS.md` remain single
  documents readable end-to-end, not fragments under `AGENTS.md.d/`.

### Negative

- **User edits inside a managed region trigger conflicts.** That's the
  design — the kit can't safely merge prose changes — but it surfaces
  as friction. Mitigated by the `wiki-conflict` skill giving Claude
  the context to help the user resolve.
- **Two comment syntaxes** (markdown + YAML). Mitigated: the parser
  detects file type from extension. No file mixes both.
- **Region ids are part of the public contract.** Renaming a region
  (e.g., `content-types` → `content_types`) is a breaking change for
  any primitive contributing to it. Treat them as stable identifiers.
- **Region nesting is not supported.** Markers are flat — no managed
  regions inside managed regions. If we ever need this we'll add it
  with explicit semantics, but YAGNI for now.

### Neutral / monitor

- If primitives proliferate so that one shared file has >20 regions,
  the file becomes hard to read. Evaluate split-out at that point.
  Currently we project ≤6 regions per file.

## Alternatives considered

### Alt 1: `.d/` directories per shared file

`AGENTS.md.d/00-core.md`, `AGENTS.md.d/10-content-types.md`, etc.,
concatenated on render. Robust composition. Loses because:

- Users can't read `AGENTS.md` end-to-end without concatenating
  fragments mentally.
- The "main file" disappears — confusing for a markdown wiki where
  files-as-documents is the mental model.
- Adds a render step on every read of `AGENTS.md`, or stales the file
  vs. the fragments. Either way, drift surface area grows.

### Alt 2: Templated rewrite (no managed regions)

Re-run the renderer on every primitive change; overwrite the file
wholesale. Loses on the charter principle "Don't auto-write to user-
edited content." If a user adds a single line, the next primitive
install eats it.

### Alt 3: JSON/YAML structured merge only

Works for `frontmatter.schema.yaml` and `.claude/research-providers.yaml`
but not for `AGENTS.md` (prose document). Would require two write
mechanisms — twice the code, twice the bug surface. Single mechanism wins.

### Alt 4: Three-way merge using git as backend

Use the user's git history to find the "base" version and compute a
real three-way merge. Loses because (a) we don't require git, and
(b) prose merges are notoriously bad even when git is present. We'd
end up surfacing conflicts to the user anyway, but with more code.

## References

- [Ansible `blockinfile` module](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/blockinfile_module.html)
  — the closest existing pattern.
- ADR-0002 (journal) — managed-region writes are journaled events.
- ADR-0004 (drift detection) — managed-region drift falls into the same
  proposal flow as page drift.
- Migration RFC `docs/rfc/0001-v2-architecture.md` (Task 6)
