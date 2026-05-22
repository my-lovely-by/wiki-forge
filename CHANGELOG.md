# Changelog

All notable changes to `llm-wiki-kit` are documented in this file.

The format follows the spirit of
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) —
an `[Unreleased]` section at the top, Added / Changed / Removed
categories, and compare links at release time — with two deviations:
category headers carry an RFC phase suffix
(`### Added — Phase A: Foundation …`), and the `Added` category is
split into one subsection per RFC phase rather than one flat block.
Naive `grep '^### Added$'` tooling will not find any matches. The
project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Project state and direction lives in [`docs/ROADMAP.md`](docs/ROADMAP.md); this
file is the shipped-work record. Decisions behind shipped work live in
[`docs/adr/`](docs/adr/); migration sequence and task-by-task progress live in
[`docs/rfc/0001-v2-architecture.md`](docs/rfc/0001-v2-architecture.md).

## [Unreleased]

### Added

- `wiki init --no-git` opt-out for the new default git-init behavior.
  Skips `git init` and the initial commit; the kit's `.gitignore`
  still ships through the normal render path. See
  [`docs/specs/wiki-init-git/spec.md`](docs/specs/wiki-init-git/spec.md).
- `VaultGitInitializedEvent` journal event (`type:
  "vault.git_initialized"`) marking the kit's initial commit.

### Changed

- `wiki init` now initializes a git repository in the new vault by
  default and makes one initial commit covering the freshly-rendered
  tree. The commit message is `Initialize wiki vault from <recipe>
  recipe`. Tutorial 1 and the README's quick-start no longer instruct
  users to run `git init && git add . && git commit -m "..."`
  manually.

## [2.0.0] — 2026-05-20

The v2.0.0 release. v2 replaces v1's three hand-edited template
variants (`vault-templates/{family,work,personal}/`) with a common
core, a catalog of droppable primitives, and recipes that compose
primitives for an audience. See
[RFC-0001](docs/rfc/0001-v2-architecture.md) for the full plan and
task-by-task progress; phase headings below match its
§"Migration sequence".

All 22 migration tasks plus Phase F's five contract-completion tasks
have shipped, alongside the four cross-cutting specs that surfaced
mid-flight. The kit is `pip install`-able and `pipx install`-able;
`wiki init --recipe {family,work-os,personal}` produces a working
vault out of the box.

> **Why Phase F items ship as `v2: implement …` rather than `v2: task …`.**
> The pre-tag audit on 2026-05-20 found five gaps between the surface
> RFC-0001 promised and what Tasks 1–22 delivered: `wiki upgrade`,
> `wiki search`, `wiki journal {tail,grep,explain}`, the vault-side
> `wiki-research` SKILL.md, and the `CHANGELOG.md` referenced by the
> CHARTER. Those are **contract-completion bugs against the RFC**, not
> deferred scope — the RFC §"What changes vs. v1" and §"CLI surface
> (target)" sections promised them, so each one shipped before the
> v2.0.0 tag with a commit message of `v2: implement <subject>` rather
> than `v2: task N - <subject>`. Future readers grepping the git log
> for the difference can use this entry as the index.

### Added — Phase A: Foundation (Tasks 1–5)

- Charter, RFC-0001, ADRs 0001–0005, and the kit-side `AGENTS.md`
  scaffolding (Task 1).
- Python package skeleton: `pyproject.toml`, the `wiki` CLI entry point
  with stubbed subcommands, and the CI workflow (Task 2).
- Pydantic v2 models (`models.py`) for `Primitive`, `Recipe`, the
  discriminated `Event` union, and `OperationContract`, plus `errors.py`
  (Task 3).
- Journal module (`journal.py`) with append / read / replay over the
  validated event union (Task 4).
- Write helper (`write_helper.py`) with `safe_write` and the proposal
  sidecar flow that backs ADR-0004's drift-detection contract (Task 5).

### Added — Phase B: Render and load (Tasks 6–10)

- Managed-region parser (`managed_regions.py`) and `safe_write_region`
  integration so multiple primitives can contribute to one file
  (Task 6).
- Render module (`render.py`) with `SafeDict` and the
  `INTERPOLATED_FILES` allowlist; everything else copies byte-for-byte
  per ADR-0001 (Task 7).
- Primitive loader and the first real primitive (`core`) with all
  baseline skills (Task 8).
- Recipe loader (`recipes.py`) and the three initial recipe files —
  `family`, `work-os`, `personal` (Task 9).
- `wiki init` end-to-end: the first working command, producing a vault
  with only the `core` primitive (Task 10). `--adopt` deferred to a
  follow-on.

