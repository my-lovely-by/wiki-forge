# Plan: example vaults and first tutorials

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** `docs/specs/task-21-examples-tutorials/spec.md`
- **Owner:** Task 21 implementer

## Approach

Build the on-ramp **inside-out**: the regenerator first (so the three
committed example vaults are reproducible artifacts, not curated
snapshots), then the example vaults themselves (committed output of
the regenerator), then the tutorials (which mirror the regenerator's
command sequence so they cannot drift apart), then the conflict
how-to (which operates on the regenerator-built
`examples/conflict-pending/`).

The reason for that order: the regenerator IS the executable spec
for what a recipe-shaped vault looks like with seed content. Once
it exists, the example vaults are its deterministic output and the
tutorials are its prose walkthrough. If the kit's `wiki init` ever
renders a different tree, the regenerator's `--check` mode fails
loudly on every PR — a single gate covers vault-shape drift,
seed-content drift, tutorial-step drift, and conflict-flow drift.

Three structural calls worth flagging up front:

- **`examples/_seed/` separates hand-authored content from rendered
  output.** Seeds are markdown the maintainer wrote (recipes,
  sample meetings, a stakeholder note); rendered output is whatever
  `wiki init` produces plus the seeds, glued by `regenerate.py`.
  Keeping them apart means a recipe change re-renders the output
  without losing the seeds, and a seed-content edit doesn't touch
  any rendered file.
- **The conflict drift lives in a committed example vault, not a
  runtime helper.** Round-1 review surfaced that
  `python -m tests.fixtures.conflict_replay` won't resolve from
  the reader's vault cwd. The replacement is
  `examples/conflict-pending/`: a committed vault built by
  `regenerate.py`'s internal `_replay_drift()` function, carrying a
  real `.proposed` sidecar and a matching `PageProposalEvent`. The
  how-to walks `cp -R` of that vault into a tmp dir, then operates
  on it.
- **`examples/regenerate.py` lives outside the wheel surface.**
  `pyproject.toml` already declares `packages = ["llm_wiki_kit"]`,
  so neither `examples/` nor `tests/` end up in the published
  artifact. The regenerator is a development tool, invoked by
  contributors and CI; the wheel ships only the runtime package.

## Pre-conditions

- Tasks 10–20 already merged (current `main` at f90f36d). Every CLI
  verb the tutorials use (`init`, `add`, `doctor`, `ingest`, `run`,
  `resolve`) is shipped and exercised by integration tests.
- `wiki research` (Task 18/19) is shipped but the tutorials do not
  exercise it in `$` lines (see spec §Non-goals — Task 19
  coordination).
- `core/files/skills/wiki-conflict/SKILL.md` is on `main` and
  stable.
- `wiki ingest` accepts `-` as a stdin sentinel (verified at
  `llm_wiki_kit/ingest.py:48` and `llm_wiki_kit/cli.py:1236`) —
  but tutorials intentionally exclude stdin-pipe shapes from
  `$`-lines per spec §Constraints. This pre-condition is listed
  for completeness; the tutorials use file-path form.
- `pyproject.toml`'s `[project].dependencies` is the literal
  `["pyyaml>=6", "pydantic>=2"]` at plan time.

## Construction tests (write before any production code)

These tests encode the contracts the implementation has to satisfy.
Land them red, then make them green step by step. They live under
`tests/integration/`, are collected by `pytest -m 'not slow and not
eval'`, and require no API keys.

1. **`test_example_vaults_doctor_per_vault`** (AC1) — parametrize
   over the three committed vaults; for each, run
   `subprocess.run(["wiki", "doctor"], cwd=vault, check=False,
   capture_output=True)` (the `capture_output=True` is
   load-bearing — without it `.stdout`/`.stderr` are `None` and
   the marker assertion below crashes). Assert per-vault outcome:
   `family-mini` and `work-os-mini` exit 0; `conflict-pending`
   exits non-zero with `b"pending-proposal"` (hyphen, singular —
   the literal token from `llm_wiki_kit.doctor.PENDING_PROPOSAL`)
   in stdout AND carries a `PageProposalEvent` in its journal
   (read via `llm_wiki_kit.journal.read_events`). `stderr` is
   intentionally not asserted empty.
