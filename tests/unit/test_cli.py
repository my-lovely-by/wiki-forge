"""Tests for the ``wiki`` CLI skeleton.

These tests assert the shape of the dispatcher — every subcommand listed in
RFC-0001 is reachable, ``--help`` works, and the stub handlers exit with
the expected sentinel status. They don't assert anything about behavior,
because there isn't any yet.
"""

from __future__ import annotations

import pytest

from llm_wiki_kit import __version__
from llm_wiki_kit.cli import NOT_IMPLEMENTED_EXIT, build_parser, main

SUBCOMMANDS_WITH_ARGS: list[list[str]] = [
    # ``init`` graduated from stub to real handler in Task 10; ``add``
    # and ``doctor`` graduated in Task 12; ``ingest`` graduated in
    # Task 16; ``run`` graduated in Task 17; ``research`` graduated in
    # Task 18; ``search`` graduated in Phase F Task 24
    # (``docs/specs/wiki-search/``); ``journal {tail,grep,explain}``
    # graduated alongside ``docs/specs/wiki-journal-readers/``;
    # ``upgrade`` graduated in Phase F Task 23
    # (``docs/specs/wiki-upgrade/``). Each has its own integration or
    # unit suite (``tests/unit/test_journal_readers.py`` for the
    # journal readers; ``tests/integration/test_wiki_upgrade.py`` for
    # ``wiki upgrade``).
    #
    # No subcommand stubs remain after Phase F.
]


def test_top_level_help_lists_all_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for cmd in (
        "init",
        "add",
        "upgrade",
        "doctor",
        "ingest",
        "run",
        "research",
        "search",
        "journal",
    ):
        assert cmd in out, f"top-level help missing subcommand {cmd!r}"


def test_version_flag_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code != 0


@pytest.mark.parametrize("argv", SUBCOMMANDS_WITH_ARGS, ids=lambda a: " ".join(a))
def test_subcommand_stub_returns_not_implemented(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == NOT_IMPLEMENTED_EXIT
    err = capsys.readouterr().err
    assert "not yet implemented" in err


@pytest.mark.parametrize(
    "subcommand",
    ["init", "add", "upgrade", "doctor", "ingest", "run", "research", "search", "journal"],
)
def test_each_subcommand_has_help(subcommand: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args([subcommand, "--help"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out  # non-empty help text


@pytest.mark.parametrize("subcommand", ["tail", "grep", "explain"])
def test_journal_subcommand_has_help(subcommand: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["journal", subcommand, "--help"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out


def test_init_requires_recipe() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["init", "/tmp/vault"])


def test_journal_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["journal"])


# ---------------------------------------------------------------------------
# ``--verbose`` flag (qC1). The behavior the flag *enables* — a Python
# traceback appended after the WikiError message line — lives in
# ``tests/integration/test_verbose_flag.py``; these unit tests cover only
# the parser shape so a future contributor doesn't drop the flag without
# noticing.
# ---------------------------------------------------------------------------


def test_verbose_flag_default_is_off() -> None:
    """No --verbose anywhere → ``getattr(args, "verbose", False)`` is False.

    The parser uses ``default=argparse.SUPPRESS`` so the attribute is
    absent unless the flag is passed; ``_is_verbose`` reads via getattr
    with a False fallback.
    """

    args = build_parser().parse_args(["doctor"])
    assert getattr(args, "verbose", False) is False


def test_verbose_flag_is_parseable_before_subcommand() -> None:
    args = build_parser().parse_args(["--verbose", "doctor"])
    assert args.verbose is True


def test_verbose_flag_is_parseable_after_subcommand() -> None:
    """Both positions work because the flag lives on a shared parent parser.

    Without the SUPPRESS default, the subparser would unconditionally set
    ``args.verbose = False`` and erase a True value the top-level parser
    had already written — this test pins the cross-parser handoff.
    """

    args = build_parser().parse_args(["doctor", "--verbose"])
    assert args.verbose is True


def test_top_level_help_lists_verbose(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    assert "--verbose" in capsys.readouterr().out


_LEAF_SUBCOMMANDS: list[list[str]] = [
    ["init"],
    ["add"],
    ["upgrade"],
    ["doctor"],
    ["ingest"],
    ["resolve"],
    ["lock", "acquire"],
    ["lock", "release"],
    ["run"],
    ["research"],
    ["search"],
    ["journal", "tail"],
    ["journal", "grep"],
    ["journal", "explain"],
]


@pytest.mark.parametrize("argv", _LEAF_SUBCOMMANDS, ids=lambda a: " ".join(a))
def test_every_leaf_subcommand_help_lists_verbose(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """``wiki <leaf> --help`` surfaces ``--verbose`` for every leaf.

    Guards the discoverability contract: a future contributor adding a
    new leaf subcommand has to remember ``parents=[verbose_parent]``,
    and this parametrised assertion fails loudly if they don't.
    """

    with pytest.raises(SystemExit):
        build_parser().parse_args([*argv, "--help"])
    assert "--verbose" in capsys.readouterr().out
