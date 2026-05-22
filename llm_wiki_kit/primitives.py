"""Load primitives from disk and order them for install.

The migration plan (RFC-0001) names three surfaces:

* :func:`load_primitive` reads a single ``primitive.yaml`` and validates it
  against the Pydantic :class:`~llm_wiki_kit.models.Primitive` model. Pydantic
  errors flow through :class:`~llm_wiki_kit.errors.ValidationError` so the
  CLI prints one human-readable line per field; everything else (missing
  directory, missing manifest, malformed YAML) flows through
  :class:`~llm_wiki_kit.errors.PrimitiveError`.

* :func:`discover_primitives` walks ``templates_dir/<kind>/<name>/`` and
  loads every primitive it finds, sorted alphabetically by name. The catalog
  layout pinned in ``docs/architecture/overview.md`` is the schema this
  module assumes.

* :func:`resolve_dependencies` is a pure function over a closed list of
  primitives. It topologically sorts by ``requires:`` and raises on cycles
  or on a ``requires:`` target that isn't in the input set. Composing the
  closed set — adding ``core``, adding everything a recipe references and
  everything those things transitively require — lives in
  ``recipes.py`` (Task 9) and the installer (Task 10), not here.

**Why ``core`` isn't special-cased.** The ``core/`` directory at the repo
root has the same shape as a templates entry. The installer's
always-include-core policy is a recipe-level concern, not a loader-level
one, so :func:`load_primitive` treats ``core/`` like any other path.
:func:`discover_primitives` only walks ``templates/``; the caller passes
``core`` to :func:`load_primitive` separately.

**Tiebreaker is alphabetical by name.** When two primitives have no
dependency relationship, install order falls back to ``sorted(by name)``
rather than declaration order so re-running ``wiki init`` against the
same recipe produces the same journal, which keeps drift detection
honest in CI fixtures.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import ValidationError as PydanticValidationError

from llm_wiki_kit.errors import PrimitiveError, ValidationError, WikiError
from llm_wiki_kit.models import OperationContract, Primitive

# Catalog directory names per ``docs/architecture/overview.md``. The kit ships
# the kind subdirectories pluralized (``ontologies/`` for the ``ontology``
# kind, etc.); ``infrastructure`` is uncountable and matches its kind value
# directly. The mapping is one-way (directory → expected kind) — discovery
# does not enforce that a primitive in ``ontologies/`` declares ``kind:
# ontology``; that's a primitive-author check we leave to ``wiki doctor``.
_CATALOG_DIRS: frozenset[str] = frozenset(
    [
        "ontologies",
        "content-types",
        "operations",
        "infrastructure",
    ]
)


# ---------------------------------------------------------------------------
# Outcome-named entry points (per
# ``docs/specs/outcome-named-entry-points/spec.md`` §Inputs §2).
#
# Constants live in ``primitives.py`` rather than ``cli.py`` because the
# validator below reads them and the kit's dependency graph is
# ``cli.py -> primitives.py``; reversing the direction would introduce a
# circular import. The *enumeration source* — which subcommands are
# reserved — is ``cli.build_parser()``;
# ``tests/unit/test_outcome_verbs.py::test_reserved_outcome_verbs_matches_subcommand_set``
# pins the two against each other so a new subcommand added to
# ``cli.py`` without an update here trips CI.
# ---------------------------------------------------------------------------

#: Verbs an outcome may never equal. Matches the set of registered
#: top-level ``wiki`` subcommands plus standard discovery aliases.
#: ``tests/unit/test_outcome_verbs.py::test_reserved_outcome_verbs_matches_subcommand_set``
#: pins this set against ``cli.build_parser()`` so a new subcommand
#: added in `cli.py` without an update here trips the test.
RESERVED_OUTCOME_VERBS: frozenset[str] = frozenset(
    {
        "init",
        "add",
        "upgrade",
        "doctor",
        "ingest",
        "resolve",
        "lock",
        "run",
        "research",
        "search",
        "journal",
        # Discovery aliases — never registered as subparsers but
        # reserved so a primitive cannot claim them.
        "help",
        "version",
        "outcomes",
    }
)


#: Permitted verb stems. A well-formed verb either equals a bare-verb
#: entry (no trailing hyphen) outright, OR starts with one of the
#: prefix entries (trailing hyphen) followed by ``<object>``. Extend
#: this set in the same PR that adds an operation needing a new stem.
OUTCOME_VERB_STEMS: frozenset[str] = frozenset(
    {
        # Bare verbs.
        "digest",
        "roll-up",
        # Prefix forms (``<stem>-<object>``).
        "plan-",
        "refresh-",
        "log-",
        "summarize-",
        "prep-",
        "review-",
        "track-",
        "synthesize-",
        "pack-",
        "remind-",
        "map-",
    }
)


_OUTCOME_VERB_SHAPE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def is_well_formed_outcome_verb(verb: str) -> None:
    """Raise :class:`WikiError` if ``verb`` violates any naming rule.

    Returns ``None`` on success. Encodes spec §Inputs §2 rules 1, 2,
    3, 4, and 6 (rule 5 — catalog uniqueness — needs the full catalog
    and lives in :func:`check_outcome_verb_uniqueness`). Each rejection
    message names the rule that triggered it so the eventual
    error renders something the primitive author can act on.
    """

    # Rule 1 (length) and rule 2 (ASCII / locale). Length comes first
    # because the shape regex assumes a non-empty value.
    if not 3 <= len(verb) <= 24:
        raise WikiError(
            f"outcome verb '{verb}' length {len(verb)} is outside the "
            "3-24 character range (spec §Inputs §2 rule 1)"
        )
    if not verb.isascii():
        raise WikiError(
            f"outcome verb '{verb}' contains non-ASCII characters; "
            "outcome verbs are English-only ASCII per spec §Inputs §2 "
            "rule 2"
        )

    # Rule 1 (shape).
    if not _OUTCOME_VERB_SHAPE.fullmatch(verb):
        if any(ch.isupper() for ch in verb):
            raise WikiError(
                f"outcome verb '{verb}' must be ASCII lowercase "
                "kebab-case matching ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ "
                "(spec §Inputs §2 rule 1)"
            )
        if "--" in verb:
            raise WikiError(
                f"outcome verb '{verb}' contains consecutive hyphens (spec §Inputs §2 rule 1)"
            )
        if verb.endswith("-"):
            raise WikiError(f"outcome verb '{verb}' has a trailing hyphen (spec §Inputs §2 rule 1)")
        if verb[:1].isdigit():
            raise WikiError(
                f"outcome verb '{verb}' starts with a digit; verbs "
                "must start with [a-z] (spec §Inputs §2 rule 1, "
                "leading digit)"
            )
        raise WikiError(
            f"outcome verb '{verb}' does not match the kebab-case "
            "shape ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ (spec §Inputs §2 "
            "rule 1)"
        )

    # Rule 6 — belt-and-braces ``wiki-`` prefix block (rule 4 already
    # rejects it because no `wiki-` stem is allowlisted, but a future
    # maintainer adding a `wiki-` stem to ``OUTCOME_VERB_STEMS`` would
    # bypass that check).
    if verb.startswith("wiki-"):
        raise WikiError(
            f"outcome verb '{verb}' starts with the reserved 'wiki-' "
            "prefix (spec §Inputs §2 rule 6)"
        )

    # Rule 3 — reserved-word block.
    if verb in RESERVED_OUTCOME_VERBS:
        raise WikiError(
            f"outcome verb '{verb}' collides with a reserved wiki "
            "subcommand (spec §Inputs §2 rule 3)"
        )

    # Rule 4 — verb-form. Either the whole verb is a bare-verb entry,
    # or it starts with an allowlisted prefix entry (``<stem>-``)
    # followed by a non-empty ``<object>``.
    if verb in OUTCOME_VERB_STEMS:
        return
    for stem in OUTCOME_VERB_STEMS:
        if stem.endswith("-") and verb.startswith(stem) and len(verb) > len(stem):
            return
    raise WikiError(
        f"outcome verb '{verb}' does not start with an allowlisted "
        "verb-stem from primitives.OUTCOME_VERB_STEMS (spec §Inputs "
        "§2 rule 4); extend the constant in the same PR that needs a "
        "new stem"
    )


def check_outcome_verb_uniqueness(contracts: Iterable[OperationContract]) -> None:
    """Raise :class:`WikiError` on catalog-level outcome-verb collisions.

    Two passes over ``contracts``:

    1. **Verb uniqueness** (spec §Inputs §2 rule 5) — a verb appears at
       most once across every operation primitive.
    2. **Verb-vs-operation-name disjointness** (spec Invariants 8 +
       Acceptance criterion "Verb does not shadow any operation
       name") — a verb may not equal any operation's ``name``,
       including the declaring operation's own name.

    The function consumes the iterable once, so callers passing a
    one-shot generator do not need to materialize it twice.
    """

    contracts_list = list(contracts)
    operation_names: set[str] = {contract.name for contract in contracts_list}

    seen_verbs: dict[str, str] = {}
    for contract in contracts_list:
        for verb in contract.outcomes:
            # Pass 1: verb uniqueness across the catalog.
            owner = seen_verbs.get(verb)
            if owner is not None:
                raise WikiError(
                    f"outcome verb '{verb}' is declared by both "
                    f"'{owner}' and '{contract.name}'; verbs must be "
                    "unique across the operation catalog (spec "
                    "§Inputs §2 rule 5)"
                )
            seen_verbs[verb] = contract.name

            # Pass 2: verb-vs-operation-name shadow.
            if verb in operation_names:
                raise WikiError(
                    f"outcome verb '{verb}' declared by "
                    f"'{contract.name}' shadows the operation name "
                    f"'{verb}'; outcome verbs and operation names "
                    "must occupy disjoint sets (spec Invariant 8)"
                )


def load_primitive(path: Path) -> Primitive:
    """Load and validate a single primitive directory.

    ``path`` is the primitive root — the directory that contains
    ``primitive.yaml``. The function does not look at ``path.parent`` to
    cross-check the kind: a primitive's declared ``kind`` is the source of
    truth, and discovery is responsible for placing primitives in the
    right kind subdirectory.
    """

    if not path.exists():
        raise PrimitiveError(f"primitive directory does not exist: {path}")
    if not path.is_dir():
        raise PrimitiveError(f"primitive path is not a directory: {path}")

    manifest_path = path / "primitive.yaml"
    if not manifest_path.exists():
        raise PrimitiveError(f"primitive.yaml not found in {path}")

    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PrimitiveError(f"cannot read {manifest_path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise PrimitiveError(f"malformed YAML in {manifest_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise PrimitiveError(
            f"{manifest_path} must contain a YAML mapping, got {type(data).__name__}"
        )

    try:
        return Primitive.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationError(f"primitive.yaml at {path}", exc) from exc


def discover_primitives(templates_dir: Path) -> list[Primitive]:
    """Walk ``templates_dir/<kind>/<name>/`` and load every primitive.

    Returns an empty list when ``templates_dir`` does not exist (a fresh
    repo before any primitives have been authored is not an error). Skips
    directories at the top level whose name is not one of the four
    :class:`~llm_wiki_kit.models.PrimitiveKind` values; debris like a
    top-level ``README.md`` or a ``.DS_Store`` directory does not crash
    discovery. A directory inside a kind subdirectory without a
    ``primitive.yaml`` is also skipped — that pattern shows up during
    primitive authoring, where the skeleton may exist before the manifest.

    A *manifest* that fails to load is fatal: a typo in
    ``primitive.yaml`` would otherwise hide the primitive from every
    recipe that depends on it, which is exactly the failure mode Pydantic
    is meant to catch.
    """

    if not templates_dir.exists():
        return []

    primitives: list[Primitive] = []
    for kind_dir in sorted(templates_dir.iterdir()):
        if not kind_dir.is_dir() or kind_dir.name not in _CATALOG_DIRS:
            continue
        for primitive_dir in sorted(kind_dir.iterdir()):
            if not primitive_dir.is_dir():
                continue
            if not (primitive_dir / "primitive.yaml").exists():
                continue
            primitives.append(load_primitive(primitive_dir))

    primitives.sort(key=lambda p: p.name)
    return primitives


def resolve_dependencies(primitives: list[Primitive]) -> list[Primitive]:
    """Return ``primitives`` topologically sorted by ``requires:``.

    The input is assumed to be the closed set of primitives the caller
    intends to install — every name appearing in any primitive's
    ``requires:`` must also appear in ``primitives``. A missing dependency
    is the caller's bug (a recipe references a primitive it didn't
    install, or the always-include-core policy was skipped) and is raised
    as a :class:`PrimitiveError` rather than papered over.

    Cycles raise :class:`PrimitiveError`. Two primitives with no
    dependency relationship are ordered alphabetically by name so the
    journal of a freshly-rendered vault is reproducible.
    """

    by_name: dict[str, Primitive] = {}
    for primitive in primitives:
        if primitive.name in by_name:
            raise PrimitiveError(
                f"duplicate primitive name '{primitive.name}' in resolve_dependencies input"
            )
        by_name[primitive.name] = primitive

    for primitive in primitives:
        for required in primitive.requires:
            if required not in by_name:
                raise PrimitiveError(
                    f"primitive '{primitive.name}' requires '{required}' "
                    "but it is not in the input set"
                )

    ordered: list[Primitive] = []
    placed: set[str] = set()
    in_progress: set[str] = set()

    def visit(name: str, chain: tuple[str, ...]) -> None:
        if name in placed:
            return
        if name in in_progress:
            cycle = " -> ".join((*chain, name))
            raise PrimitiveError(f"cycle in primitive requires: {cycle}")
        in_progress.add(name)
        primitive = by_name[name]
        for required in sorted(primitive.requires):
            visit(required, (*chain, name))
        in_progress.discard(name)
        placed.add(name)
        ordered.append(primitive)

    for name in sorted(by_name):
        visit(name, ())

    return ordered
