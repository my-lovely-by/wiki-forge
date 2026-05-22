"""The single sanctioned write path for files inside a user vault.

ADR-0004 names ``safe_write`` as the only function in the kit that calls
``Path.write_text`` against a vault path. It is drift-aware: every write
goes through a hash-compare against the most recent ``PageWrite`` event
for the same path in the journal. On a match (or when there is no prior
event for the path), it writes the file directly and appends a
``PageWrite`` event. On a mismatch, it writes a ``<path>.proposed``
sidecar instead, appends a ``PageProposal`` event, and adds a
``\\.proposed$`` pattern to the vault's ``.obsidianignore`` so Obsidian
does not index conflict files. The user's vault-side ``wiki-conflict``
skill then helps them merge.

``resolve_proposal`` is the documented bypass added by the ADR-0004
2026-05-15 revision: after the user has reviewed both versions via the
``wiki-conflict`` skill, it writes the confirmed merge directly,
deletes the sidecar, and emits a ``PageWrite`` (new baseline) plus a
``PageConflictResolved`` (audit). Nothing else in the kit bypasses the
drift check.

``WriteResult`` is a plain ``enum.Enum`` per ADR-0005: it doesn't cross
disk, so Pydantic would buy nothing here.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from llm_wiki_kit import journal, managed_regions
from llm_wiki_kit.errors import ManagedRegionError, WikiError
from llm_wiki_kit.journal import append_event
from llm_wiki_kit.models import (
    Event,
    ManagedRegionWriteEvent,
    PageConflictResolvedEvent,
    PageProposalEvent,
    PageWriteEvent,
)

OBSIDIAN_IGNORE_PROPOSED_PATTERN = r"\.proposed$"

# Load-bearing pin for the ``.obsidianignore`` non-journaled bypass.
# Anywhere that touches ``_ensure_obsidianignore`` cites this constant by
# name; ``grep OBSIDIANIGNORE_BYPASS_DOC`` surfaces every dependency.
# A paraphrase of the docstring no longer silently shifts the contract —
# only changing this constant's value does, and that's grep-discoverable.
# See ``docs/specs/safe-write-ordering/spec.md`` §Non-goals "Why
# .obsidianignore is not journaled" and ADR-0004 §Negative.
OBSIDIANIGNORE_BYPASS_DOC = "docs/specs/safe-write-ordering/spec.md"


def _now() -> datetime:
    """Wall-clock seam.

    Mirrors ``doctor._now``. Tests monkeypatch ``write_helper._now`` to
    pin the timestamp recompute in the adopt fast-path (spec §Behavior
    "Adopt fast-path" step 3); production callers never override it.
    """

    return datetime.now(UTC)


class WriteResult(Enum):
    """Whether ``safe_write`` wrote the target file or a proposal sidecar."""

    WRITTEN = "written"
    PROPOSAL = "proposal"


def safe_write(
    path: Path,
    content: str,
    by: str,
    journal_path: Path,
) -> WriteResult:
    """Write ``content`` to ``path``, falling through to a proposal on drift.

    ``path`` must be inside the vault rooted at ``journal_path.parent.parent``
    (the canonical layout ADR-0002 names). Paths are journaled relative to
    that root so a moved or renamed vault keeps its history intact.

    ``by`` is the primitive or operation name responsible for the write —
    ``"core"``, ``"meeting"``, ``"weekly-digest"``, etc. It surfaces in
    ``wiki journal tail`` so a user (or Claude) can attribute every line.

    Event-before-disk: the journal entry (``PageWriteEvent`` on the direct
    path, ``PageProposalEvent`` on drift) is appended and ``fsync``'d
    before the target file or sidecar is opened for writing. A crash
    between the event and the disk write leaves a recoverable gap that
    ``wiki doctor`` surfaces as ``missing`` (event durable, file absent)
    or ``page-drift`` (event durable, on-disk hash diverges). See
    ``docs/specs/safe-write-ordering/spec.md`` for the full contract.

    Three branches by ``(baseline_hash, file_present, on_disk_hash)``:

    1. **Direct write** — no history and no file (fresh path), or
       history with a matching on-disk hash (no drift), or history with
       the file absent (crash-recovery retry).
    2. **Adopt fast-path** — no history, file exists, bytes already
       match. Re-reads the file once to shrink the adopt-then-not race
       window, then journals the baseline without touching the file
       (preserves inode for Obsidian / inotify consumers).
    3. **Proposal** — everything else: a user-edited file diverging
       from the journaled baseline (classic drift), or a user file the
       kit has never seen whose bytes differ from the kit's proposed
       content (the qC6 case the spec inverted).
    """

    vault_root = _vault_root(journal_path)
    abs_path = path if path.is_absolute() else (vault_root / path)
    relative_path = _relative_to_vault(abs_path, vault_root)

    new_bytes = content.encode("utf-8")
    new_hash = _hash(new_bytes)
    on_disk_hash = _hash(abs_path.read_bytes()) if abs_path.exists() else None
    baseline_hash = _baseline_hash(journal_path, relative_path)
    # Single timestamp by design — direct-write and proposal are one
    # logical decision at call entry. Only the adopt fast-path
    # recomputes (see below), because the re-read shrinks the race
    # window and the timestamp should reflect the *adoption* decision.
    now = _now()

    no_history = baseline_hash is None
    file_present = abs_path.exists()
    bytes_match = on_disk_hash == new_hash

    direct_write = (
        (no_history and not file_present)  # fresh path
        or (not no_history and on_disk_hash == baseline_hash)  # no drift
        or (not no_history and not file_present)  # crash-recovery (spec §Edge cases sub-case 1)
    )
    if direct_write:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        append_event(
            journal_path,
            PageWriteEvent(timestamp=now, by=by, path=relative_path, hash=new_hash),
        )
        abs_path.write_bytes(new_bytes)
        return WriteResult.WRITTEN

    if no_history and file_present and bytes_match:
        # Adopt fast-path. Re-read to shrink the race: an editor that
        # wrote between the first ``read_bytes`` above and this point
        # would otherwise let us journal a hash no longer on disk.
        # If the re-read diverges, abandon the fast-path — control
        # falls through to the proposal branch below, which uses the
        # kit's ``new_hash`` (no predicate re-evaluation; the top-of-
        # function ``on_disk_hash`` snapshot is stale by construction).
        # See spec §Behavior "Adopt fast-path" step 2.
        #
        # FUTURE (Phase D, when ``wiki journal tail`` lands): consider
        # adding a ``reason: Literal["fresh","adopt","recovery"]`` field
        # to ``PageWriteEvent`` so a user staring at the tail can
        # distinguish "kit adopted my existing file" from "kit wrote a
        # fresh file" from "kit re-wrote after a crash" — today's
        # ``by`` field alone doesn't carry that provenance.
        reread_hash = _hash(abs_path.read_bytes())
        if reread_hash == new_hash:
            # Spec §Behavior "Adopt fast-path" step 3: recompute ``now``
            # so the journaled timestamp reflects the adoption decision,
            # not the call entry.
            append_event(
                journal_path,
                PageWriteEvent(
                    timestamp=_now(),
                    by=by,
                    path=relative_path,
                    hash=new_hash,
                ),
            )
            return WriteResult.WRITTEN

    proposed_abs = abs_path.with_name(abs_path.name + ".proposed")
    proposed_abs.parent.mkdir(parents=True, exist_ok=True)
    append_event(
        journal_path,
        PageProposalEvent(
            timestamp=now,
            by=by,
            path=relative_path,
            proposed_path=_relative_to_vault(proposed_abs, vault_root),
            hash=new_hash,
        ),
    )
    proposed_abs.write_bytes(new_bytes)
    _ensure_obsidianignore(vault_root)
    return WriteResult.PROPOSAL


def resolve_proposal(
    path: Path,
    content: str,
    by: str,
    journal_path: Path,
) -> None:
    """Commit a user-mediated merge — the documented ``safe_write`` bypass.

    The vault-side ``wiki-conflict`` skill calls this after helping the
    user reconcile a ``.proposed`` sidecar with their on-disk edits.
    ``content`` is the user's confirmed final version, which may be the
    sidecar's content, the user's edits, or a third merged version —
    ``resolve_proposal`` doesn't care which.

    Writes ``content`` directly to ``path`` (bypassing the drift check
    that ``safe_write`` enforces, per ADR-0004 §Mechanics step 6),
    deletes ``<path>.proposed`` if present, and appends two journal
    events: a ``PageWrite`` with the merged hash (the new baseline,
    so subsequent ``safe_write`` calls see no drift) and a
    ``PageConflictResolved`` for audit.

    Event-before-disk: every journal event this function emits — the
    ``PageWriteEvent``, the ``PageConflictResolvedEvent``, and any
    re-baseline ``ManagedRegionWriteEvent``s for known regions — is
    appended and ``fsync``'d *before* the target file is rewritten and
    the sidecar is deleted. A crash between the events and the disk
    write surfaces via ``wiki doctor`` as ``page-drift`` or ``missing``;
    re-running ``wiki-conflict`` reads both ``path`` and ``path.proposed``
    (the sidecar is still on disk) and produces an idempotent retry.
    See ``docs/specs/safe-write-ordering/spec.md`` §Edge cases sub-case 3.
    """

    vault_root = _vault_root(journal_path)
    abs_path = path if path.is_absolute() else (vault_root / path)
    relative_path = _relative_to_vault(abs_path, vault_root)

    new_bytes = content.encode("utf-8")
    new_hash = _hash(new_bytes)
    now = _now()

    append_event(
        journal_path,
        PageWriteEvent(timestamp=now, by=by, path=relative_path, hash=new_hash),
    )
    append_event(
        journal_path,
        PageConflictResolvedEvent(timestamp=now, by=by, path=relative_path, hash=new_hash),
    )

    # Retro-review #F-B1: if this file has managed-region history, the
    # ``PageWriteEvent`` alone doesn't re-baseline ``safe_write_region``'s
    # region-scoped lookup. Emit a fresh ``ManagedRegionWriteEvent`` per
    # known region so the next ``safe_write_region`` writes in place
    # instead of re-proposing forever (ADR-0004 §Mechanics step 6 for
    # managed regions). The region events also land *before* the disk
    # write per the event-before-disk invariant.
    known_regions = _known_regions_for_file(journal_path, relative_path)
    if known_regions:
        try:
            resolved_regions = managed_regions.parse(content)
        except ManagedRegionError:
            # The user's resolution destroyed markers. Region writes are
            # now broken for this file; surfacing happens via
            # ``safe_write_region`` raising on the next attempt. Don't
            # silently invent ``ManagedRegionWriteEvent``s for missing
            # regions. The page-level events above are still durable;
            # the disk write below still has to land so the user sees
            # their merged content.
            resolved_regions = None
        if resolved_regions is not None:
            for region in known_regions:
                body = resolved_regions.get(region)
                if body is None:
                    continue
                # Canonicalize before hashing — keeps the resolve-path
                # baseline hash in lockstep with what
                # ``safe_write_region`` recomputes on the next aggregator
                # pass. Without this, a re-aggregate after resolve would
                # see spurious drift the same way Task 19's
                # multi-contributor aggregation would have.
                append_event(
                    journal_path,
                    ManagedRegionWriteEvent(
                        timestamp=now,
                        by=by,
                        file=relative_path,
                        region=region,
                        content_hash=_hash(managed_regions.canonical_region_body(body)),
                    ),
                )

    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(new_bytes)

    sidecar = abs_path.with_name(abs_path.name + ".proposed")
    if sidecar.exists():
        sidecar.unlink()


def _read_events_cached(journal_path: Path) -> list[Event]:
    """Read events, consulting the active ``JournalReader`` if one is installed.

    Falls through to ``read_events(journal_path)`` when no cache scope
    is active (per `journal.use_journal_cache`) or when the active
    reader tracks a different journal. The fall-through path keeps
    today's "always fresh read" semantics for callers outside the
    scope — tests calling ``safe_write`` directly retain identical
    behavior. See ``docs/specs/journal-reader-cache/spec.md``.
    """

    reader = journal._CURRENT_READER.get()
    if reader is not None and reader.journal_path == journal_path.resolve():
        return reader.events()
    # Route through ``journal.read_events`` (not the import-time binding)
    # so a test that monkeypatches ``journal.read_events`` still sees
    # the call here. The two paths must agree on observability.
    return journal.read_events(journal_path)


def _known_regions_for_file(journal_path: Path, relative_file: str) -> list[str]:
    """Return the set of region ids ever written for ``relative_file``.

    Preserves first-seen order so the emitted ``ManagedRegionWriteEvent``
    sequence is stable across runs.
    """

    seen: list[str] = []
    for event in _read_events_cached(journal_path):
        if isinstance(event, ManagedRegionWriteEvent) and event.file == relative_file:
            if event.region not in seen:
                seen.append(event.region)
    return seen


def safe_write_region(
    file_path: Path,
    region_id: str,
    new_content: str,
    by: str,
    journal_path: Path,
) -> WriteResult:
    """Write ``new_content`` into a kit-owned managed region of a shared file.

    ADR-0003 names this as the write path for shared infra files like
    ``AGENTS.md``, ``frontmatter.schema.yaml``, ``.gitignore``, and
    ``.claude/research-providers.yaml`` — files multiple primitives
    contribute to via `<!-- BEGIN MANAGED: id -->` (or `# BEGIN MANAGED: id`)
    delimiters.

    Drift detection is region-scoped, not file-scoped. The kit looks up
    the most recent ``managed_region.write`` event for ``(file, region)``
    and compares its ``content_hash`` to the hash of the region's current
    on-disk body. On match (or with no prior event), the region is
    rewritten in place, the rest of the file is preserved verbatim
    (including user edits to unmanaged content — which are invisible to
    the kit by design, per ADR-0003 §Decision), and a
    ``ManagedRegionWriteEvent`` is appended.

    Event-before-disk: the ``ManagedRegionWriteEvent`` (happy path) or
    ``PageProposalEvent`` (drift path) is appended and ``fsync``'d
    before the target file is rewritten — per
    ``docs/specs/safe-write-ordering/spec.md``. A crash between the
    event and the disk write surfaces via ``wiki doctor`` as
    ``managed-region-drift``; recovery routes through the proposal flow
    (see spec §Edge cases sub-case 2). The no-prior-event-direct-write
    case is preserved by design — the install pipeline's
    ``aggregate_region_contributions`` depends on it (spec §Non-goals
    "Why qC6 is page-scoped").

    On intra-region drift, the kit doesn't touch ``file_path``. Instead
    it writes ``<file_path>.proposed`` containing the file as it would
    look after applying the region update (so the unmanaged user edits
    flow through, but the user can inspect just the region delta), emits
    a ``PageProposalEvent`` for the shared file, and updates
    ``.obsidianignore``. The user resolves via the vault-side
    ``wiki-conflict`` skill and the same
    :func:`resolve_proposal` bypass as page proposals.

    Raises :class:`FileNotFoundError` if ``file_path`` does not exist —
    shared files are seeded by ``wiki init`` and the kit relies on their
    presence to find the region markers. Raises
    :class:`llm_wiki_kit.errors.ManagedRegionError` if ``region_id`` is
    not present in the file.
    """

    vault_root = _vault_root(journal_path)
    abs_path = file_path if file_path.is_absolute() else (vault_root / file_path)
    relative_path = _relative_to_vault(abs_path, vault_root)

    on_disk_text = abs_path.read_text(encoding="utf-8")
    current_regions = managed_regions.parse(on_disk_text)
    if region_id not in current_regions:
        raise ManagedRegionError(f"file '{relative_path}' has no managed region '{region_id}'")

    # ``_normalise_snippet`` writes each contributor's snippet with a
    # trailing newline; ``managed_regions.parse`` strips it when
    # reading back (the body is "between markers", terminator excluded).
    # The two forms differ by exactly one byte for any non-empty body,
    # which would cause a spurious drift event the first time a second
    # contributor lands in the same region. Canonicalize both sides
    # before hashing — append a single trailing newline if non-empty,
    # matching the form ``_normalise_snippet`` writes. Pre-existing
    # ``ManagedRegionWriteEvent`` baseline hashes were computed against
    # the with-trailing-newline form (the aggregator's ``new_content``
    # is already in that form), so this canonicalization keeps the
    # baseline comparison valid for Task 18 vaults.
    current_region_hash = _hash(managed_regions.canonical_region_body(current_regions[region_id]))
    baseline_hash = _managed_region_baseline_hash(journal_path, relative_path, region_id)
    new_region_hash = _hash(managed_regions.canonical_region_body(new_content))
    rewritten = managed_regions.update(on_disk_text, region_id, new_content)
    now = _now()

    if baseline_hash is None or current_region_hash == baseline_hash:
        append_event(
            journal_path,
            ManagedRegionWriteEvent(
                timestamp=now,
                by=by,
                file=relative_path,
                region=region_id,
                content_hash=new_region_hash,
            ),
        )
        abs_path.write_text(rewritten, encoding="utf-8")
        return WriteResult.WRITTEN

    proposed_abs = abs_path.with_name(abs_path.name + ".proposed")
    proposed_abs.parent.mkdir(parents=True, exist_ok=True)
    append_event(
        journal_path,
        PageProposalEvent(
            timestamp=now,
            by=by,
            path=relative_path,
            proposed_path=_relative_to_vault(proposed_abs, vault_root),
            hash=_hash(rewritten.encode("utf-8")),
        ),
    )
    proposed_abs.write_text(rewritten, encoding="utf-8")
    _ensure_obsidianignore(vault_root)
    return WriteResult.PROPOSAL


def _managed_region_baseline_hash(
    journal_path: Path, relative_file: str, region_id: str
) -> str | None:
    for event in reversed(_read_events_cached(journal_path)):
        if (
            isinstance(event, ManagedRegionWriteEvent)
            and event.file == relative_file
            and event.region == region_id
        ):
            return event.content_hash
    return None


def _vault_root(journal_path: Path) -> Path:
    # Canonical layout is `<vault_root>/.wiki.journal/journal.jsonl`.
    return journal_path.parent.parent


def _relative_to_vault(abs_path: Path, vault_root: Path) -> str:
    """Return ``abs_path`` as a POSIX path relative to ``vault_root``.

    Resolves both sides so ``..`` segments and symlinks point at the
    same canonical file (retro-review qC9). Drift detection keys off
    the journaled vault-relative path; a symlinked-in route would
    otherwise journal under one key and check under another, silently
    splitting baselines.

    Rejects symlink escape: a path that lives inside ``vault_root``
    lexically but resolves to a target outside it raises
    :class:`WikiError`. The journal must not record a path that
    escapes the vault — the next ``safe_write`` against the same
    lexical path would diverge from the resolved target.

    Bare ``ValueError`` from :meth:`Path.relative_to` is wrapped as
    :class:`WikiError` (retro-review qB3) so the CLI boundary renders
    one line instead of a Python traceback, per ADR-0005.
    """

    resolved_path = abs_path.resolve()
    resolved_root = vault_root.resolve()
    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        # Include both lexical and resolved forms when they differ —
        # the resolved path is the actionable detail in the symlink-
        # escape case ("but `linked/leaked.md` is under `vault/`!").
        if resolved_path != abs_path or resolved_root != vault_root:
            detail = (
                f"path '{abs_path}' resolves to '{resolved_path}', "
                f"which is not inside the vault rooted at '{vault_root}' "
                f"(resolved: '{resolved_root}')"
            )
        else:
            detail = f"path '{abs_path}' is not inside the vault rooted at '{vault_root}'"
        raise WikiError(detail) from exc


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _baseline_hash(journal_path: Path, relative_path: str) -> str | None:
    """Return the hash of the most recent ``PageWrite`` event for the path.

    ``PageConflictResolved`` is audit-only here; ``resolve_proposal``
    re-establishes the baseline by emitting its own ``PageWrite``
    alongside the audit event (ADR-0004 §Mechanics step 6).
    """

    for event in reversed(_read_events_cached(journal_path)):
        if isinstance(event, PageWriteEvent) and event.path == relative_path:
            return event.hash
    return None


def _ensure_obsidianignore(vault_root: Path) -> None:
    """Append the ``\\.proposed$`` pattern to ``.obsidianignore`` if absent.

    **Documented non-journaled bypass** — the only one in the kit
    alongside ``resolve_proposal`` (which IS journaled but bypasses the
    drift check). See ``OBSIDIANIGNORE_BYPASS_DOC`` §Non-goals "Why
    ``.obsidianignore`` is not journaled" and ADR-0004 §Negative for
    the rationale. Three reasons, cumulatively:

    1. Every user edit to ``.obsidianignore`` (adding their own
       scratch-dir ignore) would register as ``page-drift`` in
       ``wiki doctor`` — wrong UX for a file users are expected to edit.
    2. Routing through ``safe_write`` proper would produce
       ``.obsidianignore.proposed`` on user drift, which Obsidian then
       indexes (because ``.obsidianignore`` itself no longer carries
       the proposed-pattern) — a self-defeating bootstrap.
    3. Special-casing ``check_page_drift`` to skip ``.obsidianignore``
       re-introduces the bypass the spec is supposed to remove.

    Adding a third bypass requires amending
    ``OBSIDIANIGNORE_BYPASS_DOC`` first; the journaling story is
    load-bearing.
    """

    ignore = vault_root / ".obsidianignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if OBSIDIAN_IGNORE_PROPOSED_PATTERN in existing.splitlines():
        return
    if existing and not existing.endswith("\n"):
        existing += "\n"
    ignore.write_text(existing + OBSIDIAN_IGNORE_PROPOSED_PATTERN + "\n", encoding="utf-8")
