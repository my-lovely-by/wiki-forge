# Spec: outcome-named entry points

> **Living document.** Updated alongside the code. Drift between spec
> and code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit/cli.py`, `llm_wiki_kit/models.py`,
  `llm_wiki_kit/recipes.py`, `llm_wiki_kit/install.py`, plus the
  catalog under `templates/operations/*/contract.yaml` and each
  matching `templates/operations/*/files/skills/<op>/SKILL.md`.
- **Related:** RFC-0001 §"CLI surface (target)" (the contract this
  spec adds sugar onto); ADR-0003 (managed regions — the slash-stub
  pipeline reuses `safe_write`); ADR-0004 (drift detection — slash
  stubs are kit-owned and route through `safe_write`); ADR-0006
  (additive contributions — informs the install-time aggregation
  shape); [`docs/specs/task-17-wiki-run/spec.md`](../task-17-wiki-run/spec.md)
  (operation execution contract — outcomes are sugar over this);
  `docs/CHARTER.md` §Mission and Principle 5
  ("library-not-application" — the tension this spec names).
- **Constrained by:** Charter Principle 3 (no new runtime
  dependency); Charter Principle 5 (library-not-application —
  outcomes are declarative metadata + mechanical routers, not a new
  application layer); ADR-0004 (every kit write through
  `safe_write` / `safe_write_region`); RFC-0001 §"Runtime
  constraints"; AGENTS.md §"Check before acting" (no new top-level
  directory; no module boundary without RFC).

## What this is

Outcome-named entry points are a UX sugar layer over the existing
operation surface: a small set of human-readable verbs (`digest`,
`plan-meals`, `refresh-stakeholders`) that a user can reach for from
the shell, from Claude Code's slash-command palette, or in
natural-language prompts, and that resolve to the same operation
the contract already names. The operation primitive's
`contract.yaml` declares its verbs; the kit derives every surface
(CLI alias, Claude Code slash stub, SKILL trigger fragment) from
that declaration. Nothing about `wiki run <operation>` or the
operation-contract machinery changes — outcomes are an additive
shortcut, never a replacement.

This spec does **not** change *what* operations do, *which*
operations ship, or *how* `wiki run` resolves a contract. It adds
one optional metadata field, three derived surfaces, and the rules
that keep them consistent.

### Tension to name

The Charter pulls in two directions on this exact question:

- **Mission** (a non-engineer keeping a useful wiki) argues for
  verbs that match how the user thinks: "plan our meals", not
  "run the meal-planning operation".
- **Principle 5** (library-not-application) argues against the kit
  inventing a presentation layer — Claude is the application; the
  kit is the library Claude calls.

The resolution this spec proposes: outcome verbs are *declarative
metadata on the operation primitive* (a library concern — the
operation declares what it's called when humans reach for it), and
every surface that exposes them is a *mechanical router or text
fragment derived from that declaration* (no new control flow, no
new state). The kit ships data + routing; the application stays
Claude + the shell.

### The four options considered

The brief poses four ways to surface an outcome verb. This spec
picks **(d) a combination, with the rule that the operation
primitive owns the verb and every surface is derived from it**.

- **(a) CLI aliases only** — `wiki digest` shells out to
  `wiki run weekly-digest`. Discoverable from the terminal; invisible
  inside a Claude session that the user reaches without the CLI.
  Misses the dominant access pattern: a user opening their vault in
  Claude Code and saying "give me last week's digest."
- **(b) Recipe-level shortcuts** — a recipe declares its outcome
  verbs, the CLI exposes them only when that recipe is installed.
  Pushes naming into recipe YAML, which is exactly where collisions
  happen (family's `digest` ≠ work-os's `digest`). Two recipes
  inventing the same verb for different operations is exactly the
  kind of fuzzy overlap the Charter §Scope already warns against.
  Rejected because it relocates the naming problem from the
  catalog (one global namespace, easy to validate) into recipes
  (N independent namespaces, only checked at install time).
- **(c) Vault-side slash commands only** — a `/daily-review` Claude
  skill the user invokes inside Claude Code, no CLI involvement.
  Tightest fit to the Charter Mission (matches the heyitsnoah
  /thinking-partner pattern that motivated this spec). Two
  problems: it skips the terminal user who reaches `wiki` directly,
  and a pure vault-side surface as the *only* surface would lock
  outcome-shaped discovery to one agent's command palette in a way
  the Charter explicitly does not want. Slash stubs *as one of three
  surfaces* are fine — they degrade gracefully when absent and do
  not lock the user out of any markdown editor — but a pure-slash
  spec would re-introduce an application layer the kit deliberately
  does not ship.
- **(d) Combination, primitive-owned** — *picked*. The operation
  primitive's `contract.yaml` declares `outcomes: [verb, ...]`. The
  installer derives three surfaces: a CLI alias `wiki <verb>`, a
  generated Claude Code slash stub at `.claude/commands/<verb>.md`,
  and a required natural-language trigger fragment inside the
  operation's `SKILL.md` description. One declaration; three
  derivations; no recipe-level naming.

The rule that resolves recipe-collision (family's `digest` ≠
work-os's `digest`) is: **the catalog is the namespace**. Two
operation primitives in `templates/operations/` may not declare the
same outcome verb. Verb conflict is a primitive-load-time error,
caught long before any vault sees it.

This rule rests on one explicit assumption: **every shipped recipe
that installs a given operation primitive does so with identical
contract intent.** The three shipped recipes today (`family`,
`work-os`, `personal`) satisfy this — the recipes compose
*primitives*, not divergent forks of one primitive; if `family`
and `personal` both list `weekly-digest`, they both want
*that exact operation's contract* and that operation's `digest`
verb. If a future audience needs different digest semantics, the
correct shape is to ship a distinct operation primitive (e.g.
`family-digest`) with a distinct outcome verb (e.g.
`household-digest`). Forking a verb across recipes is not
supported and not in scope here.

A user who installs two recipes simultaneously into one vault is
already an unsupported case (Charter §Scope, personal-recipe
note, work-os/family separation); this spec does not change that.

## Inputs

This spec defines the contract for three categories of input.

### 1. Catalog input — what an operation primitive declares

`templates/operations/<name>/contract.yaml` gains one optional field:

```yaml
name: weekly-digest
description: ...
period: weekly
skill: weekly-digest
outcomes:
  - digest
