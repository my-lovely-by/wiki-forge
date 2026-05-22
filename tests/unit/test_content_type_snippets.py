"""Catalog-shape pin for ``templates/content-types/*`` schema snippets.

Every shipped content-type primitive contributes two managed-region
snippets to the rendered ``frontmatter.schema.yaml``:

* ``regions/frontmatter.schema.yaml.types`` — one or more bullets that
  extend the top-level ``types:`` list.
* ``regions/frontmatter.schema.yaml.fields`` — a mapping of extra
  frontmatter fields, each gated by a ``when: type == <name>`` clause
  so the field only applies to that content-type.

Nothing in the kit currently validates that the ``when:`` clause on a
field actually matches the type name the same snippet pair declares.
A renaming mistake (rename the directory, forget to update every
``when:`` line) ships silently — the kit happily writes the broken
schema, and only careful review catches it.

This module pins the catalog so a future primitive can't drift.
Retro-review concern qC7 (issue #23) gestures at a future
``wiki doctor --check-catalog`` mode that would surface the same
problem at runtime; until then, this is the validator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTENT_TYPES_DIR = REPO_ROOT / "templates" / "content-types"


def _content_type_dirs() -> list[Path]:
    if not CONTENT_TYPES_DIR.exists():
        return []
    return sorted(p for p in CONTENT_TYPES_DIR.iterdir() if p.is_dir())


def _parse_snippet_under(parent_key: str, snippet_path: Path) -> Any:
    """Parse a managed-region snippet as if it were nested under ``parent_key``.

    Snippets live as fragments indented two spaces, ready to be spliced
    into the rendered ``frontmatter.schema.yaml``. Wrapping with the
    parent key gives us a self-contained YAML document we can load.
    """
    body = snippet_path.read_text(encoding="utf-8")
    return yaml.safe_load(f"{parent_key}:\n{body}")


@pytest.mark.parametrize(
    "primitive_dir",
    _content_type_dirs(),
    ids=lambda p: p.name,
)
def test_all_content_type_snippets_carry_matching_when_clause(
    primitive_dir: Path,
) -> None:
    """Every field in ``frontmatter.schema.yaml.fields`` must be gated by
    a ``when: type == <name>`` clause whose ``<name>`` is listed in the
    sibling ``frontmatter.schema.yaml.types`` snippet.

    Catches the rename-the-primitive-forget-the-when class of bug at
    test time rather than at render time inside someone's vault.
    """
    regions_dir = primitive_dir / "regions"
    types_path = regions_dir / "frontmatter.schema.yaml.types"
    fields_path = regions_dir / "frontmatter.schema.yaml.fields"

    assert types_path.exists(), f"missing {types_path}"
    assert fields_path.exists(), f"missing {fields_path}"

    types_doc = _parse_snippet_under("types", types_path)
    declared_types = types_doc.get("types") or []
    assert declared_types, f"{types_path} declares no types"
    assert all(isinstance(t, str) for t in declared_types), (
        f"{types_path} must list string type names, got {declared_types!r}"
    )

    fields_doc = _parse_snippet_under("fields", fields_path)
    fields = fields_doc.get("fields") or {}
    assert fields, f"{fields_path} declares no fields"
    assert isinstance(fields, dict), (
        f"{fields_path} must be a mapping of field names to specs, got {type(fields).__name__}"
    )

    expected_when_clauses = {f"type == {name}" for name in declared_types}

    for field_name, spec in fields.items():
        assert isinstance(spec, dict), (
            f"{fields_path}: field {field_name!r} must be a mapping, got {type(spec).__name__}"
        )
        assert "when" in spec, (
            f"{fields_path}: field {field_name!r} is missing a 'when:' clause; "
            f"expected one of {sorted(expected_when_clauses)}"
        )
        clause = spec["when"]
        assert clause in expected_when_clauses, (
            f"{fields_path}: field {field_name!r} has when={clause!r}; "
            f"expected one of {sorted(expected_when_clauses)} (from {types_path.name})"
        )


def test_content_types_directory_is_not_empty() -> None:
    """Guard against the parametrised test silently collecting zero cases
    (e.g. if the directory layout changes and the glob stops matching)."""
    assert _content_type_dirs(), f"no content-type primitives discovered under {CONTENT_TYPES_DIR}"
