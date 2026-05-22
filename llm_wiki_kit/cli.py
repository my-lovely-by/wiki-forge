"""The ``wiki`` CLI entry point.

This module wires up the top-level ``wiki`` command and its subcommands.
``init`` is the first real handler to land (RFC-0001 Task 10); every other
subcommand is still a stub that exits with status 1 and a "not yet
implemented" notice. Real handlers land in later tasks per RFC-0001.

The CLI boundary lives in :func:`main`: it catches :class:`WikiError` and
prints ``str(exc)`` to ``stderr`` with exit code 2 so users see a one-line
message instead of a Python traceback. ``--verbose`` (or ``WIKI_DEBUG``)
appends the traceback after the message line for debugging. Stubs use
exit 1 (sentinel for "not yet implemented"), which is a different category
from a real error. Individual handlers let ``WikiError`` propagate; they
should not re-catch and re-print it themselves — the boundary is in one
place so a future handler can't forget the contract.

``--verbose`` only adds a traceback when an exception actually propagates.
A handful of error-shaped paths (``wiki lock acquire`` contention, ``wiki
lock release`` ``--by`` mismatch, ``wiki ingest`` ambiguous / no-match)
``print(..., file=sys.stderr)`` and ``return`` a non-zero exit code
without raising — there is no live ``sys.exc_info()`` for those paths and
``--verbose`` is by design a no-op on them.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import importlib.resources
import os
import shutil
import sys
import traceback
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from llm_wiki_kit import __version__, journal
from llm_wiki_kit.doctor import format_issue, run_doctor
from llm_wiki_kit.errors import JournalCorruptError, WikiError
from llm_wiki_kit.git_init import initialize_git
from llm_wiki_kit.ingest import Ambiguous, NoMatch, Routed, route
from llm_wiki_kit.install import install_primitives, validate_contributions
from llm_wiki_kit.journal import (
    LockUnavailableError,
    _release_persisted_fd,
    append_event,
    dump_event_json,
    parse_event_line,
    read_events,
    replay_state,
    transaction,
)
from llm_wiki_kit.models import (
    ConfigSetEvent,
    Event,
    IngestRoutedEvent,
    LintRunEvent,
    LockAcquiredEvent,
    LockReleasedEvent,
    ManagedRegionWriteEvent,
    OperationExecFailedEvent,
    OperationRunEvent,
    PageConflictResolvedEvent,
    PageProposalEvent,
    PageWriteEvent,
    Primitive,
    PrimitiveInstallEvent,
    PrimitiveKind,
    PrimitiveRemoveEvent,
    PrimitiveUpgradeEvent,
    Recipe,
    ResearchQueryEvent,
    SourceIngestEvent,
    VaultGitInitializedEvent,
    VaultInitEvent,
)
from llm_wiki_kit.primitives import (
    discover_primitives,
    load_primitive,
    resolve_dependencies,
)
from llm_wiki_kit.recipes import CORE_PRIMITIVE_NAME, load_recipe, resolve_recipe_primitives
from llm_wiki_kit.upgrade import plan_upgrade, upgrade_primitives

INSTALL_VEHICLE_INIT = "wiki-init"
INSTALL_VEHICLE_ADD = "wiki-add"
# ``wiki upgrade``'s vehicle string lives in ``upgrade.UPGRADE_VEHICLE`` —
# single source of truth alongside the runner that uses it. Same shape
# applies to ``INGEST_VEHICLE`` (consumed only by ``_cmd_ingest`` here).
INGEST_VEHICLE = "wiki-ingest"

NOT_IMPLEMENTED_EXIT = 1
WIKI_ERROR_EXIT = 2
DOCTOR_ISSUES_EXIT = 1
INGEST_ROUTE_FAILED_EXIT = 2

# ``wiki lock acquire`` exits with this code when the lock is genuinely held
# by another process (the holder file exists AND a non-blocking
# ``fcntl.flock(LOCK_EX | LOCK_NB)`` returns ``EAGAIN``/``EWOULDBLOCK``).
# Distinct from ``WIKI_ERROR_EXIT`` (2) so a wrapper script can tell
# "lock contention, try again later" apart from "the CLI itself misfired".
LOCK_HELD_EXIT = 3

# Sentinel ``by`` recorded on the audit ``LockReleasedEvent`` that
# precedes a stale-holder reclaim (spec §Edge cases "Lock held by a
# dead PID"). Names ``wiki-doctor`` because the doctor surface is the
# only other consumer of the "stale lock" concept and pinning a single
# actor name keeps audit grep predictable.
STALE_LOCK_RECLAIM_BY = "wiki-doctor"
STALE_LOCK_RECLAIM_REASON = "stale lock reclaimed"

# Where the kit's bundled assets (``recipes/``, ``core/``, ``templates/``)
# live at runtime. ``importlib.resources`` reads the wheel's relocated
# in-package ``_assets/`` tree; an editable / source-checkout install
# falls back to ``Path(__file__).resolve().parent.parent`` (the repo root).
# See ``docs/specs/wheel-bundled-assets/spec.md``.
#
# Resolution is **lazy**: ``_KIT_ROOT`` is populated by ``_kit_root()`` on
# first call. ``import llm_wiki_kit.cli`` does not read the filesystem to
# find the asset trees, so a misconfigured wheel still leaves
# ``wiki --version`` / ``wiki --help`` usable for diagnosis. Production
# code reads the kit root via ``_kit_root()`` or ``_kit_paths()``. The
# grep guard in ``tests/unit/test_cli_kit_root.py`` pins the *cross-file*
# boundary (no other module or test references the identifier
# ``_KIT_ROOT``); intra-``cli.py`` discipline — only the resolver block
# below reads the attribute — is convention, not gate-enforced.
_BUNDLE_PREFIX = "_assets"
_KIT_SUBDIRS: tuple[str, str, str] = ("recipes", "core", "templates")
_KIT_ROOT: Path | None = None


def _bundled_assets_path() -> Path | None:
    """Return the in-package bundled-assets directory, or ``None``.

    Separate seam so tests can monkeypatch this function directly
    without faking the ``importlib.resources`` Traversable protocol.
    Returns the path when it points at a real on-disk directory (the
    wheel-install case); returns ``None`` for editable installs where
    the relocated tree is not materialized inside the package.
    """

    traversable = importlib.resources.files("llm_wiki_kit").joinpath(_BUNDLE_PREFIX)
    candidate = Path(str(traversable))
    return candidate if candidate.is_dir() else None


def _source_tree_kit_root() -> Path:
    """Return the source-checkout root containing the asset trees.

    Separate seam so the resolver's source-tree branch is monkeypatchable
    without touching ``cli.__file__`` (which is fragile and interacts
    with importlib/traceback machinery).
    """

    return Path(__file__).resolve().parent.parent


def _resolve_kit_root() -> Path:
    """Resolve the directory containing ``recipes/``, ``core/``, ``templates/``.

    Tried in order: bundled (wheel install) → source-tree (editable /
    source-checkout). Each candidate must contain ALL three subdirectories
    to win; a half-valid candidate falls through. See
    ``docs/specs/wheel-bundled-assets/spec.md``.
    """

    bundled = _bundled_assets_path()
    if bundled is not None and all((bundled / s).is_dir() for s in _KIT_SUBDIRS):
        return bundled
    source = _source_tree_kit_root()
    if all((source / s).is_dir() for s in _KIT_SUBDIRS):
        return source
    # Name both candidate paths and the missing subdir set so a 3am pager
    # can tell wheel-misconfigured apart from running-from-wrong-Python.
    raise WikiError(
        "kit assets not found: "
        f"bundled={bundled if bundled is not None else '(not present)'}, "
        f"source-tree={source}. "
        f"Neither contains all of {', '.join(s + '/' for s in _KIT_SUBDIRS)}"
    )


def _kit_root() -> Path:
    """Lazy accessor; populates ``_KIT_ROOT`` on first call.

    Production code reads the kit root via this function, never via the
    module attribute. The cache is the module attribute itself; the
    ``tests/conftest.py`` autouse fixture resets it between tests so a
    unit test that monkeypatches ``_bundled_assets_path`` cannot leak
    state into the next test.
    """

    global _KIT_ROOT
    if _KIT_ROOT is None:
        _KIT_ROOT = _resolve_kit_root()
    return _KIT_ROOT


# Templates-directory layout per ``docs/architecture/overview.md``: each
# ``PrimitiveKind`` maps to a pluralized subdirectory of ``templates/``.
# ``infrastructure`` is uncountable and matches its enum value directly.
_KIND_DIRS: dict[PrimitiveKind, str] = {
    PrimitiveKind.ONTOLOGY: "ontologies",
    PrimitiveKind.CONTENT_TYPE: "content-types",
    PrimitiveKind.OPERATION: "operations",
    PrimitiveKind.INFRASTRUCTURE: "infrastructure",
}


def _stub(name: str) -> int:
    print(
        f"wiki {name}: not yet implemented (v2 migration in progress, see RFC-0001).",
        file=sys.stderr,
    )
    return NOT_IMPLEMENTED_EXIT


def _kit_paths(kit_root: Path | None = None) -> tuple[Path, Path, Path]:
    """Return ``(recipes_dir, core_dir, templates_dir)`` for the running kit.

    Production callers pass ``args.kit_root`` (typically ``None``, in
    which case the lazy resolver fires via :func:`_kit_root`). Tests pass
    an explicit override via ``cli.main(argv, kit_root=...)``; the
    threading is the qC8 fix for the previous monkeypatch-the-module
    pattern.
    """

    root = kit_root if kit_root is not None else _kit_root()
    return root / "recipes", root / "core", root / "templates"


def _build_context(recipe: Recipe, vault_name: str) -> dict[str, str]:
    """Compose the render context for a ``wiki init`` invocation.

    Precedence (lower → higher): recipe ``variables:`` defaults, then
    CLI-derived values (``vault_name`` from the target path's basename,
    ``recipe_name`` from ``recipe.name``). CLI-derived values win because
    ``vault_name`` is necessarily per-install and ``recipe_name`` is
    canonically the recipe's declared ``name``; a recipe author cannot
    override either without breaking the journal's identity contract.
    """

    context: dict[str, str] = {}
    context.update(recipe.variables)
    context["vault_name"] = vault_name
    context["recipe_name"] = recipe.name
    return context


def _primitive_source_dir(primitive: Primitive, core_dir: Path, templates_dir: Path) -> Path:
    """Return the on-disk directory that holds ``primitive``'s ``files/`` tree."""

    if primitive.name == CORE_PRIMITIVE_NAME:
        return core_dir
    return templates_dir / _KIND_DIRS[primitive.kind] / primitive.name