inputs: { ... }
outputs: { ... }
```

`outcomes` is a list of zero or more **outcome verbs**. An operation
that omits the field, or sets it to `[]`, is reachable only via
`wiki run <operation>` and through its existing SKILL description —
no CLI alias, no slash stub. The field is purely additive against
the v2.0.0 baseline (currently tagged); vaults that predate this
spec gain new surfaces on `wiki upgrade` and lose nothing.

### 2. Naming contract — what makes a verb well-formed

A well-formed outcome verb satisfies *every* rule below. Failure is
a `WikiError` at primitive load time (caught by `wiki init`, `wiki
add`, and the catalog-validation unit tests):

1. **Shape**: kebab-case, ASCII only, matching
   `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`. No leading digit, no consecutive
   hyphens, no trailing hyphen. Total length 3–24 characters.
2. **Locale**: English-only for v1. Internationalization is an
   explicit non-goal (see Non-goals).
3. **Reserved-word block**: the verb may not equal any name in
   `llm_wiki_kit/primitives.py:RESERVED_OUTCOME_VERBS`. That
   constant is the union of every top-level `wiki` subcommand
   registered in `cli.py:build_parser()` and the standard
   discovery aliases (`help`, `version`, `outcomes`). The set is
   updated in `primitives.py` in the same PR that adds or
   removes a top-level subcommand in `cli.py`;
   `tests/unit/test_outcome_verbs.py::test_reserved_outcome_verbs_matches_subcommand_set`
   pins the two sources of truth against each other so the drift
   trips CI.
4. **Verb-form**: must match the verb shape "verb stem optionally
   followed by `-<object>`". Concretely: either the whole verb is
   one of the bare-verb entries in
   `llm_wiki_kit/primitives.py:OUTCOME_VERB_STEMS` (illustrative
   entries: `digest`, `roll-up`), or the verb starts with
   `<stem>-<object>` where `<stem>` is one of the prefix entries
   in the same constant (illustrative entries: `plan-`,
   `refresh-`, `log-`, `summarize-`, `prep-`, `review-`,
   `track-`, `synthesize-`, `pack-`, `remind-`, `map-`). The
   illustrative lists above are *examples*, not the authoritative
   set; the canonical list lives in `primitives.py:OUTCOME_VERB_STEMS`
   and is extended in the same PR that adds an operation needing
   a new stem. Bare nouns (`meals`, `stakeholders`) and
   adjective-noun shapes (`weekly-summary`) are rejected.
5. **Global uniqueness in the catalog**: across all
   `templates/operations/*/contract.yaml`, a given verb appears
   at most once. The aggregator that surfaces this check lives
   in `llm_wiki_kit/primitives.py:check_outcome_verb_uniqueness`.
6. **No `wiki` prefix**: a verb may not start with `wiki-` —
   that surface is already the CLI itself. (Belt-and-braces:
   rule 4 already rejects any verb whose stem isn't in
   `OUTCOME_VERB_STEMS`, but this rule guards against a future
   maintainer accidentally registering a `wiki-` prefix in that
   constant.)

### 3. Vault input — what the installer reads at write time

At `wiki init` / `wiki add` / `wiki upgrade` time, for each
installed operation primitive whose contract declares one or more
outcome verbs:

- Read the contract; the verbs are part of the
  `OperationContract` Pydantic model (see Contracts with other modules).
- Resolve the matching `SKILL.md` at
  `templates/operations/<name>/files/skills/<skill>/SKILL.md` (where
  `<skill>` is the contract's `skill:` field).
- Confirm every declared verb appears verbatim, as a whole word
  (regex `\b<verb>\b`), inside the SKILL.md frontmatter
  `description:` string. If a verb is declared but missing from
  the description, fail with a `WikiError` pointing at both files.
  This is the *natural-language trigger fragment* surface — it is
  authored, not generated, because SKILL.md remains a byte-for-byte
  copy (no Jinja, no `format_map` for SKILL.md; see ADR-0001).

## Outputs

For every installed operation with at least one declared outcome
verb, the kit emits the following.

### 1. CLI alias resolution

`wiki <verb>` is recognized by `cli.py`'s top-level argparse
dispatcher when, and only when, the active vault's journal records
a `PrimitiveInstallEvent` for an operation whose contract declares
that verb. The dispatcher's behavior:

- **Match**: `wiki <verb> [args]` is rewritten internally to
  `wiki run <operation> [args]` and run through the existing
  `_cmd_run` path. Each dynamically-registered verb subparser
  inherits the same `argparse.REMAINDER` shape as `wiki run` so
  arbitrary operation arguments (`--window 2026-W18`, `--theme
  "easy"`, etc.) forward through unchanged.
- **Help**: `wiki <verb> --help` prints the underlying operation's
  help — the same output as `wiki run <operation> --help` — with a
  one-line preamble noting the alias relationship.
- **No match**: argparse's standard "invalid choice" error fires
  with the canonical list of installed outcomes printed alongside
  the built-in commands.
- **No vault context**: `wiki <verb>` outside a vault directory
  errors with `WikiError("outcome verbs are vault-scoped; run inside
  a vault or use 'wiki run <operation>'")`. The built-in commands
  remain global.
- **Operation names are not implicit verbs.** Typing `wiki
  weekly-digest` (the bare operation name) is **not** rewritten to
  `wiki run weekly-digest` unless `weekly-digest` is also a
  declared outcome verb. The naming-contract rules ensure verbs and
  operation names occupy disjoint sets in practice.

No new journal event type is introduced — the existing
`OperationRunEvent` (per RFC-0001 §Phase D) records the run; the
verb is sugar over the same call.

### 2. Claude Code slash stub

At install time, the installer writes one stub per declared verb to
`<vault>/.claude/commands/<verb>.md` via `safe_write`. The stub is
a fixed-body markdown file (YAML frontmatter + two body lines):

```
---
description: Invoke the {operation} operation (alias: /{verb}).
---
Run the `{skill}` skill from this vault. See the SKILL's own
`when to load` section for inputs.
```

The stub is byte-stable: the same verb + operation + skill produces
identical bytes every time, so re-running `wiki upgrade` is a no-op
in the absence of drift. User edits to the stub trigger the normal
`safe_write` proposal flow (a `.proposed` sidecar appears in
`.claude/commands/`). The stub directory is created if missing.

**Orphan stubs are user-resolved, not auto-deleted.** When a
verb is dropped (either because the operation is removed or
because its `outcomes:` list shrinks across a `wiki upgrade`),
the kit does **not** delete the corresponding stub file. Deletion
is intentionally outside this spec's scope because `safe_write`
is write-only, no `PageDeleteEvent` exists, and Constraint 6
forbids new journal-event types. Instead:

- The CLI dispatcher stops recognizing the orphan verb on the
  next run (Outputs §1).
- `wiki doctor` reports the orphan stub as `orphan` against the
  current installed-outcomes set, surfacing it for the user.
- The user removes the stub by hand, the same way they would
  remove any other file in their vault.

A future ADR may add `safe_delete` and a `PageDeleteEvent` so
the kit can clean orphans automatically; until that ADR lands,
this spec ships orphan-tolerant behavior.

Slash stubs are an additive surface: Claude Code reads them as
slash commands; agents that don't honor `.claude/commands/` simply
ignore the directory. They are *not* an Obsidian-specific feature
and do not break vaults opened in plain markdown editors.

### 3. SKILL description fragment

No new file is generated for this surface — the authored SKILL.md
already carries the verb in its `description:` string (the rule in
Inputs §3 enforces this). The installer's only role is the
verification step: it refuses to write if the SKILL.md is missing
a declared verb.

### 4. `wiki outcomes` output

A new read-only subcommand `wiki outcomes` prints the installed
verb table:

```
verb                  operation                 skill
digest                weekly-digest             weekly-digest
plan-meals            meal-planning             meal-planning
refresh-stakeholders  stakeholder-map-refresh   stakeholder-map-refresh
```

Output is plain text. Columns auto-size to the widest entry per
column (a two-space gutter between columns), rows are sorted by
verb. The subcommand takes no flags in v1. It is the canonical discovery surface; `wiki
--help` mentions it once in its epilog (e.g. `Run \`wiki outcomes\`
to see this vault's operation verbs.`) and does not enumerate verbs
inline (the charter target of dozens of installed primitives across
recipes means inline enumeration does not scale past ~10 verbs
cleanly). `wiki init`'s final post-install line also mentions
`wiki outcomes` so a first-time user sees the discovery surface
without having to know to look for it.

The name is a single new top-level subcommand — not a
`wiki list outcomes` (which would open a `wiki list <topic>`
subcommand family this spec does not want to start without an
RFC).

## Behavior

### Happy path — declaring a new outcome verb

1. Operation author edits `templates/operations/<op>/contract.yaml`
   and adds `outcomes: [<verb>]`.
2. Operation author edits the operation's SKILL.md description to
   include the verb as a natural-language trigger phrase.
3. The catalog-load unit test (`tests/unit/test_outcome_verbs.py`)
   passes: the verb is well-formed, unique, and present in the
   SKILL description.
4. A user runs `wiki upgrade` (or `wiki init --recipe …` for a new
   vault). The installer:
   - reads each installed operation's contract,
   - validates each declared verb (re-runs the catalog checks
     against the now-resolved installed set),
   - writes one slash stub per verb via `safe_write`,
   - appends no new journal-event types (slash stubs are
     kit-owned files; their writes already journal as
     `PageWriteEvent` per the existing `safe_write` contract).
5. `wiki outcomes` shows the new verb. `wiki <verb>` works
   from the terminal. `/<verb>` works inside Claude Code. Natural
   language ("give me the weekly digest") triggers the SKILL via
   the description fragment.

### Edge case — verb collision within the catalog

Two operations declare the same verb. The catalog-load check
(`primitives.py:check_outcome_verb_uniqueness`) raises a
`WikiError` listing both offending operations. The kit refuses to
build the wheel, run tests, or install anything until one of the
two operations renames its verb. This is a *primitive-author-time*
failure, not a *user-time* failure.

### Edge case — declared verb absent from SKILL.md

Operation declares `outcomes: [refresh-stakeholders]` but the
SKILL's description text doesn't mention it. The installer (and
the catalog unit test) raises `WikiError` naming both the
contract and the SKILL path. This is the same enforcement shape
as the "primitive declares a contributes_to but the snippet file
is missing" check that already lives in `install.py`.

### Edge case — verb collides with a built-in `wiki` subcommand

An operation declares `outcomes: [doctor]`. Caught by the
reserved-word block in the naming-contract rules (Inputs §2). Same
failure mode as the collision case.

### Edge case — `wiki <verb>` outside a vault

User runs `wiki digest` from `~/`. The CLI cannot resolve a journal,
so it cannot know which verbs are installed. Behavior: argparse
falls through; if `<verb>` matches a global reserved word (it
won't, by construction), it dispatches; otherwise it errors with
the message in Outputs §1 ("outcome verbs are vault-scoped …").
The built-in commands behave unchanged outside a vault.

### Edge case — user edits a slash stub

User opens `.claude/commands/digest.md` and rewrites it. Next
`wiki upgrade` detects the hash mismatch against the last
`PageWriteEvent` for that path; the rewrite lands as
`.claude/commands/digest.md.proposed`. `wiki doctor` surfaces the
drift. This is the standard ADR-0004 flow — no new mechanism.

### Edge case — operation renamed or removed

An operation that previously declared `outcomes: [digest]` is
removed from a vault, or has its `outcomes:` list shrunk (e.g.
from `[digest, summarize-week]` to `[digest]`) by a kit upgrade.
On the next `wiki upgrade`:

1. The installer recomputes the installed outcome set from the
   replayed journal + the new catalog. The dropped verb is no
   longer in the set.
2. The CLI dispatcher stops recognizing the dropped verb on the
   next invocation (Outputs §1).
3. The existing slash stub at `.claude/commands/<dropped-verb>.md`
   remains on disk. `wiki doctor` flags it as `orphan`. The user
   removes it by hand. (See Outputs §2 for why the kit does not
   auto-delete.)

The user-facing message in `wiki doctor` names the dropped verb
and points at the file so the user can delete it without
guessing.

### Error case — verb declared on a non-operation primitive

A content-type or ontology declares `outcomes:`. The
`OperationContract` model lives only on operations; content-type
and ontology manifests have no `outcomes` field. A YAML key the
schema doesn't know about is rejected by `_StrictModel`'s
`extra="forbid"`. Caught at catalog-load time.

## Invariants

These must hold before, during, and after every install,
upgrade, or removal:

1. **One declaration, every surface.** Every CLI alias, every slash
   stub, and every SKILL trigger fragment for verb `<v>` traces back
   to exactly one operation's `contract.yaml`.
2. **The contract surface is unchanged.** `wiki run <operation>`
   accepts the same flags, raises the same errors, and writes the
   same outputs whether or not the operation declares any verbs.
3. **The verb namespace is the catalog.** Across the shipped
   catalog, every declared verb appears at most once.
4. **No silent overwrite.** Slash stubs route through `safe_write`;
   user edits surface as `.proposed` sidecars.
5. **Catalog-time failures, not user-time failures.** Verb
   collisions, missing SKILL fragments, and ill-formed verbs are
   caught when the catalog loads — long before a user vault sees
   them. A user is never the first to discover a malformed verb.
6. **Additive only.** A vault upgraded from a build that predates
   this spec gets new slash stubs and a new CLI router, and loses
   nothing. The journal grows no new event types.
7. **Vault-scoped CLI dispatch.** `wiki <verb>` resolves verbs only
   from the active vault's installed operations; global `wiki`
   subcommands keep their current vault-independent behavior.
8. **Operation names and outcome verbs occupy disjoint sets.** A
   bare operation name (`weekly-digest`, `meal-planning`) is not
   routable as `wiki <name>`; only declared outcome verbs are. The
   `RESERVED_OUTCOME_VERBS` set and the catalog uniqueness check
   together prevent a verb from accidentally shadowing an
   operation name.

## Contracts with other modules

| Caller | What it calls | What changes |
|---|---|---|
| `cli.py` top-level dispatcher | `recipes.installed_outcome_verbs(vault_root)` (new) | New helper returning `dict[verb, (operation, skill)]` from the journal-replayed installed primitive set. |
| `cli._cmd_outcomes` (new) | Same helper | Renders the table for `wiki outcomes`. |
| `install.py` (existing region aggregator) | `write_outcome_slash_stubs(...)` (new) | Called from `install_primitives` after the existing region pass. One `safe_write` per stub. |
| `doctor.py` | `installed_outcome_verbs(...)` | Reads the installed verb set; reports orphan stubs at `.claude/commands/*.md` whose verb is not in the current set. |
| `models.py:OperationContract` | n/a (schema change) | Adds `outcomes: list[str] = Field(default_factory=list)`. |
| `primitives.py` | `check_outcome_verb_uniqueness(...)` (new) | Run from the catalog-load path; raises `WikiError` on collision or malformation. |
| Eval suite | `tests/evals/trigger/test_outcome_verbs_trigger.py` (new) | Parametrized over shipped operations with declared verbs; asserts an outcome-shaped natural-language prompt loads the matching SKILL. |
| Journal | no schema change | Slash stubs journal as the existing `PageWriteEvent`. No new event type. Orphan stub removal is *not* journaled — it is user-resolved per Outputs §2. |

## Acceptance criteria

These translate directly into tests. A reviewer should be able to
read this list and write the test file from it without re-reading
the rest of the spec.

- [ ] **Schema** — `OperationContract` accepts `outcomes:
  list[str]` and rejects unknown extra keys. Unit test.
- [ ] **Well-formed verb** — every verb matches
  `^[a-z][a-z0-9]*(-[a-z0-9]+)*$` (no consecutive hyphens, no
  trailing hyphen, no leading digit, no uppercase), total length
  3–24 characters, ASCII only, starts with an allowlisted verb
  stem, does not collide with a `RESERVED_OUTCOME_VERBS` entry,
  does not start with `wiki-`. Parametrized unit test covering
  the negative cases at minimum: `a--b`, `ab-`, `1ab`, `Ab`, `ab`
  (too short), `<25-char string>`, `wiki-foo`, `meals` (bare
  noun), `weekly-summary` (adjective-noun, no verb stem).
- [ ] **Catalog uniqueness** — declaring the same verb on two
  operation primitives fails `check_outcome_verb_uniqueness` with
  a `WikiError` naming both. Unit test.
- [ ] **SKILL-fragment presence** — declaring `outcomes:
  [<verb>]` without the verb appearing in the matching SKILL.md
  description fails install with a `WikiError` naming both files.
  Integration test against a fixture vault.
- [ ] **CLI alias** — in a vault with `weekly-digest` installed
  and `outcomes: [digest]`, `wiki digest --window 2026-W18`
  runs the same code path as `wiki run weekly-digest --window
  2026-W18`. Integration test.
- [ ] **CLI alias outside vault** — `wiki digest` from `~/`
  errors with the vault-scoped message. Integration test.
- [ ] **Slash stub written** — install writes
  `.claude/commands/<verb>.md` for every declared verb, via
  `safe_write`, with the template body fixed by this spec.
  Integration test.
- [ ] **Slash stub drift** — a user edit to
  `.claude/commands/<verb>.md` is preserved as `.proposed` on the
  next `wiki upgrade`. Integration test.
- [ ] **`wiki outcomes`** — output is sorted by verb, prints
  every installed verb, prints empty output for a vault with no
  declared outcomes. Integration test.
- [ ] **`wiki --help` epilog** — output names `wiki outcomes` so a
  first-time reader can discover the verb table. Integration test
  (string match on `--help`).
- [ ] **`wiki init` post-install message** — mentions `wiki
  outcomes` when the resolved recipe ships at least one operation
  with a declared verb. Integration test.
- [ ] **`wiki <verb> --help`** — prints the same body as `wiki run
  <operation> --help` with a one-line alias preamble. Integration
  test.
- [ ] **Argument forwarding** — `wiki digest --window 2026-W18
  --theme "easy"` reaches `_cmd_run` with the same `argparse`
  namespace as `wiki run weekly-digest --window 2026-W18 --theme
  "easy"`. Integration test.
- [ ] **Operation names are not implicit verbs** — `wiki
  weekly-digest` (without `weekly-digest` declared as an outcome)
  fails with the standard argparse "invalid choice" error.
  Integration test.
- [ ] **Verb does not shadow any operation name** —
  catalog-load rejects an outcome verb whose value equals any
  operation's `name:` field, including the declaring operation's
  own name (e.g. operation `weekly-digest` declaring `outcomes:
  [weekly-digest]`). Unit test against a fixture catalog covering
  both the cross-operation and own-name cases.
- [ ] **`wiki doctor` flags orphan stubs** — after removing an
  operation (or shrinking its outcomes list), `wiki doctor`
  reports the orphan stub at `.claude/commands/<verb>.md` and
  names the dropped verb. Integration test.
- [ ] **`wiki doctor` clean on a verb-enabled vault** — with
  declared verbs and no user edits, `wiki doctor` reports zero
  drift. Integration test.
- [ ] **Catalog-time uniqueness gate** — `tests/unit/test_outcome_verbs.py`
  walks every shipped `templates/operations/*/contract.yaml` and
  fails if any catalog-level rule is violated (shape, locale,
  reserved-word, verb-form, uniqueness, no-`wiki`-prefix). Gate
  for the standard `pytest -m 'not slow'` CI matrix.
- [ ] **Wheel-acceptance SKILL-fragment gate** — the slow
  wheel-acceptance suite (`pytest -m slow`) installs the wheel and
  asserts that for every shipped operation in the wheel's
  `_assets/templates/operations/`, every declared outcome verb
  appears in the matching `SKILL.md` description. Pins the
  "no malformed verb reaches a user" invariant to a CI gate before
  any release.
- [ ] **Eval trigger** — for each operation declaring verbs, the
  parametrized eval prompts Claude with an outcome-shaped
  natural-language ask and asserts the matching SKILL loads. The
  prompt must not name the SKILL or the `wiki run` command. The
  canonical prompt template per shipped verb (the test fixture):
  - `digest` → `"Give me last week's digest."`
  - `plan-meals` → `"Help me plan our meals for next week."`
  - `refresh-stakeholders` → `"Refresh the stakeholder map for the pluto project."`

  New verbs added to the catalog must add a matching prompt
  fixture in the same PR.
- [ ] **Backwards compatibility** — vaults built before this spec
  are upgraded by `wiki upgrade` to the new state (stubs written,
  CLI router enabled) with no journal-replay errors. Integration
  test using a `v2.0.0` baseline fixture.

## Three concrete worked examples, one per shipped recipe

### Family — `meal-planning` operation → verb `plan-meals`

```yaml
# templates/operations/meal-planning/contract.yaml
name: meal-planning
period: weekly
skill: meal-planning
outcomes:
  - plan-meals
