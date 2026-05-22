# Spec: eval harness

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** Task 20 implementer
- **Touches:** `tests/evals/`, `tests/evalkit/` (test-only helpers),
  `tests/fixtures/eval-vaults/`, `.github/workflows/evals.yml`,
  `docs/guides/explanation/evals.md`
- **Related:** RFC-0001 §"Task 20 — Eval harness",
  `docs/specs/task-20-eval-harness/plan.md`
- **Constrained by:** ADR-0002 (journal as state truth — every eval
  asserts on journal events, not log scraping), ADR-0004 (drift
  detection — `conflict/` evals exercise `safe_write`'s proposal
  flow), ADR-0005 (Pydantic for disk-bound schemas — `outcome/` evals
  validate produced pages with Pydantic models defined in the eval),
  AGENTS.md "Runtime dependencies" (the harness adds no runtime deps;
  dev-only).

## What this is

The `tests/evals/` test suite. The harness covers two flavors of
eval, both asserting on the vault's resulting on-disk state and
journal — the same surface a real user would inspect:

- **Integrated-journey evals.** A pytest test drives the user's
  Claude Code CLI (`claude`) as a subprocess against a fixture
  vault, then asserts on what the SKILL produced. Most evals are
  this shape; the harness's heavy lifting (subprocess argv,
  `$HOME` isolation, stream-json parsing, budget cap) exists to
  make these reliable.
- **Contract evals.** A pytest test drives the kit's CLI directly
  (`cli.main(...)`) against a fixture vault and asserts on the
  dispatcher's contract (journal event shape, rendered markdown
  prefix, registry state) without invoking `claude`. These live
  next to the integrated-journey scenarios in the same family so
  provider/operation authors find them when adding their own
  scenarios. They carry `@pytest.mark.eval` for routing.

The harness is *not* a benchmark, *not* a regression suite for the
kit's pure Python modules (those live in `tests/unit/` and
`tests/integration/`), and *not* a wrapper around the Anthropic API.

Five eval families ship in this task; the `research/` family ships
two scenarios (one contract, one live), the other four ship one each:

| Family | What it asks |
|---|---|
| `tests/evals/trigger/` | Given a user prompt, does Claude Code load the *right* SKILL? |
| `tests/evals/outcome/` | Does an operation produce the contracted output structure (Pydantic-validated)? |
| `tests/evals/provenance/` | Are citations attached to the notes the research feeds? |
| `tests/evals/conflict/` | Does `wiki-conflict` correctly drive a three-way merge through `wiki resolve`? |
| `tests/evals/research/` | Does `wiki research` dispatch to the configured provider and journal `ResearchQueryEvent`? |

## Inputs

### From the developer (pytest invocation)

```
pytest tests/evals -m eval
```

Environment variables the harness reads:

| Var | Required for | Behaviour when missing |
|---|---|---|
| `ANTHROPIC_API_KEY` | every eval that invokes `claude` (trigger, outcome, provenance, conflict) | tests skip with `pytest.skip("ANTHROPIC_API_KEY unset; live eval skipped")` — they do not fail |
| `PERPLEXITY_API_KEY` | live `research/perplexity` scenarios only | scenario skips; the dispatch-contract scenario (which monkeypatches the provider) still runs |
| `GEMINI_API_KEY` | live `research/gemini` scenarios only | scenario skips |
| `LLM_WIKI_KIT_EVAL_MODEL` | optional override | defaults to alias `sonnet` — see §Constraints "No new model name pin in tests" for the alias-vs-pinned-version trade-off |
| `LLM_WIKI_KIT_EVAL_CLAUDE_BIN` | optional override | defaults to `claude` from `PATH`; missing binary → skip with a one-line message naming the var. Verbose name is intentional: avoids clashing with developer shell aliases like `CLAUDE` or `CLAUDE_BIN`. |
| `LLM_WIKI_KIT_EVAL_MAX_BUDGET_USD` | optional override | per-invocation budget cap passed to `claude --max-budget-usd`. Defaults to `0.25` — small enough that one runaway eval costs under a dollar, large enough that a typical scenario completes. |

The harness does not read `OPENAI_API_KEY` or any other provider's key.
Adding a provider that needs new env vars amends this spec.

