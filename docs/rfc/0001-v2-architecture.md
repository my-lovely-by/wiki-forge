# RFC-0001: v2 — common core + primitives + recipes architecture

- **Status:** Accepted
- **Author:** maintainer
- **Created:** 2026-05-15
- **Discussion:** PR opened against `main` from the porting branch
- **Resolves to:** ADRs 0001–0005, the 22 migration tasks listed below,
  and (eventually) the `v2.0.0` tag

> This RFC is the migration plan. It summarizes the goal architecture,
> the rationale, and the task-by-task sequence to land it. Each task is
> one Claude Code session (~60–90 min) producing one PR.

## Summary

Evolve `llm-wiki-kit` from a hand-shaped three-variant template repo
(family / work / personal) into a Python package + template catalog
architecture: a common core that's always installed, a catalog of
droppable primitives (ontologies, content-types, operations,
infrastructure), and a small set of recipes that compose primitives
for specific audiences.

The migration lives on a working branch alongside `main`. The v1 tree
was deleted in flight rather than archived under `archive/v1-*/` (see
§"Pre-flight" below for the actual sequence); reference material from
v1 is recoverable through git history. v2 ships as
`pip install llm-wiki-kit==2.0.0` once all 22 tasks land.

## Motivation

v1 ships three forks of the same wiki (`vault-templates/{family,work,personal}/`)
plus a `shared/` directory the sync scripts copy into each. This worked
to bootstrap the concept but produces three growing failure modes:

1. **Drift between variants.** Each variant is hand-edited. Improvements
   to one don't propagate. The `scripts/sync-shared.sh` / `check-sync.sh`
   pair patches one slice of this but the rest is manual.
2. **No upgrade path for users.** A user who installed v1 family and
   wants the latest medical-record handling has no way to pull it in
   without manually diffing against the template repo.
3. **Adding a primitive is a multi-variant edit.** Every new content
   type touches at least one variant's CLAUDE.md, schema, and skill
   directory. The friction discourages incremental contribution.
4. **No journal, no drift detection.** v1 has no model of "what the kit
   wrote vs. what the user changed." Any future automation has to
   reinvent this.

v2 replaces the multi-variant template tree with a primitive catalog
plus recipes that compose primitives. The user installs a CLI
(`pipx install llm-wiki-kit`), runs `wiki init --recipe family`, and
gets a vault with skills, schemas, and journal already wired up. They
upgrade by re-running `wiki upgrade`. They add capabilities with
`wiki add content-type:interview`. Drift detection prevents the kit
from clobbering their edits.

## Proposal

### Goal architecture

Three layers:

1. **Common core** (`core/`) — the always-installed engine. The
   AGENTS-style contract for the vault, the frontmatter schema baseline,
   the journal, the write-helper, the `wiki-conflict` skill, the
   `wiki-lock` skill, the ingest orchestrator's routing logic, lint
   baseline, search baseline.
2. **Catalog of primitives** (`templates/`) — independently-versioned,
   droppable building blocks. Four kinds:
   - *Ontology primitives* — folder shapes with seed files (`people/`,
     `food/`, `medical/`, `projects/`, `customers/`, …).
   - *Content-type primitives* — an ingester plus page template plus
     frontmatter contribution (`meeting`, `recipe`, `medical-record`,
     `decision`, …).
   - *Operation primitives* — a contract plus skill plus eval fixture
     (`weekly-digest`, `meal-planning`, `stakeholder-map-refresh`, …).
   - *Infrastructure primitives* — cross-cutting (`research` dispatch,
     `research-perplexity`, `research-gemini`,
     `research-semantic-scholar`, search backends).
3. **Recipes** (`recipes/`) — named YAML files that compose primitives
   into a coherent vault for one audience. Initial recipes: `family`,
   `work-os`, `personal`.

### Foundational decisions (the seven ADRs)

The shape rests on five load-bearing decisions captured up front, plus
two follow-on ADRs that surfaced during implementation. All seven are
Accepted.