description: >-
  Produce a weekly family meal plan with shopping list. Reach for
  this skill when the user says "plan our meals", "what are we
  eating next week", or invokes /plan-meals.
inputs: { ... }
outputs: { ... }
```

User surfaces:

- Shell: `wiki plan-meals --window 2026-W22 --theme "easy week"`
- Claude Code: `/plan-meals`
- Natural language inside Claude: "plan our meals for next week"

Under the hood, all three resolve to `wiki run meal-planning` with
the same contract arguments. The slash stub at
`.claude/commands/plan-meals.md` is the fixed-body stub produced by
the template in Outputs §2.

### Work-OS — `stakeholder-map-refresh` operation → verb `refresh-stakeholders`

```yaml
# templates/operations/stakeholder-map-refresh/contract.yaml
name: stakeholder-map-refresh
period: monthly
skill: stakeholder-map-refresh
outcomes:
  - refresh-stakeholders
description: >-
  Refresh the per-project stakeholder map by reading
  stakeholder-update pages. Trigger when the user asks to
  "refresh the stakeholder map" or invokes /refresh-stakeholders.
inputs: { ... }
outputs: { ... }
```

User surfaces:

- Shell: `wiki refresh-stakeholders --project pluto`
- Claude Code: `/refresh-stakeholders`
- Natural language: "let's refresh the stakeholder map for pluto"

### Personal — `weekly-digest` operation → verb `digest`

```yaml
# templates/operations/weekly-digest/contract.yaml
name: weekly-digest
period: weekly
skill: weekly-digest
outcomes:
  - digest
