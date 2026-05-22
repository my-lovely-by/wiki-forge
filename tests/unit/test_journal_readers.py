"""Unit tests for ``wiki journal {tail,grep,explain}``.

Mirrors the acceptance criteria in
``docs/specs/wiki-journal-readers/spec.md`` 1:1 — every AC bullet has a
test below. Tests exercise the CLI handlers via :func:`main`, with
``monkeypatch.chdir(tmp_path)`` standing in for "the user is sitting in
a vault root".
"""

from __future__ import annotations

import hashlib
import typing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_wiki_kit.cli import (
    _EVENT_SUMMARY_FIELDS,
    WIKI_ERROR_EXIT,
    _format_event_line,
    main,
)
from llm_wiki_kit.journal import append_event, dump_event_json
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
    PrimitiveInstallEvent,
    PrimitiveRemoveEvent,
    PrimitiveUpgradeEvent,
    ResearchQueryEvent,
    SourceIngestEvent,
    VaultGitInitializedEvent,
    VaultInitEvent,
)

NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vault(tmp_path: Path) -> Path:
    """Create a minimal vault layout under ``tmp_path``."""

    (tmp_path / ".wiki.journal").mkdir()
    return tmp_path


def _journal(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def _seed(vault: Path, events: list[Event]) -> None:
    """Append events in order; each gets a per-call lock + fsync."""

    path = _journal(vault)
    for event in events:
        append_event(path, event)


def _sample_events(count: int = 5) -> list[Event]:
    """Return ``count`` distinct events, each at a one-second offset."""

    candidates: list[Event] = [
        VaultInitEvent(timestamp=NOW, by="wiki-init", vault_name="alpha", recipe="family"),
        PrimitiveInstallEvent(
            timestamp=NOW.replace(second=1), by="wiki-init", primitive="core", version="1.0.0"
        ),
        PageWriteEvent(
            timestamp=NOW.replace(second=2),
            by="wiki-add",
            path="people/alice.md",
            hash=_hash("alice content"),
        ),
        IngestRoutedEvent(
            timestamp=NOW.replace(second=3),
            by="wiki-ingest",
            source="memo.pdf",
            content_type="meeting",
            candidates=["meeting"],
            via="auto",
            signals=["file_extension:.pdf"],
        ),
        OperationRunEvent(
            timestamp=NOW.replace(second=4),
            by="wiki-run",
            operation="weekly-digest",
            status="dispatched",
        ),
    ]
    return candidates[:count]


# ---------------------------------------------------------------------------
# tail
# ---------------------------------------------------------------------------


def test_tail_empty_journal_prints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _journal(vault).touch()
    monkeypatch.chdir(vault)

    assert main(["journal", "tail"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_tail_n_3_prints_last_three_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(5))
    monkeypatch.chdir(vault)

    assert main(["journal", "tail", "-n", "3"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    types_in_order = [line.split("\t")[3] for line in lines]
    assert types_in_order == ["page.write", "ingest.routed", "operation.run"]


def test_tail_default_is_10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    # 15 events; default should clip to 10.
    events: list[Event] = []
    for i in range(15):
        events.append(
            PageWriteEvent(
                timestamp=NOW.replace(microsecond=i),
                by="wiki-add",
                path=f"p{i}.md",
                hash=_hash(f"v{i}"),
            )
        )
    _seed(vault, events)
    monkeypatch.chdir(vault)

    assert main(["journal", "tail"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 10


def test_tail_n_larger_than_journal_prints_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(3))
    monkeypatch.chdir(vault)

    assert main(["journal", "tail", "-n", "100"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 3


def test_tail_n_zero_raises_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(2))
    monkeypatch.chdir(vault)

    assert main(["journal", "tail", "-n", "0"]) == WIKI_ERROR_EXIT
    assert "--lines must be a positive integer" in capsys.readouterr().err


def test_tail_n_negative_raises_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(2))
    monkeypatch.chdir(vault)

    assert main(["journal", "tail", "-n", "-5"]) == WIKI_ERROR_EXIT
    assert "--lines must be a positive integer" in capsys.readouterr().err


def test_tail_n_non_integer_raises_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(2))
    monkeypatch.chdir(vault)

    assert main(["journal", "tail", "-n", "abc"]) == WIKI_ERROR_EXIT
    assert "--lines must be a positive integer" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


def test_grep_substring_match_against_canonical_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(5))
    monkeypatch.chdir(vault)

    # ``alice.md`` lives in the page.write event's ``path`` field — present in
    # the canonical JSON (which is what ``journal.dump_event_json`` returns)
    # so the substring match should hit one event.
    assert main(["journal", "grep", "alice.md"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].split("\t")[3] == "page.write"


def test_grep_type_filter_narrows_before_substring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    events: list[Event] = [
        PageWriteEvent(
            timestamp=NOW,
            by="wiki-add",
            path="people/foo.md",
            hash=_hash("a"),
        ),
        IngestRoutedEvent(
            timestamp=NOW.replace(second=1),
            by="wiki-ingest",
            source="foo.pdf",
            content_type="meeting",
            via="auto",
        ),
    ]
    _seed(vault, events)
    monkeypatch.chdir(vault)

    assert main(["journal", "grep", "--type", "page.write", "foo"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].split("\t")[3] == "page.write"


def test_grep_type_filter_unknown_type_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(2))
    monkeypatch.chdir(vault)

    assert main(["journal", "grep", "--type", "bogus.type", "anything"]) == 0
    assert capsys.readouterr().out == ""


def test_grep_no_matches_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(2))
    monkeypatch.chdir(vault)

    assert main(["journal", "grep", "xyzzy-no-such-thing"]) == 0
    assert capsys.readouterr().out == ""


def test_grep_empty_pattern_raises_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(1))
    monkeypatch.chdir(vault)

    assert main(["journal", "grep", ""]) == WIKI_ERROR_EXIT
    assert "non-empty" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


def test_explain_prints_multiline_block_for_valid_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(3))
    monkeypatch.chdir(vault)

    assert main(["journal", "explain", "1"]) == 0
    out = capsys.readouterr().out
    assert "Event 1 of 3 in .wiki.journal/journal.jsonl" in out
    assert "Type:      vault.init" in out
    assert "By:        wiki-init" in out
    assert "vault_name: alpha" in out
    assert "recipe: family" in out


def test_explain_out_of_range_raises_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(3))
    monkeypatch.chdir(vault)

    assert main(["journal", "explain", "999"]) == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "no event at line 999" in err
    assert "journal has 3 events" in err


