# Plan: vault-side `wiki-research` SKILL.md

> **Implementation plan paired with `spec.md`.** The spec says *what*;
> the plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** `docs/specs/wiki-research-skill/spec.md`
- **Owner:** maintainer

## Approach

Task 26 is documentation-shaped: the artifact is one markdown SKILL.md
shipped into a user's vault under `skills/wiki-research/`, plus one
trigger eval that asserts Claude reaches for it on a representative
research-flavored prompt. Tasks 18 and 19 already shipped the CLI; this
task closes a contract gap (CLI surface without a behavioral SKILL).

Two implementation lanes:

1. **Write the SKILL.md** to teach Claude when to call
   `wiki research`, how to pick a provider from
   `research-providers.yaml`, and how to integrate the markdown
   answer into downstream wiki pages under the Two-Source Rule.
   Markdown is the only file format; no Python.
2. **Pin the SKILL's accuracy** with a small unit-test suite that
   grep-checks the SKILL body against the actual kit-side surface
   (argparse subparser, dispatcher's frontmatter dict,
   `_PROVIDER_REGISTRY` keys). A SKILL that drifts ahead of the
   CLI is the failure mode this suite catches.
3. **Pin the SKILL's trigger** with one eval that runs `claude`
   against `research_dispatch_vault` and asserts the SKILL is the
   first one Claude reads on a research-flavored prompt.

The order is: write the spec → write the SKILL → write the unit
tests (red against a missing or wrong SKILL) → write the trigger
eval (skipped in non-eval CI; runs in the eval workflow) → refresh
`core/files/AGENTS.md` to drop the stale "Phase E — not yet
shipped" note about `wiki research` → mark RFC-0001 Task 26
shipped.

## Pre-conditions

- Tasks 18 and 19 are merged (`wiki research` CLI surface is live,
  `_PROVIDER_REGISTRY` contains the three slugs).
- Task 20's eval harness (`tests/evalkit/`) is merged with
  `run_claude`, `ordered_skill_reads`, `assert_skill_loaded`, and
  the `research_dispatch_vault` fixture.
- `core/files/skills/wiki-search/SKILL.md`,
  `core/files/skills/ingest/SKILL.md`, and
  `core/files/skills/wiki-conflict/SKILL.md` exist and act as
  structural / tonal precedent for the new SKILL.

## Verification mode per step

| Step | Mode |
|---|---|
| 1. SKILL.md exists with valid frontmatter | Goal-based (read the file, parse the frontmatter) — pinned by a unit test rather than a manual check so the gate stays mechanical. |
| 2. SKILL body documents only shipped flags | TDD — grep-test against `cli.py:_cmd_research`'s subparser. |
| 3. SKILL body documents only shipped frontmatter fields | TDD — grep-test against `dispatch.py:_render_markdown`'s dict keys. |
| 4. SKILL body references only `_PROVIDER_REGISTRY` slugs | TDD — import the registry, grep-check each slug mention. |
| 5. SKILL body has no kit-side path leakage | TDD — grep-reject forbidden substrings. |
| 6. SKILL body names each graceful-degradation case | TDD — grep-check the four case strings. |
| 7. SKILL body has no key-shaped substrings | TDD — regex-reject key shapes. |
| 8. Trigger eval loads the SKILL on a research prompt | Manual QA shape (driven by `claude` subprocess; gated by `ANTHROPIC_API_KEY` + `claude` binary). |
| 9. `core/files/AGENTS.md` no longer claims `wiki research` is unshipped | Goal-based (grep the file). |
| 10. RFC-0001 Task 26 marked ✅ shipped in Phase F | Goal-based (grep the RFC). |

## Steps

1. **Spec exists at `docs/specs/wiki-research-skill/spec.md`.**
   - Write the spec (above) before any SKILL content. The spec
     drives the SKILL's structure; reading the spec is faster than
     re-deriving from peers.
   - Verify: file exists, references Task 18 + 19 specs, names
     Task 26 in `Related:`.

2. **SKILL.md exists at
   `core/files/skills/wiki-research/SKILL.md` with valid
   frontmatter.**
   - Author the file end-to-end matching the body sections listed
     in `spec.md` §Outputs.
   - Tests: `tests/unit/test_wiki_research_skill.py::test_frontmatter_is_valid`
     parses the file via `yaml.safe_load`, asserts `name ==
     "wiki-research"`, asserts `license == "MIT"`, asserts
     `description` length is between 100 and 800 chars.

3. **SKILL body documents only flags the CLI accepts.**
   - Code: in the §"Invocation" section, list exactly the three
     surface elements `query`, `--provider`, `--out`. No other
     flag appears in the body anywhere.
   - Tests:
     `test_skill_body_documents_only_shipped_flags` —
     loads `cli.py`'s argparse parser via `cli.build_parser()`,
     extracts the `research` subparser's accepted args, and
     asserts every flag-shaped substring (`--<name>`) in the
     SKILL body is in that set.

4. **SKILL body documents only frontmatter fields the dispatcher
   emits.**
   - Code: §"Reading results" lists the five fields exactly.
   - Tests:
     `test_skill_body_documents_only_shipped_frontmatter_fields` —
     imports the dispatcher's `_render_markdown`, calls it with a
     minimal `_ProviderOutput`, parses the rendered frontmatter,
     and asserts the SKILL body's "frontmatter fields the kit
     emits" mentions are a subset of the rendered keys. Any
     extra mention (e.g. `source_kind` listed as a *kit-emitted*
     field) fails.

