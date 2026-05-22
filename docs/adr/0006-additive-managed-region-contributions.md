# ADR-0006: Additive managed-region contributions

- **Status:** Accepted
- **Date:** 2026-05-16
- **Deciders:** maintainer
- **Related:** ADR-0002, ADR-0003, ADR-0004, RFC-0001 (Task 11)

## Context

ADR-0003 introduced managed regions as the write contract for shared
infrastructure files (`AGENTS.md`, `frontmatter.schema.yaml`,
`.claude/research-providers.yaml`, `.gitignore`). It defined the
delimiter syntax, the parser API (`parse` / `update` /
`extract_unmanaged`), and the journal event (`managed_region.write`),
and named the per-primitive layout — each primitive declares
`contributes_to: [{file, region}]` in `primitive.yaml` and ships
snippet files under `regions/`.

What ADR-0003 left underspecified: when **N** primitives contribute to
the **same** `(file, region)` pair, how does the installer compose
their snippets into one body? `safe_write_region` takes one string and
writes it once. Naive per-primitive calls would have the last writer
clobber every earlier writer.

Task 11 of the v2 migration plan ships the first three real primitives
(`people`, `meeting`, `weekly-digest`) and the first multi-contributor
region (`frontmatter.schema.yaml:types`, soon to be joined by Tasks 13
and 14 with `recipe`, `decision`, etc.). The composition rule cannot
stay implicit — Tasks 13 and 14 are meant to run in parallel, and a
convention that lives only in `cli.py` will get reinvented twice.

The parallelization plan (`.context/tasks-11-to-15-parallelization.md`)
called for an ADR to pin this before primitive authoring goes wide.
This is that ADR.

## Decision

> **Multiple primitives contributing to the same managed region are
> composed by concatenating their snippet files in install order,
> producing one region body that is written via a single
> `safe_write_region` call at the end of the install pipeline. Each
> primitive that declares a `contributes_to` entry must ship a
> matching snippet file; mismatches in either direction are fatal at
> install time.**

Mechanics:

1. **Snippet file naming.** Each contribution declared as
   `contributes_to: [{file: F, region: R}]` has a snippet file at
   `<primitive_root>/regions/F.R`. The filename is the literal `file`
   value (extension included) joined to the literal `region` value
   with a single `.` separator. Examples:
   - `regions/frontmatter.schema.yaml.types`
   - `regions/frontmatter.schema.yaml.fields`
   - `regions/.gitignore.recipes` (note: filenames begin with a dot
     too — the convention is purely textual, no `os.path.splitext`
     gymnastics).
2. **Snippet body.** Each snippet is the literal text that goes
   between the BEGIN/END markers — no headers, no marker lines, no
   surrounding YAML/markdown framing. The text is preserved
   byte-for-byte through UTF-8 round-trip, including indentation.
3. **Aggregation order.** Snippets are concatenated in the install
   order produced by `primitives.resolve_dependencies` (topological
   under `requires:`, alphabetical-by-name tiebreaker). This matches
   the order primitives are journaled and rendered, so the composed
   region body is reproducible across runs.
4. **Concatenation rule.** For each `(file, region)` bucket, the
   aggregator reads each contributor's snippet, ensures the snippet
   ends with exactly one newline (a missing trailing newline is added;
   multiple trailing newlines are collapsed to one), and joins them
   in order. The resulting body has one newline between contributors
   and one trailing newline. The aggregator does not insert separator
   comments — the `<!-- BEGIN MANAGED -->` / `# BEGIN MANAGED` markers
   themselves are the boundary between kit and user, not between
   contributors.
5. **Single write per region.** The installer collects all
   `(file, region) → [contributors]` mappings across the full install
   closure, then iterates the buckets in deterministic order
   (alphabetical by `file`, then by `region`) and calls
   `safe_write_region` exactly once per bucket. No interleaving with
   per-primitive `files/` renders — those run first, the region
   aggregation runs after, so any new shared file landed by a
   primitive's `files/` tree is already on disk before its region
   markers are touched.
6. **Loud-fail on shape mismatch.** Two failure modes are fatal at
   install time, not deferred to `wiki doctor`:
   - **Missing snippet.** A `contributes_to: [{file: F, region: R}]`
     entry with no snippet file at `regions/F.R` raises
     `PrimitiveError` before any write.
   - **Orphan snippet.** A file under `regions/` whose name does not
     correspond to a declared `contributes_to` entry raises
     `PrimitiveError` before any write.
   Both checks run at primitive-load time when the primitive is
   actually being installed (not at `discover_primitives` time — a
   primitive being merely on disk should not crash `wiki init` for an
   unrelated recipe).
7. **No snippet, no problem.** A primitive with no `contributes_to`
   entries and no `regions/` directory is the common case and
   participates in nothing.
8. **Empty region body is allowed.** If a `(file, region)` bucket has
   zero contributors (because no installed primitive contributes to
   it), the aggregator does not call `safe_write_region` for that
   region. The region's existing body (the literal text shipped by
   the file's seed primitive — typically `core`) is preserved
   verbatim.