- **[ADR-0001](../adr/0001-stdlib-rendering-not-jinja.md)** — stdlib
  `str.format_map` for the handful of files that need interpolation,
  byte-for-byte copy for everything else. No Jinja, no delimiter
  collision with Obsidian Templater.
- **[ADR-0002](../adr/0002-journal-as-state-truth.md)** — a single
  append-only JSONL at `.wiki.journal/journal.jsonl` is the source of
  truth for vault state. No separate manifest or lockfile.
- **[ADR-0003](../adr/0003-managed-regions-for-shared-files.md)** —
  shared infrastructure files (`AGENTS.md`,
  `frontmatter.schema.yaml`, `research-providers.yaml`) use
  `<!-- BEGIN MANAGED: id --> ... <!-- END MANAGED: id -->` markers so
  multiple primitives can contribute without clobbering user edits.
- **[ADR-0004](../adr/0004-drift-detection-and-proposal-flow.md)** —
  every kit write to a user vault goes through `safe_write`; on hash
  drift, a `.proposed` sidecar is written and the `wiki-conflict`
  skill helps the user merge.
- **[ADR-0005](../adr/0005-pydantic-for-disk-bound-schemas.md)** —
  every type that crosses disk is a Pydantic v2 model; the journal
  uses Pydantic's native discriminated-union support.
- **[ADR-0006](../adr/0006-additive-managed-region-contributions.md)**
  (added Task 11) — when N primitives contribute to the same managed
  region, the installer concatenates snippet files in topological
  install order and writes the region once via `safe_write_region`.
  Snippet filename is `<file>.<region>` (flat, no `/`).
- **[ADR-0007](../adr/0007-shared-infra-config-files-at-vault-root.md)**
  (added Task 18) — shared infrastructure config files contributed
  to by managed-region snippets land at the vault root rather than
  under `.claude/` or any subdirectory, codifying the aggregator's
  no-`/` filename rule. Revisit when the aggregator gains sub-path
  support.

### Runtime constraints

- Python ≥3.11.
- Runtime deps: `pyyaml`, `pydantic>=2`, stdlib. New runtime deps
  require a new ADR.
- The kit is a CLI + library. It does not include an LLM; the user
  brings their own Claude (or other agent) which reads `AGENTS.md` and
  the SKILL.md files.

### CLI surface (target)

```
wiki init --recipe <name> [--no-git] <path>     # create a new vault; --no-git opts out of the default git init + initial commit
wiki add <kind>:<name>               # install a primitive into the current vault
wiki upgrade [--primitive <name>]    # upgrade installed primitives to latest
wiki doctor                          # validate vault state against journal
wiki ingest <source>                 # route to the right ingester
wiki run <operation>                 # run an operation
wiki research <query>                # dispatch to a configured research provider
wiki search <query>                  # ripgrep/FTS5 over the vault
wiki journal {tail,grep,explain}     # read the journal
```

### What changes vs. v1

| v1 | v2 |
|---|---|
| Three hand-edited vault templates | One core + a catalog + three recipes |
| `shared/` copied via shell scripts | Primitive contributions composed by Python |
| No state model | Append-only Pydantic-validated JSONL journal |
| No drift detection | `safe_write` + proposal sidecars + `wiki-conflict` skill |
| Bash sync scripts | `pip install llm-wiki-kit`; `wiki upgrade` |
| Per-variant CLAUDE.md | One `core/files/AGENTS.md` with managed regions |
| Research-provider configs in `.claude/` per variant | `infrastructure/research-*` primitives, opt-in across all recipes |

### Migration sequence (the 22 tasks)

Each task is one Claude Code session producing one PR. Inputs/outputs/
acceptance are in the migration plan artifact retained at
`.context/attachments/pasted_text_2026-05-15_22-00-26.txt` for the full
detail.

