# Plan: task-17-wiki-run

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** `docs/specs/task-17-wiki-run/spec.md`
- **Owner:** eugenelim (RFC-0001 Task 17)

## Approach

Three pieces of model-level work and one runtime module, in this
order:

1. **Tighten `OperationContract.inputs`** from `dict[str, object]` to
   `dict[str, OperationInputSpec]` in `llm_wiki_kit/models.py`. This
   is small but model-level — it must validate every existing
   `templates/operations/*/contract.yaml` byte-for-byte unchanged.
   Field choices come from a literal grep over the templates: `type`,
   `description`, `default`, `optional`, `items`. The model accepts
   both `type: int` and `type: integer` because both spellings appear
   on disk; the kit normalises at use-time, not at load-time, so the
   stored YAML is unaltered.

2. **Additively extend `OperationRunEvent`** with `args:
   dict[str, str] = {}` and `error: str | None = None`. Defaults
   keep older journal lines replaying unchanged (ADR-0002 §Negative).
   The fields turn the journal into a faithful audit record of every
   dispatch attempt — what the user typed, and (on `invalid_args`)
   why we rejected it.

3. **Add `llm_wiki_kit/run.py` + wire `_cmd_run` in `cli.py`.** The
   module's public surface is one function with this signature:
   ```python
   dispatch(
       operation: str,
       raw_args: list[str],
       *,
       vault_root: Path,
       kit_root: Path,
       journal_path: Path,
       now: datetime,
   ) -> DispatchResult
   ```
   Returns a `DispatchResult` on success **and** on `invalid_args`;
   raises `WikiError` on pre-load failures (no journal write). Inner
   helpers (`_parse_op_args`, `_coerce_input`, `_load_contract`,
   `_resolve_operation_kind`) are pure and tested directly.

All six implementation steps are **TDD-mode** (red→green→refactor).
The mechanical-gates step (Step 6) is the loop's verification gate,
not a separate mode.

Why this order: the model change is a tiny, focused, separately-
verifiable diff that catches any contract-file regressions before the
runtime code lands. If the audit step finds a contract that doesn't
fit the tightened schema, we adjust the model (not the contract)
before writing any of `run.py`.

Why dispatch-only (not subprocess and not in-process Python):
operations ship a `SKILL.md` plus a `contract.yaml`, not a Python
entry point. The kit ships no LLM (charter), so the "execution" of an
operation is necessarily Claude reading the SKILL and producing pages
via `safe_write`. `wiki run`'s job is the deterministic bookkeeping
boundary — validate, journal, point Claude at the skill — exactly the
same shape as `wiki ingest`.

### Declined patterns

- **A new event class `OperationDispatchFailedEvent`.** Tempting
  symmetry with `Routed`/`Ambiguous`/`NoMatch`. Declining — the
  status enum on `OperationRunEvent` plus the new `error` field
  carries the same information without a parallel class in the
  discriminated union.
- **Per-operation `argparse` subparsers built dynamically from the
  contract.** Would give us free `--help` text and type-coercion at
  argparse level. Declining — couples the kit to argparse's flag
  vocabulary forever, and the contract's `type:` namespace doesn't
  cleanly map onto argparse's. `_coerce_input` is the right seam.
- **A `--dry-run` flag** that validates args without journaling.
  Declining — out of scope for "make wiki run runnable" and absent
  from the spec.
- **A registry of type-coercion handlers.** Declining — five built-in
  types fit a five-branch dispatch; a registry is overhead for a
  trivial taxonomy.
- **Reading the contract's `outputs:` block.** Declining — the
  vault-side SKILL writes outputs via `safe_write`; the kit doesn't
  enforce their shape (per §Non-goals).

## Pre-conditions

- `llm_wiki_kit/models.py` has the `OperationContract` model and the
  `OperationRunEvent` event class from Task 3. ✓
- `llm_wiki_kit/journal.py` exposes `append_event` and `read_events`
  with the existing single-event atomicity guarantees. ✓
- `llm_wiki_kit/cli.py` has the `_kit_paths` / `_kit_root` accessor
  pattern, the `_cmd_run` stub, and the `wiki run` subparser stub. ✓
- `templates/operations/*/contract.yaml` exist on disk (Tasks
  11/13/14). ✓ — verified by `ls templates/operations/*/contract.yaml`
  before starting.
