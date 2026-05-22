"""Pydantic v2 models for everything in the kit that crosses disk.

ADR-0005 names the rule: every type read from or written to disk lives here,
in-memory plumbing stays in plain dataclasses or function signatures. The
journal's ``Event`` is a Pydantic discriminated union with one class per
event type so the JSONL parser can dispatch on a single literal field.

The event taxonomy lines up with the namespaces called out in
``docs/architecture/overview.md`` (``vault.*``, ``primitive.*``,
``managed_region.*``, ``source.*``, ``page.*``, ``operation.*``,
``research.*``, ``lint.*``, ``config.*``). New event types are added by
appending one class and one entry to ``Event``; defaults are required on
new fields so older journal lines keep replaying (ADR-0002).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

# Load-bearing for ``git_init.initialize_git``'s commit-message argv — the
# recipe name is interpolated into ``"Initialize wiki vault from <recipe>
# recipe"`` and passed as a single argv element to ``git commit -m`` with
# ``shell=False``. The ``[a-z][a-z0-9-]*`` pattern keeps the name shell-safe
# without quoting concerns. Any future relaxation must audit
# ``llm_wiki_kit/git_init.py`` per ``docs/specs/wiki-init-git/spec.md``
# §Behavior step 6.
NAME_PATTERN = r"^[a-z][a-z0-9-]*$"
SEMVER_PATTERN = r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$"


class _StrictModel(BaseModel):
    """Base for every disk-bound model.

    ``extra="forbid"`` catches typos in hand-edited YAML before they become
    silent no-ops; that's why the migration plan picks Pydantic in the first
    place (ADR-0005).
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


class PrimitiveKind(StrEnum):
    ONTOLOGY = "ontology"
    CONTENT_TYPE = "content-type"
    OPERATION = "operation"
    INFRASTRUCTURE = "infrastructure"


class Contribution(_StrictModel):
    """A primitive's write into a managed region of a shared file (ADR-0003)."""

    file: str
    region: str


class PrimitiveRouting(_StrictModel):
    """Auto-routing rules for ``wiki ingest`` (Task 16).

    Only meaningful on content-type primitives. Every list is optional; an
    empty ``PrimitiveRouting`` is the same as having no ``routing:`` block at
    all — the primitive is only reachable via ``wiki ingest --as <name>``.

    Pattern semantics: ``filename_patterns``, ``url_domains``, and
    ``url_path_patterns`` are matched with ``fnmatch`` (case-insensitive).
    ``file_extensions`` are compared case-insensitively against the
    suffix including the leading dot (``".pdf"``, not ``"pdf"``).
    """

    file_extensions: list[str] = Field(default_factory=list)
    filename_patterns: list[str] = Field(default_factory=list)
    url_domains: list[str] = Field(default_factory=list)
    url_path_patterns: list[str] = Field(default_factory=list)


class Primitive(_StrictModel):
    """The schema of a ``primitive.yaml`` manifest."""

    name: str = Field(pattern=NAME_PATTERN)
    kind: PrimitiveKind
    version: str = Field(pattern=SEMVER_PATTERN)
    description: str
    requires: list[str] = Field(default_factory=list)
    contributes_to: list[Contribution] = Field(default_factory=list)
    routing: PrimitiveRouting | None = None
    config: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _routing_only_on_content_types(self) -> Self:
        if self.routing is not None and self.kind is not PrimitiveKind.CONTENT_TYPE:
            raise ValueError(
                f"primitive '{self.name}' declares routing but kind is "
                f"'{self.kind.value}'; routing is only valid on content-type primitives"
            )
        return self


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


class Recipe(_StrictModel):
    """The schema of a ``recipes/<name>.yaml`` file."""

    name: str = Field(pattern=NAME_PATTERN)
    version: str = Field(pattern=SEMVER_PATTERN)
    description: str
    primitives: list[str]
    variables: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Operation contract
# ---------------------------------------------------------------------------


