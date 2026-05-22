# Plan: outcome-named entry points

> **Implementation plan paired with `spec.md`.** The spec says *what*;
> the plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** [`docs/specs/outcome-named-entry-points/spec.md`](spec.md)
- **Owner:** maintainer

## Approach

Nine landing-ready PRs, sequenced so each one leaves `main` green
without the next. The dependency arrow is **schema → catalog gate →
installer surfaces → helper → discovery → CLI router → doctor →
catalog rollout → release gate** — every shipped operation
declares zero outcomes today, so every PR up to the rollout is
dead code in production but exercises the new machinery against
fixture catalogs.
That keeps the catalog rollout a one-PR atomic flip (the first vault
sees verbs the moment the rollout merges) and prevents the
half-state where a contract declares `outcomes:` but the installer
doesn't yet write stubs.

The nine PRs follow the four phases the user asked for:

- **Schema / model changes (PR-1, PR-2).** `OperationContract` gains
  `outcomes: list[str]`; the naming-contract rules
  (`RESERVED_OUTCOME_VERBS`, `OUTCOME_VERB_STEMS`, shape regex, length,
  prefix block) and `check_outcome_verb_uniqueness` ship as pure
  functions. The unit-level catalog gate `test_outcome_verbs.py`
  walks the shipped catalog on every CI run, even before any
  operation declares a verb — trivially green today, a hard wall
  the moment a malformed verb lands.
