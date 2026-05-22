# Spec: task-17-wiki-run

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit/run.py`, `llm_wiki_kit/cli.py:_cmd_run`
- **Related:** RFC-0001 Task 17, ADR-0002 (journal), ADR-0004 (safe-write),
  ADR-0005 (Pydantic), `docs/specs/task-17-wiki-run/plan.md`
- **Constrained by:** ADR-0002, ADR-0004, ADR-0005, RFC-0001 §"Runtime
  constraints" (no new runtime deps without an ADR).

## What this is

`wiki run <operation> [--key=value ...]` is the CLI subcommand that
makes an installed operation primitive runnable. The kit's job is to
load the operation's `contract.yaml`, validate the user-supplied
arguments against the contract's declared inputs, journal one
`operation.run` event recording the dispatch, and print a one-line
pointer at the vault-side `SKILL.md` that performs the work. The kit
ships no LLM (charter principle: library-not-application); the actual
synthesis — reading vault pages, writing the digest, etc. — runs inside
the user's Claude session when it picks up the journaled dispatch and
opens the named skill. `wiki run` is therefore the deterministic
**dispatch boundary**, structurally parallel to `wiki ingest`:
validate, journal, surface guidance, exit.

## Inputs

CLI invocation: `wiki run <operation> [<arg> ...]` where each `<arg>`
is `--<name>=<value>` (or just `--<name>` for booleans set to true).

- `<operation>` — a kebab-case operation primitive name. Must be
  present in `replay_state(events).installed_primitives` and must
  resolve to an on-disk `templates/operations/<operation>/contract.yaml`
  under the running kit root (`cli._kit_paths`).
- `<arg>` tokens — zero or more, each `--<name>=<value>` or `--<name>`.
  Captured raw via `argparse.REMAINDER` (intentional — see §"Interaction
  with the global `--verbose` flag") so the operation contract — not
  argparse — drives the name/type vocabulary. **Kebab/snake
  normalisation:** the parser lower-cases the name and replaces `-`
  with `_` before matching against `contract.inputs`, so both
  `--include-open-ended` and `--include_open_ended` resolve to the
  on-disk snake_case field `include_open_ended`. This is a one-way
  translation; the kit never emits kebab. Comma (`--sources=a,b,c`)
  is the list separator.
- Vault root: `Path.cwd()`. Must contain `.wiki.journal/journal.jsonl`.
- Kit root: resolved via the standard `cli._kit_root()` /
  `args.kit_root` seam.

## Outputs

- **Journal append.** Exactly one `OperationRunEvent` per invocation
  that survives the "operation exists + is installed" check:

  ```
  type: "operation.run"
  timestamp: <UTC now>
  by: "wiki-run"
  operation: "<operation>"
  status: "dispatched" | "invalid_args"
  period: <contract.period or None>
  produced_pages: []                   # see §Invariants
  args: {<name>: <raw-value>, ...}     # what the user typed
  error: <one-line message> | None     # set iff status=="invalid_args"
  ```

  `args` and `error` are **additive Pydantic fields** on the existing
  `OperationRunEvent` (defaults: `{}` and `None`), so older journal
  lines from prior Task-3 / Task-16 vaults keep replaying without
  edit — the additive-schema rule in ADR-0002 §Negative covers this
  exact extension shape. `args` carries the user-supplied tokens
  verbatim (post-`_parse_op_args`, **pre-coercion**, **pre-default**)
  so `wiki doctor` / `journal explain` can answer "what did the user
  try" without rerunning coercion. Contract defaults are not stored
  on the event (they are derivable from `contract.yaml` plus the
  operation primitive's pinned version in `installed_primitives`).

- **stdout / stderr.**
  - On `status="dispatched"` (success): one stdout header line —
    `Dispatched <operation>. Run \`<skill>\` in your Claude session.`
    (skill name = `contract.skill` if non-empty, else `<operation>`).
    Then one stdout line per effective input (user-supplied **plus**
    contract defaults), sorted by name, formatted as
    `  <name>=<value>` with these rendering rules:
    - **string** and **iso_week**: the value verbatim.
    - **boolean**: lowercase `true` / `false` (symmetric with the
      input vocabulary).
    - **integer** / **int**: `str(int(value))` — decimal, no
      separators.
    - **list**: each element rendered per its element type
      (defaulting to `str()`), comma-joined with no spaces, no
      surrounding brackets, no quoting. An empty list renders as
      the empty string after the `=`.
    - **unknown type** (forward-compat catch-all): `str(value)`.

    Empty effective-inputs dict produces no follow-on lines. Exit
    code `0`. The on-screen format is for operator confirmation;
    the journal `args` field carries the audit-truthy record.
  - On `status="invalid_args"`: one stderr line naming the first
    validation failure. The line **must** contain both the offending
    `--<name>` token (in its kebab-spelled-as-typed form) and a
    short identifier of the expected shape: for type-coercion
    failures, the expected type tag (`iso_week`, `integer`,
    `boolean`); for unknown argument names, the literal substring
    `unknown argument`. Example:
    `wiki run weekly-digest: --window: expected iso_week (YYYY-Www), got '2026-20'`.
    The journal event is still appended, carrying the user-typed
    tokens in `args` and the message in `error` so `wiki doctor` /
    `journal explain` can reconstruct intent (CT-4 / CT-5 pin the
    substring contract). Exit code `WIKI_ERROR_EXIT` (2). The two
    failure modes are (a) **type coercion failure** — supplied value
    doesn't match the field's declared `type:`; and (b) **unknown
    argument name** — `--<name>` (after kebab→snake normalisation)
    does not appear in `contract.inputs`. Missing arguments do
    **not** produce this status — the kit does not enforce
    required-ness (see §Behavior step 8 and §Non-goals).
  - On unknown / not-installed operation, missing vault, or missing
    contract.yaml: `WikiError` raised — message lands at the existing
    CLI boundary in `cli.main`. **No journal event** for these:
    the kit has no operation identity to anchor against.