def _cmd_init(args: argparse.Namespace) -> int:
    """Render a fresh vault from a recipe.

    Refuses to run against a non-empty target directory. The RFC's
    "unresolved questions" list flagged an ``--adopt`` flag for adopting
    an existing folder as a vault; the decision at Task 10 is to omit
    that flag entirely for now — the design is non-trivial (every
    pre-existing file needs to be journaled before any kit-owned write
    can land safely) and ``wiki upgrade`` will cover the natural re-run
    case. A future task can add ``--adopt`` once its semantics are
    pinned in an ADR.

    Ordering follows ADR-0002: the journal is the source of truth, so
    ``VaultInitEvent`` and each ``PrimitiveInstallEvent`` are appended
    *before* the corresponding filesystem writes. If a write crashes
    mid-install, the journal still reflects the intent and ``wiki
    doctor`` (Task 12) can reconcile.

    Unless ``--no-git`` is passed, the handler concludes by calling
    :func:`llm_wiki_kit.git_init.initialize_git`, which runs ``git
    init`` + one initial commit and journals
    :class:`VaultGitInitializedEvent`. The empty-target refusal fires
    **before** the git pre-flight so a user who passes a non-empty
    path sees the existing "not empty" error rather than a misleading
    "git missing" one when both conditions fail. See
    ``docs/specs/wiki-init-git/spec.md`` §Behavior.
    """

    target = Path(args.path).resolve()

    if target.exists() and target.is_file():
        raise WikiError(f"target path is a file, not a directory: {target}")
    if target.exists() and any(target.iterdir()):
        raise WikiError(
            f"target directory is not empty: {target}\n"
            "wiki init refuses to render over existing files. "
            "Choose an empty directory or remove its contents first."
        )

    # Git pre-flight runs AFTER the empty-target refusal so a user who
    # passes a non-empty path with no git installed sees the more
    # informative refusal first (spec §Behavior step 2). Pre-flight
    # before mutation: refusing here leaves the filesystem untouched.
    if not args.no_git and shutil.which("git") is None:
        raise WikiError("git is not on PATH; install git or pass --no-git, then re-run.")

    recipes_dir, core_dir, templates_dir = _kit_paths(args.kit_root)
    recipe = load_recipe(recipes_dir / f"{args.recipe}.yaml")
    catalog: list[Primitive] = [load_primitive(core_dir)]
    catalog.extend(discover_primitives(templates_dir))
    ordered = resolve_recipe_primitives(recipe, catalog)

    # Pre-flight: every primitive's contribution shape must match its
    # on-disk ``regions/`` directory before any state-changing write.
    # ADR-0006 §Mechanics step 6 — fail loudly, not half-installed.
    sources: dict[str, Path] = {
        primitive.name: _primitive_source_dir(primitive, core_dir, templates_dir)
        for primitive in ordered
    }
    for primitive in ordered:
        validate_contributions(primitive, sources[primitive.name])

    target.mkdir(parents=True, exist_ok=True)
    journal_path = target / ".wiki.journal" / "journal.jsonl"
    vault_name = target.name
    context = _build_context(recipe, vault_name)
    now = datetime.now(UTC)

    # Install pipeline amortises baseline lookups through one
    # JournalReader scope (qC4). Every `safe_write` /
    # `safe_write_region` inside reads the journal at most once;
    # subsequent baseline lookups consult the in-memory cache that
    # `append_event` extends after each fsync. See
    # `docs/specs/journal-reader-cache/spec.md`.
    with journal.use_journal_cache(journal_path):
        append_event(
            journal_path,
            VaultInitEvent(
                timestamp=now,
                by=INSTALL_VEHICLE_INIT,
                vault_name=vault_name,
                recipe=recipe.name,
            ),
        )

        # Per-primitive render + the second-pass region aggregator
        # (ADR-0006). ``install_primitives`` runs ``to_install`` ==
        # ``all_installed`` for ``wiki init`` because every primitive in
        # the closure is new to this vault.
        install_primitives(
            to_install=ordered,
            all_installed=ordered,
            sources=sources,
            journal_path=journal_path,
            context=context,
            install_vehicle=INSTALL_VEHICLE_INIT,
            now=now,
        )

        # Git phase (spec §Behavior step 6). Runs inside the still-open
        # journal-cache scope so the new event flows through the cache
        # like every other init-time append. The function appends its
        # event between ``git init`` and ``git add -A``/``git commit``
        # so the journaled line is captured by the initial commit's
        # tree, leaving ``git status --porcelain`` empty.
        if not args.no_git:
            initialize_git(
                target,
                recipe_name=recipe.name,
                journal_path=journal_path,
                _now=now,
            )
    return 0


def _parse_primitive_spec(spec: str) -> tuple[PrimitiveKind, str]:
    """Split a ``<kind>:<name>`` argument into a validated ``(kind, name)`` pair.

    ``<kind>`` must be one of the four :class:`PrimitiveKind` values in
    its canonical dash form (``ontology``, ``content-type``,
    ``operation``, ``infrastructure``); case-sensitive, per the Task 12
    spec. Anything else is a one-line :class:`WikiError`.
    """

    kind_str, sep, name = spec.partition(":")
    if not sep or not kind_str or not name:
        raise WikiError(f"invalid primitive specifier '{spec}': expected '<kind>:<name>'")
    try:
        kind = PrimitiveKind(kind_str)
    except ValueError as exc:
        valid = ", ".join(k.value for k in PrimitiveKind)
        raise WikiError(f"unknown primitive kind '{kind_str}': expected one of {valid}") from exc
    return kind, name


def _expand_closure(target: Primitive, by_name: dict[str, Primitive]) -> list[Primitive]:
    """Return ``target`` plus its transitive ``requires:`` closure.

    Missing requires raise :class:`WikiError` with the offending name —
    a ``wiki add`` against a catalog the user's kit version doesn't
    carry is a user-facing failure, not an internal one.
    """

    closed: dict[str, Primitive] = {target.name: target}
    pending: list[str] = list(target.requires)
    while pending:
        name = pending.pop()
        if name in closed:
            continue
        primitive = by_name.get(name)
        if primitive is None:
            raise WikiError(
                f"primitive '{target.name}' requires '{name}' which is not in the catalog"
            )
        closed[name] = primitive
        for required in primitive.requires:
            if required not in closed:
                pending.append(required)
    return list(closed.values())


def _cmd_add(args: argparse.Namespace) -> int:
    """Install one primitive (and its requires-closure) into the current vault.

    Operates on the vault rooted at ``Path.cwd()``: the spec scopes
    ``wiki add`` to "the current vault," and the parser deliberately
    takes no path argument. Refuses when there is no
    ``.wiki.journal/journal.jsonl`` to anchor against — ``wiki init``
    is the only way to create a vault.

    The closure is filtered against ``replay_state(...).installed_primitives``
    so a primitive that's already installed is a no-op, and a re-run
    against an already-fully-resolved closure emits no new events. The
    region aggregator still runs against the *full* installed set
    (existing primitives plus the new closure) so a contribution that
    used to live alone in its bucket survives the install — running it
    over only the new additions would clobber the existing body to
    "new-only" (ADR-0006 §Mechanics step 5 plus the Task-12 design
    callout).
    """

    kind, name = _parse_primitive_spec(args.primitive)

    vault_root = Path.cwd().resolve()
    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(
            f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl. "
            "Run `wiki init <path> --recipe <name>` first."
        )

    state = replay_state(read_events(journal_path))
    if state.recipe is None or state.vault_name is None:
        raise WikiError(
            f"vault at {vault_root} has no vault.init event; "
            "the journal is incomplete and cannot be extended"
        )

    recipes_dir, core_dir, templates_dir = _kit_paths(args.kit_root)
    catalog: list[Primitive] = [load_primitive(core_dir)]
    catalog.extend(discover_primitives(templates_dir))
    by_name: dict[str, Primitive] = {p.name: p for p in catalog}

    target_dir = templates_dir / _KIND_DIRS[kind] / name
    target = load_primitive(target_dir)
    if target.kind != kind:
        raise WikiError(
            f"primitive '{name}' has kind '{target.kind.value}', not '{kind.value}' as specified"
        )

    closure_ordered = resolve_dependencies(_expand_closure(target, by_name))
    to_install = [
        primitive
        for primitive in closure_ordered
        if primitive.name not in state.installed_primitives
    ]

    if not to_install:
        # Idempotent re-add: the journal already records every
        # primitive in the closure as installed. No new events, no
        # disk writes — the aggregator pass would emit redundant
        # ``managed_region.write`` events for unchanged bodies.
        return 0

    # The aggregator needs every currently-installed primitive plus
    # the new ones, in topological order, with a source dir for each.
    all_installed_names = set(state.installed_primitives) | {p.name for p in to_install}
    try:
        all_installed_primitives = [by_name[n] for n in all_installed_names]
    except KeyError as exc:
        raise WikiError(
            f"installed primitive '{exc.args[0]}' is not in the kit's "
            "catalog; run `wiki doctor` to inspect"
        ) from exc
    all_installed_ordered = resolve_dependencies(all_installed_primitives)

    sources: dict[str, Path] = {
        primitive.name: _primitive_source_dir(primitive, core_dir, templates_dir)
        for primitive in all_installed_ordered
    }

    # Pre-flight: validate before any state-changing write
    # (ADR-0006 §Mechanics step 6).
    for primitive in to_install:
        validate_contributions(primitive, sources[primitive.name])

    recipe = load_recipe(recipes_dir / f"{state.recipe}.yaml")
    context = _build_context(recipe, state.vault_name)
    now = datetime.now(UTC)

    # qC4 install-pipeline cache scope, same shape as _cmd_init.
    with journal.use_journal_cache(journal_path):
        install_primitives(
            to_install=to_install,
            all_installed=all_installed_ordered,
            sources=sources,
            journal_path=journal_path,
            context=context,
            install_vehicle=INSTALL_VEHICLE_ADD,
            now=now,
        )
    return 0


