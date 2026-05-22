# Plan: wiki-search

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** In progress
- **Spec:** `docs/specs/wiki-search/spec.md`
- **Owner:** maintainer

## Approach

One new module `llm_wiki_kit/search.py` holds the pure logic: shell out
to `rg --json --fixed-strings --no-ignore --hidden --no-messages`,
parse its JSON Lines output for `(path, match_count)`, parse each
match's YAML frontmatter, apply the filter trio, sort by
`(-match_count, path)`, render as markdown. The CLI handler
`_cmd_search` becomes a thin wrapper: it does the vault-root boundary
check, validates `--top`, empty query, and empty filter values, then
calls `run_search` and prints the formatted output.

I picked ripgrep's JSON Lines output (`--json`) over the text format
because the per-file match count comes as a discrete `end` event per
file — no string parsing of "path:count" lines, no Windows-vs-Unix
separator concerns. Each line is one JSON object; `json.loads` per
line is fine.

Order: write the construction tests first (TDD-mode), then `search.py`,
then re-point `_cmd_search`, then update the CLI parser's stub-test
list, then the integration tests, then the doc sweep.

## Pre-conditions

- ripgrep installed on the dev box (already a dev convention; CI
  runners have it).
- `pyyaml` in runtime deps (already there for the research dispatcher
  and recipe loader).
- No conflicting work in flight on `cli.py:_cmd_search`.

## Steps

1. **Construction tests for `search.run_search` are red.**
   - New file `tests/unit/test_search.py` covering: empty `wiki/`
     returns `[]`; ripgrep absent raises `WikiError("ripgrep (rg)
     not found …")`; query matches in two files rank by match-count
     descending then path ascending; `SearchFilters(type=…)` drops
     non-matching pages; `tags` as bare string coerces to a single-
     element list for filter compare; malformed YAML frontmatter
     yields blank metadata, not a raise; `--top` caps the result
     count. Each test uses `tmp_path` to materialize a tiny vault
     and either invokes the real `rg` (when available — gate with
     `pytest.importorskip`-style `shutil.which("rg")` skip) or
     monkeypatches `subprocess.run`. The PATH-missing test
     monkeypatches `shutil.which` to return `None` so it runs even
     where `rg` exists.
   - **Verify:** `pytest tests/unit/test_search.py` fails at import
     (module does not exist yet).
2. **`llm_wiki_kit/search.py` makes the construction tests pass.**
   - Implement `SearchFilters`, `SearchHit`, `run_search`,
     `format_results` per the spec's §Contracts surface. `subprocess`
     call uses `check=False` because exit 1 is success-empty.
     `rg`'s `--json` emits `type: "begin" | "match" | "end" |
     "summary"` records; the **`end`** records carry the per-file
     `stats.matches` count and the `path` we need (the `summary`
     record is run-wide and has no path). `--count-matches` is *not*
     used: combined with `--json` it suppresses the JSON output, and
     `--json` alone already gives us the per-file totals.
   - Frontmatter parser: lightweight inline, not a new helper. Strip
     a leading `---\n` … `\n---\n` block, `yaml.safe_load` it inside
     a `try/except yaml.YAMLError` that swallows to `{}`. Title
     extraction reads the first non-frontmatter line that starts
     with `# `.
   - **Verify:** `pytest tests/unit/test_search.py` green; new module
     has 100% line coverage of its public functions (eyeball, not a
     gate).
3. **`_cmd_search` calls `run_search` instead of `_stub`.**
   - Replace the body of `_cmd_search` in `cli.py:1140` with: vault
     check (same shape as `_cmd_research`'s `not a wiki vault`
     guard); empty-query and `--top` validation; build
     `SearchFilters`; call `search.run_search`; `print(
     search.format_results(hits))`; return 0.
   - Extend `build_parser`'s `search` subparser with `--type`,
     `--tag`, `--status`, `--top` flags. Help strings stay terse
     (≤ 80 chars).
   - **Verify:** `wiki search --help` lists the four flags; CLI
     stub-list in `tests/unit/test_cli.py` no longer carries
     `["search", "stakeholder"]`.