### Added — Phase C: Primitives (Tasks 11–15)

- Three end-to-end primitives — `people` (ontology), `meeting`
  (content-type), `weekly-digest` (operation) — proving the primitive
  model. Surfaced ADR-0006 (additive managed-region contributions)
  (Task 11).
- `wiki add` and `wiki doctor` lifecycle commands (Task 12).
- Family-recipe primitives: `food`, `medical`, `trips`, `vendors`
  ontologies; `recipe`, `medical-record`, `trip-doc`, `receipt`,
  `tax-document`, `action-item` content-types; `meal-planning`,
  `trip-prep`, `follow-up-tracker`, `medical-summary` operations
  (Task 13).
- Work-os-recipe primitives: `projects`, `domains`, `customers`
  ontologies; `stakeholder-update`, `vendor-contract`,
  `customer-feedback`, `interview`, `decision` content-types;
  `stakeholder-map-refresh`, `action-item-rollup`,
  `renewal-reminders`, `onboarding-pack`, `status-synthesis`
  operations (Task 14).
- `identity` ontology (net new) and finalized `personal` recipe,
  which composes from Tasks 11/13/14 primitives plus `identity`
  (Task 15). (The `recipes/personal.yaml` file itself was first
  added in Task 9; Task 15 finalized its contents.)

### Added — Phase D: Runtime (Tasks 16–19)

- `wiki ingest` and the routing orchestrator — content-type
  detection via filename glob, file extension, URL host, URL path;
  `--as <name>` override (Task 16).
- `wiki run` and operation execution — contract-driven dispatch via
  `llm_wiki_kit.run`; `OperationRunEvent` records every attempt with
  status `dispatched` or `invalid_args` (Task 17).
- Research dispatch and the Perplexity provider — in-process
  dispatcher in `llm_wiki_kit/research/` using stdlib
  `urllib.request` (no new runtime deps); two opt-in infrastructure
  primitives (`research` and `research-perplexity`);
  `journal.transaction` wrap on `--out`; surfaced ADR-0007 codifying
  vault-root placement for the shared config file (Task 18).
- Gemini Deep Research and Semantic Scholar providers, completing
  the research-provider trio. All three are opt-in (Task 19).

### Added — Phase E: Quality and ship (Tasks 20–22)

- Eval harness — `trigger/`, `outcome/`, `provenance/`, `conflict/`,
  `research/` suites driving Claude Code via subprocess against
  fixture vaults; runs in its own CI workflow (Task 20).
- Three committed, regenerable example vaults under `examples/`
  (`family-mini`, `work-os-mini`, `conflict-pending`) plus an
  idempotent `examples/regenerate.py` rebuilder
  (`--check` / `--apply`); two tutorials
  (`docs/guides/tutorials/tutorial-1-first-vault.md`,
  `tutorial-2-work-os-walkthrough.md`) and a conflict-resolution
  how-to (`docs/guides/how-to/resolve-a-conflict.md`) (Task 21).
- v2.0.0 release cut — README rewrite for the package + recipe model,
  `docs/ROADMAP.md` populated with the single deferred item
  (`wiki init --adopt`), `CHANGELOG.md` `[Unreleased]` promoted to
  `[2.0.0]`, version bump to `2.0.0` in `pyproject.toml` and the
  `__version__` attribute in `llm_wiki_kit/__init__.py`, and deletion
  of the v1 `vault-templates/` and `shared/` trees (Task 22).

### Added — Phase F: Contract-completion bugs (Tasks 23–27)

The pre-tag audit on 2026-05-20 added a sweep of v2.0.0
contract-completion bugs as RFC-0001 Phase F — gaps between the
RFC's promised surface (§"CLI surface (target)" and §"What changes
vs. v1") and what Tasks 1–22 actually delivered. All five shipped
before the v2.0.0 tag with `v2: implement <subject>` commit messages
(see the lead-paragraph callout above).

- `wiki upgrade [--primitive <name>]` — the headline v1→v2 capability
  from §"What changes vs. v1" (`Bash sync scripts → pip install
  llm-wiki-kit; wiki upgrade`). New `llm_wiki_kit/upgrade.py` holds
  pure `plan_upgrade` plus the `upgrade_primitives` runner — one
  `PrimitiveUpgradeEvent` per upgraded primitive, `safe_write`-routed
  `render_tree`, then a single `aggregate_region_contributions` pass.
  ADR-0004 drift semantics preserved end-to-end. Spec at
  `docs/specs/wiki-upgrade/` (Task 23).