5. **SKILL body references only `_PROVIDER_REGISTRY` slugs.**
   - Code: §"Picking a provider" decision table uses the three
     registered slugs (`perplexity`, `gemini`,
     `semantic-scholar`).
   - Tests:
     `test_skill_body_provider_slugs_are_subset_of_registry` —
     imports `_PROVIDER_REGISTRY` from `dispatch.py`, regex-finds
     every backtick-delimited identifier in the SKILL body that
     looks like a slug (`[a-z][a-z0-9-]*`), and asserts the
     intersection with `_PROVIDER_REGISTRY.keys()` is exactly the
     mentioned-in-SKILL set (no unknown slug; the SKILL may
     mention a subset if a slug isn't named).

6. **SKILL body contains no kit-side paths.**
   - Code: vault-side audience — never name `llm_wiki_kit/`,
     `docs/`, `tests/`, `templates/`, or `.claude/research-providers.yaml`.
   - Tests:
     `test_skill_body_has_no_kit_side_paths` — asserts each
     forbidden substring is absent.

7. **SKILL body names each graceful-degradation case.**
   - Code: §"Reading research-providers.yaml first" / §"Graceful
     degradation" must say "no provider", "one provider", "more
     than one provider", and quote the CLI's "has no
     implementation in this kit version" error verbatim.
   - Tests:
     `test_skill_body_names_each_graceful_degradation_case` —
     asserts each case-string substring is present.

8. **SKILL body has no key-shaped substrings.**
   - Tests: `test_skill_body_no_key_leak` — regex-rejects
     anything matching the kit's `_KEY_LIKE_RE` shape (`sk-…`,
     `pplx-…`, `AIza…`) and the placeholders `<your-api-key>`,
     `<api-key>`, `<api_key>`.

9. **Trigger eval lives at
   `tests/evals/trigger/test_wiki_research_trigger.py`.**
   - Code: mirror `test_wiki_conflict_trigger.py`. Use the
     existing `research_dispatch_vault` fixture; tool allowlist
     `["Read", "Glob"]`; the prompt is a research-flavored,
     skill-discovery prompt (not naming the SKILL path).
   - Tests:
     `test_prompting_for_research_loads_wiki_research_skill` —
     asserts `ordered_skill_reads` returns `wiki-research`
     first, or falls back to `assert_skill_loaded(result,
     "wiki-research")` if no skill reads happened. Skips on
     unset `ANTHROPIC_API_KEY` or missing `claude` binary.

10. **`core/files/AGENTS.md` no longer claims `wiki research` is
    unshipped.**
    - Code: replace the "Phase E — not yet shipped in v2.0.0.dev;
      exits `not yet implemented`. (C7 / issue #23.)" suffix on
      the `wiki research <query>` bullet (line 153-155) with a
      pointer to the new SKILL: "Load the `wiki-research` SKILL
      for picker logic and provenance handling."
    - Verify: grep — the C7-stub note for `wiki research` is
      gone; the SKILL-pointer line is present.

11. **RFC-0001 Task 26 is marked ✅ shipped in Phase F.**
    - Code: add a "Phase F — Contract completion (parallelizable)"
      section to RFC-0001 below the existing Phase E. The section
      contains Task 26 with the same ✅-prefixed, summary-line
      shape as Tasks 1–20. Update §"Progress to date" to bump
      the shipped count and name Phase F.
    - Verify: grep — `Task 26` appears under `## Phase F`, marked
      ✅; the summary's "shipped" count reflects the new state.

## Verification gate

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

All four gates pass against the merged state. The trigger eval
is marked with the `pytest.mark.eval` marker and runs only when
the eval workflow runs (gated by `ANTHROPIC_API_KEY` + a
`claude` binary on PATH); the unit-test suite for SKILL-shape
invariants runs in the standard CI.

## Risks

- **The SKILL drifts ahead of the CLI.** Mitigation: the
  argparse-derived flag-set test and the dispatcher-derived
  frontmatter-field test are mechanical pins. If a future CLI
  change adds `--budget` without updating the SKILL, the test
  surfaces the drift.
- **The trigger eval flakes on slow networks.** Mitigation:
  `timeout_s=180.0` matches the wiki-conflict trigger eval;
  the eval marker keeps it out of fast CI.
- **The SKILL teaches a provider-picker rule the kit's
  dispatcher can't honor.** Mitigation: the SKILL only teaches
  *when to pass `--provider <slug>`*; the kit's dispatcher
  picks no automatic fallback. The SKILL's "If the picked
  provider isn't installed, fall back" rule explicitly tells
  Claude to substitute *in chat* (suggest the alternative to
  the user) rather than relying on a kit-side fallback that
  doesn't exist.
- **`core/files/AGENTS.md` edit misses a vault-side dependent.**
  Mitigation: `grep -rn "Phase E.*wiki research"
  core/ templates/` to confirm no other vault-side surface
  references the same stale note.

## Out of scope

- A `wiki research --corroborate` flag for automatic two-source
  dispatch (deferred to a future task once the SKILL teaches the
  pattern and we have telemetry on how often Claude actually
  invokes the second provider).
- A `research-source` content-type primitive (Task 18 §Non-goals;
  out of scope for Task 26).
- Reconciling `docs/research-providers/*.md` and
  `docs/design/research-layer.md` with the v2 kit-side surface
  (they reference the v1 `.claude/research-providers.yaml` path
  and an `enabled:` flag the v2 schema doesn't have). A separate
  doc-refresh task can address them; Task 26's scope is the
  SKILL, the eval, and the RFC strike.
- A `wiki research --stream` flag, a `--budget` flag, a
  `--top` flag, a `--format` flag — none of these exist; the
  SKILL teaches the surface that does ship.
