"""Vault-state validator behind ``wiki doctor``.

Replays the journal, compares to disk, and reports eight kinds of issue:

* ``page-drift`` — a journaled ``page.write`` whose on-disk hash no
  longer matches, with no outstanding ``page.proposal`` to explain it.
* ``managed-region-drift`` — a journaled ``managed_region.write``
  whose on-disk region body no longer matches.
* ``pending-proposal`` — a ``.proposed`` sidecar awaiting resolution.
* ``orphan`` — a file under a kit-owned path with no journal event.
* ``missing`` — a journaled ``page.write`` whose file is gone.
* ``primitive-missing`` — a journal-recorded primitive that the kit's
  catalog no longer carries (e.g. after a kit downgrade).
* ``stale-lock`` — a ``lock.acquired`` event older than
  ``WIKI_LOCK_STALE_HOURS`` (default 24) with no matching release
  (``journal-locking`` spec §Doctor).
* ``journal-corrupt`` — a malformed journal line; surfaced once with
  the offending line number, then the remaining checks run against
  the valid-events prefix instead of crashing the whole pass
  (``journal-locking`` spec §Recovery).

Doctor only reports. Auto-fix lives in a future ``wiki doctor --fix``
task. The CLI surface maps a non-empty report to exit code 1; ``2`` is
reserved for internal errors raised through :class:`WikiError`.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from llm_wiki_kit import managed_regions
from llm_wiki_kit.errors import ManagedRegionError
from llm_wiki_kit.journal import read_events_lenient, replay_state
from llm_wiki_kit.models import Event, ManagedRegionWriteEvent, PageWriteEvent, VaultState
from llm_wiki_kit.primitives import discover_primitives, load_primitive

# Issue kinds (also serve as the line prefix in the CLI output).
PAGE_DRIFT = "page-drift"
MANAGED_REGION_DRIFT = "managed-region-drift"
PENDING_PROPOSAL = "pending-proposal"
ORPHAN = "orphan"
MISSING = "missing"
PRIMITIVE_MISSING = "primitive-missing"
STALE_LOCK = "stale-lock"
JOURNAL_CORRUPT = "journal-corrupt"

# Default stale-lock threshold per ``journal-locking`` spec §Invariants.
# Doctor reads ``WIKI_LOCK_STALE_HOURS`` on each run; this default applies
# when the env var is absent, blank, or unparseable.
_DEFAULT_STALE_HOURS = 24

# Kit-owned vault paths are derived from ``state.page_writes`` rather
# than enumerated as a static tuple (retro-review qC10 + C6). The kit
# claims territory by writing it; an empty vault claims nothing, and
# the orphan check stays silent until ``wiki init`` (or a later
# ``safe_write``) has journaled at least one path. ADR-0004: the kit
# never touches user territory.


@dataclass(frozen=True)
class Issue:
    """One finding from ``run_doctor``.

    Not a Pydantic model because :class:`Issue` never crosses disk —
    ADR-0005 reserves Pydantic for the disk-bound schemas. ``detail``
    is optional context (e.g. "region missing"); empty string by default
    so the rendered line stays compact.

    Most issue kinds put a vault-relative filesystem path in ``path``
    (``AGENTS.md``, ``skills/rogue/SKILL.md``). The one shim today is
    ``journal-corrupt``, where ``path`` carries the 1-based line number
    of the offending journal row as a string — there is no vault file
    that "owns" a torn JSONL line, and the plan
    (``docs/specs/journal-locking/plan.md`` §Steps step 6) makes this
    the explicit contract. A future ``Issue`` refactor that splits
    ``path`` into ``path | line`` should update both call sites at
    once — including ``run_doctor``'s ``(kind, path, detail)`` sort
    key, which would order ``journal-corrupt: 10`` before
    ``journal-corrupt: 2`` (lexicographic on the line-number string)
    the moment ``read_events_lenient`` learns to surface more than one
    ``Corruption`` per pass. Today only one corruption is reported, so
    the bug is latent.
    """

    kind: str
    path: str
    detail: str = ""


def format_issue(issue: Issue) -> str:
    """Render an :class:`Issue` as one CLI line, prefixed with its kind."""

    if issue.detail:
        return f"{issue.kind}: {issue.path} ({issue.detail})"
    return f"{issue.kind}: {issue.path}"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_page_drift(state: VaultState, vault_root: Path, events: list[Event]) -> list[Issue]:
    """Pages whose on-disk hash diverges from the latest ``page.write``.

    A path with an outstanding ``page.proposal`` is reported as
    ``pending-proposal``, not ``page-drift`` — the user already knows
    the kit wanted to write something there.

    A path whose latest event for that file is a
    ``ManagedRegionWriteEvent`` (post-Task-19) is also skipped — the
    install pipeline's aggregator rewrites managed-region files in
    place via :func:`write_helper.safe_write_region` *after* the seed
    primitive's ``safe_write`` lands the empty-region seed bytes, so
    the original ``PageWriteEvent`` baseline goes stale by design.
    Region-level drift is what :func:`check_managed_region_drift`
    exists to catch; double-reporting at the page level would surface
    every managed-region file as drifted after every install.
    """

    files_with_later_region_writes = _files_with_managed_region_write_after_page_write(events)

    issues: list[Issue] = []
    for relative, event in state.page_writes.items():
        if relative in state.pending_proposals:
            continue
        if relative in files_with_later_region_writes:
            continue
        abs_path = vault_root / relative
        if not abs_path.exists():
            continue  # surfaces via check_missing
        if _hash(abs_path.read_bytes()) != event.hash:
            issues.append(Issue(PAGE_DRIFT, relative))
    return issues


def _files_with_managed_region_write_after_page_write(events: list[Event]) -> set[str]:
    """Return paths whose latest write event is a region write, not a page write.

    Used by :func:`check_page_drift` to skip files the install
    pipeline has rewritten via the managed-region path after the
    initial seed. The order matters — a future page write would
    re-baseline and bring the file back under page-level drift
    detection.
    """

    latest_write_kind: dict[str, str] = {}
    for event in events:
        if isinstance(event, PageWriteEvent):
            latest_write_kind[event.path] = "page"
        elif isinstance(event, ManagedRegionWriteEvent):
            latest_write_kind[event.file] = "region"
    return {path for path, kind in latest_write_kind.items() if kind == "region"}


def check_managed_region_drift(
    events: list[Event], vault_root: Path, state: VaultState
) -> list[Issue]:
    """Managed regions whose on-disk body diverges from the latest write.

    Walks ``events`` (not the replayed state) because
    ``managed_region.write`` events aren't projected into
    :class:`VaultState`. Per-region "latest" is the last event for
    ``(file, region)`` in journal order.

    A file with an outstanding ``page.proposal`` is skipped — the
    proposal already explains every region inside it, and reporting
    both ``pending-proposal`` and ``managed-region-drift`` for the
    same file is double-counting (retro-review #B6, pairs with
    ``write_helper.resolve_proposal``'s region re-baseline fix #F-B1).
    """

    latest: dict[tuple[str, str], ManagedRegionWriteEvent] = {}
    for event in events:
        if isinstance(event, ManagedRegionWriteEvent):
            latest[(event.file, event.region)] = event

    file_cache: dict[str, dict[str, str] | None] = {}
    issues: list[Issue] = []
    for (file_path, region), event in latest.items():
        if file_path in state.pending_proposals:
            continue
        abs_file = vault_root / file_path
        if not abs_file.exists():
            continue  # surfaces via check_missing
        if file_path not in file_cache:
            try:
                file_cache[file_path] = managed_regions.parse(abs_file.read_text(encoding="utf-8"))
            except ManagedRegionError:
                file_cache[file_path] = None
        parsed = file_cache[file_path]
        target = f"{file_path}:{region}"
        if parsed is None:
            issues.append(Issue(MANAGED_REGION_DRIFT, target, "markers malformed"))
            continue
        body = parsed.get(region)
        if body is None:
            issues.append(Issue(MANAGED_REGION_DRIFT, target, "region missing"))
            continue
        if _hash(managed_regions.canonical_region_body(body)) != event.content_hash:
            issues.append(Issue(MANAGED_REGION_DRIFT, target))
    return issues


def check_pending_proposals(state: VaultState) -> list[Issue]:
    """One issue per unresolved ``.proposed`` sidecar.

    Surfaces the sidecar's vault-relative path so the user can hand it
    to the vault-side ``wiki-conflict`` skill.
    """

    return [
        Issue(PENDING_PROPOSAL, event.proposed_path) for event in state.pending_proposals.values()
    ]


def check_missing(state: VaultState, vault_root: Path) -> list[Issue]:
    """Journal-recorded pages whose file is no longer on disk."""

    return [
        Issue(MISSING, relative)
        for relative in state.page_writes
        if not (vault_root / relative).exists()
    ]


def check_orphans(state: VaultState, vault_root: Path) -> list[Issue]:
    """Files under kit-owned paths with no corresponding journal event.

    Kit-owned territory is derived from ``state.page_writes``
    (retro-review qC10 + C6): every path the kit has ever recorded a
    ``page.write`` for contributes. The top-level directory of every
    such path is treated as kit territory; journaled top-level
    filenames are watched directly. Skips ``.proposed`` sidecars
    (those surface as pending-proposal) and any path outside the
    derived territory (user-owned by default).

    Doctrine: only ``page.write`` events extend territory.
    ``ManagedRegionWriteEvent``s always reference a file that was
    seeded earlier via ``safe_write`` (which emits a ``PageWriteEvent``),
    so the shared-file case is already covered by the page-writes
    fold-in. ``SourceIngestEvent.produced_pages`` is a forward-looking
    record; the actual page writes the vault-side ingester performs
    flow through ``safe_write`` and emit their own ``PageWriteEvent``s.
    Folding either of those event types in would double-count
    territory without expanding what the kit actually owns.

    Transition note: an empty journal claims no territory. The orphan
    check fires only after the kit has journaled at least one write —
    which the install pipeline does on every ``wiki init``. A user
    adding files under a top-level dir before the kit has touched
    that dir gets a silent pass; once the kit journals anything under
    that dir, those files surface as orphans. The same was true under
    the previous static tuples for ``skills/``, ``_templates/``, and
    ``wiki/`` (categorically kit-owned), so the user-visible UX change
    is bounded to the pre-init window.
    """

    journaled = set(state.page_writes)
    proposal_sidecars = {e.proposed_path for e in state.pending_proposals.values()}

    owned_files: set[str] = set()
    owned_dirs: set[str] = set()
    for journaled_path in journaled:
        parts = Path(journaled_path).parts
        if not parts:
            continue
        if len(parts) == 1:
            owned_files.add(parts[0])
        else:
            owned_dirs.add(parts[0])

    candidates: list[str] = []
    for name in owned_files:
        if (vault_root / name).is_file():
            candidates.append(name)
    for dir_name in owned_dirs:
        directory = vault_root / dir_name
        if not directory.is_dir():
            continue
        for entry in directory.rglob("*"):
            if entry.is_file():
                candidates.append(entry.relative_to(vault_root).as_posix())

    issues: list[Issue] = []
    for relative in candidates:
        if relative.endswith(".proposed"):
            continue
        if relative in proposal_sidecars:
            continue
        if relative not in journaled:
            issues.append(Issue(ORPHAN, relative))
    return issues


def _now() -> datetime:
    """Wall-clock seam.

    Lives in this module so tests can monkeypatch ``doctor._now`` to pin
    "now" against a fixed datetime — sleeping in tests would be both
    slow and flaky. Production callers never override it.
    """

    return datetime.now(UTC)


def _stale_threshold_hours() -> int:
    """Read ``WIKI_LOCK_STALE_HOURS`` or fall back to the 24-hour default.

    Blank, unparseable, zero, or negative values fall back rather than
    raising — ``wiki doctor`` is the diagnostic command of last resort,
    so it must not refuse to run because an env var was mistyped.
    Malformed values emit one warning to stderr so the user knows their
    config was ignored.
    """

    raw = os.environ.get("WIKI_LOCK_STALE_HOURS")
    if raw is None or raw == "":
        return _DEFAULT_STALE_HOURS
    try:
        hours = int(raw)
    except ValueError:
        print(
            f"wiki doctor: WIKI_LOCK_STALE_HOURS={raw!r} is not an integer; "
            f"using default {_DEFAULT_STALE_HOURS}",
            file=sys.stderr,
        )
        return _DEFAULT_STALE_HOURS
    if hours <= 0:
        print(
            f"wiki doctor: WIKI_LOCK_STALE_HOURS={raw!r} is not positive; "
            f"using default {_DEFAULT_STALE_HOURS}",
            file=sys.stderr,
        )
        return _DEFAULT_STALE_HOURS
    return hours


def check_stale_lock(state: VaultState, threshold_hours: int) -> list[Issue]:
    """Surface a ``stale-lock`` issue if the latest acquire has no release.

    Reads ``state.held_lock`` rather than re-deriving from events: the
    "last-acquire-wins, any-release-clears" semantics already live in
    ``replay_state`` (``journal.py``), and a parallel walk here would
    be a second source of truth waiting to drift. Pattern-matches the
    rest of the doctor checks (``check_page_drift``,
    ``check_pending_proposals``, ``check_orphans``,
    ``check_missing``, ``check_primitive_missing``) which all consume
    the replayed ``VaultState`` directly.

    Precondition: ``threshold_hours`` must be a positive integer. The
    only in-kit caller routes through ``_stale_threshold_hours()`` which
    clamps; a direct caller passing zero or a negative value gets
    "everything is stale" semantics by arithmetic, which is the caller's
    bug to fix.

    Naive (tz-less) ``HeldLock.acquired_at`` values are coerced to UTC
    before the age subtraction. The kit's own writers always emit
    tz-aware timestamps, but a hand-edited or externally produced
    journal line may carry a naive one — and ``wiki doctor`` must not
    crash on a journal it was specifically built to inspect.
    """

    holder = state.held_lock
    if holder is None:
        return []

    acquired_at = holder.acquired_at
    if acquired_at.tzinfo is None:
        acquired_at = acquired_at.replace(tzinfo=UTC)
    age = _now() - acquired_at
    if age.total_seconds() < threshold_hours * 3600:
        return []

    return [Issue(STALE_LOCK, holder.by, f"acquired {acquired_at.isoformat()}")]


def check_primitive_missing(state: VaultState, kit_root: Path) -> list[Issue]:
    """Installed primitives the current kit catalog no longer ships.

    Useful when a user downgrades the kit underneath a vault — the
    journal still references primitives the new install can't render or
    upgrade. Names are surfaced verbatim; the user (or the kit's future
    ``wiki upgrade`` step) decides what to do.
    """

    catalog_names: set[str] = set()
    core_dir = kit_root / "core"
    if (core_dir / "primitive.yaml").is_file():
        catalog_names.add(load_primitive(core_dir).name)
    for primitive in discover_primitives(kit_root / "templates"):
        catalog_names.add(primitive.name)

    return [
        Issue(PRIMITIVE_MISSING, name)
        for name in state.installed_primitives
        if name not in catalog_names
    ]


def run_doctor(vault_root: Path, kit_root: Path) -> list[Issue]:
    """Replay the journal and return every issue, sorted by ``(kind, path)``.

    Uses ``read_events_lenient`` so a malformed line surfaces as a
    ``journal-corrupt`` issue while the remaining checks run against the
    valid-events prefix. Strict ``read_events`` would have raised and
    hidden every other problem in the vault — the opposite of what
    ``wiki doctor`` is for.
    """

    journal_path = vault_root / ".wiki.journal" / "journal.jsonl"
    events, corruption = read_events_lenient(journal_path)
    state = replay_state(events)

    issues: list[Issue] = []
    if corruption is not None:
        issues.append(Issue(JOURNAL_CORRUPT, str(corruption.line), corruption.reason))
    issues.extend(check_page_drift(state, vault_root, events))
    issues.extend(check_managed_region_drift(events, vault_root, state))
    issues.extend(check_pending_proposals(state))
    issues.extend(check_orphans(state, vault_root))
    issues.extend(check_missing(state, vault_root))
    issues.extend(check_primitive_missing(state, kit_root))
    issues.extend(check_stale_lock(state, _stale_threshold_hours()))
    issues.sort(key=lambda issue: (issue.kind, issue.path, issue.detail))
    return issues
