"""Re-apply installed primitives against the running kit's catalog.

Backs the ``wiki upgrade [--primitive <name>]`` CLI handler. Every
installed primitive whose journaled version differs from the catalog
version is re-rendered through ``render_tree`` (which routes file
writes through ``safe_write``) and journaled as a
``PrimitiveUpgradeEvent``. After the per-primitive loop, the managed-
region aggregator runs over every installed primitive — exactly the
same pass ``wiki add`` uses — so contributions from version-unchanged
primitives survive into the composed region body.

The module exposes two surfaces:

* :func:`plan_upgrade` — pure function over a ``VaultState`` + catalog
  list. Returns an :class:`UpgradePlan` naming the version-changed
  primitives, the (catalog-filtered) full installed set, the
  installed-but-missing-from-catalog names, and the louder
  ``no_op_target`` when ``--primitive <name>`` was given and already
  matched.
* :func:`upgrade_primitives` — the runner. Appends one
  ``PrimitiveUpgradeEvent`` per ``to_upgrade`` primitive (event-before-
  disk per ``docs/specs/safe-write-ordering/spec.md``), renders each
  primitive's ``files/`` tree, then runs
  ``aggregate_region_contributions`` over ``plan.all_installed``.
  Returns the vault-relative POSIX paths of every ``.proposed``
  sidecar appended during the run (from both per-primitive renders
  AND aggregator-emitted region-host file drifts).

See ``docs/specs/wiki-upgrade/spec.md`` for the full contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from llm_wiki_kit.errors import WikiError

# Intentional private import of ``_warn_if_install_pipeline_uncached`` —
# pinned by ``docs/specs/wiki-upgrade/spec.md`` §Contracts.upgrade_primitives
# as the runner's first line so the cache-discipline warning fires on
# uncached invocations. If the install module renames this symbol, the
# upgrade runner needs the corresponding rename in the same PR.
from llm_wiki_kit.install import (
    _warn_if_install_pipeline_uncached,
    aggregate_region_contributions,
)
from llm_wiki_kit.journal import append_event, read_events
from llm_wiki_kit.models import (
    PageProposalEvent,
    Primitive,
    PrimitiveUpgradeEvent,
    VaultState,
)
from llm_wiki_kit.primitives import resolve_dependencies
from llm_wiki_kit.render import render_tree

UPGRADE_VEHICLE = "wiki-upgrade"


@dataclass(frozen=True)
class UpgradePlan:
    """The planner's output, consumed by :func:`upgrade_primitives` and the CLI.

    ``to_upgrade`` lists primitives whose installed version differs
    from the catalog (sorted in install order — topological by
    ``requires:``, alphabetical tiebreaker — matching the install
    pipeline's invariant).

    ``all_installed`` is the topologically-sorted installed set
    filtered to primitives currently in the catalog. Primitives no
    longer in the catalog never enter this list so the aggregator
    pass cannot see a missing-snippet-directory shape.

    ``not_in_catalog`` names every installed primitive absent from
    the catalog. ``wiki doctor`` is the primary surfacing mechanism;
    the CLI also prints a one-line stderr hint when this list is
    non-empty (spec §Outputs).

    ``no_op_target`` is ``(name, version)`` when ``--primitive <name>``
    was requested but the catalog already matched the installed
    version. ``None`` otherwise. The CLI uses this to print a louder
    "already at version <V>" message rather than the all-primitives
    "nothing to upgrade" message.
    """

    to_upgrade: list[Primitive] = field(default_factory=list)
    all_installed: list[Primitive] = field(default_factory=list)
    not_in_catalog: list[str] = field(default_factory=list)
    no_op_target: tuple[str, str] | None = None


def plan_upgrade(
    state: VaultState,
    catalog: Sequence[Primitive],
    *,
    only: str | None,
) -> UpgradePlan:
    """Compute the upgrade plan for ``state`` against ``catalog``.

    Pure function — no I/O, no journal mutation. Raises
    :class:`WikiError` when ``only`` is set and the named primitive is
    either not installed or not in the catalog.
    """

    catalog_by_name: dict[str, Primitive] = {p.name: p for p in catalog}
    installed = state.installed_primitives

    if only is not None:
        if only not in installed:
            raise WikiError(
                f"primitive '{only}' is not installed; run `wiki add <kind>:{only}` first"
            )
        if only not in catalog_by_name:
            raise WikiError(
                f"primitive '{only}' is no longer in the kit catalog; "
                "the installed kit version does not ship it"
            )

    # ``all_installed``: every installed primitive that is in the catalog,
    # topologically ordered. Filtered so the aggregator never sees a
    # missing-from-catalog primitive (whose snippet directory may not
    # exist on disk).
    in_catalog_primitives = [catalog_by_name[name] for name in installed if name in catalog_by_name]
    all_installed = resolve_dependencies(in_catalog_primitives) if in_catalog_primitives else []
    not_in_catalog = sorted(name for name in installed if name not in catalog_by_name)

    if only is not None:
        # ``--primitive`` restricts the upgrade to one primitive. The
        # planner already raised when ``only`` was not installed or not
        # in catalog, so the lookup is safe.
        target = catalog_by_name[only]
        if installed[only] == target.version:
            return UpgradePlan(
                to_upgrade=[],
                all_installed=all_installed,
                not_in_catalog=not_in_catalog,
                no_op_target=(only, target.version),
            )
        return UpgradePlan(
            to_upgrade=[target],
            all_installed=all_installed,
            not_in_catalog=not_in_catalog,
            no_op_target=None,
        )

    # All-installed mode: collect every primitive whose installed version
    # disagrees with the catalog (newer or older — record honestly per
    # spec §Edge cases). Order ``to_upgrade`` by filtering
    # ``all_installed`` (already topologically sorted) so the upgrade
    # event sequence matches the install pipeline's invariant without
    # re-running ``resolve_dependencies`` over a non-closed candidate
    # set (which would raise on transitive ``requires:`` not in the
    # candidates list).
    bumped: set[str] = {
        name
        for name, ver in installed.items()
        if name in catalog_by_name and catalog_by_name[name].version != ver
    }
    to_upgrade = [p for p in all_installed if p.name in bumped]

    return UpgradePlan(
        to_upgrade=to_upgrade,
        all_installed=all_installed,
        not_in_catalog=not_in_catalog,
        no_op_target=None,
    )


def upgrade_primitives(
    *,
    plan: UpgradePlan,
    sources: Mapping[str, Path],
    journal_path: Path,
    context: Mapping[str, str],
    state_versions: Mapping[str, str],
    now: datetime,
) -> list[tuple[str, str]]:
    """Apply ``plan.to_upgrade`` against the vault at ``journal_path``.

    Emits one :class:`PrimitiveUpgradeEvent` per ``to_upgrade``
    primitive (event-before-disk; the event is fsync'd before
    ``render_tree`` opens any file under the primitive's ``files/``
    tree). After the per-primitive loop, calls
    :func:`aggregate_region_contributions` over ``plan.all_installed``
    so contributions from version-unchanged primitives survive into
    the composed region body.

    Returns a list of ``(path, proposed_path)`` tuples — both vault-
    relative POSIX paths from each :class:`PageProposalEvent` appended
    during the run, in journal order. The list includes both per-
    primitive renderer-emitted proposals AND aggregator-emitted
    region-host file drifts on shared files (e.g.
    ``frontmatter.schema.yaml``). The CLI prints one drift-line per
    tuple. Returning both fields (rather than just ``proposed_path``)
    decouples the CLI's `Wrote <sidecar> (drift detected on <path>)`
    rendering from the structural assumption that sidecars are always
    named ``<path>.proposed`` — the event already carries both
    values authoritatively.

    Preconditions enforced by ``assert``:

    1. ``plan.to_upgrade`` is non-empty. The CLI short-circuits
       before entering the runner when ``to_upgrade`` is empty
       (spec §Behavior step 6); the runner is *only* reachable when
       there is work to do. A direct caller that bypasses the
       short-circuit would emit aggregator events the no-op path is
       designed to avoid.
    2. ``sources`` covers every primitive in ``plan.all_installed``.
       The aggregator pass reads ``regions/`` snippets for every
       installed primitive; a missing source key crashes inside
       the aggregator with a less actionable error.

    ``state_versions`` carries ``dict(VaultState.installed_primitives)``
    so the runner can build ``PrimitiveUpgradeEvent.from_version``
    without importing ``VaultState`` (keeps the runner module
    decoupled from the journal's derived-state model).

    Calls :func:`install._warn_if_install_pipeline_uncached` as its
    first line so a caller that forgets the
    ``journal.use_journal_cache`` wrapper hits the same WARNING that
    ``install_primitives`` already emits.
    """

    if not plan.to_upgrade:
        raise ValueError(
            "upgrade_primitives requires plan.to_upgrade to be non-empty; "
            "the caller is responsible for the no-op short-circuit per "
            "docs/specs/wiki-upgrade/spec.md §Behavior step 6"
        )
    missing_sources = {p.name for p in plan.all_installed} - set(sources)
    if missing_sources:
        raise ValueError(
            f"upgrade_primitives: sources missing entries for "
            f"{sorted(missing_sources)}; every primitive in "
            "plan.all_installed must have a source directory recorded"
        )

    _warn_if_install_pipeline_uncached(journal_path)

    # Length-before snapshot: cheap line count (JSONL — one event per line).
    # We use a parsed-event count via ``read_events`` for the slice anchor
    # so a future blank-line in the journal cannot desync the index. The
    # cost is one journal walk; the cache scope absorbs it when active.
    length_before = len(read_events(journal_path))

    vault_root = journal_path.parent.parent

    for primitive in plan.to_upgrade:
        from_version = state_versions[primitive.name]
        append_event(
            journal_path,
            PrimitiveUpgradeEvent(
                timestamp=now,
                by=UPGRADE_VEHICLE,
                primitive=primitive.name,
                from_version=from_version,
                to_version=primitive.version,
            ),
        )
        render_tree(
            sources[primitive.name] / "files",
            vault_root,
            context,
            journal_path,
            by=primitive.name,
        )

    aggregate_region_contributions(
        plan.all_installed,
        sources,
        journal_path,
        by=UPGRADE_VEHICLE,
    )

    # Collect every ``.proposed`` sidecar that landed during this run by
    # walking the new-events slice. The disk re-read (vs the cached
    # ``JournalReader.events()`` slice) is intentional: slicing the
    # cache's internal list aliases mutable state. This redundant read
    # happens once per ``wiki upgrade``, after every write, and is the
    # load-bearing source of the drift surface. See
    # ``docs/specs/wiki-upgrade/spec.md`` §Contracts.upgrade_primitives.
    new_events = read_events(journal_path)[length_before:]
    proposals: list[tuple[str, str]] = []
    for event in new_events:
        if isinstance(event, PageProposalEvent):
            proposals.append((event.path, event.proposed_path))
    return proposals