def _cmd_upgrade(args: argparse.Namespace) -> int:
    """Re-apply installed primitives against the running kit's catalog.

    Mirrors ``_cmd_add``'s boundary-then-pipeline shape (`docs/specs/
    wiki-upgrade/spec.md`). Every installed primitive whose journaled
    version differs from the catalog version is re-rendered through
    ``render_tree`` (every file via ``safe_write``) and journaled as
    a :class:`PrimitiveUpgradeEvent`. The managed-region aggregator
    then runs over every installed primitive — exactly the pass
    ``wiki add`` uses — so contributions from version-unchanged
    primitives survive.

    Idempotency is a CLI concern, not a runner concern: when
    ``plan.to_upgrade`` is empty, this handler short-circuits before
    entering the runner so the no-op invocation is journal-clean.
    """

    primitive_arg: str | None = args.primitive
    if primitive_arg is not None and ":" in primitive_arg:
        raise WikiError("--primitive must be a bare primitive name, not <kind>:<name>")

    vault_root = Path.cwd().resolve()
    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(
            f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl. "
            "Run `wiki init <path> --recipe <name>` first."
        )

    state = replay_state(read_events(journal_path))
    if state.recipe is None or state.vault_name is None:
        raise WikiError(
            f"vault at {vault_root} has no vault.init event; "
            "the journal is incomplete and cannot be upgraded"
        )

    recipes_dir, core_dir, templates_dir = _kit_paths(args.kit_root)
    catalog: list[Primitive] = [load_primitive(core_dir)]
    catalog.extend(discover_primitives(templates_dir))

    plan = plan_upgrade(state, catalog, only=primitive_arg)

    def _print_not_in_catalog_hint() -> None:
        if not plan.not_in_catalog:
            return
        count = len(plan.not_in_catalog)
        word = "primitive" if count == 1 else "primitives"
        print(
            f"note: {count} installed {word} no longer in the kit catalog; "
            f"run `wiki doctor` for details.",
            file=sys.stderr,
        )

    if not plan.to_upgrade:
        if plan.no_op_target is not None:
            name, version = plan.no_op_target
            print(f"wiki upgrade: primitive '{name}' is already at version {version}.")
        else:
            print("wiki upgrade: nothing to upgrade.")
        _print_not_in_catalog_hint()
        return 0

    sources: dict[str, Path] = {
        primitive.name: _primitive_source_dir(primitive, core_dir, templates_dir)
        for primitive in plan.all_installed
    }
    # Widened pre-flight per spec §Invariants 8: validate every
    # contributing primitive's snippet shape (not just the version-
    # bumped ones), because the aggregator reads every installed
    # primitive's ``regions/`` snippets.
    for primitive in plan.all_installed:
        validate_contributions(primitive, sources[primitive.name])

    recipe = load_recipe(recipes_dir / f"{state.recipe}.yaml")
    context = _build_context(recipe, state.vault_name)
    now = datetime.now(UTC)

    # Snapshot the upgrade pairs before the runner walks ``to_upgrade``
    # so the per-primitive summary lines can be printed in journal order
    # without re-replaying state afterwards.
    upgrade_pairs: list[tuple[str, str, str]] = [
        (p.name, state.installed_primitives[p.name], p.version) for p in plan.to_upgrade
    ]

    with journal.use_journal_cache(journal_path):
        proposals = upgrade_primitives(
            plan=plan,
            sources=sources,
            journal_path=journal_path,
            context=context,
            state_versions=dict(state.installed_primitives),
            now=now,
        )

    for name, from_v, to_v in upgrade_pairs:
        print(f"upgraded {name} {from_v} → {to_v}")
    for original, proposed in proposals:
        print(
            f"Wrote {proposed} (drift detected on {original}); "
            f"run the wiki-conflict skill to merge."
        )
    count = len(upgrade_pairs)
    word = "primitive" if count == 1 else "primitives"
    print(f"wiki upgrade: upgraded {count} {word}.")
    _print_not_in_catalog_hint()
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Validate the current vault against its journal and report issues.

    Operates on ``Path.cwd()`` like ``wiki add``. Exit codes split
    cleanly between "found things" (1) and "the run itself failed" (2)
    so a wrapper script (or CI) can distinguish a noisy vault from a
    broken invocation. A reserved ``--json`` flag for machine-readable
    output is deferred to a later task.
    """

    vault_root = Path.cwd().resolve()
    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl")

    issues = run_doctor(vault_root, args.kit_root if args.kit_root is not None else _kit_root())

    for issue in issues:
        print(format_issue(issue))

    return DOCTOR_ISSUES_EXIT if issues else 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Route ``<source>`` to a content-type primitive and journal the decision.

    Operates on the vault at :func:`Path.cwd`, like ``wiki add`` and
    ``wiki doctor``. The orchestrator is pure (see ``llm_wiki_kit.ingest``);
    this handler is the I/O boundary: load the installed catalog, walk
    routing rules, append one :class:`IngestRoutedEvent`, print a
    one-liner to stdout or stderr, exit. The CLI never invokes Claude or
    fetches the URL — the vault-side ``ingest-<name>/SKILL.md`` does
    that when the user's session picks up the journaled route.
    """

    vault_root = Path.cwd().resolve()
    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(
            f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl. "
            "Run `wiki init <path> --recipe <name>` first."
        )

    state = replay_state(read_events(journal_path))

    _, core_dir, templates_dir = _kit_paths(args.kit_root)
    catalog: list[Primitive] = [load_primitive(core_dir)]
    catalog.extend(discover_primitives(templates_dir))
    installed = [p for p in catalog if p.name in state.installed_primitives]

    result = route(args.source, installed, as_override=args.as_content_type)

    now = datetime.now(UTC)
    event = _ingest_event_from_result(args.source, result, now)
    append_event(journal_path, event)

    if isinstance(result, Routed):
        print(
            f"Routed {args.source} -> content-type:{result.content_type}. "
            f"Run `ingest-{result.content_type}` in your Claude session."
        )
        return 0
    if isinstance(result, Ambiguous):
        print(
            f"Ambiguous: {args.source} matched multiple content-types: "
            f"{', '.join(result.candidates)}. Re-run with --as <name>.",
            file=sys.stderr,
        )
        return INGEST_ROUTE_FAILED_EXIT
    # NoMatch
    available = ", ".join(result.available) or "(none installed)"
    print(
        f"No content-type matched {args.source}. Available: {available}. Re-run with --as <name>.",
        file=sys.stderr,
    )
    return INGEST_ROUTE_FAILED_EXIT


def _ingest_event_from_result(
    source: str, result: Routed | Ambiguous | NoMatch, now: datetime
) -> IngestRoutedEvent:
    """Translate a ``RouteResult`` into the journaled event shape.

    Every outcome — single match, ambiguous, no match — produces one
    ``ingest.routed`` line so ``wiki doctor`` and future
    ``journal explain`` can reconstruct what the user tried.
    """

    if isinstance(result, Routed):
        return IngestRoutedEvent(
            timestamp=now,
            by=INGEST_VEHICLE,
            source=source,
            content_type=result.content_type,
            candidates=[result.content_type],
            via=result.via,
            signals=result.signals,
        )
    if isinstance(result, Ambiguous):
        return IngestRoutedEvent(
            timestamp=now,
            by=INGEST_VEHICLE,
            source=source,
            content_type=None,
            candidates=result.candidates,
            via="auto",
            signals=[],
        )
    return IngestRoutedEvent(
        timestamp=now,
        by=INGEST_VEHICLE,
        source=source,
        content_type=None,
        candidates=[],
        via="auto",
        signals=[],
    )


RESOLVE_VEHICLE = "wiki-conflict"


