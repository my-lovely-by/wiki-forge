"""Render a primitive's ``files/`` tree into a user's vault.

ADR-0001 names the contract: ``str.format_map(SafeDict(context))`` for a
small allowlist of files (:data:`INTERPOLATED_FILES`), byte-for-byte copy
for everything else. Every file dropped into the vault is routed through
:func:`write_helper.safe_write` per ADR-0004, so every write is journaled
and drift-protected.

Three design decisions worth pinning here because the task spec calls
them out:

1. **Binary files.** :func:`safe_write` takes ``content: str`` and the
   kit's runtime deps are ``pyyaml`` + ``pydantic`` + stdlib only — there
   is no binary write path today. Every file the kit currently ships
   (markdown, YAML, SKILL.md, scripts) is text. ``render_tree`` decodes
   each source file as UTF-8 and raises :class:`WikiError` on a decode
   failure rather than smuggling raw bytes through the str-typed write
   path. A primitive that needs to ship images should propose a new ADR
   for a bytes-aware ``safe_write`` instead of bypassing this module.

2. **``by`` attribution.** ``render_tree`` does not try to infer the
   responsible primitive from ``src`` — relative paths into a templates
   tree depend on installer plumbing the renderer shouldn't know about.
   Every call site (``primitives.install``, ``wiki init``) already
   knows the primitive name, so ``by`` is a required keyword argument
   and is passed straight through to ``safe_write`` for the journal.

3. **``SafeDict`` aggressiveness.** ADR-0001 promises that single-brace
   tokens in interpolated files "survive untouched." That holds for
   identifier-named tokens (``{foo}``, ``{foo.bar}``, ``{foo[0]}``,
   ``{foo:>10}``) via the :class:`_LazyToken` placeholder returned from
   :meth:`SafeDict.__missing__`. It does *not* hold for positional
   tokens (``{0}``, ``{4}``) because ``str.format_map`` rejects those
   before ``SafeDict`` is consulted — a regex like ``\\d{4}`` in an
   interpolated file must be authored as ``\\d{{4}}``. Pinned in
   ``tests/unit/test_render.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from llm_wiki_kit.errors import WikiError
from llm_wiki_kit.write_helper import safe_write

INTERPOLATED_FILES: frozenset[str] = frozenset(
    [
        "AGENTS.md",
        "CORE.md",
        "identity.md",
        "frontmatter.schema.yaml",
        ".gitignore",
    ]
)


class _LazyToken:
    """Placeholder returned for missing keys.

    Its ``__format__`` re-emits the original ``{name}`` (or ``{name.attr}``,
    ``{name[idx]}``) so a stray token in an interpolated file is preserved
    instead of crashing the render. The format spec is dropped on the
    assumption that a file author would never attach a spec to a token they
    didn't intend to substitute.
    """

    __slots__ = ("_token",)

    def __init__(self, token: str) -> None:
        self._token = token

    def __format__(self, _spec: str) -> str:
        return "{" + self._token + "}"

    def __getattr__(self, attr: str) -> _LazyToken:
        return _LazyToken(f"{self._token}.{attr}")

    def __getitem__(self, key: object) -> _LazyToken:
        return _LazyToken(f"{self._token}[{key}]")


class SafeDict(dict[str, object]):
    """``dict`` subclass whose missing keys yield a self-formatting placeholder.

    Values are typed as ``object`` because :meth:`__missing__` returns a
    :class:`_LazyToken` rather than a ``str``; substitution still works at
    runtime because ``str.format_map`` only needs ``__format__`` on the
    resolved value.
    """

    def __missing__(self, key: str) -> _LazyToken:
        return _LazyToken(str(key))


def render_tree(
    src: Path,
    dest: Path,
    context: Mapping[str, str],
    journal_path: Path,
    by: str,
) -> None:
    """Walk ``src`` and render every file into ``dest`` via :func:`safe_write`.

    Files whose basename is in :data:`INTERPOLATED_FILES` are rendered via
    ``str.format_map(SafeDict(context))``; everything else is copied
    byte-for-byte (after a UTF-8 round-trip — see the module docstring on
    binary files). Empty or missing source trees are a no-op.

    ``dest`` must be the vault root that owns ``journal_path``;
    :func:`safe_write` computes vault-relative paths from
    ``journal_path.parent.parent`` and a mismatched ``dest`` would produce
    nonsense journal entries.
    """

    vault_root = journal_path.parent.parent
    if dest.resolve() != vault_root.resolve():
        raise WikiError(
            f"render_tree dest {dest} must equal the vault root "
            f"derived from journal_path ({vault_root})"
        )

    if not src.exists():
        return

    safe_dict: SafeDict = SafeDict()
    safe_dict.update(context)

    for source_file in sorted(p for p in src.rglob("*") if p.is_file()):
        relative = source_file.relative_to(src)
        try:
            text = source_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WikiError(
                f"render_tree only supports UTF-8 text files; "
                f"{source_file} is not valid UTF-8 ({exc.reason})"
            ) from exc

        if source_file.name in INTERPOLATED_FILES:
            content = text.format_map(safe_dict)
        else:
            content = text

        safe_write(dest / relative, content, by=by, journal_path=journal_path)