class OperationInputSpec(_StrictModel):
    """Per-input declaration inside an operation's ``contract.yaml``.

    Captures the on-disk shape used across the shipped catalog:
    every input has a ``type`` tag; the rest is optional. Field set
    pinned in ``docs/specs/task-17-wiki-run/spec.md`` §Contracts.

    ``type`` values seen in production: ``string``, ``iso_week``,
    ``list``, ``integer``, ``int`` (alias for integer), ``boolean``,
    and ``page`` (used by ``trip-prep``). Unknown values are
    accepted at the schema level; coercion in
    :mod:`llm_wiki_kit.run` decides what to do with them.

    ``default: None`` (Python ``None``, either from an absent
    ``default:`` key or an explicit ``default: null``) means "no
    default applied" — see spec §Behavior step 8.
    """

    type: str
    description: str | None = None
    default: object | None = None
    optional: bool = False
    items: str | None = None


class OperationContract(_StrictModel):
    """The schema of an operation primitive's ``contract.yaml``.

    ``outcomes`` declares human-readable verbs that map back to this
    operation (per ``docs/specs/outcome-named-entry-points/spec.md``
    §Inputs §1). Each verb is validated by
    :func:`llm_wiki_kit.primitives.is_well_formed_outcome_verb` at
    catalog-load time and surfaces via three derived surfaces (CLI
    alias, Claude Code slash stub, SKILL trigger fragment). An
    omitted or empty ``outcomes:`` field is the v2.0.0 baseline —
    the operation is reachable only through ``wiki run <name>``.
    """

    name: str = Field(pattern=NAME_PATTERN)
    description: str
    period: str | None = None
    skill: str | None = None
    outcomes: list[str] = Field(default_factory=list)
    inputs: dict[str, OperationInputSpec] = Field(default_factory=dict)
    outputs: dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Research providers config (ADR-0003 managed region body)
# ---------------------------------------------------------------------------


class ProviderConfig(_StrictModel):
    """One provider's block inside ``research-providers.yaml``.

    Each ``infrastructure:research-*`` primitive contributes one of
    these into the ``providers`` managed region of the shared config.
    ``api_key_env`` is schema-optional so future providers (e.g. Task
    19's Semantic Scholar, which works without a key) can omit it;
    per-provider code (e.g. Perplexity's ``dispatch``) enforces its
    own requirement. See ``docs/specs/task-18-research-perplexity/spec.md``
    §"ProviderConfig schema".
    """

    api_key_env: str | None = None
    endpoint: str | None = None
    model: str | None = None
    cost_signal: Literal["free", "low", "medium", "high"] | None = None
    strengths: list[str] = Field(default_factory=list)


class ResearchProvidersConfig(RootModel[dict[str, ProviderConfig]]):
    """The shape of the ``providers`` managed region in ``research-providers.yaml``.

    A flat mapping ``<provider_slug>: ProviderConfig`` — no wrapping
    ``providers:`` key. The dispatcher reads only the managed-region
    body (via ``managed_regions.parse``) and YAML-loads that slice;
    text outside the markers is preserved on disk (ADR-0003) but
    ignored here.

    The root-model shape means any string key becomes a candidate
    provider slug. Unknown *inner* keys on a ``ProviderConfig`` block
    (e.g. ``endpiont:`` typo) are rejected by ``_StrictModel``'s
    ``extra="forbid"``; an unknown slug whose implementation isn't
    registered is caught separately by the dispatcher's
    "no implementation" path.
    """

    def slugs(self) -> list[str]:
        """Return installed provider slugs in sorted order."""

        return sorted(self.root.keys())


# ---------------------------------------------------------------------------
# Journal events
# ---------------------------------------------------------------------------


class _EventBase(_StrictModel):
    """Fields every journal event carries."""

    timestamp: datetime
    by: str


class VaultInitEvent(_EventBase):
    type: Literal["vault.init"] = "vault.init"
    vault_name: str
    recipe: str
    schema_version: int = 1


class VaultGitInitializedEvent(_EventBase):
    """Recorded when ``wiki init`` initializes a git repo for the vault.

    The event is appended between ``git init`` and ``git add -A`` /
    ``git commit`` so its journal line is captured by the initial
    commit's tree, leaving ``git status --porcelain`` empty after a
    successful ``wiki init``. Carries no ``commit_sha`` or ``branch``
    — see ``docs/specs/wiki-init-git/spec.md`` §Outputs for the
    rationale (recording the SHA would require either two commits or
    a journal-ahead-of-HEAD state).
    """

    type: Literal["vault.git_initialized"] = "vault.git_initialized"
    schema_version: int = 1


class PrimitiveInstallEvent(_EventBase):
    type: Literal["primitive.install"] = "primitive.install"
    primitive: str
    version: str


