# Spec: wiki-search

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit/search.py`, `llm_wiki_kit/cli.py:_cmd_search`
- **Related:** RFC-0001 §"CLI surface (target)" line 139,
  `core/files/skills/wiki-search/SKILL.md` (vault-side skill),
  `docs/specs/wiki-search/plan.md`
- **Constrained by:** RFC-0001 §"Runtime constraints" (no new runtime
  deps without an ADR), AGENTS.md §"Check before acting" (no vault path
  baked in; tests use `tmp_path` or fixture vaults).

## What this is

`wiki search <query>` is the read-only CLI subcommand that searches the
vault's `wiki/` content tree for a literal substring, optionally filtered
by frontmatter fields, and prints a markdown-formatted list of ranked
matches. The kit shells out to **ripgrep (`rg`)** as the scanner — no
Python regex engine, no index, no journal write. The vault-side
`wiki-search` SKILL.md describes a two-tier system (ripgrep then FTS5
auto-upgrade at ~1000 pages); this spec ships **tier 1 only**. FTS5 is
explicitly deferred (§Non-goals).

The contract is "given a substring and zero or more frontmatter
filters, point me at the pages most worth opening." No LLM, no synonyms,
no semantic match — that's the lexical-only invariant the SKILL.md
documents.

## Inputs

CLI invocation: `wiki search <query> [--type <name>] [--tag <name>]
[--status <name>] [--top <N>]`.

- `<query>` — a single positional string. Treated as a **literal
  substring** by `rg --fixed-strings`; regex metacharacters do not
  apply. May contain spaces (the user quotes at the shell).
- `--type <name>` — optional. Restrict results to pages whose
  frontmatter `type` field equals this value (string compare,
  case-sensitive). Repeating the flag is not supported in tier 1;
  argparse would silently keep the last value if it were.
- `--tag <name>` — optional. Restrict results to pages whose
  frontmatter `tags` list contains this value (string compare,
  case-sensitive). Pages whose `tags` field is absent, empty, or not a
  list fail the filter.
- `--status <name>` — optional. Restrict results to pages whose
  frontmatter `status` field equals this value.
- `--top <N>` — optional, default `10`, integer ≥ 1. Maximum number of
  pages to print. Pages are ranked by ripgrep match count descending,
  then by vault-relative path ascending (deterministic tie-break for
  test stability).
- Vault root: `Path.cwd()`. Must contain `.wiki.journal/journal.jsonl`
  (same `not a wiki vault` boundary check the other commands use).
- Search root: `<vault_root>/wiki/`. If the directory does not exist,
  the command exits 0 with "no matches" — an empty `wiki/` tree is a
  legitimate fresh-vault state, not an error.

## Outputs

- **stdout — markdown ranked list.** One block per matched page,
  separated by blank lines. Each block:

  ```
  ## <title> — <relative-path>
  - type: <type-or-empty>
  - status: <status-or-empty>
  - tags: <comma-joined-or-empty>
  - matches: <count>
  ```

  `<title>` resolves to the first `# H1` line in the file's body —
  ignoring any `# ` line that appears inside a fenced code block —
  or the filename stem (`Path.stem`) if no H1 is present.
  `<relative-path>` is the page's path relative to the vault root,
  POSIX-separated. The four metadata lines are always printed (empty
  string after the colon is fine — the SKILL.md says the consumer
  reads these as decision inputs). `<count>` is ripgrep's
  `data.stats.matches` — the number of match instances in the file,
  not the number of matched lines (two `kafka`s on one line
  contribute 2 to the count).

  The block ends with a single trailing newline so blocks concatenate
  cleanly. The whole output ends with a final newline.

  When there are zero matches: print `no matches.\n` to stdout. Exit 0.
- **stderr — boundary errors only.** "not a wiki vault", "ripgrep (`rg`)
  not found on PATH", or argparse usage. No progress chatter.
- **No journal writes.** Search is read-only. Repeating the same query
  produces the same output bit-for-bit (within the same on-disk state).