- **Mechanical routers (PR-3, PR-4).** The installer's SKILL-fragment
  validator and the slash-stub writer in `install.py`, plus the
  parallel wiring inside `upgrade.upgrade_primitives` so vaults
  picked up via `wiki upgrade` gain new verbs without a fresh
  init. The `recipes.installed_outcome_verbs(vault_root)` helper
  the CLI reads (location per spec §"Contracts with other
  modules" — see §Risks for the alternative-home tradeoff).
  No new user-visible surface yet; everything still funnels
  through `wiki run <operation>`.
- **Surface wiring (PR-5, PR-6, PR-7).** Discovery surface
  (`wiki outcomes`, `wiki --help` epilog, `wiki init` post-install
  line), then the dynamic `wiki <verb>` alias dispatcher, then the
  `wiki doctor` orphan-stub reporter. Each surface is additive and
  testable independently.
- **Catalog rollout + release gate (PR-8, PR-9).** Declare verbs on
  the three worked-example operations (`weekly-digest → digest`,
  `meal-planning → plan-meals`, `stakeholder-map-refresh →
  refresh-stakeholders`); add the per-verb canonical eval prompts;
  wire the wheel-acceptance slow gate that asserts the
  SKILL-fragment invariant in the *installed* wheel.

Verification mode picks per task (default TDD for pure functions,
integration for CLI surfaces, an eval suite for SKILL triggering).
Each step's `Tests:` block precedes the `Approach:` block — tests
drive implementation, not the other way around.

### Declined patterns (commitments for REVIEW)

- **Tempted to lift outcome-verb state into a new module
  `llm_wiki_kit/outcomes.py`.** Declined per spec §Constraints 2 ("No
  new module under `llm_wiki_kit/`"): the work fits across the four
  modules the spec already names (`cli.py`, `models.py`,
  `primitives.py`, `install.py`) plus a thin helper in `recipes.py`.
  A new module would introduce a boundary worth its own RFC.
- **Tempted to add a `wiki alias`/`wiki shortcuts` umbrella
  subcommand instead of the single `wiki outcomes` reader.**
  Declined per spec §Constraints 5: the only static CLI addition is
  `wiki outcomes`. An alias/shortcuts family opens a `wiki list
  <topic>` namespace that the spec deliberately does not start.
- **Tempted to journal `OutcomeAliasInstalledEvent`/etc. so verb
  dispatch can replay from state.** Declined per spec §Constraints 6
  ("No new journal-event type"). Verb metadata is derived from each
  operation's `contract.yaml` and the existing
  `PrimitiveInstallEvent` set; nothing new needs replaying.
- **Tempted to auto-delete orphan `.claude/commands/<verb>.md`
  stubs when an operation is removed or shrinks its `outcomes:`
  list.** Declined per spec §Outputs §2 ("Orphan stubs are
  user-resolved, not auto-deleted"): the kit has no `safe_delete` or
  `PageDeleteEvent` yet, and inventing either is structural change
  outside this spec.
- **Tempted to render the slash-stub body via `str.format_map` so
  future templates can interpolate more context.** Declined per spec
  §Constraints 7 / §Outputs §2: the stub body is byte-stable; a
  constant template string keeps re-runs idempotent and avoids
  pulling `render.py` into the install path for a four-line
  markdown file.
- **Tempted to add a recipe-level `outcomes:` field so a recipe can
  override or namespace a primitive's verb.** Declined per spec
  §Constraints 8: recipes compose primitives; primitives own
  verbs. A recipe-level field would re-introduce the
  collision-across-recipes problem the spec resolves by making the
  catalog the namespace.
- **Tempted to interpolate the verb into `SKILL.md` via
  `format_map`.** Declined per spec §Constraints 7 / ADR-0001:
  SKILL.md remains a byte-for-byte copy. The "verb appears in
  description" rule is satisfied by authored text — verified, not
  generated.

## Pre-conditions

- Spec [`docs/specs/outcome-named-entry-points/spec.md`](spec.md) at
  Status: Draft. This plan does not amend the spec; any divergence
  between the spec and the constraints lands as a separate
  spec-amendment PR (see §Risks).
- The shipped operation catalog under `templates/operations/*` is
  the post-v2.0.0 baseline on `origin/main` (no `v2*` tag exists
  yet; the baseline is `main@<sha>` at PR-1's branch point). No
  primitive declares `outcomes:` today.
- [`task-17-wiki-run`](../task-17-wiki-run/spec.md) and
  [`wiki-run-exec`](../wiki-run-exec/spec.md) have shipped.
  Outcome verbs are sugar over `wiki run`'s dispatch path; this
  plan does not modify `_cmd_run`, `OperationContract`'s existing
  fields, or the `OperationRunEvent` shape.
- `safe_write` / `safe_write_region` ship per
  [ADR-0004](../../adr/0004-drift-detection-and-proposal-flow.md)
  and [`safe-write-ordering`](../safe-write-ordering/spec.md). The
  slash-stub writer in PR-3 calls `safe_write` directly; no
  predicate change.
- `install_primitives` (in `install.py`) is the shared install entry
  for `wiki init` and `wiki add` only — `wiki upgrade` uses a
  separate `upgrade.upgrade_primitives` path
  (`llm_wiki_kit/upgrade.py`, current implementation lines
  241–290) that re-renders trees and calls
  `aggregate_region_contributions` directly. PR-3 therefore
  wires the slash-stub writer into both call sites so `wiki
  upgrade` picks up new verbs automatically when the catalog
  rollout lands in PR-8 (the spec's Acceptance criterion
  "Backwards compatibility" requires this).
- No conflicting work in flight on `models.py:OperationContract`,
  `cli.py`'s top-level argparse build (`build_parser` plus the
  `INSTALL_VEHICLE_INIT` / `INSTALL_VEHICLE_ADD` constants at
  `cli.py:87-88`), `install.py:install_primitives`,
  `upgrade.py:upgrade_primitives` (which owns `UPGRADE_VEHICLE` at
  `upgrade.py:60`), `primitives.py`'s discovery path, or
  `doctor.py`'s orphan checks.

**Strict PR ordering: PR-1 → PR-2 → PR-3 → PR-4 → PR-5/6/7 (any
order) → PR-8 → PR-9.** PR-3 reads
`OperationContract.outcomes` (from PR-1) and the
`check_outcome_verb_uniqueness`/well-formedness helpers (from
PR-1/PR-2). PR-4's `installed_outcome_verbs` helper is consumed by
PR-5 (`wiki outcomes`), PR-6 (`wiki <verb>` dispatch), and PR-7
(`wiki doctor` orphan check). PR-8 is the **only** PR that flips a
shipped contract; it must merge AFTER PR-3 and PR-4 are in `main`
so the SKILL-fragment validator and slash-stub writer fire on the
first install/upgrade post-rollout. PR-9's slow gate runs against
the rollout, so it merges last.

## Steps

### PR-1 — `OperationContract.outcomes` field + naming-contract helpers

1. **`OperationContract` accepts `outcomes: list[str]` and rejects
   unknown extras.**
   - **Depends on:** none.
   - **Verification mode:** TDD.
   - **Tests** (extend `tests/unit/test_models.py` or add
     `tests/unit/test_outcome_verbs.py` and house schema tests there
     to keep grep results focused — see PR-2):
     - `test_operation_contract_accepts_outcomes_list` — YAML with
       `outcomes: [digest]` validates; `OperationContract.outcomes
       == ["digest"]`.
     - `test_operation_contract_defaults_outcomes_to_empty_list` —
       contract omitting `outcomes:` validates;
       `contract.outcomes == []`.
     - `test_operation_contract_rejects_unknown_field` — contract
       with `extras: foo` raises `ValidationError` (pins
       `_StrictModel`'s `extra="forbid"` continues to hold; spec
       Error case "Verb declared on a non-operation primitive"
       depends on this).
     - `test_operation_contract_outcomes_accepts_empty_explicitly`
       — `outcomes: []` parses to `[]` (spec Inputs §1 "An
       operation that omits the field, or sets it to `[]`, is
       reachable only via `wiki run <operation>`").
   - **Approach:** add `outcomes: list[str] = Field(
     default_factory=list)` to `models.py:OperationContract` at the
     same indentation as the existing `inputs`/`outputs` fields.
     No constructor changes; the Pydantic model handles defaulting.
   - **Verify:** `pytest tests/unit/test_outcome_verbs.py
     tests/unit/test_models.py` green.
2. **`RESERVED_OUTCOME_VERBS` and `OUTCOME_VERB_STEMS` constants
   live in `primitives.py` as module-level frozensets.**
   - **Depends on:** none (alongside step 1).
   - **Verification mode:** Goal-based.
   - **Tests:**
     - `test_reserved_outcome_verbs_matches_subcommand_set` —
       walk the parser built by `build_parser()`, collect every
       top-level subcommand name, and assert
       `RESERVED_OUTCOME_VERBS == subcommands | {"help",
       "version", "outcomes"}` (set equality, NOT just
       containment). Pins the spec's "literal enumeration of the
       current `wiki` subcommand set as registered in `cli.py`
       argparse plus the standard discovery aliases" rule
       (Inputs §2 rule 3) symmetrically — extra entries that
       don't correspond to subcommands are caught the same way
       missing entries are. The test imports
       `RESERVED_OUTCOME_VERBS` from `primitives.py` and
       `build_parser` from `cli.py`, so it spans the two
       modules at the test layer only.
     - `test_outcome_verb_stems_contains_bare_and_prefix_forms` —
       constants contain at minimum the illustrative entries the
       spec names (`digest`, `roll-up`, `plan-`, `refresh-`,
       `log-`, `summarize-`, `prep-`, `review-`, `track-`,
       `synthesize-`, `pack-`, `remind-`, `map-`). Pins the
       authoritative list so future PRs that extend the stems
       update the test alongside.
   - **Approach:** add two module-level frozensets to
     `primitives.py`, near the existing primitive-load
     constants. Comment cites the spec §Inputs §2 rules so a
     future reader knows where the authoritative source lives.
     **Constants live in `primitives.py`, not `cli.py`**, because
     the validator (`is_well_formed_outcome_verb`, step 3) reads
     them and the kit's dependency graph already has
     `cli.py → primitives.py` (see `cli.py:44`); reversing the
     direction (`primitives.py → cli.py`) would introduce a
     circular import for nothing. The spec's prose
     "registered in `cli.py` argparse" refers to the
     *enumeration source* (the subcommands argparse builds),
     not the storage location of the constant; the test above
     pins the round-trip.
   - **Verify:** `pytest tests/unit/test_outcome_verbs.py` green.
3. **`is_well_formed_outcome_verb(verb) -> None` raises `WikiError`
   on every violation; succeeds silently otherwise.**
   - **Depends on:** step 2 (uses both constants).
   - **Verification mode:** TDD.
   - **Tests** (extend `tests/unit/test_outcome_verbs.py`):
     - Parametrized over every well-formed example from spec
       §"Three concrete worked examples" plus illustrative stems:
       `digest`, `plan-meals`, `refresh-stakeholders`,
       `summarize-week`, `track-budget`. Each asserts no
       exception.
     - Parametrized over the spec's negative cases (Acceptance
       criteria, "Well-formed verb"): `a--b` (consecutive
       hyphens), `ab-` (trailing hyphen), `1ab` (leading digit),
       `Ab` (uppercase), `ab` (too short — 2 chars), a 25-char
       string (too long), `wiki-foo` (reserved prefix), `meals`
       (bare noun — fails verb-stem check), `weekly-summary`
       (adjective-noun, no verb stem), `doctor` (reserved). Each
       asserts a `WikiError` and that the message names which
       rule failed.
     - `test_well_formed_verb_locale_rejects_non_ascii` — input
       `digést`, expect `WikiError`. Pins spec §Inputs §2 rule 2.
   - **Approach:** new module-level function in `primitives.py`
     (next to the existing primitive validators) that consumes
     the constants from `cli.py`. Returns `None` on success,
     raises `WikiError` with a one-line message per rule. The
     function is the single mechanical-check site; both the
     catalog walker (PR-2) and the install-time validator (PR-3)
     call it.
   - **Verify:** `pytest tests/unit/test_outcome_verbs.py` green.
4. **`check_outcome_verb_uniqueness(contracts)` raises `WikiError`
   on collision.**
   - **Depends on:** step 1 (consumes
     `OperationContract.outcomes`).
   - **Verification mode:** TDD.
   - **Tests** (extend `tests/unit/test_outcome_verbs.py`):
     - `test_uniqueness_passes_with_disjoint_verbs` — feed two
       fixture contracts with disjoint `outcomes`; no exception.
     - `test_uniqueness_passes_with_empty_outcomes` — every
       contract has `outcomes == []`; no exception (mirrors the
       shipped catalog state until PR-8).
     - `test_uniqueness_fails_on_collision` — two contracts
       declare `outcomes: [digest]`. Assert `WikiError` whose
       message names both operations and the colliding verb.
       Pins spec §"Edge case — verb collision within the
       catalog".
     - `test_uniqueness_fails_on_verb_equals_operation_name` —
       contract `weekly-digest` declares `outcomes:
       [weekly-digest]`; second contract declares
       `outcomes: [refresh-stakeholders]`. Assert `WikiError`
       naming the cross-shadow. Pins spec Acceptance criterion
       "Verb does not shadow any operation name", including the
       declaring operation's own name.
   - **Approach:** new module-level function
     `primitives.check_outcome_verb_uniqueness(contracts:
     Iterable[OperationContract]) -> None`. Two passes — one for
     intra-catalog verb collisions, one for verb-vs-operation-name
     collisions (an inverse check across the same iterable). Pure
     function; no I/O.
   - **Verify:** `pytest tests/unit/test_outcome_verbs.py` green.
5. **PR-1 integration smoke.**
   - `pytest -m 'not slow'`, `ruff check llm_wiki_kit tests`,
     `ruff format --check llm_wiki_kit tests`,
     `mypy llm_wiki_kit tests` — all green.
   - Commit message: `v2: outcome-named entry points — schema +
     naming-contract helpers (PR-1 of 9)`.

### PR-2 — Catalog-load gate wired into discovery

1. **`primitives.discover_primitives` runs
   `check_outcome_verb_uniqueness` and per-verb well-formedness
   over the loaded set.**
   - **Depends on:** PR-1 (consumes
     `OperationContract.outcomes`, the verb constants, and both
     helpers).
   - **Verification mode:** TDD.
   - **Tests** (extend `tests/unit/test_primitives.py` or add
     `tests/unit/test_outcome_verbs_catalog.py` — the spec names
     `tests/unit/test_outcome_verbs.py` as the catalog gate, so
     fold both PR-1 and PR-2 tests under that single file to
     match the spec's reference and the `git grep outcomes
     tests/unit/test_*` pattern):
     - `test_discover_primitives_rejects_collision` — fixture
       catalog with two operation primitives declaring the same
       verb. Assert `WikiError`, message names both.
     - `test_discover_primitives_rejects_malformed_verb` —
       fixture with `outcomes: [bad--verb]`. Assert `WikiError`
       naming the rule.
     - `test_discover_primitives_rejects_reserved_verb` —
       fixture with `outcomes: [doctor]`. Assert `WikiError`.
     - `test_discover_primitives_rejects_verb_with_wiki_prefix`
       — fixture with `outcomes: [wiki-foo]`. Assert `WikiError`
       (rule 6 belt-and-braces).
     - `test_discover_primitives_accepts_v2_0_0_catalog` — point
       at the real shipped `templates/` tree (no
       `outcomes:`-declaring operation yet). Assert no
       exception, asserts every contract's `outcomes == []`.
       Pins the "vaults that predate this spec gain new
       surfaces on `wiki upgrade` and lose nothing" baseline.
     - `test_shipped_catalog_outcome_verbs_well_formed` — walks
       every shipped `templates/operations/*/contract.yaml` and
       runs every declared verb through
       `is_well_formed_outcome_verb`. Trivially green pre-PR-8
       (zero verbs); the wall the moment PR-8 declares any. Spec
       Acceptance criterion "Catalog-time uniqueness gate".
   - **Approach:** inside
     `primitives.discover_primitives`, after the existing
     per-primitive load loop, collect every `OperationContract`
     and call `check_outcome_verb_uniqueness(contracts)`; inside
     the per-primitive load (operation kind only), iterate
     `contract.outcomes` and call
     `is_well_formed_outcome_verb(verb)`. Both call sites raise
     `WikiError` directly; the spec contracts them as
     "primitive-load-time error[s], caught long before any vault
     sees [them]".
   - **Verify:** `pytest tests/unit/test_outcome_verbs.py` green;
     `pytest -m 'not slow'` green (no shipped contract declares
     a verb yet, so the production catalog passes unchanged).
2. **PR-2 integration smoke.**
   - Re-run the four gates; commit message `v2: outcome-named
     entry points — catalog-load gate (PR-2 of 9)`.

### PR-3 — Installer SKILL-fragment validator + slash-stub writer (init/add and upgrade paths)

**Fixture-catalog note.** PR-3's integration tests need a fixture
operation primitive that declares `outcomes:` — the shipped catalog
declares none until PR-8. PR-3 introduces
`tests/fixtures/outcome-catalog/` (mirroring the shape of the eval
factories' per-family seed directories under
`tests/fixtures/eval-vaults/`) containing two synthetic operation
primitives:

- `tests/fixtures/outcome-catalog/operations/fixture-digest/contract.yaml`
  declaring `outcomes: [fix-digest]` (verb passes
  `OUTCOME_VERB_STEMS` via the `prep-`/`refresh-`/etc. illustrative
  list — adjust the stem set in PR-1 step 2 if a fixture verb
  forces it).
- `tests/fixtures/outcome-catalog/operations/fixture-digest/files/skills/fixture-digest/SKILL.md`
  with frontmatter `description:` containing the verb verbatim.
- A second primitive
  `tests/fixtures/outcome-catalog/operations/fixture-skill-missing/contract.yaml`
  declaring `outcomes: [fix-skill-missing]`, and
  `tests/fixtures/outcome-catalog/operations/fixture-skill-missing/files/skills/fixture-skill-missing/SKILL.md`
  with frontmatter `description:` deliberately omitting the
  verb (used by step 2's negative-path tests).

The PR-3 tests load this fixture catalog via the same
`kit_root`-override pattern existing integration tests
(`tests/integration/test_wiki_add.py`) use to point the CLI at a
non-default catalog root.

1. **`install.write_outcome_slash_stubs(...)` writes one stub per
   declared verb under `<vault>/.claude/commands/<verb>.md` via
   `safe_write`, with the fixed-body template from spec §Outputs
   §2.**
   - **Depends on:** PR-1 (schema), PR-2 (catalog gate).
   - **Verification mode:** TDD + integration.
   - **Tests** (new file `tests/unit/test_install_outcomes.py`):
     - `test_write_outcome_slash_stubs_byte_stable` — call twice
       against a tmp vault with one synthetic
       outcome-declaring contract; assert the second call
       produces an identical journal slice (idempotent
       re-run). Pins spec §Outputs §2 "The stub is byte-stable:
       the same verb + operation + skill produces identical
       bytes every time".
     - `test_write_outcome_slash_stubs_creates_commands_dir` —
       parent dir absent; the helper creates
       `.claude/commands/` before writing.
     - `test_write_outcome_slash_stubs_body_matches_spec_template`
       — assert the written file contains the YAML frontmatter
       `description: Invoke the {operation} operation (alias:
       /{verb}).` and the two body lines verbatim per the spec
       template, with `{operation}`, `{skill}`, and `{verb}`
       substituted by ordinary Python `.format()` (either
       `.format()` or `format_map` is fine here — the stub body
       is a literal template constant inside `install.py`, not a
       SKILL.md byte-stable copy and not a `render.py`-driven
       file, so Constraint 7 / ADR-0001 do not apply). The
       `{skill}` substitution uses `contract.skill or
       contract.name` — same fallback as
       `recipes.installed_outcome_verbs` (PR-4) and
       `_cmd_run` (`run.py:508`), so `wiki outcomes` and the
       on-disk stub agree about `{skill}` for an operation
       that omits `skill:`.
     - `test_write_outcome_slash_stubs_skill_fallback_when_contract_skill_absent`
       — fixture contract with `outcomes: [<verb>]` and
       `skill:` omitted (legal per `models.py:158`,
       `skill: str | None = None`). Assert the written stub
       body contains `Run the \`<operation_name>\` skill`
       (i.e. the fallback to `contract.name` fires). Mirrors
       PR-4's
       `test_installed_outcome_verbs_falls_back_to_operation_name_when_skill_absent`
       at the stub-write boundary, so `wiki outcomes` and
       the on-disk stub agree about `{skill}` for the same
       operation.
     - `test_write_outcome_slash_stubs_routes_through_safe_write`
       — wrap `safe_write` with a spy; assert exactly one call
       per declared verb, with `by` equal to the calling
       vehicle constant — `INSTALL_VEHICLE_INIT` /
       `INSTALL_VEHICLE_ADD` (per `cli.py:87-88`) when the
       caller is `_cmd_init` / `_cmd_add`, or `UPGRADE_VEHICLE`
       (per `upgrade.py:60`) when the caller is `_cmd_upgrade`.
       The stub writer takes the vehicle string as a `by:`
       parameter from its caller; it does not introduce a new
       vehicle constant of its own (matches the existing
       region-aggregator convention in
       `install.aggregate_region_contributions`, see
       `install.py:223`).
   - **Integration tests** (new file
     `tests/integration/test_wiki_init_outcomes.py`, reusing the
     `kit_root` fixture pattern from
     `tests/integration/test_wiki_add.py`):
     - `test_wiki_init_writes_slash_stubs_for_declared_outcomes`
       — run `wiki init <tmp> --recipe <r>` against a fixture
       catalog with one outcome-declaring operation; assert
       `<vault>/.claude/commands/<verb>.md` exists with the
       expected body bytes. Pins spec Acceptance criterion
       "Slash stub written".
     - `test_wiki_init_no_stubs_when_no_outcomes_declared` —
       run against the v2.0.0 fixture catalog (no outcomes);
       assert `.claude/commands/` does not exist (no kit
       writes there).
     - `test_wiki_init_stub_drift_preserved_as_proposed` — run
       `wiki init`; modify the stub; run `wiki upgrade`. Assert
       the original (user-edited) stub remains, and the kit's
       version lands as `.proposed`. Pins spec Acceptance
       criterion "Slash stub drift" — standard `safe_write`
       proposal flow (ADR-0004).
2. **The installer fails fast when a declared verb is missing
   from the matching SKILL.md description.**
   - **Depends on:** step 1.
   - **Verification mode:** TDD + integration.
   - **Tests** (extend `tests/unit/test_install_outcomes.py` and
     `tests/integration/test_wiki_init_outcomes.py`):
     - `test_validate_skill_fragment_passes_when_verb_present`
       — fixture SKILL.md description includes the verb
       verbatim; no exception.
     - `test_validate_skill_fragment_fails_on_missing_verb` —
       fixture SKILL.md without the verb. Assert `WikiError`
       naming both files (contract path + SKILL.md path).
       Pins spec §Inputs §3 and §"Edge case — declared verb
       absent from SKILL.md".
     - `test_validate_skill_fragment_whole_word_match` — SKILL
       description contains `digestion` (substring of `digest`)
       but not `digest` as a whole word. Assert `WikiError` —
       the spec pins `\b<verb>\b`, so substring matches don't
       satisfy the rule.
     - Integration twin:
       `test_wiki_init_refuses_when_skill_missing_verb` — run
       `wiki init` against the
       `tests/fixtures/outcome-catalog/operations/fixture-skill-missing/`
       primitive (SKILL.md description deliberately omits the
       verb); assert exit 2, stderr names both paths, and the
       target vault has no `.wiki.journal/` directory. Pin
       this by placing the validator call in `_cmd_init` /
       `_cmd_add` / `_cmd_upgrade` **before** their
       `with journal.use_journal_cache(journal_path)` block
       (`cli.py:334`, `cli.py:511`, `cli.py:608`) — mirroring
       the existing `validate_contributions` pre-flight.
       `install_primitives` itself does not open the cache
       scope; its callers do.
   - **Approach:** new helper
     `install._validate_outcome_skill_fragments(primitives:
     Sequence[Primitive], sources: Mapping[str, Path]) -> None`,
     called from `_cmd_init`, `_cmd_add`, and `_cmd_upgrade`
     BEFORE their `journal.use_journal_cache` scope opens —
     same call site as the existing `validate_contributions`
     pre-flight. For each operation primitive whose contract
     declares one or more outcomes, the helper reads the
     matching `templates/operations/<name>/files/skills/
     <skill>/SKILL.md` from the resolved source path; parses
     the frontmatter `description:` field; runs `\b<verb>\b`
     against it for each declared verb (ASCII-only by spec
     §Inputs §2 rule 2, so the regex is safe in Python's
     default Unicode-aware mode); raises `WikiError` pointing
     at both files on the first miss.
     **Constraint check:** the helper does not call `safe_write`
     (the SKILL.md is in the *source* tree, not the vault);
     it's a read-only validator.
3. **Both `install_primitives` AND `upgrade.upgrade_primitives`
   call `write_outcome_slash_stubs` after their region pass,
   passing the caller's vehicle constant as the `by:`
   attribution.**
   - **Depends on:** steps 1 and 2.
   - **Tests** (extend
     `tests/integration/test_wiki_init_outcomes.py` and add
     `tests/integration/test_wiki_upgrade_outcomes.py`):
     - `test_wiki_init_outcome_event_ordering_after_region_pass`
       — run `wiki init` against the outcome-declaring fixture;
       assert the journal slice has shape `[VaultInitEvent,
       *PrimitiveInstallEvents, *PageWriteEvents (kit-side
       renders), *ManagedRegionWriteEvents (region aggregator),
       *PageWriteEvents (slash stubs by=INSTALL_VEHICLE_INIT)]`.
       Pins the stubs land after the region pass, so an
       interrupted install never leaves a stub without its
       owning operation.
     - `test_wiki_init_outcome_attribution_by_field` — every
       slash-stub `PageWriteEvent` has `by ==
       INSTALL_VEHICLE_INIT` (init) or `by ==
       INSTALL_VEHICLE_ADD` (add via the `_cmd_add` path);
       primitive renders keep their existing per-primitive
       attribution.
     - `test_wiki_upgrade_writes_stubs_for_newly_declared_outcomes`
       — pre-init a vault against the v2.0.0 fixture catalog
       (no outcomes); switch the catalog under the vault to
       `tests/fixtures/outcome-catalog/` (one verb declared);
       run `wiki upgrade`; assert
       `.claude/commands/fix-digest.md` lands on disk with
       `by == UPGRADE_VEHICLE` in the journal. Pins spec
       Acceptance criterion "Backwards compatibility" — vaults
       built before the spec gain stubs via `wiki upgrade`
       with no journal-replay errors. (Moved from step 1 to
       here so the upgrade-path wiring is co-located with the
       failure it would expose.)
     - `test_wiki_upgrade_outcome_attribution_by_field` —
       every upgrade-written stub `PageWriteEvent` has
       `by == UPGRADE_VEHICLE`.
   - **Approach:**
     - **Init/add path:** extend `install_primitives`'s final
       block (after `aggregate_region_contributions`) with a
       call to `write_outcome_slash_stubs(all_installed,
       sources, journal_path, by=install_vehicle)`. The
       stub writer walks **`all_installed`**, not
       `to_install` — symmetric to how
       `aggregate_region_contributions` already operates on
       the full closure (`install.py:240`), so a `wiki add`
       of a non-outcome-declaring primitive doesn't drop
       stubs for already-installed primitives. The `by:`
       argument reuses the existing `install_vehicle:`
       parameter `install_primitives` already accepts
       (per `install.py:265`).
     - **Upgrade path:** extend `upgrade.upgrade_primitives`
       (after `aggregate_region_contributions` at
       `upgrade.py:271-276`) with a call to
       `write_outcome_slash_stubs(plan.all_installed, sources,
       journal_path, by=UPGRADE_VEHICLE)`. The
       `all_installed` slice is the right closure: vaults
       that gain a verb across the upgrade need a stub written
       even if the verb-declaring primitive was not itself
       upgraded (e.g. a recipe-wide catalog migration where
       `weekly-digest`'s contract gains `outcomes: [digest]`
       but its version did not bump).
   - **No new vehicle constant.** `INSTALL_VEHICLE_OUTCOMES`
     was tempting but unnecessary — stubs attribute to their
     calling vehicle (init / add / upgrade), matching how
     `aggregate_region_contributions` already works. This
     keeps the `by:` namespace tied to user-facing CLI
     entries, not internal install phases.
4. **PR-3 integration smoke.**
   - Re-run the four gates; commit message `v2: outcome-named
     entry points — installer SKILL-fragment + slash-stub
     pipeline (init/add/upgrade) (PR-3 of 9)`.

### PR-4 — `recipes.installed_outcome_verbs` helper

1. **`installed_outcome_verbs(vault_root) -> dict[str, tuple[str,
   str]]` returns the verb → (operation, skill) mapping from the
   journal-replayed installed primitive set.**
   - **Depends on:** PR-1, PR-3 (PR-3 doesn't gate this
     functionally, but landing the slash-stub writer first
     means the discovery surface PR-5 wires up sees stubs on
     disk for visual confirmation during manual QA).
   - **Verification mode:** TDD.
   - **Tests** (new file `tests/unit/test_installed_outcomes.py`):
     - `test_installed_outcome_verbs_empty_for_v2_0_0_vault` —
       seed a vault from the v2.0.0 fixture (no declared
       outcomes); assert `{}`.
     - `test_installed_outcome_verbs_returns_verb_to_operation_map`
       — seed a tmp vault, write a journal with a
       `PrimitiveInstallEvent` for a fixture
       outcome-declaring operation, place the fixture catalog
       on disk via the test's `kit_root`; call
       `installed_outcome_verbs(vault_root)`; assert
       `{"digest": ("weekly-digest", "weekly-digest")}`.
     - `test_installed_outcome_verbs_skips_removed_primitives`
       — journal has `PrimitiveInstallEvent` then
       `PrimitiveRemoveEvent` (matches `models.py:251`) for
       the same operation; assert
       the verb is NOT in the returned dict (matches
       `installed_primitives` semantics).
     - `test_installed_outcome_verbs_picks_up_upgrade_version` —
       `PrimitiveUpgradeEvent` advancing an operation's version
       to one whose `contract.yaml` declares a new verb. Assert
       the new verb appears. Pins the upgrade-time visibility
       AC `Backwards compatibility` already exercises end-to-end
       at the integration level.
   - **Approach:** new module-level helper in `recipes.py`
     (matches the spec §"Contracts with other modules" table:
     "`recipes.installed_outcome_verbs(vault_root)`"). The
     helper:
     1. Resolves the journal path
        (`<vault_root>/.wiki.journal/journal.jsonl`).
     2. Calls `journal.read_events` + `journal.replay_state` to
        get the installed-primitive set.
     3. For each installed operation primitive, loads its
        `contract.yaml` via the existing primitive-load
        machinery (resolved against the kit's bundled
        `templates/operations/<name>/`).
     4. Returns `{verb: (operation_name, skill_name) for
        verb in contract.outcomes}` where `skill_name =
        contract.skill or operation_name`. The fallback
        mirrors `_cmd_run`'s existing resolution
        (`run.py:508`, documented in `wiki run --help` at
        `cli.py:1919` as `<contract.skill or operation>`),
        so operations that name their skill identically to
        their operation can legally omit `skill:` and still
        declare `outcomes:`. A construction test
        (`test_installed_outcome_verbs_falls_back_to_operation_name_when_skill_absent`)
        pins the fallback against a fixture
        contract with `outcomes: [<verb>]` and no
        `skill:` declared.
     The helper is pure (no writes); callers that need a
     vault-context check (e.g. PR-6's CLI dispatcher) handle
     the "outside vault" case themselves before calling.
2. **PR-4 integration smoke.**
   - Re-run the four gates; commit message `v2: outcome-named
     entry points — installed_outcome_verbs helper (PR-4 of 9)`.

### PR-5 — `wiki outcomes` + `wiki --help` epilog + `wiki init` post-install message

1. **`wiki outcomes` prints the installed-verb table sorted by
   verb, two-space column gutter, empty for vaults with no
   declared verbs.**
   - **Depends on:** PR-4 (`installed_outcome_verbs`).
   - **Verification mode:** TDD + integration.
   - **Tests** (new file `tests/integration/test_wiki_outcomes.py`):
     - `test_wiki_outcomes_empty_vault_prints_nothing` — fresh
       `wiki init` against v2.0.0 fixture catalog; run `wiki
       outcomes`; assert exit 0, stdout is empty (or contains
       only a header line if the spec requires one — the spec
       in §Outputs §4 shows no header, so assert truly empty).
     - `test_wiki_outcomes_renders_table_sorted_by_verb` —
       fixture vault with three declared verbs (`digest`,
       `plan-meals`, `refresh-stakeholders`); assert stdout
       equals the spec's worked example table verbatim,
       modulo column widths that auto-size to the widest
       entry per column. Pin column widths by asserting the
       gutter is exactly two spaces.
     - `test_wiki_outcomes_outside_vault_errors` — run from a
       directory with no `.wiki.journal/`; assert exit 2 with
       a clear message (matches the same vault-scoping rule
       the `wiki <verb>` dispatcher uses in PR-6).
     - `test_wiki_outcomes_takes_no_flags` — `wiki outcomes
       --anything` errors with argparse's standard
       "unrecognized arguments" message (pins spec §Outputs §4
       "takes no flags in v1").
   - **Approach:** add a `_cmd_outcomes(args) -> int` function
     to `cli.py`; register the subparser
     `outcomes` in `build_parser()`; render the table by
     calling `installed_outcome_verbs(vault_root)` and printing
     the sorted rows. Column widths via `max(len(...))` over
     the three columns; gutter is a literal two spaces. No new
     argparse arguments; no flags.
2. **`wiki --help` epilog names `wiki outcomes` as the
   discovery surface.**
   - **Depends on:** step 1.
   - **Verification mode:** Goal-based.
   - **Tests:**
     - `test_wiki_help_epilog_mentions_wiki_outcomes` — run
       `wiki --help` via subprocess; assert stdout contains
       the literal `wiki outcomes` reference per spec §Outputs
       §4. Pins spec Acceptance criterion "`wiki --help`
       epilog".
   - **Approach:** add the one-line epilog to the top-level
     `argparse.ArgumentParser` constructor in `build_parser`:
     `epilog="Run \`wiki outcomes\` to see this vault's
     operation verbs."`.
3. **`wiki init` post-install message mentions `wiki outcomes`
   when the resolved recipe ships at least one
   outcome-declaring operation.**
   - **Depends on:** step 1, PR-4.
   - **Verification mode:** TDD + integration.
   - **Tests** (extend `tests/integration/test_wiki_outcomes.py`):
     - `test_wiki_init_mentions_wiki_outcomes_when_recipe_has_verbs`
       — fixture catalog declaring one verb; run `wiki init`;
       assert stdout contains the `wiki outcomes` reference
       in the post-install summary block.
     - `test_wiki_init_silent_about_outcomes_when_recipe_has_none`
       — v2.0.0 fixture (no verbs); assert stdout does NOT
       contain `wiki outcomes`. Pins spec Acceptance
       criterion "`wiki init` post-install message".
   - **Approach:** extend `_cmd_init`'s tail with a conditional
     line that re-reads the just-rendered installed set (via
     `installed_outcome_verbs(target)`) and prints the
     reference iff the dict is non-empty.
4. **PR-5 integration smoke.**
   - Re-run the four gates; commit message `v2: outcome-named
     entry points — wiki outcomes + help epilog +
     post-install message (PR-5 of 9)`.

### PR-6 — Dynamic `wiki <verb>` alias dispatcher

1. **`wiki <verb>` recognized when, and only when, the active
   vault declares `<verb>`; rewrites to `wiki run <operation>`
   and forwards args.**
   - **Depends on:** PR-4 (helper), PR-5 (so the discovery
     surface exists for the spec §Behavior happy path step 5
     end-to-end claim — strictly, PR-6 could land before
     PR-5, but the manual-QA story is cleaner if `wiki
     outcomes` exists first).
   - **Verification mode:** TDD + integration.
   - **Tests** (new file
     `tests/integration/test_wiki_verb_dispatch.py`):
     - `test_wiki_verb_dispatches_to_wiki_run_path` — fixture
       vault with `weekly-digest` installed and `outcomes:
       [digest]`. Run `wiki digest --window 2026-W18`; assert
       it reaches the same `_cmd_run` code path as `wiki run
       weekly-digest --window 2026-W18`. Pin by asserting the
       resulting `OperationRunEvent` matches the operation
       name, status, and arguments. Pins spec Acceptance
       criterion "CLI alias".
     - `test_wiki_verb_argument_forwarding` — `wiki digest
       --window 2026-W18 --theme "easy"` reaches `_cmd_run`
       with the same `argparse` namespace as the
       wiki-run-equivalent. Pin by capturing the inner call's
       parsed `op_args` and asserting structural equality.
       Pins spec Acceptance criterion "Argument forwarding".
     - `test_wiki_verb_help_preamble_then_run_help_body` — run
       `wiki digest --help`; assert exit 0; assert stdout
       starts with a one-line alias preamble (e.g. `(alias
       for \`wiki run weekly-digest\`)`) followed by the
       output of `wiki run weekly-digest --help`. Pin by
       diffing the trailing body bytes against the
       wiki-run-help output. Pins spec Acceptance criterion
       "`wiki <verb> --help`".
     - `test_wiki_verb_invalid_choice_outside_vault` — run
       `wiki digest` from a directory with no
       `.wiki.journal/`; assert exit 2 with the literal
       message `outcome verbs are vault-scoped; run inside a
       vault or use 'wiki run <operation>'`. Pins spec
       Acceptance criterion "CLI alias outside vault".
     - `test_wiki_typo_outside_vault_falls_through_to_argparse`
       — run `wiki dgest` (kebab-shape match for the verb
       regex, but obviously a typo) from a directory with no
       `.wiki.journal/`; the vault-scoped `WikiError` fires
       (matches the spec §Edge case wording). And: `wiki
       not_a_verb` (underscore — fails the kebab regex from
       spec §Inputs §2 rule 1) from outside a vault gets
       argparse's standard "invalid choice" error, NOT the
       vault-scoped message. Pins the dispatcher only
       swallows verb-shaped tokens outside a vault;
       manifestly-non-verb input keeps argparse's native
       error path even outside a vault. **Inside** a vault,
       both kebab-shaped and non-kebab unknown tokens go
       through argparse's `error()` override (PR-6 step 6),
       producing one unified shape. The outside-vault
       narrowing is flagged as a spec-clarification
       candidate in §"Spec drift to flag in PR body".
     - `test_wiki_verbose_position_invariant_for_verbs` —
       fixture vault with `outcomes: [digest]`. Run each of
       `wiki --verbose digest --window 2026-W18` and `wiki
       digest --verbose --window 2026-W18`; assert exit 0,
       and assert the resulting `OperationRunEvent`s are
       structurally equal modulo their `--verbose` flag
       routing. Pins one canonical position for `--verbose`
       relative to outcome verbs — for v1, **`--verbose` is
       a top-level flag and MUST precede the verb token**
       (mirrors `_cmd_run`'s pre-existing constraint that
       `--verbose` precedes the subcommand, per the
       inline comment at `cli.py:1933-1935`). The test asserts
       the post-verb form is either accepted-equivalently (if
       the dispatcher forwards it to `_cmd_run`'s `op_args`
       remainder, where `_cmd_run` itself rejects it) or
       rejected with a clear "place --verbose before the verb"
       message. Pick one and commit; flag in PR body.
     - `test_wiki_verb_unknown_verb_inside_vault_lists_choices`
       — fixture vault with `outcomes: [digest]`; run `wiki
       nonsense`; assert exit 2 AND stderr matches argparse's
       native `error: argument ...: invalid choice:` prefix
       AND stderr contains the installed-verb list suffix
       appended by the dispatcher's `ArgumentParser.error()`
       override. Pins spec §Outputs §1 "argparse's standard
       'invalid choice' error fires with the canonical list
       of installed outcomes printed alongside the built-in
       commands" verbatim.
     - `test_wiki_operation_name_not_implicit_verb` — fixture
       vault with `weekly-digest` installed and `outcomes:
       [digest]`. Run `wiki weekly-digest`; assert exit 2
       with the standard "invalid choice" error (NOT a
       silent rewrite to `wiki run weekly-digest`). Pins
       spec Acceptance criterion "Operation names are not
       implicit verbs".
     - `test_wiki_verb_global_commands_still_work_outside_vault`
       — `wiki --help`, `wiki init`, `wiki doctor` all
       function unchanged outside a vault. Pins spec
       Acceptance criterion "Vault-scoped CLI dispatch".
     - `test_wiki_argparse_error_override_falls_through_for_non_invalid_choice`
       — fixture vault with `outcomes: [digest]`. Run `wiki
       run --bogus-flag weekly-digest` (an argparse error
       inside a vault that is NOT a top-level "invalid
       choice"). Assert exit 2, stderr matches argparse's
       native error shape, and stderr does **not** contain
       the installed-verb list suffix. Pins the
       `ArgumentParser.error()` override's narrow scope —
       only the top-level "invalid choice" path gets the
       suffix; every other argparse error stays untouched.
     - `test_wiki_verb_dispatch_forwards_kit_root` — call
       `cli.main(["digest", "--window", "2026-W18"],
       kit_root=tmp_kit_root)` against a tmp-vault whose
       installed `weekly-digest` primitive was loaded from
       `tmp_kit_root`'s catalog (with `outcomes: [digest]`
       declared). Assert exit 0 and the inner
       `OperationRunEvent` resolves the operation from the
       overridden catalog — pins that the recursive
       re-dispatch forwards `kit_root` instead of falling
       back to the bundled resolver.
   - **Approach:** extend `cli.main(argv)` so that:
     1. The parser is built statically with the existing
        global subcommands (per RFC-0001 §"CLI surface").
     2. `cli.main` peeks at `argv[0]` (if any). If it matches
        any static subcommand OR begins with `-` (a flag,
        e.g. `--help`/`--version`), do nothing — argparse
        handles it on the existing path.
     3. Otherwise classify the token. If it does not match
        the kebab regex from spec §Inputs §2 rule 1
        (`^[a-z][a-z0-9]*(-[a-z0-9]+)*$`), pass through to
        argparse unchanged — argparse's standard "invalid
        choice" error fires (e.g. `wiki not_a_verb`,
        `wiki Foo`).
     4. If the token is verb-shaped, resolve the CWD's
        journal path. **No journal** (`wiki dgest` outside a
        vault): raise
        `WikiError("outcome verbs are vault-scoped; run
        inside a vault or use 'wiki run <operation>'")`.
        This matches the spec's §Outputs §1 contract
        verbatim. The pre-argparse interception is narrow
        (verb-shaped tokens only) so typos like
        `wiki not_a_verb` keep argparse's native error.
     5. **Journal present**: call
        `installed_outcome_verbs(cwd)`. If `argv[0]` is in
        the verb map:
        a. **`--help` / `-h` in `argv[1:]`** — print the
           one-line alias preamble
           (`(alias for \`wiki run <operation>\`)`) to
           stdout, then re-dispatch with `argv = ["run",
           "<operation>", "--help"]` so `_cmd_run`'s
           existing `--help` pre-scan (at `cli.py:1925-1928`
           per the inline comment) prints the operation
           help. Test pins the two outputs concatenate;
           no double-print.
        b. **Otherwise** — rewrite to
           `["run", "<operation>", *argv[1:]]` and
           re-dispatch via `cli.main(rewritten_argv,
           kit_root=kit_root_override)`, forwarding the
           original `kit_root` from the dispatcher's entry
           (so test seams via `cli.main(argv,
           kit_root=tmp_path)` survive the re-dispatch).
           The recursive call enters argparse once, hits
           `_cmd_run`, and the `argparse.REMAINDER` capture
           on `op_args` (cli.py:1929-1937) carries the
           forwarded arguments unchanged. A construction
           test
           (`test_wiki_verb_dispatch_forwards_kit_root`)
           pins the override survives.
     6. If `argv[0]` is verb-shaped but NOT in the installed
        verb map: let argparse handle the parse normally —
        argparse's standard "invalid choice" error fires
        verbatim (spec §Outputs §1 contracts this shape).
        To append the installed-verb list to argparse's
        message, the dispatcher builds the top-level
        `ArgumentParser` with a small subclass that
        overrides `error(message)` to splice
        `; installed outcome verbs in this vault:
        <verb_list>` into the trailing message **only when
        CWD is a vault AND the error is the "invalid choice"
        path on the top-level subcommand**. The override
        falls through to `super().error(message)` otherwise,
        so every other argparse error keeps its native
        shape. This preserves the spec's "argparse's
        standard 'invalid choice' error fires" contract
        verbatim — no custom `WikiError` shape, no
        divergence from argparse's exit code (2). The
        `test_wiki_verb_unknown_verb_inside_vault_lists_choices`
        test asserts exit 2 AND stderr matches argparse's
        native `error: argument <name>: invalid choice:`
        prefix AND stderr contains the installed-verb list
        suffix. The same path covers `test_wiki_verb_*_not_a_verb`
        cases from step 3 (kebab-regex failures inside a
        vault): both go through the same overridden
        `error()`, producing one error shape rather than
        two.
     The rewrite is a wrapper around `cli.main`'s entry
     path, not a new subparser per verb — dynamic
     subparser registration would force a journal read on
     every `wiki --help` invocation outside a vault, which
     the verb-shape check above avoids.
     **No recursion guard needed:** the verb-shape check
     and the `argv[0] in static_subcommands` check together
     mean the rewritten `["run", ...]` re-dispatch
     terminates after one recursion (the second pass sees
     `run` as a static subcommand).
   - **Verify:** `pytest
     tests/integration/test_wiki_verb_dispatch.py
     tests/integration/test_cli.py` green (the existing CLI
     suite must continue passing — pins Invariant 2 of the
     spec: "The contract surface is unchanged").
2. **PR-6 integration smoke.**
   - Re-run the four gates; commit message `v2: outcome-named
     entry points — dynamic CLI alias dispatcher (PR-6 of 9)`.

### PR-7 — `wiki doctor` orphan-stub reporter

1. **`wiki doctor` reports an `orphan` for each
   `.claude/commands/<verb>.md` whose verb is not in the
   installed-verb set.**
   - **Depends on:** PR-4 (`installed_outcome_verbs`).
   - **Verification mode:** TDD + integration.
   - **Tests** (new file `tests/unit/test_doctor_outcomes.py`):
     - `test_doctor_reports_orphan_stub_after_outcome_dropped`
       — seed a vault with one declared verb, run install,
       then patch the fixture catalog to drop the verb (or
       remove the operation), then call
       `run_doctor(vault_root, kit_root)`. Assert the
       returned issue list contains one entry naming
       `.claude/commands/<dropped-verb>.md` as orphan AND
       naming the dropped verb. Pins spec Acceptance
       criterion "`wiki doctor` flags orphan stubs".
     - `test_doctor_clean_on_verb_enabled_vault_no_user_edits`
       — fixture vault with declared verbs; no user edits;
       assert `run_doctor` returns zero issues. Pins spec
       Acceptance criterion "`wiki doctor` clean on a
       verb-enabled vault".
     - `test_doctor_clean_on_v2_0_0_vault` — v2.0.0 fixture
       (no outcomes); assert zero outcome-related issues.
       Pins the additive-only invariant.
     - `test_doctor_ignores_user_owned_command_files` —
       fixture vault with declared verbs; user hand-creates
       `.claude/commands/myown.md` (no `PageWriteEvent`
       exists for this path); assert `run_doctor` does NOT
       flag the file as orphan. Pins the kit-vs-user
       distinction the spec §Non-goal 9 and §Outputs §2
       contract: "user can already write their own slash
       command in `.claude/commands/`; the kit doesn't need
       to manage user-defined aliases." The orphan filter is
       `(on_disk_command_file and NOT in installed_verb_set
       AND path in state.page_writes)`.
   - **Approach:** extend `doctor.py` with a new
     `_check_outcome_orphan_stubs(state, vault_root,
     kit_root)` function called from `run_doctor`. The
     function:
     1. Lists `<vault>/.claude/commands/*.md` files on disk.
     2. For each file, derives the verb from the filename
        (stem).
     3. Looks up the installed-verb set via
        `installed_outcome_verbs(vault_root)`.
     4. For each on-disk verb file NOT in the installed set,
        emits an `Issue(ORPHAN, "<.claude/commands/...>",
        details=f"dropped verb {verb} — operation no longer
        installed or no longer declares this outcome")`.
     The function only flags files the kit knows it
     previously wrote (filter on whether the path is in
     `state.page_writes`); user-created files in
     `.claude/commands/` (per spec Non-goal 9, "user can
     already write their own slash command in
     `.claude/commands/`") are not orphans. Spec §Outputs §2
     names this kit-vs-user distinction.
2. **PR-7 integration smoke.**
   - Re-run the four gates; commit message `v2: outcome-named
     entry points — wiki doctor orphan-stub reporter (PR-7
     of 9)`.

### PR-8 — Catalog rollout: declare `outcomes:` on the three worked-example operations

1. **`templates/operations/weekly-digest/contract.yaml` declares
   `outcomes: [digest]` and the matching SKILL.md description
   includes `digest`.**
   - **Depends on:** PR-1, PR-2, PR-3 (validators must be in
     place); PR-4, PR-5, PR-6, PR-7 (so the user-visible
     surfaces work the moment the catalog declares verbs).
   - **Verification mode:** Goal-based.
   - **Tests:**
     - `test_shipped_catalog_outcome_verbs_well_formed` (from
       PR-2) now exercises three real verbs; the test moves
       from trivially-green to substantively-green.
     - `test_wiki_init_personal_recipe_shows_digest_in_outcomes_table`
       — run `wiki init --recipe personal`; run `wiki
       outcomes`; assert stdout contains the `digest` row.
     - `test_wiki_init_family_recipe_shows_plan_meals_and_digest`
       — run `wiki init --recipe family`; assert `wiki
       outcomes` lists both `digest` and `plan-meals`.
     - `test_wiki_init_work_os_recipe_shows_refresh_stakeholders_and_digest`
       — run `wiki init --recipe work-os`; assert `wiki
       outcomes` lists `digest` and `refresh-stakeholders`.
     - The three integration tests above pin spec §"Three
       concrete worked examples" end-to-end.
   - **Approach:** edit three contract YAMLs and three
     SKILL.md description blocks. No Python changes.
2. **`templates/operations/meal-planning/contract.yaml` declares
   `outcomes: [plan-meals]`; matching SKILL.md describes the
   verb.**
   - **Approach:** same shape as step 1.
3. **`templates/operations/stakeholder-map-refresh/contract.yaml`
   declares `outcomes: [refresh-stakeholders]`; matching
   SKILL.md describes the verb.**
   - **Approach:** same shape as step 1.
4. **Example vaults regenerate cleanly; diff is bounded to
   the rollout's surfaces.**
   - **Tests:**
     - `examples/regenerate.py --check` exits 0 after PR-8's
       regenerate-and-commit step (the existing integration
       suite for Task 21 runs this gate).
   - **Approach:** run `examples/regenerate.py --apply` and
     inspect the diff. The **expected** diff set is:
     (a) new files `.claude/commands/<verb>.md` for each
     verb the example's recipe ships;
     (b) edits to `wiki/skills/<skill>/SKILL.md` (or the
     equivalent in-vault skill path used by each example)
     reflecting the verb additions to the source SKILL.md
     descriptions in PR-8 steps 1–3;
     (c) journal `PageWriteEvent` entries for both of the
     above, if the example commits its journal.
     Anything outside that set (other vault files changing,
     unrelated regions re-aggregating, ordering shifts) is a
     red flag — investigate before commit. The PR
     description enumerates the per-example diff so the
     reviewer can cross-check without re-running the
     regenerate.
5. **PR-8 integration smoke.**
   - Re-run the four gates; commit message `v2: outcome-named
     entry points — declare outcomes on weekly-digest,
     meal-planning, stakeholder-map-refresh (PR-8 of 9)`.

### PR-9 — Eval trigger fixture + wheel-acceptance slow gate

1. **Per-shipped-verb canonical eval prompts trigger the
   matching SKILL via natural language.**
   - **Depends on:** PR-8.
   - **Verification mode:** Eval (Claude-driven via
     subprocess).
   - **Tests** (new file
     `tests/evals/trigger/test_outcome_verbs_trigger.py`):
     - Parametrized over the three shipped verbs with the
       canonical prompts from spec Acceptance criterion
       "Eval trigger":
       - `digest` → `"Give me last week's digest."`
       - `plan-meals` → `"Help me plan our meals for next
         week."`
       - `refresh-stakeholders` → `"Refresh the stakeholder
         map for the pluto project."`
     - Each test drives Claude Code via subprocess against a
       per-verb eval vault built by a fixture factory in
       `tests/evals/conftest.py` (extending the existing
       `weekly_digest_vault` / `conflict_pending_vault`
       factory pattern documented in
       `tests/fixtures/eval-vaults/README.md`). New
       factories: `digest_vault` (reuses `weekly-digest`
       primitive set), `meal_planning_vault` (family-recipe
       subset including `meal-planning`),
       `stakeholder_map_refresh_vault` (work-os subset
       including `stakeholder-map-refresh`). The
       per-family seed directories live under
       `tests/fixtures/eval-vaults/<family>/`. The prompt
       MUST NOT name the SKILL or the `wiki run` command —
       pins spec Acceptance criterion "Eval trigger"
       verbatim.
     - A meta-check: walk every shipped
       `templates/operations/*/contract.yaml` with declared
       outcomes; assert the eval suite has a parametrized
       case for each verb. Pins the spec's "New verbs added
       to the catalog must add a matching prompt fixture in
       the same PR" rule mechanically.
   - **Approach:** mirror the existing `tests/evals/trigger/`
     suite's shape (per Task 20). The meta-check lives as a
     small unit test in `tests/unit/test_eval_fixture_completeness.py`
     so a missing fixture surfaces in CI without running the
     slow eval suite.
2. **Wheel-acceptance slow gate asserts SKILL fragments are
   present in the installed wheel.**
   - **Depends on:** PR-3, PR-8.
   - **Verification mode:** Goal-based slow test.
   - **Tests** (new file
     `tests/integration/test_wheel_acceptance_outcomes.py`,
     marked `@pytest.mark.slow`):
     - Build the wheel, install into a temporary venv, walk
       the installed `_assets/templates/operations/*` tree,
       assert every contract's declared `outcomes:` appear
       in the matching SKILL.md description. Mirrors PR-3's
       fail-fast validator but against the *built and
       installed* wheel — pins the wheel-bundling spec
       (`docs/specs/wheel-bundled-assets/spec.md`) hasn't
       silently dropped SKILL files. Spec Acceptance
       criterion "Wheel-acceptance SKILL-fragment gate".
   - **Approach:** model on the existing
     `tests/integration/test_wheel_acceptance.py` (or its
     equivalent slow gate); reuse the wheel-build/install
     helpers that Task 21 / `wheel-bundled-assets` already
     ship. `pytest -m slow` runs this; CI's `pytest -m 'not
     slow'` skips it.
3. **PR-9 integration smoke.**
   - Run the four gates plus `pytest -m slow
     tests/integration/test_wheel_acceptance_outcomes.py`.
   - Commit message `v2: outcome-named entry points — eval
     trigger + wheel-acceptance gate (PR-9 of 9 — release
     gate)`.

## Verification gate

Each PR runs the standard four-gate sequence per
[`AGENTS.md` § Commands you'll need](../../../AGENTS.md#commands-youll-need):

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

PR-9 additionally runs `pytest -m slow
tests/integration/test_wheel_acceptance_outcomes.py` and the eval
suite via `pytest tests/evals/trigger/`. CI does not gate on
`pytest -m slow`; the maintainer runs it locally and in the
release-cut workflow.

End-to-end verification (post PR-9):

- All 18 acceptance criteria from `spec.md` pass.
- `wiki init --recipe family <tmp>` produces a vault with
  `.claude/commands/digest.md` and `.claude/commands/plan-meals.md`
  on disk; `wiki outcomes` lists both verbs; `wiki digest --help`
  prints the alias preamble + `wiki run weekly-digest --help`
  body; `wiki plan-meals --window 2026-W22 --theme "easy"`
  dispatches the same code path as the explicit `wiki run`
  invocation; `wiki doctor` reports zero issues.
- Deleting `templates/operations/weekly-digest/contract.yaml`'s
  `outcomes:` line and re-running `wiki upgrade` makes `wiki
  doctor` flag `.claude/commands/digest.md` as orphan; the user
  can `rm` the file; `wiki doctor` returns clean.
- The eval suite, run locally on Claude Code, fires the
  matching SKILL for each of the three canonical prompts
  without mentioning the SKILL or `wiki run` in the prompt.

## Risks

- **Dynamic argparse dispatch is the heaviest single
  change.** PR-6 inserts a pre-`parse_args` hook in
  `cli.main` that reads the journal before argparse runs.
  Risks:
  (a) journal-read latency on every `wiki <verb-shaped>`
  invocation inside a vault;
  (b) failure modes when the journal is mid-write or
  corrupt (the hook calls `journal.read_events`, which can
  raise `JournalCorruptError`);
  (c) interaction with the global `--verbose` flag (already
  has positional-ordering quirks per the comment at
  `cli.py:1933-1935`);
  (d) interaction with `_cmd_run`'s own `--help` pre-scan at
  `cli.py:1925-1928` — the alias preamble must concatenate
  cleanly with the run-help body without double-print.
  Mitigation: the verb-shape gate (kebab regex from spec
  §Inputs §2 rule 1) means tokens that obviously aren't
  verbs — `--help`, `init`, `not_a_verb` — never reach the
  journal read. `journal-reader-cache` (already shipped)
  keeps the read sub-millisecond when it does fire.
  `JournalCorruptError` surfaces with the same exit shape
  as every other journal-reading command (`wiki doctor`,
  `wiki journal tail`); the dispatcher does not silently
  fall back. The `--verbose` test in PR-6 (verb-position
  invariant) pins the canonical position, and the `--help`
  preamble test pins the concatenation. PR-6's
  adversarial-reviewer pass should focus on this surface
  specifically.
- **`installed_outcome_verbs` is a hot path on every `wiki
  <verb>` invocation.** Reading + replaying the journal on
  every CLI call costs more than the equivalent static
  argparse lookup. Mitigation: `journal-reader-cache`
  (already shipped) means the read is sub-millisecond for
  typical journal sizes; PR-4 calls `read_events` once per
  CLI invocation, not per verb. A construction test
  asserting `len(read_events_called) == 1` would over-pin
  the implementation; instead, the existing CLI tests' wall
  time is the canary — if PR-6 introduces a noticeable
  regression on `wiki --help` outside a vault, surface in
  review.
- **Catalog rollout (PR-8) creates the first user-visible
  change.** Up to PR-7, every PR is dead code in
  production. PR-8 makes the rollout atomic, but it ALSO
  changes the `examples/regenerate.py --check` snapshot
  (because the example vaults now ship slash stubs).
  Mitigation: regenerate examples in the same PR; flag the
  diff in the PR body; tests pin the expected file set
  per recipe.
- **`wiki upgrade` interaction.** Vaults built before PR-8
  upgrade to gain slash stubs and the dynamic dispatcher.
  Spec Acceptance criterion "Backwards compatibility"
  contracts this. Risk: a user who hand-edited
  `.claude/commands/<verb>.md` after a third-party
  workflow (per spec Non-goal 9) sees their file land as
  `.proposed` on the next upgrade. Mitigation: this is the
  standard `safe_write` proposal flow — the spec accepts
  this as the cost of the kit owning the verb namespace.
  The `wiki doctor` orphan check (PR-7) names the file so
  the user can investigate.
- **SKILL-fragment whole-word check (`\b<verb>\b`) is
  Unicode-aware in Python `re` by default.** A verb like
  `digest` matched against a description containing
  `digestion` could match if the regex used `(?<!\w)digest(?!\w)`
  semantics in a non-ASCII locale. Mitigation: spec §Inputs
  §2 rule 2 already restricts verbs to ASCII; the
  validator can use `re.search(rf"\\b{verb}\\b", desc)`
  safely because both sides are ASCII. A construction test
  pins the whole-word semantics against the substring
  case (PR-3 step 2's
  `test_validate_skill_fragment_whole_word_match`).
- **`recipes.installed_outcome_verbs` home.** The spec's
  §"Contracts with other modules" table puts the helper in
  `recipes.py`, but the helper reads
  `state.installed_primitives` (a journal-replay output) and
  the operation `contract.yaml`s — it doesn't touch recipe
  shapes at all. A more natural home would be `primitives.py`
  (next to `check_outcome_verb_uniqueness`) or a small new
  `cli.py` helper section. The plan follows the spec's
  contracted location verbatim to avoid silent spec drift;
  if the adversarial reviewer prefers a different module,
  fold the move into PR-4 and flag the spec amendment in
  the same PR. Either way, this is a **spec drift
  candidate** (see §"Spec drift to flag in PR body").
- **Slash-stub user edits before the kit owns the file.**
  A user installs the kit, manually creates
  `.claude/commands/digest.md` themselves (perhaps copied
  from another vault), THEN PR-8 ships and `wiki upgrade`
  tries to write the same path. `safe_write` sees no prior
  `PageWriteEvent` for the path, so it falls into the "no
  prior knowledge" branch and overwrites silently —
  violating the user's expectation. Mitigation: the
  installer's `safe_write` call uses the standard "no
  prior knowledge" branch per ADR-0004 / `safe-write-ordering`;
  this is the same trade-off every kit-owned file makes
  at first-write time. The user's recourse is to back up
  their file before the upgrade. Document this in the
  spec or accept it; the spec is currently silent. **PR
  body to flag this for follow-up.**

## Spec drift to flag in PR body

The plan honors the spec verbatim, but landed-in-passing as
the plan was written, the following candidates surfaced as
amendments worth a follow-up PR. Each is noted here so the
PR body can lift them en bloc; none alter this plan's task
shape.

1. **Outside-vault behavior on a verb-shaped typo.** Spec
   §Outputs §1 says `wiki <verb>` outside a vault errors
   with the vault-scoped `WikiError`; spec §Edge cases says
   "argparse falls through". The plan narrows this to
   verb-shaped tokens only (kebab regex), so
   `wiki not_a_verb` keeps argparse's native error. A
   spec amendment could pin this narrowing explicitly.
2. **`--verbose` position relative to outcome verbs.** Spec
   is silent; the plan pins `--verbose` MUST precede the
   verb (mirroring `_cmd_run`'s existing pre-scan
   constraint). Spec amendment to make this explicit.
3. **`installed_outcome_verbs` module home.** Spec
   §"Contracts with other modules" puts it in `recipes.py`;
   the helper reads journal-replayed state, not recipe
   shapes. If PR-4 relocates it (per §Risks), the spec
   table needs amending in the same PR.
4. **Slash-stub user-created before the kit owns it.** Spec
   is silent on the case where a user hand-creates
   `.claude/commands/<verb>.md` before the kit's first
   write to the path; `safe_write`'s "no prior knowledge"
   branch will overwrite silently. The spec should name
   this case (either as a known limitation or via a
   pre-write existence check that routes to `.proposed`).
5. **PR-3 install-vehicle attribution.** The plan drops
   `INSTALL_VEHICLE_OUTCOMES` in favor of attributing stubs
   to the calling vehicle (init / add / upgrade). The spec
   §Outputs §2 prose doesn't specify a `by:` value; the
   amendment would pin "stubs attribute to their calling
   CLI vehicle, not a separate stub-writer vehicle."
6. **`v2.0.0` baseline tag.** Spec §Inputs §1 says
   `outcomes` is "purely additive against the v2.0.0
   baseline (currently tagged)". No `v2*` tag exists in
   the repo (only `pre-coauthor-rewrite-backup`). Spec
   amendment to read "additive against the post-v2-
   development baseline on `origin/main`."

### Resolved during implementation

- **Constants in `primitives.py`, not `cli.py`** (was item 7).
  Spec §Inputs §2 rules 3 and 4 were amended in PR-1 to
  reference `llm_wiki_kit/primitives.py:RESERVED_OUTCOME_VERBS`
  and `llm_wiki_kit/primitives.py:OUTCOME_VERB_STEMS` directly,
  and the prose now points at
  `cli.py:build_parser()` as the enumeration source with the
  unit test
  (`test_reserved_outcome_verbs_matches_subcommand_set`) as
  the drift pin. Per AGENTS.md, spec/code drift is a bug —
  fixed in-PR rather than deferred.

## Out of scope

### Deferred per spec Non-goals

- **Internationalization of verb names** (spec Non-goal 3) —
  future ADR.
- **A first-run bootstrap wizard that surfaces verbs** (spec
  Non-goal 4) — future spec.
- **A recipe-selector UI** (spec Non-goal 5) — out of scope.
- **A TUI for browsing verbs** (spec Non-goal 6) — out of scope.
- **Outcome verbs on content-type primitives** (spec Non-goal
  7) — future spec.
- **Outcome verbs on infrastructure primitives** (spec
  Non-goal 8) — future spec.
- **Per-vault user-defined verb aliases** (spec Non-goal 9) —
  out of scope.
- **Recipe-level `outcomes:` field** (spec Non-goal 10) — out
  of scope.
- **Removing or modifying any existing `wiki` subcommand**
  (spec Non-goal 11) — out of scope.
- **A `safe_delete` / `PageDeleteEvent` for auto-deleting
  orphan stubs** (spec §Outputs §2) — future ADR.

### Deferred to follow-up PRs (this plan's choice)

- **Outcome verbs on operations beyond the three worked
  examples.** Other operations in the catalog (`trip-prep`,
  `follow-up-tracker`, `medical-summary`,
  `action-item-rollup`, `renewal-reminders`,
  `onboarding-pack`, `status-synthesis`) can add
  `outcomes:` in follow-up PRs; the machinery PR-1 through
  PR-7 ships supports them with zero further code changes.
  PR-8 ships only the three operations the spec's worked
  examples name; other operations follow as their authors
  reach for verbs.
