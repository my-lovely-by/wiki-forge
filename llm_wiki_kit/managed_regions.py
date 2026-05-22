"""Parse, update, and strip kit-owned regions inside shared infra files.

ADR-0003 names three functions and two delimiter syntaxes:

- Markdown comment form, used inside ``.md`` files (e.g. ``AGENTS.md``)::

      <!-- BEGIN MANAGED: <id> -->
      ...body...
      <!-- END MANAGED: <id> -->

- YAML / shell comment form, used inside YAML files (e.g.
  ``frontmatter.schema.yaml``, ``.claude/research-providers.yaml``)::

      # BEGIN MANAGED: <id>
      ...body...
      # END MANAGED: <id>

The function signatures in ADR-0003 §Decision (``parse(content)``,
``update(content, region_id, new_content)``,
``extract_unmanaged(content)``) deliberately do not take a file path or
a flavor argument. This module accepts both delimiter forms in the same
parser pass — the two shapes do not collide in practice (no real ``.md``
file uses ``#``-prefixed BEGIN MANAGED lines as document content, and no
real YAML file uses ``<!-- ... -->``), and a file that mixes the two
forms for the same region is malformed and surfaces as a flavor-mismatch
error from the scanner. Keeping the API path-free means callers
(``write_helper.safe_write_region``, the renderer, and any future
introspection tooling) never need to thread file-type metadata.

Region bodies are returned and rewritten as the text strictly between
the BEGIN and END marker lines, joined with ``\\n``. A region with no
lines between markers has an empty body. The markers themselves are not
part of the body and are preserved verbatim (including indentation) on
``update``.

Errors surface as :class:`llm_wiki_kit.errors.ManagedRegionError`:
nesting, unmatched / unclosed markers, mismatched ids, duplicate ids in
the same file, mismatched flavor across a marker pair, and
``update(content, region_id, ...)`` against an id the file does not
contain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from llm_wiki_kit.errors import ManagedRegionError

_ID = r"[A-Za-z0-9][A-Za-z0-9_-]*"

_MARKDOWN_BEGIN = re.compile(rf"^[ \t]*<!--[ \t]*BEGIN MANAGED:[ \t]+(?P<id>{_ID})[ \t]*-->[ \t]*$")
_MARKDOWN_END = re.compile(rf"^[ \t]*<!--[ \t]*END MANAGED:[ \t]+(?P<id>{_ID})[ \t]*-->[ \t]*$")
_YAML_BEGIN = re.compile(rf"^[ \t]*#[ \t]*BEGIN MANAGED:[ \t]+(?P<id>{_ID})[ \t]*$")
_YAML_END = re.compile(rf"^[ \t]*#[ \t]*END MANAGED:[ \t]+(?P<id>{_ID})[ \t]*$")


@dataclass(frozen=True)
class _Region:
    region_id: str
    flavor: str
    begin_line: int
    end_line: int


def _match_marker(line: str) -> tuple[str, str, str] | None:
    """Return ``(kind, flavor, region_id)`` if ``line`` is a marker.

    ``kind`` is ``"begin"`` or ``"end"``; ``flavor`` is ``"markdown"`` or
    ``"yaml"``. Returns ``None`` for ordinary content lines.
    """
    for kind, pattern, flavor in (
        ("begin", _MARKDOWN_BEGIN, "markdown"),
        ("end", _MARKDOWN_END, "markdown"),
        ("begin", _YAML_BEGIN, "yaml"),
        ("end", _YAML_END, "yaml"),
    ):
        match = pattern.match(line)
        if match is not None:
            return kind, flavor, match.group("id")
    return None


def _scan(content: str) -> list[_Region]:
    lines = content.splitlines()
    regions: list[_Region] = []
    open_id: str | None = None
    open_flavor: str | None = None
    open_line: int = -1
    seen_ids: set[str] = set()

    for index, line in enumerate(lines):
        marker = _match_marker(line)
        if marker is None:
            continue
        kind, flavor, region_id = marker
        if kind == "begin":
            if open_id is not None:
                raise ManagedRegionError(
                    f"line {index + 1}: BEGIN MANAGED '{region_id}' inside "
                    f"open region '{open_id}'; nesting is not supported"
                )
            if region_id in seen_ids:
                raise ManagedRegionError(
                    f"line {index + 1}: duplicate managed region id '{region_id}'"
                )
            seen_ids.add(region_id)
            open_id = region_id
            open_flavor = flavor
            open_line = index
        else:  # kind == "end"
            if open_id is None:
                raise ManagedRegionError(
                    f"line {index + 1}: END MANAGED '{region_id}' has no matching BEGIN"
                )
            if region_id != open_id:
                raise ManagedRegionError(
                    f"line {index + 1}: END MANAGED '{region_id}' does not "
                    f"match open region '{open_id}'"
                )
            if flavor != open_flavor:
                raise ManagedRegionError(
                    f"line {index + 1}: END MANAGED '{region_id}' uses "
                    f"{flavor} syntax but BEGIN used {open_flavor}"
                )
            regions.append(_Region(open_id, flavor, open_line, index))
            open_id = None
            open_flavor = None
            open_line = -1

    if open_id is not None:
        raise ManagedRegionError(
            f"unclosed managed region '{open_id}' opened on line {open_line + 1}"
        )
    return regions


def canonical_region_body(body: str) -> bytes:
    """Return the byte form a managed-region hash is computed over.

    Bridges the asymmetry between two halves of the install pipeline:

    * The aggregator (``install._normalise_snippet``) writes each
      contributor's snippet with a trailing newline so concatenation
      across multiple contributors stays well-formed.
    * :func:`parse` returns the between-markers body with no trailing
      newline — the body is "lines joined by ``\\n``", terminator
      excluded.

    Ensures exactly one trailing newline when the body is non-empty;
    preserves interior whitespace verbatim. An empty body stays
    empty so the seed-file "no contributors yet" case hashes to
    ``hash(b"")``. The function is not a general whitespace-
    normaliser: ``"foo\\n\\n\\n"`` is left alone (intentional — it
    matches whatever the snippet author wrote), so a future
    contributor must not add a stronger normalisation rule without
    bumping the canonicalization version.

    Single source of truth so :func:`write_helper.safe_write_region`
    (write side) and :func:`doctor.check_managed_region_drift`
    (read side) cannot drift on subtle canonicalization rules —
    every future tweak lands here once.
    """

    if body and not body.endswith("\n"):
        body = body + "\n"
    return body.encode("utf-8")


def parse(content: str) -> dict[str, str]:
    """Return ``{region_id: body}`` for every managed region in ``content``.

    The body is the text strictly between the BEGIN and END marker lines,
    joined with ``\\n``. A region with no lines between markers maps to
    the empty string.
    """
    lines = content.splitlines()
    return {
        region.region_id: "\n".join(lines[region.begin_line + 1 : region.end_line])
        for region in _scan(content)
    }


def update(content: str, region_id: str, new_content: str) -> str:
    """Rewrite the body of ``region_id`` to ``new_content`` in place.

    Everything outside the named region — including other managed
    regions, the markers themselves, and any indentation on the marker
    lines — is preserved exactly. A trailing newline on ``new_content``
    is treated as part of the line terminator, not as an extra blank
    line; ``"foo\\n"`` and ``"foo"`` produce the same single-line body.
    The result preserves the original trailing-newline state of
    ``content``.

    Raises :class:`ManagedRegionError` if ``region_id`` is not present.
    """
    regions = _scan(content)
    target = next((r for r in regions if r.region_id == region_id), None)
    if target is None:
        raise ManagedRegionError(f"unknown managed region '{region_id}'")

    lines = content.splitlines()
    body_lines = new_content.splitlines() if new_content else []
    rebuilt = lines[: target.begin_line + 1] + body_lines + lines[target.end_line :]
    result = "\n".join(rebuilt)
    if content.endswith("\n"):
        result += "\n"
    return result


def extract_unmanaged(content: str) -> str:
    """Return ``content`` with every managed region (markers and body) removed.

    Used by drift detection on the user-editable parts of a shared file:
    ADR-0003 §Decision says drift inside a managed region falls through
    to the proposal flow, while drift outside is invisible by design.
    Hashing ``extract_unmanaged(on_disk)`` against
    ``extract_unmanaged(journaled)`` is how a future caller could
    distinguish the two if the design ever flips.
    """
    regions = _scan(content)
    if not regions:
        return content

    lines = content.splitlines()
    keep: list[str] = []
    region_iter = iter(regions)
    next_region = next(region_iter, None)
    index = 0
    while index < len(lines):
        if next_region is not None and index == next_region.begin_line:
            index = next_region.end_line + 1
            next_region = next(region_iter, None)
            continue
        keep.append(lines[index])
        index += 1

    result = "\n".join(keep)
    if content.endswith("\n") and result:
        result += "\n"
    return result