class PrimitiveRemoveEvent(_EventBase):
    type: Literal["primitive.remove"] = "primitive.remove"
    primitive: str


class PrimitiveUpgradeEvent(_EventBase):
    type: Literal["primitive.upgrade"] = "primitive.upgrade"
    primitive: str
    from_version: str
    to_version: str


class ManagedRegionWriteEvent(_EventBase):
    type: Literal["managed_region.write"] = "managed_region.write"
    file: str
    region: str
    content_hash: str
    hash_algo: str = "sha256"


class IngestRoutedEvent(_EventBase):
    """Recorded by ``wiki ingest`` after the orchestrator picks a route.

    Written on every outcome — single match, ambiguous, and no match —
    so ``wiki doctor`` and (future) ``journal explain`` can reconstruct
    what the user tried. Successful synthesis is recorded separately
    by :class:`SourceIngestEvent` after the vault-side ingester writes
    its pages.
    """

    type: Literal["ingest.routed"] = "ingest.routed"
    source: str
    content_type: str | None = None
    candidates: list[str] = Field(default_factory=list)
    via: Literal["auto", "as_flag"] = "auto"
    signals: list[str] = Field(default_factory=list)


class SourceIngestEvent(_EventBase):
    type: Literal["source.ingest"] = "source.ingest"
    source: str
    source_hash: str
    content_type: str
    produced_pages: list[str] = Field(default_factory=list)


class PageWriteEvent(_EventBase):
    type: Literal["page.write"] = "page.write"
    path: str
    hash: str
    hash_algo: str = "sha256"


class PageProposalEvent(_EventBase):
    type: Literal["page.proposal"] = "page.proposal"
    path: str
    proposed_path: str
    hash: str
    hash_algo: str = "sha256"


class PageConflictResolvedEvent(_EventBase):
    type: Literal["page.conflict_resolved"] = "page.conflict_resolved"
    path: str
    hash: str
    hash_algo: str = "sha256"
    # Optional managed-region label for per-region audit (retro-review C1).
    # ``None`` for whole-file resolves; older journal lines replay unchanged
    # under ADR-0002 §Negative's additive-schema rule.
    region: str | None = None


class OperationRunEvent(_EventBase):
    """Recorded by ``wiki run`` on every invocation that gets past the
    contract-load step (``docs/specs/task-17-wiki-run/spec.md``).

    ``args``, ``error``, and ``event_id`` are additive extensions per
    ADR-0002 §Negative's additive-schema rule — all have defaults so
    older journal lines (Task 3) keep replaying unchanged. ``status``
    is a Literal-bounded enum: pre-Task-17 lines could only have
    carried ``"dispatched"`` (no other emitter existed), so the
    narrowing rejects no legitimate legacy value.

    ``event_id`` is populated by ``run.dispatch`` via
    ``uuid.uuid4().hex[:12]`` for every new event. Older journal
    lines (no ``event_id`` key) replay with ``event_id is None``;
    the wiki-run-exec spec is the only consumer at v1 and tolerates
    that absence. See ``docs/specs/wiki-run-exec/spec.md`` §"Event
    identity".
    """

    type: Literal["operation.run"] = "operation.run"
    operation: str
    status: Literal["dispatched", "invalid_args"]
    period: str | None = None
    produced_pages: list[str] = Field(default_factory=list)
    args: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    event_id: str | None = None


class OperationExecFailedEvent(_EventBase):
    """Recorded by ``wiki run --exec`` when the subprocess attempt fails.

    Three failure shapes (see ``docs/specs/wiki-run-exec/spec.md``
    §Outputs):

    - ``non-zero-exit`` — Claude exited with a non-zero return code.
    - ``timeout`` — the subprocess was killed after
      ``WIKI_EXEC_TIMEOUT`` seconds. ``exit_code`` is the sentinel
      ``-2``.
    - ``conflict-refused`` — the kit refused to spawn the subprocess
      because the vault has unresolved ``.proposed`` sidecars.
      ``exit_code`` is ``-1``, ``stderr_tail`` is empty, sidecar
      paths live in ``conflict_sidecars``.

    Two reserved reasons (``binary-missing``, ``skill-missing``)
    appear in the Literal but are **not emitted at v1** — those
    failure modes raise ``WikiError`` before reaching the journal
    append. ``_append_failure_event`` enforces this with a
    ``RuntimeError`` guard.
    """

    type: Literal["operation.exec_failed"] = "operation.exec_failed"
    operation: str
    dispatch_event_id: str
    exit_code: int
    reason: Literal[
        "non-zero-exit",
        "timeout",
        "conflict-refused",
        "binary-missing",
        "skill-missing",
    ]
    stderr_tail: str = ""
    log_path: str | None = None
    conflict_sidecars: list[str] = Field(default_factory=list)