This decision applies only to the install pipeline used by
`wiki init` (Task 10) and `wiki add` (Task 12). `wiki upgrade` (a
later task) will reuse the same aggregator over the new install
closure.

## Consequences

### Positive

- **Parallel primitive authoring is safe.** Two authors adding
  unrelated content-types in parallel never edit the same source file;
  they each add a new primitive directory with its own `regions/`
  snippets. Merge conflicts in git are limited to recipe files
  (different recipes per author) and the new primitive directory
  (no overlap).
- **Deterministic output.** Two `wiki init` runs on the same recipe
  produce the same `frontmatter.schema.yaml`, byte-for-byte. CI and
  drift-detection fixtures stay honest.
- **No coordinator needed in `primitive.yaml`.** A primitive doesn't
  know what other primitives contribute to the same region. The
  installer owns the merge, not the manifest. This keeps primitives
  decoupled.
- **Drift-detection stays region-scoped.** `safe_write_region` already
  hashes the region body. The aggregator just builds the body — the
  drift contract is unchanged from ADR-0003.
- **The kit fails before writing.** Missing or orphan snippets are
  caught at primitive-load time, before any state-changing event.
  Half-rendered vaults from a bad manifest never happen.

### Negative

- **Order matters for human-readability.** If a user reads the
  rendered `types:` list in `frontmatter.schema.yaml`, the order is
  alphabetical-tiebroken-topological, not the order the user expects
  from any one recipe. This is mildly surprising but stable.
- **Snippet files are tiny and numerous.** Each content-type ships
  ~2 files under `regions/` (one for `types`, one for `fields`). A
  recipe with 10 content-types has ~20 snippet files contributing to
  one rendered file. The trade is worth it; the alternative
  (single-file snippets with section markers) would re-invent
  managed regions inside managed regions.
- **A region with one contributor still goes through the aggregator.**
  No fast path for the single-contributor case — that's intentional,
  the code path is uniform.

### Neutral / monitor

- If a future primitive needs a *non-additive* contribution — e.g.
  "this primitive owns the entire `installed-skills` region of
  `AGENTS.md`" — we'll need a new contribution kind (`mode: replace`
  vs the implicit `mode: append`). Out of scope for v2.0; revisit if
  the use case shows up.
- The aggregator does not detect *semantic* conflicts (two primitives
  both declaring a `type: meeting` would produce a duplicate-line
  schema). `wiki doctor` is the eventual home for that check.
- The 20-region-per-file ceiling ADR-0003 monitored stays the same —
  this ADR doesn't change the per-file region count, only the
  per-region contributor count.

## Alternatives considered

### Alt 1: Single owner per region (no multi-contributor)

Restrict each managed region to exactly one declaring primitive.
Loses immediately: `frontmatter.schema.yaml:types` is *by definition*
the union of every content-type primitive's contribution. A single-
owner rule would force a "schema" primitive that re-declared every
type the recipe used, defeating the catalog's decoupling premise.

### Alt 2: Replace-on-write (last writer wins)

Let each primitive call `safe_write_region` independently; the last
one in install order wins. Loses because it makes the multi-
contributor case useless without a coordinator primitive — exactly
what we're trying to avoid. Also produces garbage journal events
(N writes for one effective region body).

### Alt 3: Snippet bodies in `primitive.yaml`

Inline the snippet text directly into the manifest:
```yaml
contributes_to:
  - file: frontmatter.schema.yaml
    region: types
    content: |
      - meeting
```
Loses on (a) `primitive.yaml` becomes a 200-line document for a real
content-type, (b) YAML indentation rules inside `content: |` blocks
are subtly different from the host file's indentation, (c) Pydantic
strict-mode validation gets entangled with snippet content, and (d)
the snippet is no longer reviewable as the file it'll become — a
diff against `regions/frontmatter.schema.yaml.types` reads exactly
like the rendered output, an inline snippet doesn't.

### Alt 4: `regions/` as a directory tree mirroring the target

`regions/frontmatter.schema.yaml/types` (directory split). Loses on
filesystem ergonomics: most snippets are one or two lines, and
forcing each to be in its own subdirectory triples the path depth
for no payoff. The dotted-flat filename grammar reads exactly the
way the contribution is declared.

### Alt 5: Defer the decision (let cli.py invent it)

The path of least resistance: write the aggregator inline in
`_cmd_init`, no ADR. Loses because the parallelization plan
explicitly identified the absence of this contract as the highest-
risk parallelization hazard. Writing it down once costs less than
two authors discovering it independently.

## References

- ADR-0003 (managed regions for shared files) — the contract this
  ADR builds on.
- ADR-0002 (journal) — every region write is a journaled event;
  aggregation does not change that.
- ADR-0004 (drift detection) — region drift falls through to the
  proposal flow, unchanged by aggregation.
- RFC-0001 §"Phase C — Primitives" — Task 11 is the first task that
  needs this contract.
- `.context/tasks-11-to-15-parallelization.md` — the planning
  artifact that flagged the need for this ADR.