**Progress to date (2026-05-20):** All 22 migration tasks plus the
five Phase F contract-completion items have shipped; `v2.0.0` is
tagged. Phases A, B, C, and D delivered the 19 foundation, render,
primitive, and runtime tasks. Phase E added Task 20 (eval harness),
Task 21 (example vaults + tutorials), and Task 22 (README/ROADMAP +
v2.0.0 release cut). Phase F closed the gap between this RFC's
promised surface and what Tasks 1–22 actually delivered — Task 23
(`wiki upgrade`), Task 24 (`wiki search` ripgrep tier), Task 25
(`wiki journal {tail,grep,explain}`), Task 26 (vault-side
`wiki-research` SKILL.md), and Task 27 (`CHANGELOG.md`). Phase F
items shipped as `v2: implement <subject>` bug-fixes against this
RFC's contract per AGENTS.md §"When this file is wrong" — bugs, not
deferrals — and merged before the v2.0.0 tag.

Side artifacts that landed alongside: ADR-0006
(additive managed-region contributions, Task 11), ADR-0007 (shared
infra config files at vault root, Task 18), and several living
specs under `docs/specs/` for cross-cutting concerns surfaced
mid-flight (`journal-locking/`, `journal-reader-cache/`,
`safe-write-ordering/`, `wheel-bundled-assets/`).

#### Phase A — Foundation (sequential) — ✅ shipped

1. **Task 1 — Charter, RFC, ADRs.** ✅ AGENTS.md, CHARTER, this RFC,
   ADRs 0001–0005, doc templates.
1. **Task 2 — Python package skeleton.** ✅ `pyproject.toml`, `wiki`
   CLI entry point with stubbed subcommands, CI workflow.
1. **Task 3 — Pydantic models.** ✅ `models.py` with `Primitive`,
   `Recipe`, the discriminated `Event` union (one class per event
   type), `OperationContract`, plus `errors.py`.
1. **Task 4 — Journal module.** ✅ `journal.py` with append / read /
   replay over the validated event union; gained `fcntl.flock`
   serialization + `journal.transaction()` brackets per the
   `journal-locking` spec.
1. **Task 5 — Write helper.** ✅ `write_helper.py` with `safe_write`
   and the proposal sidecar flow; later refined by the
   `safe-write-ordering` spec (event-before-disk; adopt fast-path;
   ADR-0004 §Revisions).

#### Phase B — Render and load (sequential) — ✅ shipped

6. **Task 6 — Managed-region parser.** ✅ `managed_regions.py` and
   integration with `safe_write_region`.
1. **Task 7 — Render module.** ✅ `render.py` with `SafeDict` and the
   `INTERPOLATED_FILES` allowlist.
1. **Task 8 — Primitive loader + the `core` primitive.** ✅ First
   real primitive, with all baseline skills.
1. **Task 9 — Recipe loader.** ✅ `recipes.py` and the three initial
   recipe files.
1. **Task 10 — `wiki init` end-to-end.** ✅ First working command —
   a vault with only the core primitive renders correctly. (`--adopt`
   deferred; see §"Unresolved questions".)

#### Phase C — Primitives (parallelizable after Task 11) — ✅ shipped

11. **Task 11 — Three primitives end-to-end.** ✅ `people`
    (ontology), `meeting` (content-type), `weekly-digest`
    (operation). Surfaced ADR-0006 (additive managed-region
    contributions). Proved the primitive model.
1. **Task 12 — `wiki add` and `wiki doctor`.** ✅ Lifecycle commands.
1. **Task 13 — Family-recipe primitives.** ✅ `food`, `medical`,
    `trips`, `vendors` ontologies; `recipe`, `medical-record`,
    `trip-doc`, `receipt`, `tax-document`, `action-item`
    content-types; `meal-planning`, `trip-prep`, `follow-up-tracker`,
    `medical-summary` operations.
1. **Task 14 — Work-os-recipe primitives.** ✅ `projects`, `domains`,
    `customers` ontologies; `stakeholder-update`, `vendor-contract`,
    `customer-feedback`, `interview`, `decision` content-types;
    `stakeholder-map-refresh`, `action-item-rollup`,
    `renewal-reminders`, `onboarding-pack`, `status-synthesis`
    operations.
1. **Task 15 — Personal recipe.** ✅ Recipe file plus identity stubs;
    reuses primitives from the other two.