- The branch is off `origin/main` at the head of `4afda2b`
  (post-Task-16). ✓

## Steps

Each step is one verifiable goal. Tests come *before* the step's
implementation per CONVENTIONS § Contract tests vs. construction tests.

### Step 1 — `OperationInputSpec` accepts every existing contract YAML

**Tests** (construction):

- `tests/unit/test_operation_contract_inputs.py::test_loads_weekly_digest_contract`
  — parses `templates/operations/weekly-digest/contract.yaml` and
  asserts `contract.inputs["window"].type == "iso_week"` and
  `contract.inputs["sources"].default == ["meeting"]`.
- `tests/unit/test_operation_contract_inputs.py::test_loads_every_shipped_contract`
  — globs `templates/operations/*/contract.yaml`, parses each, and
  asserts no `ValidationError`. This is CT-10 from spec acceptance
  criteria.
- `tests/unit/test_operation_contract_inputs.py::test_int_alias_is_accepted`
  — feeds `{"type": "int", "default": 30}` into the model directly
  and asserts the field validates (both `int` and `integer` spellings
  must round-trip).
- `tests/unit/test_operation_contract_inputs.py::test_unknown_field_is_rejected`
  — feeds `{"type": "string", "frobnicate": true}` and asserts
  Pydantic raises (`_StrictModel` uses `extra="forbid"`).
- `tests/unit/test_operation_run_event_schema.py::test_legacy_event_replays`
  — pins CT-15 with a **literal JSON string** representative of a
  pre-extension journal line:
  ```python
  legacy = (
      '{"type":"operation.run","timestamp":"2026-05-15T00:00:00+00:00",'
      '"by":"wiki-run","operation":"weekly-digest","status":"dispatched",'
      '"period":"weekly","produced_pages":[]}'
  )
  event = _EVENT_ADAPTER.validate_json(legacy)
  assert event.args == {}
  assert event.error is None
  ```
  The string is committed verbatim so a future refactor that drops
  the defaults (or replaces them with `Field(...)` requiring
  presence) fails this test loudly.
- `tests/unit/test_operation_run_event_schema.py::test_extended_fields_round_trip`
  — constructs an `OperationRunEvent` with `args={"window":
  "2026-W20"}` and `error="--window: ..."`, serialises with
  `model_dump_json`, reparses, asserts equal.
- `tests/unit/test_operation_run_event_schema.py::test_status_literal_rejects_typo`
  — feeds `{"type":"operation.run", ..., "status":"dispached"}`
  into `_EVENT_ADAPTER.validate_python` and asserts Pydantic
  raises. Pins the Literal-tightening described in §Approach.

**Approach:**

- Add `OperationInputSpec(_StrictModel)` to `models.py` with fields:
  `type: str`, `description: str | None = None`,
  `default: object | None = None`, `optional: bool = False`,
  `items: str | None = None`.
- Change `OperationContract.inputs` from `dict[str, object]` to
  `dict[str, OperationInputSpec]`.
- **Additively extend `OperationRunEvent`** with two fields:
  `args: dict[str, str] = Field(default_factory=dict)` and
  `error: str | None = None`. Defaults are critical — without them
  ADR-0002's old-line-replay guarantee breaks. No change to the
  discriminator literal.
- **Tighten `OperationRunEvent.status`** from `str` to
  `Literal["dispatched", "invalid_args"]`. Backward-compat is
  preserved: the only `status` value any pre-Task-17 journal line
  can carry is `"dispatched"` (no previous code path emitted
  anything else), so the Literal narrowing rejects no legitimate
  legacy line. Pydantic raises on a journal that someone hand-
  edited to a third spelling — that's the desired "fail loudly"
  behaviour per ADR-0005.
- Update the module docstring's "OperationContract" reference if the
  prose names the inputs type; otherwise no other models.py change.
- The tightened model is consumed by `run.py` only — no other module
  reads `OperationContract.inputs` today (verified by grep).
- **`default: null` ambiguity.** Pydantic loads `default: null` (or
  an omitted `default:` key) as Python `None`. The spec
  (`spec.md:170-178`) treats both states identically — no default
  applied. The model itself does not need to distinguish them
  because no current contract uses `default: null`; the spec just
  formalises the behaviour the unsentineled field already produces.

**Done when:** every test in the Tests subsection above passes;
`ruff check llm_wiki_kit/`, `mypy llm_wiki_kit tests`, and the full
`pytest -m 'not slow'` suite are still green; no
`templates/operations/*/contract.yaml` was edited.