2. **`test_example_vaults_are_seeded`** (AC2) — for
   `examples/family-mini/` and `examples/work-os-mini/`, iterate
   `wiki/<area>/` directories; assert that `len([p for p in
   area.iterdir() if p.suffix == ".md" and p.name != "README.md"])
   >= 1` per area. The implementer SHOULD hit 3–5 per primitive
   category where authoring permits; the AC floor is 1.
3. **`test_regenerate_check_mode_clean`** (AC6) — pin
   `REPO_ROOT = Path(__file__).resolve().parents[2]`; invoke
   `subprocess.run([sys.executable, str(REPO_ROOT /
   "examples/regenerate.py"), "--check"], cwd=str(REPO_ROOT),
   check=False)`; assert exit 0, stdout empty. The same
   `REPO_ROOT` constant is reused by tests #4 and #5 for imports
   (see Step T1).
4. **`test_regenerate_is_idempotent`** (AC7) — call
   `regenerate.build_vault(recipe="family", target=tmp_path / "a")`
   twice into two tmp dirs, normalize per the AC6 rules
   (timestamp / hash / content_hash / source_hash → sentinel),
   assert the trees are byte-identical. Also assert that
   `regenerate.build_conflict_pending(target=…)` twice yields
   identical trees.
5. **`test_regenerate_crash_safety`** (AC7) — simulate a crash by
   monkeypatching `os.replace` to raise mid-apply; assert the
   committed `examples/<vault>/` directory is unchanged.
