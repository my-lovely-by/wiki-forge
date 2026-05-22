# Plan: eval harness

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** `docs/specs/task-20-eval-harness/spec.md`
- **Owner:** Task 20 implementer

## Approach

Build the harness inside-out: shared helpers first
(`tests/evalkit/`), then one passing scenario per family, then the
authoring guide, then CI wiring. The order matters because every
scenario uses the same `run_claude` + `assert_*` helpers — landing
those first means each family-specific scenario is the prompt + the
allowed-tools list + the assertion, not 200 lines of plumbing. A
reviewer can read a family's `test_*.py` and see the whole story
without paging through harness internals.

Three structural calls worth flagging up front:

- **`evalkit` lives under `tests/`, not `llm_wiki_kit/`.** AGENTS.md
  and `pyproject.toml`'s `[tool.mypy]` already extend strict typing
  to `tests/` (`files = ["llm_wiki_kit", "tests"]`); same for ruff
  (`ruff check llm_wiki_kit tests`). Putting helpers under `tests/`
  gets the typing for free, eliminates any wheel-exclusion
  ceremony, and removes the risk that a future contributor adds a
  runtime `from llm_wiki_kit.evalkit import …` and ships a broken
  wheel.
- **One seed vault per family**, built once per session via a
  `tmp_path_factory` fixture. The seeds aren't checked in as
  pre-baked vaults — they are *built* by the fixture (running
  `wiki init` + `wiki add` against a tmp tree, plus, for the
  `conflict-pending/` family, a real `safe_write` drift replay
  that lands a genuine `PageProposalEvent` in the journal). The
  committed `tests/fixtures/eval-vaults/<family>/` directory only
  holds a `README.md` per family and any minimal user-content
  page the fixture replays *as* the user-edited side of the drift.
- **The `claude` subprocess gets a pinned, opinionated argv.** See
  Step 1 for the full list — `--permission-mode acceptEdits`,
  `--output-format stream-json --verbose`,
  `--no-session-persistence`, `--max-budget-usd`, cwd=vault,
  isolated `$HOME`. Each flag earns its place by closing a known
  failure mode the adversarial review surfaced; none is
  decorative.

## Pre-conditions

- Task 17 (`wiki run`) and Task 18 (`wiki research` + Perplexity
  provider) are merged on `main`. Task 19 (Gemini + Semantic Scholar)
  is in flight; the `research/` eval scenarios target the
  *dispatch contract*, not specific providers, so they survive
  Task 19's additions.
- `core/files/skills/wiki-conflict/SKILL.md` is shipped (it is, see
  the existing file).
- A maintainer running this plan locally has `claude` (Claude Code
  CLI) on `$PATH` and `ANTHROPIC_API_KEY` exported in the shell.
  Without those the green-but-all-SKIPPED path applies — fine for
  CI, useless for local development.

## Steps

Each step is one verifiable goal. Construction tests precede
implementation (TDD where the unit is pure; goal-based where the
verification is a one-liner against an artifact).