#### Phase D — Runtime (sequential) — ✅ shipped

16. **Task 16 — `wiki ingest` + orchestrator.** ✅ Content-type
    routing, detection signals (filename glob, file extension, URL
    host, URL path). `--as <name>` override.
1. **Task 17 — `wiki run` + operation execution.** ✅ Contract-driven
    dispatch via `llm_wiki_kit.run`; `OperationRunEvent` records
    every attempt (status `dispatched` / `invalid_args`).
1. **Task 18 — Research dispatch + Perplexity.** ✅ In-process
    dispatcher in `llm_wiki_kit/research/`; stdlib `urllib.request`
    only (no new runtime deps); two opt-in infrastructure primitives
    (`research` + `research-perplexity`); `journal.transaction`
    wrap on `--out`; ADR-0007 codifies vault-root placement for the
    shared config file.
1. **Task 19 — Gemini Deep Research + Semantic Scholar providers.**
    ✅ Two new infrastructure primitives (`research-gemini`,
    `research-semantic-scholar`) plug into Task 18's
    `_PROVIDER_REGISTRY` extension point; all three providers are
    opt-in. Additive `json_body` argument on `research/http.py` for
    Semantic Scholar's GET path; ResearchHTTPError vs WikiError
    boundary preserved per Task 18's spec.

#### Phase E — Quality and ship (sequential) — ✅ shipped

20. **Task 20 — Eval harness.** ✅ `trigger/`, `outcome/`,
    `provenance/`, `conflict/`, `research/` evals. Drives Claude Code
    via subprocess.
1. **Task 21 — Example vaults and tutorials.** ✅ Three committed,
    regenerable example vaults under `examples/` (`family-mini`,
    `work-os-mini`, `conflict-pending`); `examples/regenerate.py`
    idempotent rebuilder with `--check` / `--apply`; tutorials 1
    (first vault) and 2 (work-os walkthrough); resolve-a-conflict
    how-to; integration suite covers AC1–AC10 + AC13 and a
    no-new-top-level-dirs guardrail.
1. **Task 22 — README, ROADMAP, v2.0.0.** ✅ Final pass, merge to
    main, tag the release. Phase F completed before this task landed,
    so the RFC's CLI surface and the shipped CLI agreed at tag time.
    The ROADMAP pass documents only items the RFC explicitly defers,
    currently a single entry:
    - `wiki init --adopt` — explicit "Unresolved question" in this
      RFC; deferred at Task 10. Needs its own spec before any task
      picks it up. `cli.py:_cmd_init` carries an inline-docstring
      pointer for future readers.

    Acceptance: `docs/ROADMAP.md` lists `--adopt` under "Deferred
    from v2.0" with the one-line intent above; `CHANGELOG.md`
    (created by Task 27) has its `## [Unreleased]` content promoted
    to `## [2.0.0] — <date>`; the v2.0.0 release notes call out the
    Phase F bug-fix sweep so future readers understand why the
    final five tasks ship as `v2: implement …` rather than
    `v2: task …`.

#### Phase F — Contract-completion bugs (parallelizable) — ✅ shipped

Identified during the pre-tag audit (2026-05-20). Each item is a
RFC contract violation: the surface this RFC promised (either in
§"CLI surface (target)" or in §"What changes vs. v1") that
Tasks 1–22 did not deliver, OR (Task 26) a load-bearing functional
gap that breaks the value proposition of an already-shipped
surface. Commit messages use `v2: implement <subject>` rather
than `v2: task N` because these are bug fixes against the RFC,
not new task scope. PRs strike the corresponding bug from
Phase F's status in this RFC in the same commit.

All five Phase F items have shipped. Task 23 (`wiki upgrade`) was
the heaviest and landed last, running solo — after Tasks 24
(`wiki search`), 25 (`wiki journal {tail,grep,explain}`), 26
(vault-side `wiki-research` SKILL.md), and 27 (`CHANGELOG.md`)
had merged. Task 22 (README/ROADMAP, v2.0.0 release cut) is no
longer blocked.

