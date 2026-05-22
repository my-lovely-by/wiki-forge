# Evals: how the kit's eval harness works

> Why this exists, how it's shaped, and how to add a new eval. Spec:
> [`docs/specs/task-20-eval-harness/spec.md`](../../specs/task-20-eval-harness/spec.md).
> Plan: [`docs/specs/task-20-eval-harness/plan.md`](../../specs/task-20-eval-harness/plan.md).

The kit's `tests/evals/` suite is the only place we exercise the
integrated journey — *prompt → Claude Code → kit artifacts* — and the
contract surface around it (dispatch shape, journaled events,
rendered markdown). Pure-Python tests live in `tests/unit/` and
`tests/integration/`; evals are different because they involve a
running model.

## What is an eval?

An eval is a pytest test under `tests/evals/` that asserts on
observable artifacts produced when *something other than the test
itself* drives the kit. Two flavors ship today:

- **Integrated-journey evals.** Drive `claude` via subprocess
  against a fixture vault, then assert on the vault's resulting
  on-disk state and journal. The `evalkit.run_claude` helper pins
  the argv (model, permission mode, budget cap, isolated `$HOME`,
  etc.) so every eval starts from the same baseline. Used in
  `trigger/`, `outcome/`, `provenance/`, `conflict/`, and the live
  `research/` scenario (5f).
- **Contract evals.** Drive the kit's CLI directly (`cli.main`) and
  assert on journal events + rendered output, no `claude` involved.
  Used for the research-dispatch contract (5e); add more when a
  provider/operation contract benefits from being asserted next to
  the integrated-journey scenarios in the same family.

The five eval families:

| Family | What it asks |
|---|---|
| `tests/evals/trigger/` | Does the right SKILL load given a natural prompt? |
| `tests/evals/outcome/` | Does the SKILL produce the contracted output structure? |
| `tests/evals/provenance/` | Do citations propagate from research into consuming notes? |
| `tests/evals/conflict/` | Does `wiki-conflict` correctly drive a three-way merge? |
| `tests/evals/research/` | Does the research dispatcher's contract hold across providers? |

## How to add a new eval

Suppose you want to add `tests/evals/trigger/test_wiki_search_trigger.py`
asserting that prompting "find the meeting where we decided X" loads
the `wiki-search` SKILL.

1. **Pick the family.** A scenario about which SKILL loads is a
   trigger eval. A scenario about whether a specific output appears
   is an outcome eval. Don't mix families — a passing eval that
   spans two families is testing a journey, but the assertion
   should split across files.

2. **Pick or build a seed-vault factory.** Every eval reads its
   vault from a pytest fixture defined in
   `tests/evals/conftest.py`. The factories are session-scoped and
   build their vault once via the real kit CLI (`wiki init` +
   `wiki add`). If a family needs a new content-type or
   infrastructure primitive installed, extend the factory rather
   than building a one-off fixture.

3. **Carry the marker.** Every eval file's first executable line is
   `pytestmark = pytest.mark.eval`. This is what keeps the fast
   `pytest` invocation fast — `addopts = "-m 'not slow and not
   eval'"` in `pyproject.toml` excludes the suite by default.

4. **Skip on missing prerequisites, don't fail.** Integrated-journey
   scenarios call `evalkit.skip_if_env_unset("ANTHROPIC_API_KEY")`
   and `evalkit.skip_if_no_claude()` at the top of the test body.
   Contract evals (5e shape) skip nothing — they always run.

5. **Drive the run.** For an integrated-journey eval:
   ```python
   result = evalkit.run_claude(
       prompt="...",
       vault=trigger_vault,                 # the fixture
       allowed_tools=["Read"],              # scoped to what's needed
       timeout_s=120.0,
   )
   ```

6. **Assert on observable artifacts.** Skill names are read from
   the SKILL's frontmatter at fixture-build time so a future rename
   doesn't silently flake the eval. Journal assertions use
   `evalkit.assert_journal_has(events, kind=..., **filters)`. File
   shape is validated by inline Pydantic models defined next to the
   test.

7. **Run it locally.** `pytest tests/evals/trigger -m eval -k
   wiki_search` against a machine with `ANTHROPIC_API_KEY` set and
   `claude` on `$PATH`. The `-m eval` is required even for
   path-based selection — see "The addopts foot-gun" below.