### From the fixture vaults

Per-eval-family fixture seeds live under `tests/fixtures/eval-vaults/`:

```
tests/fixtures/eval-vaults/
├── minimal/              # trigger evals (core only + minimal recipe)
├── weekly-digest/        # outcome evals (core + meeting + weekly-digest)
├── research-cited/       # provenance evals (core + meeting + research)
├── conflict-pending/     # conflict evals (sidecar built via drift replay at fixture time)
└── research-dispatch/    # research evals (core + research + research-perplexity)
```

Each fixture-vault directory is a *seed*, never the live test target.
Tests copy the seed into `tmp_path` at session start (see §Invariants).
The `conflict-pending/` seed is *not* a pre-baked vault with a hand-
authored `.proposed` sidecar; it is built at fixture-construction
time by replaying a real drift (an in-process `safe_write` against an
edited-on-disk page) so the journal carries a genuine
`PageProposalEvent` — the SKILL's documented failure-mode
("sidecar without a `page.proposal` event. Unexpected. Investigate")
would otherwise mean `wiki-conflict` refuses to resolve the seed.

## Outputs

Each eval test asserts and returns nothing — the pytest assertion is
the output. The harness exposes one shared helper module,
`tests/evalkit/__init__.py`, that defines:

| Symbol | Purpose |
|---|---|
| `EvalkitClaudeRunner` | dataclass wrapping `subprocess.run` of `claude`; pins model, timeout, allowed tools, permission mode, budget cap, output format, working dir, isolated `$HOME` |
| `run_claude(prompt: str, vault: Path, *, allowed_tools: list[str], timeout_s: float) -> ClaudeRunResult` | one-shot invocation; returns stdout, stderr, exit code, parsed stream-json events, duration |
| `ClaudeRunResult` | `model`, `stdout`, `stderr`, `returncode`, `events` (list of dicts parsed from stream-json lines), `duration_s` |
| `read_journal_events(vault: Path) -> list[Event]` | re-uses `llm_wiki_kit.journal.read_events`; just a convenience import |
| `assert_skill_loaded(result: ClaudeRunResult, skill_name: str)` | scans `result.events` for a `Skill` tool-use event naming `skill_name`; fails with the list of skills the run actually loaded and the first 1000 chars of stdout when none match |
| `assert_journal_has(events, *, kind, **filters)` | filters the journal by event kind and field values; fails with the offending event list when no match |
| `EVAL_MODEL`, `EVAL_MAX_BUDGET_USD`, `EVAL_DEFAULT_TIMEOUT_S` | module-level constants reading the env-var overrides; one place to bump defaults |

`evalkit` lives under `tests/` (covered by the existing
`mypy llm_wiki_kit tests` + `ruff check llm_wiki_kit tests` gates per
AGENTS.md). Nothing in the runtime package imports it; placing it
outside `llm_wiki_kit/` removes any wheel-exclusion ceremony and the
risk that a future contributor adds a runtime import to a test-only
helper.

## Behavior

### Happy path — one eval scenario, end to end

1. **Pytest collects** an eval test (e.g.
   `tests/evals/outcome/test_weekly_digest.py::test_produces_expected_digest`).
2. **Marker gate** — the test carries `@pytest.mark.eval`. The CI job
   runs `pytest tests/evals -m eval`; the regular `pytest -m 'not
   slow and not eval'` invocation in `ci.yml` skips collection.
3. **Skip checks** — fixtures verify `claude` is on `$PATH` (or
   `LLM_WIKI_KIT_EVAL_CLAUDE_BIN` resolves) and the required env vars
   are set. Missing prerequisites yield `pytest.skip(...)`; they do
   not fail.
4. **Seed copy** — the fixture copies its seed vault into `tmp_path`
   (or a `tmp_path_factory`-managed dir shared across the module
   when the seed is expensive to construct). The kit-side primitives
   the vault needs are installed via `cli.main([...])` calls inside
   the fixture, so the journal carries real `PrimitiveInstallEvent`s
   — no hand-edited `.wiki.journal/`.