def test_explain_empty_journal_uses_out_of_range_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _journal(vault).touch()
    monkeypatch.chdir(vault)

    assert main(["journal", "explain", "1"]) == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "no event at line 1" in err
    assert "journal has 0 events" in err


def test_explain_non_integer_raises_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(1))
    monkeypatch.chdir(vault)

    assert main(["journal", "explain", "abc"]) == WIKI_ERROR_EXIT
    assert "event must be a positive integer" in capsys.readouterr().err


def test_explain_non_positive_raises_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(1))
    monkeypatch.chdir(vault)

    assert main(["journal", "explain", "0"]) == WIKI_ERROR_EXIT
    assert "event must be a positive integer" in capsys.readouterr().err

    assert main(["journal", "explain", "-3"]) == WIKI_ERROR_EXIT
    assert "event must be a positive integer" in capsys.readouterr().err


def test_explain_value_tabs_and_newlines_are_substituted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(
        vault,
        [
            ConfigSetEvent(
                timestamp=NOW,
                by="wiki-config",
                key="banner",
                value="line1\tcol\nline2",
            )
        ],
    )
    monkeypatch.chdir(vault)

    # tail: TSV must remain splittable to exactly 5 fields.
    assert main(["journal", "tail"]) == 0
    tail_out = capsys.readouterr().out.strip()
    assert tail_out.count("\n") == 0, "TSV row should be one line"
    assert tail_out.count("\t") == 4, "TSV must keep exactly five fields"

    # explain: tabs and newlines in the value collapse to spaces so the
    # multi-line block keeps one field per line.
    assert main(["journal", "explain", "1"]) == 0
    block = capsys.readouterr().out
    assert "\t" not in block.split("value: ", 1)[1].splitlines()[0]
    value_line = next(line for line in block.splitlines() if line.startswith("value: "))
    assert "line1" in value_line and "line2" in value_line
    # The original \n inside the value would have produced a second "line2"
    # line; substitution collapses that.
    assert sum(1 for line in block.splitlines() if line.startswith("value: ")) == 1


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["journal", "tail"],
        ["journal", "grep", "foo"],
        ["journal", "explain", "1"],
    ],
    ids=lambda a: " ".join(a),
)
def test_readers_outside_vault_raise_wiki_error(
    argv: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # tmp_path has no .wiki.journal — invocations should report the missing
    # vault rather than silently exit 0.
    monkeypatch.chdir(tmp_path)
    assert main(argv) == WIKI_ERROR_EXIT
    assert "not a wiki vault" in capsys.readouterr().err


def test_readers_propagate_corruption_as_wiki_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(1))
    # Add a garbage line so read_events raises on line 2.
    with _journal(vault).open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    monkeypatch.chdir(vault)

    assert main(["journal", "tail"]) == WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "line 2" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["journal", "tail"],
        ["journal", "grep", "alice"],
        ["journal", "explain", "1"],
    ],
    ids=lambda a: " ".join(a),
)
def test_readers_do_not_mutate_journal(
    argv: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = _vault(tmp_path)
    _seed(vault, _sample_events(3))
    monkeypatch.chdir(vault)

    journal_path = _journal(vault)
    before_bytes = journal_path.read_bytes()
    before_size = journal_path.stat().st_size

    main(argv)

    assert journal_path.read_bytes() == before_bytes
    assert journal_path.stat().st_size == before_size


def test_format_event_line_round_trips_through_explain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every line number printed by ``tail`` is a valid ``explain`` input.

    Tests the line-number-stability invariant against a journal with
    intentional mid-file blanks, whitespace-only lines, and CRLF line
    endings — the three shapes the spec explicitly covers.
    """

    vault = _vault(tmp_path)
    _seed(vault, _sample_events(3))
    journal_path = _journal(vault)

    # Inject a CRLF-ended event, a blank line, and a whitespace-only line.
    extra = PageWriteEvent(
        timestamp=NOW.replace(second=5),
        by="wiki-add",
        path="people/bob.md",
        hash=_hash("bob"),
    )
    with journal_path.open("a", encoding="utf-8") as fh:
        # whitespace-only line
        fh.write("   \n")
        # CRLF-terminated event
        fh.write(dump_event_json(extra) + "\r\n")
        # truly blank line
        fh.write("\n")

    monkeypatch.chdir(vault)

    # tail emits all 4 events; collect printed line numbers.
    assert main(["journal", "tail"]) == 0
    tail_lines = capsys.readouterr().out.splitlines()
    line_numbers = [int(line.split("\t")[0]) for line in tail_lines]
    assert len(line_numbers) == 4

    # Each printed line number should resolve via explain to a matching type.
    for printed_line, tail_row in zip(line_numbers, tail_lines, strict=True):
        expected_type = tail_row.split("\t")[3]
        assert main(["journal", "explain", str(printed_line)]) == 0
        block = capsys.readouterr().out
        assert f"Type:      {expected_type}" in block


def _concrete_event_classes() -> list[type]:
    """Unwrap the Annotated[union, Field(...)] to get the concrete classes."""

    args = typing.get_args(Event)
    union = args[0]
    return list(typing.get_args(union))


@pytest.mark.parametrize(
    "event_class",
    _concrete_event_classes(),
    ids=lambda c: c.__name__,
)
def test_format_event_line_covers_every_concrete_event_class(event_class: type) -> None:
    """Every concrete event class has a row in ``_EVENT_SUMMARY_FIELDS``.

    Asserts the formatter doesn't raise and produces the documented
    prefix. Adding a new event class without a summary row regresses
    this test.
    """

    instance = _build_instance(event_class)
    line = _format_event_line(7, instance)
    fields = line.split("\t")
    assert len(fields) == 5, f"expected 5 tab-separated fields, got {fields!r}"
    # Field layout per spec §Outputs:
    # ``<line>\t<timestamp>\t<by>\t<type>\t<summary>``
    assert fields[0] == "7"
    assert fields[2] == instance.by
    assert fields[3] == instance.type


_SUMMARY_FIXTURES: list[tuple[type, dict[str, object], str]] = [
    # (event_class, kwargs beyond timestamp/by, expected summary).
    # Pins the spec §Outputs table row-by-row so the labels can't drift.
    (
        VaultInitEvent,
        {"vault_name": "alpha", "recipe": "family"},
        "vault=alpha recipe=family",
    ),
    (
        # `vault.git_initialized` carries no per-event payload fields
        # (see `docs/specs/wiki-init-git/spec.md` §Outputs). The
        # summary is the empty string — pinned here so a future field
        # accidentally added to the event class wouldn't silently
        # change the journal-readers contract.
        VaultGitInitializedEvent,
        {},
        "",
    ),
    (
        PrimitiveInstallEvent,
        {"primitive": "core", "version": "1.0.0"},
        "primitive=core version=1.0.0",
    ),
    (
        PrimitiveRemoveEvent,
        {"primitive": "core"},
        "primitive=core",
    ),
    (
        PrimitiveUpgradeEvent,
        {"primitive": "core", "from_version": "1.0.0", "to_version": "1.1.0"},
        "primitive=core from=1.0.0 to=1.1.0",
    ),
    (
        ManagedRegionWriteEvent,
        {"file": "AGENTS.md", "region": "fields", "content_hash": "deadbeef"},
        "file=AGENTS.md region=fields",
    ),
    (
        IngestRoutedEvent,
        {"source": "memo.pdf", "content_type": "meeting", "via": "auto"},
        "source=memo.pdf content_type=meeting via=auto",
    ),
    (
        SourceIngestEvent,
        {"source": "memo.pdf", "source_hash": "abc", "content_type": "meeting"},
        "source=memo.pdf content_type=meeting",
    ),
    (
        PageWriteEvent,
        {"path": "people/alice.md", "hash": "abc"},
        "path=people/alice.md",
    ),
    (
        PageProposalEvent,
        {"path": "x.md", "proposed_path": "x.md.proposed", "hash": "abc"},
        "path=x.md proposed=x.md.proposed",
    ),
    (
        PageConflictResolvedEvent,
        {"path": "x.md", "hash": "abc"},  # region is None → omitted
        "path=x.md",
    ),
    (
        PageConflictResolvedEvent,
        {"path": "x.md", "hash": "abc", "region": "fields"},
        "path=x.md region=fields",
    ),
    (
        OperationRunEvent,
        {"operation": "weekly-digest", "status": "dispatched"},
        "operation=weekly-digest status=dispatched",
    ),
    (
        ResearchQueryEvent,
        {"query": "q", "provider": "perplexity"},
        "provider=perplexity status=ok",
    ),
    (
        LintRunEvent,
        {"status": "ok"},
        "status=ok issues=0",
    ),
    (
        ConfigSetEvent,
        {"key": "banner", "value": "v"},
        "key=banner",
    ),
    (
        LockAcquiredEvent,
        {"reason": "wiki research dispatch"},
        "reason=wiki research dispatch",
    ),
    (
        LockAcquiredEvent,
        {},  # reason None
        "reason=(none)",
    ),
    (
        LockReleasedEvent,
        {},
        "reason=(none)",
    ),
]


@pytest.mark.parametrize(
    ("event_class", "extra_kwargs", "expected_summary"),
    _SUMMARY_FIXTURES,
    ids=[f"{fixture[0].__name__}[{i}]" for i, fixture in enumerate(_SUMMARY_FIXTURES)],
)
def test_format_event_line_summary_matches_spec(
    event_class: type, extra_kwargs: dict[str, object], expected_summary: str
) -> None:
    """Every spec §Outputs row produces exactly the documented summary string."""

    event = event_class(timestamp=NOW, by="test", **extra_kwargs)
    line = _format_event_line(1, event)
    fields = line.split("\t")
    assert fields[4] == expected_summary


def test_format_event_line_raises_on_unmapped_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A type missing from ``_EVENT_SUMMARY_FIELDS`` must blow up loudly.

    Uses ``monkeypatch.delitem`` so a future change to the body of the
    assertion (one that raises something ``pytest.raises`` doesn't
    catch) can't leave the module-global table broken for the rest of
    the test session.
    """

    event = VaultInitEvent(timestamp=NOW, by="wiki-init", vault_name="x", recipe="family")
    monkeypatch.delitem(_EVENT_SUMMARY_FIELDS, VaultInitEvent)
    with pytest.raises(KeyError):
        _format_event_line(1, event)


def test_dump_event_json_matches_on_disk_bytes(tmp_path: Path) -> None:
    """``dump_event_json`` and ``append_event`` must agree byte-for-byte.

    The substring-on-canonical-JSON contract that ``grep`` relies on
    only holds if the bytes ``dump_event_json`` returns are exactly
    the bytes ``append_event`` writes (less the trailing newline). If
    one ever adds e.g. ``sort_keys`` and the other doesn't, ``grep``
    silently stops finding values that are clearly visible in the
    file. This test pins them together.
    """

    vault = _vault(tmp_path)
    events = _sample_events(5)
    _seed(vault, events)

    on_disk = _journal(vault).read_text(encoding="utf-8").splitlines()
    for event, line in zip(events, on_disk, strict=True):
        assert line == dump_event_json(event)


def test_explain_renders_non_empty_list_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``explain`` joins list-typed fields with ``, `` per spec §Outputs.

    ``IngestRoutedEvent.signals`` is a ``list[str]`` that only the
    explain block reaches (the tail/grep summary doesn't include it).
    Pins the ``", "`` join contract so a future formatter rewrite
    that defaulted to ``str(value)`` (producing Python's
    ``"['a', 'b']"`` repr) would fail loudly.
    """

    vault = _vault(tmp_path)
    _seed(
        vault,
        [
            IngestRoutedEvent(
                timestamp=NOW,
                by="wiki-ingest",
                source="memo.pdf",
                content_type="meeting",
                candidates=["meeting", "weekly-digest"],
                via="auto",
                signals=["file_extension:.pdf", "mime:application/pdf"],
            )
        ],
    )
    monkeypatch.chdir(vault)

    assert main(["journal", "explain", "1"]) == 0
    block = capsys.readouterr().out
    assert "signals: file_extension:.pdf, mime:application/pdf" in block
    assert "candidates: meeting, weekly-digest" in block


def test_summary_table_has_no_list_typed_fields() -> None:
    """Pins spec §Outputs' "no list fields in the summary table" rule."""

    for event_class, fields in _EVENT_SUMMARY_FIELDS.items():
        model_fields = event_class.model_fields
        for field_name, _label, _omit_when_none in fields:
            annotation = model_fields[field_name].annotation
            origin = typing.get_origin(annotation)
            assert origin is not list, (
                f"{event_class.__name__}.{field_name} is list-typed; "
                "tail/grep summary rendering for lists is unspecified."
            )


# ---------------------------------------------------------------------------
# Helpers for the parametrised "every event class" test
# ---------------------------------------------------------------------------


def _build_instance(cls: type) -> typing.Any:
    """Construct a minimal instance of ``cls`` with stub field values.

    Uses the model's field defaults where available, and a per-type
    fallback for the required fields each event class adds beyond
    ``_EventBase``.
    """

    kwargs: dict[str, object] = {"timestamp": NOW, "by": "test"}

    extras_by_class: dict[type, dict[str, object]] = {
        VaultInitEvent: {"vault_name": "x", "recipe": "family"},
        VaultGitInitializedEvent: {},
        PrimitiveInstallEvent: {"primitive": "core", "version": "1.0.0"},
        PrimitiveRemoveEvent: {"primitive": "core"},
        PrimitiveUpgradeEvent: {
            "primitive": "core",
            "from_version": "1.0.0",
            "to_version": "1.1.0",
        },
        ManagedRegionWriteEvent: {
            "file": "x.md",
            "region": "fields",
            "content_hash": "deadbeef",
        },
        IngestRoutedEvent: {
            "source": "memo.pdf",
            "content_type": "meeting",
            "via": "auto",
        },
        SourceIngestEvent: {
            "source": "memo.pdf",
            "source_hash": "abc",
            "content_type": "meeting",
        },
        PageWriteEvent: {"path": "x.md", "hash": "deadbeef"},
        PageProposalEvent: {
            "path": "x.md",
            "proposed_path": "x.md.proposed",
            "hash": "deadbeef",
        },
        PageConflictResolvedEvent: {"path": "x.md", "hash": "deadbeef"},
        OperationRunEvent: {"operation": "weekly-digest", "status": "dispatched"},
        OperationExecFailedEvent: {
            "operation": "weekly-digest",
            "dispatch_event_id": "0123456789ab",
            "exit_code": 137,
            "reason": "non-zero-exit",
        },
        ResearchQueryEvent: {"query": "q", "provider": "perplexity"},
        LintRunEvent: {"status": "ok"},
        ConfigSetEvent: {"key": "k", "value": "v"},
        LockAcquiredEvent: {},
        LockReleasedEvent: {},
    }
    kwargs.update(extras_by_class[cls])
    return cls(**kwargs)
