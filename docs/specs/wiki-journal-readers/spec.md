# Spec: wiki journal readers (`tail`, `grep`, `explain`)

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit/cli.py`
- **Related:** RFC-0001 §"CLI surface (target)" line 140, ADR-0002
  (journal as state truth), `docs/specs/wiki-journal-readers/plan.md`
- **Constrained by:** ADR-0002 — these are *readers*, never mutators;
  every observation goes through the strict readers in `journal`
  (`parse_event_line` for the single-pass walk used by all three
  commands; never `read_events_lenient`).

## What this is

Three read-only subcommands that surface the journal at
`.wiki.journal/journal.jsonl` to a human at the terminal:

- `wiki journal tail [-n N]` — print the N most recent events (default 10)
  in chronological order, one event per line.
- `wiki journal grep [--type T] PATTERN` — print every event whose
  serialized JSON contains `PATTERN` (substring, case-sensitive), optionally
  pre-filtered by event `type`.
- `wiki journal explain N` — print a multi-line human-readable summary of
  the event at 1-based line number `N` in the journal.

They are the user-facing view onto the source of truth ADR-0002 names.
None of them write to the journal, accept paths outside the cwd vault, or
spawn external tools. They are *not* a query language, *not* a structured
output format (`--json` is a non-goal), and *not* a substitute for
programmatic access (callers should import `journal.read_events`).

## Inputs

All three commands require a wiki vault at `Path.cwd()` — i.e.
`./.wiki.journal/journal.jsonl` must exist (same pre-flight as `wiki
doctor`, `wiki add`, `wiki ingest`).

- `tail`:
  - `-n, --lines N` — positive integer, default 10. Values ≤0 raise
    `WikiError`. If N exceeds the event count, print every event.
- `grep`:
  - `pattern` — required positional string. Empty string raises `WikiError`
    (matches everything; the user almost certainly meant `tail`).
  - `--type T` — optional event-type literal (e.g. `page.write`,
    `ingest.routed`). When set, only events with `type == T` are
    considered before the substring match. Unknown types print zero
    matches and exit `0` (consistent with grep semantics — "no matches"
    is not an error).
- `explain`:
  - `event` — required positional 1-based integer (renamed from the
    pre-spec `event_id`). Out-of-range or non-integer values raise
    `WikiError`.

## Outputs

All three commands print to **stdout** on success and **never** mutate
the journal, the holder file, or any vault file. No journal events are
appended by these readers (ADR-0002 §Negative: "Replay must be free of
side effects").

`tail` and `grep` format each event as one line:

```
<line>\t<timestamp>\t<by>\t<type>\t<summary>
```

- `<line>` — 1-based line number in `journal.jsonl` (the same number
  `explain` consumes).
- `<timestamp>` — ISO-8601 with timezone suffix (e.g.
  `2026-05-15T12:34:56+00:00`). Whatever `datetime.isoformat()` produces
  for the event's `timestamp`; we don't reformat.
- `<by>` — the event's `by` field.
- `<type>` — the event's `type` discriminator (e.g. `page.write`).
- `<summary>` — one-line `key=value` pairs of the event-type-specific
  fields. The mapping per event type is:

  | Event type | Summary fields |
  |---|---|
  | `vault.init` | `vault=<vault_name> recipe=<recipe>` |
  | `vault.git_initialized` | *(no fields — empty summary)* |
  | `primitive.install` | `primitive=<primitive> version=<version>` |
  | `primitive.remove` | `primitive=<primitive>` |
  | `primitive.upgrade` | `primitive=<primitive> from=<from_version> to=<to_version>` |
  | `managed_region.write` | `file=<file> region=<region>` |
  | `ingest.routed` | `source=<source> content_type=<content_type-or-(none)> via=<via>` |
  | `source.ingest` | `source=<source> content_type=<content_type>` |
  | `page.write` | `path=<path>` |
  | `page.proposal` | `path=<path> proposed=<proposed_path>` |
  | `page.conflict_resolved` | `path=<path>` (plus ` region=<region>` when set) |
  | `operation.run` | `operation=<operation> status=<status>` |
  | `research.query` | `provider=<provider> status=<status>` |
  | `lint.run` | `status=<status> issues=<issues>` |
  | `config.set` | `key=<key>` |
  | `lock.acquired` | `reason=<reason-or-(none)>` |
  | `lock.released` | `reason=<reason-or-(none)>` |

  Each field's value renders as `str(value)`, with `None`
  substituted by the literal `(none)`. None of the rows above carry
  a list-typed field, so list rendering for the tail/grep format
  is unspecified here. Adding a new event class without a row in
  this table is a spec change. The summary is for human reading;
  machine consumers should parse the journal directly.

  Tab and newline characters inside any rendered value are replaced
  with a single space before being printed so the tab-separated
  line stays splittable. The only fields where this can fire in
  practice are user-controllable free-text strings
  (`ConfigSetEvent.value`, `LockAcquiredEvent.reason`,
  `LockReleasedEvent.reason`); the kit's own event emitters never
  produce embedded tabs or newlines.

Tab-separation keeps fields cleanly splittable by `cut -f` and `awk`
without us shipping a JSON dependency.

`explain` prints a multi-line block:

```
Event <N> of <total> in .wiki.journal/journal.jsonl
Type:      <type>
Timestamp: <iso>
By:        <by>