def _cmd_resolve(args: argparse.Namespace) -> int:
    """Commit a user-mediated merge of a ``<path>.proposed`` sidecar.

    Vault-side ``wiki-conflict`` skill drives this after the user picks a
    resolution. Three input modes — ``--keep`` (re-baseline to the user's
    on-disk version, discard the kit's proposal), ``--accept`` (write the
    sidecar's content verbatim), or stdin (a custom merge the user typed
    or piped). Wired to :func:`write_helper.resolve_proposal` (ADR-0004
    §Mechanics step 6).

    The subcommand exists because retro-review #F-B2 found the
    vault-side SKILL.md calling a CLI verb that didn't exist; argparse
    refused on every fresh `wiki init`.
    """

    from llm_wiki_kit.write_helper import resolve_proposal

    vault_root = Path.cwd().resolve()
    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(
            f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl. "
            "Run `wiki init <path> --recipe <name>` first."
        )

    path = Path(args.path)
    abs_path = path if path.is_absolute() else (vault_root / path)

    if args.keep:
        if not abs_path.is_file():
            raise WikiError(
                f"--keep needs '{path}' to exist on disk (the user's version "
                "to re-baseline to); the file is missing"
            )
        content = abs_path.read_text(encoding="utf-8")
    elif args.accept:
        sidecar = abs_path.with_name(abs_path.name + ".proposed")
        if not sidecar.is_file():
            raise WikiError(
                f"--accept needs '{sidecar.relative_to(vault_root)}' on disk "
                "but no .proposed sidecar is present for this path"
            )
        content = sidecar.read_text(encoding="utf-8")
    else:
        content = sys.stdin.read()

    resolve_proposal(
        path=abs_path,
        content=content,
        by=RESOLVE_VEHICLE,
        journal_path=journal_path,
    )

    print(f"Resolved {args.path}.")
    return 0


def _lock_journal_path() -> Path:
    """Resolve the current vault's journal path or raise ``WikiError``.

    Centralizes the "must be a vault" check ``_cmd_lock_acquire`` and
    ``_cmd_lock_release`` share. Matches the pattern in ``_cmd_add``,
    ``_cmd_doctor``, and ``_cmd_ingest``: ``wiki lock`` is a vault
    operation, not a vault-bootstrap operation, so running it outside
    a vault is a one-line user error rather than a silent no-op.
    """

    vault_root = Path.cwd().resolve()
    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(
            f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl. "
            "Run `wiki init <path> --recipe <name>` first."
        )
    return journal_path


def _read_holder_file(holder_path: Path) -> tuple[str, str, str | None] | None:
    """Parse ``.wiki.journal/lock`` into ``(by, iso_timestamp, reason)`` or ``None``.

    Format pinned by ``journal._write_holder_file``: two lines (``by``,
    iso ts) without a reason, three with. A file present but shorter
    than two lines is a corruption we treat as "no holder" — the spec's
    contract is that a holder file in this state shouldn't exist, and
    refusing to acquire because of it would block the vault on a
    corruption nobody is around to fix. ``wiki doctor``'s stale-lock
    check is the diagnostic surface for that case.
    """

    if not holder_path.is_file():
        return None
    lines = holder_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        return None
    by = lines[0]
    ts = lines[1]
    reason = lines[2] if len(lines) >= 3 else None
    return by, ts, reason