- `wiki search <query>` — ripgrep tier shipped per
  `docs/specs/wiki-search/`. Literal-substring scan over
  `<vault_root>/wiki/` with `--type` / `--tag` / `--status` /
  `--top` frontmatter filters; read-only (no journal events);
  `WikiError` on missing `rg`. FTS5 auto-upgrade tier remains future
  work (Task 24).
- `wiki journal {tail,grep,explain}` — three read-only handlers
  replace the `_stub()` callsites in `cli.py`: `tail [-n N]` (default
  10) emits tab-separated rows; `grep [--type T] PATTERN` does
  case-sensitive substring match over canonical JSON; `explain N`
  prints a human-readable block for the 1-based event line. Built on
  a new public `journal.parse_event_line` helper (Task 25).
- Vault-side `wiki-research` SKILL.md at
  `core/files/skills/wiki-research/SKILL.md` — closes the
  Tasks 18/19 deferral chain by giving the shipped `wiki research`
  CLI a behavioral spec inside user vaults. The SKILL teaches the
  provider picker (Perplexity / Gemini Deep Research / Semantic
  Scholar) by question shape, the graceful-degradation rules over
  `research-providers.yaml`, and the Two-Source Rule for
  load-bearing claims. Trigger eval at
  `tests/evals/trigger/test_wiki_research_trigger.py`; invariant
  suite pinning the SKILL against the CLI surface and the
  dispatcher's frontmatter fields at
  `tests/unit/test_wiki_research_skill.py`. Spec at
  `docs/specs/wiki-research-skill/` (Task 26).
- `CHANGELOG.md` at repo root — fills the `docs/CHARTER.md:113`
  reference to "current project state" sources (Task 27).

### Added — Cross-cutting specs landed mid-flight

These surfaced during v2 task work and ship as living specs under
`docs/specs/`:

- [`journal-locking`](docs/specs/journal-locking/spec.md) — `fcntl.flock`
  serialization around journal append, `journal.transaction()`
  brackets, `wiki lock acquire|release`, doctor stale-lock check.
- [`journal-reader-cache`](docs/specs/journal-reader-cache/spec.md) —
  `JournalReader` cache for install-pipeline baseline lookups.
- [`safe-write-ordering`](docs/specs/safe-write-ordering/spec.md) —
  event-before-disk ordering and fast-path adoption; ADR-0004
  §Revisions.
- [`wheel-bundled-assets`](docs/specs/wheel-bundled-assets/spec.md) —
  ship template assets inside the wheel and thread `kit_root`
  through `cli.main` so `pipx install` works without a checkout.

### Added — Foundational ADRs (referenced above)

- [ADR-0001](docs/adr/0001-stdlib-rendering-not-jinja.md) — stdlib
  `str.format_map` for interpolation; byte-for-byte copy otherwise.
- [ADR-0002](docs/adr/0002-journal-as-state-truth.md) — single
  append-only JSONL is the vault state of truth.
- [ADR-0003](docs/adr/0003-managed-regions-for-shared-files.md) —
  `<!-- BEGIN MANAGED: id -->` markers for multi-primitive
  contributions to shared files.
- [ADR-0004](docs/adr/0004-drift-detection-and-proposal-flow.md) —
  every kit write goes through `safe_write`; drift produces a
  `.proposed` sidecar.
- [ADR-0005](docs/adr/0005-pydantic-for-disk-bound-schemas.md) —
  every type that crosses disk is a Pydantic v2 model.
- [ADR-0006](docs/adr/0006-additive-managed-region-contributions.md) —
  additive snippet aggregation in topological install order
  (surfaced by Task 11).
- [ADR-0007](docs/adr/0007-shared-infra-config-files-at-vault-root.md)
  — shared infra config files land at the vault root (surfaced by
  Task 18).

### Removed

- The remaining v1 tree at the repo root: `vault-templates/`
  (`work/`, `family/`, `personal/` hand-edited vault skeletons) and
  `shared/` (the v1 canonical `CLAUDE.md`, per-variant
  `CLAUDE.variant.*.md` extensions, and `purpose.md` template) (Task
  22). v2 supersedes these with the package + recipe + primitive
  model; v1 reference material remains reachable through `git log`.
- v1 sync scripts (`scripts/sync-shared.sh`, `scripts/check-sync.sh`)
  and the `.github/workflows/check-sync.yml` workflow that ran them.
  No v2 code path invokes them.

[Unreleased]: https://github.com/eugenelim/llm-wiki-kit/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/eugenelim/llm-wiki-kit/releases/tag/v2.0.0