### Step 2 — `run._parse_op_args` parses raw CLI tokens into a dict

**Tests** (construction): `tests/unit/test_run.py::TestParseOpArgs`

- `test_name_value_pairs` — `["--window=2026-W20", "--theme=summer"]`
  → `{"window": "2026-W20", "theme": "summer"}`.
- `test_bare_flag_is_true` — `["--include-open-ended"]` →
  `{"include_open_ended": "true"}` (kebab→snake normalisation
  applied; the value `"true"` is a string sentinel — coercion is
  Step 3's job, not this one's).
- `test_kebab_and_snake_both_normalise` —
  `["--include-open-ended=false", "--theme=summer"]` →
  `{"include_open_ended": "false", "theme": "summer"}`. Also
  `["--include_open_ended=false"]` → `{"include_open_ended":
  "false"}` (already-snake input passes through unchanged).
- `test_uppercase_in_name_is_lower_cased` — `["--Window=x"]` →
  `{"window": "x"}` (normalisation is `name.lower().replace("-",
  "_")`).
- `test_repeated_name_last_wins` — `["--window=a", "--window=b"]` →
  `{"window": "b"}`. Mixed kebab/snake also collapse:
  `["--include-open-ended=a", "--include_open_ended=b"]` →
  `{"include_open_ended": "b"}`. Bare-flag/value-form
  interleaving: `["--include-open-ended", "--include-open-ended=
  false"]` → `{"include_open_ended": "false"}`; and the reverse
  `["--include-open-ended=false", "--include-open-ended"]` →
  `{"include_open_ended": "true"}`. Pins last-wins across all
  spelling and form mixes.
- `test_empty_value` — `["--sources="]` → `{"sources": ""}`.
- `test_positional_token_raises` — `["banana", "--window=x"]` raises
  `WikiError` whose message names `"banana"`.
- `test_lone_dash_dash_raises` — `["--"]` raises `WikiError`.
- `test_empty_name_raises` — `["--=value"]` raises `WikiError`
  (the `--` is the entire `<name>` segment; empty names are a
  malformed-token failure mode).
- `test_value_with_equals_inside` — `["--query=a=b"]` →
  `{"query": "a=b"}` (split on the first `=` only).

**Approach:**

- `_parse_op_args(tokens: list[str]) -> dict[str, str]`. Pure
  function; no contract awareness. Applies kebab→snake
  normalisation to each `<name>` (`.lower().replace("-", "_")`)
  before recording. Raises `WikiError` only for shape failures
  (positional token, `--` with nothing, `--=value` with empty
  name).
- The output key is the **normalised** snake_case name; the value
  is the user's raw string verbatim. Coercion (string → typed value)
  happens in Step 3.

**Done when:** every test in the Tests subsection above passes;
the function is reachable as `llm_wiki_kit.run._parse_op_args`
(underscore-prefixed but test-imported, matching the kit's
convention for "pure helpers we test directly").

### Step 3 — `run._coerce_input` maps (raw_value, OperationInputSpec) to a typed Python value

**Tests** (construction): `tests/unit/test_run.py::TestCoerceInput`

- `test_string` — coerces `"foo"` against `type:string` → `"foo"`.
- `test_integer_and_int_aliases` — both `"30"` against
  `type:integer` and `type:int` → `30`. `"banana"` raises
  `ArgCoercionError` (private exception class; see Approach).
- `test_boolean_truthy_and_falsy` — `"true"`, `"yes"`, `"1"`, `"on"`,
  `"TRUE"` all → `True`. `"false"`, `"no"`, `"0"`, `"off"` all →
  `False`. `"banana"` raises.
- `test_iso_week_format` — `"2026-W20"` → `"2026-W20"`. `"2026-20"`,
  `"2026-W3"`, `"banana"` all raise. `"2025-W53"` is accepted
  (regex matches; calendar validity is the SKILL's job — out of
  scope, see §Risks). **`"2026-W00"`, `"2026-W54"`, `"2026-W99"`
  all raise** — pinned because the tightened regex bounds the week
  group to `01–53`. The two-digit-week rule reflects ISO 8601
  (`W01`–`W53`).
