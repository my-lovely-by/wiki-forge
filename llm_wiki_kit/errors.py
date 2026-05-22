"""Kit-side exceptions.

ADR-0005 requires every kit error to be a ``WikiError`` subclass so the CLI
boundary can catch one base type and render a human-readable message instead
of leaking a Python traceback. ``ValidationError`` wraps Pydantic's structured
errors; ``JournalCorruptError`` is raised by ``journal.read_events`` when a
JSONL line fails to parse or validate.
"""

from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError


class WikiError(Exception):
    """Base class for every error the kit raises to the CLI boundary."""


class ValidationError(WikiError):
    """Human-readable wrapper around ``pydantic.ValidationError``.

    Renders one line per field error in the form
    ``Invalid <thing> at <dotted.path>: <message>``.
    """

    _INPUT_MAX_REPR_LEN = 120

    def __init__(self, thing: str, pydantic_error: PydanticValidationError) -> None:
        self.thing = thing
        self.pydantic_error = pydantic_error
        super().__init__(self._format(thing, pydantic_error))

    @staticmethod
    def _format(thing: str, pydantic_error: PydanticValidationError) -> str:
        lines: list[str] = []
        for err in pydantic_error.errors():
            loc = ".".join(str(part) for part in err.get("loc", ()))
            msg = err.get("msg", "invalid value")
            # Append the offending value when Pydantic surfaces one
            # (retro-review qC2). Pydantic v2 surfaces ``input`` on most
            # rows; the guard handles the absent case. ``!r`` keeps
            # quoting unambiguous for empty strings, whitespace, and
            # embedded quotes. Cap the rendering so a whole-object
            # failure (Pydantic puts the full dict in ``input``) doesn't
            # produce a wall of text on a single stderr line. The
            # truncation is human-readable, not eval-able — a mid-string
            # cut may leave an unclosed quote in the rendered tail.
            if "input" in err:
                rendered = repr(err["input"])
                if len(rendered) > ValidationError._INPUT_MAX_REPR_LEN:
                    rendered = rendered[: ValidationError._INPUT_MAX_REPR_LEN] + "..."
                tail = f": {msg} (got: {rendered})"
            else:
                tail = f": {msg}"
            if loc:
                lines.append(f"Invalid {thing} at {loc}{tail}")
            else:
                lines.append(f"Invalid {thing}{tail}")
        return "\n".join(lines) if lines else f"Invalid {thing}"


class JournalCorruptError(WikiError):
    """Raised on the first malformed line in ``.wiki.journal/journal.jsonl``.

    Carries the 1-based line number so ``wiki doctor`` and ``wiki journal``
    can point the user (or Claude) at the exact line to repair.
    """

    def __init__(self, line: int, reason: str) -> None:
        self.line = line
        self.reason = reason
        super().__init__(f"Journal corrupt at line {line}: {reason}")


class ManagedRegionError(WikiError):
    """Raised by ``managed_regions`` on malformed input or missing regions.

    Covers nesting, unmatched / unclosed markers, duplicate region ids in
    the same file (ADR-0003 §Consequences: region ids are part of the
    public contract), and ``update`` calls naming a region the file
    doesn't contain.
    """


class PrimitiveError(WikiError):
    """Raised by ``primitives`` for non-schema failures.

    Schema failures (a malformed ``primitive.yaml`` field) flow through
    :class:`ValidationError` so Pydantic's structured errors stay legible.
    This class covers the kit-side concerns the migration plan calls out:
    a missing primitive directory or manifest, malformed YAML, a closed
    set passed to :func:`primitives.resolve_dependencies` that references
    a primitive it doesn't contain, a duplicate primitive name in the
    same set, and ``requires:`` cycles.
    """


class RecipeError(WikiError):
    """Raised by ``recipes`` for non-schema failures.

    Schema failures (a malformed ``recipes/<name>.yaml`` field) flow through
    :class:`ValidationError`. ``RecipeError`` covers everything else the
    recipe layer is responsible for: a missing recipe file, malformed
    YAML, and the closure step in
    :func:`recipes.resolve_recipe_primitives` discovering that a recipe
    names a primitive the catalog does not contain. The closure step is
    a recipe-authoring concern rather than a primitive-loading one — a
    missing primitive there means the recipe and catalog disagree — so
    it raises ``RecipeError`` instead of leaking ``PrimitiveError``
    through.
    """