5. **Prompt** — the test invokes
   `run_claude(prompt, vault, allowed_tools=[...], timeout_s=...)`.
   `claude` runs against the vault with these flags pinned by
   `EvalkitClaudeRunner._argv`:
   - `--print` (non-interactive; the prompt is delivered via stdin
     rather than as a positional arg, so long prompts don't fight
     shell quoting)
   - `--model <model>` (default alias `sonnet`)
   - `--output-format stream-json --verbose` (gives the harness one
     JSON object per line on stdout including `tool_use` blocks the
     `assert_skill_loaded` helper scans; `--verbose` is required by
     `claude --print` when stream-json is selected). The harness
     does *not* pass `--include-partial-messages` — partial chunks
     add noise without helping the skill-load assertion.
   - `--permission-mode acceptEdits` (non-interactive mode cannot
     answer permission prompts; without this, every scenario that
     calls a non-readonly tool hangs or fails before any skill
     loads)
   - `--no-session-persistence` (each invocation is fresh, nothing
     written under `$HOME` between evals — belt-and-suspenders
     alongside the HOME isolation below)
   - `--allowed-tools <list>` scoped to what the scenario needs.
     Patterns follow Anthropic's documented shape (`Read`, `Write`,
     `Edit`, `Bash(wiki *)`, `Bash(wiki resolve *)`, etc.) — space
     between command and glob, never `Bash(wiki:*)`.
   - `--max-budget-usd <cap>` (defaults to `EVAL_MAX_BUDGET_USD`)
     so a runaway loop costs at most that many cents.

   All four pinned subprocess flags (`--max-budget-usd`,
   `--no-session-persistence`, `--output-format`, `--verbose` for
   stream-json) require `--print` per `claude --help`. Removing
   `--print` from the argv silently invalidates the harness's
   safety contract; the construction tests in
   `tests/unit/test_evalkit_runner.py` pin `--print` as the first
   flag.

   The harness deliberately does **not** pass `--bare`. The trigger
   eval's whole point is exercising Claude Code's automatic SKILL
   discovery from the vault's `AGENTS.md` — `--bare` skips
   `CLAUDE.md` auto-discovery and breaks exactly the path under
   test. Isolation comes from `cwd=vault` + isolated `$HOME`, not
   `--bare`.

   The subprocess's `cwd` is set to the vault root (so `Read`/`Write`
   default-allow paths under the vault and `--add-dir` is
   unnecessary). `$HOME` is overridden to a per-invocation tmp dir
   for two independent reasons that justify keeping both
   `--no-session-persistence` and the HOME override:
   1. **`--no-session-persistence`** disables `claude`'s
      transcript-write path — nothing new lands under
      `~/.claude/projects/`.
   2. **HOME isolation** additionally prevents the subprocess from
      *reading* the developer's `~/.claude/settings.json`,
      `~/.claude/agents/`, plugin dir, and (on macOS) the keychain.
      Without it, a developer with locally-configured agents or
      MCP servers could see different eval results from a CI
      runner, and the kit's own agent-ready-repo session hooks
      would silently inject context into the eval. The
      `--no-session-persistence` flag does not block these
      reads; only HOME isolation does.

   The subprocess `env` carries `ANTHROPIC_API_KEY`, `PATH`, and
   the per-invocation `HOME` override only — other env vars are
   not forwarded. The harness deliberately does *not* pass
   `--exclude-dynamic-system-prompt-sections`: real users running
   `claude` against their vault do not pass it either, so
   omitting it keeps the eval's cwd/env/system-prompt shape close
   to the path under test.
6. **Assertions** — the test reads the journal via
   `read_journal_events(vault)` and the vault tree via stdlib
   `Path.iterdir`/`Path.read_text`, then asserts:
   - the expected skill was loaded (`trigger/` family);
   - the expected pages exist with the contracted shape, validated
     by a Pydantic model defined in the test
     (`outcome/`, `provenance/`);
   - the expected journal events were appended in the expected order
     (`conflict/`, `research/`).

### Edge cases

- **`claude` binary missing.** Skip, do not fail. Single source of
  truth for the binary path is the `LLM_WIKI_KIT_EVAL_CLAUDE_BIN`
  env var; falling back to `shutil.which("claude")`.
- **`claude` exits non-zero.** The harness surfaces stdout + stderr
  in the pytest failure body so a maintainer can see why. The
  test fails — exit-code-≠-0 is a real eval failure, not a skip.