23. **Task 23 — `wiki upgrade [--primitive <name>]`.** ✅ The
    headline v1→v2 capability from §"What changes vs. v1" (line
    151: `Bash sync scripts → pip install llm-wiki-kit; wiki
    upgrade`) shipped per `docs/specs/wiki-upgrade/`. New
    `llm_wiki_kit/upgrade.py` holds the pure `plan_upgrade` (which
    names version-changed primitives) plus `upgrade_primitives`
    (the runner: one `PrimitiveUpgradeEvent` per upgraded primitive,
    `safe_write`-routed `render_tree`, then a single
    `aggregate_region_contributions` pass over the full installed
    set). No new event type — `PrimitiveUpgradeEvent` already
    carried `from_version` + `to_version`. No install-pipeline
    contract change. ADR-0004 drift semantics preserved end-to-end
    (sidecar proposals on hash drift, no silent overwrites of
    user-edited files; `Wrote <path>.proposed` drift lines surface
    from BOTH per-primitive renders AND aggregator-emitted region
    drifts). Idempotency is a CLI concern (short-circuit on
    `plan.to_upgrade == []`), not a runner concern.
1. **Task 24 — `wiki search <query>`.** ✅ Ripgrep tier shipped per
    `docs/specs/wiki-search/`. Literal-substring scan over
    `<vault_root>/wiki/` with `--type` / `--tag` / `--status` /
    `--top` frontmatter filters; read-only (no journal events);
    `WikiError` on missing `rg`. FTS5 auto-upgrade tier remains
    future work (vault-side SKILL.md flags it as deferred).
1. **Task 25 — `wiki journal {tail,grep,explain}`.** ✅ Three new
    read-only handlers replace the `_stub()` callsites in `cli.py`:
    `tail [-n N]` (default 10) emits tab-separated rows;
    `grep [--type T] PATTERN` does case-sensitive substring match
    over canonical JSON (no-match exits 0, grep convention);
    `explain N` prints a human-readable block for the 1-based event
    line. Single-pass walk built on a new public
    `journal.parse_event_line` helper. Stdlib only.
1. **Task 26 — Vault-side `wiki-research` SKILL.md.** ✅ Vault-side
    SKILL.md at `core/files/skills/wiki-research/SKILL.md`
    (`name: wiki-research`) closing the Tasks 18/19 deferral chain
    — teaches Claude when to invoke `wiki research`, picks among
    the three providers by question shape against an installed
    `research-providers.yaml`, and propagates citations into
    downstream pages under the Two-Source Rule. Trigger eval at
    `tests/evals/trigger/test_wiki_research_trigger.py`; invariant
    suite pinning the SKILL against the CLI surface, the
    dispatcher's frontmatter dict, and each provider's
    `DEFAULT_MODEL` at `tests/unit/test_wiki_research_skill.py`;
    spec at `docs/specs/wiki-research-skill/`.
1. **Task 27 — `CHANGELOG.md`.** ✅ Created at repo root in
    Keep-a-Changelog 1.1.0 format. First entry is `## [Unreleased]`
    grouped by RFC phase, summarizing Tasks 1–22 + the four
    cross-cutting living specs. Task 22's release-cut promotes
    `[Unreleased]` to `[2.0.0]`.

### Pre-flight (what actually happened)

The original plan called for archiving the v1 tree under `archive/v1-*/`
before v2 work began. In flight we made a different call: the v1
files (`vault-templates/`, `shared/`, the v1 `scripts/sync-shared.sh`
and `check-sync.sh`, the `.github/workflows/check-sync.yml`) were
deleted on the v2 branch rather than moved under `archive/`. Git
history on `main` preserves anything a future maintainer needs to
reference, and no v2 task reads from the v1 tree at runtime — so the
archive directory would have been carry-only weight. Concern B4
(retro-review 2026-05-16) tracked the cleanup of the dangling
`check-sync` workflow and scripts that the archive plan had implied
would still be needed.

The migration plan artifact section 2 originally described the
v1-tree archive as a deferred step; this RFC supersedes that note.

### Per-task prompt template