### 1. `evalkit` helpers exist, compile, type-check; argv shape is pinned

   - **What changes**
     - New package `tests/evalkit/__init__.py` exporting:
       `EvalkitClaudeRunner`, `ClaudeRunResult`, `run_claude`,
       `read_journal_events`, `assert_skill_loaded`,
       `assert_journal_has`, and the module constants
       `EVAL_MODEL` (default alias `"sonnet"`),
       `EVAL_MAX_BUDGET_USD` (default `0.25`),
       `EVAL_DEFAULT_TIMEOUT_S` (default `180.0`).
     - Module-private helpers: `_resolve_claude_bin`,
       `_parse_stream_json`, `_skip_if_no_claude`,
       `_skip_if_env_unset`, `_isolated_home`.
     - `EvalkitClaudeRunner._argv(...)` returns the canonical
       `claude` argv:
       ```
       [bin, "--print",
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "acceptEdits",
        "--no-session-persistence",
        "--allowed-tools", ",".join(allowed_tools),
        "--max-budget-usd", f"{budget_usd:.2f}"]
       ```
       The prompt is passed via stdin (closed after one line),
       *not* as an arg — keeps very long prompts off the argv and
       avoids shell-quoting issues.
     - The runner sets `cwd=vault`, and constructs a minimal
       subprocess `env` that carries only `PATH` and
       `ANTHROPIC_API_KEY` plus the overridden `HOME` (a tmp dir
       under `tmp_path`). Other env vars are not forwarded — keeps
       the eval reproducible across machines.
   - **Tests (construction, all under `tests/unit/test_evalkit_*.py`)**
     - `test_evalkit_runner.py::test_run_claude_resolves_binary_from_env`
       — monkeypatches `LLM_WIKI_KIT_EVAL_CLAUDE_BIN` to a tmp shell
       script; asserts env wins, falls back to `shutil.which("claude")`
       second.
     - `…::test_run_claude_raises_skip_when_binary_missing` — the
       resolver raises a sentinel the fixtures translate into
       `pytest.skip`.
     - `…::test_run_claude_kills_on_timeout` — invokes a tmp script
       that sleeps 5 s with `timeout_s=0.5`; asserts the harness
       kills the subprocess and the result carries a clear timeout
       error.
     - `…::test_argv_pins_permission_mode_accept_edits` — asserts
       the assembled argv contains `--permission-mode acceptEdits`.
     - `…::test_argv_pins_output_format_stream_json` — asserts the
       argv contains `--output-format stream-json --verbose` (and
       does NOT contain `--include-partial-messages`).
     - `…::test_argv_pins_no_session_persistence` — asserts
       `--no-session-persistence` is present.
     - `…::test_argv_does_not_pass_bare_flag` — asserts `--bare`
       is absent (it would skip CLAUDE.md auto-discovery, breaking
       the trigger eval's whole premise).
     - `…::test_argv_pins_budget_cap` — asserts
       `--max-budget-usd` is present with a numeric value.
     - `…::test_argv_uses_allowed_tools_space_pattern` — passes
       `["Bash(wiki resolve *)"]` and asserts the comma-joined
       string preserves the space-and-glob pattern (no
       `Bash(wiki:*)`).
     - `…::test_run_claude_isolates_home` — asserts the subprocess
       env's `HOME` is a tmp dir under `tmp_path` and is not
       `os.path.expanduser("~")`.
     - `…::test_run_claude_sets_cwd_to_vault` — asserts the
       subprocess's `cwd` kwarg is the vault root.
     - `…::test_parse_stream_json_finds_skill_tool_use` — feeds a
       three-line stream-json fixture where one line is a
       `{"type":"tool_use","name":"Skill","input":{"skill":"wiki-conflict"}}`
       block; asserts `assert_skill_loaded(result, "wiki-conflict")`
       passes and `assert_skill_loaded(result, "wrong")` fails with
       a clear message.
     - `…::test_assert_journal_has_filters_by_kind_and_field` — pure
       unit test against an in-memory event list.
     - `…::test_run_claude_passes_pinned_flags_to_subprocess` —
       monkeypatches `subprocess.run` with a capturing fake that
       records the argv + cwd + env it was called with; invokes
       `run_claude(...)` against a `tmp_path` vault; asserts the
       captured argv contains `--max-budget-usd`, the captured
       env has `HOME` set to a `tmp_path` subdir, and the
       captured `cwd` is the vault. Closes AC11 + AC13 at the
       *invocation site*, not just the internal `_argv` helper —
       a future code path that bypasses `_argv` still gets caught.
   - **Done when** every test above passes locally; `ruff check
     llm_wiki_kit tests`, `ruff format --check llm_wiki_kit tests`,
     and `mypy llm_wiki_kit tests` are clean.

### 2. The `eval` marker is registered and the fast suite stays fast

   - **What changes**
     - `pyproject.toml`'s `[tool.pytest.ini_options].markers` gains
       `"eval: marks tests under tests/evals/ that drive Claude
       Code via subprocess (run via the evals CI workflow or
       pytest tests/evals -m eval)"`.
     - `pyproject.toml`'s `[tool.pytest.ini_options].addopts` flips
       to `"-ra --strict-markers -m 'not slow and not eval'"` so a
       bare `pytest` invocation collects unit + integration only.
       The two CI marker flips (slow, eval) override `-m` at the
       command line.
     - **Foot-gun mitigation.** Path-based pytest selection still
       inherits `-m` from addopts, so
       `pytest tests/evals/x.py -k something` reports
       `"0 tests collected, N deselected"`. Mitigations:
       - Every eval file's top-of-file carries
         `pytestmark = pytest.mark.eval` (in addition to the
         per-function `@pytest.mark.eval` for readability) so a
         developer running an explicit selection sees the marker
         is the gate.
       - The authoring guide (Step 7) documents
         `pytest tests/evals/x.py -m eval -k name` as the
         debugging incantation.
       - A comment in `pyproject.toml` next to the addopts line
         names the trade-off in one sentence.
     - `tests/evals/__init__.py` + `tests/evals/conftest.py` (which
       defines the CWD guard from Spec §Invariants and the
       per-family seed-vault factories that Step 4 fleshes out).
   - **Tests (construction)**
     - `tests/unit/test_evalkit_marker.py::test_eval_marker_registered`
       reads `pyproject.toml` and asserts the marker exists.
     - `…::test_bare_pytest_skips_eval_dir` — runs `pytest
       --collect-only -q tests/evals/` as a subprocess in the
       same interpreter and asserts the output line matches
       `r"^0 tests collected"` (exact zero, not >= 0). Survives
       the addopts default; a future PR that adds a
       non-`eval`-marked test under `tests/evals/` will fail
       this — and that is itself a smell worth surfacing.
     - `…::test_marker_select_collects_eval_dir` — runs
       `pytest --collect-only tests/evals -m eval` and asserts
       collection > 0 once Step 3's scaffolding lands. (The test
       guards against regression; Step 2 adds it as a skip until
       Step 3 makes it pass.)
     - `…::test_every_eval_file_carries_module_level_pytestmark` —
       reads each `tests/evals/**/test_*.py` and asserts a
       `pytestmark = pytest.mark.eval` line is present.
   - **Done when** the marker exists, the addopts flip is in place,
     and the bare-pytest collection test passes. The integration
     suite continues to pass under `pytest` with the new default.