description: >-
  Summarize the week's activity across the vault into one durable
  digest page. Reach for this when the user asks for "the digest",
  "last week's summary", "what happened this week", or invokes
  /digest.
inputs: { ... }
outputs: { ... }
```

User surfaces:

- Shell: `wiki digest`
- Claude Code: `/digest`
- Natural language: "give me last week's digest"

`digest` is the same verb across all three recipes that install
`weekly-digest`. There is no collision because the verb is owned
by the operation, not by the recipe — every recipe that installs
`weekly-digest` gets `digest` as the canonical verb. Two different
operations cannot both claim `digest`; the catalog uniqueness
check enforces that.

## Non-goals

Explicit non-goals — listed so a future PR doesn't drift into
them:

1. **Translating or renaming existing operations.** `weekly-digest`
   stays `weekly-digest` as an operation name. Outcomes are
   additive sugar, not a rename.
2. **Removing the primitive / operation / recipe vocabulary.** The
   contract layer stays. `wiki run <operation>` keeps working.
   Outcomes do not collapse the abstraction.
3. **Internationalization of verb names.** v1 is English-only; a
   future ADR can revisit locale-aware verbs once a non-English
   recipe lands.
4. **Bootstrap wizard surfacing verbs on first-run.** A future
   spec (separate PR) can add a "here are the verbs your recipe
   ships" first-run greeting. This spec ships only `wiki
   outcomes` (and a one-line mention in `wiki --help` /
   `wiki init`'s post-install message) plus the SKILL
   descriptions.
5. **A recipe-selector UI.** Out of scope; the kit stays a CLI +
   library. (Charter Principle 5.)
6. **A TUI for browsing verbs.** Same reason as the selector UI.
7. **Outcome verbs on content-type primitives.** "Log this
   meeting", "save this recipe" are also outcome-shaped. Two
   reasons to defer them rather than fold them in here:
   (a) Content-type invocation takes a *source* argument
   (`wiki ingest <path>`); an outcome verb on a content-type
   means `wiki log-meeting <path>` *and* `/log-meeting` *with no
   path*, which forks the verb's argument shape across surfaces
   in a way operations don't. The right answer for the slash
   surface — does Claude prompt for a path, or look at the
   currently-open file? — isn't obvious and shouldn't be
   smuggled into this spec.
   (b) `wiki ingest`'s routing already infers the content-type
   from the source's extension / filename / URL; the user does
   not need to name the verb at all in the common path. The
   primary UX win this spec targets — replacing
   `wiki run weekly-digest` with `wiki digest` — does not
   transfer to a surface where naming is already inferred.
   A future spec can revisit; nothing here precludes it.
8. **Outcome verbs on infrastructure primitives** (research,
   search). `wiki research` and `wiki search` are already
   verb-shaped surfaces; they don't benefit from a second alias
   layer.
9. **Per-vault user-defined verb aliases.** A user can already
   write their own slash command in `.claude/commands/`; the kit
   doesn't need to manage user-defined aliases. If a user wants
   to rename a verb in their vault, they edit the stub and accept
   the drift flag, or write their own alongside it.
10. **Verb namespacing per recipe** (option (b) from the brief).
    The catalog is the namespace; recipes compose the catalog.
11. **Removing or modifying any existing `wiki` subcommand.** The
    RFC-0001 §"CLI surface (target)" stays exactly as it is.

## Constraints

What implementation strategies are off the table for this spec:

1. **No new runtime dependency.** Verb routing is argparse +
   stdlib; slash-stub generation is `safe_write` + a constant
   template string. (Charter Principle 3.)
2. **No new module under `llm_wiki_kit/`.** The work fits into
   `cli.py`, `models.py`, `primitives.py`, and `install.py`. A
   new module would imply a new boundary worth its own RFC.
3. **No new top-level directory at the repo root.** Slash stubs
   live under each user's `<vault>/.claude/commands/`. The kit's
   catalog gains no new directory.
4. **No bypass of `safe_write` / `safe_write_region`.** Every
   slash stub is written through the existing helper. Drift
   detection is non-negotiable. (ADR-0004.)
5. **No new public CLI verb beyond what Behavior describes.** The
   only static addition is `wiki outcomes` (one new top-level
   subcommand). Plus the dynamic `wiki <verb>` dispatcher derived
   from the installed catalog. No `wiki alias`, `wiki commands`,
   `wiki shortcuts`, or `wiki list <topic>` family.
6. **No new journal-event type.** Outcome metadata is not state
   to be replayed — the operation's existing `OperationRunEvent`
   already records each run. Slash stubs piggyback on
   `PageWriteEvent`.
7. **No interpolation added to SKILL.md.** SKILL.md remains a
   byte-for-byte copy (ADR-0001). The "verb appears in
   description" rule is satisfied by authored text, not by
   template substitution.
8. **No recipe-level `outcomes:` field.** Recipes compose
   primitives; primitives own verbs. A recipe author who wants a
   new verb adds it to the operation primitive's contract, not to
   the recipe YAML.
9. **No mutation of operation behavior.** This spec adds
   discovery; it does not change inputs, outputs, or any contract
   semantics of any operation.
