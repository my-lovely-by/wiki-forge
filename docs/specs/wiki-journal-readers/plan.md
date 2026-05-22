# Plan: wiki journal readers (`tail`, `grep`, `explain`)

> **Implementation plan paired with `spec.md`.** The spec says *what*;
> the plan says *how, in what order, with what verification*.

- **Status:** In progress
- **Spec:** `docs/specs/wiki-journal-readers/spec.md`
- **Owner:** Eu Gene Lim

## Approach

One PR replaces the three `_stub("journal …")` handlers in
`llm_wiki_kit/cli.py:1143–1152` with real handlers. All three share a
single helper that loads the journal once per invocation (the
`tail`/`grep`/`explain` invocations are independent, so the helper is
function-call shared, not state shared). Formatting is two pure
functions (`_format_event_line` for the tab-separated tail/grep format,
`_format_event_block` for explain's multi-line block) so unit tests
can exercise them without touching the filesystem.

The line-number↔event mapping is computed by streaming the raw file
once via a new public `journal.parse_event_line(raw, line_no)` helper
— the same strict validator `read_events` uses, exposed so the CLI
can parse + capture line numbers in one pass. Single-pass parsing
eliminates the desync window a "load then re-walk" two-step approach
would risk under a concurrent writer.

### Declined patterns

- **Tempted to add a `journal_format.py` module.** Declining: two
  small format functions are not enough surface to justify a new
  module boundary (Constraints §"No new module boundary").
- **Tempted to add `--json` to `tail`/`grep`.** Declining: there's no
  caller asking for it yet; it's a clean additive change later
  (Non-goals).
- **Tempted to add regex / `-i` / `-v` to `grep`.** Declining: users
  can `grep` the file directly for advanced cases; substring covers
  the common shape (Non-goals).
- **Tempted to call `read_events_lenient` so corrupt journals
  partially print.** Declining: ADR-0002 §Negative names this as the
  exact bug to avoid; the lenient reader is reserved for `wiki
  doctor` (Invariants).
- **Tempted to extend `read_events` to also return line numbers.**
  Declining: it would broaden a load-bearing core API for a single
  caller (the readers). The CLI streams the file once locally via
  the new `journal.parse_event_line` helper instead.

## Pre-conditions

- `journal.read_events` and the discriminated `Event` union are
  shipped (Tasks 3–4, already done).
- `WikiError` boundary in `cli.main` already prints one-line errors
  to stderr with `WIKI_ERROR_EXIT` (Task 2).
- `journal` subparser with `tail`/`grep`/`explain` parsers exists in
  `build_parser` (Task 2 stub).

## Steps

1. **Spec written and committed alongside this plan.** ✓
1. **TDD red: unit tests for the three handlers fail.**
   - Add `tests/unit/test_journal_readers.py` covering each acceptance
     criterion in spec §"Acceptance criteria". Use `tmp_path` and
     `monkeypatch.chdir(tmp_path)` so `Path.cwd()` resolves to the
     fixture vault.
   - Add a fixture helper that builds a journal with N events of
     known types (`VaultInitEvent`, `PrimitiveInstallEvent`,
     `PageWriteEvent`, `OperationRunEvent`, …) via `append_event`.
   - Update `tests/unit/test_cli.py`:
     - Remove `["journal", "tail"]`, `["journal", "tail", "-n", "20"]`,
       `["journal", "grep", "ingest"]`, `["journal", "explain",
       "abc123"]` from `SUBCOMMANDS_WITH_ARGS` — they're no longer
       stubs.
     - Keep the `_LEAF_SUBCOMMANDS` entries that test `--verbose`
       discoverability for the three leaves.
   - Verify: `pytest tests/unit/test_journal_readers.py` fails for
     every new test before any implementation lands.
1. **TDD green: handlers implemented to match the spec.**
   - Replace `_cmd_journal_tail`, `_cmd_journal_grep`,
     `_cmd_journal_explain` in `cli.py`. Add the shared loader helper
     `_load_journal_events_with_lines(vault_root) -> tuple[Path,
     list[tuple[int, Event]]]` that pre-flights the vault and streams
     the file once via `journal.parse_event_line(raw, line_no)`,
     recording `(line_no, event)` for every non-blank line. The
     blank-line decision mirrors `journal._parse_line` exactly
     (`raw.strip() == ""`) so the line numbers stay well-defined for
     CRLF / final-newline / mid-file blank line shapes. Single-pass
     parsing avoids the desync class a `read_events`-then-re-walk
     approach would have under a concurrent writer.
   - Add `_format_event_line(line_no, event) -> str` and
     `_format_event_block(line_no, total, event) -> str`. Add an
     `_EVENT_SUMMARY_FIELDS` table mapping each concrete event class
     to the ordered field list named in spec §Outputs. The formatter
     **must raise** `KeyError` (or a typed sub-exception) when
     `type(event)` is absent from `_EVENT_SUMMARY_FIELDS` — a silent
     "empty summary" fallback would defeat the "missing summary
     mapping fails loudly" invariant. Both formatters apply the
     tab/newline→space substitution rule from spec §Outputs to every
     rendered value before joining.
   - Update the `argparse` argument name for `explain` from
     `event_id` to `event`. Drop `type=int` (do not add one) so the
     handler can emit the spec-mandated `WikiError` for non-integer
     input.
   - Re-type `-n/--lines` on `tail` to take a string (drop the
     existing `type=int`) so all invalid `-n` shapes route through
     the same `WikiError`.
   - Add a `--type` argument to the `grep` parser (optional string).
   - Add `journal.dump_event_json(event) -> str` (one-line wrapper
     over `_EVENT_ADAPTER.dump_json(event).decode()`) so `cli.py`
     uses a public helper instead of importing the underscore-prefixed
     adapter. The grep substring match calls this; the bytes match
     the on-disk line exactly (less the trailing newline).
   - Tests: green.
1. **TDD refactor: cli.py stays readable.**
   - Pull the per-event-type summary mapping out into a small dict or
     match statement so adding a new event type later is one-line.
   - Verify: tests still green; ruff and mypy clean.
1. **All four CI gates pass.**
   - `ruff check llm_wiki_kit tests`
   - `ruff format --check llm_wiki_kit tests`
   - `mypy llm_wiki_kit tests`
   - `pytest -m 'not slow'`
1. **PR opened.**
   - Title: `v2: implement wiki journal tail/grep/explain`
   - Commit message matches title.
   - PR body links to this spec and notes the three stubs are gone.

### Tests (live in `tests/unit/test_journal_readers.py`)

The spec's acceptance criteria are the contract; the unit tests
below mirror them 1:1 since the surface is small. Per AGENTS.md TDD
guidance for pure-function-shaped code, all tests are written before
the handlers, and `pytest -m 'not slow'` fails before any
implementation lands. The pre-implementation failure shape is: the
existing `_stub("journal …")` callsites print `not yet implemented`
to stderr and return `NOT_IMPLEMENTED_EXIT` (1), so tests asserting
spec-mandated stdout, exit code 0, or a `WikiError` will all fail in
that shape until step 3 lands.

- `test_tail_empty_journal_prints_nothing` — spec AC empty-journal.
- `test_tail_n_3_prints_last_three_in_order` — spec AC tail-default.
- `test_tail_default_is_10` — spec AC tail-default.
- `test_tail_n_larger_than_journal_prints_all` — spec AC tail-overshoot.
- `test_tail_n_zero_raises_wiki_error` — spec AC tail-n-zero.
- `test_tail_n_negative_raises_wiki_error` — spec AC tail-n-zero.
- `test_tail_n_non_integer_raises_wiki_error` — spec §Error cases.
- `test_grep_substring_match_against_canonical_json` — spec AC grep.
  Asserts the substring is matched against
  `journal.dump_event_json(event)` (the on-disk bytes, less the
  trailing newline) by searching for a field value that does not
  appear in the human-readable `<summary>`.
- `test_grep_type_filter_narrows_before_substring` — spec AC grep-type.
- `test_grep_type_filter_unknown_type_exits_zero` — spec §Edge cases
  ("`grep --type X` with unknown X").
- `test_grep_no_matches_exits_zero` — spec AC grep-no-match.
- `test_grep_empty_pattern_raises_wiki_error` — spec AC grep-empty.
- `test_explain_prints_multiline_block_for_valid_line` — spec AC
  explain-valid. Asserts the header reads exactly `Event N of K in
  .wiki.journal/journal.jsonl` regardless of the cwd within the
  vault tree.
- `test_explain_value_tabs_and_newlines_are_substituted` — spec
  §Outputs (escaping rule). Builds a `ConfigSetEvent` with a value
  containing `\t` and `\n`, asserts both `tail`'s TSV stays splittable
  to the expected field count and `explain`'s block has the chars
  collapsed to spaces.
- `test_explain_out_of_range_raises_wiki_error` — spec AC explain-oob.
- `test_explain_empty_journal_uses_out_of_range_message` — spec
  §Edge cases ("explain N on empty journal").
- `test_explain_non_integer_raises_wiki_error` — spec AC explain-bad-arg.
- `test_explain_non_positive_raises_wiki_error` — spec §Error cases
  (0 and negative).
- `test_readers_outside_vault_raise_wiki_error` (parametrized over
  the three commands) — spec AC outside-vault.
- `test_readers_propagate_corruption_as_wiki_error` — spec AC
  corruption.
- `test_readers_do_not_mutate_journal` (parametrized over the three
  commands) — spec §Invariants. Asserts the file's content bytes and
  `stat().st_size` are unchanged after the invocation; mtime is not
  the assertion target (filesystem-noisy).
- `test_format_event_line_round_trips_through_explain` — spec
  §Invariants line-number-stability. Builds a journal with intentional
  mid-file blank lines, whitespace-only lines, and CRLF line endings,
  then asserts every line number printed by `tail` is a valid
  `explain` target that returns the matching event's `type`.
- `test_format_event_line_covers_every_concrete_event_class` —
  parametrizes over every concrete event class in `Event`'s union;
  asserts the formatter does not raise and the line begins with
  `<line>\t<timestamp>\t<by>\t<type>\t`. Paired with
  `test_format_event_line_raises_on_unmapped_class` which constructs
  a fake `_EventBase` subclass (or monkey-patches an entry out of
  `_EVENT_SUMMARY_FIELDS`) and asserts `_format_event_line` raises
  — together these pin "every concrete class is mapped AND an
  unmapped class is loud".
- `test_summary_table_has_no_list_typed_fields` — walks
  `_EVENT_SUMMARY_FIELDS`, looks up each named field on the
  corresponding event class via `model_fields[name].annotation`, and
  asserts the annotation isn't a `list[...]` type. Pins spec §Outputs'
  "no list fields in the summary table" invariant so adding a list-
  typed event field and including it in the summary fails the gate.

## Verification gate

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

All green; adversarial-reviewer returns `Clean — ready to commit.`.

## Risks

- **Format drift across event types.** Different event subclasses
  carry different fields; the per-type summary could rot when a new
  event ships. Mitigation: the unit-test suite parametrizes over
  every concrete event class in `Event` so a new class without a
  summary mapping fails loudly.
- **Blank-line classification drift between strict loader and CLI
  reader.** Single-pass parsing in `cli.py` calls the same
  `parse_event_line` validator the strict loader uses, so the
  blank-vs-non-blank decision is one rule, not two. Mitigation: a
  unit test seeds a journal with mid-file blank, whitespace-only,
  and CRLF-terminated lines and asserts every `tail`-printed line
  number resolves through `explain` to the matching event.
- **`--type` filter for an unknown type silently exits 0.** Could be
  surprising. Mitigation: spec is explicit that "no matches" exits 0
  (grep convention); the help text names `wiki journal tail` as the
  way to discover types.

## Out of scope

- `--json` output, regex matching, `--follow`, color, pagination
  (spec §Non-goals).
- Hash-based addressing for `explain` (spec §Non-goals).
- Aggregation / counting (spec §Non-goals).
- Touching the journal's on-disk format (spec §Constraints).