<field-name>: <value>
<field-name>: <value>
...
```

The journal path in the header is always the literal string
`.wiki.journal/journal.jsonl` (the well-known vault-relative path),
not a cwd-dependent rendering. Keeps the AC string-comparable and
the message stable across machines. (Like the other vault-bound
handlers, these readers require the cwd to *be* the vault root —
they don't walk up the tree — so the literal vault-relative path
is also the absolute-relative path the user would type.)

Field names are the Pydantic model's field names from
`model.model_fields`, skipping the three already-printed
(`timestamp`, `by`, `type`). Each value renders as: `str(value)` for
scalars, `, ` (comma-space) join for lists, the literal `(none)` for
`None`. The same tab/newline→space substitution rule from the
tail/grep summary applies to each rendered value so a `\n` in
`ConfigSetEvent.value` (or any other free-text field) doesn't break
the block's "one field per line" shape. Because field names are the
model attribute names, a future Pydantic field rename is a
user-visible change requiring a spec edit — see §Invariants.

Exit codes: `0` on success (including "no matches" for `grep`), `2`
(`WIKI_ERROR_EXIT`) on invariant violation surfaced via `WikiError`.

## Behavior

### `tail`

1. Resolve `Path.cwd()`, assert `.wiki.journal/journal.jsonl` exists.
2. Stream the file once, parsing each non-blank line via
   `journal.parse_event_line(raw, line_no)` and recording
   `(line_no, event)` pairs as we go. Strict mode — any line that
   fails JSON parsing or schema validation raises
   `JournalCorruptError`, which we surface as `WikiError` naming
   the offending line. Single-pass parsing rules out the read /
   re-walk race a two-step approach would risk under a concurrent
   writer.
3. The blank-line rule matches `journal._parse_line` exactly
   (`raw.strip() == ""`), so whitespace-only lines, CRLF line
   endings, and a final line without trailing newline all collapse
   to the same blank-vs-non-blank decision the strict reader makes
   — the mapping is well-defined for hand-edited journals.
4. Take the last `min(N, len(pairs))` pairs, preserving order.
5. Print each formatted event line to stdout.

If the journal is empty (or contains only blank lines), print nothing
and exit `0`.

### `grep`

1. Same vault + load pre-flight as `tail` (same single-pass parse
   producing `(line_no, event)` pairs).
2. Filter events by `type` if `--type` was passed. An unknown type
   produces zero matches and exits `0` — see §Edge cases.
3. Filter remaining events: keep those whose canonical JSON contains
   `pattern` as a substring (case-sensitive). The canonical JSON is
   produced by a new public helper `journal.dump_event_json(event) ->
   str` that wraps the module's existing `_EVENT_ADAPTER.dump_json`
   so callers don't reach into private state. The bytes match the
   on-disk line exactly (less the trailing newline).
4. Print each match (in chronological order) using the same format as
   `tail`. Print nothing if no matches; exit `0`.

The substring match is against the canonical JSON the journal writer
emits, not the format string. That way `wiki journal grep alice.md`
finds page events whose `path` field is `"people/alice.md"` even
though `alice.md` doesn't appear in the summary's short-form output.
The timestamp is also part of that JSON, so `wiki journal grep
2026-05-15` matches every event on that ISO date — useful for
date-scoped audits, intentional, and documented here so it isn't a
surprise.

### `explain`

1. Same vault + load pre-flight as `tail`.
2. Parse the positional argument as a 1-based integer ≥1; reject
   non-integers and ≤0 with `WikiError`.
3. Build an absolute-file-line → event mapping from the same
   single-pass parse used by `tail`/`grep`. Blank lines contribute
   no entry, so a lookup miss collapses "blank line in range" and
   "line past EOF"
   to the same condition — both fall through to step 4's error.
4. If no event lives at that file line — because the file has fewer
   lines, or the line is blank, or the line is past EOF — raise
   `WikiError("no event at line N (journal has K events)")` where
   `N` is the user's input and `K` is the loaded event count. The
   message intentionally does *not* claim a contiguous valid range
   (a hand-edited journal with mid-file blanks has a non-contiguous
   set of valid line numbers; promising `1..K` would be a lie for
   such journals). `K=0` covers the empty-journal shape.
5. Print the multi-line block to stdout.

If the journal exists but is empty, every `explain N` produces the
same "no event at line N (journal has 0 events)" error.

### Edge cases

- **Empty journal** — `tail` prints nothing; `grep` prints nothing;
  `explain N` (for any `N≥1`) errors with `"no event at line N
  (journal has 0 events)"`. One message shape covers EOF-overshoot,
  blank-in-range, and empty-journal — see §Behavior/explain step 4.