- `test_list_comma_split` — `"a,b,c"` → `["a", "b", "c"]`;
  `"a"` → `["a"]`; `""` → `[]`; `"a , b ,c"` → `["a", "b", "c"]`
  (each element's surrounding whitespace stripped — pins CT-12's
  whitespace clause). An all-whitespace element coerces to `""`
  (e.g. `"a,,b"` → `["a", "", "b"]`).
- `test_unknown_type_passes_through_as_str` — coerces against a
  contract that says `type:path` → returns the raw string. Documented
  in spec §Non-goals.

**Approach:**

- `_coerce_input(raw_value: str, spec: OperationInputSpec) -> object`.
  Returns the Python value; raises a private `ArgCoercionError`
  carrying the field name placeholder and expected type so the
  caller can format a one-line message.
- The boolean truthy set is the same as `cli._WIKI_DEBUG_TRUTHY`
  union {"on"}: `{"1", "true", "yes", "on"}`; the falsy set:
  `{"0", "false", "no", "off"}`. Inlined here rather than imported —
  these are two different domains, and pulling them together would
  introduce a coupling we don't want.

**Done when:** every test in the Tests subsection above passes;
the function has no dependencies beyond `models.OperationInputSpec`
and Python stdlib.

### Step 4 — `run.dispatch` orchestrates load → parse → coerce → journal

**Tests** (construction): `tests/unit/test_run_dispatch.py`

- `test_happy_path_dispatched` — synth a fixture vault with the
  weekly-digest primitive installed (helper `_install_op_in_vault`),
  call `dispatch("weekly-digest", ["--window=2026-W20"], ...)`,
  assert: one `OperationRunEvent`, `status="dispatched"`,
  `period="weekly"`, `produced_pages == []`, returned
  `DispatchResult.parsed == {"window": "2026-W20", "sources":
  ["meeting"]}`.
- `test_missing_arg_is_dispatched_not_failed` — fixture with one
  non-defaulted, non-optional field; call with no args; assert one
  `OperationRunEvent(status="dispatched")`, exit-code-equivalent
  return path, and `DispatchResult.parsed` does NOT contain the
  missing field's key. This pins the spec's "no kit-side required
  enforcement" decision (CT-3).
- `test_type_mismatch_invalid_args` — call with `--window=banana`;
  assert one `OperationRunEvent(status="invalid_args")`, the event's
  `args == {"window": "banana"}`, `event.error` is non-empty and
  contains both `--window` and `iso_week`. Pins CT-4.
- `test_unknown_arg_invalid_args` — call with `--frobnicate=x`;
  assert one `OperationRunEvent(status="invalid_args")`, the event's
  `args == {"frobnicate": "x"}`, `event.error` contains both
  `--frobnicate` and `unknown argument`. Pins CT-5.
- `test_kebab_snake_kind_mismatch_routes_to_same_field` —
  `dispatch("renewal-reminders", ["--include-open-ended=false"],
  ...)` produces `status="dispatched"` and
  `DispatchResult.parsed["include_open_ended"] is False`; the
  journal event's `args == {"include_open_ended": "false"}` (the
  normalised key, not the user's kebab spelling — pins the audit
  shape).
- `test_operation_not_installed` — vault has no
  `weekly-digest` installed; assert `dispatch(...)` raises
  `WikiError` and journal has zero new events vs before the call.
- `test_operation_kind_mismatch` — `meeting` (a content-type) is
  installed; assert `dispatch("meeting", ...)` raises `WikiError`
  whose message names `content-type`, zero new events. The kind
  resolution runs **before** contract.yaml lookup so this fires
  cleanly without depending on a missing-file path. Pins CT-7.
- `test_contract_yaml_missing` — fixture vault where the operation
  is in `installed_primitives` and the discovered catalog says
  `kind=operation`, but `contract.yaml` was deleted from the
  templates tree; assert `WikiError` whose message contains the
  absolute path to the missing file, zero new events. Pins the
  Edge-case wording (no `wiki doctor` hint).
- `test_at_most_one_event_per_invocation` — parameterized over the
  three invalid_args paths and the two happy paths above; for each,
  count `OperationRunEvent` rows in the journal before and after,
  assert delta is exactly 1 (or 0 for the pre-load-failure paths).
- `test_period_threaded_from_contract` — fixture with
  `period: monthly` produces `OperationRunEvent.period == "monthly"`;
  fixture with no `period:` produces `period is None`.
