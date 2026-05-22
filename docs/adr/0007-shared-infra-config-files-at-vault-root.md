# ADR-0007: Shared infrastructure config files land at the vault root

- **Status:** Accepted
- **Date:** 2026-05-17
- **Deciders:** maintainer
- **Related:** ADR-0003, ADR-0006, RFC-0001 (Task 18),
  `docs/specs/task-18-research-perplexity/spec.md`

## Context

ADR-0003 named three example shared infrastructure files: `AGENTS.md`,
`frontmatter.schema.yaml`, and `.claude/research-providers.yaml`. The
first two live at the vault root; the third was the only example
placed in a subdirectory. ADR-0003's *mechanism* — `<!-- BEGIN MANAGED:
id -->` / `# BEGIN MANAGED: id` markers with the
`parse` / `update` / `extract_unmanaged` API — works regardless of
where the file lives.

ADR-0006 then pinned the aggregator that composes multi-primitive
contributions into one managed region. The aggregator's filename
convention — `<primitive_root>/regions/<file>.<region>` — encodes the
target file and region id flatly into one filename. The implementation
(`install.py:_snippet_filename`) explicitly rejects `/` in the
contribution `file` field:

```python
if "/" in contribution.file or "/" in contribution.region:
    raise PrimitiveError(...)
```

That validator predates Task 18. It is correct as written for every
shipped contribution today — `frontmatter.schema.yaml.types`,
`frontmatter.schema.yaml.fields`, hypothetical `.gitignore.*` — all
flat names at vault root.

When Task 18 went to ship `infrastructure:research-perplexity`'s
contribution to ADR-0003's third example file, the conflict surfaced:
`{file: .claude/research-providers.yaml, region: providers}` would
need a snippet at `regions/.claude/research-providers.yaml.providers`,
which the validator rejects. Three options:

1. **Extend the aggregator to encode sub-paths**, e.g. via a flat
   encoding (`.claude__research-providers.yaml.providers`) or by
   parsing the contribution `file` as a real path.
2. **Bypass the aggregator** with a parallel write path for sub-dir
   targets — a second mechanism that ADR-0006 explicitly avoided.
3. **Land all shared infra config files at the vault root** — the
   path the two already-shipped examples follow.

Option 3 is the smallest change. Options 1 and 2 are substantive — a
refactor of `install.py`, new tests, new fail-modes around what
"under vault root" means for symlinks, and either an additional ADR
or an amendment to ADR-0006. None of those are blocked by Task 18,
but they are not needed by Task 18 either, and "fix the aggregator
because one provider config wants to live in a subdir" is a worse
ratio than "pin the rule, defer the refactor."

The user-facing impact of vault-root placement: a vault with all
three research providers installed has one extra top-level file
(`research-providers.yaml`) alongside `AGENTS.md` and
`frontmatter.schema.yaml` — a config file the user can see, edit
outside the managed region, and grep. The `.claude/` subdirectory in
v1 vaults was Claude-Code's own configuration mechanism; in v2 the
kit owns its own contracts and doesn't piggyback on the Claude Code
runtime's directory layout.

## Decision

> **Shared infrastructure config files contributed-to by managed-region
> snippets live at the vault root, not under `.claude/` or any other
> subdirectory.**

Mechanics:

- The `<file>` field in any `contributes_to` entry is a flat filename
  (no `/`), enforced by `install.py:_snippet_filename`. ADR-0006's
  filename convention (`<file>.<region>`) is unchanged.
- `research-providers.yaml`, the first multi-provider config under
  this rule, lives at `<vault_root>/research-providers.yaml`.
  `infrastructure:research`'s `files/research-providers.yaml` seeds
  it; each `infrastructure:research-*` primitive contributes a block
  to its `providers` managed region.
- Future multi-provider configs (search backends, ingest delta
  trackers, anything ADR-0003 §"Industry patterns" covers) follow the
  same rule: vault-root flat filename.

This decision applies only to **shared infrastructure config files**
— files multiple primitives contribute to. Single-primitive config
files that the kit reads but doesn't compose can live wherever the
primitive's `files/` tree puts them; ADR-0003's managed-region
mechanism doesn't apply.

ADR-0003's `.claude/research-providers.yaml` example is superseded by
this ADR. The *mechanism* ADR-0003 describes (managed regions,
parsing, drift detection) is unchanged.

## Consequences

### Positive

- **Task 18 ships without an `install.py` refactor.** The aggregator
  stays single-rule, the validator stays one-line, the tests stay
  green.
- **One filesystem rule for shared infra configs.** A future primitive
  author asking "where does my shared YAML live?" has one answer.
  ADR-0006's `<file>.<region>` filename grammar is sufficient.
- **Vault root stays human-greppable.** A user (or Claude) running
  `ls` at the vault root sees `AGENTS.md`,
  `frontmatter.schema.yaml`, `research-providers.yaml` — the kit's
  managed contracts, all in one place.
- **No collision with Claude-Code's `.claude/`.** The kit's contracts
  don't have to compete with whatever the user's agent runtime puts
  there.