- **Journal with only blank lines** — same as empty (the strict loader
  skips blank lines; the single-pass parse in `tail`/`grep` makes the same
  decision so the two stay in sync).
- **Corrupt journal** — `JournalCorruptError` from
  `journal.parse_event_line` propagates as `WikiError` with the
  offending line number. We don't fall back to `read_events_lenient`;
  that's `wiki doctor`'s job per ADR-0002 §Negative ("Concurrent
  writers require an advisory lock" and the strict-vs-lenient split
  — see also `journal.read_events_lenient`'s docstring).
- **N larger than journal** for `tail` — print every event, exit `0`.
- **`grep --type X` with unknown X** — zero matches, exit `0`. We
  don't enumerate the literal set in the help text because adding a
  new event type would silently desync the help; the user can discover
  types via `wiki journal tail`.
- **`explain N` where file line N is blank** — same `"no event at
  line N (journal has K events)"` message as out-of-range. `<line>`
  numbers preserve absolute file positions; `tail`/`grep` only emit
  numbers for non-blank lines, so a blank-line address can only
  arise from a hand-typed argument.

### Error cases

- Vault missing → `WikiError("not a wiki vault: …")` (matches
  `wiki doctor` / `wiki add` / `wiki ingest`).
- Corrupt journal → `WikiError("journal corruption at line N: <reason>")`.
- `tail -n 0` or `tail -n -5` or `tail -n abc` →
  `WikiError("--lines must be a positive integer")`. The argparse
  declaration intentionally takes the value as a string and validates
  it in the handler so the user sees this one message for every
  invalid `-n`, instead of mixing argparse's stderr usage line with
  the kit's own error shape.
- `grep ''` → `WikiError("grep pattern must be non-empty")`.
- `explain 0`, `explain abc`, `explain -5`, `explain 999` →
  `WikiError("event must be a positive integer")` for the non-integer
  or ≤0 case, and `WikiError("no event at line N (journal has K
  events)")` for "valid integer, but no event lives there"
  (EOF-overshoot, blank-in-range, and empty-journal all collapse to
  this one shape; `K=0` for empty). The argparse declaration also
  takes a string here so the handler can emit these messages, not
  argparse's.

## Invariants

- **Read-only.** No reader appends a journal event, writes a holder
  file, or mutates any path under the vault. Tests assert the journal
  file's *content* (byte-for-byte) and size are unchanged across an
  invocation. (Mtime alone would be flaky on filesystems with coarse
  mtime granularity; content equality is the stronger and more stable
  invariant.)
- **Line-number stability.** The 1-based line number `tail` and `grep`
  print is the same number `explain` accepts. A user piping
  `wiki journal grep foo | head -1 | awk '{print $1}' | xargs wiki journal explain`
  works.
- **`explain` field labels track Pydantic field names.** Renaming a
  field on any `_EventBase` subclass changes user-visible `explain`
  output; such a rename therefore requires a spec edit in this PR,
  not just a model edit.
- **No silent corruption swallowing.** All three commands use the
  strict path (`journal.parse_event_line`, same validator as
  `read_events`); the lenient reader is reserved for `wiki doctor`
  (ADR-0002 §Negative and `journal.read_events_lenient`'s docstring).
- **Exit code 0 includes "no matches".** Standard grep convention; CI
  scripts that pipe `grep` to `wc -l` should not see a non-zero exit
  when the journal happens to be quiet.

## Contracts with other modules

- Callers: a human at the terminal; `wiki doctor`-style follow-up
  workflows that pipe `grep` output to `xargs explain`.
- Calls: `journal.parse_event_line` and `journal.dump_event_json`
  only. No write paths, no `journal.transaction`, no holder-file
  inspection, no use of `journal.read_events_lenient`.
- Journal: read once per invocation; no events appended.

## Acceptance criteria

- [ ] `wiki journal tail` on a fresh empty vault prints nothing and
  exits 0.
- [ ] `wiki journal tail -n 3` on a vault with 10 events prints the
  last 3 in chronological order with the documented format.
- [ ] `wiki journal tail` without `-n` defaults to 10.
- [ ] `wiki journal tail -n 100` on a 5-event journal prints all 5
  (no padding, no error).
- [ ] `wiki journal tail -n 0` raises `WikiError` and exits 2.
- [ ] `wiki journal grep ingest` matches events whose JSON contains
  the substring; non-matches are filtered out.
- [ ] `wiki journal grep --type page.write foo.md` filters by type
  before substring.
- [ ] `wiki journal grep --type bogus.type foo` exits 0 with no
  output (unknown type is not an error, per §Edge cases).
- [ ] `wiki journal grep nothingmatches` exits 0 with no output.
- [ ] `wiki journal grep ''` raises `WikiError` and exits 2.
- [ ] `wiki journal explain 1` on a vault with at least one event
  prints the multi-line block describing event 1.
- [ ] `wiki journal explain 999` (out of range) raises `WikiError`
  whose message is `"no event at line 999 (journal has K events)"`
  and exits 2. The same message shape covers blank-in-range and
  empty-journal.
- [ ] `wiki journal explain abc` (non-integer) raises `WikiError`
  and exits 2.
- [ ] All three commands raise `WikiError` when run outside a vault.
- [ ] All three commands propagate `JournalCorruptError` as a
  `WikiError` whose message names the offending line.
- [ ] The journal file's content and size are unchanged across any
  reader invocation (mtime is intentionally not asserted — see
  §Invariants).

## Non-goals

- **`--json` / machine-readable output.** Deferred until a concrete
  caller needs it. The substring format is documented but not a parse
  contract; a future `--json` is a clean additive change.
- **Multi-line pattern matching, regex, `-i` flag.** Substring is the
  cheapest thing that lets a user find an event; regex is YAGNI until
  a use case lands. Users can `grep` the file directly for advanced
  cases.
- **Filtering `tail` by type.** Use `grep --type T` for that.
- **Color output, pagination, `--follow`.** A terminal pager (`less`)
  and shell history cover the human ergonomics; we don't ship our own.
- **Hash-based addressing for `explain`.** Events don't carry a content
  hash today; adding one is out of scope.
- **Counting / aggregation (`--count`, `--unique`).** Not a query
  language.

## Constraints

- **Scope budget (positive).** The only kit-side surface changes this
  spec authorizes are:
  - Three handlers in `llm_wiki_kit/cli.py` replacing the existing
    `_stub("journal …")` callsites.
  - Pure formatting helpers (`_format_event_line`,
    `_format_event_block`, an event-type→summary table) co-located
    with the handlers.
  - Two new public helpers on `journal`: `dump_event_json(event) ->
    str` (used by `grep`'s substring match) and
    `parse_event_line(raw, line_number) -> Event | None` (used by
    the reader handlers to parse the journal in a single pass —
    eliminates the read/re-walk race that a separate `read_events`
    call + file re-walk would risk under a concurrent writer). Both
    are thin public wrappers over existing module-private state and
    do not introduce a new module boundary.
  - Two argparse-shape edits on the existing `journal` subparser: a
    new `--type` flag on `grep`; the `event_id` positional on
    `explain` renamed to `event` and re-typed as `str`. The `-n`
    argument on `tail` also moves to `str` so the handler can emit
    the spec-mandated error.
  - One new test file `tests/unit/test_journal_readers.py`.
  - One small `SUBCOMMANDS_WITH_ARGS` edit in
    `tests/unit/test_cli.py` to drop the three stub assertions.

  Anything beyond this set is out of scope and lands in a separate
  PR.
- **No new runtime dependency.** The kit's deps stay `pyyaml`,
  `pydantic`, stdlib. No `rich`, no `jq`, no `click` (the rest of the
  CLI uses `argparse`).
- **No new module boundary.** Handlers live in `llm_wiki_kit/cli.py`
  beside the other read-only command handlers. A `journal_format.py`
  helper module is not justified by the size of this change.
- **No new top-level directory.**
- **No bypass of `write_helper.safe_write()`** — readers don't write,
  so this is automatic; restated to match the AGENTS.md checklist.
- **No new public CLI verb beyond `journal tail|grep|explain`.** The
  `journal` subparser already exists from Task 2.
- **No change to the journal's on-disk format or schema.** The
  readers consume what `journal.append_event` produces and no more.
- **No change to `journal.read_events`' signature.** The line-number
  recovery is a local helper inside `cli.py` that uses
  `parse_event_line`; broadening a core API
  for one caller would couple the journal module to a CLI concern.