## Behavior

### Happy path

1. Resolve `vault_root = Path.cwd().resolve()`; verify
   `vault_root/.wiki.journal/journal.jsonl` exists. If not, raise
   `WikiError("not a wiki vault: …")`.
2. Resolve `wiki_dir = vault_root / "wiki"`. If it does not exist or
   is empty, print `no matches.` and return 0.
3. Locate the ripgrep binary via `shutil.which("rg")`. If absent, raise
   `WikiError("ripgrep (rg) not found on PATH. Install via your OS
   package manager (e.g. 'brew install ripgrep', 'apt install ripgrep').
   See https://github.com/BurntSushi/ripgrep#installation.")`.
4. Invoke `rg --json --fixed-strings --no-ignore --hidden --no-messages
   -- <query> wiki` under `cwd=vault_root`. The kit reads `rg`'s JSON
   Lines output and collects `(relative-path, match-count)` from the
   per-file `type: "end"` records (the `data.stats.matches` field
   carries ripgrep's total — the number of match instances in the
   file, not the number of matched lines). Exit status 1 from `rg`
   (no matches) is a successful empty result, not an error; exit ≥ 2
   raises `WikiError` with the stderr text. `--no-ignore --hidden`
   keep the scan exhaustive: a `.gitignore` under `wiki/` must not
   silently hide pages, since the journal — not `.gitignore` — is the
   vault's authoritative ledger. `--no-messages` mutes ripgrep's
   permission-denied / symlink-loop chatter so the §Outputs "stderr —
   boundary errors only" contract holds.
5. For each candidate path:
   1. Read the file's UTF-8 content (skip and continue on
      `UnicodeDecodeError`; binaries under `wiki/` are not the
      target).
   2. Parse the YAML frontmatter if the file starts with `---\n` and
      contains a closing `---\n` line. Malformed YAML degrades to
      empty frontmatter (`{}`) — the path still appears in results
      with all metadata fields blank.
   3. Apply `--type` / `--tag` / `--status` filters. A page that
      fails any active filter is dropped from the result set.
6. Rank the surviving candidates: primary key `match_count` descending,
   secondary key `relative_path` ascending (POSIX form). Take the
   first `--top` entries (default 10).
7. Render each entry per the §Outputs schema. Print the joined output.
8. Return 0. Always 0 on a clean run, even when zero pages match —
   "no result" is a signal, not an error.

### Edge cases

- **`wiki/` missing.** Print `no matches.`; exit 0. A vault initialized
  without any ontology primitives may legitimately have no `wiki/`
  tree yet.
- **Query is the empty string.** argparse accepts the empty string as
  a positional; `rg --fixed-strings ""` matches every line of every
  file, which is useless. Reject at the CLI boundary: raise
  `WikiError("search query must not be empty")`.
- **Non-UTF-8 file under `wiki/`.** Skipped (the file's match count is
  preserved from ripgrep's output, but the frontmatter and title
  cannot be read — the entry renders with the filename stem as title
  and empty metadata; filters that gate on the unreadable frontmatter
  drop the entry).
- **Frontmatter missing closing `---`.** Treated as no frontmatter;
  metadata renders empty.
- **`tags` field is a string, not a list.** Coerced to a single-element
  list for the filter comparison. (Obsidian-style `tags: urgent` is
  common in user vaults.)