- **No filesystem writes** other than the journal append. Operation
  outputs (digest pages, etc.) are written later by the vault-side
  skill via `safe_write` (ADR-0004), not by this command.

## Behavior

### Happy path

1. Parse argv. argparse pulls the leading `<operation>` token; the
   trailing `<arg>` tokens are captured as a raw list via
   `nargs=argparse.REMAINDER`. **CLI-level `--help` short-circuit:**
   the `_cmd_run` handler scans `args.op_args` for an op-arg token
   that is *exactly* `--help` or `-h` (no `=`, no other characters);
   if found, it calls the `wiki run` subparser's `print_help()` and
   returns `0` without invoking `run.dispatch` and without
   journaling. Value-form tokens like `--help=false` are NOT
   short-circuited — they flow through `dispatch` and land as
   "unknown argument" `invalid_args`. The short-circuit is
   single-sourced at the CLI boundary; `run.dispatch` does not
   itself handle `--help`.
2. Resolve vault root and journal path; raise `WikiError` if not a
   vault.
3. Replay the journal to a `VaultState`; raise `WikiError` if
   `<operation>` is not in `state.installed_primitives` (message
   lists the installed operation names as a hint). No journal write.
4. Resolve the operation primitive's **kind** by looking it up in
   the discovered catalog (`load_primitive(core_dir)` plus
   `discover_primitives(templates_dir)`, same pattern as
   `_cmd_ingest`). If `<operation>` is not in the catalog at all, or
   the primitive's `kind` is not `operation`, raise `WikiError` with
   the discovered kind named (e.g. `'meeting' is installed but its
   kind is 'content-type', not 'operation'`). No journal write.
5. Locate `templates/operations/<operation>/contract.yaml` under the
   resolved kit root; raise `WikiError` with the absolute path if it
   is missing (kit-version skew between journal and templates). No
   journal write.
6. Load the contract via the existing `OperationContract` Pydantic
   model (`yaml.safe_load` → `model_validate`).