6. **`test_tutorial_1_runs_end_to_end`** (AC3) — parse the
   tutorial, extract each `$ `-prefixed line from each fence
   whose info-string is exactly `bash`, **concatenate the
   extracted lines into a single `bash -c` script** (per spec
   §Behavior "Shell-state continuity across `$` lines") with
   `set -euo pipefail` prepended, and execute that single
   subprocess against `tmp_path`. Env-whitelist policy per AC3
   (`PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, `SSL_CERT_FILE`,
   `VIRTUAL_ENV`; blacklist `ANTHROPIC_API_KEY`,
   `PERPLEXITY_API_KEY`, `GEMINI_API_KEY` — fail fast if any
   blacklisted key is set in the runner env). Assert the
   subprocess exits 0; on failure, surface the concatenated
   script and the captured stderr for debugging.
7. **`test_tutorial_2_runs_end_to_end`** (AC4) — same as 6 for
   tutorial 2.
8. **`test_resolve_a_conflict_runs_end_to_end`** (AC5) —
   substitute the repo-root path into the how-to's `$ cp -R …`
   line at gate time (string-replace `<repo-root>` with the
   pinned `REPO_ROOT` literal), then concatenate every `$ `
   line into a single `bash -c` script with `set -euo pipefail`
   prepended and execute. Assert that after step 1's `cd` the
   copied tmp vault carries a `PageProposalEvent` (read from
   `<tmp>/conflict-demo/.wiki.journal/journal.jsonl`); assert
   the final `wiki doctor` exits 0 with no `pending-proposal`
   marker and that a `PageConflictResolvedEvent` was appended.
9. **`test_two_surface_block_counts_and_positions`** (AC10) —
   parse each tutorial; for tutorial 1, find the line index of
   the first `$ wiki ingest` and the first `$ wiki run` lines in
   the markdown file, then assert there is at least one `>` line
   at a *strictly greater* line index in the same file for each;
   for tutorial 2, at least one `>` line index is strictly
   greater than the first
   `$ wiki ingest --as stakeholder-update ` line index; for the
   how-to assert
   `len(list(iter_claude_prompt_lines(howto_path))) == 0`
   (equality, not floor — a stray `>` inside any `bash` fence
   fails the test).
10. **`test_no_new_top_level_dirs_beyond_examples`** — pin the
    repo root via `Path(__file__).resolve().parents[2]` (the test
    file lives at `tests/integration/test_tutorials.py`, so
    `parents[2]` is the repo root). Pin the expected pre-task
    top-level directory set as a frozen literal inside the test
    body with a comment referencing this plan; assert
    `set(os.listdir(repo_root)) ⊇ pre_task_set ∪ {"examples"}` and
    that no *additional* unexpected top-level directory has
    appeared. (The set comparison is `⊇` rather than `==` so that
    pre-existing directories that the test author didn't enumerate
    don't trip the test; the load-bearing assertion is "no
    unexpected new dir.")
11. **`test_no_new_runtime_dep`** (AC13) — parse
    `pyproject.toml`'s `[project].dependencies` via tomllib and
    assert it equals the literal `["pyyaml>=6", "pydantic>=2"]`.

All eleven tests should be red at end of Step T1 and green by end
of Step T6.

## Steps

Steps are sequential. Each declares its dependency and verification
mode explicitly per the work-loop's PLAN convention.

### T1. Construction tests landed and red.
- **Depends on:** none.
- **Verification mode:** TDD (red tests are the artifact).
- **What changes:** new files
  `tests/integration/test_tutorials.py` and
  `tests/integration/test_examples_regenerable.py`. The eleven
  tests above land as plain failing tests (no `xfail`). Both
  files pin `REPO_ROOT = Path(__file__).resolve().parents[2]`
  at module level. `test_examples_regenerable.py` imports
  `regenerate` via:
  ```python
  import sys
  sys.path.insert(0, str(REPO_ROOT))
  from examples import regenerate  # noqa: E402
  ```
  This is the project-blessed way for a test under `tests/` to
  reach a sibling non-package directory; the `noqa` is paired
  with the `sys.path` insert that precedes it.
- **Done when:** `pytest tests/integration/test_tutorials.py
  tests/integration/test_examples_regenerable.py` — every test
  fails for the expected reason (missing file, missing dir,
  missing command). Do not move on if any test fails for a
  *different* reason than "the artifact doesn't exist yet."

### T2. `examples/regenerate.py` produces a clean family vault.
- **Depends on:** T1.
- **Verification mode:** TDD against tests #3, #4 (idempotence);
  goal-based for the CLI surface (`--check`, `--apply` exit codes).
- **What changes:** new file `examples/regenerate.py` with:
  - The recipe → target → seed-dir mapping the regenerator
    operates over:

    | recipe       | target.name        | seed dir                 |
    |--------------|--------------------|--------------------------|
    | `family`     | `family-mini`      | `examples/_seed/family/`     |
    | `work-os`    | `work-os-mini`     | `examples/_seed/work-os/`    |
    | `personal`   | `conflict-pending` | (no seeds — drift-replay only) |

  - `build_vault(recipe, target)` — requires `target.name` to
    equal `"family-mini"` or `"work-os-mini"` per the table;
    calls `llm_wiki_kit.cli.main(["init", str(target), "--recipe",
    recipe])` and copies any `<seed_dir>/wiki/**` pages into the
    target via `safe_write` (each seed page lands with a
    `PageWriteEvent`). The `target.name` invariant is
    load-bearing for AC6 (kit-rendered files carry `{vault_name}`
    substitutions keyed to the basename).
  - `build_conflict_pending(target)` — `target.name` must equal
    `"conflict-pending"`. Calls `wiki init` with
    `--recipe personal`, then invokes
    `_replay_drift(target, page_rel="wiki/people/example-contact.md")`.
    Signature takes no clock argument — it calls `safe_write`
    directly and lets `write_helper._now()` fire.
    `_replay_drift` executes a three-step sequence:
    1. `safe_write(target, page_rel, _REPLAY_CONTENTS["initial"])`
       — produces the baseline `PageWriteEvent`.
    2. `(target / page_rel).write_bytes(_REPLAY_CONTENTS["user_edit"].encode())`
       — *direct* disk write simulating the user editing the
       page. This is the single documented `safe_write` carve-out
       (see spec §Constraints "No bypass of `safe_write` — one
       narrow exception"); `safe_write` short-circuits to
       direct-write when `on_disk == baseline`
       (`write_helper.py:131-145`), so a third `safe_write` would
       never detect drift without this step.
    3. `safe_write(target, page_rel, _REPLAY_CONTENTS["kit_update"])`
       — detects `on_disk_hash != baseline_hash`, writes the
       `.proposed` sidecar, and appends the `PageProposalEvent`.

    **No clock pinning needed**: `PageWriteEvent.hash` /
    `PageProposalEvent.hash` are sha256 of *content* (not
    timestamped), and AC6 normalizes the `timestamp` JSON key
    out of the comparison. Content is fixed string literals
    (see `_REPLAY_CONTENTS` below), so non-timestamp fields are
    deterministic-by-construction.
  - `_REPLAY_CONTENTS` — module-level dict mapping
    `{"initial", "user_edit", "kit_update"}` to fixed
    markdown-string literals (`"# Initial\n"`, `"# User
    edit\n"`, `"# Kit update\n"` or similar). One source of
    truth so the §Risks pointer (below) and the body of
    `_replay_drift` cannot drift apart.
  - `_check_mode()` — for each (recipe, committed_dir) pair,
    builds into a tmp directory whose basename equals
    `committed_dir.name` (via `mkdtemp` prefix + a child
    directory created with the canonical basename), normalizes
    journals per AC6, byte-compares against committed trees;
    exits 0 on clean, non-zero with a unified-diff fragment on
    divergence.
  - `_apply_mode()` — same build-then-canonical-basename
    discipline; final step `os.replace`s the tmp dir over the
    committed `examples/<vault>/` atomically.
  - Every `wiki init` invocation passes recipe variables as
    empty strings (except `recipe_name`, which the recipe loader
    already populates) so the rendered `{owner_*}` substitutions
    produce stable bytes across runs (see spec AC6).
- **Done when:** `test_regenerate_is_idempotent` (#4) and
  `test_regenerate_crash_safety` (#5) green;
  `test_regenerate_check_mode_clean` (#3) still red (no committed
  vaults yet).

### T3. Hand-author seeds and commit all three example vaults.
- **Depends on:** T2.
- **Verification mode:** Goal-based (`wiki doctor` exit code on each
  vault is the contract) plus mechanical (AC6 byte-comparison).
- **What changes:**
  - Seed authoring applies only to `family-mini` and
    `work-os-mini`; `conflict-pending` is built entirely by
    `_replay_drift` and carries no `examples/_seed/personal/`
    content (AC2 exempts it).
  - Author markdown pages under `examples/_seed/family/wiki/**`
    and `examples/_seed/work-os/wiki/**`. Floor is 1 per
    recipe-created `wiki/<area>/` directory; the target counts
    below balance "populated, not empty" against the
    minimal-diff principle. Per-area targets the implementer
    should hit:
    - **family** — `food`: 3, `meetings`: 2, `people`: 2,
      `medical`: 1, `trips`: 2, `vendors`: 1, `receipts`: 1,
      `actions`: 2, `tax`: 1. Areas this enumeration may have
      missed (because they appear transitively via `requires:`)
      land at the floor of 1; T3 re-derives the exact set from
      a fresh `wiki init` against the family recipe before
      authoring.
    - **work-os** — `stakeholder-updates`: 3, `projects`: 2,
      `customers`: 2, `domains`: 1, `decisions`: 2, `meetings`: 2,
      `people`: 2, `vendor-contracts`: 1, `customer-feedback`: 1,
      `interviews`: 1, `actions`: 1. Same caveat: T3 re-derives
      the area set from `wiki init` against the work-os recipe;
      any area the table missed lands at floor 1.
    The area names above are best-effort guesses derived from
    each recipe's primitive closure; the authoritative list comes
    from `ls examples/family-mini/wiki/` and
    `ls examples/work-os-mini/wiki/` after T2 lands. T3
    re-derives the area set there and adjusts the target counts
    if any name differs (e.g. plural vs. singular). Author counts
    above the target are welcome where authoring is cheap and
    the pages stay plausible; counts below the target need a
    one-line note in the PR body. Pages must be
    plausible (a real recipe in `wiki/food/`, a real meeting
    page in `wiki/meetings/`, a real stakeholder update under
    `wiki/stakeholders/`, etc.) — non-engineer reviewers will
    skim them.
  - Run `python examples/regenerate.py --apply` to land the
    committed `examples/family-mini/`, `examples/work-os-mini/`,
    and `examples/conflict-pending/` trees.
  - Author `examples/README.md` (top-level intro) explaining the
    three vaults, the recipe of each (`conflict-pending` uses
    `personal`), and the regen workflow. The seed convention is
    documented in one paragraph here; `examples/_seed/` does not
    get its own README.
- **Done when:** `test_example_vaults_doctor_per_vault` (#1),
  `test_example_vaults_are_seeded` (#2), and
  `test_regenerate_check_mode_clean` (#3) all green.

### T4. Tutorial 1 (recipe-agnostic) walkable end-to-end.
- **Depends on:** T3.
- **Verification mode:** TDD against tests #6 and #9 (positional `>`
  for tutorial 1).
- **What changes:**
  - New file `docs/guides/tutorials/tutorial-1-first-vault.md` per
    spec §Behavior "Tutorial 1." Each `$` line lives inside a
    fenced block with info-string exactly `bash`; each `>` line
    inside another `bash` fence. The install command lives in a
    non-`bash` fence (info-string `sh`) so the parser ignores it.
  - Bash-block parser inside
    `tests/integration/test_tutorials.py`: a single function
    `iter_executable_lines(md_path) -> Iterator[tuple[int, str]]`
    that walks fenced blocks whose info-string is exactly `bash`,
    yields `(line_no, command)` for lines starting with `$ ` (with
    the `$ ` stripped), and a sibling
    `iter_claude_prompt_lines(md_path) -> Iterator[int]` that
    yields the line numbers of `> `-prefixed lines inside `bash`
    fences. Inline in the test file (one caller).
  - Update `docs/CONVENTIONS.md` with a one-line
    reviewer-checklist pointer for AC12 (PR cold-walk paragraph).
    Pinned placement: in the existing `## Pull-request reviewer
    checklist` section (or add a `### Tutorial-touching PRs`
    subsection if that header doesn't exist); add the literal
    sentence "When a PR touches `docs/guides/tutorials/`, the
    reviewer reads the PR's cold-walk paragraph first; missing
    paragraph or unaddressed thinks-required steps block merge."
    Scope: one sentence + an optional subsection header; does
    not require an RFC because it's a clarifying annotation
    rather than a process change.
- **Done when:** `test_tutorial_1_runs_end_to_end` (#6) green;
  tutorial-1 portion of test #9 green.

### T5. Tutorial 2 (work-os walkthrough) walkable end-to-end.
- **Depends on:** T4 (reuses the parser added in T4).
- **Verification mode:** TDD against tests #7 and #9 (tutorial-2
  positional check).
- **What changes:**
  - New file
    `docs/guides/tutorials/tutorial-2-work-os-walkthrough.md` per
    spec §Behavior "Tutorial 2." Reuse the same fenced-block
    conventions; mirror the regenerator's command sequence so the
    tutorial-produced vault matches the `examples/work-os-mini/`
    shape. Deep-dive primitive is `stakeholder-update`
    (content-type) feeding `stakeholder-map-refresh` (operation).
    The reader writes a transcript to `raw/q3-board-sync.md` via
    `echo`, then runs `wiki ingest --as stakeholder-update
    raw/q3-board-sync.md` (file-path form). The stdin sentinel
    (`-`) exists as a real CLI feature but is intentionally
    excluded from tutorial `$`-lines per spec §Constraints — the
    cross-reference belongs in prose only, never as an
    executable step.
  - Update `docs/guides/tutorials/README.md` index: both tutorials
    listed with one-line descriptions, "tutorial 3 (family) is the
    next milestone" entry.
- **Done when:** `test_tutorial_2_runs_end_to_end` (#7) green;
  test #9 fully green.

### T6. Conflict how-to walkable end-to-end against the committed vault.
- **Depends on:** T3 (needs `examples/conflict-pending/`).
- **Verification mode:** TDD against test #8.
- **What changes:**
  - New file `docs/guides/how-to/resolve-a-conflict.md` per spec
    §Behavior "How-to." Step 1 is `$ cp -R <repo-root>/examples/conflict-pending /tmp/conflict-demo
    && cd /tmp/conflict-demo`; the gate substitutes the real
    repo-root path before executing. The walked example uses
    `wiki resolve <path> --accept` (simplest CI assertion). The
    other two modes (`--keep`, default merge) get one-line
    guidance.
  - Cross-link the vault-side
    `core/files/skills/wiki-conflict/SKILL.md`.
  - Update `docs/guides/how-to/README.md` index.
- **Done when:** `test_resolve_a_conflict_runs_end_to_end` (#8)
  green.

### T7. Cold walk + mechanical gates.
- **Depends on:** T4, T5, T6.
- **Verification mode:** Visual / manual QA (AC12); plus the four
  mechanical gates.
- **What changes:** documentation polish only — fixes to any
  tutorial step the cold-walk surfaced as requiring thought.
- **Done when:**
  - Cold-walked tutorial 1 then tutorial 2 in a fresh `tmp` dir
    using only the literal commands from the published markdown;
    every step landed without "wait, what?" moments.
  - The four mechanical gates in §Verification gate exit zero.
  - The PR body carries the AC12 cold-walk paragraph.

## Verification gate

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow and not eval'
```

Plus the manual cold-walk (AC12), recorded in the PR body.

Each acceptance criterion in the spec maps to a construction test
or a mechanical-gate invocation:

| AC  | How it's verified |
|-----|-------------------|
| AC1 | `test_example_vaults_doctor_per_vault` (#1) |
| AC2 | `test_example_vaults_are_seeded` (#2) |
| AC3 | `test_tutorial_1_runs_end_to_end` (#6) |
| AC4 | `test_tutorial_2_runs_end_to_end` (#7) |
| AC5 | `test_resolve_a_conflict_runs_end_to_end` (#8) |
| AC6 | `test_regenerate_check_mode_clean` (#3) |
| AC7 | `test_regenerate_is_idempotent` (#4) + `test_regenerate_crash_safety` (#5) |
| AC8 | The new tests run under `pytest -m 'not slow and not eval'` (CI invocation) |
| AC9 | Reviewer check (adversarial-reviewer reads against spec §AC9) |
| AC10 | `test_two_surface_block_counts_and_positions` (#9) |
| AC11 | The four gates above |
| AC12 | PR-body paragraph (discipline gate) |
| AC13 | `test_no_new_runtime_dep` (#11) |
| Invariant: "No new top-level directory beyond `examples/`" | `test_no_new_top_level_dirs_beyond_examples` (#10) |

## Risks

- **Seed pages drift away from primitive contracts.** A primitive
  evolves its frontmatter schema; the committed seed page no
  longer validates. Mitigation: AC1's per-vault `wiki doctor`
  catches this on every PR; the fix is a seed re-author in the
  same PR as the primitive change.
- **Tutorial-drift gate goes from "useful" to "annoying."** A
  cosmetic CLI tweak shouldn't force a tutorial rewrite.
  Mitigation: the gate asserts on exit code + journal events
  only, never on literal stdout, per spec §Constraints. The
  snapshot-block exception from round 1 was removed entirely.
- **`examples/regenerate.py` import root.** `pyproject.toml`'s
  package finder is `packages = ["llm_wiki_kit"]` (literal); the
  regenerator's `examples` directory is not on the wheel surface.
  Verified at plan time.
- **Personal recipe's `meeting` content-type panics on an empty-ish
  source.** Tutorial 1's `wiki ingest raw/note.md` step would
  fail at the CLI before reaching the `>` handoff. Mitigation:
  T4 picks the source so the routing layer returns a `Routed`,
  not `NoMatch`; the resulting `ingest.routed` event is
  observable; the gate asserts on event existence, not Claude
  artifact.
- **`examples/conflict-pending/` regeneration is non-deterministic
  in subtle ways.** Mitigation: `_replay_drift` passes fixed
  content strings (see `_REPLAY_CONTENTS` in T2) to every
  `safe_write` call; `PageWriteEvent.hash` and
  `PageProposalEvent.hash` are sha256 of *content* (not
  timestamps), so the only per-run-varying field is `timestamp`
  itself, which AC6 normalizes. No clock-pinning is needed.
  Verified at `llm_wiki_kit/models.py:274-287` (no timestamp in
  the hash input) and `llm_wiki_kit/write_helper.py:124-231`
  (timestamps are read once per event for the `timestamp` field
  only).
- **Cold-walk reveals a fundamental UX issue.** Mitigation: AC12
  explicitly authorizes the implementer to update tutorials based
  on cold-walk findings in the same PR; if the finding implies a
  spec change, amend the spec in the same PR (the canonical
  "drift between spec and code is a bug" pattern).
- **The how-to's `$ cp -R <repo-root>/examples/conflict-pending`
  line is awkward for a pip-installed reader.** Mitigation: the
  prose alongside step 1 documents both invocations (clone vs.
  pip-install), and the CI gate substitutes the repo-root path
  it knows. A future RFC may add `wiki info examples-path` if
  this proves common.

## Out of scope

- Tutorial 3 (family walkthrough) — pinned to the next milestone;
  the spec's §Non-goals lists it.
- Migration of legacy `docs/guides/*.md` into Diátaxis buckets —
  separate follow-up PR; the bucket READMEs already list
  candidates.
- A `wiki dev replay-conflict` CLI verb — possible future RFC if
  the committed-vault approach proves unergonomic.
- A `wiki info examples-path` CLI verb — possible future RFC; not
  needed for AC5 because the gate uses the repo-root path
  directly.
- Implementing the stub commands (`wiki journal tail`, etc.) —
  separate tasks under RFC-0001's roadmap.
- Eval-grade coverage of Claude-driven steps — `tests/evals/`
  (Task 20), not this PR.