- **`# `-prefixed line inside a fenced code block.** Skipped when
  picking the title — `_read_page_metadata` tracks an `in_fence` flag
  over ```` ``` ```` lines so a page that opens with a Python or Bash
  snippet doesn't pick a code comment as its title.
- **OS-level read error during the metadata pass.** Propagates as the
  underlying `OSError`. `cli.main()` only catches `WikiError`, so the
  user sees a Python traceback identifying the offending path —
  that's the intended behavior: if ripgrep matched a file the metadata
  reader can't open, the underlying OS failure is the signal the user
  needs, not a blank-metadata hit.
- **`--top 0` or negative.** argparse `type=int` accepts these; the
  CLI rejects `< 1` with `WikiError("--top must be ≥ 1")`.
- **`--type ""` / `--tag ""` / `--status ""` (empty filter value).**
  Rejected at the CLI boundary with `WikiError("--<flag> must not be
  empty")`. Accepting them would degenerate to "match only pages whose
  frontmatter field is missing or empty" — almost certainly not the
  caller's intent, and unreachable from any reasonable invocation.
- **Path with embedded newlines.** Not produced by file systems we
  support; not handled.
- **Symlinks inside `wiki/`.** ripgrep does not follow symlinks by
  default — we rely on that default.
- **`.gitignore` inside the vault.** Bypassed via `--no-ignore`;
  ripgrep's default gitignore filtering does not apply. The journal
  is the authoritative inventory of what belongs in the vault.

### Error cases

- `not a wiki vault: …` — exit 2 (`WikiError` boundary), as with the
  other vault-bound commands.
- `ripgrep (rg) not found on PATH. …` — exit 2.
- `search query must not be empty` — exit 2.
- `--top must be ≥ 1` — exit 2.
- `--<flag> must not be empty` (for `--type` / `--tag` / `--status`)
  — exit 2.
- `ripgrep failed (exit <N>): …` (anything `rg` writes to stderr when
  its exit status is ≥ 2; the exit code is included in the message so
  a maintainer can distinguish e.g. `137` (OOM kill) from `134`
  (SIGABRT) without re-running) — exit 2.
- `ripgrep search exceeded 60s; the vault may be on a slow or
  unresponsive filesystem.` — exit 2. Defense-in-depth wall clock on
  the `subprocess.run(timeout=…)` call; 60s is well past any
  legitimate scan even for very large vaults.

## Invariants

1. **Read-only.** No journal events appended; no files written; no
   directories created. `git status` after a search is identical to
   `git status` before.
2. **Deterministic ordering.** For a fixed on-disk state and a fixed
   `(query, filters, top)`, the output is byte-identical across
   invocations. The match-count-then-path tie-break makes the order
   reproducible without depending on filesystem iteration order.
3. **Lexical only.** No stemming, no synonyms, no semantic match. The
   SKILL.md commits to this; tests verify (`run` does not match
   `running`).
4. **Vault-rooted scan.** Only files under `<vault_root>/wiki/` are
   scanned. `.wiki.journal/`, `.claude/`, `_templates/`, and primitive-
   contributed config files at the vault root are out of scope. The
   SKILL.md routes "code search inside `skills/`" to the IDE's Grep.
5. **No new runtime deps.** Uses stdlib (`shutil`, `subprocess`,
   `json`) plus `pyyaml` (already a runtime dep). Ripgrep is a system
   binary discovered via `shutil.which`; it is not imported.

## Contracts with other modules

- **`llm_wiki_kit.cli`** — `_cmd_search` does the boundary check and
  delegates to `llm_wiki_kit.search.run_search(vault_root, query,
  filters, top)`. The CLI is the only module with stdout side effects.
- **`llm_wiki_kit.search`** — new module. Public surface:
  - `SearchFilters(type: str | None, tag: str | None, status: str | None)`
    — small frozen dataclass.
  - `SearchHit(path: str, title: str, type: str, status: str, tags: list[str], match_count: int)`
    — frozen dataclass; ordering keys live here.
  - `run_search(vault_root: Path, query: str, filters: SearchFilters, top: int) -> list[SearchHit]`
    — orchestrates ripgrep + frontmatter parsing + ranking. Raises
    `WikiError` on the boundary failures listed above.
  - `format_results(hits: list[SearchHit]) -> str` — pure rendering;
    no I/O.
- **`llm_wiki_kit.errors.WikiError`** — re-used; no new exception
  type.
- **No interaction with `journal`, `safe_write`, `install`, or
  `research`.** Search is orthogonal to write paths.

## Acceptance criteria

- [ ] **AC1 — `wiki search "kafka"` in an empty vault prints
  `no matches.` and exits 0.**
- [ ] **AC2 — `wiki search "stakeholder"` over a fixture vault with
  two pages containing the word returns both, ranked by match count
  descending, with title / type / status / tags / matches lines.**
- [ ] **AC3 — `--type meeting` drops pages whose frontmatter type is
  not `meeting`.** A page with type `interview` containing the query
  is excluded.
- [ ] **AC4 — `--tag urgent` drops pages whose `tags` list does not
  include `urgent`.** A page tagged `[urgent, q4]` is included; a
  page tagged `[q4]` is excluded.
- [ ] **AC5 — `--status active` drops pages whose `status` is not
  `active`.**
- [ ] **AC6 — `--top 1` returns at most one entry even when more
  match.**
- [ ] **AC7 — Outside a vault, exits 2 with `not a wiki vault` on
  stderr.**
- [ ] **AC8 — When `rg` is not on `PATH`, exits 2 with the install
  guidance on stderr.** (Test monkeypatches `shutil.which` to return
  `None`; cross-platform-safe and avoids relying on a `PATH=""`
  invocation's behavior in the test harness's subshell.)
- [ ] **AC9 — Empty query exits 2 with
  `search query must not be empty`.**
- [ ] **AC10 — A page with malformed YAML frontmatter still appears in
  results with blank metadata; the search does not raise.**
- [ ] **AC11 — Search is read-only: journal length is unchanged after
  any invocation that returns 0 *and* after the boundary-error
  paths.**
- [ ] **AC12 — Output is byte-identical across two consecutive
  invocations against the same vault state (determinism).**
- [ ] **AC13 — `--type ""` / `--tag ""` / `--status ""` exits 2 with
  `--<flag> must not be empty`.** Pins the empty-filter-value
  guardrail so a future flag rewiring can't silently re-introduce the
  surprising "only pages missing this field" semantics.
- [ ] **AC14 — A page whose first H1-shaped line lives inside a
  fenced code block is *not* titled from that line.** The metadata
  reader skips ```` ``` ```` blocks; the title falls back to the
  filename stem when no out-of-fence H1 is present.

## Non-goals

- **FTS5 / SQLite tier.** The SKILL.md describes auto-upgrade at
  ~1000 pages. This spec ships ripgrep only. FTS5 is a future spec.
- **Snippet rendering with highlights.** ripgrep can produce snippets;
  rendering them is FTS5-tier work per the SKILL.md ("Snippet — the
  matched lines, with the query terms highlighted (FTS5 only)").
- **Regex queries.** `--fixed-strings` is the contract; users who
  want regex use the IDE's Grep.
- **Case-insensitive matching.** Out of scope for tier 1; can land as
  `--ignore-case` in a follow-up if a user asks. ripgrep is
  case-sensitive by default and the SKILL.md does not promise
  insensitive match.
- **Repeating `--tag` to AND or OR multiple tags.** Single-tag filter
  only.
- **Indexing or caching.** Each invocation re-runs ripgrep from cold.
- **Journaling search events.** Read-only; no audit trail.
- **Configurable search root.** Always `<vault_root>/wiki/`.
- **Coloured / paginated output.** Pure text; consumers (humans and
  agents) paginate themselves.

## Constraints

- **No new module boundary beyond `llm_wiki_kit/search.py`.** A single
  module under the existing package; no sub-package, no new top-level
  directory.
- **No new runtime dependency.** ripgrep is a *system binary* invoked
  via `subprocess`, not a Python distribution. `pyyaml` covers
  frontmatter parsing.
- **No bypass of the `not a wiki vault` boundary check.** Same shape
  as `_cmd_research`, `_cmd_add`, `_cmd_doctor`.
- **No journal interaction.** Search must not import `journal.py` or
  call `safe_write`.
- **Vault path is explicit.** `run_search` takes `vault_root` as an
  argument; no module-level constants, no `Path.cwd()` inside the
  search module.