### 3. The five eval directories scaffold, one scenario each (skeleton)

   - **What changes**
     - `tests/evals/trigger/test_wiki_conflict_trigger.py`
     - `tests/evals/outcome/test_weekly_digest_outcome.py`
     - `tests/evals/provenance/test_research_provenance.py`
     - `tests/evals/conflict/test_resolve_proposal.py`
     - `tests/evals/research/test_dispatch_contract.py`
     - Each test marked `@pytest.mark.eval` and `@pytest.mark.skip`
       until Step 4's fixtures and Step 5's assertions land.
   - **Tests (construction)**
     - `tests/unit/test_evalkit_layout.py::test_five_eval_family_dirs_present`
       — asserts the five family directories exist and that each
       contains at least one `test_*.py` with a module-level
       `pytestmark = pytest.mark.eval`. Counts family
       directories, not test files (the `research/` family ships
       two scenarios; the sentinel adds another; the count is
       intentionally `>= 5`, not `== 5`).
   - **Done when** `pytest --collect-only tests/evals -m eval`
     collects at least five tests (more once 5e + 5f + the
     sentinel land) and the unit layout test passes.

### 4. Per-family seed vaults build via `tmp_path_factory`

   - **What changes**
     - `tests/evals/conftest.py` gains a session-scoped factory
       fixture per family. Each fixture:
       - Copies any committed seed material from
         `tests/fixtures/eval-vaults/<family>/` into the tmp dir.
       - Runs the kit's `cli.main([...])` to `wiki init` then
         `wiki add` the primitives the family needs.
       - For the `conflict-pending/` family, *replays a real
         drift*: writes a baseline `meetings/2026-05-12-q2.md` via
         `llm_wiki_kit.write_helper.safe_write`, then has the
         fixture (NOT `safe_write`) overwrite the file with the
         "user edit" using `Path.write_text` to simulate a real
         user opening their editor and editing the page outside
         the kit's awareness, then calls `safe_write` again with
         a different proposed body — this is what the SKILL
         expects to see (a real `PageProposalEvent` in the journal
         plus the `.proposed` sidecar). The intermediate
         `Path.write_text` is a deliberate carve-out: the kit's
         "every write goes through `safe_write`" rule is about
         *kit writes*, and this fixture is simulating a user
         write. AGENTS.md's rule is preserved everywhere it
         matters; the carve-out is documented here for future
         reviewers. Hand-authored sidecars without a matching
         event are rejected by `wiki doctor` and ignored by
         `wiki-conflict` (failure mode documented at
         `core/files/skills/wiki-conflict/SKILL.md:144`).
       - Returns the tmp `Path` to the test.
     - `tests/fixtures/eval-vaults/<family>/` directories exist
       with a one-line `README.md` explaining what each seed is
       for, and (for `conflict-pending/`) one user-content page
       used as the drift's user-edited side. No `.proposed`
       sidecars are committed.
   - **Tests (construction)**
     - `tests/integration/test_eval_fixtures.py::test_each_family_seed_builds`
       — exercises each factory (not the `@pytest.mark.eval`
       tests themselves) and asserts the resulting vault passes
       `wiki doctor` with exit 0 (for non-conflict families) or
       `wiki doctor` exit 1 with `pending_proposals` listed (for
       the `conflict-pending/` family). Runs in the **integration**
       lane (not eval) because no LLM is involved.
     - `…::test_conflict_seed_has_real_proposal_event` — reads
       the journal and asserts at least one
       `PageProposalEvent` with the expected path is present
       *and* the corresponding `.proposed` sidecar exists on disk.
   - **Done when** the integration tests pass under `pytest -m 'not
     slow and not eval'`.