For each task, open a fresh Claude Code session and use:

```
Work on Task <N> from docs/rfc/0001-v2-architecture.md.

Read:
- docs/rfc/0001-v2-architecture.md (section "Task <N>")
- docs/adr/000<X>-*.md (the relevant ADRs)
- The most recent commits on the working branch
- v1 history via `git log main -- <path>` if a task needs it
  (the v1 tree was deleted in-flight; see §"Pre-flight")

Produce exactly the outputs listed for Task <N>, nothing more. Don't
preview or start later tasks. Don't add runtime dependencies beyond
pyyaml, pydantic, and stdlib without writing a new ADR first.

Acceptance criteria are in the task spec. When you're done, run the
tests, run `wiki doctor` if it exists yet, and commit with a message
in the format: `v2: task <N> - <one-line summary>`.

If anything in the task spec is unclear, stop and ask before proceeding.
```

## Alternatives

### Alt 1: Start a fresh repo (`llm-wiki-kit-v2`)

Considered. Loses because the v1 git history, issue tracker, and stars
all live under the existing name, and the v1 tree is genuinely useful
reference material during migration. Evolving in place under a working
branch (with v1 history reachable via `git log main`) preserves
continuity at the cost of a busier branch graph for ~3 months.

### Alt 2: Keep multi-variant templates, add a sync engine

Considered. Loses because the variants drift faster than scripted sync
can keep up, and there's no clean upgrade path for end users. The
primitive model is the structural fix; sync is a workaround.

### Alt 3: Build on an existing tool (cookiecutter, copier, yeoman)

Considered. Cookiecutter and Copier are template-rendering CLIs but
don't model state, drift, or composition of multiple primitives into
one output. We'd be reimplementing most of the kit *and* paying their
dep cost. Stdlib + Pydantic costs less.

### Alt 4: One monolithic recipe (skip primitives)

Considered. Loses because the audiences are too different — a family
doesn't need `stakeholder-map-refresh`, a CX lead doesn't need
`meal-planning`. Without primitives, recipes become forks again.

## Drawbacks

- **Three months of two-branch maintenance.** `main` stays on v1 while
  `v2`-work-branch progresses. Mitigated by keeping `main` frozen
  (no v1 feature work) during the migration.
- **The kit's API is bigger than v1's.** A CLI with nine subcommands
  plus a primitive-authoring contract is more surface than `git clone`
  + edit-in-place. Mitigated by the recipes hiding most of it from
  end users; primitive authors are a smaller audience.
- **Pydantic v2 + Python ≥3.11 floor.** Excludes users on older
  Python installs. Mitigated: 3.11 is two years old at v2 release; we
  document the version requirement clearly.

## Unresolved questions

- **What's the exact CLI library?** Migration plan suggests Click; ADR
  not yet written. Decided at Task 2.
- **Does `wiki init` over a non-empty folder refuse, or offer an
  `--adopt` path?** Resolved post-v2.0: `wiki init` keeps its
  refuse-on-non-empty default; `wiki init --adopt` adopts a
  pre-existing folder by journaling kit-owned files as dedicated
  `PageAdoptedEvent` / `ManagedRegionAdoptedEvent` baselines before
  the install pipeline runs. Policy pinned in
  [ADR-0008](../adr/0008-init-adopt-ownership-policy.md); contract
  + plan in
  [`docs/specs/wiki-init-adopt/`](../specs/wiki-init-adopt/).
  Implementation queued (three PRs per the plan); the inline comment
  in `cli.py:_cmd_init` carries the same pointer.
- **Recipe inheritance (`extends:`)?** Out of scope for v2.0. Tier 3
  roadmap item.

## Outcome

On acceptance, this RFC produced:

- Five ADRs (0001–0005) capturing the load-bearing decisions.
- The 27-task migration sequence (22 original tasks plus the five
  Phase F contract-completion bugs surfaced during the pre-tag
  audit), with Task 1 (this set of docs) shipped first.
- A path to `v2.0.0` over ~3 months of incremental PRs.

Tracking PR: opened against `main`.
