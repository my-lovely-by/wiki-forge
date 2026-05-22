"""Tests for ``llm_wiki_kit.models``.

These pin the shape that ADR-0005 names: every type that crosses disk is a
Pydantic v2 model, and the journal's ``Event`` type is a discriminated union
on a literal ``type`` field with one class per event type. ADR-0002 names
the load-bearing event types (``page.write``, ``page.proposal``,
``page.conflict_resolved``, ``managed_region.write``, plus the
``primitive.*`` and ``operation.*`` events that ``VaultState`` derives from).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from llm_wiki_kit.models import (
    ConfigSetEvent,
    Contribution,
    Event,
    HeldLock,
    IngestRoutedEvent,
    LintRunEvent,
    LockAcquiredEvent,
    LockReleasedEvent,
    ManagedRegionWriteEvent,
    OperationContract,
    OperationRunEvent,
    PageConflictResolvedEvent,
    PageProposalEvent,
    PageWriteEvent,
    Primitive,
    PrimitiveInstallEvent,
    PrimitiveKind,
    PrimitiveRemoveEvent,
    PrimitiveRouting,
    PrimitiveUpgradeEvent,
    ProviderConfig,
    Recipe,
    ResearchProvidersConfig,
    ResearchQueryEvent,
    SourceIngestEvent,
    VaultGitInitializedEvent,
    VaultInitEvent,
    VaultState,
)

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)
NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


def _valid_primitive_dict() -> dict[str, object]:
    return {
        "name": "meeting",
        "kind": "content-type",
        "version": "0.1.0",
        "description": "Ingest meeting transcripts into pages.",
    }


def test_primitive_validates_minimal_input() -> None:
    p = Primitive.model_validate(_valid_primitive_dict())
    assert p.name == "meeting"
    assert p.kind is PrimitiveKind.CONTENT_TYPE
    assert p.version == "0.1.0"
    assert p.requires == []
    assert p.contributes_to == []
    assert p.config == {}


def test_primitive_accepts_all_four_kinds() -> None:
    for kind in ("ontology", "content-type", "operation", "infrastructure"):
        data = _valid_primitive_dict()
        data["kind"] = kind
        Primitive.model_validate(data)


def test_primitive_rejects_unknown_kind() -> None:
    data = _valid_primitive_dict()
    data["kind"] = "skill"
    with pytest.raises(PydanticValidationError):
        Primitive.model_validate(data)


def test_primitive_rejects_bad_name() -> None:
    data = _valid_primitive_dict()
    data["name"] = "Meeting Notes"
    with pytest.raises(PydanticValidationError):
        Primitive.model_validate(data)


def test_primitive_rejects_bad_version() -> None:
    data = _valid_primitive_dict()
    data["version"] = "v1"
    with pytest.raises(PydanticValidationError):
        Primitive.model_validate(data)


def test_primitive_rejects_extra_top_level_fields() -> None:
    data = _valid_primitive_dict()
    data["mystery"] = "boom"
    with pytest.raises(PydanticValidationError):
        Primitive.model_validate(data)


def test_primitive_parses_contributions() -> None:
    data = _valid_primitive_dict()
    data["contributes_to"] = [
        {"file": "AGENTS.md", "region": "content-types"},
        {"file": "frontmatter.schema.yaml", "region": "fields"},
    ]
    p = Primitive.model_validate(data)
    assert p.contributes_to == [
        Contribution(file="AGENTS.md", region="content-types"),
        Contribution(file="frontmatter.schema.yaml", region="fields"),
    ]


def test_primitive_requires_is_a_list_of_strings() -> None:
    data = _valid_primitive_dict()
    data["requires"] = ["core", "people"]
    p = Primitive.model_validate(data)
    assert p.requires == ["core", "people"]


# ---------------------------------------------------------------------------
# PrimitiveRouting + the content-type-only constraint
# ---------------------------------------------------------------------------


def test_primitive_routing_defaults_to_none() -> None:
    p = Primitive.model_validate(_valid_primitive_dict())
    assert p.routing is None


def test_primitive_routing_parses_all_signal_lists() -> None:
    data = _valid_primitive_dict()
    data["routing"] = {
        "file_extensions": [".pdf", ".jpg"],
        "filename_patterns": ["EOB-*", "*receipt*"],
        "url_domains": ["allrecipes.com"],
        "url_path_patterns": ["/recipe/*"],
    }
    p = Primitive.model_validate(data)
    assert p.routing is not None
    assert p.routing.file_extensions == [".pdf", ".jpg"]
    assert p.routing.filename_patterns == ["EOB-*", "*receipt*"]
    assert p.routing.url_domains == ["allrecipes.com"]
    assert p.routing.url_path_patterns == ["/recipe/*"]


def test_primitive_routing_rejects_extra_signal_kinds() -> None:
    data = _valid_primitive_dict()
    data["routing"] = {"magic_bytes": ["%PDF"]}
    with pytest.raises(PydanticValidationError):
        Primitive.model_validate(data)


def test_primitive_routing_only_valid_on_content_type() -> None:
    for kind in ("ontology", "operation", "infrastructure"):
        data = _valid_primitive_dict()
        data["kind"] = kind
        data["routing"] = {"file_extensions": [".pdf"]}
        with pytest.raises(PydanticValidationError):
            Primitive.model_validate(data)


def test_primitive_routing_empty_block_is_allowed() -> None:
    data = _valid_primitive_dict()
    data["routing"] = {}
    p = Primitive.model_validate(data)
    assert p.routing == PrimitiveRouting()


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


def test_recipe_validates_minimal_input() -> None:
    r = Recipe.model_validate(
        {
            "name": "family",
            "version": "0.1.0",
            "description": "Family-oriented vault.",
            "primitives": ["core", "people", "meeting"],
        }
    )
    assert r.name == "family"
    assert r.primitives == ["core", "people", "meeting"]
    assert r.variables == {}


def test_recipe_rejects_missing_primitives_field() -> None:
    with pytest.raises(PydanticValidationError):
        Recipe.model_validate({"name": "family", "version": "0.1.0", "description": "x"})


def test_recipe_rejects_extra_fields() -> None:
    with pytest.raises(PydanticValidationError):
        Recipe.model_validate(
            {
                "name": "family",
                "version": "0.1.0",
                "description": "x",
                "primitives": ["core"],
                "extends": "personal",
            }
        )


# ---------------------------------------------------------------------------
# OperationContract
# ---------------------------------------------------------------------------


def test_operation_contract_validates_minimal_input() -> None:
    c = OperationContract.model_validate(
        {"name": "weekly-digest", "description": "Synthesize the week."}
    )
    assert c.name == "weekly-digest"
    assert c.period is None
    assert c.inputs == {}
    assert c.outputs == {}


def test_operation_contract_accepts_period_and_skill() -> None:
    c = OperationContract.model_validate(
        {
            "name": "weekly-digest",
            "description": "Synthesize the week.",
            "period": "weekly",
            "skill": "weekly-digest",
            "inputs": {"sources": {"type": "list", "items": "content-type"}},
            "outputs": {"digest": "Page"},
        }
    )
    assert c.period == "weekly"
    assert c.skill == "weekly-digest"
    assert c.inputs["sources"].type == "list"


# ---------------------------------------------------------------------------
# Event union — one class per event type
# ---------------------------------------------------------------------------


EVENT_CLASSES_BY_TYPE: dict[str, type] = {
    "vault.init": VaultInitEvent,
    "vault.git_initialized": VaultGitInitializedEvent,
    "primitive.install": PrimitiveInstallEvent,
    "primitive.remove": PrimitiveRemoveEvent,
    "primitive.upgrade": PrimitiveUpgradeEvent,
    "managed_region.write": ManagedRegionWriteEvent,
    "ingest.routed": IngestRoutedEvent,
    "source.ingest": SourceIngestEvent,
    "page.write": PageWriteEvent,
    "page.proposal": PageProposalEvent,
    "page.conflict_resolved": PageConflictResolvedEvent,
    "operation.run": OperationRunEvent,
    "research.query": ResearchQueryEvent,
    "lint.run": LintRunEvent,
    "config.set": ConfigSetEvent,
    "lock.acquired": LockAcquiredEvent,
    "lock.released": LockReleasedEvent,
}


EVENT_FIXTURES: dict[str, dict[str, object]] = {
    "vault.init": {"vault_name": "home", "recipe": "family"},
    "vault.git_initialized": {},
    "primitive.install": {"primitive": "meeting", "version": "0.1.0"},
    "primitive.remove": {"primitive": "meeting"},
    "primitive.upgrade": {
        "primitive": "meeting",
        "from_version": "0.1.0",
        "to_version": "0.2.0",
    },
    "managed_region.write": {
        "file": "AGENTS.md",
        "region": "content-types",
        "content_hash": "deadbeef" * 8,
    },
    "ingest.routed": {
        "source": "https://allrecipes.com/recipe/sheet-pan-tacos",
        "content_type": "recipe",
        "candidates": ["recipe"],
        "via": "auto",
        "signals": ["url_domain:allrecipes.com"],
    },
    "source.ingest": {
        "source": "/tmp/transcript.txt",
        "source_hash": "abc" * 21 + "d",
        "content_type": "meeting",
        "produced_pages": ["meetings/2026-05-15.md"],
    },
    "page.write": {
        "path": "meetings/2026-05-15.md",
        "hash": "feedface" * 8,
    },
    "page.proposal": {
        "path": "meetings/2026-05-15.md",
        "proposed_path": "meetings/2026-05-15.md.proposed",
        "hash": "feedface" * 8,
    },
    "page.conflict_resolved": {
        "path": "meetings/2026-05-15.md",
        "hash": "1234abcd" * 8,
    },
    "operation.run": {
        "operation": "weekly-digest",
        "period": "2026-W20",
        "status": "dispatched",
        "produced_pages": ["digests/2026-W20.md"],
    },
    "research.query": {
        "query": "rust async runtimes",
        "provider": "perplexity",
    },
    "lint.run": {"status": "ok", "issues": 0},
    "config.set": {"key": "search_backend", "value": "ripgrep"},
    "lock.acquired": {"reason": "2026-W20 digest"},
    "lock.released": {},
}


@pytest.mark.parametrize("type_name", sorted(EVENT_CLASSES_BY_TYPE))
def test_each_event_type_has_its_own_class(type_name: str) -> None:
    cls = EVENT_CLASSES_BY_TYPE[type_name]
    payload: dict[str, object] = {
        "type": type_name,
        "timestamp": NOW.isoformat(),
        "by": "core",
        **EVENT_FIXTURES[type_name],
    }
    event = EVENT_ADAPTER.validate_python(payload)
    assert isinstance(event, cls)
    assert event.type == type_name


def test_event_classes_are_all_distinct() -> None:
    seen = set(EVENT_CLASSES_BY_TYPE.values())
    assert len(seen) == len(EVENT_CLASSES_BY_TYPE)


def test_event_union_rejects_unknown_type() -> None:
    with pytest.raises(PydanticValidationError):
        EVENT_ADAPTER.validate_python(
            {
                "type": "made.up",
                "timestamp": NOW.isoformat(),
                "by": "core",
            }
        )


def test_event_union_rejects_missing_discriminator() -> None:
    with pytest.raises(PydanticValidationError):
        EVENT_ADAPTER.validate_python({"timestamp": NOW.isoformat(), "by": "core"})


def test_event_union_round_trips_through_json() -> None:
    original = PageWriteEvent(
        timestamp=NOW,
        by="meeting",
        path="meetings/2026-05-15.md",
        hash="cafebabe" * 8,
    )
    text = EVENT_ADAPTER.dump_json(original).decode()
    parsed = EVENT_ADAPTER.validate_json(text)
    assert parsed == original
    assert isinstance(parsed, PageWriteEvent)


def test_vault_git_initialized_event_roundtrip() -> None:
    """The bare event shape — only ``type``, ``timestamp``, ``by``, ``schema_version``.

    Spec §Outputs (`docs/specs/wiki-init-git/spec.md`) deliberately
    drops ``commit_sha`` and ``branch`` so the journal stays one line
    behind the commit's tree. This test pins that shape — adding a
    field here would silently violate the spec's clean-porcelain
    invariant.
    """

    original = VaultGitInitializedEvent(timestamp=NOW, by="wiki-init")
    text = EVENT_ADAPTER.dump_json(original).decode()
    parsed = EVENT_ADAPTER.validate_json(text)
    assert parsed == original
    assert isinstance(parsed, VaultGitInitializedEvent)
    assert parsed.type == "vault.git_initialized"
    assert parsed.by == "wiki-init"
    assert parsed.schema_version == 1


def test_vault_git_initialized_event_discriminator() -> None:
    """The ``type`` literal must dispatch through the discriminated union.

    A raw payload with ``type: "vault.git_initialized"`` and only the
    base fields must parse to ``VaultGitInitializedEvent`` — no extra
    fields required.
    """

    payload = {
        "type": "vault.git_initialized",
        "timestamp": NOW.isoformat(),
        "by": "wiki-init",
    }
    parsed = EVENT_ADAPTER.validate_python(payload)
    assert isinstance(parsed, VaultGitInitializedEvent)
    assert parsed.type == "vault.git_initialized"


def test_page_conflict_resolved_event_region_defaults_to_none() -> None:
    """Retro-review C1: ``region`` is optional and additive (ADR-0002 §Negative).

    Constructing without ``region=`` defaults to ``None`` for the
    whole-file conflict-resolve case.
    """

    e = PageConflictResolvedEvent(
        timestamp=NOW,
        by="wiki-conflict",
        path="meetings/2026-05-15.md",
        hash="a" * 64,
    )
    assert e.region is None


def test_page_conflict_resolved_event_legacy_json_without_region_still_replays() -> None:
    """ADR-0002 §Negative additive-schema rule: legacy lines stay valid.

    A pre-C1 journal line (no ``region`` key in the JSON) must parse
    through the event-union adapter and yield ``region=None``. This
    pins the on-disk replay contract — not just the Python default.
    """

    legacy_payload = {
        "type": "page.conflict_resolved",
        "timestamp": NOW.isoformat(),
        "by": "wiki-conflict",
        "path": "meetings/2026-05-15.md",
        "hash": "a" * 64,
    }
    parsed = EVENT_ADAPTER.validate_python(legacy_payload)
    assert isinstance(parsed, PageConflictResolvedEvent)
    assert parsed.region is None


def test_page_conflict_resolved_event_round_trips_with_region() -> None:
    """A managed-region resolve records the region label and round-trips."""

    original = PageConflictResolvedEvent(
        timestamp=NOW,
        by="wiki-conflict",
        path="AGENTS.md",
        hash="b" * 64,
        region="content-types",
    )
    text = EVENT_ADAPTER.dump_json(original).decode()
    parsed = EVENT_ADAPTER.validate_json(text)
    assert parsed == original
    assert isinstance(parsed, PageConflictResolvedEvent)
    assert parsed.region == "content-types"


def test_page_write_event_default_hash_algo_is_sha256() -> None:
    e = PageWriteEvent(
        timestamp=NOW,
        by="meeting",
        path="meetings/x.md",
        hash="a" * 64,
    )
    assert e.hash_algo == "sha256"


def test_managed_region_event_records_file_and_region() -> None:
    e = ManagedRegionWriteEvent(
        timestamp=NOW,
        by="meeting",
        file="AGENTS.md",
        region="content-types",
        content_hash="b" * 64,
    )
    assert e.file == "AGENTS.md"
    assert e.region == "content-types"


def test_ingest_routed_event_defaults_match_a_failed_route() -> None:
    e = IngestRoutedEvent(
        timestamp=NOW,
        by="wiki-ingest",
        source="/tmp/mystery.bin",
    )
    assert e.content_type is None
    assert e.candidates == []
    assert e.via == "auto"
    assert e.signals == []


def test_ingest_routed_event_carries_full_routing_record() -> None:
    e = IngestRoutedEvent(
        timestamp=NOW,
        by="wiki-ingest",
        source="EOB-2026-04-15.pdf",
        content_type="medical-record",
        candidates=["medical-record"],
        via="auto",
        signals=["filename_pattern:EOB-*", "file_extension:.pdf"],
    )
    assert e.type == "ingest.routed"
    assert e.content_type == "medical-record"
    assert e.candidates == ["medical-record"]
    assert e.signals == [
        "filename_pattern:EOB-*",
        "file_extension:.pdf",
    ]


def test_ingest_routed_event_rejects_unknown_via_value() -> None:
    with pytest.raises(PydanticValidationError):
        IngestRoutedEvent.model_validate(
            {
                "type": "ingest.routed",
                "timestamp": NOW.isoformat(),
                "by": "wiki-ingest",
                "source": "x",
                "via": "guessed",
            }
        )


def test_lock_event_round_trips_through_pydantic_union() -> None:
    """Both lock events serialize through ``Event`` and survive a round trip.

    Acceptance criterion from journal-locking spec §"Schema evolution".
    """

    acquired = LockAcquiredEvent(
        timestamp=NOW,
        by="weekly-digest",
        reason="Build 2026-W20 digest",
    )
    released = LockReleasedEvent(timestamp=NOW, by="weekly-digest")

    for original in (acquired, released):
        text = EVENT_ADAPTER.dump_json(original).decode()
        parsed = EVENT_ADAPTER.validate_json(text)
        assert parsed == original
        assert type(parsed) is type(original)


def test_lock_acquired_event_reason_defaults_to_none() -> None:
    e = LockAcquiredEvent(timestamp=NOW, by="weekly-digest")
    assert e.reason is None
    assert e.type == "lock.acquired"


def test_lock_released_event_reason_defaults_to_none() -> None:
    """``LockReleasedEvent.reason`` is the audit hook for the stale-holder reclaim.

    Step 5 added the optional ``reason`` field so ``wiki lock acquire``
    can record ``LockReleasedEvent(by="wiki-doctor", reason="stale lock
    reclaimed")`` before its own acquire — the audit pair described in
    spec §Edge cases ("Lock held by a dead PID"). Pin the default-None
    so a future caller that doesn't set it produces a wire format
    identical to the pre-amendment shape.
    """

    e = LockReleasedEvent(timestamp=NOW, by="weekly-digest")
    assert e.reason is None
    assert e.type == "lock.released"

    with_reason = LockReleasedEvent(timestamp=NOW, by="wiki-doctor", reason="stale lock reclaimed")
    assert with_reason.reason == "stale lock reclaimed"


# ---------------------------------------------------------------------------
# VaultState
# ---------------------------------------------------------------------------


def test_vault_state_defaults_are_empty() -> None:
    state = VaultState()
    assert state.vault_name is None
    assert state.recipe is None
    assert state.installed_primitives == {}
    assert state.page_writes == {}
    assert state.ingested_sources == {}
    assert state.recent_operations == {}
    assert state.recent_research == []
    assert state.pending_proposals == {}
    assert state.held_lock is None


def test_held_lock_is_frozen() -> None:
    """``HeldLock`` is a snapshot — replay never mutates it in place.

    Asserts the project decision (`dataclass(frozen=True)`) as well as
    the runtime enforcement, so a refactor to a mutable shape fails here
    rather than letting silently-mutating consumer code through.
    """

    import dataclasses

    assert dataclasses.is_dataclass(HeldLock)
    assert HeldLock.__dataclass_params__.frozen is True  # type: ignore[attr-defined]

    held = HeldLock(by="weekly-digest", acquired_at=NOW, reason="W20")
    with pytest.raises(AttributeError):
        held.by = "other"  # type: ignore[misc]


def test_vault_state_round_trips_with_held_lock() -> None:
    """``VaultState`` with a non-None ``held_lock`` survives JSON round-trip.

    Guards the stdlib-dataclass-inside-Pydantic-model serialization shape
    before step 6 (doctor) or a future ``wiki doctor --json`` flag depends
    on it.
    """

    state = VaultState(held_lock=HeldLock(by="weekly-digest", acquired_at=NOW, reason="W20"))
    restored = VaultState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.held_lock is not None
    assert restored.held_lock.by == "weekly-digest"
    assert restored.held_lock.acquired_at == NOW
    assert restored.held_lock.reason == "W20"


def test_vault_state_rejects_extra_fields() -> None:
    with pytest.raises(PydanticValidationError):
        VaultState.model_validate({"surprise": 1})


# ---------------------------------------------------------------------------
# Research providers config (Task 18)
# ---------------------------------------------------------------------------


def test_provider_config_minimal_yaml_round_trip() -> None:
    """An empty block parses with every field defaulted.

    Key-optional at the schema level — Task 19's Semantic Scholar will
    ship with no ``api_key_env``. Per-provider code (e.g. Perplexity's
    ``dispatch``) enforces its own requirements.
    """

    c = ProviderConfig.model_validate({})
    assert c.api_key_env is None
    assert c.endpoint is None
    assert c.model is None
    assert c.cost_signal is None
    assert c.strengths == []


def test_provider_config_with_api_key_env_set() -> None:
    c = ProviderConfig.model_validate({"api_key_env": "PERPLEXITY_API_KEY"})
    assert c.api_key_env == "PERPLEXITY_API_KEY"


def test_provider_config_accepts_full_shape() -> None:
    c = ProviderConfig.model_validate(
        {
            "api_key_env": "PERPLEXITY_API_KEY",
            "endpoint": "https://api.perplexity.ai/chat/completions",
            "model": "sonar-pro",
            "cost_signal": "low",
            "strengths": ["current_web_state", "cited_factual_lookup"],
        }
    )
    assert c.endpoint == "https://api.perplexity.ai/chat/completions"
    assert c.model == "sonar-pro"
    assert c.cost_signal == "low"
    assert c.strengths == ["current_web_state", "cited_factual_lookup"]


def test_provider_config_rejects_unknown_inner_key() -> None:
    """``_StrictModel`` enforces ``extra="forbid"`` on the inner block.

    A typo (`endpiont:` instead of `endpoint:`) surfaces here. The CLI
    boundary in ``_cmd_research`` wraps the ``ValidationError`` as a
    ``WikiError`` whose user-facing message quotes the bad field name —
    that contract is pinned in the CLI integration tests.
    """

    with pytest.raises(PydanticValidationError) as exc_info:
        ProviderConfig.model_validate({"endpiont": "https://example/x"})
    assert "endpiont" in str(exc_info.value)


def test_provider_config_rejects_unknown_cost_signal() -> None:
    with pytest.raises(PydanticValidationError):
        ProviderConfig.model_validate({"cost_signal": "exorbitant"})


def test_research_providers_config_one_provider() -> None:
    rpc = ResearchProvidersConfig.model_validate(
        {"perplexity": {"api_key_env": "PERPLEXITY_API_KEY", "model": "sonar-pro"}}
    )
    assert rpc.slugs() == ["perplexity"]
    assert rpc.root["perplexity"].api_key_env == "PERPLEXITY_API_KEY"


def test_research_providers_config_two_providers() -> None:
    rpc = ResearchProvidersConfig.model_validate(
        {
            "perplexity": {"api_key_env": "PERPLEXITY_API_KEY"},
            "gemini": {"api_key_env": "GEMINI_API_KEY"},
        }
    )
    assert rpc.slugs() == ["gemini", "perplexity"]


def test_research_providers_config_empty_root_parses() -> None:
    """A seed file with no contributors yet parses as an empty mapping.

    The dispatcher then surfaces ``no research providers installed``.
    """

    rpc = ResearchProvidersConfig.model_validate({})
    assert rpc.slugs() == []


def test_research_query_event_additive_fields_default() -> None:
    """New ``model`` and ``status`` fields default to ``None`` and ``"ok"``.

    Task 18 extended ``ResearchQueryEvent`` with two optional fields;
    older constructors that don't supply them still produce a valid
    event (ADR-0002 additive-schema invariant).
    """

    e = ResearchQueryEvent(
        timestamp=NOW,
        by="wiki-research",
        query="rust async runtimes",
        provider="perplexity",
    )
    assert e.model is None
    assert e.status == "ok"
    assert e.result_path is None


def test_research_query_event_legacy_json_without_new_fields_replays() -> None:
    """A pre-Task-18 journal line (no ``model`` / ``status``) replays cleanly.

    The disk format from before this PR shipped is what gets read on
    replay — additive-schema rule means older lines must parse via
    ``Event`` validation and gain defaulted new fields.
    """

    legacy_payload = {
        "type": "research.query",
        "timestamp": NOW.isoformat(),
        "by": "wiki-research",
        "query": "rust async runtimes",
        "provider": "perplexity",
        "result_path": None,
    }
    parsed = EVENT_ADAPTER.validate_python(legacy_payload)
    assert isinstance(parsed, ResearchQueryEvent)
    assert parsed.model is None
    assert parsed.status == "ok"


def test_research_query_event_round_trips_with_new_fields() -> None:
    original = ResearchQueryEvent(
        timestamp=NOW,
        by="wiki-research",
        query="rust async runtimes",
        provider="perplexity",
        result_path="research/rust-async.md",
        model="sonar-pro",
        status="ok",
    )
    text = EVENT_ADAPTER.dump_json(original).decode()
    parsed = EVENT_ADAPTER.validate_json(text)
    assert parsed == original
    assert isinstance(parsed, ResearchQueryEvent)
    assert parsed.model == "sonar-pro"
    assert parsed.status == "ok"


def test_research_query_event_rejects_unknown_status() -> None:
    with pytest.raises(PydanticValidationError):
        ResearchQueryEvent.model_validate(
            {
                "type": "research.query",
                "timestamp": NOW.isoformat(),
                "by": "wiki-research",
                "query": "q",
                "provider": "perplexity",
                "status": "partial",
            }
        )
