"""Contract-driven dispatch for ``wiki run`` (RFC-0001 Task 17) and
the opt-in headless executor (``wiki run --exec``, RFC-0003).

The kit ships no LLM (charter principle: library-not-application), so
``wiki run`` is not the actor that performs the operation. It is the
deterministic **dispatch boundary**: validate the user-supplied
arguments against the operation primitive's ``contract.yaml``, record
one ``OperationRunEvent`` in the journal, print a one-liner pointing
the user's Claude session at the corresponding SKILL.md, exit. The
SKILL is what reads vault pages, synthesises the digest, and writes
the result via ``safe_write``.

When invoked with ``--exec``, the kit additionally shells out to the
user-installed ``claude`` CLI in headless mode (ADR-0009), captures
the outcome in a per-failure file and an optional
``OperationExecFailedEvent``, and exits. The vault-side SKILL still
performs the operation work — the executor is a thin shim.

Behavior, edge cases, invariants, and acceptance tests live in
``docs/specs/task-17-wiki-run/spec.md`` (dispatch) and
``docs/specs/wiki-run-exec/spec.md`` (exec). Don't reason about this
module without reading those.

Module contract: two public functions, :func:`dispatch` and
:func:`dispatch_and_exec`. Inner helpers (``_parse_op_args``,
``_coerce_input``, ``_load_contract``, ``_resolve_operation_kind``,
``_locate_claude``, ``_locate_skill``, ``_walk_proposed_sidecars``,
``_validate_max_budget``, ``_build_prompt``, ``_build_argv``,
``_rotate_logs``, ``_run_subprocess``, ``_write_failure_file``,
``_append_failure_event``) are pure-ish and tested directly under
``tests/unit/test_run*.py``.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.journal import append_event, read_events, replay_state
from llm_wiki_kit.models import (
    OperationContract,
    OperationExecFailedEvent,
    OperationInputSpec,
    OperationRunEvent,
    Primitive,
    PrimitiveKind,
)
from llm_wiki_kit.primitives import discover_primitives, load_primitive
from llm_wiki_kit.write_helper import safe_write

# Hex pattern for ``dispatch_event_id`` shape validation. 12 lowercase
# hex chars — first 12 of ``uuid.uuid4().hex``. See
# ``docs/specs/wiki-run-exec/spec.md`` §"Event identity".
_EVENT_ID_RE = re.compile(r"^[0-9a-f]{12}$")

# Vehicle name recorded on every event the exec phase emits. The
# dispatch event itself stays ``wiki-run`` (RUN_VEHICLE below) so
# ``journal grep`` can distinguish the two phases.
EXEC_VEHICLE = "wiki-run-exec"

# Shape check for ``WIKI_EXEC_MAX_BUDGET_USD``. Digits optionally
# followed by a decimal point and more digits — anything else
# (whitespace, control chars, scientific notation, negative sign) is
# rejected at exec start so schedule-artifact templates stay safe.
# See spec §"Environment variables".
_MAX_BUDGET_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")

# Walk-scope unconditional excludes. Dot-prefixed directories are
# already excluded by the "no leading dot" Included rule; this list
# names the only non-dot-prefixed exclusion (the kit's own scratch).
# See spec §"Conflict-refusal walk scope".
_CONFLICT_WALK_NESTED_EXCLUDE = "inbox/scheduled-failures"

# Vehicle name recorded on every ``OperationRunEvent`` this module emits.
# Pinned by the spec — cross-task ``by`` values are how ``wiki doctor``
# and ``journal grep`` attribute actions to the actor that produced
# them.
RUN_VEHICLE = "wiki-run"

# Truthy / falsy spellings accepted for ``type: boolean``. Inlined here
# rather than imported from ``cli._WIKI_DEBUG_TRUTHY``: the two domains
# are independent (one is a CLI debug-mode toggle, the other is a
# contract-input vocabulary) and pulling them together would couple
# unrelated concerns.
_BOOL_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSY: frozenset[str] = frozenset({"0", "false", "no", "off"})

# ISO 8601 week-date format, bounded to legal week numbers (W01-W53).
# Calendar-validity (does year X actually have W53?) is intentionally
# out of scope — see spec §Non-goals.
_ISO_WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")


# ---------------------------------------------------------------------------
# Pure helpers — argument parsing
# ---------------------------------------------------------------------------


class ArgCoercionError(Exception):
    """Raised by :func:`_coerce_input` when a value fails type coercion.

    Internal to :mod:`llm_wiki_kit.run`; :func:`dispatch` catches it and
    translates to the user-facing one-line error message. Not a
    :class:`WikiError` because it doesn't ride the CLI boundary
    directly — every invocation that produces an ``ArgCoercionError``
    still journals an ``OperationRunEvent(status="invalid_args")``.
    """

    def __init__(self, value: str, expected: str) -> None:
        self.value = value
        self.expected = expected
        super().__init__(f"expected {expected}, got {value!r}")


def _normalise_name(name: str) -> str:
    """Lower-case + kebab→snake. Pinned by spec §Inputs."""

    return name.lower().replace("-", "_")


def _parse_op_args(tokens: list[str]) -> dict[str, str]:
    """Parse raw CLI tokens into a name→value dict.

    Each token must match ``--<name>=<value>`` or ``--<name>`` (the
    latter records the sentinel string ``"true"``). Names are
    normalised via :func:`_normalise_name` before becoming dict keys
    so kebab and snake spellings collapse onto the same field. Last
    write to a given name wins on value, but the name's iteration
    position is set by its first occurrence — see spec §Behavior
    step 7 §"Error-precedence rule".

    Raises :class:`WikiError` on shape failures (positional token,
    bare ``--``, empty-name ``--=value``). Shape failures abort the
    parse before any journal write so no event is recorded for a
    malformed argv.
    """

    result: dict[str, str] = {}
    for token in tokens:
        if not token.startswith("--"):
            raise WikiError(f"malformed argument: {token!r}: expected --name=value")
        body = token[2:]
        if not body:
            raise WikiError(f"malformed argument: {token!r}: expected --name=value")
        if "=" in body:
            name, _, value = body.partition("=")
        else:
            name = body
            value = "true"
        if not name:
            raise WikiError(f"malformed argument: {token!r}: empty name")
        result[_normalise_name(name)] = value
    return result


def _coerce_input(raw_value: str, spec: OperationInputSpec) -> object:
    """Coerce a raw user string against an :class:`OperationInputSpec`.

    Type tags handled: ``string``, ``integer``/``int``, ``boolean``,
    ``iso_week``, ``list``. Anything else falls through as ``str``
    — forward-compat for new tags (e.g. ``page`` in ``trip-prep``)
    before the kit grows dedicated coercions. Raises
    :class:`ArgCoercionError` on type-mismatch.
    """

    type_ = spec.type
    if type_ == "string":
        return raw_value
    if type_ in ("integer", "int"):
        try:
            return int(raw_value)
        except ValueError as exc:
            raise ArgCoercionError(raw_value, "integer") from exc
    if type_ == "boolean":
        lowered = raw_value.lower()
        if lowered in _BOOL_TRUTHY:
            return True
        if lowered in _BOOL_FALSY:
            return False
        raise ArgCoercionError(raw_value, "boolean (true/false/yes/no/1/0/on/off)")
    if type_ == "iso_week":
        if _ISO_WEEK_RE.fullmatch(raw_value):
            return raw_value
        raise ArgCoercionError(raw_value, "iso_week (YYYY-Www, W01-W53)")
    if type_ == "list":
        if raw_value == "":
            return []
        return [element.strip() for element in raw_value.split(",")]
    # Unknown type — accept verbatim. The SKILL is responsible for any
    # further validation (e.g. resolving a `type: page` wikilink).
    return raw_value


# ---------------------------------------------------------------------------
# Contract + kind resolution
# ---------------------------------------------------------------------------


def _operation_contract_path(operation: str, kit_root: Path) -> Path:
    return kit_root / "templates" / "operations" / operation / "contract.yaml"


def _load_contract(operation: str, kit_root: Path) -> OperationContract:
    """Read and validate ``templates/operations/<operation>/contract.yaml``.

    Raises :class:`WikiError` with the absolute path on a missing
    file (the kit-version-skew case — primitive is installed in the
    journal but its contract file disappeared from disk).
    """

    path = _operation_contract_path(operation, kit_root)
    if not path.is_file():
        raise WikiError(f"operation {operation!r}: no contract.yaml at {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return OperationContract.model_validate(payload)


def _resolve_operation_kind(
    operation: str,
    *,
    kit_root: Path,
    installed_primitive_names: set[str],
) -> None:
    """Verify ``operation`` is installed AND has kind ``operation``.

    Looks the primitive up in the full discovered catalog
    (``core`` + ``templates/``) so a content-type installed under
    the operation name gives a clean kind-mismatch error rather
    than a confusing missing-contract error. Raises
    :class:`WikiError` on either failure.
    """

    if operation not in installed_primitive_names:
        installed_sorted = ", ".join(sorted(installed_primitive_names)) or "(none)"
        raise WikiError(
            f"operation {operation!r} is not installed in this vault. "
            f"Installed primitives: {installed_sorted}"
        )

    core_dir = kit_root / "core"
    templates_dir = kit_root / "templates"
    catalog: list[Primitive] = []
    if core_dir.is_dir():
        catalog.append(load_primitive(core_dir))
    if templates_dir.is_dir():
        catalog.extend(discover_primitives(templates_dir))

    target = next((p for p in catalog if p.name == operation), None)
    if target is None:
        # In the journal but not on disk — kit-version skew.
        # _load_contract will raise the path-bearing error a moment
        # later; raise the same shape here so callers see one
        # consistent message.
        raise WikiError(
            f"operation {operation!r} is installed but not present in "
            f"the kit's catalog (searched {core_dir} and {templates_dir})"
        )
    if target.kind is not PrimitiveKind.OPERATION:
        raise WikiError(
            f"{operation!r} is installed but its kind is {target.kind.value!r}, not 'operation'"
        )


# ---------------------------------------------------------------------------
# DispatchResult
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    """In-memory return value from :func:`dispatch`.

    ``status`` is bounded to the two values the journal records;
    ``__post_init__`` enforces the spec §Invariants rule "error is
    non-None iff status==invalid_args". The kit's `--help`
    short-circuit lives at the CLI boundary, not here — dispatch
    only sees real invocations.

    ``dispatch_event_id`` carries the ``event_id`` the kit
    generated and journaled on the surviving ``OperationRunEvent``
    (see ``docs/specs/wiki-run-exec/spec.md`` §"DispatchResult
    extension"). Required field, ordered before defaulted fields
    so Python's ``@dataclass`` field-ordering rule holds.
    ``__post_init__`` validates the shape (12 lowercase hex).
    """

    status: Literal["dispatched", "invalid_args"]
    operation: str
    parsed: dict[str, object]
    args_raw: dict[str, str]
    period: str | None
    skill: str
    dispatch_event_id: str
    error: str | None = None
    produced_pages: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status == "invalid_args" and self.error is None:
            raise ValueError("DispatchResult: status='invalid_args' requires a non-None error")
        if self.status == "dispatched" and self.error is not None:
            raise ValueError("DispatchResult: status='dispatched' must have error=None")
        if not _EVENT_ID_RE.fullmatch(self.dispatch_event_id):
            raise ValueError(
                "DispatchResult: dispatch_event_id must be 12 lowercase hex chars; "
                f"got {self.dispatch_event_id!r}"
            )


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def _skill_or_default(contract: OperationContract) -> str:
    """Return the skill the dispatch line names. Empty/absent → operation."""

    if contract.skill:
        return contract.skill
    return contract.name


def dispatch(
    operation: str,
    raw_args: list[str],
    *,
    vault_root: Path,
    kit_root: Path,
    journal_path: Path,
    now: datetime,
) -> DispatchResult:
    """Validate args against the operation contract and journal the run.

    See ``docs/specs/task-17-wiki-run/spec.md`` §Behavior for the
    canonical sequence. Pre-load failures (vault check, unknown
    operation, kind mismatch, missing contract.yaml) raise
    :class:`WikiError` and do **not** journal. Post-load failures
    (unknown argument name, type-coercion failure) produce one
    ``OperationRunEvent(status="invalid_args")`` and a
    ``DispatchResult`` carrying the error message.

    Vault root is accepted for symmetry with other dispatch-shaped
    handlers (``wiki ingest`` etc.); the function does not currently
    read anything under it beyond what ``journal_path`` covers, but
    naming it explicitly keeps the seam available for a future
    pre-flight check (e.g. validating that produced-page paths fit
    inside the vault).

    **Installed-state staleness is intentional.** The
    ``read_events`` → ``_resolve_operation_kind`` → ``append_event``
    sequence is *not* wrapped in a ``transaction()``. A concurrent
    ``wiki upgrade`` or ``wiki remove`` could in principle land
    between the installed-check and the journal append, leaving us
    journaling a dispatch for a primitive that's just been
    uninstalled. This is by design: ``wiki run`` is read-only on the
    installed catalog, and the journal-append-only model
    (ADR-0002) already accepts this kind of state staleness across
    every command. The single append itself is locked via
    ``append_event``'s ``flock``; that's the only synchronization
    point.
    """

    # vault_root is reserved for future pre-flight checks; reference
    # it once so static analysis can't flag the unused parameter.
    _ = vault_root

    events = list(read_events(journal_path))
    state = replay_state(events)

    _resolve_operation_kind(
        operation,
        kit_root=kit_root,
        installed_primitive_names=set(state.installed_primitives),
    )

    contract = _load_contract(operation, kit_root)
    skill = _skill_or_default(contract)

    raw = _parse_op_args(raw_args)

    # Walk in dict iteration order — first-occurrence position of each
    # name in the user's typed tokens. First failure wins.
    parsed: dict[str, object] = {}
    error: str | None = None
    for name, value in raw.items():
        spec = contract.inputs.get(name)
        if spec is None:
            error = f"--{name}: unknown argument"
            break
        try:
            parsed[name] = _coerce_input(value, spec)
        except ArgCoercionError as exc:
            error = f"--{name}: {exc}"
            break

    if error is None:
        # Apply defaults for any field the user didn't supply.
        for field_name, spec in contract.inputs.items():
            if field_name in parsed:
                continue
            if spec.default is not None:
                parsed[field_name] = spec.default

    status: Literal["dispatched", "invalid_args"] = (
        "invalid_args" if error is not None else "dispatched"
    )

    event_id = uuid.uuid4().hex[:12]

    append_event(
        journal_path,
        OperationRunEvent(
            timestamp=now,
            by=RUN_VEHICLE,
            operation=operation,
            status=status,
            period=contract.period,
            produced_pages=[],
            args=raw,
            error=error,
            event_id=event_id,
        ),
    )

    return DispatchResult(
        status=status,
        operation=operation,
        parsed=parsed,
        args_raw=raw,
        period=contract.period,
        skill=skill,
        dispatch_event_id=event_id,
        error=error,
    )


# ---------------------------------------------------------------------------
# wiki-run-exec helpers (RFC-0003 + ADR-0009 + ADR-0010)
# ---------------------------------------------------------------------------


def _locate_claude(*, override: Path | None) -> Path | None:
    """Resolve the ``claude`` binary path.

    Resolution order pinned by spec §Inputs:

    1. ``override`` (from ``--claude-binary``).
    2. ``WIKI_CLAUDE_BINARY`` environment variable.
    3. ``shutil.which("claude")``.
    4. ``None`` — caller raises ``WikiError``.

    Each path is sanity-checked for existence + executability. A
    set-but-invalid value (override or env var) raises immediately —
    no silent fall-through to the next step, because the user
    explicitly named that path.
    """

    if override is not None:
        if not override.is_file() or not os.access(override, os.X_OK):
            raise WikiError(f"--claude-binary {str(override)!r}: not an executable file")
        return override
    env_value = os.environ.get("WIKI_CLAUDE_BINARY")
    if env_value:
        env_path = Path(env_value)
        if not env_path.is_file() or not os.access(env_path, os.X_OK):
            raise WikiError(f"WIKI_CLAUDE_BINARY={env_value!r}: not an executable file")
        return env_path
    which_result = shutil.which("claude")
    if which_result:
        return Path(which_result)
    return None


def _locate_skill(
    *,
    skill_path_override: Path | None,
    contract: OperationContract,
    vault_root: Path,
) -> Path:
    """Resolve the SKILL.md the executor should pass to ``claude``.

    Explicit ``--skill-path`` wins. Otherwise the kit constructs
    ``<vault_root>/.claude/skills/<contract.skill or operation>/SKILL.md``
    — matching task-17 CT-13's "skill name falls back to operation
    name" rule. A missing file at the resolved path raises
    ``WikiError``; the dispatch event is already journaled by the
    time this helper runs, so the user can re-attempt manually.
    """

    if skill_path_override is not None:
        path = skill_path_override
        if not path.is_absolute():
            path = (vault_root / path).resolve()
        if not path.is_file():
            raise WikiError(f"--skill-path {str(path)!r}: SKILL file not found")
        return path
    skill_name = contract.skill or contract.name
    default = vault_root / ".claude" / "skills" / skill_name / "SKILL.md"
    if not default.is_file():
        raise WikiError(
            f"SKILL file not found at {str(default)!r}; create it or pass --skill-path <path>"
        )
    return default


def _read_obsidianignore(vault_root: Path) -> tuple[str, ...]:
    """Read ``.obsidianignore`` as exact-prefix lines.

    Subset of Obsidian's grammar pinned by spec §"Conflict-refusal
    walk scope": one path-prefix per line, blank lines and
    ``#``-comments dropped, no negation. Returns vault-relative
    prefixes (POSIX form) suitable for ``str.startswith`` matching.
    """

    ignore_file = vault_root / ".obsidianignore"
    if not ignore_file.is_file():
        return ()
    prefixes: list[str] = []
    for raw in ignore_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # The kit emits one ignore pattern: the literal `\.proposed$`
        # regex (per write_helper.OBSIDIAN_IGNORE_PROPOSED_PATTERN).
        # Skip it here — that's the kit's own bookkeeping, not user
        # content to suppress from the walk.
        if line.startswith("\\") or line.endswith("$"):
            continue
        prefixes.append(line)
    return tuple(prefixes)


@dataclass(frozen=True)
class _SidecarWalk:
    """Result of a conflict-refusal walk.

    ``paths`` holds up to 20 vault-relative POSIX paths in sorted
    order. ``total`` counts every sidecar discovered in scope,
    including those beyond the 20-path cap, so the failure-file can
    render the spec's ``(…N more)`` indicator. ``over_cap`` is a
    convenience derived from ``total > len(paths)``.
    """

    paths: list[str]
    total: int

    @property
    def over_cap(self) -> bool:
        return self.total > len(self.paths)


def _obsidianignore_matches(rel: str, ignore_prefixes: tuple[str, ...]) -> bool:
    """Match a vault-relative POSIX path against ``.obsidianignore`` entries.

    Spec §"Conflict-refusal walk scope": "exact-prefix matching
    against vault-relative paths, no negation." Concretely: a path
    is ignored when an entry equals it (with or without a trailing
    ``/``) or names a directory that contains it. Prefix tokens
    that don't end on a path-segment boundary do **not** match a
    deeper segment — ``att`` does not match ``attachments/x.md``.
    """

    for raw in ignore_prefixes:
        token = raw.rstrip("/")
        if rel == token or rel.startswith(token + "/"):
            return True
    return False


def _walk_proposed_sidecars(vault_root: Path) -> _SidecarWalk:
    """Walk the vault for unresolved ``.proposed`` sidecars in scope.

    Scope per spec §"Conflict-refusal walk scope":

    - **Included** — every direct child of ``vault_root`` whose name
      does not start with ``.`` (already excludes ``.wiki.journal/``,
      ``.git/``, ``.obsidian/``, ``.claude/``).
    - **Nested exclusion** — ``inbox/scheduled-failures/`` (the kit's
      own scratch; preventing a refusal-loop on failures the kit
      itself authored).
    - **`.obsidianignore` excludes** — path-segment-boundary match
      against the vault-relative path (``att`` does not match
      ``attachments/foo``; ``attachments/`` does).

    Returns up to 20 vault-relative POSIX paths plus a ``total``
    count of every in-scope sidecar (so the failure file can render
    ``(…N more)`` when ``total > 20``). The 20-path cap is the
    single bound per spec; counting past the cap is cheap because
    the walk keeps iterating the same generator. Ordering inside
    each top-level subtree is ``Path.rglob`` insertion order
    sorted lexicographically.
    """

    paths: list[str] = []
    total = 0
    ignore_prefixes = _read_obsidianignore(vault_root)
    if not vault_root.is_dir():
        return _SidecarWalk(paths=paths, total=total)

    def consume(rel: str) -> None:
        nonlocal total
        nested = _CONFLICT_WALK_NESTED_EXCLUDE
        if rel == nested or rel.startswith(nested + "/"):
            return
        if _obsidianignore_matches(rel, ignore_prefixes):
            return
        total += 1
        if len(paths) < 20:
            paths.append(rel)

    # Resolve once so symlink-escape rejection (below) compares
    # canonical paths.
    resolved_root = vault_root.resolve()
    for child in sorted(vault_root.iterdir()):
        if child.name.startswith("."):
            continue
        # Skip symlinks at the top level — a symlinked subtree could
        # cause the walk to descend system directories (security
        # review concern). Same posture for symlinks discovered
        # deeper: ``os.walk(..., followlinks=False)`` is the canon.
        if child.is_symlink():
            continue
        if child.is_file():
            if child.name.endswith(".proposed"):
                consume(child.name)
            continue
        if not child.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(child, followlinks=False, onerror=None):
            # Sort dirnames so the walk is deterministic; this also
            # lets the consumer rely on lexicographic ordering.
            dirnames.sort()
            for name in sorted(filenames):
                if not name.endswith(".proposed"):
                    continue
                path = Path(dirpath) / name
                # Defence in depth — even with followlinks=False,
                # the file itself might be a symlink whose target
                # lives outside the vault. Reject those.
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                try:
                    resolved.relative_to(resolved_root)
                except ValueError:
                    continue
                rel = path.relative_to(vault_root).as_posix()
                consume(rel)
    return _SidecarWalk(paths=paths, total=total)


def _validate_max_budget(value: str | None) -> str | None:
    """Validate ``WIKI_EXEC_MAX_BUDGET_USD`` shape.

    Returns the value unchanged when it matches
    ``^[0-9]+(\\.[0-9]+)?$``; returns ``None`` when the input is
    ``None`` or empty. Anything else raises ``WikiError`` so the kit
    never templates an unsafe string into a schedule artifact.
    See spec §"Environment variables".
    """

    if value is None or value == "":
        return None
    if not _MAX_BUDGET_RE.fullmatch(value):
        raise WikiError(f"WIKI_EXEC_MAX_BUDGET_USD must match ^[0-9]+(\\.[0-9]+)?$; got {value!r}")
    return value


def _build_prompt(
    *,
    operation: str,
    skill_path: Path,
    dispatch_event_id: str,
) -> str:
    """Render the trailing positional argument of the ADR-0009 argv.

    Pinned content: operation name, SKILL path, dispatch event id.
    Per ADR-0009 §"What this ADR does not cover", the prompt body
    is *not* part of any kit contract — the SKILL is free to evolve
    its own conventions. CT-13 only asserts that
    ``dispatch_event_id`` appears as a substring.

    ``parsed_args`` is deliberately NOT rendered into the prompt —
    the SKILL reads them from the journaled
    ``OperationRunEvent.args`` by ``event_id`` (spec §Happy path
    step 4).
    """

    return (
        f"Run the `{operation}` skill against this vault. "
        f"The SKILL.md is at {skill_path}. "
        f"The dispatch event id for this run is {dispatch_event_id}. "
        f"Read the operation's args from the journal entry with this id. "
        f"On completion, write produced pages via the kit's standard "
        f"helpers and exit."
    )


def _build_argv(
    *,
    claude_binary: Path,
    vault_root: Path,
    prompt: str,
    max_budget_usd: str | None,
) -> list[str]:
    """Build the headless ``claude -p`` argv pinned by ADR-0009.

    Shape:

    ``[<binary>, "-p", "--add-dir", <vault>,
       "--permission-mode", "dontAsk",
       "--output-format", "json",
       [\"--max-budget-usd\", <cap>],   # only when set
       <prompt>]``

    ``--agent <name>`` is **not** emitted at v1 (ADR-0010's
    resolution chain depends on RFC-0004, which has not landed).
    """

    argv: list[str] = [
        str(claude_binary),
        "-p",
        "--add-dir",
        str(vault_root),
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "json",
    ]
    if max_budget_usd is not None:
        argv.extend(["--max-budget-usd", max_budget_usd])
    argv.append(prompt)
    return argv


def _exec_log_dir(vault_root: Path) -> Path:
    """Return the directory exec logs live under, creating it if needed."""

    log_dir = vault_root / ".wiki.journal" / "exec-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _rotate_logs(*, vault_root: Path, retention_days: int, now: datetime) -> None:
    """Delete ``exec-logs/*.log`` older than ``retention_days``.

    Best-effort: per-file ``OSError``s (permission denied, file
    vanished mid-walk) are swallowed silently — the spec calls this
    cache housekeeping, not a state change.
    ``retention_days <= 0`` disables rotation entirely and
    short-circuits before enumerating the directory; the CLI
    rejects negative values upstream, so this is the
    ``retention_days == 0`` user-facing case the spec
    §"Environment variables" blesses. The ``< 0`` branch is
    defence-in-depth — callers from outside the CLI seam can
    still hit it.
    """

    if retention_days <= 0:
        return
    log_dir = vault_root / ".wiki.journal" / "exec-logs"
    if not log_dir.is_dir():
        return
    cutoff = (now - timedelta(days=retention_days)).timestamp()
    for path in log_dir.iterdir():
        if not path.is_file():
            continue
        if not path.name.endswith(".log"):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


@dataclass
class _SubprocessResult:
    """In-memory return value from ``_run_subprocess``.

    ``stderr_tail`` is the last 4 KB of stderr, lossy-decoded.
    ``timed_out`` flips true when ``WIKI_EXEC_TIMEOUT`` fired (the
    exec failure event carries ``reason="timeout"`` in that case).
    """

    returncode: int
    stderr_tail: str
    timed_out: bool


_STDERR_TAIL_CAP_BYTES = 4096


def _run_subprocess(
    *,
    argv: list[str],
    cwd: Path,
    log_path: Path,
    timeout_seconds: int,
) -> _SubprocessResult:
    """Run ``claude`` and capture stdout+stderr to ``log_path``.

    stdout streams into ``log_path`` (truncate-mode, bytes). stderr
    is captured separately via ``communicate`` so the failure event
    can carry the last 4 KB as ``stderr_tail``; after the process
    exits the kit appends the captured stderr to the log so the
    log file holds the full union. On timeout
    (``WIKI_EXEC_TIMEOUT`` seconds): SIGTERM, wait 5 seconds for a
    graceful exit, then SIGKILL.
    """

    log_path.parent.mkdir(parents=True, exist_ok=True)
    timed_out = False
    stderr_bytes: bytes = b""
    with log_path.open("wb") as log_fh:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=log_fh,
            stderr=subprocess.PIPE,
        )
        try:
            _, stderr_bytes = proc.communicate(timeout=max(timeout_seconds, 0))
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.send_signal(signal.SIGTERM)
            try:
                _, stderr_bytes = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr_bytes = proc.communicate()
        if stderr_bytes:
            log_fh.write(stderr_bytes)

    returncode = -2 if timed_out else proc.returncode
    tail = stderr_bytes[-_STDERR_TAIL_CAP_BYTES:] if stderr_bytes else b""
    return _SubprocessResult(
        returncode=returncode,
        stderr_tail=tail.decode("utf-8", errors="replace"),
        timed_out=timed_out,
    )


def _append_failure_event(
    *,
    journal_path: Path,
    now: datetime,
    operation: str,
    dispatch_event_id: str,
    exit_code: int,
    reason: str,
    stderr_tail: str = "",
    log_path: str | None = None,
    conflict_sidecars: list[str] | None = None,
) -> OperationExecFailedEvent:
    """Append one ``OperationExecFailedEvent``. Internal v1 guard.

    Spec §"Contracts with other modules": ``binary-missing`` and
    ``skill-missing`` are reserved-but-not-emitted at v1.
    ``RuntimeError`` (not ``assert``) so the check survives
    ``python -O``; not ``WikiError`` because reaching this branch is
    an internal-invariant violation, not a user-actionable error.
    """

    if reason in ("binary-missing", "skill-missing"):
        raise RuntimeError(
            f"v1: reason {reason!r} is reserved; no emit path should reach this branch"
        )
    event = OperationExecFailedEvent(
        timestamp=now,
        by=EXEC_VEHICLE,
        operation=operation,
        dispatch_event_id=dispatch_event_id,
        exit_code=exit_code,
        reason=reason,  # type: ignore[arg-type]
        stderr_tail=stderr_tail,
        log_path=log_path,
        conflict_sidecars=conflict_sidecars or [],
    )
    append_event(journal_path, event)
    return event


def _render_failure_file(
    *,
    operation: str,
    dispatch_event_id: str,
    dispatched_at: datetime,
    failed_at: datetime,
    reason: str,
    exit_code: int,
    stderr_tail: str,
    log_path: str | None,
    conflict_sidecars: list[str],
    extra_sidecars: int = 0,
) -> str:
    """Render the per-failure markdown body.

    Two templates by ``reason`` — pinned by spec §"Per-failure file
    format". ``non-zero-exit`` / ``timeout`` carry log link +
    duration + last stderr line; ``conflict-refused`` carries the
    sidecar bullet list and omits log/duration.
    """

    if reason == "conflict-refused":
        lines = [
            "# Scheduled exec refused: unresolved conflicts",
            "",
            f"- **Operation:** {operation}",
            f"- **Dispatched:** {dispatched_at.isoformat()} (dispatch event {dispatch_event_id})",
            f"- **Refused:** {failed_at.isoformat()} (dispatch event {dispatch_event_id})",
            "- **Reason:** conflict-refused — `.proposed` sidecars present in scope.",
            "- **Sidecars found:**",
        ]
        if conflict_sidecars:
            for sidecar in conflict_sidecars:
                lines.append(f"  - `{sidecar}`")
        else:
            lines.append("  - (none recorded)")
        # Spec §"Conflict-refusal walk scope": if more than 20 sidecars
        # exist, render `(…N more)` after the 20th. ``extra_sidecars``
        # carries the overflow count from the caller; 0 means no
        # overflow.
        if extra_sidecars > 0:
            lines.append(f"  - (…{extra_sidecars} more)")
        lines.extend(
            [
                "",
                "Resolve each sidecar via the `wiki-conflict` SKILL (or delete",
                "manually), then delete this file. The next scheduled run will",
                "proceed.",
                "",
            ]
        )
        return "\n".join(lines)

    duration_s = (failed_at - dispatched_at).total_seconds()
    last_stderr = ""
    for candidate in reversed(stderr_tail.splitlines()):
        if candidate.strip():
            last_stderr = candidate
            break
    log_line = (
        f"- **Log:** [`{log_path}`](../../{log_path})"
        if log_path is not None
        else "- **Log:** (none — subprocess did not spawn)"
    )
    return "\n".join(
        [
            "# Scheduled exec failure",
            "",
            f"- **Operation:** {operation}",
            f"- **Dispatched:** {dispatched_at.isoformat()} (dispatch event {dispatch_event_id})",
            f"- **Failed:** {failed_at.isoformat()} (dispatch event {dispatch_event_id})",
            f"- **Reason:** {reason} (exit {exit_code}, duration {duration_s:.0f}s)",
            log_line,
            (
                f"- **Last non-empty stderr line:** `{last_stderr}`"
                if last_stderr
                else "- **Last non-empty stderr line:** (none)"
            ),
            "",
            "Resolve by reading the log, fixing the underlying cause, and either",
            f"deleting this file or running the operation manually (`wiki run {operation}`).",
            "The next scheduled run fires normally regardless of whether this file is removed.",
            "",
        ]
    )


def _write_failure_file(
    *,
    vault_root: Path,
    journal_path: Path,
    dispatch_event_id: str,
    body: str,
) -> None:
    """Write the per-failure file via ``safe_write`` (in-vault).

    Lives at ``inbox/scheduled-failures/<event_id>.md``. The
    dispatch event id is single-use, so the file is new and
    ``safe_write``'s drift-detection has nothing to check; the write
    journals a ``PageWriteEvent``.
    """

    _ = journal_path  # safe_write derives it from vault_root via its own path resolution
    relative = Path("inbox") / "scheduled-failures" / f"{dispatch_event_id}.md"
    safe_write(
        path=relative,
        content=body,
        by=EXEC_VEHICLE,
        journal_path=vault_root / ".wiki.journal" / "journal.jsonl",
    )


def _write_failure_file_safe(
    *,
    vault_root: Path,
    journal_path: Path,
    dispatch_event_id: str,
    body: str,
) -> None:
    """Best-effort wrapper around ``_write_failure_file``.

    Spec §"Error cases" lines 376-381: if the per-failure write
    fails (disk full, permission denied, write_helper drift on the
    failure path itself), the kit must still journal the
    ``OperationExecFailedEvent`` so the operator has a journal
    record of the exec attempt. We swallow the inner exception
    here, write a one-line warning to stderr, and let the orchestrator
    proceed to ``_append_failure_event``.
    """

    try:
        _write_failure_file(
            vault_root=vault_root,
            journal_path=journal_path,
            dispatch_event_id=dispatch_event_id,
            body=body,
        )
    except (OSError, WikiError) as exc:
        import sys as _sys

        print(
            f"wiki-run-exec: per-failure file write failed: {exc!r}; "
            "failure event will still be journaled.",
            file=_sys.stderr,
        )


# ---------------------------------------------------------------------------
# dispatch_and_exec orchestrator
# ---------------------------------------------------------------------------


@dataclass
class ExecResult:
    """Return value from :func:`dispatch_and_exec`.

    Wraps the inner :class:`DispatchResult` and adds an ``exec_status``
    discriminator naming the exec phase outcome. ``exit_code`` /
    ``duration_seconds`` / ``log_path`` are set on the
    ``succeeded`` / ``failed_exit`` / ``failed_timeout`` paths so
    the CLI can render the success/failure line per spec
    §"Happy path" step 5. ``conflict-refused`` and ``skipped`` paths
    leave them as defaults.
    """

    dispatch: DispatchResult
    exec_status: Literal[
        "skipped",
        "succeeded",
        "failed_conflict",
        "failed_exit",
        "failed_timeout",
    ]
    exit_code: int | None = None
    duration_seconds: float | None = None
    log_path: str | None = None


def _utc_now() -> datetime:
    """Default failure-clock — module-level so tests can monkeypatch
    ``llm_wiki_kit.run._utc_now`` to inject a deterministic value
    without touching the orchestrator's signature.
    """

    return datetime.now(UTC)


def dispatch_and_exec(
    operation: str,
    raw_args: list[str],
    *,
    vault_root: Path,
    kit_root: Path,
    journal_path: Path,
    now: datetime,
    claude_binary: Path | None = None,
    skill_path_override: Path | None = None,
    timeout_seconds: int = 1800,
    log_retention_days: int = 30,
    max_budget_usd: str | None = None,
    failure_clock: Callable[[], datetime] | None = None,
) -> ExecResult:
    """Orchestrate ``wiki run --exec``.

    Sequence per spec §Behavior:

    1. Run :func:`dispatch` (journals one ``OperationRunEvent`` with
       a fresh ``event_id``). If status is ``invalid_args``, return
       ``exec_status="skipped"`` — no subprocess, no failure event.
    2. Walk for ``.proposed`` sidecars. If any: write the per-failure
       file, append a ``conflict-refused`` exec event, return
       ``exec_status="failed_conflict"`` — **regardless** of whether
       the binary/SKILL/budget would have resolved. The spec puts
       conflict refusal ahead of binary discovery so a vault in
       conflict surfaces cleanly even when ``claude`` is missing.
    3. Resolve the Claude binary (override > env var > shutil.which).
       Missing → ``WikiError``; dispatch event remains journaled,
       no failure event.
    4. Re-load the contract; resolve the SKILL path. Missing →
       ``WikiError``.
    5. Validate ``max_budget_usd`` shape if supplied. Malformed →
       ``WikiError``.
    6. Rotate old logs; build prompt + argv; run subprocess.
    7. On exit 0 → ``exec_status="succeeded"``; no second journal
       event (CT-14).
    8. On non-zero exit / timeout → write per-failure file, append
       ``non-zero-exit`` / ``timeout`` exec event, return
       ``exec_status="failed_exit"`` / ``"failed_timeout"``.

    ``now`` is the dispatch timestamp (used by ``dispatch()`` and
    by the exec event's journaled ``timestamp``). ``failure_clock``
    is called at failure-render time to produce the per-failure
    file's ``Failed:`` timestamp and the duration calculation —
    defaults to ``_utc_now``; tests inject a fixed
    ``lambda: now + timedelta(seconds=K)`` to make duration
    deterministic.
    """

    clock = failure_clock if failure_clock is not None else _utc_now

    dispatch_result = dispatch(
        operation,
        raw_args,
        vault_root=vault_root,
        kit_root=kit_root,
        journal_path=journal_path,
        now=now,
    )

    if dispatch_result.status != "dispatched":
        return ExecResult(dispatch=dispatch_result, exec_status="skipped")

    # Conflict-refusal walk runs *before* binary/SKILL/budget resolution
    # so a vault in conflict surfaces with a refusal event even when
    # claude is missing. Spec §"What this is" lists the order:
    # refuse-on-sidecars → locate binary → invoke.
    walk = _walk_proposed_sidecars(vault_root)
    if walk.paths:
        failed_at = clock()
        extra = max(walk.total - len(walk.paths), 0)
        body = _render_failure_file(
            operation=operation,
            dispatch_event_id=dispatch_result.dispatch_event_id,
            dispatched_at=now,
            failed_at=failed_at,
            reason="conflict-refused",
            exit_code=-1,
            stderr_tail="",
            log_path=None,
            conflict_sidecars=walk.paths,
            extra_sidecars=extra,
        )
        _write_failure_file_safe(
            vault_root=vault_root,
            journal_path=journal_path,
            dispatch_event_id=dispatch_result.dispatch_event_id,
            body=body,
        )
        _append_failure_event(
            journal_path=journal_path,
            now=failed_at,
            operation=operation,
            dispatch_event_id=dispatch_result.dispatch_event_id,
            exit_code=-1,
            reason="conflict-refused",
            stderr_tail="",
            log_path=None,
            conflict_sidecars=walk.paths,
        )
        return ExecResult(dispatch=dispatch_result, exec_status="failed_conflict")

    # No conflicts — resolve binary, SKILL, budget. All three raise
    # WikiError on failure; none journal an exec event (the dispatch
    # event is already durable).
    binary = _locate_claude(override=claude_binary)
    if binary is None:
        raise WikiError(
            "--exec set but no claude binary found; install Claude Code or "
            "pass --claude-binary <path>"
        )
    contract = _load_contract(operation, kit_root)
    skill_path = _locate_skill(
        skill_path_override=skill_path_override,
        contract=contract,
        vault_root=vault_root,
    )
    validated_budget = _validate_max_budget(max_budget_usd)

    # Happy path — rotate logs, build prompt + argv, run subprocess.
    _rotate_logs(vault_root=vault_root, retention_days=log_retention_days, now=now)
    log_dir = _exec_log_dir(vault_root)
    log_path = log_dir / f"{dispatch_result.dispatch_event_id}.log"
    prompt = _build_prompt(
        operation=operation,
        skill_path=skill_path,
        dispatch_event_id=dispatch_result.dispatch_event_id,
    )
    argv = _build_argv(
        claude_binary=binary,
        vault_root=vault_root,
        prompt=prompt,
        max_budget_usd=validated_budget,
    )
    sp_result = _run_subprocess(
        argv=argv,
        cwd=vault_root,
        log_path=log_path,
        timeout_seconds=timeout_seconds,
    )

    log_path_relative = log_path.relative_to(vault_root).as_posix()
    finished_at = clock()
    duration_s = max((finished_at - now).total_seconds(), 0.0)

    if sp_result.returncode == 0:
        # Success — no second journal event per CT-14.
        return ExecResult(
            dispatch=dispatch_result,
            exec_status="succeeded",
            exit_code=0,
            duration_seconds=duration_s,
            log_path=log_path_relative,
        )

    # Non-zero exit or timeout — journal the failure event + write
    # the per-failure file.
    failed_at = finished_at
    reason: Literal["non-zero-exit", "timeout"] = (
        "timeout" if sp_result.timed_out else "non-zero-exit"
    )
    body = _render_failure_file(
        operation=operation,
        dispatch_event_id=dispatch_result.dispatch_event_id,
        dispatched_at=now,
        failed_at=failed_at,
        reason=reason,
        exit_code=sp_result.returncode,
        stderr_tail=sp_result.stderr_tail,
        log_path=log_path_relative,
        conflict_sidecars=[],
    )
    _write_failure_file_safe(
        vault_root=vault_root,
        journal_path=journal_path,
        dispatch_event_id=dispatch_result.dispatch_event_id,
        body=body,
    )
    _append_failure_event(
        journal_path=journal_path,
        now=failed_at,
        operation=operation,
        dispatch_event_id=dispatch_result.dispatch_event_id,
        exit_code=sp_result.returncode,
        reason=reason,
        stderr_tail=sp_result.stderr_tail,
        log_path=log_path_relative,
    )
    exec_status: Literal["failed_exit", "failed_timeout"] = (
        "failed_timeout" if sp_result.timed_out else "failed_exit"
    )
    return ExecResult(
        dispatch=dispatch_result,
        exec_status=exec_status,
        exit_code=sp_result.returncode,
        duration_seconds=duration_s,
        log_path=log_path_relative,
    )
