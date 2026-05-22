"""End-to-end integration tests for the Task-15 ``personal`` recipe.

Task 15 (RFC-0001) expanded ``recipes/personal.yaml`` past the
core-only shape into a deliberate composition of Task 11, Task 13, and
Task 14 primitives plus the new ``identity`` ontology. The recipe is
*composition* — these tests pin the closure shape and the one piece of
new behaviour (interpolation of the seeded ``identity.md``) so a future
"add medical to personal" or "drop trips from personal" change has to
update the closure expectation and the comment block in lockstep.

Like the Task-13 and Task-14 suites, this runs against the real shipped
catalog via ``cli._kit_paths()`` rather than fixtures-on-disk — the
recipe-as-shipped is the contract.
"""

from __future__ import annotations

from pathlib import Path

from llm_wiki_kit.cli import main
from llm_wiki_kit.journal import read_events, replay_state
from llm_wiki_kit.models import PrimitiveInstallEvent, VaultInitEvent

RECIPE = "personal"

# Full transitive closure of the personal recipe's ``primitives:`` list.
# ``core`` is always installed; ``food`` and ``trips`` are pulled in via
# ``recipe`` / ``trip-doc`` respectively (the recipe also lists them
# directly for readability). Nothing in this set is work-OS or
# household shaped — see the comment block in ``recipes/personal.yaml``
# for why.
EXPECTED_PRIMITIVES = {
    "action-item",
    "core",
    "decision",
    "follow-up-tracker",
    "food",
    "identity",
    "meal-planning",
    "meeting",
    "people",
    "recipe",
    "trip-doc",
    "trip-prep",
    "trips",
    "weekly-digest",
}


def _journal_path(vault: Path) -> Path:
    return vault / ".wiki.journal" / "journal.jsonl"


def test_personal_init_installs_expected_closure(tmp_path: Path) -> None:
    """``wiki init --recipe personal`` resolves the recipe's full closure."""

    vault = tmp_path / "v"

    assert main(["init", str(vault), "--recipe", RECIPE]) == 0

    events = read_events(_journal_path(vault))

    assert isinstance(events[0], VaultInitEvent)
    assert events[0].recipe == RECIPE

    install_events = [e for e in events if isinstance(e, PrimitiveInstallEvent)]
    installed = {e.primitive for e in install_events}
    assert installed == EXPECTED_PRIMITIVES

    state = replay_state(events)
    assert state.recipe == RECIPE
    assert set(state.installed_primitives) == EXPECTED_PRIMITIVES
    assert state.installed_primitives["core"] == "0.1.0"
    assert state.installed_primitives["identity"] == "0.1.0"


def test_personal_init_excludes_household_and_work_os_primitives(tmp_path: Path) -> None:
    """The recipe deliberately omits medical / vendors / work-OS primitives.

    The comment block in ``recipes/personal.yaml`` is the contract for
    these omissions; this test pins it so a future "let's add medical to
    personal" change has to update the comment block too.
    """

    vault = tmp_path / "v"

    assert main(["init", str(vault), "--recipe", RECIPE]) == 0

    state = replay_state(read_events(_journal_path(vault)))
    installed = set(state.installed_primitives)

    forbidden = {
        # Household-shape (Task 13).
        "medical",
        "medical-record",
        "medical-summary",
        "vendors",
        "receipt",
        "tax-document",
        # Work-OS shape (Task 14).
        "customers",
        "customer-feedback",
        "domains",
        "interview",
        "onboarding-pack",
        "projects",
        "renewal-reminders",
        "stakeholder-map-refresh",
        "stakeholder-update",
        "status-synthesis",
        "action-item-rollup",
        "vendor-contract",
    }
    leaked = forbidden & installed
    assert not leaked, f"personal closure should not include: {sorted(leaked)}"


def test_personal_init_seeds_identity_page(tmp_path: Path) -> None:
    """The new ``identity`` ontology seeds ``identity.md`` at the vault root.

    The page is on the ``INTERPOLATED_FILES`` allowlist, so the four
    ``{owner_*}`` tokens get substituted from the recipe's
    ``variables:`` defaults. With the recipe shipping empty-string
    defaults, the tokens render as visibly empty rather than as raw
    ``{owner_name}`` placeholders.
    """

    vault = tmp_path / "v"

    assert main(["init", str(vault), "--recipe", RECIPE]) == 0

    identity = vault / "identity.md"
    assert identity.is_file()

    body = identity.read_text(encoding="utf-8")

    # The tokens themselves must not survive (otherwise interpolation broke).
    for token in ("{owner_name}", "{owner_pronouns}", "{owner_role}", "{owner_timezone}"):
        assert token not in body, f"identity.md still contains raw token {token!r}"

    # The empty-string defaults render as `Field: ` lines.
    assert "- **Name:** \n" in body
    assert "- **Pronouns:** \n" in body
    assert "- **Role:** \n" in body
    assert "- **Timezone:** \n" in body

    # Companion README explains the page's purpose.
    assert (vault / "wiki" / "identity" / "README.md").is_file()


def test_personal_init_seeds_every_ontology_folder(tmp_path: Path) -> None:
    """Every ontology in the closure seeds a ``wiki/<name>/README.md``."""

    vault = tmp_path / "v"

    assert main(["init", str(vault), "--recipe", RECIPE]) == 0

    for ontology in ("people", "food", "trips", "identity"):
        readme = vault / "wiki" / ontology / "README.md"
        assert readme.is_file(), f"missing wiki/{ontology}/README.md"


def test_personal_init_installs_operation_skills(tmp_path: Path) -> None:
    """The three personal operations each ship a SKILL.md under ``skills/``."""

    vault = tmp_path / "v"

    assert main(["init", str(vault), "--recipe", RECIPE]) == 0

    for operation in ("meal-planning", "trip-prep", "follow-up-tracker", "weekly-digest"):
        assert (vault / "skills" / operation / "SKILL.md").is_file()