7. Parse the raw `<arg>` tokens against `contract.inputs`:
   - Each token must match `--<name>=<value>` or `--<name>` (boolean
     true). A token that doesn't match raises `WikiError("malformed
     argument: '<token>': expected --name=value")` (no journal event;
     no operation work has begun).
   - Apply kebab→snake normalisation to `<name>` (lower-case,
     `-` → `_`) before matching against `contract.inputs`. The
     parsed dict's keys are the normalised names; the journal's
     `args` field uses these normalised keys.
   - Unknown `<name>` (not in `contract.inputs` post-normalisation)
     → see "Edge cases".
   - **Error-precedence rule.** Walk the parsed dict in its
     iteration order — equivalent to the order each **input name**
     first appears in the user's typed token list, after kebab→snake
     collapse. (Python dict preserves first-insertion order, so a
     later `--name=value` overrides an earlier one's value but does
     not change the name's iteration position.) For each
     token, first check whether the normalised name is in
     `contract.inputs`; unknown → `invalid_args` with an
     "unknown argument" error message and short-circuit. Known →
     run `_coerce_input`; coercion failure → `invalid_args` with a
     type-mismatch error and short-circuit. **The first failing
     token wins**, regardless of failure kind. CT-16 pins this.
   - Coerce `<value>` per the field's declared `type`:
     - `string` → str.
     - `integer` (alias `int`) → `int(value)`; on `ValueError`, fail.
     - `boolean` → accept `true`/`false`/`yes`/`no`/`1`/`0`/`on`/`off`
       (case-insensitive). Bare `--flag` ⇒ `True`.
     - `iso_week` → str matching
       `^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$`. The bounded week-number
       group rejects `W00` and `W54`–`W99` (cheap format-level
       defence). Calendar validity (whether a given year actually
       has W53) is **not** checked — see §Non-goals.
     - `list` → comma-split into list[str]; surrounding whitespace
       per element is stripped; an entirely empty input string
       collapses to `[]`; **empty mid-string elements** (e.g.
       `a,,b`) preserve as empty strings in the list (→ `["a", "",
       "b"]`). The `items:` declaration on the field (e.g.
       `items: content-type`) is **documentation-only** at this
       boundary — elements are not further validated against
       installed primitives.
     - Anything else → accept as str. This catch-all already covers
       one in-production type tag: `type: page` (used by
       `trip-prep/contract.yaml` for a `trip:` input). The kit
       coerces it to a string; the SKILL is responsible for
       resolving the wikilink. Future tags (`type: path`,
       `type: date`, etc.) compose the same way without a kit
       change. Documented under §Non-goals.
8. Apply defaults: any field in `contract.inputs` whose `default:` is
   **not None** and that the user did not supply uses the default
   verbatim (already typed by Pydantic when the contract loaded). An
   explicit `default: null` in the YAML loads as Python `None` and
   is treated the same as omitting the `default:` key — i.e. no
   default is applied; the field is simply absent from the effective
   inputs (this is a deliberate spec choice: `None` is not a useful
   coerced value for any of `iso_week`, `list`, `boolean`, or
   `integer`, and conflating "absent" with "explicitly null" would
   force the SKILL to disambiguate them).
9. **Do not enforce required-ness.** A field with no effective
   default and no user-supplied value is simply absent from the
   effective inputs dict. The vault-side SKILL is the actor that
   decides whether the absence is fatal (and may compute a runtime
   default — e.g. `weekly-digest`'s `window` defaults to "the most
   recent complete week" per its contract `description:`). The kit's
   job is shape validation, not semantic validation. `optional: true`
   in the contract is documentation-only metadata at this boundary.
10. Append `OperationRunEvent(status="dispatched",
    period=contract.period, produced_pages=[],
    args=<raw-tokens-dict>, error=None)`.
11. Print the dispatch line + effective inputs to stdout. Exit `0`.

### Edge cases

- **`wiki run` against an empty vault directory** — no journal file.
  Raise `WikiError("not a wiki vault: ...")` matching the existing
  `_cmd_add` / `_cmd_ingest` / `_cmd_doctor` message shape.
- **Operation primitive installed but `contract.yaml` is missing on
  disk** — kit / vault drift. Raise `WikiError("operation '<name>':
  no contract.yaml at <absolute-path>")`. No journal event. (We
  deliberately don't suggest `wiki doctor` here — `doctor.py` flags
  catalog/journal mismatches, and a deleted `contract.yaml` from an
  otherwise installed primitive is a different drift mode.)
- **Operation installed but kind is not `operation`** — raise
  `WikiError("'<name>' is installed but its kind is '<kind>', not
  'operation'")`. No journal event.
- **Unknown argument name** (`--frobnicate=x` against a contract that
  has no `frobnicate` field) — append
  `OperationRunEvent(status="invalid_args", ...)`, stderr names the
  field, exit `WIKI_ERROR_EXIT`. Rationale: the operation identity is
  valid; the user's *attempt* is recorded so `wiki doctor` and a
  future `journal explain` can see "they tried to run weekly-digest
  with --frobnicate".
- **Repeated argument name** (`--window=a --window=b`) — last wins.
  Same convention as POSIX getopt; consistent with how a sloppy
  human-typed command works.
- **Boolean as `--flag=value`** — supported alongside bare `--flag`.
  Bare `--flag` ⇒ `True`. `--flag=false` ⇒ `False`.
- **List input with one element and no comma** — produces a
  single-element list, not a scalar.
- **Contract with no `inputs:` block** (empty dict) — accept zero
  args; reject any user-supplied arg as unknown.
- **`contract.skill` absent or empty string** — the dispatch line
  names the operation itself as the skill (operations conventionally
  ship a SKILL.md under `<primitive>/files/skills/<operation>/SKILL.md`
  with the same name). CT-13 pins this.
- **`--help` / `-h` after `<operation>`** — REMAINDER would otherwise
  consume them as op-args. The CLI pre-scans `args.op_args` and, if
  any token is **exactly** `--help` or `-h` (no `=`, no other
  characters), invokes the `wiki run` subparser's help and exits
  `0` without journaling. The short-circuit fires regardless of
  position and regardless of malformed sibling tokens (e.g.
  `wiki run weekly-digest banana --help` exits `0`, prints help,
  no journal). Value-form tokens like `--help=false` are NOT
  short-circuited; they flow through `dispatch` and produce
  `invalid_args` (`help` is not in any contract's `inputs`).
- **Other global flags after `<operation>`** (e.g. `--verbose`) —
  consumed by REMAINDER and produce `invalid_args` (`verbose` is
  not in any contract's `inputs`). See §"Interaction with the
  global `--verbose` flag".
- **Malformed token alongside otherwise-validatable tokens** — if
  any token fails the `--<name>=<value>` / `--<name>` shape check
  in `_parse_op_args`, the parse aborts before validation and **no
  journal event is written**, regardless of how the other tokens
  would have classified. The journal records what the user tried
  only on calls where the kit got far enough to know what
  operation was being run *and* the user's intent was parseable as
  arguments.

### Error cases

- Argv-shape errors (malformed `--name=value`, unknown operation,
  missing contract.yaml) → `WikiError`, caught at the existing CLI
  boundary; no journal event.
- Argument validation errors (type mismatch on a supplied value,
  unknown argument name) → `OperationRunEvent(status="invalid_args")`
  + stderr line + exit `WIKI_ERROR_EXIT`. These are journaled because
  the operation identity is resolved before validation runs. Missing
  arguments do not appear here — see §Behavior step 8.
- Journal append errors (fsync failures, lock-unavailable from a
  concurrent process) → propagate as `OSError` /
  `LockUnavailableError` per existing journal contract. Not Task 17's
  responsibility to catch.

### Interaction with the global `--verbose` flag

`wiki run` captures everything after `<operation>` via
`argparse.REMAINDER` so the operation's contract — not argparse —
governs the trailing vocabulary. Side-effect: a `--verbose` placed
**after** the operation name is consumed as an op-arg, not as the
global flag (and will then fail as an unknown argument against the
contract). The documented way to get tracebacks on a `wiki run` is
to put `--verbose` **before** the subcommand: `wiki --verbose run
weekly-digest --window=2026-W20`. `WIKI_DEBUG=1` also works, since
the env var is read independent of argparse.

## Invariants

- One CLI invocation appends **at most one** `OperationRunEvent`. Zero
  events on the "operation does not resolve" paths and on the
  `--help` / `-h` short-circuit and on the malformed-token raise
  inside step 7; exactly one on every path that gets past step 7
  (argument parse + per-token validation loop). Steps 1–6 are
  pre-load checks: a `WikiError` from any of them aborts without a
  journal write. Step 7's malformed-token raise also aborts without
  a journal write — the user's intent wasn't parseable as
  arguments, so we have nothing to record.
- The journal append is ordered **last** in the happy path: every
  validation that can fail runs first, so a journaled dispatch is a
  promise that the kit had everything it needed.
- `OperationRunEvent.by == "wiki-run"`. This identifies the dispatch
  vehicle; a later vault-side actor will appear in the `by` field of
  any follow-on `PageWriteEvent`s.
- `OperationRunEvent.period` is exactly the contract's `period`
  field (string or `None`). The kit does not synthesize a period.
- `OperationRunEvent.produced_pages == []` at dispatch (always; the
  journal is append-only and no later step rewrites this list).
- `OperationRunEvent.args` records the user-supplied tokens **pre-
  coercion**: a `dict[str, str]` keyed by snake_case-normalised
  name, values verbatim as the user typed them (after last-wins
  collapse — only the final occurrence's value survives per name).
  One exception: bare `--flag` tokens (no `=`) store the sentinel
  string `"true"` — the kit does not preserve the on-screen
  distinction between `--flag` and `--flag=true` in the journal.
  Contract defaults are **not** stored on the event.
- `OperationRunEvent.error` is non-None **iff** `status ==
  "invalid_args"`. The implementation enforces this via
  `DispatchResult.__post_init__`.
- No filesystem writes other than the journal append happen inside
  `wiki run`. The vault-side skill is the actor that creates pages.

## Contracts with other modules

- **`cli.py`** — `_cmd_run` wraps `llm_wiki_kit.run.dispatch`. Reads
  `args.operation`, `args.op_args`, `args.kit_root`. Uses the
  established `_kit_paths` / `_kit_root` accessor (qC8 spec).
- **`llm_wiki_kit.run`** — new module. Public signature is exactly:
  `dispatch(operation: str, raw_args: list[str], *,
  vault_root: Path, kit_root: Path, journal_path: Path,
  now: datetime) -> DispatchResult`. Keyword-only after `raw_args`
  so tests can wire up a `tmp_path` journal without round-tripping
  through vault discovery. Returns a `DispatchResult` on the
  "operation identity resolved" paths (success **and**
  `invalid_args`); raises `WikiError` on pre-load failures. Inner
  helpers (`_parse_op_args`, `_coerce_input`, `_load_contract`,
  `_resolve_operation_kind`) are pure functions tested directly.
  `dispatch` is responsible for its own catalog discovery
  (`load_primitive(core_dir)` + `discover_primitives(templates_dir)`);
  the CLI handler doesn't pre-load and pass it in. This accepts a
  single disk walk per `wiki run` invocation in exchange for a
  one-argument-list dispatch surface — same trade-off `wiki ingest`
  makes.
- **`llm_wiki_kit.journal.append_event`** — used once per surviving
  invocation. No `transaction` block needed — a single event append
  is already atomic under journal-locking step 1.
- **`llm_wiki_kit.models.OperationContract`** — read by `run.py`.
  Task 17 **tightens** `OperationContract.inputs` from
  `dict[str, object]` to `dict[str, OperationInputSpec]`, where
  `OperationInputSpec` is a new Pydantic model in `models.py`. Field
  set: `type: str` (required), `description: str | None`,
  `default: object | None`, `optional: bool = False`, `items: str | None`.
  This is a model-tightening change that all existing `contract.yaml`
  files must continue to validate against — verified by an audit
  step in plan §Steps.
- **`llm_wiki_kit.models.OperationRunEvent`** — already in the
  discriminated `Event` union from Task 3 with the literal
  `"operation.run"`. Task 17 **additively extends** the class with
  two fields whose defaults preserve old-journal-line replay
  (ADR-0002 §Negative's additive-schema rule):
  - `args: dict[str, str] = Field(default_factory=dict)`
  - `error: str | None = None`

  No new event class, no change to the discriminator literal. (The
  task brief suggested `operation_run` as the literal to keep
  distinct from Task 18's `research_query`; in practice both are
  already pinned in dot-form — `operation.run` and `research.query`
  — consistent with the `models.py` namespace docstring.)

## Acceptance criteria

The contract tests below define "done". Construction tests live in
`plan.md`.

- [ ] **CT-1: dispatch on the happy path.** Given an installed
  operation `weekly-digest` with `inputs.window:{type:iso_week}` and
  `inputs.sources:{type:list, items:content-type, default:[meeting]}`,
  `wiki run weekly-digest --window=2026-W20` (a) appends exactly one
  journal event, (b) the event is an `OperationRunEvent` with
  `operation=="weekly-digest"`, `status=="dispatched"`,
  `period=="weekly"`, `produced_pages==[]`, `by=="wiki-run"`,
  `args=={"window": "2026-W20"}` (raw, pre-coercion), `error is None`,
  (c) the returned `DispatchResult.parsed` equals
  `{"window": "2026-W20", "sources": ["meeting"]}` (effective
  inputs: user-supplied plus contract defaults), (d) exit code is `0`,
  (e) stdout contains `Dispatched weekly-digest`, names the
  operation's `skill:` field (`weekly-digest`), and includes
  `  window=2026-W20` and `  sources=meeting` on separate lines.

- [ ] **CT-2: default-fill.** Given a contract with
  `inputs.sources:{type:list, items:string, default:[meeting]}` and
  no user-supplied `--sources`, the parsed inputs include
  `{"sources": ["meeting"]}`.

- [ ] **CT-3: missing argument is dispatched, not failed.** Given a
  contract field with no `default:` and no `optional: true` (e.g.
  `weekly-digest`'s `window`), omitting the corresponding `--window`
  produces `OperationRunEvent(status="dispatched")` and exit `0`. The
  effective inputs dict has no `window` key — the kit does not
  enforce required-ness; the SKILL is responsible.

- [ ] **CT-4: type-mismatch is journaled as invalid_args.**
  `wiki run weekly-digest --window=banana` against a `type: iso_week`
  field produces one `OperationRunEvent(status="invalid_args")`, exit
  2. The journal event's `args == {"window": "banana"}` and `error`
  is a non-empty string. Stderr **contains** both the substring
  `--window` and the substring `iso_week` (anything else about the
  message is implementation-defined). The event's
  `args == {"window": "banana"}`.

- [ ] **CT-5: unknown argument is journaled as invalid_args.**
  `wiki run weekly-digest --frobnicate=x` against a contract with no
  `frobnicate` field produces one `OperationRunEvent(status=
  "invalid_args")`, exit 2. Stderr **contains** both the substring
  `--frobnicate` and the literal substring `unknown argument`. The
  journal event's `args == {"frobnicate": "x"}`.

- [ ] **CT-6: unknown operation is rejected before any journal
  write.** `wiki run nonexistent-op` raises `WikiError` (exit 2), the
  message lists the installed operations as a hint, and the journal
  has zero new events compared to before the call.

- [ ] **CT-7: operation installed but not of kind `operation`.**
  Given `meeting` (a `content-type`) appears in
  `installed_primitives`, `wiki run meeting` raises `WikiError`, no
  journal event is appended.

- [ ] **CT-8: not a vault directory.** Running `wiki run weekly-digest`
  in a directory without `.wiki.journal/journal.jsonl` raises
  `WikiError` with the standard "not a wiki vault" message; no
  journal event.

- [ ] **CT-9: malformed `--arg` token.**
  `wiki run weekly-digest banana --window=2026-W20` (a positional
  rather than `--name=value`) raises `WikiError` with a message
  pointing at the malformed token. No journal event.

- [ ] **CT-10: existing operation contracts revalidate.** Every
  `templates/operations/*/contract.yaml` loads cleanly under the
  tightened `OperationContract` model. This is the regression check
  on the model change.

- [ ] **CT-11: boolean coercion and kebab→snake normalisation.**
  Given `inputs.include_open_ended:{type:boolean, default:true}`
  (snake_case on disk), `--include-open-ended=false` (kebab,
  normalised) parses to `False`; `--include-open-ended` (bare)
  parses to `True`; the snake-spelled `--include_open_ended=false`
  also parses to `False` (both spellings reach the same field); and
  omitting the arg fills from the contract default to `True`. CT-11
  is the canonical kebab/snake test.

- [ ] **CT-12: list coercion.** Given `inputs.sources:{type:list,
  items:string}`, `--sources=a,b,c` parses to `["a", "b", "c"]`;
  `--sources=a` parses to `["a"]`; `--sources=` parses to `[]`;
  `--sources=a , b ,c` parses to `["a", "b", "c"]` (whitespace
  around commas is stripped).

- [ ] **CT-13: `skill:` fallback.** Given a fixture operation
  contract that omits the `skill:` key (or sets it to the empty
  string), the dispatch stdout line names the operation itself as
  the skill — `Dispatched my-op. Run \`my-op\` in your Claude session.`

- [ ] **CT-14: `--help` short-circuit.** Each of
  `wiki run weekly-digest --help`, `wiki run weekly-digest -h`,
  `wiki run weekly-digest --window=2026-W20 --help`, and
  `wiki run weekly-digest banana --help` (malformed positional
  alongside `--help`) exits `0`, prints the `wiki run` subparser
  help, and does **not** append a journal event. Conversely,
  `wiki run weekly-digest --help=false` does **not** short-circuit:
  it exits `2` with `invalid_args` and a journal event whose
  `args == {"help": "false"}`.

- [ ] **CT-15: `OperationRunEvent` schema is backward-compatible.**
  A literal pre-extension journal line of the form
  `{"type": "operation.run", "timestamp": "2026-05-15T00:00:00+00:00",
  "by": "wiki-run", "operation": "weekly-digest", "status":
  "dispatched", "period": "weekly", "produced_pages": []}` (no
  `args`, no `error`) validates under the extended Pydantic model
  via `_EVENT_ADAPTER.validate_json` and loads as `event.args == {}`
  and `event.error is None`. Round-tripping the parsed event back
  through `model_dump_json` produces JSON that, when reparsed,
  yields an equal event. Pins ADR-0002's additive-schema rule for
  this PR's extension.

- [ ] **CT-16: error-precedence is "first-occurring input name
  wins".** Given a contract with `inputs.window:{type:iso_week}`
  and no `frobnicate` field:
  - `wiki run weekly-digest --frobnicate=x --window=banana`
    produces `invalid_args` with `error` naming `--frobnicate`
    (the first-occurring name is `frobnicate`).
  - `wiki run weekly-digest --window=banana --frobnicate=x`
    produces `invalid_args` with `error` containing `--window` and
    `iso_week` (the first-occurring name is `window`).
  - `wiki run weekly-digest --window=2026-W20 --frobnicate=x
    --window=banana` produces `invalid_args` with `error`
    containing `--window` and `iso_week`. This is the **last-wins-
    on-value, first-position-on-name** case: `window` first appeared
    at position 1, so it wins iteration order; its effective value
    after last-wins collapse is `banana`, which fails coercion. The
    later `--frobnicate=x` never gets evaluated.

  In all cases the event's `args` field captures every user-
  supplied token (after collapse — so the three-token case stores
  `{"window": "banana", "frobnicate": "x"}`, not the intermediate
  `2026-W20`). The journal records what the user *effectively*
  asked for, with each name's value resolved by last-wins.

## Non-goals

- **Executing the operation's work.** `wiki run` does not read vault
  pages, summarize them, or write the digest. The vault-side skill
  does, inside the user's Claude session.
- **A second journal event for completion.** The `OperationRunEvent`
  records dispatch only; whether the work succeeds is reconstructed
  from any `PageWriteEvent`s the skill subsequently emits. A
  follow-up event class for "operation succeeded / failed" is a
  later RFC if we ever need it.
- **Output schema validation.** The `contract.outputs` block
  describes where pages will land and what they look like. `wiki
  run` does not enforce it; the skill (via `safe_write`) does.
- **Bidirectional arg-name translation.** The CLI translates user-
  typed kebab to the contract's snake_case **one-way** (so both
  `--include-open-ended` and `--include_open_ended` reach the same
  field). The reverse direction — emitting kebab in error messages
  when the on-disk spelling is snake — is **not** in scope; messages
  echo the user's typed spelling verbatim so the operator sees what
  they wrote.
- **`--list` / `--describe` introspection flags.** `wiki run
  weekly-digest --help` showing the contract's input fields is nice
  to have; deferred unless trivial.
- **Subprocess invocation.** Operations are SKILL.md + contract;
  there is no Python entry point to call. A future contract field
  (`entrypoint:`) could add one, but that's an RFC.
- **Strict type-tag enumeration.** Unknown `type:` values in the
  contract pass through as `str`. We'd rather forward-compat new
  type names than fail a contract whose author added `type: path`
  before the kit grew a coercion for it.
- **`items:` enforcement on `type: list`.** The `items:` declaration
  (e.g. `items: content-type`) is documentation-only at this
  boundary. Lists coerce to `list[str]` regardless; the SKILL is
  responsible for any further per-element validation.
- **ISO-week calendar validity.** The kit's `iso_week` coercion is
  a regex match on `^\d{4}-W\d{2}$`; it does not reject `2025-W53`
  in a 52-week year. Calendar-true validation belongs in the SKILL.
- **`default: null` as a distinct state.** An explicit `default:
  null` in a contract YAML is treated identically to omitting the
  `default:` key. Future RFC may add a sentinel; deliberately
  conflated for v0.1.
- **Stdout-renderer / journal-`args`-renderer symmetry.** The
  stdout renderer normalises typed values (boolean → lowercase
  `true`/`false`, integer → decimal `str(int)`); the journal's
  `args` field preserves the user's typed spelling verbatim
  ("YES", "0", "True"). A future `wiki doctor` or `journal explain`
  that re-renders an `OperationRunEvent` may pick its own
  convention. The asymmetry is deliberate: stdout is for the
  operator running the command *right now*; `args` is forensic
  evidence of what was typed.
- **Bare-flag round-trip fidelity in `args`.** A bare `--flag`
  serializes into `args` as `{"flag": "true"}`, indistinguishable
  from `--flag=true`. We don't preserve the bare-vs-value-form
  distinction; the kit treats them as semantically equivalent at
  parse-time, and the journal records semantics.
- **Per-operation lock.** The journal-locking spec already covers
  multi-event sequences; a single `operation.run` append does not
  need a `transaction()` block.
- **Required-arg enforcement.** The kit dispatches whatever the user
  supplied (plus contract defaults). It does not refuse a call for
  a missing field. Rationale: the vault-side SKILL is the actor that
  computes runtime defaults (date math, "latest week", "current
  user") and it has context the kit does not. A future contract-side
  `required: true` flag could opt back into kit-side enforcement;
  out of scope until a concrete need surfaces.
- **`--verbose` after `<operation>`.** Consumed as an op-arg by
  `argparse.REMAINDER` design. Use `wiki --verbose run ...` or
  `WIKI_DEBUG=1` instead. Working around this would require a
  pre-scan of REMAINDER that subtly differs from argparse's own flag
  semantics; not worth the surface area.

## Constraints

- No new runtime dependency.
- No new top-level directory.
- No new public CLI verb beyond `wiki run`.
- No bypass of `journal.append_event` for the dispatch record.
- No bypass of `write_helper.safe_write` for any subsequent file
  writes (out of scope for this PR but recorded so a reviewer flags
  drift if the scope creeps).
- No new event-type literal: `OperationRunEvent` already exists in
  `models.py` with `type=="operation.run"`. Don't add a parallel
  class.
- No retroactive edit of `OperationRunEvent.produced_pages` after
  append. The journal is append-only.
- No subprocess execution.