- **`claude` exceeds timeout.** The harness kills the subprocess and
  the test fails with `"claude exceeded N s timeout"`. Default
  per-scenario timeout: `EVAL_DEFAULT_TIMEOUT_S` (180 s), exported
  from `tests/evalkit/__init__.py` and overrideable per-test via
  the `eval_timeout` marker argument. The default is large enough
  to cover a sonnet-paced multi-step scenario; one-call scenarios
  finish well under it.
- **Live API rate-limit / 5xx in `research/` evals.** The eval that
  hits the live provider catches `ResearchHTTPError` and calls
  `pytest.skip(...)` with a reason string including the HTTP status
  and the first 200 chars of the response body — never `xfail`,
  which surfaces as a yellow line maintainers learn to ignore. The
  skip reason is preserved in the CI run's pytest report and (when
  `evals.yml` is configured to upload one) in a junit XML artifact.
  The dispatch-contract scenario (monkeypatched provider) keeps
  running so the dispatch surface stays under test even when the
  live provider is unreachable.
- **Fixture vault drift.** Each seed-vault is replayed through
  `wiki doctor` at session-fixture build time; a non-zero exit
  aborts the eval run with a clear "fixture is stale" message —
  rebuild the seed via the script the plan ships.

### Error cases

- **Missing fixture seed directory.** The session-level fixture
  raises `FileNotFoundError` with the path it expected. Not a skip;
  the developer committed a broken eval.
- **Pydantic-model mismatch (outcome eval).** The test fails with
  Pydantic's structured error, plus the offending file path. The
  raw file content is *not* dumped to stdout (a digest body can be
  thousands of tokens of meeting summaries); the test logs the
  first 400 chars and the file path.
- **`assert_skill_loaded` cannot find the expected skill in the
  stream-json event list.** Fails with "expected skill `<name>` to
  be loaded; found loaded skills: [...]". When the event list is
  empty (claude exited before invoking any tool), the test prints
  the first 1000 chars of stdout to aid debugging. The
  `<name>` argument to `assert_skill_loaded` is *not* a string
  literal in the test — it is read at fixture-build time from the
  target SKILL's frontmatter `name:` field so renaming the SKILL
  doesn't silently flake the eval.

## Invariants

- **Evals never mutate the repo working tree.** Every `claude`
  subprocess invocation's `cwd` resolves under `tmp_path`, never
  under `<repo_root>/core/`, `<repo_root>/templates/`, or
  `<repo_root>/recipes/`. Enforced two ways: (a) every fixture
  produces a vault under `tmp_path` (or `tmp_path_factory`) and
  passes that as `cwd` to `run_claude`; (b) the runner asserts in
  its construction step that the supplied vault path resolves
  under the system tmp dir (`tempfile.gettempdir()`), raising
  immediately if a fixture mistakenly hands it a repo path.
- **The kit's runtime imports do not depend on `evalkit`.** A unit
  test greps `llm_wiki_kit/**/*.py` for any `from tests.` or
  `import evalkit` (the helpers live under `tests/`, so a runtime
  import would be both a wheel-shape bug and an architectural
  smell).
- **`pytest -m 'not slow and not eval'` collects zero `tests/evals/`
  tests.** The existing fast CI job stays fast. Each eval file
  carries a top-level `pytestmark = pytest.mark.eval` so a developer
  running an explicit path selection (`pytest tests/evals/x.py -k y`)
  sees the marker and learns the override pattern (`-m eval`).
- **Every eval names the skill or event it asserts on.** Assertions
  are on observable artifacts (a SKILL named in the stream-json
  event list, a journal event of a known kind), never on internal
  call counts inside the kit. SKILL names are read from frontmatter
  at fixture-build time, never hard-coded in the test body.
- **Live-provider scenarios in `research/` skip cleanly when the
  provider's env var is unset *or* when the provider returns a 5xx.**
  They do not fail; the skip reason carries the HTTP status and
  body excerpt so a maintainer reading the CI run can see why.
- **One model for every eval invocation in a single run.** The model
  pin is read once at session start and surfaced in the run header
  so a maintainer can correlate flake rates with model choice. A
  test cannot silently change the model mid-run.