class ResearchQueryEvent(_EventBase):
    """Recorded by ``wiki research`` on every dispatch attempt.

    Task 18 extended the original shape with two optional fields with
    defaults so older journal lines keep replaying (ADR-0002 additive-
    schema invariant). See ``docs/specs/task-18-research-perplexity/spec.md``
    §"To the journal".
    """

    type: Literal["research.query"] = "research.query"
    query: str
    provider: str
    result_path: str | None = None
    model: str | None = None
    status: Literal["ok", "error"] = "ok"


class LintRunEvent(_EventBase):
    type: Literal["lint.run"] = "lint.run"
    status: str
    issues: int = 0


class ConfigSetEvent(_EventBase):
    type: Literal["config.set"] = "config.set"
    key: str
    value: str


class LockAcquiredEvent(_EventBase):
    """Recorded when a multi-event operation takes the journal-wide lock.

    ``reason`` is the free-text label that surfaces in ``wiki journal tail``
    so a user can see what's running. The journal-locking spec
    (``docs/specs/journal-locking/spec.md``) names this event as the
    enter-side bracket emitted by ``journal.transaction()``.
    """

    type: Literal["lock.acquired"] = "lock.acquired"
    reason: str | None = None


class LockReleasedEvent(_EventBase):
    """Recorded when ``journal.transaction()`` (or ``wiki lock release``) exits.

    ``reason`` is optional and defaults to ``None``. ``wiki lock acquire``
    sets it to ``"stale lock reclaimed"`` on the audit pair emitted when
    a dead-PID holder is reclaimed (spec §Edge cases, "Lock held by a
    dead PID"). Ordinary release paths leave it ``None``.
    """

    type: Literal["lock.released"] = "lock.released"
    reason: str | None = None


Event = Annotated[
    VaultInitEvent
    | VaultGitInitializedEvent
    | PrimitiveInstallEvent
    | PrimitiveRemoveEvent
    | PrimitiveUpgradeEvent
    | ManagedRegionWriteEvent
    | IngestRoutedEvent
    | SourceIngestEvent
    | PageWriteEvent
    | PageProposalEvent
    | PageConflictResolvedEvent
    | OperationRunEvent
    | OperationExecFailedEvent
    | ResearchQueryEvent
    | LintRunEvent
    | ConfigSetEvent
    | LockAcquiredEvent
    | LockReleasedEvent,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Vault state (derived by replay)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeldLock:
    """Snapshot of the journal lock's current holder.

    Populated by ``replay_state`` from a ``LockAcquiredEvent`` and cleared
    by a ``LockReleasedEvent``. ``acquired_at`` is the holding event's
    timestamp — ``wiki doctor`` compares it against ``WIKI_LOCK_STALE_HOURS``
    to surface stale locks (see ``docs/specs/journal-locking/spec.md``
    plan step 6).

    Frozen because replay treats it as a value, not an aggregate; mutations
    would let consumer code silently change derived state.
    """

    by: str
    acquired_at: datetime
    reason: str | None = None


class VaultState(_StrictModel):
    """Snapshot computed by ``journal.replay_state(events)`` (ADR-0002).

    Pydantic because tests serialize it across module boundaries; nothing
    here is meant to be edited by hand.
    """

    vault_name: str | None = None
    recipe: str | None = None
    installed_primitives: dict[str, str] = Field(default_factory=dict)
    page_writes: dict[str, PageWriteEvent] = Field(default_factory=dict)
    pending_proposals: dict[str, PageProposalEvent] = Field(default_factory=dict)
    ingested_sources: dict[str, SourceIngestEvent] = Field(default_factory=dict)
    recent_operations: dict[str, OperationRunEvent] = Field(default_factory=dict)
    recent_research: list[ResearchQueryEvent] = Field(default_factory=list)
    held_lock: HeldLock | None = None