## What not to put in an eval

- A test that mocks `claude`. That tests the harness, not the
  integrated path. Tests of the harness's plumbing (argv shape,
  timeout, `$HOME` isolation) belong under `tests/unit/
  test_evalkit_*.py` and use a tmp shell-script stand-in.
- A test that asserts on the model's prose. Models reword
  citations, list elements, paraphrases — the assertion shape is
  always "file exists", "journal carries event of kind X",
  "frontmatter validates against this Pydantic model", "body
  contains this substring". Equality on prose is a flake factory.
- A test that calls a paid API in the fast lane. The
  `pytest -m 'not slow and not eval'` invocation never burns
  budget; live scenarios under `tests/evals/research/` skip with a
  reason when the relevant `*_API_KEY` is unset.
- A test that writes outside `tmp_path`. The harness enforces this
  in `EvalkitClaudeRunner.__post_init__` — fixtures that hand the
  runner a non-tmp path fail fast before any subprocess fires.

## How `evalkit` fits in

`tests/evalkit/` is the shared harness. Public surface:

| Symbol | What it does |
|---|---|
| `run_claude(...)` | One-shot subprocess invocation; returns parsed events |
| `EvalkitClaudeRunner` | The dataclass behind `run_claude`; pins argv + env |
| `assert_skill_loaded(result, name)` | Scans the stream-json for `Skill` tool-use or `Read(skills/<name>/SKILL.md)` |
| `assert_journal_has(events, kind=, **filters)` | Filters journal events by class + field values |
| `read_journal_events(vault)` | Convenience re-export of `journal.read_events` |
| `skip_if_env_unset(name)` | `pytest.skip(...)` carrying the missing var name |
| `skip_if_no_claude()` | `pytest.skip(...)` when the binary can't be resolved |
| `EVAL_MODEL`, `EVAL_MAX_BUDGET_USD`, `EVAL_DEFAULT_TIMEOUT_S` | Module-level defaults; override via env vars of the same name |

It lives under `tests/` rather than inside `llm_wiki_kit/` so the
wheel never ships it — and the existing `mypy llm_wiki_kit tests`
gate gives the same strict-typing coverage that putting it under
`llm_wiki_kit/_evalkit/` would have.

## The addopts foot-gun

`pyproject.toml` carries `addopts = "-ra --strict-markers -m 'not
slow and not eval'"`. Path-based selection inherits the `-m`
filter, so:

```bash
# WRONG: reports "0 tests collected, 1 deselected"
pytest tests/evals/conflict/test_resolve_proposal.py

# RIGHT: -m eval overrides the addopts filter
pytest tests/evals/conflict/test_resolve_proposal.py -m eval
```

Every eval file carries a top-level `pytestmark = pytest.mark.eval`
so a developer reading the file sees the marker that's gating
collection. The convention is documented inline in the addopts
comment in `pyproject.toml`.

## How evals run in CI

Two GitHub Actions workflows split the work:

- **`.github/workflows/ci.yml`** — the fast lane. Lint, format,
  typecheck, then `pytest -m 'not slow and not eval'`. Always
  green; never reads `ANTHROPIC_API_KEY`.
- **`.github/workflows/evals.yml`** — the eval lane. Installs the
  pinned `@anthropic-ai/claude-code` npm package, then `pytest
  tests/evals -m eval -ra --junitxml=...`. Uploads junit XML so
  per-scenario skip reasons survive into the run UI. Triggered on
  `push` to `main` and `pull_request`; no nightly cron (cadence
  follows PR activity).

Fork PRs do not receive repo secrets, so every keyed scenario
reports `SKIPPED` with a reason on fork builds. That is by design
— see Spec §Non-goals.

## When you find yourself fighting the harness

The harness pins a small set of opinionated flags
(`--permission-mode acceptEdits`, `--output-format stream-json
--verbose`, `--no-session-persistence`, `--max-budget-usd`,
isolated `$HOME`, `cwd=vault`). Each closes a specific failure mode
documented in the spec. If a new scenario doesn't work without
flipping one of these, that's a signal to *amend the spec*, not
add a per-scenario override.

The transcript parser is the part most likely to drift. If
Anthropic changes the stream-json shape, `evalkit._parse_stream_json`
is the one function to fix; the failing unit tests under
`tests/unit/test_evalkit_runner.py` will tell you the new shape.
