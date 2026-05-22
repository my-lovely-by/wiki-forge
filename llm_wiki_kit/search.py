"""Read-only vault search shelling out to ripgrep.

Tier 1 of the two-tier search the vault-side `wiki-search` SKILL.md
describes. The kit invokes ``rg --json --fixed-strings`` over
``<vault_root>/wiki/``, parses the per-file ``type: "end"`` records for
``(path, match_count)``, reads each match's YAML frontmatter so the
``--type`` / ``--tag`` / ``--status`` filters can drop pages that don't
qualify, and renders the survivors as a markdown ranked list. No
journal interaction, no writes. See ``docs/specs/wiki-search/spec.md``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from llm_wiki_kit.errors import WikiError

_RG_INSTALL_HINT = (
    "ripgrep (rg) not found on PATH. Install via your OS package manager "
    "(e.g. 'brew install ripgrep', 'apt install ripgrep'). "
    "See https://github.com/BurntSushi/ripgrep#installation."
)

# Defense-in-depth wall clock for the ripgrep subprocess. A wedged
# scan (stuck NFS mount under ``wiki/``, an OS-level read stall) would
# otherwise hang the user-facing CLI indefinitely; 60s is well past any
# legitimate scan of even very large vaults.
_RG_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class SearchFilters:
    """Optional frontmatter filters applied after ripgrep returns paths."""

    type: str | None = None
    tag: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class SearchHit:
    """One ranked search result.

    Ordering is ``(-match_count, path)`` — sorting in Python uses
    ``sorted(hits, key=lambda h: (-h.match_count, h.path))`` rather than
    leaning on a ``__lt__`` here, so the dataclass stays a plain record.
    """

    path: str
    title: str
    type: str
    status: str
    tags: list[str] = field(default_factory=list)
    match_count: int = 0


def run_search(
    vault_root: Path,
    query: str,
    filters: SearchFilters,
    top: int,
) -> list[SearchHit]:
    """Search ``<vault_root>/wiki/`` for ``query`` and return ranked hits.

    Raises ``WikiError`` when ``rg`` is missing from ``PATH`` or exits
    with a non-search-related failure. An empty ``wiki/`` tree (or one
    with zero matches) returns ``[]`` — "no result" is a signal, not
    an error, per the SKILL.md.
    """

    rg = shutil.which("rg")
    if rg is None:
        raise WikiError(_RG_INSTALL_HINT)

    wiki_dir = vault_root / "wiki"
    if not wiki_dir.is_dir():
        return []

    # ``--no-ignore --hidden`` keep the scan exhaustive over the vault:
    # the journal is the authoritative ledger of what belongs, so a
    # ``.gitignore`` under ``wiki/`` or a draft folder whose name starts
    # with a dot must not silently hide pages. ``--no-messages`` mutes
    # ripgrep's permission-denied / symlink-loop chatter so the spec's
    # "stderr — boundary errors only" output contract holds.
    try:
        completed = subprocess.run(
            [
                rg,
                "--json",
                "--fixed-strings",
                "--no-ignore",
                "--hidden",
                "--no-messages",
                "--",
                query,
                "wiki",
            ],
            cwd=vault_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=_RG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise WikiError(
            f"ripgrep search exceeded {_RG_TIMEOUT_SECONDS}s; "
            "the vault may be on a slow or unresponsive filesystem."
        ) from exc

    # Exit 0 = matches; 1 = no matches (still emits a valid empty
    # summary line); 2+ = real failure (bad path, permission, etc.).
    if completed.returncode >= 2:
        stderr = completed.stderr.strip() or "ripgrep failed with no stderr"
        raise WikiError(f"ripgrep failed (exit {completed.returncode}): {stderr}")

    counts = _parse_match_counts(completed.stdout)
    if not counts:
        return []

    hits: list[SearchHit] = []
    for rel_path, match_count in counts.items():
        abs_path = vault_root / rel_path
        title, fm = _read_page_metadata(abs_path)
        if not _filters_match(fm, filters):
            continue
        hits.append(
            SearchHit(
                path=rel_path,
                title=title or abs_path.stem,
                type=str(fm.get("type", "") or ""),
                status=str(fm.get("status", "") or ""),
                tags=_coerce_tags(fm.get("tags")),
                match_count=match_count,
            )
        )

    hits.sort(key=lambda h: (-h.match_count, h.path))
    return hits[:top]


def format_results(hits: list[SearchHit]) -> str:
    """Render hits as the markdown block format the SKILL.md expects.

    ``no matches.\\n`` for an empty list; one block per hit otherwise,
    blocks separated by a blank line, output terminated by a single
    trailing newline.
    """

    if not hits:
        return "no matches.\n"

    blocks: list[str] = []
    for hit in hits:
        block = (
            f"## {hit.title} — {hit.path}\n"
            f"- type: {hit.type}\n"
            f"- status: {hit.status}\n"
            f"- tags: {', '.join(hit.tags)}\n"
            f"- matches: {hit.match_count}\n"
        )
        blocks.append(block)
    return "\n".join(blocks)


def _parse_match_counts(stdout: str) -> dict[str, int]:
    """Extract ``{relative_path: match_count}`` from ``rg --json`` output.

    Only ``type: "end"`` records carry the per-file aggregate (under
    ``data.stats.matches``). A malformed JSON line is skipped — ripgrep's
    own output is well-formed in normal use, but a partial flush on a
    SIGPIPE could in principle truncate the last line.
    """

    counts: dict[str, int] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("type") != "end":
            continue
        data = record.get("data") or {}
        path_obj = data.get("path") or {}
        stats = data.get("stats") or {}
        path_text = path_obj.get("text")
        matches = stats.get("matches")
        if isinstance(path_text, str) and isinstance(matches, int) and matches > 0:
            counts[path_text] = matches
    return counts


def _read_page_metadata(abs_path: Path) -> tuple[str, dict[str, object]]:
    """Return ``(title, frontmatter_dict)`` for a vault page.

    Non-UTF-8 bytes → ``("", {})`` (per spec §Edge cases). Genuine
    ``OSError`` propagates — a page ripgrep found but the metadata pass
    can't read is a system-level failure the user should see, not a
    silent blank-metadata hit.
    Missing or malformed frontmatter → ``(title, {})``. Title is the
    first ``# ``-prefixed line *outside* fenced code blocks; empty
    string when no H1 is present (caller falls back to the filename
    stem).
    """

    try:
        text = abs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "", {}

    body = text
    frontmatter: dict[str, object] = {}
    if text.startswith("---\n"):
        # Split off the frontmatter block: from the opening ``---\n`` to
        # the next standalone ``---`` line.
        rest = text[4:]
        end_marker = rest.find("\n---\n")
        if end_marker != -1:
            block = rest[:end_marker]
            body = rest[end_marker + len("\n---\n") :]
            try:
                loaded = yaml.safe_load(block)
            except yaml.YAMLError:
                loaded = None
            if isinstance(loaded, dict):
                frontmatter = loaded

    # Skip ``# ``-prefixed lines inside fenced code blocks (``` … ```)
    # so a page that opens with a Python / Bash snippet doesn't pick a
    # comment as the title.
    title = ""
    in_fence = False
    for line in body.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("# "):
            title = line[2:].strip()
            break
    return title, frontmatter


def _coerce_tags(raw: object) -> list[str]:
    """Normalize the ``tags`` frontmatter field to a list of strings.

    Accepts a bare string (Obsidian-style ``tags: urgent``) by wrapping
    in a single-element list; otherwise must be a list of stringable
    values. Anything else yields an empty list.
    """

    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _filters_match(fm: dict[str, object], filters: SearchFilters) -> bool:
    """True iff every active filter is satisfied by the frontmatter."""

    if filters.type is not None and str(fm.get("type", "") or "") != filters.type:
        return False
    if filters.status is not None and str(fm.get("status", "") or "") != filters.status:
        return False
    if filters.tag is not None and filters.tag not in _coerce_tags(fm.get("tags")):
        return False
    return True