4. **Stub-list test updates.**
   - In `tests/unit/test_cli.py`, remove the `["search", "stakeholder"]`
     entry from `SUBCOMMANDS_WITH_ARGS` (the comment above the list
     also gets the "search graduated in Task 22" annotation).
     `init`/`add`/`doctor`/`ingest`/`run`/`research`/`search` is the
     graduated set after this PR.
   - **Verify:** `pytest tests/unit/test_cli.py` green.
5. **Integration test covers the CLI boundary.**
   - New file `tests/integration/test_wiki_search.py`. Reuses the
     `kit_root` / `fresh_vault` fixture pattern from
     `tests/integration/test_wiki_research.py`. Three scenarios:
     happy path (two pages match, ordered as expected), filter
     (`--type meeting` excludes a non-meeting hit), no-vault
     boundary (`not a wiki vault` on stderr). The `rg` binary is
     assumed to exist on CI; a module-level
     `pytest.mark.skipif(not shutil.which("rg"), …)` skips the
     suite where it's missing.
   - **Verify:** `pytest tests/integration/test_wiki_search.py`
     green.
6. **Doc sweep aligns with the shipped surface.**
   - `core/files/skills/wiki-search/SKILL.md` — replace the
     "⚠️ Not yet shipped in v2.0.0.dev" admonition with a "Tier 1
     (ripgrep) ships in v2.0.0; tier 2 (FTS5 auto-upgrade) remains
     future work — see `docs/specs/wiki-search/spec.md` §Non-goals."
     note. The rest of the SKILL.md (composing queries, reading
     results) stays — it's the agent-facing contract.
   - `core/files/AGENTS.md` — drop the "*Phase D — not yet shipped
     in v2.0.0.dev*" admonition from the `wiki search` bullet
     (lines 149-152).
   - `docs/architecture/overview.md` line 119 — soften "Phase D/E
     subcommands are stubs" to acknowledge `search` graduated:
     "(`upgrade` and `journal` subcommands are stubs in
     v2.0.0.dev)".
   - `docs/rfc/0001-v2-architecture.md` — Phase E now records "Task
     22 also ships `wiki search` per §CLI surface (target)". (The
     RFC has no explicit deferred-list for Task 22 today; the
     stub-aware admonitions in AGENTS.md and overview.md are the
     functional deferred list. The user's brief asked to "strike
     `wiki search` from Task 22's deferred list" — the operational
     interpretation is the doc sweep above.)
   - **Verify:** `git grep -n "wiki search" core docs` shows no
     "not yet implemented" / "Phase D" warnings on `wiki search`.

## Verification gate

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

All four green. `pytest tests/unit/test_search.py
tests/integration/test_wiki_search.py tests/unit/test_cli.py` green
in particular.

## Risks

- **CI lacks `rg`.** `.github/workflows/ci.yml` installs ripgrep via
  `sudo apt-get install -y ripgrep` before pytest runs, so the full
  search suite stays on the critical path rather than silently
  skipping. The `shutil.which("rg")` guards in both the unit
  (`@rg_required`) and integration (`pytestmark = skipif`) suites
  remain as graceful-degradation for developer hosts that don't
  have ripgrep installed locally.
- **ripgrep JSON-output schema drift.** ripgrep 13+ has stable
  per-file `end` records; we depend only on `type == "end"`,
  `data.path.text`, and `data.stats.matches`. Older `rg < 11` may
  not carry `matches` in `stats`; mitigation: `_parse_match_counts`
  only emits an entry when `matches` is a positive integer, so a
  missing field degrades to "page absent from results" rather than
  a crash. A follow-up spec can add a `match`-record-summing
  fallback if a real user reports an `rg` version that needs it.
- **PATH manipulation in tests leaks.** Use `monkeypatch.setenv`
  / `monkeypatch.setattr(shutil, "which", …)` exclusively so the
  scope is per-test.
- **Encoding edge cases.** A vault page that's UTF-16 or has a BOM
  could trip `read_text`. The spec drops unreadable files from the
  metadata pass while preserving the path in results; tested.

## Out of scope

- FTS5 / SQLite tier (spec §Non-goals; future spec).
- Snippet rendering with term highlights (FTS5-tier).
- Regex queries, case-insensitive flag, multi-tag filters.
- Wiring `wiki doctor` to flag a missing `rg` (search reports it on
  demand; doctor flagging is duplicative).