- `test_skill_fallback_to_operation_name` — parameterised over
  `(skill omitted, skill="")`; in both cases assert
  `DispatchResult.skill == "<operation>"` and the CLI stdout names
  the operation as the skill. Pins CT-13's "absent or empty
  string" wording.
- `test_by_field_is_wiki_run` — explicit assertion that
  `event.by == "wiki-run"` on the happy path. (Cheap, but the
  invariant earns a dedicated test because cross-task `by` values
  are how `wiki doctor` and journal grep attribute work.)
- `test_dispatch_result_invariant` — direct construction:
  `DispatchResult(status="invalid_args", ..., error=None)`
  raises `ValueError` from `__post_init__`;
  `DispatchResult(status="dispatched", ..., error="bad")`
  also raises. Pins the spec §Invariants "error is non-None iff
  status==invalid_args" rule.
- `test_error_precedence_first_token_wins` — parameterised over
  two cases:
  - `["--frobnicate=x", "--window=banana"]` → `invalid_args`,
    `event.error` contains `--frobnicate`,
    `event.args == {"frobnicate": "x", "window": "banana"}`.
  - `["--window=banana", "--frobnicate=x"]` → `invalid_args`,
    `event.error` contains `--window` and `iso_week`,
    `event.args == {"window": "banana", "frobnicate": "x"}`.
  Pins CT-16 — the **first user-supplied token** wins regardless
  of failure kind, and the journal records every parsed token.
  Implementation note: `_parse_op_args` produces the full dict
  before the validation loop runs, so `args` is always complete;
  short-circuit happens during the walk, not during the parse.

**Approach:**

- New module `llm_wiki_kit/run.py` with:
  - Module docstring pointing at `docs/specs/task-17-wiki-run/spec.md`.
  - `RUN_VEHICLE = "wiki-run"` constant.
  - `DispatchResult` dataclass with `__post_init__` that enforces
    `error is not None iff status == "invalid_args"`. Fields:
    `status: Literal["dispatched", "invalid_args"]`,
    `operation: str`, `parsed: dict[str, object]`,
    `args_raw: dict[str, str]`, `period: str | None`,
    `skill: str`, `error: str | None`. **`error` matches the
    journal field name** so CLI code and tests reading both stay
    symmetric; the field is the same concept on either side of the
    journal append. No `"help"` status exists — the `--help`
    short-circuit lives entirely at the CLI boundary
    (`_cmd_run` calls `subparser.print_help` before invoking
    `dispatch`).
  - `_parse_op_args` (Step 2).
  - `_coerce_input` and `ArgCoercionError` (Step 3).
  - `_load_contract(operation, kit_root)` — single source of the
    `templates/operations/<name>/contract.yaml` path resolution and
    the missing-file error.
  - `_resolve_operation_kind(operation, kit_root, vault_state)` —
    loads `core` + `discover_primitives(templates_dir)`, finds
    `<operation>` in the catalog, returns its `PrimitiveKind`. Raises
    `WikiError` if not found or not installed. Run **before**
    `_load_contract` so a `meeting` (content-type) gives a clean
    kind-mismatch error rather than a confusing missing-contract
    error.
  - `dispatch(operation, raw_args, *, vault_root, kit_root,
    journal_path, now)` — composes the above; returns
    `DispatchResult`. Sequence (mirrors spec §Behavior):
    1. Short-circuit on `--help` / `-h` in `raw_args`.
    2. `replay_state(read_events(journal_path))` →
       `installed_primitives`.
    3. `_resolve_operation_kind` (raises on not-installed or
       kind-mismatch).
    4. `_load_contract` (raises on missing file).
    5. `_parse_op_args` (raises on shape).
    6. For each raw value, `_coerce_input` against
       `contract.inputs[name]`. Unknown name = "unknown argument"
       error. First coercion failure short-circuits to
       `invalid_args`.
    7. Apply contract defaults to fields not in user-supplied
       dict and whose `default` is not None.
    8. Append `OperationRunEvent` with the chosen `status`, the
       raw user-supplied dict in `args`, and `error` set iff
       `invalid_args`.
- `dispatch` always appends the journal event *as the last action*
  on every path that reaches the contract-load step. Pre-load
  failures raise `WikiError` and never journal.

**Done when:** every test in the Tests subsection above passes;
`mypy llm_wiki_kit tests` and `ruff check llm_wiki_kit/` clean;
no other module imports `run.py` yet (CLI wiring is Step 5).