def _probe_lock_contention(journal_path: Path) -> bool | None:
    """Tri-state probe: ``True`` held, ``False`` free, ``None`` cannot tell.

    Opens the journal in append mode and tries ``LOCK_EX | LOCK_NB``.
    The close of the ``with`` block releases the lock implicitly (BSD
    flock is fd-bound; no explicit ``LOCK_UN`` needed).

    - ``True`` on ``EAGAIN``/``EWOULDBLOCK`` — another process holds it.
    - ``False`` on a clean acquire — no live lock holder.
    - ``None`` on ``EOPNOTSUPP``/``ENOTSUP``/``ENOLCK`` — the filesystem
      doesn't support advisory locking, so the probe can't distinguish
      "free" from "held". Callers must not infer "free" from this:
      ``wiki lock acquire`` skips the stale-reclaim audit on ``None``
      to avoid emitting a "wiki-doctor reclaimed a stale lock" event
      on every invocation against a synced holder file (iCloud Drive,
      NFS without lockd, etc.).

    Any other ``OSError`` propagates: a real disk error should not be
    silently re-interpreted as a free lock.
    """

    with journal_path.open("a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return True
            if exc.errno in (errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOLCK):
                return None
            raise
        return False


def _cmd_lock_acquire(args: argparse.Namespace) -> int:
    """Take the journal lock on behalf of a Claude-session manual hold.

    Non-blocking by design (spec §Behavior "Acquire-side contention
    semantics"). The CLI takes three signals into account:

    - **No holder file** — fresh acquire path. Enter
      ``transaction(persist=True, nonblocking=True)``. If a competitor
      grabs the lock first, ``LockUnavailableError`` propagates and we
      exit ``LOCK_HELD_EXIT``.
    - **Holder file + lock genuinely held** (LOCK_NB probe returns
      ``True``) — print the holder + acquired-at, exit
      ``LOCK_HELD_EXIT``. No journal write.
    - **Holder file + lock free** (probe returns ``False``) — the
      previous holder is dead. Append
      ``LockReleasedEvent(by="wiki-doctor", reason="stale lock reclaimed")``
      for clean audit (spec §Edge cases), then enter the same
      non-blocking transaction. If a competitor wins between the
      reclaim event and the transaction's own ``flock``, the
      ``LockUnavailableError`` arm catches it: we re-read the holder
      file to surface the *new* holder (whichever process won) and
      exit ``LOCK_HELD_EXIT`` — no deadlock, no spurious holder file
      overwrite.
    - **Holder file + probe is ``None``** (unsupported filesystem —
      iCloud Drive, NFS without lockd, etc.) — we cannot tell free
      from held. Skip the reclaim audit (we have no evidence to
      reclaim) and fall through to the non-blocking transaction.
      ``flock`` itself logs the unsupported-FS warning once per
      resolved path, so the operator still sees the degraded regime.

    ``transaction(persist=True)`` writes the holder file and stashes
    the fd in ``journal._PERSISTED_FDS`` so the OS lock survives the
    CLI's process exit. If the reclaim event's own ``append_event``
    raises (fsync EIO, disk full), the ``OSError`` propagates as
    traceback — matching spec §Error cases for fsync failures across
    every other CLI handler.
    """

    # Reject newlines in --by/--reason: the holder file is a
    # line-oriented format (``<by>\n<iso>\n[<reason>]``) and embedded
    # newlines would corrupt subsequent reads — including
    # ``wiki doctor``'s stale-lock check. Reject at the boundary
    # rather than escape, so the audit values match what the user
    # typed.
    if "\n" in args.by or "\r" in args.by:
        raise WikiError("--by must not contain newline characters")
    if args.reason is not None and ("\n" in args.reason or "\r" in args.reason):
        raise WikiError("--reason must not contain newline characters")

    journal_path = _lock_journal_path()

    holder_path = journal_path.parent / "lock"
    existing = _read_holder_file(holder_path)
    reclaimed_from: str | None = None
    if existing is not None:
        probe = _probe_lock_contention(journal_path)
        if probe is True:
            by_holder, ts_holder, _ = existing
            print(
                f"lock held by {by_holder} since {ts_holder}",
                file=sys.stderr,
            )
            return LOCK_HELD_EXIT
        if probe is False:
            try:
                append_event(
                    journal_path,
                    LockReleasedEvent(
                        timestamp=datetime.now(UTC),
                        by=STALE_LOCK_RECLAIM_BY,
                        reason=STALE_LOCK_RECLAIM_REASON,
                    ),
                    nonblocking=True,
                )
                reclaimed_from = existing[0]
            except LockUnavailableError:
                # A competitor won between the probe and our reclaim
                # write. Don't hang on a blocking flock; surface the
                # new holder and exit.
                current = _read_holder_file(holder_path)
                if current is not None:
                    by_h, ts_h, _ = current
                    print(f"lock held by {by_h} since {ts_h}", file=sys.stderr)
                else:
                    print(
                        "lock held by another process (holder file missing)",
                        file=sys.stderr,
                    )
                return LOCK_HELD_EXIT
        # probe is None: unsupported FS. Skip the reclaim audit
        # (the spec's "stale" claim would be a lie) and fall
        # through to the non-blocking transaction.

    try:
        with transaction(
            journal_path,
            by=args.by,
            reason=args.reason,
            persist=True,
            nonblocking=True,
        ):
            # No body: ``persist=True`` stashes the fd on clean exit so
            # the OS lock outlives this generator. The holder file and
            # ``LockAcquiredEvent`` are written by ``transaction()``.
            pass
    except LockUnavailableError:
        # Race: a competitor won between our probe (or initial check)
        # and the transaction's LOCK_NB. Re-read the holder file to
        # name whoever's holding it now, then exit with the contention
        # code rather than blocking.
        current = _read_holder_file(holder_path)
        if current is not None:
            by_h, ts_h, _ = current
            print(f"lock held by {by_h} since {ts_h}", file=sys.stderr)
        else:
            print(
                "lock held by another process (holder file missing)",
                file=sys.stderr,
            )
        return LOCK_HELD_EXIT

    # One-line confirmation on the happy path so an operator running
    # ``wiki lock acquire`` interactively sees that the hold landed
    # (the command otherwise exits 0 silently). The reclaim suffix
    # surfaces what was journal-only: that we just deemed a prior
    # holder stale.
    suffix = (
        f" (reclaimed stale lock previously held by {reclaimed_from})"
        if reclaimed_from is not None
        else ""
    )
    reason_part = f", reason: {args.reason}" if args.reason else ""
    print(f"Acquired lock for {args.by}{reason_part}.{suffix}")
    return 0


def _cmd_lock_release(args: argparse.Namespace) -> int:
    """Drop the lock the matching ``wiki lock acquire`` took.

    Reads the holder file to identify the prior actor, refuses on
    ``--by`` mismatch unless ``--force`` is passed, then closes any
    persisted fd this process is still holding (the common case: same
    Claude session acquired and now releases), appends a
    ``LockReleasedEvent``, and deletes the holder file. With no holder
    file, exits 0 silently — spec §Outputs makes "release on an
    unheld lock" a no-op and the vault-side ``wiki-lock`` SKILL.md
    already documents this as harmless.

    The release event's ``by`` is ``--by`` when provided, otherwise the
    holder's recorded ``by`` — the audit names whoever ran the release.
    A ``--force`` override therefore records the *forcing* actor, not
    the prior holder, which is what an operator looking at
    ``wiki journal tail`` wants to see.
    """

    journal_path = _lock_journal_path()

    holder_path = journal_path.parent / "lock"
    existing = _read_holder_file(holder_path)
    if existing is None:
        # No holder file → silent zero. The spec accepts a
        # release-against-nothing as harmless.
        return 0

    by_holder, ts_holder, _reason_holder = existing
    if args.by is not None and args.by != by_holder and not args.force:
        print(
            f"lock held by {by_holder} since {ts_holder}; pass --force to override",
            file=sys.stderr,
        )
        return WIKI_ERROR_EXIT

    # Drop any persisted fd this process is still holding before the
    # release-event append. Without this, ``append_event``'s
    # ``LOCK_EX`` would (on BSD-flock semantics) block on the same
    # process's own held fd. Cross-process release paths see an
    # empty stash; ``_release_persisted_fd`` is a no-op there.
    _release_persisted_fd(journal_path)

    release_by = args.by if args.by is not None else by_holder
    append_event(
        journal_path,
        LockReleasedEvent(
            timestamp=datetime.now(UTC),
            by=release_by,
        ),
    )

    try:
        holder_path.unlink()
    except FileNotFoundError:
        # Already gone — desired end-state, race with another
        # release is fine.
        pass

    return 0


RUN_HELP_TOKENS: frozenset[str] = frozenset({"--help", "-h"})


def _render_dispatch_value(value: object) -> str:
    """Render an effective input value for stdout echo.

    Per ``docs/specs/task-17-wiki-run/spec.md`` §Outputs:
    booleans → lowercase ``true``/``false``; ints → ``str(int)``;
    lists → comma-joined element ``str()`` (no brackets, no
    quoting); anything else → ``str(value)``. The renderer is
    deliberately asymmetric with the journal ``args`` field, which
    keeps the user's typed casing — see §Non-goals.
    """

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return ",".join(_render_dispatch_value(element) for element in value)
    return str(value)


def _cmd_run(args: argparse.Namespace) -> int:
    """Validate args against the operation contract and journal one run.

    Real handler since RFC-0001 Task 17 (dispatch) + RFC-0003
    (``--exec``). The orchestrator is in :mod:`llm_wiki_kit.run`;
    this handler is the I/O boundary.

    The ``--help``/``-h`` short-circuit lives here (not in
    ``dispatch``) because only the CLI has access to the subparser
    needed to ``print_help``. Pre-scanning ``args.op_args`` for an
    exact match on either token (no ``=``, no trailing characters)
    sidesteps ``argparse.REMAINDER``'s capture; value-form tokens
    like ``--help=false`` flow through the normal validation path
    and produce ``invalid_args``.

    With ``--exec``: after dispatch, the kit shells out to ``claude``
    in headless mode (ADR-0009). The dispatch line is still printed
    in both flows; on exec failure (non-zero exit / timeout /
    conflict-refused) the kit prints a failure summary to stderr
    and exits ``WIKI_ERROR_EXIT``. Without ``--exec``: behavior is
    byte-identical to the dispatch-only contract.
    """

    op_args: list[str] = list(args.op_args or [])
    if any(token in RUN_HELP_TOKENS for token in op_args):
        # The `wiki run` subparser is stashed on the namespace by
        # ``build_parser`` via ``set_defaults(_subparser=run)``, so
        # the short-circuit can call ``print_help`` directly without
        # iterating ``parser._actions`` (which depends on argparse's
        # private API surface).
        args._subparser.print_help()
        return 0

    vault_root = Path.cwd().resolve()
    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(
            f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl. "
            "Run `wiki init <path> --recipe <name>` first."
        )

    kit_root = args.kit_root if args.kit_root is not None else _kit_root()

    if getattr(args, "exec_run", False):
        return _cmd_run_exec(
            args=args,
            vault_root=vault_root,
            kit_root=kit_root,
            journal_path=journal_path,
        )

    # Local import keeps the module-load surface narrow — dispatch
    # is the only consumer here.
    from llm_wiki_kit.run import dispatch

    result = dispatch(
        args.operation,
        op_args,
        vault_root=vault_root,
        kit_root=kit_root,
        journal_path=journal_path,
        now=datetime.now(UTC),
    )

    if result.status == "invalid_args":
        # The journal already carries the failing args + error.
        # Surface a one-liner to stderr and exit with the standard
        # wiki-error code.
        assert result.error is not None  # invariant pinned by DispatchResult.__post_init__
        print(
            f"wiki run {result.operation}: {result.error}",
            file=sys.stderr,
        )
        return WIKI_ERROR_EXIT

    # Happy path: one header line + one `  name=value` line per
    # effective input, sorted by name.
    print(f"Dispatched {result.operation}. Run `{result.skill}` in your Claude session.")
    for name in sorted(result.parsed):
        print(f"  {name}={_render_dispatch_value(result.parsed[name])}")
    return 0


def _cmd_run_exec(
    *,
    args: argparse.Namespace,
    vault_root: Path,
    kit_root: Path,
    journal_path: Path,
) -> int:
    """``wiki run --exec`` branch — see ``docs/specs/wiki-run-exec/spec.md``."""

    from llm_wiki_kit.run import dispatch_and_exec

    op_args: list[str] = list(args.op_args or [])
    timeout_env = os.environ.get("WIKI_EXEC_TIMEOUT", "1800")
    try:
        timeout_seconds = int(timeout_env)
    except ValueError as exc:
        raise WikiError(f"WIKI_EXEC_TIMEOUT={timeout_env!r}: must be an integer (seconds)") from exc
    if timeout_seconds <= 0:
        raise WikiError(
            f"WIKI_EXEC_TIMEOUT={timeout_env!r}: must be a positive integer "
            "(seconds); zero or negative would kill the subprocess immediately"
        )
    retention_env = os.environ.get("WIKI_EXEC_LOG_RETENTION_DAYS", "30")
    try:
        log_retention_days = int(retention_env)
    except ValueError as exc:
        raise WikiError(
            f"WIKI_EXEC_LOG_RETENTION_DAYS={retention_env!r}: must be an integer (days)"
        ) from exc
    if log_retention_days < 0:
        raise WikiError(
            f"WIKI_EXEC_LOG_RETENTION_DAYS={retention_env!r}: must be a "
            "non-negative integer (0 disables rotation)"
        )
    max_budget_usd = os.environ.get("WIKI_EXEC_MAX_BUDGET_USD")

    # Observability — record which binary the kit resolved so the user
    # reviewing recent runs can see what was executed. (Security review:
    # the explicit log line is the only signal that --claude-binary or
    # WIKI_CLAUDE_BINARY pointed somewhere unexpected.)
    from llm_wiki_kit.run import _locate_claude

    resolved_binary = _locate_claude(override=args.claude_binary)
    if resolved_binary is not None:
        print(
            f"wiki-run-exec: invoking {resolved_binary}",
            file=sys.stderr,
        )

    result = dispatch_and_exec(
        args.operation,
        op_args,
        vault_root=vault_root,
        kit_root=kit_root,
        journal_path=journal_path,
        now=datetime.now(UTC),
        claude_binary=args.claude_binary,
        skill_path_override=args.skill_path,
        timeout_seconds=timeout_seconds,
        log_retention_days=log_retention_days,
        max_budget_usd=max_budget_usd,
    )

    dispatch_result = result.dispatch
    if dispatch_result.status == "invalid_args":
        # CT-2: exec phase skipped; mirror the non-exec contract.
        assert dispatch_result.error is not None
        print(
            f"wiki run {dispatch_result.operation}: {dispatch_result.error}",
            file=sys.stderr,
        )
        return WIKI_ERROR_EXIT

    # Always print the dispatch line so the user has a record.
    print(
        f"Dispatched {dispatch_result.operation}. "
        f"Run `{dispatch_result.skill}` in your Claude session."
    )
    for name in sorted(dispatch_result.parsed):
        print(f"  {name}={_render_dispatch_value(dispatch_result.parsed[name])}")

    if result.exec_status == "succeeded":
        # Spec §"Happy path" step 5a: full success line carries the
        # exit code, duration, and log path.
        duration = (
            f"{result.duration_seconds:.0f}s" if result.duration_seconds is not None else "?s"
        )
        log = result.log_path or "(none)"
        print(
            f"Exec succeeded for {dispatch_result.operation} "
            f"(exit {result.exit_code}, {duration}, log: {log})."
        )
        return 0

    # exec_status is one of failed_conflict / failed_exit / failed_timeout.
    print(
        f"wiki run --exec {dispatch_result.operation}: failed ({result.exec_status}); "
        f"see inbox/scheduled-failures/{dispatch_result.dispatch_event_id}.md",
        file=sys.stderr,
    )
    return WIKI_ERROR_EXIT


RESEARCH_VEHICLE = "wiki-research"


def _cmd_research(args: argparse.Namespace) -> int:
    """Dispatch a query to a configured research provider.

    Three sequenced boundary checks before the dispatcher runs (spec
    §Behavior happy path steps 1-2):

    1. Vault root must contain ``.wiki.journal/journal.jsonl`` — same
       check ``_cmd_add`` / ``_cmd_doctor`` / ``_cmd_ingest`` use.
    2. When ``--out`` is set, the path must resolve under the vault
       root. Absolute paths, ``..`` escapes, and symlinks that resolve
       out of tree are rejected *before* any HTTP attempt — no
       ``research.query`` event is appended.

    After ``research.dispatch_query`` returns, two flows:

    - **stdout flow** (one event total): bare ``append_event``, then
      ``print(markdown)``.
    - **--out flow** (two events): wrap the event-and-write pair in
      ``journal.transaction(by="wiki-research", reason="research
      <slug>")`` so a concurrent ``wiki add`` cannot interleave its
      own events. Inside the transaction: ``append_event(result.event)``
      then ``safe_write(out_path, markdown, by="wiki-research",
      journal_path=...)``.

    On ``ResearchDispatchError``: append ``exc.event`` (with the
    journal-append wrapped so a fsync failure surfaces as ``__cause__``
    of the original dispatch exception, not the other way around — spec
    invariant 10). Then re-raise; ``main()`` catches as ``WikiError``
    and exits 2.
    """

    from llm_wiki_kit.research import ResearchDispatchError, dispatch_query
    from llm_wiki_kit.write_helper import WriteResult, safe_write

    vault_root = Path.cwd().resolve()
    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(
            f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl. "
            "Run `wiki init <path> --recipe <name>` first."
        )

    out_relative: str | None = None
    out_abs: Path | None = None
    if args.out is not None:
        out_relative, out_abs = _resolve_out_path(args.out, vault_root)

    now = datetime.now(UTC)
    try:
        result = dispatch_query(args.query, args.provider, vault_root, now=now)
    except ResearchDispatchError as exc:
        # Provider-side runtime failure: journal the prepared error
        # event *before* re-raising so the audit trail records the
        # attempt. If the append itself raises (fsync EIO, disk full),
        # preserve the original dispatch error as the primary signal —
        # spec invariant 10.
        try:
            append_event(journal_path, exc.event)
        except OSError as journal_exc:
            raise exc from journal_exc
        raise

    if out_abs is None:
        # stdout flow — one event, no transaction.
        append_event(journal_path, result.event)
        print(result.markdown, end="" if result.markdown.endswith("\n") else "\n")
        return 0

    # --out flow: two events, wrapped in a transaction.
    out_event = result.event.model_copy(update={"result_path": out_relative})
    with transaction(
        journal_path,
        by=RESEARCH_VEHICLE,
        reason=f"research {result.event.provider}",
    ):
        append_event(journal_path, out_event)
        write_result = safe_write(
            out_abs,
            result.markdown,
            by=RESEARCH_VEHICLE,
            journal_path=journal_path,
        )

    if write_result is WriteResult.PROPOSAL:
        sidecar = out_abs.with_name(out_abs.name + ".proposed")
        print(
            f"Wrote {sidecar.relative_to(vault_root)} (drift detected on {out_relative}); "
            f"run the wiki-conflict skill to merge."
        )
    return 0


def _resolve_out_path(raw: str, vault_root: Path) -> tuple[str, Path]:
    """Resolve ``--out`` to ``(vault-relative posix, absolute Path)``.

    Rejects absolute paths, ``..`` escapes, and symlinks that resolve
    out of the vault tree. ``Path.resolve(strict=False)`` follows
    symlinks in any existing prefix even when the leaf doesn't exist
    yet — the symlink-escape test exercises that path. The resolved
    location must be the vault root or a descendant of it.

    Raises ``WikiError`` with a one-line message; no journal event has
    been appended at this point in the CLI's flow.
    """

    candidate = Path(raw)
    if candidate.is_absolute():
        raise WikiError(f"--out path must be relative to the vault root: got {raw!r}")
    abs_path = (vault_root / candidate).resolve(strict=False)
    resolved_root = vault_root.resolve()
    try:
        rel = abs_path.relative_to(resolved_root)
    except ValueError as exc:
        raise WikiError(
            f"--out path must resolve under the vault root: "
            f"{raw!r} resolves to {abs_path}, outside {resolved_root}"
        ) from exc
    return rel.as_posix(), abs_path


def _cmd_search(args: argparse.Namespace) -> int:
    """Search the vault's ``wiki/`` tree for a literal substring.

    Read-only — no journal events, no writes. Boundary check (vault
    root, non-empty query, ``--top`` ≥ 1) before any ripgrep subprocess
    fires. See ``docs/specs/wiki-search/spec.md``.
    """

    from llm_wiki_kit.search import SearchFilters, format_results, run_search

    vault_root = Path.cwd().resolve()
    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(
            f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl. "
            "Run `wiki init <path> --recipe <name>` first."
        )

    query: str = args.query
    if not query:
        raise WikiError("search query must not be empty")

    if args.top < 1:
        raise WikiError("--top must be ≥ 1")

    # Empty-string filter values would degenerate to "only pages whose
    # frontmatter field is missing or empty" — almost certainly not what
    # the caller meant; reject rather than ship the surprise.
    for flag, value in (
        ("--type", args.search_type),
        ("--tag", args.tag),
        ("--status", args.status),
    ):
        if value is not None and value == "":
            raise WikiError(f"{flag} must not be empty")

    filters = SearchFilters(
        type=args.search_type,
        tag=args.tag,
        status=args.status,
    )
    hits = run_search(vault_root, query, filters, args.top)
    print(format_results(hits), end="")
    return 0


# Per-event-type ``<summary>`` field ordering for ``tail``/``grep``. Pinned by
# ``docs/specs/wiki-journal-readers/spec.md`` §Outputs. Each entry is a
# tuple of ``(field_name, label, omit_when_none)``:
#
# - ``field_name`` — the Pydantic field on the event class to read.
# - ``label`` — the text rendered before ``=``. Differs from the field
#   name where the spec picks a shorter or friendlier word
#   (``vault=`` for ``vault_name``, ``from=`` / ``to=`` for the
#   ``primitive.upgrade`` version pair, ``proposed=`` for the
#   proposal path).
# - ``omit_when_none`` — when ``True`` and the value is ``None``, the
#   pair is skipped entirely instead of rendered as ``label=(none)``.
#   Used for fields that are documented as "plus ... when set" in the
#   spec (currently only ``page.conflict_resolved.region``).
#
# Adding a new event class without a row here is a spec change:
# ``_format_event_line`` raises ``KeyError`` on lookup so a missing
# mapping fails loudly at the first call.
_SummaryField = tuple[str, str, bool]
_EVENT_SUMMARY_FIELDS: dict[type[Event], tuple[_SummaryField, ...]] = {
    VaultInitEvent: (
        ("vault_name", "vault", False),
        ("recipe", "recipe", False),
    ),
    # ``vault.git_initialized`` carries no per-event payload fields (see
    # ``docs/specs/wiki-init-git/spec.md`` §Outputs); the summary is
    # empty. The empty tuple keeps the row present so the "missing
    # summary mapping is loud" KeyError invariant still pins coverage.
    VaultGitInitializedEvent: (),
    PrimitiveInstallEvent: (
        ("primitive", "primitive", False),
        ("version", "version", False),
    ),
    PrimitiveRemoveEvent: (("primitive", "primitive", False),),
    PrimitiveUpgradeEvent: (
        ("primitive", "primitive", False),
        ("from_version", "from", False),
        ("to_version", "to", False),
    ),
    ManagedRegionWriteEvent: (
        ("file", "file", False),
        ("region", "region", False),
    ),
    IngestRoutedEvent: (
        ("source", "source", False),
        ("content_type", "content_type", False),
        ("via", "via", False),
    ),
    SourceIngestEvent: (
        ("source", "source", False),
        ("content_type", "content_type", False),
    ),
    PageWriteEvent: (("path", "path", False),),
    PageProposalEvent: (
        ("path", "path", False),
        ("proposed_path", "proposed", False),
    ),
    PageConflictResolvedEvent: (
        ("path", "path", False),
        # Spec §Outputs: "(plus ` region=<region>` when set)" — omit
        # the pair entirely when no region is recorded.
        ("region", "region", True),
    ),
    OperationRunEvent: (
        ("operation", "operation", False),
        ("status", "status", False),
    ),
    OperationExecFailedEvent: (
        ("operation", "operation", False),
        ("reason", "reason", False),
        ("exit_code", "exit_code", False),
    ),
    ResearchQueryEvent: (
        ("provider", "provider", False),
        ("status", "status", False),
    ),
    LintRunEvent: (
        ("status", "status", False),
        ("issues", "issues", False),
    ),
    ConfigSetEvent: (("key", "key", False),),
    LockAcquiredEvent: (("reason", "reason", False),),
    LockReleasedEvent: (("reason", "reason", False),),
}


def _sanitize_value(value: object) -> str:
    """Render ``value`` as a single-line, tab-free string.

    Per spec §Outputs: tabs and newlines in any rendered value are
    replaced with a single space so the TSV format stays splittable and
    ``explain``'s "one field per line" block keeps its shape. ``None``
    becomes the literal ``(none)``; lists join with ``, ``.
    """

    if value is None:
        text = "(none)"
    elif isinstance(value, list):
        text = ", ".join(_sanitize_value(item) for item in value)
    else:
        text = str(value)
    return text.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def _format_event_line(line_no: int, event: Event) -> str:
    """Format one event as the tab-separated ``tail``/``grep`` row.

    Layout pinned by spec §Outputs:
    ``<line>\\t<timestamp>\\t<by>\\t<type>\\t<summary>``. Raises
    ``KeyError`` if ``type(event)`` has no row in
    ``_EVENT_SUMMARY_FIELDS`` — silent fallthrough would defeat the
    "missing summary mapping is loud" invariant.
    """

    fields = _EVENT_SUMMARY_FIELDS[type(event)]
    pairs: list[str] = []
    for field_name, label, omit_when_none in fields:
        value = getattr(event, field_name)
        if value is None and omit_when_none:
            continue
        pairs.append(f"{label}={_sanitize_value(value)}")
    summary = " ".join(pairs)
    return "\t".join(
        [
            str(line_no),
            _sanitize_value(event.timestamp.isoformat()),
            _sanitize_value(event.by),
            event.type,
            summary,
        ]
    )


def _format_event_block(line_no: int, total: int, event: Event) -> str:
    """Format one event as ``explain``'s multi-line block.

    Header is always the literal ``.wiki.journal/journal.jsonl`` path
    regardless of cwd within the vault, so the AC is string-comparable
    and the output reads consistently from any subdirectory.
    """

    # Skip the three fields the header already shows when iterating
    # ``model_fields``. Pinned to the ``_EventBase`` shape: ``timestamp``
    # + ``by`` come from the base class; ``type`` is the discriminator
    # every concrete event redeclares as ``Literal[...]``. A future
    # subclass that shadowed one of these names would need a spec edit
    # anyway (the header reading would no longer reflect the field).
    already_printed = {"timestamp", "by", "type"}
    lines = [
        f"Event {line_no} of {total} in .wiki.journal/journal.jsonl",
        f"Type:      {event.type}",
        f"Timestamp: {_sanitize_value(event.timestamp.isoformat())}",
        f"By:        {_sanitize_value(event.by)}",
        "",
    ]
    for name in type(event).model_fields:
        if name in already_printed:
            continue
        lines.append(f"{name}: {_sanitize_value(getattr(event, name))}")
    return "\n".join(lines)


def _load_journal_events_with_lines(vault_root: Path) -> tuple[Path, list[tuple[int, Event]]]:
    """Pre-flight the vault, load events, and recover absolute line numbers.

    Returns ``(journal_path, [(line_no, event), ...])`` where ``line_no``
    is the 1-based absolute file line number of each event.

    Streams the file once, parsing each non-blank line via
    :func:`journal.parse_event_line`. A separate ``read_events`` call
    followed by a re-walk would risk a desync under a concurrent
    writer (the writer's exclusive ``flock`` makes the window narrow
    but real); single-pass parsing rules out the race by
    construction. The blank-line decision matches
    ``journal._parse_line`` exactly (``raw.strip() == ""``) so the
    line numbers ``tail`` prints address the same lines ``explain``
    accepts.

    A malformed JSON or schema-invalid line surfaces as ``WikiError``
    with the offending line number; the lenient reader is reserved
    for ``wiki doctor`` per ADR-0002 §Negative. ``OSError`` from the
    open/read is wrapped into ``WikiError`` so the CLI's one-line
    error contract holds.
    """

    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    if not journal_path.is_file():
        raise WikiError(
            f"not a wiki vault: {vault_root} has no .wiki.journal/journal.jsonl. "
            "Run `wiki init <path> --recipe <name>` first."
        )

    pairs: list[tuple[int, Event]] = []
    try:
        with journal_path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                try:
                    event = parse_event_line(raw, line_no)
                except JournalCorruptError as exc:
                    raise WikiError(f"journal corruption at line {exc.line}: {exc.reason}") from exc
                if event is None:
                    continue
                pairs.append((line_no, event))
    except OSError as exc:
        raise WikiError(f"cannot read journal at {journal_path}: {exc}") from exc
    return journal_path, pairs


def _parse_positive_int(raw: str, flag: str) -> int:
    """Parse a CLI argument expected to be a positive integer.

    The journal-reader handlers take ``--lines`` and ``event`` as
    strings so the user sees one ``WikiError`` shape for "invalid
    integer" and "≤ 0" instead of argparse's stderr usage line. Spec
    §Error cases pins the exact message text.
    """

    try:
        value = int(raw)
    except ValueError as exc:
        raise WikiError(f"{flag} must be a positive integer") from exc
    if value <= 0:
        raise WikiError(f"{flag} must be a positive integer")
    return value


def _cmd_journal_tail(args: argparse.Namespace) -> int:
    n = _parse_positive_int(args.lines, "--lines")
    vault_root = Path.cwd().resolve()
    _, pairs = _load_journal_events_with_lines(vault_root)
    for line_no, event in pairs[-n:]:
        print(_format_event_line(line_no, event))
    return 0


def _cmd_journal_grep(args: argparse.Namespace) -> int:
    if args.pattern == "":
        raise WikiError("grep pattern must be non-empty")
    vault_root = Path.cwd().resolve()
    _, pairs = _load_journal_events_with_lines(vault_root)
    type_filter: str | None = args.event_type
    for line_no, event in pairs:
        if type_filter is not None and event.type != type_filter:
            continue
        if args.pattern in dump_event_json(event):
            print(_format_event_line(line_no, event))
    return 0


def _cmd_journal_explain(args: argparse.Namespace) -> int:
    n = _parse_positive_int(args.event, "event")
    vault_root = Path.cwd().resolve()
    _, pairs = _load_journal_events_with_lines(vault_root)
    by_line = {line_no: event for line_no, event in pairs}
    if n not in by_line:
        raise WikiError(f"no event at line {n} (journal has {len(pairs)} events)")
    print(_format_event_block(n, len(pairs), by_line[n]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    # ``--verbose`` lives on a parent parser so it appears in both
    # ``wiki --help`` and every ``wiki <cmd> --help``, and so it works
    # either side of the subcommand (``wiki --verbose init …`` or
    # ``wiki init --verbose …``). ``default=argparse.SUPPRESS`` is the
    # critical bit: without it, the subparser's argparse-default would
    # overwrite a True value the top-level parser had already set on the
    # namespace — the classic "shared flag erased by subparser default"
    # argparse footgun. ``_is_verbose`` reads with ``getattr(..., False)``
    # so the unset case stays clean.
    verbose_parent = argparse.ArgumentParser(add_help=False)
    verbose_parent.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "Print a Python traceback after the error message when a command "
            "fails. Equivalent to setting WIKI_DEBUG to one of 1, true, yes, "
            "or on (case-insensitive) in the environment."
        ),
    )

    parser = argparse.ArgumentParser(
        prog="wiki",
        description="Build and maintain an LLM-readable markdown wiki.",
        parents=[verbose_parent],
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    init = subparsers.add_parser(
        "init",
        parents=[verbose_parent],
        help="Create a new vault from a recipe.",
    )
    init.add_argument("path", help="Directory to create the vault in.")
    init.add_argument(
        "--recipe", required=True, help="Recipe name (e.g. family, work-os, personal)."
    )
    init.add_argument(
        "--no-git",
        dest="no_git",
        action="store_true",
        help=(
            "Skip git repo initialization and the initial commit. The kit's "
            ".gitignore still ships through the normal render path."
        ),
    )
    init.set_defaults(func=_cmd_init)

    add = subparsers.add_parser(
        "add",
        parents=[verbose_parent],
        help="Install a primitive into the current vault.",
    )
    add.add_argument(
        "primitive",
        help="Primitive in the form <kind>:<name> (e.g. content-type:interview).",
    )
    add.set_defaults(func=_cmd_add)

    upgrade = subparsers.add_parser(
        "upgrade",
        parents=[verbose_parent],
        help="Upgrade installed primitives to their latest versions.",
    )
    upgrade.add_argument(
        "--primitive",
        help="Restrict the upgrade to a single primitive (default: all installed).",
    )
    upgrade.set_defaults(func=_cmd_upgrade)

    doctor = subparsers.add_parser(
        "doctor",
        parents=[verbose_parent],
        help="Validate vault state against the journal.",
    )
    doctor.set_defaults(func=_cmd_doctor)

    ingest = subparsers.add_parser(
        "ingest",
        parents=[verbose_parent],
        help="Route source material to the right content-type ingester.",
    )
    ingest.add_argument("source", help="Path or URL to ingest, or '-' for stdin.")
    ingest.add_argument(
        "--as",
        dest="as_content_type",
        default=None,
        metavar="<name>",
        help="Override auto-detection with an explicit content-type primitive name.",
    )
    ingest.set_defaults(func=_cmd_ingest)

    resolve = subparsers.add_parser(
        "resolve",
        parents=[verbose_parent],
        help="Commit a user-mediated merge of a <path>.proposed sidecar.",
    )
    resolve.add_argument(
        "path",
        help="Vault-relative path of the conflicted page (without the `.proposed` suffix).",
    )
    resolve_mode = resolve.add_mutually_exclusive_group()
    resolve_mode.add_argument(
        "--keep",
        action="store_true",
        help="Discard the kit's proposal; re-baseline to the on-disk content.",
    )
    resolve_mode.add_argument(
        "--accept",
        action="store_true",
        help="Discard user edits; write the .proposed sidecar's content verbatim.",
    )
    resolve.set_defaults(func=_cmd_resolve)

    lock = subparsers.add_parser(
        "lock", help="Acquire or release the journal lock for a multi-event session."
    )
    lock_sub = lock.add_subparsers(dest="lock_command", metavar="<subcommand>")
    lock_sub.required = True

    lock_acquire = lock_sub.add_parser(
        "acquire",
        parents=[verbose_parent],
        help="Acquire the journal lock; exits 3 if another holder is in possession.",
    )
    lock_acquire.add_argument(
        "--by",
        required=True,
        help="Caller identity (e.g. agent name, operation name) recorded in the journal.",
    )
    lock_acquire.add_argument(
        "--reason",
        default=None,
        help="Optional one-line description shown in `wiki journal tail`.",
    )
    lock_acquire.set_defaults(func=_cmd_lock_acquire)

    lock_release = lock_sub.add_parser(
        "release",
        parents=[verbose_parent],
        help="Release the journal lock; silent no-op if no holder file is present.",
    )
    lock_release.add_argument(
        "--by",
        default=None,
        help="Caller identity; must match the holder unless `--force` is passed.",
    )
    lock_release.add_argument(
        "--force",
        action="store_true",
        help="Override a `--by` mismatch; intended for stale-lock recovery.",
    )
    lock_release.set_defaults(func=_cmd_lock_release)

    run = subparsers.add_parser(
        "run",
        parents=[verbose_parent],
        help="Run a named operation.",
    )
    # ``--exec`` and friends sit BEFORE the operation positional so
    # ``argparse.REMAINDER`` doesn't swallow them. See
    # ``docs/specs/wiki-run-exec/spec.md`` §"Contracts with other
    # modules" — pinned by example
    # ``wiki run --exec --claude-binary /opt/claude weekly-digest --window=…``.
    run.add_argument(
        "--exec",
        dest="exec_run",
        action="store_true",
        help=(
            "After dispatch, shell out to the user-installed `claude` CLI "
            "in headless mode (ADR-0009) and journal the outcome."
        ),
    )
    run.add_argument(
        "--claude-binary",
        type=Path,
        default=None,
        help=(
            "Explicit path to the `claude` binary. Wins over WIKI_CLAUDE_BINARY "
            "and shutil.which. Only meaningful with --exec."
        ),
    )
    run.add_argument(
        "--skill-path",
        type=Path,
        default=None,
        help=(
            "Override the SKILL.md path the executor passes to claude. Default "
            "resolves to <vault>/.claude/skills/<contract.skill or operation>/SKILL.md."
        ),
    )
    run.add_argument("operation", help="Operation name (e.g. weekly-digest).")
    # `argparse.REMAINDER` captures everything after `<operation>`
    # verbatim — the operation's contract.yaml drives the vocabulary
    # for these flags, not argparse. Side-effect: `--verbose` and
    # `--help` after the operation positional are captured here;
    # `_cmd_run` pre-scans for `--help`/`-h` (see
    # ``docs/specs/task-17-wiki-run/spec.md``).
    run.add_argument(
        "op_args",
        nargs=argparse.REMAINDER,
        help=(
            "Operation-specific args of the form --name=value or --name. See "
            "`wiki run <operation>'s` contract.yaml for the field set. "
            "Put `--verbose` BEFORE the subcommand (`wiki --verbose run …`)."
        ),
    )
    # Stash the subparser on the namespace so ``_cmd_run``'s
    # ``--help`` short-circuit can call ``print_help()`` without
    # walking ``parser._actions`` (argparse private API).
    run.set_defaults(func=_cmd_run, _subparser=run)

    research = subparsers.add_parser(
        "research",
        parents=[verbose_parent],
        help="Dispatch a query to a configured research provider.",
    )
    research.add_argument("query", help="The research query.")
    research.add_argument(
        "--provider",
        default=None,
        metavar="<name>",
        help=(
            "Provider slug (e.g. perplexity). Required when more than one "
            "provider is installed; optional when exactly one is."
        ),
    )
    research.add_argument(
        "--out",
        default=None,
        metavar="<path>",
        help=(
            "Vault-relative path to write the markdown answer to "
            "(via safe_write). Drift detection applies. Default: print "
            "to stdout."
        ),
    )
    research.set_defaults(func=_cmd_research)

    search = subparsers.add_parser(
        "search",
        parents=[verbose_parent],
        help="Search the vault (ripgrep tier; FTS5 tier is future work).",
    )
    search.add_argument("query", help="Literal substring to search for in the vault.")
    # ``--type`` lands on the namespace as ``search_type`` so it doesn't
    # collide with Python's built-in ``type``; user-facing flag stays
    # ``--type`` per the SKILL.md.
    search.add_argument(
        "--type",
        dest="search_type",
        default=None,
        metavar="<name>",
        help="Restrict to pages whose frontmatter type equals this value.",
    )
    search.add_argument(
        "--tag",
        default=None,
        metavar="<name>",
        help="Restrict to pages whose frontmatter tags list contains this value.",
    )
    search.add_argument(
        "--status",
        default=None,
        metavar="<name>",
        help="Restrict to pages whose frontmatter status equals this value.",
    )
    search.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="<N>",
        help="Maximum number of ranked results to print (default: 10).",
    )
    search.set_defaults(func=_cmd_search)

    journal = subparsers.add_parser("journal", help="Read the vault journal.")
    journal_sub = journal.add_subparsers(dest="journal_command", metavar="<subcommand>")
    journal_sub.required = True

    journal_tail = journal_sub.add_parser(
        "tail",
        parents=[verbose_parent],
        help="Show the most recent events.",
    )
    # ``--lines`` stays a string so the handler can emit the
    # spec-mandated ``WikiError`` for invalid values (non-integer, ≤0).
    # Argparse's ``type=int`` would intercept "abc" with its own usage
    # line and split the error shape across two channels.
    journal_tail.add_argument(
        "-n",
        "--lines",
        default="10",
        help="Number of events to show (positive integer, default 10).",
    )
    journal_tail.set_defaults(func=_cmd_journal_tail)

    journal_grep = journal_sub.add_parser(
        "grep",
        parents=[verbose_parent],
        help="Filter journal events by pattern.",
    )
    journal_grep.add_argument(
        "--type",
        dest="event_type",
        default=None,
        help=(
            "Restrict matches to events of the given type "
            "(e.g. page.write, ingest.routed). Unknown types yield no matches."
        ),
    )
    journal_grep.add_argument(
        "pattern",
        help=(
            "Substring matched against the event's canonical JSON "
            "(case-sensitive); empty pattern is rejected."
        ),
    )
    journal_grep.set_defaults(func=_cmd_journal_grep)

    journal_explain = journal_sub.add_parser(
        "explain",
        parents=[verbose_parent],
        help="Explain a specific journal event in plain language.",
    )
    # ``event`` stays a string for the same reason ``--lines`` does:
    # one error shape ("event must be a positive integer") for invalid
    # input, emitted by the handler.
    journal_explain.add_argument(
        "event",
        help=(
            "1-based line number in journal.jsonl of the event to explain "
            "(the same number `wiki journal tail` prints in column 1)."
        ),
    )
    journal_explain.set_defaults(func=_cmd_journal_explain)

    return parser


_WIKI_DEBUG_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _is_verbose(args: argparse.Namespace) -> bool:
    """Return True if the user asked for tracebacks on error.

    Either ``--verbose`` on the command line OR ``WIKI_DEBUG`` set to one
    of the documented truthy spellings — ``1``, ``true``, ``yes``, ``on``
    (case-insensitive, whitespace-trimmed). Anything else — ``0``, the
    empty string, ``false``, ``no``, ``off``, an unrecognised word — reads
    as off. We allow-list rather than deny-list so a wrapper script that
    spells "disabled" as ``WIKI_DEBUG=false`` gets the obvious meaning
    instead of the opposite.
    """

    if getattr(args, "verbose", False):
        return True
    return os.environ.get("WIKI_DEBUG", "").strip().lower() in _WIKI_DEBUG_TRUTHY


def main(argv: Sequence[str] | None = None, *, kit_root: Path | None = None) -> int:
    """Entry point. ``kit_root`` overrides the bundled-asset resolver.

    Tests pass ``kit_root=tmp_path`` to point the CLI at a synthetic kit
    layout without monkey-patching module state. Production callers
    (the console script and ``python -m llm_wiki_kit``) leave it
    ``None``; the lazy resolver fires on first asset-touching call.
    See ``docs/specs/wheel-bundled-assets/spec.md`` §Behavior.
    """

    parser = build_parser()
    args = parser.parse_args(argv)
    args.kit_root = kit_root
    func = args.func
    try:
        return int(func(args))
    except WikiError as exc:
        # The CLI boundary: one human-readable line on stderr by default,
        # the full traceback appended after it when the user asked for
        # debugging detail. Stays as a single boundary so a future
        # subcommand can't forget the contract — every handler is free
        # to ``raise WikiError(...)`` and the message lands here.
        print(str(exc), file=sys.stderr)
        if _is_verbose(args):
            traceback.print_exc(file=sys.stderr)
        return WIKI_ERROR_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
