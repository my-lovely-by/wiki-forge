# Spec: example vaults and first tutorials

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** Task 21 implementer
- **Touches:** `examples/family-mini/`, `examples/work-os-mini/`,
  `examples/conflict-pending/`, `examples/_seed/`,
  `examples/regenerate.py`, `examples/README.md`,
  `docs/guides/tutorials/{tutorial-1-first-vault,tutorial-2-work-os-walkthrough}.md`,
  `docs/guides/tutorials/README.md`,
  `docs/guides/how-to/resolve-a-conflict.md`,
  `docs/guides/how-to/README.md`,
  `tests/integration/test_tutorials.py`,
  `tests/integration/test_examples_regenerable.py`
- **Related:** RFC-0001 §"Task 21 — Example vaults and tutorials",
  `docs/specs/task-21-examples-tutorials/plan.md`,
  `core/files/skills/wiki-conflict/SKILL.md`
- **Constrained by:** ADR-0002 (journal as state truth — examples and
  tutorials never bypass `safe_write`/the journal), ADR-0004 (drift
  detection — the conflict how-to operates on a real proposal
  sidecar), AGENTS.md "Runtime dependencies" (no new runtime dep),
  AGENTS.md "Propose new top-level directories via RFC" (`examples/`
  is a new top-level directory authorized by RFC-0001 Task 21's
  literal text "`family-mini/`, `work-os-mini/`" — no separate RFC
  needed; `conflict-pending/` is a third example vault under the
  same `examples/` umbrella and does not introduce a separate
  top-level), CHARTER §Mission ("non-engineer — a working
  professional or a family" — tutorial voice is for non-engineers,
  not API authors)

## What this is

The user-facing on-ramp for v2.0.0: three pre-shaped vaults a
non-engineer can browse to see what the kit produces, and the first
two [Diátaxis](https://diataxis.fr/) **tutorials** plus one **how-to**
that walk a reader from `pip install llm-wiki-kit` through a working
vault they understand.

In scope:

- `examples/family-mini/` and `examples/work-os-mini/` — committed,
  regenerable vaults with at least one (target 3–5) hand-authored
  seed page per recipe-created `wiki/<area>/` directory so a
  first-time reader sees a *populated* vault, not empty scaffolding.
- `examples/conflict-pending/` — a third committed vault, smallest
  recipe (`personal`), shaped with one drifted page and the matching
  `PageProposalEvent` already in its journal. The how-to operates on
  a copy of this vault, so the reader does not need to construct
  drift themselves and the how-to does not require Python invocation.
- `docs/guides/tutorials/tutorial-1-first-vault.md` — recipe-agnostic
  "Create your first vault" (init → ingest one source → run one
  operation → read the journal).
- `docs/guides/tutorials/tutorial-2-work-os-walkthrough.md` —
  work-os-recipe-specific walkthrough that ends with a populated
  work-os-mini-shaped vault.
- `docs/guides/how-to/resolve-a-conflict.md` — problem-oriented walk
  through the committed drift in `examples/conflict-pending/`,
  exercising the `wiki-conflict` vault-side skill.
- A regeneration mechanism for all three example vaults
  (`examples/regenerate.py`) and a tutorial-drift CI gate
  (`tests/integration/test_tutorials.py`) that fail when the literal
  CLI surface in either tutorial diverges from what the kit actually
  does.

Out of scope (see §Non-goals for the full list): tutorial 3 (family
walkthrough), migration of legacy `docs/guides/*.md` into Diátaxis
buckets, any new `wiki` CLI verb, and any work that would unblock
the stub commands (`wiki journal tail`, `wiki search`, `wiki upgrade`,
`wiki journal grep`, `wiki journal explain`).

## Inputs

### From the user (running the tutorial)

A working Python 3.11+ install with `pip install -e '.[dev]'` (during
development from a clone) or `pip install llm-wiki-kit` (post-release).
**No API keys required** for either tutorial — the tutorial path uses
only the locally-runnable CLI verbs (`init`, `add`, `ingest`, `run`,
`doctor`, `resolve`) and reads the journal file directly with `cat`.
`wiki research` is mentioned only in the "what's next" section of
tutorial 2 with an explicit "optional, requires a Perplexity / Gemini
key" pointer, never as a step the reader must execute.

A user's Claude Code session (or any agent that reads vault
`SKILL.md`s) is required to actually *execute* an ingest or operation
— the kit ships only the dispatch boundary. The tutorials make this
distinction explicit in §Behavior "Two-surface convention" below. No
tutorial step asserts on Claude-produced output.

### From the kit (build- and test-time)

- The kit's templates (`templates/`, `core/`, `recipes/`) — used by
  `examples/regenerate.py` to produce the example vaults.
