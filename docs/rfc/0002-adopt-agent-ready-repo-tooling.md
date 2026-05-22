# RFC-0002: adopt agent-ready-repo tooling at full scope

- **Status:** Accepted
- **Author:** maintainer
- **Created:** 2026-05-16
- **Discussion:** PR opened against `main` from
  `eugenelim/rfc-0002-adopt-agent-ready-repo`
- **Resolves to:** A sequence of follow-on PRs (B/C/D/E/F/G) that vendor
  the tooling and reconcile the kit's docs; no ADRs in this RFC itself

## Summary

Adopt the [`agent-ready-repo`](https://github.com/eugenelim/agent-ready-repo)
dev tooling at full scope into `llm-wiki-kit`: the `work-loop` SKILL, five
workflow skills (`new-spec`, `new-adr`, `new-rfc`, `bug-fix`,
`update-conventions`), four reviewer subagents (`adversarial-reviewer`,
`implementer`, `quality-engineer`, `security-reviewer`), `check-done.py`,
the `state.json` template, the docs-and-artifact linters, the
`pre-pr` / `session-start` hooks, `install-skill.py`, Ralph, the
practitioner knowledge base, and a substantial expansion of
[`docs/CONVENTIONS.md`](../CONVENTIONS.md). Skip only `new-package` —
structurally N/A for a single Python package.

The vendored linters impose load-bearing preconditions on the kit
(symlink shape, line caps, required directory tree, drift-watch tokens,
required H2 anchors in CONVENTIONS.md). This RFC documents those
preconditions in one place so the follow-on PRs are mechanical.

## Motivation

The kit's current [`CLAUDE.md`](../../CLAUDE.md) describes the
plan → execute → verify → review loop in prose. That prose has been
enough for tasks 1–16 of [RFC-0001](0001-v2-architecture.md), but three
gaps are visible now that the migration is two-thirds through:

1. **Per-task gates miss integrated-journey bugs.** Each task's
   acceptance criteria are scoped to one PR. v2's 22 tasks compose into
   one shipped product, and nothing currently runs a cross-cutting
   review pass against the integrated state. Tasks 17–22 (research
   dispatch, eval harness, example vaults, release) are the
   highest-coupling stretch of the plan, so the gap is most expensive
   exactly when it lands.
2. **No mechanical termination on review iterations.** "Self-review
   against the task spec" relies on the agent recognizing when it has
   stopped making progress. `tools/check-done.py` enforces a hard cap,
   token-budget threshold, plan-approval gate, and fingerprint-based
   stasis detection — properties prose can't provide.
3. **No place for practitioner residue.** Lessons from building (e.g.
   "when you touch `safe_write`, remember managed regions short-circuit
   drift detection") currently end up in commit messages or nowhere.
   ADRs are too heavy and architecture docs describe current structure,
   not gotchas. `docs/knowledge/patterns.jsonl` is the missing layer.

Adopting `agent-ready-repo` wholesale closes all three gaps without
rebuilding the same machinery kit-locally. The tooling is stack-agnostic
(it's just shell, Python, and markdown) and the four reviewer subagents
read AGENTS.md and CONVENTIONS.md at session start rather than hard-coding
any particular stack, which is the same posture the kit already takes.

## Proposal

### Adopted artifacts (every file, with destination path)

| Source | Destination |
|---|---|
| `work-loop/SKILL.md` | `.claude/skills/work-loop/SKILL.md` |
| `new-spec/SKILL.md` | `.claude/skills/new-spec/SKILL.md` |
| `new-adr/SKILL.md` | `.claude/skills/new-adr/SKILL.md` |
| `new-rfc/SKILL.md` | `.claude/skills/new-rfc/SKILL.md` |
| `bug-fix/SKILL.md` | `.claude/skills/bug-fix/SKILL.md` |
| `update-conventions/SKILL.md` | `.claude/skills/update-conventions/SKILL.md` |
| Skill index | `.claude/skills/README.md` |
| `adversarial-reviewer.md` | `.claude/agents/adversarial-reviewer.md` |
| `implementer.md` | `.claude/agents/implementer.md` |
| `quality-engineer.md` | `.claude/agents/quality-engineer.md` |
| `security-reviewer.md` | `.claude/agents/security-reviewer.md` |
| `check-done.py` | `tools/check-done.py` |
| `lint-agents-md.sh` | `tools/lint-agents-md.sh` |
| `lint-agent-artifacts.sh` | `tools/lint-agent-artifacts.sh` |
| `lint-skill-deps.sh` | `tools/lint-skill-deps.sh` |
| `lint-knowledge.sh` | `tools/lint-knowledge.sh` |
| `hooks/pre-pr.sh` | `tools/hooks/pre-pr.sh` |
| `hooks/session-start.sh` | `tools/hooks/session-start.sh` |
| Hooks README | `tools/hooks/README.md` |
| `install-skill.py` | `tools/install-skill.py` |
| `ralph.sh` + `RALPH.md` | `tools/ralph.sh`, `tools/RALPH.md` |
| `state.json` template | `docs/_templates/state.json` |
| `patterns.jsonl` seed + README | `docs/knowledge/patterns.jsonl`, `docs/knowledge/README.md` |
| CONVENTIONS sections (see § Repository preconditions) | additions to [`docs/CONVENTIONS.md`](../CONVENTIONS.md) |

Adopted ungrouped, this is one new top-level directory (`.claude/`), one
new mid-level directory (`tools/hooks/`), and two new `docs/` subtrees
(`docs/_templates/state.json` lands alongside existing templates;
`docs/knowledge/` is new).

The vault-side `core/files/skills/` and `templates/*/files/skills/`
trees are unaffected. The two skill scopes (kit-side vs. vault-side)
remain physically distinct, per
[`CLAUDE.md` § Two scopes, one repo](../../CLAUDE.md#two-scopes-one-repo).

## Repository preconditions imposed by the tooling

Load-bearing — what the kit must look like for the linters and skill
`dependencies:` resolution to work. PR-1 (Wave 2, foundation) lands
every precondition in this section in one commit so subsequent PRs can
be merged in parallel without the linters fighting each other.

### Hard requirements (`lint-agents-md.sh` fails without these)

- **`CLAUDE.md` is a symlink to `AGENTS.md`.** Kit: ✅ already true
  (verified with `ls -la CLAUDE.md`).
- **Root `AGENTS.md` ≤ 250 lines; subdir `AGENTS.md` ≤ 150 lines.**
  Kit root is currently ~155 lines; the AGENTS.md scrub in PR-1 keeps
  it under cap.
- **[`docs/CHARTER.md`](../CHARTER.md) exists.** Kit: ✅.
- **No `docs/constitution/` directory.** Kit: ✅ (never existed).
- **`docs/guides/{tutorials,how-to,reference,explanation}/` all exist
  as directories.** Content can be empty for now. The existing flat
  user docs under `docs/guides/*.md` (`customizing.md`,
  `file-formats.md`, `inventories.md`, `setup.md`, `sync-options.md`,
  `web-clipper.md`) stay in place; gradual migration into the right
  Diátaxis bucket is a future docs PR, not adoption scope. Kit: ❌ —
  subdirs don't exist yet. **CREATE EMPTY (with README placeholders)
  IN PR-1.**
- **All relative markdown links in `AGENTS.md` and
  `docs/CONVENTIONS.md` resolve.** Anchors are allowed. PR-1's
  reconcile pass fixes any links broken by the doc-tree changes.
- **`.gitignore` covers `docs/specs/*/state.json`,
  `docs/specs/*/notes/`, `.worktrees/`.** Single-segment globs — the
  linter probes specific example paths
  (`docs/specs/example/state.json`,
  `docs/specs/example/notes/implementer-T1-0.md`,
  `.worktrees/T1/README.md`). PR-1 adds these rules.

### Drift-watch (forbidden strings in AGENTS.md, CONVENTIONS.md, CHARTER.md, APPROACH.md)

The linter enforces single-source-of-truth for a small set of phrases.
Each phrase has exactly one canonical home; appearing anywhere else
fails the lint.

- `"max_iterations": <n>` — canonical: `docs/_templates/state.json`.
  Forbidden elsewhere.
- `cap of five iterations` / `cap of 5 iterations` — canonical: the
  `work-loop` SKILL. Forbidden elsewhere.
- `**Goal-based check**` — canonical: the `work-loop` SKILL.
  Forbidden elsewhere.
- `ultrathink` — vendor (Claude-specific) UX token. Forbidden in
  AGENTS.md, CONVENTIONS.md, CHARTER.md, APPROACH.md. **Kit: ❌ —
  current AGENTS.md line 65 violates. SCRUB IN PR-1** (see
  § Two pre-existing kit drifts).
- `Plan Mode (Shift+Tab` — vendor UX token. Forbidden, same rule.
  Kit: ✅ — current AGENTS.md says "Plan Mode" without the
  `(Shift+Tab` parenthetical, so the linter passes; the AGENTS.md
  scrub still rephrases for consistency.

**Rationale for drift-watch.** AGENTS.md and CONVENTIONS.md describe
the *project* in a model-neutral way. Agent-specific affordances
("ultrathink", "Plan Mode (Shift+Tab") belong in the work-loop SKILL
— the agent-swappable layer that gets replaced when the operator
switches agents (Cursor, Codex, Gemini CLI, Copilot). The discipline
is layering, not censorship: the same instruction can exist, it just
exists in the right file.

### CONVENTIONS.md must define H2 headings with these exact slugs

Skill and agent `dependencies:` lists pin these GitHub-generated
anchors. Slugs are auto-generated by GitHub from H2 text: lowercase,
non-word characters stripped, spaces → hyphens.

- `contract-tests-vs-construction-tests`
- `work-loop-state`
- `supervisor-mode`
- `knowledge-base`
- `model-selection`

PR-1 adds these H2 sections to
[`docs/CONVENTIONS.md`](../CONVENTIONS.md). The next subsection
explains why anchors alone aren't enough.

### Runtime quality (not enforced by lint, but degrades reviewer output)

The four subagent bodies instruct each agent to read AGENTS.md and
CONVENTIONS.md at session start. They look for:

- the three verification modes (TDD / goal-based check / visual or
  manual QA),
- the contract-vs-construction tests split,
- project anti-patterns, and per-package or per-module test
  conventions.

If the H2 anchors exist but the sections are stubs, the linters pass
but the reviewers degrade silently — they cite empty advice or
hallucinate. **PR-1's CONVENTIONS expansion must substantively define
each section, not just create the heading anchors.** Source material
is the corresponding sections of the upstream
`agent-ready-repo/docs/CONVENTIONS.md`, adapted to kit terminology
(`ruff`/`mypy`/`pytest`; the kit's `v2: task <N> - <summary>` commit
format; existing journal / managed-regions / drift-detection
vocabulary from ADRs 0001–0005).

## End-user impact analysis

[`pyproject.toml`](../../pyproject.toml) declares
`[tool.hatch.build.targets.wheel] packages = ["llm_wiki_kit"]`. Only the
Python package is bundled into the wheel. There is no `MANIFEST.in` and
no `package_data` directive. Therefore:

- `/.claude/` does not ship to end users.
- `/tools/` does not ship to end users.
- `/docs/` does not ship to end users.

The primitive loader walks only `templates/*/files/` and
`core/files/`. The repo-root `.claude/` directory is invisible to
`wiki init` and `wiki add`. There are two physical-isolation layers
between kit-side dev tooling and any end user's vault: the wheel build
filter, and the loader's directory scoping.

**Side-note (out of scope for this RFC):** `pyproject.toml` currently
lacks `package_data` for `core/files/` and `templates/*/files/`
entirely. Flag as a separate packaging-bug follow-up.

## Two pre-existing kit drifts this RFC forces resolution of

(a) **Doc-tree drift.** `docs/guides/` is flat (six existing user-doc
    files) but [`CLAUDE.md` § Source of truth](../../CLAUDE.md#source-of-truth)
    references `docs/tutorials/`, `docs/how-to/`, `docs/reference/`,
    `docs/concepts/` as Diátaxis buckets. Neither shape matches what
    the linter requires
    (`docs/guides/{tutorials,how-to,reference,explanation}/`).
    Resolution: PR-1 creates the four Diátaxis subdirs under
    `docs/guides/` (empty, with README placeholders explaining each
    bucket and listing candidate migrations) and reconciles AGENTS.md
    to point at `docs/guides/{tutorials,how-to,reference,explanation}/`.
    Existing flat files stay where they are and migrate gradually in a
    future docs PR.

(b) **Vendor-token drift.** [`CLAUDE.md` line 65](../../CLAUDE.md#workflow)
    uses "ultrathink" — a Claude-specific affordance — inside the
    workflow's plan-mode bullet. Resolution: replace
    `use Plan Mode and "think hard" / "ultrathink"` with
    `use Plan Mode and the agent's deepest-thinking setting`. Same
    instruction, model-agnostic phrasing. The Claude-specific token
    moves into the work-loop SKILL, which is the agent-swappable
    layer.

## What we skip (and why)

- **`new-package`** — structural N/A. The kit is a single Python
  package (`llm_wiki_kit/`), not a monorepo with a `packages/`
  directory. The skill assumes the latter.
- **Conventional Commits.** Keep the kit's existing
  `v2: task <N> - <one-line summary>` format through the v2 migration
  (per [CONVENTIONS § Commit messages](../CONVENTIONS.md#commit-messages)).
  Revisit after `v2.0.0` ships, at which point CONVENTIONS already
  prescribes Conventional Commits for the post-v2 phase.

## QE schedule

The `quality-engineer` and `adversarial-reviewer` subagents need
explicit moments to engage with integrated state, not just per-task
review:

- **Retrospective pass against current `main`** (tasks 1–16) right
  after PR-5 of RFC-0002 lands. This is the first time the integrated
  review tooling exists, applied to the work that pre-dates it.
- **Phase boundaries thereafter:**
  - After task 19 (end of Phase D — research dispatch + providers).
  - After task 21 (end of Phase E — example vaults + tutorials,
    before the `v2.0.0` tag in task 22).

## Adaptations

The vendor is stack-agnostic, but three artifacts get small edits on
the way in:

- **`work-loop` SKILL** — gates section swaps to `ruff` / `mypy` /
  `pytest` (per [CLAUDE.md § Commands you'll need](../../CLAUDE.md#commands-youll-need));
  commit-message section uses the v2 format;
  doc-path references use
  `docs/guides/{tutorials,how-to,reference,explanation}/` after PR-1's
  reconcile.
- **Four agent files** — vendored as-is. Their lenses
  (adversarial, security, quality, implementer) are stack-agnostic;
  they read AGENTS.md and CONVENTIONS.md at session start, so
  kit-specific context arrives through those files rather than via
  edits to the agent bodies.
- **`check-done.py`** — vendored as-is. Stdlib-only; no kit-specific
  behavior to change.

## Follow-ons (the PRs this RFC unblocks)

This RFC produces no code on its own. On acceptance, it unblocks the
following sequence of PRs. Parallelism is intentional — Waves 2 and 3
each contain PRs that can be merged in any order within the wave.

**WAVE 2 (parallel):**

- **PR-1 (B): foundation.** `work-loop` SKILL + four agent files +
  `check-done.py` + `state.json` template + AGENTS.md scrub
  (ultrathink → deepest-thinking; Plan Mode rephrase) + doc-tree
  reconcile (CLAUDE.md source-of-truth row points at
  `docs/guides/...`) + `docs/guides/{tutorials,how-to,reference,explanation}/`
  subdir creation (with README placeholders) + `.gitignore` rules +
  `.claude/skills/README.md` seed + CONVENTIONS.md expansion adding
  the five mandatory H2 anchors with substantive sections.
- **PR-2 (C): five workflow skills.** `new-spec`, `new-adr`,
  `new-rfc`, `bug-fix`, `update-conventions`. Each in
  `.claude/skills/<name>/SKILL.md`.
- **PR-3 (E): knowledge base.** `docs/knowledge/patterns.jsonl`
  (initial empty + seed entries from in-flight v2 work) and
  `docs/knowledge/README.md`.
- **PR-4 (F): Ralph.** `tools/ralph.sh` and `tools/RALPH.md`.

**WAVE 3 (parallel):**

- **PR-5 (D): linters + hooks + install-skill + CI integration.**
  `tools/lint-{agents-md,agent-artifacts,skill-deps,knowledge}.sh`,
  `tools/hooks/{pre-pr,session-start}.sh`,
  `tools/hooks/README.md`, `tools/install-skill.py`, and the CI
  workflow that runs `tools/hooks/pre-pr.sh` on every PR.
- **Wave 4 (G): retrospective adversarial + QE review of tasks
  1–16.** Not a vendoring PR — it's the first application of the
  integrated tooling to existing code. May produce one or more
  follow-up PRs depending on findings.

**Parallel-safety rationale.**

- PRs B/C/E/F touch disjoint file sets. B owns `.claude/skills/README.md`
  with stubs that index C's skills; C only adds the SKILL.md files
  themselves.
- C's skills don't pin CONVENTIONS anchors that B doesn't already
  create (audited against the upstream skill bodies).
- The drift-watch linter (which arrives in Wave 3, not Wave 2) only
  watches files B touches. C/E/F can't trip drift-watch by
  construction.
- Broken-link tolerance applies between merges *within* Wave 2: the
  link-resolution linter arrives in Wave 3, so any transient broken
  link during Wave 2 is invisible to mechanical gates. The PR
  reviewers still catch obvious cases.

## Alternatives

### Alt 1: Selective adoption

Pick a subset (e.g. only `work-loop` + `check-done.py`). Rejected.
The integrated value comes from the combination: caps require
`state.json`; reviewers require CONVENTIONS anchors; knowledge base
requires `session-start.sh`. Removing pieces leaves uncoordinated
fragments. The full scope is also small in absolute terms (one new
top-level directory, ~15 new files plus a CONVENTIONS expansion).

### Alt 2: Wait until after `v2.0.0`

Adopt after the release tag, when the migration churn settles.
Rejected. Tasks 17–22 are the highest-coupling stretch of the
migration plan (research dispatch, eval harness, example vaults,
release) and benefit most from cross-task review. Delaying adoption
costs exactly the review value it would have provided.

### Alt 3: Build a kit-specific equivalent

Hand-roll the work-loop, caps script, and reviewers tuned for this
codebase. Rejected. The vendor is mature, stack-agnostic, and
maintained upstream. The kit's value lives in the wiki primitives,
not in inventing a parallel dev-loop. Forking the vendor adds
maintenance cost with no offsetting benefit; adopting wholesale and
contributing back if we find gaps is the cheaper path.

## Drawbacks

- **Adds one new top-level directory (`.claude/`).** [`CLAUDE.md`
  § Check before acting](../../CLAUDE.md#check-before-acting) asks for
  an RFC before adding top-level directories. This RFC is that ask.
- **CONVENTIONS.md expansion is substantial.** The current
  ~139 lines roughly doubles. The added sections are necessary for the
  reviewer subagents to function (see § Runtime quality), not optional
  decoration.
- **`docs/guides/` subdir creation adds four directories and four
  README placeholders.** No existing files move; migration into the
  right bucket happens gradually in a future docs PR.
- **Two skill scopes for new contributors to understand.** Kit-side
  at repo root (`.claude/skills/`) vs. vault-side under
  `core/files/skills/` and `templates/*/files/skills/`. CLAUDE.md
  already calls out the distinction; the new directory makes it more
  visible, which arguably helps as much as it confuses.
- **Linter strictness creates a new class of CI failure.** Drift-watch
  tokens, line caps, and required anchors can break the build on a
  doc-only PR. The trade is intentional: those failures are exactly
  the kind of slow drift this RFC exists to prevent.

## Unresolved questions

None.

## Outcome

Filled in on acceptance. Expected: the PR sequence in § Follow-ons
lands as described, the four reviewer subagents become available on
the kit-side `.claude/`, and CONVENTIONS.md grows the five required
H2 anchors with substantive content. The kit's existing v2 migration
(RFC-0001 tasks 17–22) continues unchanged in commit format and task
shape; only the surrounding review discipline tightens.

### Upstream baseline

The adoption tracks upstream
[`eugenelim/agent-ready-repo`](https://github.com/eugenelim/agent-ready-repo).
Each refresh PR records the upstream HEAD it syncs against here so
the next sync isn't archaeology.

#### 2026-05-17 — baseline `e6bb41f`

First post-adoption refresh after the initial RFC-0002 imports
(PR-1 through PR-5).

- **Imported / refreshed:**
  - `.claude/skills/work-loop/SKILL.md` — declined-pattern register
    in PLAN; structural-change pre-EXECUTE trigger; two new
    anti-patterns; gates adapted to ruff/mypy/pytest per kit; commit
    format restored to `v2: task <N> - <summary>`.
  - `.claude/agents/adversarial-reviewer.md` — spec/plan-review
    trigger expanded to cover structural changes without spec edit.
  - `docs/_templates/spec.md` — surgical add of `## Constraints`
    subsection (kit template kept its existing shape; full upstream
    template not adopted to avoid orphaning existing kit specs).
  - `AGENTS.md` — added "Keeping changes minimal" section; renamed
    "Things you should not do without asking" → "Check before
    acting" with positive-imperative phrasing; all kit-specific
    bullets preserved.
- **Upstream PRs folded in:** #5 (QE fuzz coverage), #6 (state
  schema + caps), #7 (supervisor + implementer + parallel dispatch),
  #8 (knowledge base lifecycle), #11 (broadened work-loop trigger
  surface), #13 (spec template Constraints), #14 (pre-EXECUTE
  structural-change trigger).
- **Loose commits folded in:** `04e1f8d` (changes-minimal block),
  `3b678a3` (B1 dodge-deferment hardening), `acea0d5` (declined-pattern
  register).
- **No-op (local already matches or exceeds upstream):**
  `quality-engineer.md`, `implementer.md`, `security-reviewer.md`,
  `docs/_templates/state.json`, `docs/knowledge/patterns.jsonl`
  (local has 7 entries; upstream's is empty seed),
  `docs/knowledge/README.md` (local is kit-adapted).
- **Skipped per kit scope:** `new-package` skill (kit is single
  Python package), `.claude/commands/conventions-check.md`
  (deferred), brownfield-adopter guidance (#12, template-only),
  `USING_THIS_TEMPLATE.md`, `LICENSE-*`, template README refresh,
  `packages/_example/`.