### Step 5 — `wiki run <operation> --foo=bar` end-to-end via the CLI

**Tests** (construction): `tests/unit/test_cli_run.py` (mirrors the
shape of `tests/unit/test_cli_ingest.py`)

- `test_cli_dispatch_exits_zero_and_prints_skill_pointer` — invoke
  `cli.main(["run", "weekly-digest", "--window=2026-W20"], kit_root=...)`
  from a synth vault; assert exit `0`, stdout contains
  `Dispatched weekly-digest`, the skill name from the contract, and
  the lines `  window=2026-W20` and `  sources=meeting`.
- `test_cli_renders_typed_values_canonically` — uses a fixture
  operation with `inputs.include_open_ended:{type:boolean,
  default:true}` and `inputs.window_days:{type:integer,
  default:30}` (e.g. `renewal-reminders`); invoke with no args;
  assert stdout contains `  include_open_ended=true` and
  `  window_days=30`. Invoke with `--include-open-ended=false`;
  assert stdout contains `  include_open_ended=false`. Pins the
  rendering rules pinned in spec §Outputs (boolean lowercase,
  integer decimal).
- `test_cli_invalid_args_exits_two` — exit code is `2`, stderr
  contains both `--window` and `iso_week`, journal grew by exactly
  one `OperationRunEvent(status="invalid_args")`.
- `test_cli_unknown_argument_exits_two` — exit `2`, stderr contains
  both `--frobnicate` and `unknown argument`, journal grew by one
  `invalid_args` event whose `args == {"frobnicate": "x"}`.
- `test_cli_unknown_operation_exits_two_no_journal` — exit `2`,
  stderr names the operation, journal length unchanged.
- `test_cli_not_a_vault` — exit `2`, stderr matches the standard
  "not a wiki vault" prefix used by `_cmd_add` etc.
- `test_cli_malformed_token` — `wiki run weekly-digest banana`,
  exit `2`, stderr names `banana`. (Spec CT-9.)
- `test_cli_help_short_circuit_exits_zero_no_journal` —
  parameterised over `["run", "weekly-digest", "--help"]`,
  `["run", "weekly-digest", "-h"]`,
  `["run", "weekly-digest", "--window=x", "--help"]`, and
  `["run", "weekly-digest", "banana", "--help"]` (malformed
  positional alongside `--help`): each exits `0`, stdout contains
  argparse-style help, journal grew by zero events. The malformed-
  positional case proves the short-circuit wins over `_parse_op_args`'s
  WikiError path. Pins CT-14.
- `test_cli_help_value_form_does_not_short_circuit` —
  `cli.main(["run", "weekly-digest", "--help=false"], kit_root=...)`
  exits `2`, stderr names `--help` plus `unknown argument`, journal
  grew by one `invalid_args` event whose `args == {"help": "false"}`.
  Pins the spec rule that the short-circuit is exact-token only.
- `test_cli_uses_kit_root_override` — passes `kit_root=tmp_path`
  pointing at a fixture kit; asserts the right contract.yaml is
  loaded (no leak to the real templates tree).

**Approach:**

- Replace the `_cmd_run` stub in `cli.py` with a real handler that:
  - Resolves vault root + journal path with the same pattern as
    `_cmd_add` / `_cmd_ingest` (raise `WikiError` if no journal
    file).
  - Pre-scans `args.op_args` for `--help` / `-h` and calls the
    `wiki run` subparser's `print_help()` + returns `0` if either
    appears. This sidesteps argparse.REMAINDER's swallowing of the
    flag.
  - Otherwise calls `run.dispatch(...)`.
  - On `status == "dispatched"`: prints the header line plus one
    `  name=value` line per effective input (sorted, list values
    comma-joined). Returns `0`.
  - On `status == "invalid_args"`: prints `result.error_message`
    to stderr and returns `WIKI_ERROR_EXIT`.