### 5. Six scenarios across five families (one per family, two for `research/`)

   Each scenario is its own design — six different prompts,
   allowed-tools sets, and assertion shapes. Below, one subsection
   per file. 5a–5d drive Claude Code via subprocess
   (integrated-journey evals); 5e and 5f drive the kit's CLI
   directly (contract evals). All six are the construction tests
   for this step.

   **Additional deliverable** under this step: a unit-level
   companion file `tests/unit/test_evalkit_research_registry.py`
   that asserts `gemini.PROVIDER_SLUG` and
   `semantic_scholar.PROVIDER_SLUG` are present in
   `_PROVIDER_REGISTRY` at `llm_wiki_kit/research/dispatch.py`.
   This is the provider-agnosticism check the dispatch contract
   would otherwise have to assert at the eval level (see 5e
   assertion #3 below).

   #### 5a. `trigger/test_wiki_conflict_trigger.py`

   - **Pre-condition.** Vault is built by the `conflict-pending`
     factory (Step 4): real `PageProposalEvent` in journal, real
     `.proposed` sidecar on disk.
   - **Prompt** (verbatim): `"I need to merge meetings/2026-05-12-q2.md
     with its proposed sidecar — can you walk me through it?"`
   - **Allowed tools.** `["Read"]` only. Trigger evals do not need
     write access: the assertion is on *which skill loaded*, not on
     downstream actions.
   - **Assertion.** Read the SKILL name from
     `core/files/skills/wiki-conflict/SKILL.md`'s YAML frontmatter
     at fixture-build time (so a future SKILL rename doesn't flake
     the eval), then `assert_skill_loaded(result, skill_name)`.

   #### 5b. `outcome/test_weekly_digest_outcome.py`

   - **Pre-condition.** Vault is built by the `weekly-digest`
     factory: core + `meeting` + `weekly-digest`, plus one fixture
     meeting page (copy of `templates/operations/weekly-digest/
     fixtures/sample-meeting.md`) so the digest has content to
     summarize. The digest output path (`outputs/digests/2026-W20.md`)
     does *not* exist before the test runs — asserted up front.
   - **Prompt.** `"Run wiki run weekly-digest --window=2026-W20 and
     then follow the SKILL it points you at to actually write the
     digest page."` (Two-stage: dispatch journals the
     `OperationRunEvent`; the SKILL writes the page. Without the
     second sentence, Claude can stop at dispatch and the test
     would falsely pass.)
   - **Allowed tools.** `["Read", "Write", "Edit",
     "Bash(wiki run *)"]`.
   - **Assertions (all must hold)** —
     1. Pre-assertion: `outputs/digests/2026-W20.md` did not exist
        before `run_claude`.
     2. Post-assertion: `outputs/digests/2026-W20.md` exists.
     3. The journal carries one
        `OperationRunEvent(operation="weekly-digest",
        status="dispatched")`.
     4. A `WeeklyDigestPage` Pydantic model (defined inline in the
        test, mirroring the SKILL's frontmatter schema:
        `type="digest"`, `digest_window="2026-W20"`,
        `tags: list[str]` containing `"weekly-digest"` and the
        window) validates the page's frontmatter via
        `pyyaml`-loaded YAML.
   - **The Pydantic schema lives inside this test file**, not
     exported — it is a contract assertion for the test, not for
     the kit. If the SKILL's frontmatter contract changes, this
     model changes in the same PR.

   #### 5c. `provenance/test_research_provenance.py`

   - **Pre-condition.** Vault is built by the `research-cited`
     factory: core + `meeting` + `infrastructure:research` +
     `infrastructure:research-perplexity`. The factory
     pre-populates `research/deployment.md` (the "research result"
     side of the provenance flow) with a `citations:` frontmatter
     list and a short body. The eval does *not* invoke `wiki
     research`; that would require the subprocess `claude` spawns
     to see a monkeypatched provider, which a pytest
     `monkeypatch.setattr` cannot reach across process boundaries.
     Live dispatch is exercised by 5f; 5c tests the *consume*
     side.
   - **Assertion shape note.** This is by design a narrower test
     than "round-trip a research call." It assets *propagation*:
     given a research page with citations frontmatter, does Claude
     read it and cite it in the consuming note? The
     `wiki research`-dispatch side is covered by 5e (contract) and
     5f (live).
   - **Prompt.** `"Read research/deployment.md and write
     meetings/notes-on-deployment.md summarizing the finding and
     citing the research page."`
   - **Allowed tools.** `["Read", "Write", "Edit"]`. No Bash —
     this scenario does not run `wiki research`.
   - **Assertions** —
     1. The follow-on note at `meetings/notes-on-deployment.md`
        exists.
     2. Its body contains the substring `research/deployment`
        — substring on the file path rather than a specific
        wikilink syntax (`[[…]]` vs `[…](…)`), since the
        citation-format contract isn't pinned in the
        `infrastructure:research` SKILL.md yet. When that contract
        is pinned (Task 21 or a follow-up), this assertion
        tightens to the specific form.
     3. The pre-existing `research/deployment.md` is unchanged
        (the eval reads-and-cites, it doesn't mutate the source).

   #### 5d. `conflict/test_resolve_proposal.py`

   - **Pre-condition.** Vault is built by the `conflict-pending`
     factory: real drift state with a journaled
     `PageProposalEvent` for `meetings/2026-05-12-q2.md`.
   - **Prompt.** `"There's a pending .proposed sidecar at
     meetings/2026-05-12-q2.md.proposed. Walk me through resolving
     it — pick a sensible merge and commit it through wiki resolve."`
   - **Allowed tools.** `["Read", "Edit", "Bash(wiki resolve *)",
     "Bash(wiki doctor)", "Bash(wiki journal *)"]`.
   - **Assertions** —
     1. The `.proposed` sidecar is gone after the run.
     2. The journal carries one
        `PageConflictResolvedEvent(path="meetings/2026-05-12-q2.md")`
        — verified to be present in `llm_wiki_kit/models.py` and
        appended by `llm_wiki_kit/write_helper.resolve_proposal`.
     3. The on-disk file's content hash equals the journal's
        most-recent `PageWriteEvent(path="meetings/2026-05-12-q2.md")`
        hash (the resolved write is the new baseline — what
        `wiki doctor` checks for cleanliness).

   #### 5e. `research/test_dispatch_contract.py`

   - **Pre-condition.** Vault is built by the `research-dispatch`
     factory: core + `infrastructure:research` +
     `infrastructure:research-perplexity`. The Perplexity
     provider's `dispatch` is monkeypatched the same way 5c does
     — but this scenario *does not invoke `claude`*. It runs the
     `wiki research` CLI directly via `cli.main(...)` and asserts
     on the dispatcher's contract (journal event + markdown
     shape). This is what makes the eval survive Task 19's
     additions (Gemini, Semantic Scholar): it targets the
     dispatch contract, not a specific provider's wire format.
   - **Allowed tools.** N/A — no `claude` subprocess.
   - **Assertions** —
     1. The journal carries one
        `ResearchQueryEvent(provider="perplexity", status="ok")`.
     2. Captured stdout starts with `"---\nprovider: perplexity\n"`
        (the renderer's YAML frontmatter shape, from
        `llm_wiki_kit/research/dispatch.py:_render_markdown`).
     3. (Provider-agnosticism check.) The dispatch contract is
        not pinned to Perplexity. A unit-level companion test
        under `tests/unit/test_evalkit_research_registry.py`
        asserts `gemini.PROVIDER_SLUG` and
        `semantic_scholar.PROVIDER_SLUG` are both present in
        `_PROVIDER_REGISTRY` (verifying Task 19 stays wired up).
        Adding a `--provider gemini` assertion in the eval scenario
        itself would require also installing
        `infrastructure:research-gemini` in the factory and
        monkeypatching `gemini.dispatch` — that conflates the
        dispatch-contract check with a Task-19-coupled second
        provider, and the unit test gives the same signal with
        less ceremony.
     - This eval carries `@pytest.mark.eval` for marker-routing
       reasons (it lives under `tests/evals/research/`) but does
       not need `ANTHROPIC_API_KEY`. It always runs in the evals
       CI job.
   - **Why this lives in `tests/evals/` rather than
     `tests/integration/`.** The dispatch-contract pattern is the
     anchor scenario new research-provider authors will follow
     when adding their eval — it lives next to 5f below for
     family coherence. If you find yourself adding more
     integration-shaped tests under `tests/evals/research/`,
     that's a smell.

   #### 5f. `research/test_perplexity_live.py`

   This scenario is what AC12 ("upstream provider errors surface,
   not xfail") and the §Invariants live-provider 5xx clause
   actually exercise. Without it, both ACs are aspirational.

   - **Pre-condition.** Same `research-dispatch` factory as 5e,
     but the Perplexity provider's `dispatch` is **not**
     monkeypatched.
   - **Skip rule (the AC12 case).** If `PERPLEXITY_API_KEY` is
     unset, `pytest.skip("PERPLEXITY_API_KEY unset; live eval
     skipped")`. If the provider returns a `ResearchHTTPError`
     (5xx, rate-limit, network error), catch it and call
     `pytest.skip(f"perplexity upstream error: HTTP {status} —
     {body[:200]}")`. Never `xfail`.
   - **Allowed tools.** N/A — no `claude` subprocess.
   - **Assertions (only run if the live call succeeded)** —
     1. The journal carries one
        `ResearchQueryEvent(provider="perplexity", status="ok")`
        with a real model name (not the test fixture's).
     2. The rendered stdout includes at least one URL in the
        `citations:` frontmatter list (real Perplexity responses
        contain at least one citation; if a real query returns
        zero, the assertion failure surfaces a real change in
        provider behavior worth the maintainer's attention).
   - **Cost.** Single live Perplexity call per CI run when the
     key is set. Budget-bound by the spec's
     `EVAL_MAX_BUDGET_USD` does NOT apply (no `claude`
     subprocess) — Perplexity's own billing is the cap. Document
     this in the test docstring so a future maintainer doesn't
     mistakenly think the eval is budgeted.

   - **Done when** all six tests pass on a developer machine with
     `ANTHROPIC_API_KEY` and `PERPLEXITY_API_KEY` set (5a–5d need
     `ANTHROPIC_API_KEY`; 5e needs neither key; 5f needs
     `PERPLEXITY_API_KEY`). On a machine with no env vars and no
     `claude`, 5a–5d report SKIPPED (no `ANTHROPIC_API_KEY` / no
     `claude`), 5e passes, and 5f reports SKIPPED
     (`PERPLEXITY_API_KEY unset`). On a CI runner with
     `ANTHROPIC_API_KEY` but no `PERPLEXITY_API_KEY`, 5a–5d and
     5e pass; 5f reports SKIPPED.

### 6. `ci.yml` excludes evals and the new `evals.yml` workflow runs them

   - **What changes**
     - `.github/workflows/ci.yml`: the existing `pytest -m 'not
       slow'` step becomes `pytest -m 'not slow and not eval'`. No
       other change.
     - New `.github/workflows/evals.yml`:
       - Triggers on `push` to `main` and `pull_request` only.
         No nightly cron — every run spends Anthropic budget, and
         PR + main-merge cadence is the right pressure for now.
         If upstream regressions caught post-merge become a
         problem, a follow-up PR adds the cron then.
       - Runs `pytest tests/evals -m eval -ra` plus
         `--junitxml=test-results.xml` and uploads the junit
         artifact (so skipped-with-reason lines for
         provider-error skips are inspectable in the run UI).
       - `ANTHROPIC_API_KEY` sourced from a repo secret of the same
         name; `PERPLEXITY_API_KEY` and `GEMINI_API_KEY` sourced
         from secrets when present (env block uses `${{ secrets.X
         || '' }}` so missing-secret runs cleanly skip).
       - Installs `claude` via `npm i -g @anthropic-ai/claude-code@<pinned>`
         in a setup step. Pin to a specific version; bump in its
         own PR when needed.
       - `timeout-minutes: 30`.
       - `concurrency:` group cancels in-progress runs on
         force-push so a PR doesn't burn budget on stale commits.
   - **Tests (construction)**
     - `tests/unit/test_evalkit_ci.py::test_ci_yml_excludes_eval_marker`
       — reads `.github/workflows/ci.yml` and asserts the pytest
       command contains `not eval`.
     - `…::test_evals_yml_runs_eval_marker` — reads `evals.yml`
       and asserts the pytest command contains `tests/evals -m eval`.
     - `…::test_evals_yml_pins_claude_version` — asserts the
       npm install line names a version (any version), not a
       moving target like `@latest`.
     - `…::test_evals_yml_uploads_junit_artifact` — asserts the
       workflow contains an `upload-artifact` step naming the
       junit xml file (so AC12's surfaced skip reasons survive).
   - **Done when** the unit tests pass; a `gh workflow run evals.yml`
     on the PR branch (or a `pull_request` event from the PR) goes
     green with the Anthropic key set on the repo. The fast `ci.yml`
     job remains green (no claim about absolute duration — pytest
     does not promise stable wall-clock and the regression bound is
     not load-bearing).

### 7. Authoring guide

   - **What changes**
     - New file `docs/guides/explanation/evals.md`. Sections:
       *"What is an eval?"* (one paragraph + the five-family
       table), *"How to add a sixth eval"* (a worked example
       adding a hypothetical `trigger/test_wiki_search_trigger.py`),
       *"What not to put in an eval"* (anything that should be a
       unit or integration test, anything that mocks `claude`),
       *"How `evalkit` fits in"* (link to spec).
     - `docs/guides/explanation/README.md` index updates to list
       the new entry.
     - `tests/evals/README.md` — a five-line pointer to the spec
       and the authoring guide.
   - **Tests (construction)**
     - `tests/unit/test_evalkit_docs.py::test_authoring_guide_exists_and_references_spec`
       reads `evals.md` and asserts the spec link and the marker
       name both appear verbatim.
     - `…::test_explanation_index_links_to_evals` — reads the
       index and asserts it contains the new entry.
   - **Done when** the unit tests pass and a maintainer reading
     the guide cold (no prior context) can describe the addition
     workflow back in two minutes.

### 8. Self-check sentinel, grep guards, and the cross-cutting AC bundle

   This step bundles the small cross-cutting tests that close
   AC5, AC9, AC12, and AC14. It exists as a separate step so the
   gates are visible end-to-end rather than smeared across the
   step that happened to need them.

   - **What changes**
     - `tests/evals/test_self_check.py` — one always-passing test
       (`def test_eval_harness_self_check() -> None: assert True`)
       carrying `pytestmark = pytest.mark.eval`. Requires no env
       vars, no `claude`. A fully-skipped CI run still reports
       this as PASSED so a maintainer can distinguish "harness
       intact, nothing exercised" from "harness silently broken,
       no tests collected".
     - `tests/unit/test_evalkit_layout.py::test_runtime_does_not_import_evalkit`
       — greps `llm_wiki_kit/**/*.py` for any line matching
       `from tests\.` or `import evalkit` and asserts no matches.
     - `…::test_evals_dir_has_no_xfail` — greps
       `tests/evals/**/*.py` for `pytest.xfail` and asserts no
       matches (closes AC12's "no xfail" rule mechanically).
     - `EvalkitClaudeRunner.__post_init__` (Step 1) asserts the
       supplied vault path resolves under `tempfile.gettempdir()`
       and raises `ValueError` otherwise — this is the
       Spec §Invariants "subprocess cwd under tmp_path" enforcement
       point.
     - `tests/unit/test_evalkit_runner.py::test_runner_rejects_non_tmp_vault`
       — instantiates the runner with `vault=Path(__file__)` (a
       repo path) and asserts it raises before ever calling
       `subprocess.run`.
   - **Tests (construction)** — the four unit tests above plus the
     sentinel are themselves the construction tests for this step.
   - **Done when** they all pass under `pytest -m 'not slow and not
     eval'` (for the grep + runner guards) and
     `pytest tests/evals -m eval` (for the sentinel), and AC5 / AC9
     / AC12 / AC14 are mechanically enforced.

## Verification gate

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow and not eval'      # fast suite — must stay green
pytest tests/evals -m eval             # eval suite — must stay green with ANTHROPIC_API_KEY set
pytest --collect-only tests/evals -m eval  # >= 5 tests (six scenarios + sentinel)
```

Acceptance criteria AC1–AC14 from `spec.md` translate to:

- AC1 → Step 5 (the six scenarios pass — 5a–5d via live `claude`,
  5e via CLI dispatch contract, 5f via live Perplexity HTTP) +
  Step 6 (in CI).
- AC2 → Step 2 (marker registration + addopts) + Step 3 (scaffolds
  carry the marker + module-level `pytestmark`).
- AC3 → Step 6 (`ci.yml` flip).
- AC4 → Step 6 (`evals.yml` creation).
- AC5 → Step 1's skip helpers + Step 5's `pytest.skip` calls +
  Step 8's sentinel self-check (AC14).
- AC6 → Step 4's `tmp_path_factory` fixtures + Step 2's CWD guard.
- AC7 → Step 7.
- AC8 → mechanical gates run at the close of every step.
- AC9 → `evalkit` lives under `tests/`, never imported from
  runtime; Step 8 adds the unit-test grep guard.
- AC10 → no `[project].dependencies` edit; only the dev/test deps
  (none anticipated — `pytest-timeout` would be the only defensible
  add, and the plan does not add it because we implement the timeout
  in `subprocess.run` directly).
- AC11 → Step 1's `EVAL_MAX_BUDGET_USD` constant + argv pinning +
  `test_argv_pins_budget_cap`.
- AC12 → Step 5e's research scenario uses `pytest.skip` carrying
  HTTP status + body excerpt; Step 8's grep guard fails the build
  if any `xfail(strict=False)` lands in `tests/evals/`.
- AC13 → Step 1's `_isolated_home` helper +
  `test_run_claude_isolates_home` (unit-level) +
  `test_run_claude_passes_pinned_flags_to_subprocess` (the
  load-bearing one — captures `subprocess.run` kwargs at the
  invocation site, so a future code path that bypasses `_argv`
  still gets caught).
- AC14 → Step 8's sentinel test.

## Risks

- **`claude` CLI surface drift.** Anthropic ships changes to the
  `--print` / `--allowed-tools` / `--add-dir` flags. Mitigation:
  all flag construction is in one function (`evalkit/__init__.py
  :EvalkitClaudeRunner._argv`); a flag rename is a one-line fix.
  The pinned npm version in `evals.yml` is the second safety.
- **`claude` non-determinism** — a model rewording a citation
  string can flake the provenance eval. Mitigation: each scenario
  asserts on shape (Pydantic-validated structure, journal-event
  presence) not on prose. Where we must assert on prose (e.g.
  citation wikilink to a specific page name), the test uses a
  substring match, not an equality. SKILL names are read from the
  SKILL.md frontmatter at fixture-build time, never hardcoded in
  the assertion string, so a SKILL rename doesn't silently flake.
- **API cost on every push.** The Anthropic key on `pull_request`
  fires four evals per PR (the research eval is monkeypatched).
  Mitigation: the `evals.yml` `concurrency:` block cancels
  in-progress runs on force-push; sonnet is the cheapest viable
  model. If cost climbs, we add a `paths-ignore:` filter to skip
  docs-only PRs — but not in this task.
- **Stream-json format change in `claude`.** If Anthropic changes
  the per-line shape of `--output-format stream-json`,
  `_parse_stream_json` breaks. Mitigation: the parser is one
  function with its own unit test and a fixture event stream;
  fixing it is local. The pinned npm version in `evals.yml` is
  the second safety — version bumps land in their own PR.
- **Fixture vault staleness.** A primitive contract change in a
  later task could break a fixture seed. Mitigation: Step 4's
  `test_each_family_seed_builds` integration test runs on every
  PR (fast lane) and catches the drift before it reaches the
  eval job.

## Out of scope

- Cassette / replay support — punted to a follow-up RFC if flake
  rate proves intolerable.
- Multi-trial pass-rate scoring — Tier-2 follow-up.
- A `wiki eval` CLI verb — Spec §Non-goals.
- An eval that exercises Obsidian's renderer — orthogonal,
  belongs in the wheel-bundled-assets spec.
- Tasks 21 (example vaults / tutorials) and 22 (README / ROADMAP
  / v2.0.0 tag) — explicit non-preview per the prompt.
