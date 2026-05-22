"""``--verbose`` / ``WIKI_DEBUG`` surface for the CLI boundary (qC1).

The CLI's ``WikiError`` boundary prints ``str(exc)`` on stderr by default —
no Python traceback, because end users (not engineers) read this output.
``--verbose`` and ``WIKI_DEBUG`` opt into the traceback for debugging,
appended *after* the human-readable line so the line itself stays at
column 0.

These tests pin the contract from two angles: the ``add`` handler covers
the ``_parse_primitive_spec`` raise path (which fires before any cwd or
vault state matters), and the ``doctor`` handler covers the "not a wiki
vault" raise path — together they prove the verbose plumbing covers
distinct handlers, not a single special-cased one. Combined with the
"every WikiError handler uses the same boundary" property the
centralised handler in ``cli.main`` provides, this is sufficient to
cover the qC1 fix sketch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki_kit import cli


@pytest.fixture(autouse=True)
def _scrub_wiki_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ``WIKI_DEBUG`` from the env for every test in this file.

    Without this scrub, a contributor who has ``WIKI_DEBUG=1`` exported
    in their shell (the obvious thing to do once this flag lands) would
    see the default-mode tests fail locally, and the explicit-set tests
    pass for the wrong reason. The two tests that need the variable set
    use ``monkeypatch.setenv`` to re-introduce it after this fixture
    runs.
    """

    monkeypatch.delenv("WIKI_DEBUG", raising=False)


def test_wiki_error_default_prints_message_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No flag, no env: stderr carries the message and nothing else."""

    rc = cli.main(["add", "people"])

    assert rc == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "<kind>:<name>" in err
    assert "Traceback" not in err
    assert "WikiError" not in err  # implementation detail should not leak


def test_verbose_flag_appends_traceback_after_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--verbose`` adds a Python traceback *after* the message line."""

    rc = cli.main(["--verbose", "add", "people"])

    assert rc == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "<kind>:<name>" in err
    assert "Traceback" in err
    assert "WikiError" in err
    # Ordering: message first, traceback second.
    assert err.index("<kind>:<name>") < err.index("Traceback")


def test_verbose_flag_works_when_placed_after_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``wiki add --verbose people`` is accepted as well as ``wiki --verbose add``.

    Users naturally reach for the post-subcommand placement (it's where
    most non-global flags live); supporting both keeps discoverability
    from ``wiki <cmd> --help`` aligned with what argparse accepts.
    """

    rc = cli.main(["add", "--verbose", "people"])

    assert rc == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "Traceback" in err


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "  1  "])
def test_wiki_debug_truthy_values_enable_traceback(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``WIKI_DEBUG`` set to any documented truthy spelling enables verbose."""

    monkeypatch.setenv("WIKI_DEBUG", value)
    rc = cli.main(["add", "people"])

    assert rc == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "Traceback" in err


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_wiki_debug_falsy_values_do_not_enable_traceback(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Anything not in the truthy allow-list (incl. ``"false"``) reads as off."""

    monkeypatch.setenv("WIKI_DEBUG", value)
    rc = cli.main(["add", "people"])

    assert rc == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "Traceback" not in err


def test_centralized_boundary_covers_doctor_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A second handler proves verbose is not special-cased on ``add``.

    ``wiki doctor`` invoked outside a vault raises ``WikiError`` from a
    different call site than the ``_parse_primitive_spec`` path. If the
    verbose surface is wired through ``main`` rather than a per-handler
    edit, both paths must honor it.
    """

    monkeypatch.chdir(tmp_path)
    rc = cli.main(["--verbose", "doctor"])

    assert rc == cli.WIKI_ERROR_EXIT
    err = capsys.readouterr().err
    assert "not a wiki vault" in err
    assert "Traceback" in err