### Negative

- **Vault root accumulates kit-owned files as more multi-contributor
  configs ship.** Each future infrastructure category that goes
  through managed regions adds one. Mitigation: the
  `<!-- BEGIN MANAGED -->` / `# BEGIN MANAGED` markers keep each
  file's kit-owned regions visibly separated; users can scan the
  vault root and know what's theirs.
- **Departure from ADR-0003's illustrative example.** Reviewers and
  primitive authors who read ADR-0003 first will see a `.claude/`
  path that v2 doesn't follow. Mitigation: this ADR references the
  ADR-0003 mention explicitly; future ADR-0003 revisions can
  cross-link.
- **Re-platform cost if a future agent runtime requires `.claude/`
  placement.** If Claude Code (or another agent) ever *requires*
  configs under `.claude/` to be picked up, the kit would need to
  ship a `.claude/research-providers.yaml` that points to / symlinks
  to the vault-root file, or revisit this decision. Today no such
  requirement exists.

### Neutral / monitor

- **Trigger to revisit:** when the aggregator gains support for
  sub-path targets — either via a flat-encoding rule (e.g.
  `.claude__research-providers.yaml.providers`) or by parsing
  `contribution.file` as a real path. At that point this ADR can
  flip via a successor; primitive `contributes_to` entries gain
  optional path prefixes.
- **Watch:** vault-root clutter. If users surface complaints that
  the vault root has accumulated too many kit-owned files (or if a
  primitive author requests sub-path placement), open an RFC to
  reconsider — likely a `.wiki-managed/` subdirectory parallel to
  `.wiki.journal/`, with the aggregator supporting that one prefix
  as a special case.

## Alternatives considered

### Alt 1: Extend the aggregator to support sub-path targets via flat encoding

Encode `/` in the contribution `file` as a marker like `__` in the
snippet filename: `regions/.claude__research-providers.yaml.providers`.
`install.py:_snippet_filename` strips/reverses the marker when
resolving the on-disk write path. Loses because:

- New encoding to teach primitive authors. The current rule is
  "the snippet filename is the target filename"; a flat-encoding
  rule breaks that read-it-and-you-know-where-it-lands property.
- New failure modes: a target filename that legitimately contains
  `__` (none today, but the kit-vs-user contract should not depend
  on filename hygiene that has nothing to do with the kit).
- The aggregator's existing test surface assumes flat filenames;
  every assertion would gain an encoding/decoding step that has to
  stay symmetric forever.

Worth revisiting if a future ADR establishes a real need for
sub-path targets (the "Neutral / monitor" trigger above).

### Alt 2: Extend the aggregator to parse `contribution.file` as a real `PurePosixPath`

Drop the `/`-forbidden validator; have the aggregator resolve
`vault_root / contribution.file` against a sanity-check (no
absolute paths, no `..`, no symlinks-out-of-tree). Loses because:

- More logic on the `safe_write` boundary that has to be perfect.
- More edge cases (Windows paths, NFC vs NFD on macOS for filenames
  containing non-ASCII characters). The current rule is portable;
  this one would not be without extra normalization.
- Same "no caller today" argument: Task 18 doesn't need it; building
  it speculatively for "the next config" is the same trap RFC-0001
  warns about in §"Minimal scope."

### Alt 3: Bypass the aggregator for `.claude/`-prefixed targets

Add a parallel write path: any `contributes_to` whose `file` starts
with `.claude/` is handled by a separate aggregator that knows how
to write to subdirectories. Loses because:

- Two mechanisms for the same job; ADR-0006 explicitly named
  "single mechanism" as a positive consequence (§"The aggregator
  pattern keeps primitives decoupled").
- Drift detection becomes mode-dependent (which writer wrote it?),
  doubling the surface that `wiki doctor` reasons about.

### Alt 4: Ship the `.claude/` path under a hardcoded primitive that doesn't go through the aggregator

Have `infrastructure:research` (and only that primitive) write
`.claude/research-providers.yaml` directly via `safe_write`. No
aggregation, no managed-region composition — just one big file the
seed primitive owns. Loses because the entire point of ADR-0003 +
ADR-0006 is that *multiple* primitives contribute to that file;
giving up composition means future research provider primitives
either edit each other's snippets in git (drift hell) or each ship
their own config file (no shared discovery surface for the
dispatcher).

## References

- ADR-0003 (managed regions for shared infrastructure files) — the
  mechanism this ADR specializes.
- ADR-0006 (additive managed-region contributions) — the aggregator
  whose flat-filename convention this ADR codifies as a path rule.
- `docs/specs/task-18-research-perplexity/spec.md` §Constraints —
  the spec citing this ADR as the rule for `research-providers.yaml`.
- `llm_wiki_kit/install.py:_snippet_filename` — the validator whose
  no-`/` rule is the implementation of this ADR's "flat filename"
  requirement.
- RFC-0001 §"Phase D — Runtime" Task 18 — the migration task that
  motivated this ADR.
