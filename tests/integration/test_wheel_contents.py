"""Wheel-build contents tests (B5 acceptance: wheel-bundled-assets).

Every test in this file is ``@pytest.mark.slow`` because it consumes the
session-scoped ``built_wheel`` fixture (which invokes ``python -m
build``). Run with ``pytest -m slow`` to opt in; the default ``pytest``
invocation filters them out via ``pytest -m 'not slow'`` in CI.

See ``docs/specs/wheel-bundled-assets/spec.md`` §Acceptance → Wheel
contents.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_BUNDLE_PREFIX = "llm_wiki_kit/_assets"

pytestmark = pytest.mark.slow


def _wheel_namelist(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        return zf.namelist()


def test_built_wheel_contains_recipes(built_wheel: Path) -> None:
    names = set(_wheel_namelist(built_wheel))
    for recipe in ("family", "work-os", "personal"):
        path = f"{_BUNDLE_PREFIX}/recipes/{recipe}.yaml"
        assert path in names, f"{path} missing from wheel {built_wheel.name}"


def test_built_wheel_contains_core_primitive_and_every_file(built_wheel: Path) -> None:
    names = set(_wheel_namelist(built_wheel))
    assert f"{_BUNDLE_PREFIX}/core/primitive.yaml" in names

    source_core_files = REPO_ROOT / "core" / "files"
    expected: list[str] = []
    for path in source_core_files.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT / "core")
        expected.append(f"{_BUNDLE_PREFIX}/core/{relative.as_posix()}")

    missing = [p for p in expected if p not in names]
    if missing:
        head = missing[:5]
        extra = f" (and {len(missing) - 5} more)" if len(missing) > 5 else ""
        pytest.fail(f"wheel is missing core files: {head}{extra}")


def test_built_wheel_contains_every_template_primitive(built_wheel: Path) -> None:
    names = set(_wheel_namelist(built_wheel))
    source_templates = REPO_ROOT / "templates"
    primitive_paths = sorted(source_templates.rglob("primitive.yaml"))
    assert primitive_paths, "no template primitives discovered on disk"

    missing: list[str] = []
    for primitive_yaml in primitive_paths:
        relative = primitive_yaml.relative_to(REPO_ROOT / "templates")
        expected = f"{_BUNDLE_PREFIX}/templates/{relative.as_posix()}"
        if expected not in names:
            missing.append(expected)

    assert not missing, f"wheel is missing template primitives: {missing}"