- `examples/_seed/<recipe>/<path>/<page>.md` — hand-authored seed
  pages copied into the regenerated vault by `regenerate.py` (for
  `family` and `work-os`; `conflict-pending/` has no seeds — its
  content is produced by the regenerator's internal drift-replay
  function).
- `examples/conflict-pending/` is built by `regenerate.py`'s
  internal drift-replay function: `wiki init … --recipe personal`,
  then (1) one `safe_write` of an initial page producing the
  baseline `PageWriteEvent`, (2) one *direct* `Path.write_bytes`
  to simulate the user editing the page on disk (this single
  line is the documented `safe_write` carve-out — see
  §Constraints "No bypass of `safe_write` — one narrow
  exception"), (3) one `safe_write` of the kit-update which
  detects drift (`on_disk_hash != baseline_hash`) and produces
  the `.proposed` sidecar plus the matching `PageProposalEvent`.
  Hand-crafted sidecars without a matching event are rejected
  by the `wiki-conflict` SKILL's documented failure-mode, which
  is why we replay rather than `touch <path>.proposed`.
  The drifted page is pinned to `wiki/people/example-contact.md`
  (the personal recipe creates `wiki/people/` via the `people`
  primitive) so `_replay_drift` and the committed tree cannot
  disagree about the path.

## Outputs

### Files in the repo (committed)

```
examples/
├── README.md                       # what these are, how regen works
├── _seed/
│   ├── family/                     # hand-authored seed pages copied into family-mini
│   │   └── wiki/<area>/<page>.md
│   └── work-os/
│       └── wiki/<area>/<page>.md
├── regenerate.py                   # idempotent rebuild script (three vaults)
├── family-mini/                    # committed, regenerable family vault
│   ├── .wiki.journal/journal.jsonl
│   ├── AGENTS.md, CORE.md, frontmatter.schema.yaml, .gitignore
│   ├── _templates/, skills/, wiki/, raw/, outputs/
│   └── (≥1, target 3–5 seed pages per primitive category)
├── work-os-mini/                   # committed, regenerable work-os vault
│   └── (same shape as family-mini)
└── conflict-pending/               # committed, regenerable personal vault with one drifted page
    ├── .wiki.journal/journal.jsonl  # carries PageProposalEvent for wiki/people/example-contact.md
    ├── wiki/people/example-contact.md           # on-disk version (simulated user edit)
    └── wiki/people/example-contact.md.proposed  # kit-proposed version

docs/guides/tutorials/
├── README.md                       # updated: index of available tutorials
├── tutorial-1-first-vault.md       # NEW
└── tutorial-2-work-os-walkthrough.md  # NEW

docs/guides/how-to/
├── README.md                       # updated: index
└── resolve-a-conflict.md           # NEW

tests/
└── integration/
    ├── test_tutorials.py           # NEW — tutorial-drift gate (covers how-to too)
    └── test_examples_regenerable.py  # NEW — regenerate.py is idempotent
```

`examples/_seed/` is intentionally not given its own README; the
directory structure mirrors the recipe-rendered `wiki/<area>/` shape
exactly, and `examples/README.md` documents the seed convention in
one paragraph for any maintainer authoring new pages.

### Journal events appended (by `regenerate.py` and by tutorial steps)

- `VaultInitEvent`, `PrimitiveInstallEvent` per `wiki init` — produced
  by the existing `_cmd_init` path; the spec adds no new event kinds.
- `PageWriteEvent` per seed page that the regenerator writes through
  `safe_write` (it MUST — see §Constraints "No bypass of `safe_write`").
- `IngestRoutedEvent` per `wiki ingest` invocation in the tutorials.
- `OperationRunEvent` per `wiki run` invocation in the tutorials.
- `PageProposalEvent` already present in `examples/conflict-pending/`'s
  committed journal (produced by the regenerator's drift-replay
  function at build time).
- `PageConflictResolvedEvent` appended by the `wiki resolve` step in
  the how-to.

## Behavior

### Two-surface convention (used by all tutorials and the how-to)

Every step is one of two shapes so a reader never confuses "type
this into my terminal" with "ask Claude to do this":

- **`$`** — runs in the reader's shell. The tutorial-drift gate
  (§Acceptance AC3/AC4/AC5) executes these.
- **`>`** — a prompt the reader types into their Claude Code session
  (or any agent attached to the vault). The gate does NOT execute
  these; it only asserts they exist where the tutorial promises
  them (AC10).

**Fence and prefix rules — load-bearing, parser contract:**

- A line carries an executable `$` or `>` prefix **only inside a
  fenced code block whose info-string is exactly `bash`**
  (i.e. the line opens with ` ```bash `). Markdown blockquotes
  outside fences (`> Note:` prose) are ignored by the gate.
- Inside a `bash` fence, lines starting with `$ ` are executed in
  order. Lines starting with `> ` are recognized as Claude-prompt
  markers (counted for AC10) but not executed. Any other line
  inside a `bash` fence (blank, comment, output sample) is
  prose-context and ignored by the parser.
- Install commands and other "show, don't run" commands live in
  fences with a non-`bash` info-string (use ` ```sh ` for install
  examples) or as inline code. The gate ignores any fence whose
  info-string is not exactly `bash`.
- **Lines outside any fenced block, or inside any fence whose
  info-string is not exactly `bash`, are ignored by the gate
  regardless of prefix.** Inline code (single backticks) and
  paragraph text never produce executable steps even if they
  literally start with `$ ` or `> `. The fence boundary is the
  only thing that turns a prefix into a parser event.

**Shell-state continuity across `$` lines — gate contract:**

All `$` lines extracted from a single tutorial (or how-to) are
concatenated, in order, into a single `bash -c` invocation that
preserves cwd, environment, and shell state across lines. The
gate prepends `set -euo pipefail` so the first non-zero exit
terminates the script and surfaces as the test's failure. This
means a `$ cd my-first-vault` in step 2 carries through to a
`$ cat .wiki.journal/journal.jsonl | head` in step 3; step 3
does not need to repeat the `cd`. Tutorials may rely on this:
every step after a `$ cd …` reads from the new cwd, and shell
variables set with `$ FOO=bar` (rare, but allowed) survive
through the rest of the script.

The brief reads "every CLI invocation in a tutorial MUST work
end-to-end." This spec interprets that as: every `$ `-prefixed line
inside a `bash` fence exits 0 and produces the journal events the
tutorial calls out. Claude-driven follow-up steps (the `>` lines)
are documented but not executed by CI — verifying Claude output
would require the ANTHROPIC_API_KEY-gated eval harness, which is
out of scope here (those evals live in `tests/evals/`, Task 20).

### Tutorial 1 — "Create your first vault" (recipe-agnostic)

**Audience:** a non-engineer who has just installed the kit and wants
to confirm it works. Reading time ≤ 15 minutes; doing time ≤ 25
minutes.

**Prerequisites box (prose, not executable):**

- Python 3.11 or newer.
- `llm-wiki-kit` installed (`pip install llm-wiki-kit`, shown in a
  non-`bash` fence so the gate doesn't try to install in CI).
- `wiki --version` ≥ the version the tutorial pins (AC11's gates
  exercise the pin).

Tutorial 1 deliberately targets the `personal` recipe — the smallest
recipe — so the first vault feels approachable. There is no
committed `personal-mini/` example vault; the reader builds their
own. A reader wanting a reference vault is pointed at
`examples/work-os-mini/` (or family-mini) at the end.

Executable steps (each a `$` line inside a `bash` fence):

1. `wiki init my-first-vault --recipe personal` — `family` and
   `work-os` get mentioned in prose with a one-line "pick this if
   you're …".
2. `cd my-first-vault && wiki doctor` (succeeds silently with exit
   0; the tutorial calls this out — "no news is good news").
3. `cat .wiki.journal/journal.jsonl | head` — read the journal as
   plain JSONL. The tutorial walks one line and names each field.
4. **Ingest dispatch.** The reader creates a small fixture
   (`echo "..." > raw/note.md`) and runs `wiki ingest --as
   meeting raw/note.md`. The `--as` flag is the genuine
   override here — `note.md` matches no auto-router pattern
   (the auto-router in `templates/content-types/meeting/primitive.yaml`
   only fires on filenames containing `transcript`/`meeting-notes`/`standup`
   or `.vtt`/`.srt` extensions), so the tutorial teaches the
   reader to name the content-type explicitly when their source
   doesn't carry a routing hint. The tutorial describes the
   dispatch line in prose (paraphrased, not pasted) and a
   follow-up `cat .wiki.journal/journal.jsonl | tail -1` to see
   the `ingest.routed` event. A `>` line then asks the reader to
   open the vault in Claude Code and run the routed
   `ingest-meeting` skill — the tutorial is explicit that this is
   where the page actually gets created, and that the rest of the
   tutorial does not depend on the Claude-produced page existing.
5. **Operation dispatch.** `wiki run weekly-digest` (personal
   recipe ships `weekly-digest`). Same shape: dispatch line in
   prose, journal event via `cat`, `>` line for the Claude
   handoff.
6. **Read the journal once more.** One `$` line:
   `cat .wiki.journal/journal.jsonl`. One sentence of prose: "this
   file is the source of truth for what's happened in your vault."

**See also (footer, not a numbered step):** the planned commands
`wiki journal tail` / `wiki search` / `wiki upgrade` are listed in
a one-line "coming soon" pointer; tutorial 2 and the resolve-a-
conflict how-to are cross-linked. This is a Diátaxis-tutorial-
permitted footer, not narrative.

### Tutorial 2 — work-os walkthrough

**Audience:** a non-engineer professional (a manager, a solo
operator, an account lead) who has read tutorial 1 and wants to see
the work-os recipe in practice. Reading time ≤ 20 minutes; doing
time ≤ 30 minutes.

The reader builds a vault that ends up identical in *shape* (not
content; content seeds are hand-authored) to
`examples/work-os-mini/`. Steps:

1. `wiki init my-work-os --recipe work-os`.
2. `wiki doctor`, then `ls wiki/` and `ls skills/` — orient the
   reader to the produced layout (people, meetings, projects,
   customers, domains, decisions, etc.).
3. Walk *one* primitive category in depth —
   `stakeholder-update` (content-type) feeding
   `stakeholder-map-refresh` (operation). The reader writes a
   stakeholder-update transcript to a fixture file
   (`echo "..." > raw/q3-board-sync.md`) and runs
   `wiki ingest --as stakeholder-update raw/q3-board-sync.md`.
   Stdin-mode ingest (`wiki ingest --as <type> -`) is a real CLI
   feature but the tutorial uses a file path so the gate's
   subprocess shell doesn't need a stdin-provisioning contract
   (see §Constraints "No stdin-pipe shape in tutorial $-lines").
4. Dispatch `wiki run stakeholder-map-refresh` and read the
   resulting journal event via `cat`. Hand off to Claude in a
   `>` line.
5. A short "what's next" pointing at the resolve-a-conflict how-to
   and the (forthcoming) family tutorial.

### How-to — "Resolve a conflict"

**Audience:** a reader who already has a vault and has been told (by
the kit, by Claude, or by `wiki doctor`) that there's a `.proposed`
sidecar to resolve. Problem-oriented; the reader knows what they
want.

The how-to walks one canonical scenario end-to-end, operating on a
*copy* of the committed `examples/conflict-pending/` vault so the
reader does not have to construct drift themselves:

1. **Copy the pre-baked drifted vault.** One `$` line:
   `cp -R <repo-root>/examples/conflict-pending /tmp/conflict-demo
   && cd /tmp/conflict-demo`. The how-to gives two ways to find
   `<repo-root>`: from a clone, it's the working tree; from a
   pip-installed kit, it's the `wiki info examples-path` output
   (if available) or a documented `pip show -f llm-wiki-kit |
   grep examples` path. The CI gate uses the repo-root path
   directly.
2. `wiki doctor` — surfaces `pending_proposals`. The how-to
   paraphrases the output and asserts on exit code, not literal
   text.
3. **Read the three versions** (baseline / on-disk / proposed) —
   the how-to shows `cat <path>` and `cat <path>.proposed` and
   notes that the baseline lives in the journal (and that
   `wiki journal explain` is a planned-but-not-yet-shipped
   surface — until it lands, the journal can be read directly).
4. **Choose a merge mode.** The how-to presents three commands —
   `wiki resolve <path>`, `wiki resolve <path> --accept`,
   `wiki resolve <path> --keep` — and explains when to use each.
   The walked example uses `--accept` because it requires no
   merged-content piping and is the simplest path to assert in
   CI; the other two are named with one-line guidance.
5. **Confirm resolution.** `wiki doctor` again (clean), then a
   `cat .wiki.journal/journal.jsonl | tail` to show the
   `page.conflict_resolved` event (produced by a
   `PageConflictResolvedEvent` appended by `wiki resolve`).

The how-to explicitly cross-links the vault-side
`core/files/skills/wiki-conflict/SKILL.md` and notes that, inside a
real Claude session, Claude loads that skill automatically when a
`.proposed` file appears — the how-to is for the reader operating
without Claude, or wanting to understand what Claude will do.

### Edge cases

- **Reader is on Python 3.10 or earlier.** Each tutorial's
  prerequisites box names "Python 3.11 or newer" and links to the
  setup guide; no in-tutorial workaround.
- **Reader skipped tutorial 1 before starting tutorial 2.** Tutorial
  2's prereqs block says "Read tutorial 1 first" and links it.
- **Reader's `wiki` is older than the tutorial.** Each tutorial's
  prereqs box pins a `wiki --version` lower bound; the
  tutorial-drift gate asserts the pin matches the live kit version
  on every PR.
- **Reader runs `wiki ingest` against a content-type that isn't
  installed in the recipe.** The CLI already prints "No
  content-type matched … Available: …" and exits non-zero. The
  tutorials pick example sources the loaded recipe handles, so the
  happy path is reachable; the no-match case is mentioned in a
  one-line aside but not walked.
- **Reader has no Claude session available.** All `$` lines still
  succeed; only the `>` lines (which the reader recognizes as
  "ask Claude to do this") are no-ops. The tutorial is explicit
  that the dispatch+journal pattern is observable without Claude.
- **Reader's repo-root path differs in CI vs. their machine.** The
  CI gate substitutes the literal repo-root path into the `$ cp
  -R …` line before executing; the how-to documents both
  invocations.

### Error cases

- **`examples/regenerate.py` crashes mid-run.** The script builds
  into an out-of-tree tmp dir, copies the result to a sibling
  staging directory under `examples/`, then swaps it over the
  committed `examples/<vault>/` via two same-filesystem
  `os.rename` calls (`committed → backup`, then `staged →
  committed`). POSIX `rename(2)` returns `ENOTEMPTY` on non-empty
  directory targets, so a single-call atomic swap is not
  available; the two-rename pattern is the next-best contract.
  An in-process failure on the second rename is rolled back by
  renaming the backup back into place; a double-fault (rollback
  ALSO fails) preserves the backup at the staging path and
  raises a `RuntimeError` naming the recovery command. AC7
  asserts the in-process rollback path.
- **Tutorial-drift gate flags a divergence.** CI fails with the
  offending tutorial path, the divergent `$` line's line number,
  the command that was run, and the actual exit code / stderr.
  The fix is to update the tutorial in the same PR as the CLI
  change.
- **`examples/_seed/<recipe>/…` page references a primitive that
  the recipe doesn't install.** `regenerate.py` raises with the
  page path and the missing primitive name; AC6 catches this.

## Invariants

- **Tutorials never bypass `safe_write`.** Every committed example
  vault was produced by `regenerate.py`, which uses the kit's own
  `safe_write` path for every seed-page write and every
  drift-replay step. No example file lands in git without a
  matching journal event.
- **Tutorials never depend on a Claude-produced artifact.** A
  reader who never opens Claude can complete every `$` line in
  order with the documented outcomes. The tutorial-drift gate
  runs in CI without `ANTHROPIC_API_KEY`.
- **`examples/family-mini/` and `examples/work-os-mini/` pass
  `wiki doctor` with exit 0 on every PR.**
  `examples/conflict-pending/` deliberately does *not* pass — it
  reports `pending_proposals`; AC1 encodes both expectations
  per-vault.
- **The example vaults are regenerable from `_seed/` + the kit.**
  Running `python examples/regenerate.py --check` exits 0 only
  when the committed `examples/<vault>/` tree byte-matches the
  output of a fresh regenerate, under the normalization rules in
  AC6.
- **Tutorials match the live CLI.** Every `$` line in every
  tutorial is exercised by `tests/integration/test_tutorials.py`
  against a freshly-installed `wiki` in CI. Stale output, renamed
  flags, removed verbs — all fail the gate.
- **`>` lines are visually distinct and not executed.** The gate
  parses tutorial markdown by fence + prompt prefix and only
  executes `$`-prefixed lines inside `bash` fences. A future
  maintainer adding a `>` line does not have to update the gate.
- **No new top-level directory beyond `examples/`.** All other new
  files land under existing trees (`docs/guides/`, `tests/`).
  `examples/` is the one new top-level dir, authorized by RFC-0001
  Task 21 (see §Constrained by).
- **No `Co-Authored-By: Claude` trailer, no "Generated with Claude
  Code" PR footer.** Per the kit's standing convention.

## Contracts with other modules

- **Calls** `llm_wiki_kit.cli.main` (from `examples/regenerate.py`
  and from `tests/integration/test_tutorials.py`) — drives the kit
  the same way a user would.
- **Calls** `llm_wiki_kit.write_helper.safe_write` (from
  `examples/regenerate.py` for seed pages and for the
  drift-replay function that builds `examples/conflict-pending/`)
  — the same path the kit's own writes take. The regenerator's
  drift-replay journal lineage lands in the committed
  `examples/conflict-pending/.wiki.journal/journal.jsonl`, so
  the per-PR `--check` mode compares the committed journal
  against a re-replayed one under AC6's normalization.
- **Reads** `llm_wiki_kit.journal.read_events` and
  `llm_wiki_kit.models.PageProposalEvent` /
  `PageConflictResolvedEvent` (from the regenerability test, to
  normalize and to assert event shape).
- **Does not touch** any vault outside `examples/<vault>/`
  (committed) or `tmp_path` (CI). No write into the developer's
  home directory.
- **The vault-side `core/files/skills/wiki-conflict/SKILL.md` is
  the contract** the how-to documents; the how-to MUST stay
  consistent with that SKILL (cross-linked from each).

## Acceptance criteria

Each translates to one or more tests under `tests/integration/`.

- [ ] **AC1 — Per-vault `wiki doctor` expectations hold.** A
  parametrized test runs `wiki doctor` (cwd=vault, `check=False`)
  for each committed example vault and asserts:
  - `examples/family-mini/`: `returncode == 0`.
  - `examples/work-os-mini/`: `returncode == 0`.
  - `examples/conflict-pending/`: `returncode != 0` and `b"pending-proposal" in stdout`
    (the literal token emitted by `llm_wiki_kit.doctor.PENDING_PROPOSAL`
    — hyphen, singular); the test also asserts on the presence of a
    `PageProposalEvent` in the vault's journal via
    `llm_wiki_kit.journal.read_events`.
  `stderr` is intentionally not asserted-empty — doctor may emit
  advisories that are not failures.
- [ ] **AC2 — Example vaults are seeded to the agreed floor.** For
  `examples/family-mini/` and `examples/work-os-mini/`, each
  recipe-created `wiki/<area>/` directory contains at least one
  hand-authored markdown page beyond the kit-rendered
  `README.md`. The narrative target is 3–5 per primitive category;
  the AC floor is ≥ 1 because higher floors penalize areas where
  3 plausible pages are hard to author. The implementer SHOULD
  hit the 3–5 target where the primitive admits it.
  `examples/conflict-pending/` is exempted (it ships one drifted
  page, not seeds).
- [ ] **AC3 — Tutorial 1 walks `$`-blocks end-to-end without an API
  key.** `tests/integration/test_tutorials.py::test_tutorial_1`
  parses `docs/guides/tutorials/tutorial-1-first-vault.md`,
  extracts every `$ `-prefixed line from every fence whose
  info-string is exactly `bash`, and executes them in order
  against `tmp_path` with a controlled subprocess env (whitelist:
  `PATH`, `HOME=tmp_path/home`, `LANG`, `LC_ALL`, `TMPDIR`,
  `SSL_CERT_FILE`, `VIRTUAL_ENV`; blacklist: any of
  `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY`, `GEMINI_API_KEY` —
  the test fails fast if a blacklisted key is set in the runner
  env). Each line must exit 0; the test passes on a runner with
  no Anthropic credentials.
- [ ] **AC4 — Tutorial 2 walks `$`-blocks end-to-end without an
  API key.** Same as AC3 for tutorial 2.
- [ ] **AC5 — Conflict how-to drives a real drift to a real
  resolution.** `test_resolve_a_conflict` executes every `$ `
  line in `docs/guides/how-to/resolve-a-conflict.md` in order
  (with `<repo-root>` substituted into the `cp -R` line at gate
  time), asserts that the copied tmp vault carries a
  `PageProposalEvent` in its journal after step 1, and asserts
  that step 5's final `wiki doctor` exits 0 with no pending
  proposals and that a `PageConflictResolvedEvent` was appended.
- [ ] **AC6 — Example vaults are regenerable.** `python
  examples/regenerate.py --check` exits 0 against the committed
  example vaults. The comparison rules:
  - For each file: byte-compare after the per-file normalizations
    below.
  - For JSONL journal files: replace the values of `timestamp`,
    `hash`, `content_hash`, and `source_hash` with the sentinel
    string `"<normalized>"` before comparing. Any *other*
    journal-line key is load-bearing — if a future field becomes
    non-deterministic, this AC is amended in the same PR.
  - Non-JSONL files compare byte-for-byte without normalization.
    Kit-rendered files (`AGENTS.md`, `CORE.md`, `.gitignore`,
    `frontmatter.schema.yaml`) contain `{vault_name}` and
    `{recipe_name}` substitutions; the regenerator's
    build-into-tmp step pins the tmp dir's basename to the
    committed vault's directory name (e.g. `family-mini`), so
    `{vault_name}` resolves to the same string every run. Recipe
    variables come from the recipe's own `variables:` block —
    today only the `personal` recipe declares `owner_*` fields
    (defaulting to empty strings); `family` and `work-os` declare
    only `recipe_name`. The regenerator does not override these;
    it relies on the recipe-declared defaults producing stable
    bytes, and asserts at build time that every non-`recipe_name`
    variable defaults to `""` (fail-loud if a future recipe edit
    introduces a non-empty default that would break AC6
    byte-equality). This is a load-bearing invariant the
    regenerator must uphold (see plan T2).
  - Filter out the OS-specific hidden files in this explicit
    allowlist: `.DS_Store`, `Thumbs.db`. Any other hidden file
    is load-bearing.
  - Walk directories with `sorted(os.listdir(d))` so traversal
    order is stable.
  CI runs this on every PR.
- [ ] **AC7 — `examples/regenerate.py` is idempotent and the
  in-process rollback works.** A test runs the script twice
  against a tmp dir and asserts the second invocation produces a
  tree byte-identical to the first (under AC6's normalization).
  A second test runs `apply_vault` against a populated
  destination, asserts pre-existing files are gone and the new
  tree is in place, and asserts no staging directories leaked.
  A third test patches `os.rename` so the *second* rename
  (staged → committed) raises, then asserts (a) the committed
  bytes are unchanged after the rollback, and (b) no staging
  directory leaked. The SIGKILL-mid-swap case is documented but
  not asserted by a test — the brief-absent window between the
  two renames cannot be exercised via in-process exceptions.
- [ ] **AC8 — Tutorial-drift CI gate runs in the default fast
  suite.** The new tests live under `tests/integration/` and
  are collected by the default `pytest` invocation (no marker
  decorator added that would exclude them from CI defaults).
  They require no network call and no API key. The `slow`
  marker remains reserved for the wheel-acceptance suite; the
  `eval` marker remains reserved for `tests/evals/`.
- [ ] **AC9 — Diátaxis genre boundaries hold.** Tutorial files
  contain no problem-oriented prose ("if you want to X, do Y")
  inside numbered steps, and the how-to contains no narrative
  arc ("first, you'll learn what a conflict is — then you'll set
  one up and watch it resolve"). Tutorial "see also" footers
  (one-line cross-links at the end of a tutorial) are explicitly
  allowed by this AC. Reviewer-only check (no automated gate);
  encoded here so adversarial-reviewer can verify against it.
- [ ] **AC10 — `>` lines exist where a real reader would need
  Claude, at the right line indices.** A small test walks each
  tutorial's fenced `bash` blocks in order and asserts:
  - Tutorial 1: at least one `>` line appears at a strictly
    greater line index in the same markdown file than the first
    `$ wiki ingest` line, and at least one `>` line appears at a
    strictly greater line index than the first `$ wiki run`
    line.
  - Tutorial 2: at least one `>` line appears at a strictly
    greater line index than the first
    `$ wiki ingest --as stakeholder-update ` line.
  - The how-to: zero `>` lines.
  Floor counts and positional checks both encode the spec.
- [ ] **AC11 — Mechanical gates pass.** `ruff check llm_wiki_kit
  tests`, `ruff format --check llm_wiki_kit tests`,
  `mypy llm_wiki_kit tests`, and `pytest -m 'not slow and not eval'`
  all exit zero on the PR branch.
- [ ] **AC12 — Manual cold walk recorded.** The PR body includes a
  one-paragraph note from the implementer recording the cold-walk
  pass through both tutorials in a fresh `tmp` dir, naming any
  step that required thought (those become docs bugs to fix
  before merge). Not an automated gate — a discipline gate, per
  the task brief. The reviewer-checklist line in
  `docs/CONVENTIONS.md` (added in this PR) makes the cold-walk
  paragraph the first thing the PR reviewer reads.
- [ ] **AC13 — No new runtime dependency.** `pyproject.toml`'s
  `[project].dependencies` matches the literal list
  `["pyyaml>=6", "pydantic>=2"]` (captured verbatim from
  `pyproject.toml` at plan time). A unit test reads the file
  and asserts equality.

## Non-goals

- **Tutorial 3 (family walkthrough).** Pinned to the next milestone.
  `docs/guides/tutorials/README.md` lists it as "coming next" with
  one line.
- **Migration of legacy `docs/guides/*.md`** (`customizing.md`,
  `setup.md`, `sync-options.md`, `web-clipper.md`, `file-formats.md`,
  `inventories.md`) into Diátaxis buckets. Tracked in the per-bucket
  README candidate lists; out of scope here.
- **Implementing any stub command** (`wiki journal tail`,
  `wiki journal grep`, `wiki journal explain`, `wiki search`,
  `wiki upgrade`). The tutorials work around the stubs by reading
  the journal file directly with `cat`. Each tutorial mentions the
  planned UX in one line so the reader knows it's coming.
- **Tutorial coverage of `wiki research`.** Mentioned in tutorial
  2's closing "what's next" with a one-line "optional, needs a
  Perplexity or Gemini key" pointer. Not exercised in any `$` line
  (per the Task 19 coordination note in the brief — target the
  dispatch shape, not provider-specific UX).
- **Eval-grade coverage of Claude-driven steps.** Asserting that
  Claude correctly executes the `ingest-<type>` or operation skill
  belongs in `tests/evals/` (Task 20). The tutorial-drift gate
  only asserts the dispatch boundary works.
- **A `wiki tutorial` CLI verb** (à la `git tutorial`). Tutorials
  are markdown the reader reads; no CLI surface added.
- **A standalone `python -m tests.fixtures.conflict_replay`
  invocation in the how-to.** Reviewer round 1 flagged that
  `python -m` won't resolve from the reader's vault cwd. The
  conflict drift now lives in a committed example vault
  (`examples/conflict-pending/`); the how-to operates on a copy.
- **In-browser interactive tutorials.** Plain markdown only. A
  "literate" executable-blocks format (asciidoctor `[source,bash]`,
  Jupyter, etc.) was considered and rejected — it adds a renderer
  dependency and obscures the simple "type this, see that" shape.
- **`examples/` as a discoverability surface for primitives.** The
  primitive catalog is `templates/`; `examples/` is shaped vaults,
  not a catalog. A reader looking for "what primitives ship" goes
  to `docs/architecture/overview.md`.
- **Versioning the example vaults independently.** They re-render
  every PR via the regen script; pinning a version on them would
  invite drift between the vault snapshot and the kit it ships
  alongside.
- **Replay/cassette support for tutorial commands.** The drift
  gate runs live against the local kit; that's the contract under
  test.
- **Snapshot-testing literal CLI stdout in tutorial markdown.**
  Reviewer round 1 surfaced the snapshot exception as
  under-defined. The gate now asserts on exit code + journal
  events only; tutorials paraphrase output in prose.

## Constraints

- **No new top-level directory beyond `examples/`.** All other new
  files land under existing trees (`docs/guides/`, `tests/`).
  `examples/` is authorized by RFC-0001 Task 21's literal listing
  (`conflict-pending/` is a subdir under that authorized umbrella).
- **No new runtime dependency.** The regenerator uses stdlib +
  the kit's own modules. Dev deps may grow only with a one-line
  rationale per addition in the plan.
- **No bypass of `safe_write` — one narrow exception.** Every
  *kit-write* in the example vaults is written through
  `safe_write`. Seed pages and the regenerator's kit-update
  writes all go through `safe_write`. The single documented
  exception is the user-edit-simulation line inside
  `_replay_drift`, which writes directly to disk with
  `Path.write_bytes` to produce the drift the conflict scenario
  depends on — `safe_write` structurally cannot simulate a user
  (it short-circuits to direct-write when `on_disk == baseline`,
  per `llm_wiki_kit/write_helper.py:131-145`, so a third
  `safe_write` would never detect drift). This carve-out joins
  the existing documented bypasses (`resolve_proposal` for
  user-mediated merges; `_ensure_obsidianignore` for the
  additive Obsidian-index config — see
  `docs/specs/safe-write-ordering/spec.md`). Any other
  vault-bound write through `Path.write_text`/`Path.write_bytes`
  is a bug.
- **No new public CLI verb.** Regenerators and gates are pytest /
  script concerns; the `wiki` surface is unchanged.
- **No new package under `llm_wiki_kit/`.** The drift-replay
  function lives inside `examples/regenerate.py`, not the runtime
  package; this keeps the wheel surface unchanged.
- **No reliance on Claude in CI.** The tutorial-drift gate and the
  regenerability test run on a runner with no `ANTHROPIC_API_KEY`,
  no `PERPLEXITY_API_KEY`, no `GEMINI_API_KEY` set. The `>` lines
  are not executed.
- **No use of `pytest.xfail` or `pytest.mark.slow` to skirt the
  gate.** Tutorial-drift failures are real failures; the fix is
  to update the tutorial.
- **No silent renaming of `$` / `>` line prefixes or the `bash`
  info-string requirement.** The fence-and-prefix convention is
  load-bearing for the parser; changing it requires a spec
  amendment.
- **Tutorial CLI output is paraphrased in prose, not pasted as
  literal stdout.** Pasting exact stdout invites bit-rot. The
  gate asserts on exit code + journal events. No snapshot blocks
  in tutorial markdown.
- **No stdin-pipe shape in tutorial `$`-lines.** Commands that
  read from `-` (`wiki ingest --as <type> -`,
  `wiki resolve <path>` without a flag) are real CLI surface but
  the tutorials use a file path or a flag-only invocation so the
  gate's subprocess shell never needs to provision stdin. The
  CLI feature is mentioned in prose with a "see also" pointer;
  it is not exercised in any `$` line.
- **`examples/conflict-pending/` uses the `personal` recipe.**
  The directory name is asymmetric with `family-mini`/`work-os-mini`
  (which name their recipe) because the vault's job is showing
  a conflict state, not a recipe-shape. `examples/README.md`
  documents the recipe inline, and §Outputs above lists it next
  to the file tree.