- **Each `claude` invocation runs with an isolated `$HOME`.** A
  per-invocation tmp dir is passed as `HOME` in the subprocess env;
  Anthropic's CLI caches session transcripts under `~/.claude/`
  keyed by cwd hash, and we do not want cache leakage between
  evals or contamination of the developer's real home directory.
- **Each `claude` invocation runs with a budget cap.** The cap is
  `EVAL_MAX_BUDGET_USD` (default $0.25); a runaway agent loop
  costs no more than that. Surfaced as `--max-budget-usd` in the
  argv.
- **No new top-level directory under the repo root.** New paths:
  `tests/evals/`, `tests/evalkit/`, `tests/fixtures/eval-vaults/`,
  `docs/specs/task-20-eval-harness/`,
  `docs/guides/explanation/evals.md`,
  `.github/workflows/evals.yml`.

## Contracts with other modules

- **Calls** `llm_wiki_kit.cli.main` to install primitives in the
  fixture vault (same surface integration tests use).
- **Calls** `llm_wiki_kit.journal.read_events` to inspect the
  journal — the read path, not the append path.
- **Calls** `llm_wiki_kit.research.dispatch.dispatch_query` indirectly
  via the `wiki research` CLI; live `research/` evals exercise the
  real provider, the dispatch-contract scenario monkeypatches the
  provider module the same way `tests/integration/test_wiki_research.py`
  already does (provider-author rule from Task 18's spec).
- **Calls** `claude` (a subprocess); the kit ships no LLM. The eval
  harness depends on the user's Claude Code CLI being installed and
  authenticated.
- **Does not write** to the journal directly. The kit's CLI does
  every write; assertions read.

## Acceptance criteria

Each of the following must hold; each translates to one or more
contract tests under `tests/evals/`.

- [ ] **AC1 — Five families, at least one passing scenario each,
  in CI.** The five directories
  `tests/evals/{trigger,outcome,provenance,conflict,research}/`
  each contain at least one `test_*.py` file with at least one
  `@pytest.mark.eval`-decorated scenario. On the `evals.yml`
  GitHub Actions job triggered from a primary-repo branch with
  `ANTHROPIC_API_KEY` set in repo secrets, every scenario either
  PASSES or reports SKIPPED-with-reason — and *at least one
  non-sentinel scenario PASSES*. The sentinel (AC14) cannot
  satisfy AC1 alone; the integrated-journey path must drive at
  least one real eval on every primary-repo PR. Provider-keyed
  live scenarios (5f) may report SKIPPED on PRs where the
  provider key is absent — that still counts toward AC1 as long
  as a non-sentinel non-provider-keyed scenario (5a–5d, 5e)
  PASSES.
- [ ] **AC2 — Marker hygiene.** `pytest -m 'not slow and not eval'`
  collects zero tests from `tests/evals/`. `pytest tests/evals -m eval`
  collects every eval scenario. `pyproject.toml` registers `eval`
  in `[tool.pytest.ini_options].markers` with a one-line description.
- [ ] **AC3 — `ci.yml` skips evals.** The default CI job's pytest
  invocation explicitly excludes the `eval` marker — confirmed by
  inspecting `.github/workflows/ci.yml`. The existing fast suite
  remains green on a runner with no Anthropic credentials.
- [ ] **AC4 — `evals.yml` CI workflow runs in its own job.** A
  workflow file `.github/workflows/evals.yml` exists; on `push` to
  `main` and `pull_request` from primary-repo branches it runs
  `pytest tests/evals -m eval`. No nightly cron — every run costs
  real Anthropic API budget, and PRs + main pushes are the
  cadence the project wants. The Anthropic key is sourced from a
  repo secret; provider keys are optional. The job is allowed to
  be slow (timeout-minutes ≥ 30). Fork PRs run all-SKIPPED for
  the keyed scenarios (secrets are unavailable to fork workflows)
  — that is by design; see §Non-goals.
- [ ] **AC5 — Missing credentials skip, do not fail.** Running
  `pytest tests/evals -m eval` with all env vars unset yields a
  green run with every eval reported as `SKIPPED` and a one-line
  reason naming the missing var.
- [ ] **AC6 — Fixture vaults are seeds, not targets.** Every
  `tests/evals/*/test_*.py` resolves the vault under test from a
  `tmp_path`/`tmp_path_factory` fixture, and no test mutates a
  path under the repo's `core/`, `templates/`, or `recipes/` trees
  during execution. A `conftest.py` autouse fixture asserts this.
- [ ] **AC7 — Authoring guide.** `docs/guides/explanation/evals.md`
  exists and walks a contributor through adding a sixth eval —
  picking the family, declaring the marker, structuring the
  fixture vault, naming the skill/event assertion. The guide
  references this spec.
- [ ] **AC8 — Mechanical gates pass.** `ruff check llm_wiki_kit tests`,
  `ruff format --check llm_wiki_kit tests`, `mypy llm_wiki_kit tests`,
  and `pytest -m 'not slow and not eval'` all exit zero on the PR
  branch. The new `evals.yml` job exits zero on a runner with the
  Anthropic key set.
- [ ] **AC9 — `evalkit` is test-only.** `evalkit` lives under
  `tests/` and is never imported from `llm_wiki_kit/*`. A fast unit
  test under `tests/unit/test_evalkit_layout.py` greps the runtime
  package for any reference to `evalkit` or `tests.` and fails on
  match.
- [ ] **AC10 — No new runtime dependency.** `pyproject.toml`'s
  `[project].dependencies` is unchanged. Only dev/test deps may
  grow; if any do, the plan lists each one with a one-line
  rationale.
- [ ] **AC11 — Per-invocation budget cap.** Every `claude` subprocess
  invocation passes `--max-budget-usd <cap>`; the default cap is
  the `EVAL_MAX_BUDGET_USD` constant in `tests/evalkit/__init__.py`
  (default $0.25) and is overridable via the env var of the same
  name. A unit test asserts the flag is in the assembled argv.
- [ ] **AC12 — Upstream provider errors surface, not xfail.** When a
  `research/` live eval skips because the provider returned a 5xx
  or HTTP error, the `pytest.skip` reason includes the numeric HTTP
  status and the kit's redacted error message. The response body
  itself is *not* logged — the kit's `ResearchHTTPError` design
  (`llm_wiki_kit/research/http.py:39-54`) deliberately stores only
  the status and a redacted message because echoed API keys could
  leak through body bytes. The status alone plus the kit's message
  is the diagnostic signal a maintainer needs. No
  `pytest.xfail(strict=False)` calls land in `tests/evals/`.
- [ ] **AC13 — `$HOME` isolation.** Every `claude` invocation runs
  with `HOME` set to a per-invocation tmp dir (not the developer's
  real `~/.claude/`, not a shared one across evals). A unit test
  asserts the runner overrides `HOME` and that the override path
  is under `tmp_path`.
- [ ] **AC14 — Eval-suite self-check.** A sentinel test
  `tests/evals/conftest.py::test_eval_harness_self_check` (or
  `tests/evals/test_self_check.py`) carries `@pytest.mark.eval`,
  always passes, and asserts no env vars / no `claude` binary —
  so a fully-skipped CI run still reports at least one PASSED.
  Distinguishes "all skipped, harness intact" from "all skipped,
  harness silently broken."

## Non-goals

- Live evals against paid APIs in the default CI matrix. The
  `evals.yml` job runs with the Anthropic key; provider keys are
  optional and gated by env-var presence at test time.
- Eval-quality scoring (precision/recall, rubric grading, LLM-as-
  judge). Each scenario is one assertion on observable artifacts.
  Multi-trial flake-rate metrics and rubric-based grading are
  Tier-2 follow-ups, not v0.1.
- Replay / cassette mode for deterministic re-runs. The harness is
  honest about non-determinism: a live `claude` invocation can
  fail on a sentence-rewording change. Cassettes are a follow-up
  RFC; v0.1 ships live.
- A "preview vault" eval that catches Obsidian-rendering regressions.
  That's `docs/specs/wheel-bundled-assets/spec.md`'s territory.
- Skill-discovery tests that bypass `claude` and call Anthropic's
  API directly. The whole point of this task is exercising the
  Claude-Code-CLI skill loader, not the bare-model behaviour.
- Performance evals (token usage, wall-clock). The job captures
  duration in the run header for human inspection; no assertion.
- A `wiki eval` CLI verb. Evals are a pytest concern; users do not
  run them.
- Live `claude` evals on fork PRs. GitHub Actions does not share
  repo secrets with workflows triggered by `pull_request` from a
  fork; the `evals.yml` job runs but every keyed scenario reports
  SKIPPED. That is the documented behavior — we do *not* use
  `pull_request_target` (which would run fork code with primary
  repo secrets) because the security trade-off (untrusted code
  with paid-API credentials) is unacceptable.
- **Integrity-pinned `@anthropic-ai/claude-code` install.** The
  workflow pins the version (`@2.0.27`) but does not verify a
  tarball checksum or use a lockfile. A supply-chain compromise
  of the npm package between version cuts would land inside the
  eval CI runs with `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY`,
  and `GEMINI_API_KEY` in env. This is an accepted residual risk
  for a test-CI workflow; a follow-up issue tracks moving to an
  `npm ci` lockfile install. Until then, version bumps land in
  their own PR with a deliberate review.
- **CI-side prompt-injection check on kit content.** The fixture
  vaults copy `core/files/` and `templates/` verbatim. A
  malicious commit that plants prompt-injection in
  `core/files/AGENTS.md` or a SKILL.md would execute against an
  eval runner with secrets in env. Today's defenses are the
  budget cap, the per-scenario allowed-tools allowlists, the
  isolated `$HOME`, and human review of every kit-content change.
  The redactor on stdout/stderr dumps is defense-in-depth against
  *accidental key echo* in failure messages — it doesn't filter
  adversarial output and shouldn't be relied on as a
  prompt-injection mitigation. A grep-based CI check on kit
  content for high-risk strings is a follow-up, not in this
  task's scope.

## Constraints

- **No new top-level directory at the repo root.** Everything new
  lands under `tests/evals/`, `tests/evalkit/`,
  `tests/fixtures/eval-vaults/`, `docs/specs/task-20-eval-harness/`,
  `docs/guides/explanation/evals.md`, and `.github/workflows/evals.yml`.
- **No new runtime dependency.** The harness uses stdlib (`subprocess`,
  `shutil`, `os`, `json`, `pathlib`, `tempfile`) plus the kit's
  existing `pyyaml` and `pydantic>=2`. Any new dev dep must appear in
  `pyproject.toml`'s `[project.optional-dependencies].dev` with a
  one-line PR-description rationale; the plan calls out each.
- **No bypass of `write_helper.safe_write()`.** Evals do not edit
  vault files directly; they drive `wiki` CLI commands or `claude`
  itself.
- **No new public CLI verb.** Evals are pytest-side; the `wiki`
  surface is unchanged by this task.
- **No mocking of `claude`.** A test that mocks the binary tests
  the harness, not the integrated path — and the integrated path
  is the whole reason this task exists. Tests that exercise the
  harness's plumbing (skip behaviour, timeout, argv shape) belong
  under `tests/unit/test_evalkit_*.py` and target the `evalkit`
  helpers directly with a tmp shell-script stand-in, not the
  `tests/evals/` family.
- **No reliance on `claude`'s text output format.** The harness
  uses `--output-format stream-json --verbose` (and deliberately
  *not* `--include-partial-messages`) and parses stdout
  line-by-line as JSON. Skill-loaded detection scans for
  `tool_use` blocks named `Skill`. If Anthropic ships a format
  change, the fix is one function (`evalkit._parse_stream_json`).
- **No `pytest.xfail`.** Upstream-provider transient failures use
  `pytest.skip` with a reason string carrying the HTTP status and
  body excerpt. `xfail(strict=False)` masks regressions by
  surfacing as a yellow "expected failure" line in the run.
- **No new model name pin in tests.** The model choice lives in
  one constant in `tests/evalkit/__init__.py` — default alias
  `sonnet`, not a pinned full name — overrideable via
  `LLM_WIKI_KIT_EVAL_MODEL`. The alias follows Anthropic's
  "latest" pointer when Sonnet ships a new generation; the
  trade-off is that a generation bump can flake an assertion.
  We accept the flake risk in exchange for not silently running
  an outdated model. Override to a pinned full name (e.g.
  `claude-sonnet-4-7`) to bisect a regression. Bumping the
  default alias is a one-line PR.
