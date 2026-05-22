#!/usr/bin/env python3
"""Regenerate the ``examples/*-mini/`` and ``examples/conflict-pending/`` vaults.

Two modes:

* ``--check`` — rebuild all three vaults into a tmp dir, normalize
  journal lines per spec AC6, byte-compare against the committed
  trees. Exit 0 on clean, non-zero with a unified-diff fragment on
  divergence. CI gate: ``tests/integration/test_examples_regenerable.py::
  test_regenerate_check_mode_clean``.
* ``--apply`` — same build into a tmp dir, then ``os.replace`` the
  tmp dir over the committed ``examples/<vault>/``. Atomic by design;
  a crash mid-swap leaves the committed tree untouched.

Spec: ``docs/specs/task-21-examples-tutorials/spec.md``.
Plan: ``docs/specs/task-21-examples-tutorials/plan.md`` §Steps T2 / T3.

The regenerator deliberately lives outside ``llm_wiki_kit/`` —
``pyproject.toml``'s ``packages = ["llm_wiki_kit"]`` keeps it off the
wheel surface.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from llm_wiki_kit import cli  # noqa: E402
from llm_wiki_kit.write_helper import safe_write  # noqa: E402

EXAMPLES_DIR = REPO_ROOT / "examples"
SEED_ROOT = EXAMPLES_DIR / "_seed"

# Recipe → target.name → seed directory. The recipe→target mapping is
# load-bearing (AC6 byte-compares kit-rendered files against committed
# bytes; the rendered files carry `{vault_name}` substitutions keyed
# to `target.name`).
RECIPE_TARGETS: dict[str, str] = {
    "family": "family-mini",
    "work-os": "work-os-mini",
    "personal": "conflict-pending",
}

# Fixed content strings for the drift-replay sequence. Pulled to module
# scope so the §Risks rationale in plan.md and the body of
# `_replay_drift` cannot drift apart.
_REPLAY_CONTENTS: dict[str, str] = {
    "initial": (
        "---\n"
        "type: person\n"
        "status: active\n"
        "---\n"
        "\n"
        "# Example Contact\n"
        "\n"
        "Initial baseline contact note authored by the kit.\n"
    ),
    "user_edit": (
        "---\n"
        "type: person\n"
        "status: active\n"
        "tags: [reviewed]\n"
        "---\n"
        "\n"
        "# Example Contact\n"
        "\n"
        "Initial baseline contact note authored by the kit.\n"
        "\n"
        "Added a personal note here so the kit notices the drift.\n"
    ),
    "kit_update": (
        "---\n"
        "type: person\n"
        "status: active\n"
        "---\n"
        "\n"
        "# Example Contact\n"
        "\n"
        "Updated baseline that the kit would have written on a fresh pass.\n"
    ),
}

# JSONL keys whose values vary per run and are normalized out before
# byte-comparison. Any other JSONL field is load-bearing — if a future
# event field becomes non-deterministic, the spec is amended in the
# same PR.
NORMALIZED_JOURNAL_KEYS: frozenset[str] = frozenset(
    {"timestamp", "hash", "content_hash", "source_hash"}
)

# Hidden files that vary by OS / dev environment; filtered before
# comparison and before traversal.
IGNORED_FILES: frozenset[str] = frozenset({".DS_Store", "Thumbs.db"})

# The personal recipe's `wiki/people/` directory holds the drifted page
# for `examples/conflict-pending/` — see spec §Behavior "How-to step 1"
# and §Outputs.
CONFLICT_PAGE_REL = "wiki/people/example-contact.md"


# ---------------------------------------------------------------------------
# Recipe variable invariant (AC6)
# ---------------------------------------------------------------------------


_recipe_variables_checked: set[str] = set()


def _assert_recipe_variables_stable(recipe_name: str) -> None:
    """Fail loudly if a recipe ever declares a non-empty non-`recipe_name` default.

    AC6 depends on every kit-rendered byte being deterministic between
    `--apply` (committed) and `--check` (CI). Recipe variables flow
    into rendered files via `_build_context`; if a future recipe edit
    introduces a `owner_role: "Manager"` default (for example), the
    `{owner_role}` substitution will produce different bytes on every
    rebuild keyed to whatever default the recipe carries — but the
    rebuild's output won't differ within a single run, so byte-compare
    against the committed tree would still pass as long as the
    committed tree was rebuilt at the same time. The check here is a
    forward-safety net: it makes a recipe change that introduces a
    non-empty default visible in CI immediately.
    """

    if recipe_name in _recipe_variables_checked:
        return

    import yaml

    recipe_path = REPO_ROOT / "recipes" / f"{recipe_name}.yaml"
    data = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    variables = data.get("variables", {}) or {}
    declared_recipe_name = variables.get("recipe_name", recipe_name)
    if declared_recipe_name != recipe_name:
        raise RuntimeError(
            f"Recipe `{recipe_name}` declares `recipe_name: {declared_recipe_name!r}` — "
            f"expected {recipe_name!r}. A typo here would propagate through "
            f"`{{recipe_name}}` substitutions in kit-rendered files and "
            f"break AC6 byte-equality without an obvious failure mode."
        )
    for key, value in variables.items():
        if key == "recipe_name":
            continue
        if value != "":
            raise RuntimeError(
                f"Recipe `{recipe_name}` declares `{key}: {value!r}` — "
                f"only `recipe_name` is allowed a non-empty default. "
                f"A non-empty `{key}` default would propagate through "
                f"`{{{key}}}` substitutions in kit-rendered files and "
                f"break AC6 byte-equality. If the new default is "
                f"intentional, amend the spec and this guard together."
            )
    _recipe_variables_checked.add(recipe_name)


# ---------------------------------------------------------------------------
# Build paths — vault construction
# ---------------------------------------------------------------------------


def build_vault(recipe: str, target: Path) -> None:
    """Build a clean, seeded vault for `family` or `work-os`.

    `target.name` must equal `RECIPE_TARGETS[recipe]`. `wiki init`
    embeds `target.name` into `{vault_name}` substitutions in every
    kit-rendered file; AC6 byte-compares those substitutions, so the
    basename has to match the committed vault's basename.
    """

    if recipe not in RECIPE_TARGETS or recipe == "personal":
        raise ValueError(
            f"build_vault: recipe must be 'family' or 'work-os'; got {recipe!r}. "
            "For the personal/conflict-pending vault use build_conflict_pending()."
        )
    expected = RECIPE_TARGETS[recipe]
    if target.name != expected:
        raise ValueError(
            f"build_vault: target.name must equal {expected!r} for recipe {recipe!r}; "
            f"got {target.name!r}. AC6 byte-equality depends on this."
        )

    _assert_recipe_variables_stable(recipe)

    # `--no-git` keeps committed example vaults free of a `.git/`
    # directory and the corresponding `VaultGitInitializedEvent` line
    # in the journal — the examples are reference content, not git
    # repositories. See `docs/specs/wiki-init-git/spec.md`.
    rc = cli.main(["init", str(target), "--recipe", recipe, "--no-git"])
    if rc != 0:
        raise RuntimeError(f"`wiki init --recipe {recipe}` exited {rc}")

    seed_dir = SEED_ROOT / recipe / "wiki"
    journal_path = target / ".wiki.journal" / "journal.jsonl"
    if seed_dir.is_dir():
        for src in sorted(seed_dir.rglob("*.md")):
            rel = src.relative_to(seed_dir)
            content = src.read_text(encoding="utf-8")
            safe_write(
                Path("wiki") / rel,
                content,
                by=f"examples-{recipe}-seed",
                journal_path=journal_path,
            )


def build_conflict_pending(target: Path) -> None:
    """Build the `personal`-recipe vault with one drifted page.

    Drift-replay sequence (see plan T2):
      1. `safe_write` of `_REPLAY_CONTENTS["initial"]` — produces the
         baseline `PageWriteEvent`.
      2. *Direct* `Path.write_bytes` of `_REPLAY_CONTENTS["user_edit"]`
         — the documented single `safe_write` carve-out (spec
         §Constraints). Simulates the user editing the page on disk.
      3. `safe_write` of `_REPLAY_CONTENTS["kit_update"]` — detects
         `on_disk_hash != baseline_hash`, writes the `.proposed`
         sidecar, and appends the `PageProposalEvent`.

    No clock pinning needed: `PageWriteEvent.hash` /
    `PageProposalEvent.hash` are sha256 of *content* (not timestamp),
    and AC6 normalizes the `timestamp` JSON key out of comparison.
    """

    if target.name != RECIPE_TARGETS["personal"]:
        raise ValueError(
            f"build_conflict_pending: target.name must equal "
            f"{RECIPE_TARGETS['personal']!r}; got {target.name!r}."
        )

    _assert_recipe_variables_stable("personal")

    # `--no-git` for the same reason as `build_vault`: committed
    # example vaults are reference content, not git repositories.
    rc = cli.main(["init", str(target), "--recipe", "personal", "--no-git"])
    if rc != 0:
        raise RuntimeError(f"`wiki init --recipe personal` exited {rc}")

    journal_path = target / ".wiki.journal" / "journal.jsonl"
    page_path = target / CONFLICT_PAGE_REL

    safe_write(
        Path(CONFLICT_PAGE_REL),
        _REPLAY_CONTENTS["initial"],
        by="examples-conflict-replay",
        journal_path=journal_path,
    )
    # Documented `safe_write` carve-out (spec §Constraints): simulate
    # the user editing the page on disk so the next safe_write detects
    # drift. safe_write itself short-circuits to direct-write when
    # on_disk == baseline; this is the only way to produce a
    # PageProposalEvent without a real user.
    page_path.write_bytes(_REPLAY_CONTENTS["user_edit"].encode("utf-8"))
    safe_write(
        Path(CONFLICT_PAGE_REL),
        _REPLAY_CONTENTS["kit_update"],
        by="examples-conflict-replay",
        journal_path=journal_path,
    )


# ---------------------------------------------------------------------------
# Normalization (AC6)
# ---------------------------------------------------------------------------


def normalize_journal(data: bytes) -> bytes:
    """Replace non-deterministic JSONL field values with a sentinel.

    Per AC6: `timestamp`, `hash`, `content_hash`, `source_hash` are
    the only normalized keys. Any other journal-line key is
    load-bearing.

    Corrupt JSON lines (rare; would indicate prior journal damage)
    pass through untouched with a sentinel marker prepended so the
    `--check` unified diff stays actionable rather than collapsing
    into a parse traceback.
    """

    out_lines: list[str] = []
    for line_no, raw_line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not raw_line.strip():
            out_lines.append(raw_line)
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            out_lines.append(f"<unparseable line {line_no}> {raw_line}")
            continue
        for key in NORMALIZED_JOURNAL_KEYS:
            if key in event:
                event[key] = "<normalized>"
        out_lines.append(json.dumps(event, sort_keys=True))
    return ("\n".join(out_lines) + "\n").encode("utf-8")


def _iter_vault_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in IGNORED_FILES:
            yield path


def _normalize_file(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix == ".jsonl":
        return normalize_journal(data)
    return data


# ---------------------------------------------------------------------------
# Build into tmp with the canonical basename preserved
# ---------------------------------------------------------------------------


def _build_into_tmp(recipe: str) -> tuple[Path, Path]:
    """Build ``recipe``'s vault under a fresh tmp parent.

    Returns ``(tmp_parent, built_vault)``. The caller is responsible
    for cleaning up ``tmp_parent`` (or moving ``built_vault`` over the
    committed tree, then removing ``tmp_parent``).

    The build target's basename equals ``RECIPE_TARGETS[recipe]`` so
    `{vault_name}` substitutions match the committed bytes.
    """

    tmp_parent = Path(tempfile.mkdtemp(prefix="llm-wiki-kit-regen-"))
    built = tmp_parent / RECIPE_TARGETS[recipe]
    if recipe == "personal":
        build_conflict_pending(built)
    else:
        build_vault(recipe, built)
    return tmp_parent, built


# ---------------------------------------------------------------------------
# --check mode
# ---------------------------------------------------------------------------


def _diff(committed: Path, built: Path) -> str:
    """Return a unified diff fragment for every divergent file in the vault.

    Empty string when the two trees match under AC6 normalization.
    Surfaces *all* divergences in one pass so a contributor doesn't
    have to re-run `--check` once per drifted file.
    """

    committed_files = {
        p.relative_to(committed): _normalize_file(p) for p in _iter_vault_files(committed)
    }
    built_files = {p.relative_to(built): _normalize_file(p) for p in _iter_vault_files(built)}

    out: list[str] = []

    extra_committed = sorted(set(committed_files) - set(built_files))
    extra_built = sorted(set(built_files) - set(committed_files))
    if extra_committed:
        out.append(f"committed has files the rebuild does not: {extra_committed}\n")
    if extra_built:
        out.append(f"rebuild has files the committed tree does not: {extra_built}\n")

    for rel in sorted(set(committed_files) & set(built_files)):
        if committed_files[rel] != built_files[rel]:
            committed_text = (
                committed_files[rel].decode("utf-8", errors="replace").splitlines(keepends=True)
            )
            built_text = (
                built_files[rel].decode("utf-8", errors="replace").splitlines(keepends=True)
            )
            diff = "".join(
                difflib.unified_diff(
                    committed_text,
                    built_text,
                    fromfile=f"committed/{rel}",
                    tofile=f"rebuild/{rel}",
                    n=3,
                )
            )
            out.append(diff or f"{rel}: byte difference but no textual diff (binary?)\n")
    return "".join(out)


def check_mode() -> int:
    """Rebuild all three vaults and compare against the committed trees."""

    fragments: list[str] = []
    diverged_vaults: list[str] = []
    for recipe, vault_name in RECIPE_TARGETS.items():
        committed = EXAMPLES_DIR / vault_name
        if not committed.is_dir():
            fragments.append(f"committed vault {committed} does not exist yet\n")
            diverged_vaults.append(vault_name)
            continue
        tmp_parent, built = _build_into_tmp(recipe)
        try:
            fragment = _diff(committed, built)
            if fragment:
                fragments.append(f"=== {vault_name} ===\n{fragment}")
                diverged_vaults.append(vault_name)
        finally:
            shutil.rmtree(tmp_parent, ignore_errors=True)

    if fragments:
        sys.stderr.write("regenerate.py --check: divergence detected\n")
        for f in fragments:
            sys.stderr.write(f)
        sys.stderr.write(
            f"\n{len(diverged_vaults)} vault(s) diverged: {', '.join(diverged_vaults)}. "
            "Run `python examples/regenerate.py --apply` after reviewing the diffs.\n"
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# --apply mode
# ---------------------------------------------------------------------------


def apply_vault(recipe: str, committed: Path) -> None:
    """Rebuild a single vault and swap it over the committed tree.

    Contract (verified by tests):

    * Happy path — the committed tree ends up with the new bytes; no
      staging or backup directories leak.
    * In-process failure during the second rename — `apply_vault`
      renames the backup back into place, then re-raises the
      original error. The committed bytes are unchanged. No staging
      directory leaks. Verified by
      ``test_regenerate_crash_safety``.
    * Double-fault (rollback rename ALSO fails) — `apply_vault`
      raises a ``RuntimeError`` naming both errors plus the path
      where the backup survives, and the staging parent is *not*
      cleaned up (so the user has a recoverable copy).

    Swap sequence (the in-process window where `committed` is absent
    between the two renames is unavoidable on POSIX because
    `rename(2)` returns ENOTEMPTY when the target is a non-empty
    directory — you cannot replace `committed/` with `staged/` in one
    syscall):

    1. Build the new tree into a tmp directory (TMPDIR-based).
    2. `shutil.copytree(built, staged)` into a sibling staging
       directory ``<committed.parent>/.staging-<name>-<random>/<name>``
       (same filesystem as `committed`, so the renames in steps 3-4
       cannot raise EXDEV).
    3. If `committed` exists: `os.rename(committed, backup)` — moves
       the existing tree aside to
       ``<committed.parent>/.staging-<name>-<random>/<name>.bak``
       without touching its contents.
    4. `os.rename(staged, committed)` — moves the new tree into
       place. If this rename raises, the inner `except` block tries
       `os.rename(backup, committed)`; if THAT also raises,
       `apply_vault` preserves the staging parent and surfaces a
       ``RuntimeError`` so the user can recover manually.
    5. `shutil.rmtree(backup)` — clean up the backup, then
       ``shutil.rmtree(staging_parent)``.

    No SIGKILL contract is asserted — the test harness can simulate
    in-process exceptions but cannot kill the process mid-syscall.
    """

    tmp_parent, built = _build_into_tmp(recipe)
    try:
        # Stage on the same filesystem as `committed` so the renames
        # in steps 3-4 are guaranteed not to raise EXDEV.
        staging_parent = Path(
            tempfile.mkdtemp(prefix=f".staging-{committed.name}-", dir=committed.parent)
        )
        preserve_staging = False
        try:
            staged = staging_parent / committed.name
            shutil.copytree(built, staged)

            backup = staging_parent / f"{committed.name}.bak"
            had_existing = committed.exists()
            if had_existing:
                os.rename(committed, backup)
            try:
                os.rename(staged, committed)
            except OSError as swap_err:
                if had_existing and backup.exists():
                    try:
                        os.rename(backup, committed)
                    except OSError as rollback_err:
                        # Double fault: the committed tree is gone and
                        # we couldn't restore the backup. Preserve the
                        # staging parent so the user can recover by
                        # hand, and surface both errors.
                        preserve_staging = True
                        raise RuntimeError(
                            f"apply_vault: swap failed ({swap_err!r}) AND "
                            f"rollback failed ({rollback_err!r}). The "
                            f"committed tree at {committed} is absent. "
                            f"A backup of the original is preserved at "
                            f"{backup}; recover with "
                            f"`mv {backup} {committed}`. The staging "
                            f"parent {staging_parent} has been left "
                            f"in place — clean it up after recovery."
                        ) from rollback_err
                raise
            if had_existing and backup.exists():
                shutil.rmtree(backup)
        finally:
            if not preserve_staging:
                shutil.rmtree(staging_parent, ignore_errors=True)
    finally:
        shutil.rmtree(tmp_parent, ignore_errors=True)


def apply_mode() -> int:
    completed: list[str] = []
    for recipe, vault_name in RECIPE_TARGETS.items():
        committed = EXAMPLES_DIR / vault_name
        try:
            apply_vault(recipe, committed)
        except Exception as exc:
            raise RuntimeError(
                f"apply_vault({recipe!r}, {committed}) failed: {exc}. "
                f"Vaults already swapped this run: {completed or '(none)'}."
            ) from exc
        completed.append(vault_name)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="Verify committed vaults match a fresh rebuild."
    )
    mode.add_argument(
        "--apply", action="store_true", help="Rebuild all committed vaults atomically."
    )
    args = parser.parse_args(argv)
    if args.check:
        return check_mode()
    return apply_mode()


if __name__ == "__main__":
    raise SystemExit(main())