- Extend the `wiki run` subparser:
  ```python
  run.add_argument("operation", ...)
  run.add_argument("op_args", nargs=argparse.REMAINDER,
                   help="Operation-specific args (see contract).")
  ```
  REMAINDER (not `nargs="*"`) is required so trailing `--name=value`
  tokens land in `op_args` instead of being rejected as unknown
  global flags. Side-effect — `--verbose` and `--help` after the
  positional are captured by REMAINDER; the `--help` short-circuit
  above handles `--help`; `--verbose` is documented as needing to
  appear before the subcommand (see spec §"Interaction with the
  global `--verbose` flag").

**Done when:** every test in the Tests subsection above passes;
`wiki run --help` (no operation positional) shows the subparser's
help; `wiki run weekly-digest --help` triggers the short-circuit;
the manual smoke (`wiki run weekly-digest --window=2026-W20`
against a fixture vault) produces the expected stdout. The existing
CLI stub-gate tests (`tests/unit/test_cli.py::test_run_stub*` if
present) are updated or removed, since `wiki run` is no longer a
stub.

### Step 6 — full CI gates green; PR-ready

**Tests** (verification gate):

```
ruff check llm_wiki_kit/
ruff format --check llm_wiki_kit/ tests/
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

All four must exit zero. Per memory: ruff format is a separate gate
from ruff check, and mypy must include `tests/`. The kit's pre-pr
aggregation hook (`tools/hooks/pre-pr.sh`) is the local mirror.

**Approach:**

- Re-run gates after the final commit, before opening the PR. Any
  test that was modified in Step 5 (because the stub went away)
  shows up here.
- If `ruff format --check` complains, run `ruff format` and fold
  into the same commit.

**Done when:** all four gates pass locally; the branch is pushed; the
PR is open against `main` from the `eugenelim` account with no
"Generated with Claude Code" footer and no `Co-Authored-By: Claude`
trailer.

## Verification gate

```
pytest tests/unit/test_operation_contract_inputs.py
pytest tests/unit/test_run.py tests/unit/test_run_dispatch.py
pytest tests/unit/test_cli_run.py
ruff check llm_wiki_kit/
ruff format --check llm_wiki_kit/ tests/
mypy llm_wiki_kit tests
pytest -m 'not slow'        # full sweep; catches indirect regressions
```

Acceptance: every `CT-*` row in spec.md §Acceptance criteria passes,
the model audit across `templates/operations/*/contract.yaml` is
clean, and the CLI smoke (`wiki run weekly-digest --window=...`
against a fixture vault) prints the expected dispatch line.

## Risks

- **Tightening `OperationContract.inputs` breaks a contract YAML we
  didn't read.** Mitigation: Step 1's "loads every shipped contract"
  test globs the entire templates tree. CI fails loudly on day one,
  not on a future user's vault.
- **`type: int` vs `type: integer` divergence.** Two existing
  contracts use `int`, the rest use `integer`. The model accepts
  both; the coercion path normalises. Risk that a future contract
  author adopts a third spelling — surfaced by the catch-all
  "unknown type passes through as str" path (spec §Non-goals), which
  is degrade-not-fail behaviour.
- **`argparse.REMAINDER` quirks.** REMAINDER captures everything
  after the operation name verbatim, including unintended
  consumption of `--verbose` and `--help` if the user puts them
  after `<operation>`. Mitigations: (a) the `_cmd_run` handler
  pre-scans `args.op_args` for `--help` / `-h` and short-circuits
  to subparser help; (b) `--verbose` lives on the global parent
  parser, so users put it before the subcommand (`wiki --verbose
  run ...`) or use `WIKI_DEBUG=1`. Both behaviours are pinned by
  tests in Step 5 (`test_cli_help_short_circuit*`).
- **ISO-week regex accepts week 53 in non-53 years.** The simple
  regex doesn't compute calendar validity. Accepting a slightly
  malformed `2025-W53` is preferable to importing `datetime.strptime`
  with `%V` (Python 3.11 has it but the failure modes are awkward).
  Out of scope; surfaced as a §Non-goals note.
- **Vault writes drift past `safe_write`.** Out of scope for Task 17
  (we don't write anything but the journal here); flagged so a
  reviewer catches scope creep into the SKILL-side work.

## Out of scope

- Anything Task 18 (research dispatch + Perplexity).
- A second `operation.completed` / `operation.failed` event class.
- `wiki run --describe <operation>` introspection mode.
- Operation outputs writing — that happens in the user's Claude
  session via vault-side SKILLs and `safe_write`.
- A formal subprocess-execution surface for operations. If one is
  ever needed, it goes through an ADR per AGENTS.md ("no new
  runtime deps without an ADR" applies in spirit to new execution
  surfaces too).
- Kebab-case ↔ snake_case translation between CLI flags and
  contract field names.
