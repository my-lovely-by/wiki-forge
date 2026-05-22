# ADR-0005: Pydantic v2 for every schema that crosses disk

- **Status:** Accepted
- **Date:** 2026-05-15
- **Deciders:** maintainer
- **Related:** RFC-0001, ADR-0002, `docs/architecture/overview.md` ("The Python package")

## Context

The kit's runtime touches several disk formats:

- `primitive.yaml` — every primitive's manifest.
- `recipes/*.yaml` — recipe composition.
- `.wiki.journal/journal.jsonl` — one event per line, twenty-plus event
  types.
- `frontmatter.schema.yaml` — the vault's frontmatter contract.
- Operation `contract.yaml` files.
- Various per-skill `schema.yaml` files.

Every one of these is read by Claude or by the kit, mutated, and written
back. A subtle malformation (a misspelled field, a stringly-typed enum,
a missing required key) anywhere in this chain produces hard-to-debug
failures far from the source.

The options for validating these inputs:

1. **Hand-rolled `if`/`assert` validation** — fast to write the first
   one, expensive to maintain after the tenth schema; produces
   inconsistent error messages.
2. **`jsonschema`** — language-agnostic, but verbose, no native Python
   types, errors are unfriendly.
3. **`attrs` / dataclasses** — light, no validation by default; pair
   with `cattrs` for parsing but still no first-class validation
   ergonomics.
4. **Pydantic v2** — fast (Rust-backed), Python-native, discriminated
   unions, native YAML/JSON interop, clear error structure.

The kit's data model is also moving toward a *discriminated union of
events*, which Pydantic v2 expresses natively via `Field(discriminator=...)`.
That feature is the single most load-bearing schema requirement for the
journal (twenty-plus event types in one file, parsed one line at a
time).

A separate consideration is the boundary between disk-bound types and
in-memory plumbing. The kit also has internal data flow that doesn't
cross disk — function arguments, the build context dict, the
`WriteResult` enum. Forcing Pydantic on internal plumbing buys nothing
and clutters the call stack.

## Decision

> **Every type that crosses disk is a Pydantic v2 model. In-memory
> plumbing uses plain dataclasses, plain dicts, or type-hinted function
> signatures. Errors raise `WikiError` subclasses; `ValidationError`
> from Pydantic is caught at the CLI boundary and reformatted for human
> readability.**

Mechanics:

- All Pydantic models live in `llm_wiki_kit/models.py`.
- The `Event` type is a discriminated union over a `type` literal field,
  one Pydantic class per event type.
- `Primitive`, `Recipe`, `OperationContract` are top-level models.
- Loaders (`load_primitive`, `load_recipe`, `journal.read_events`) call
  `Model.model_validate(yaml.safe_load(text))` or
  `Model.model_validate_json(line)` as appropriate.
- `errors.ValidationError` wraps `pydantic.ValidationError` and renders
  errors as: `Invalid <thing> at <path>: <field>: <human message>`.
- Other modules import from `models.py`; nothing else defines
  cross-disk types.
- The `WriteResult` enum (`WRITTEN` / `PROPOSAL`) is plain Python
  because it doesn't cross disk.
- The `VaultState` returned by `journal.replay_state` is a Pydantic
  model because it gets passed across module boundaries and serialized
  in tests.

Schemas are documented in two places:

1. `docs/reference/data-models.md` — auto-generated from the Pydantic
   models, kept in sync via a script run in CI (planned for Task 3).
2. `docs/reference/journal-events.md` — human-readable per-event-type
   documentation.

## Consequences

### Positive

- **One validation library, one error shape.** Every loader produces
  the same error format. Users learn it once.
- **Discriminated event union is a one-liner.** Pydantic v2's
  `Field(discriminator='type')` handles parsing twenty-plus event
  types from one JSONL file natively.
- **Fast.** Pydantic v2's Rust core makes per-line validation cheap
  enough that we can validate every journal line on every read.
- **IDE support.** Type-checkers and editors understand the models;
  refactors are safe.
- **Schema docs are generated, not maintained.** The Pydantic model is
  the single source of truth for shape; docs come from `.json_schema()`.

### Negative

- **Pydantic is a runtime dep.** We're already committed to it
  (charter); this ADR formalizes the reason.
- **Pydantic v2 ≠ v1.** If we ever support Python <3.11 we have to
  pin compatibility carefully. Mitigation: we declare `python>=3.11`
  in `pyproject.toml`.
- **Models in `models.py` will grow.** With ~20 event types plus the
  top-level models, the module is at risk of becoming a 1000-line file.
  Acceptable for now; split when it crosses 800.
- **Validation errors at runtime, not at write time.** The journal
  files can be edited by hand, so we can't pre-validate. Mitigation:
  errors are explicit and actionable.

### Neutral / monitor

- If `models.py` exceeds 800 lines, split by concern: `models/events.py`,
  `models/primitive.py`, `models/recipe.py`. Internal API stays the
  same via a `models/__init__.py` re-export.
- If Pydantic adds a breaking change in v3, evaluate the migration cost
  vs. pinning. The Pydantic team has signaled v2 will be the LTS line
  for the foreseeable future.

## Alternatives considered

### Alt 1: Hand-rolled validation

`if "name" not in data: raise ValidationError("missing name")`. Loses on
maintenance. Twenty event types × five fields each = a hundred manual
checks. Error messages diverge. Discriminated unions become a switch
statement.

### Alt 2: `jsonschema`

Mature, language-agnostic, but in Python it produces poor error
messages, doesn't natively materialize types, and requires a second
type layer (e.g., `attrs`) for downstream code. Worse ergonomics than
Pydantic for the same job.

### Alt 3: `attrs` + `cattrs`

Light, fast, supports unions. Loses to Pydantic on (a) explicit
validation rules being a first-class feature, not bolted on, and
(b) the native discriminated-union ergonomics for the journal.

### Alt 4: Dataclasses + manual `__post_init__` validation

Free, stdlib, but the validation story is hand-rolled (alt 1's problem)
and there's no JSON/YAML interop without a serialization library.

## References

- [Pydantic v2 discriminated unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions)
- ADR-0002 (journal) — uses the discriminated `Event` union.
- Migration RFC `docs/rfc/0001-v2-architecture.md` (Task 3)
