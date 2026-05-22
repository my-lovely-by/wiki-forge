# Knowledge base

The repository's accumulating record of *patterns, gotchas, and
antipatterns* — the things this project learns about itself as code lands.
It lives at `patterns.jsonl` next to this file; agents prime from it at
session start, contributors curate it by hand.

This is deliberately different from the documents that already exist:

| Where | What goes there |
|---|---|
| `docs/adr/` | Decisions ("we chose X over Y because…"). Frozen once accepted. |
| `docs/rfc/` | Proposed changes. Frozen once accepted/rejected. |
| `docs/architecture/` | Current code structure. Living. |
| `docs/specs/<thing>/spec.md` | Contract for a piece of the kit. Living. |
| `docs/guides/`, `docs/concepts/`, `docs/reference/`, `docs/tutorials/` | User-facing docs (Diátaxis). |
| **`docs/knowledge/patterns.jsonl`** | **Practitioner-level lessons: patterns, gotchas, antipatterns. Scoped to file globs.** |

ADRs answer *why was this decided*. Knowledge entries answer *what
should the next person avoid stepping on, or repeat*.

## When to add an entry

A loop has finished. You ask: *what would have made this go faster?*
Three answers worth recording here:

- **Pattern.** "When you touch X, also remember Y." A repeatable shape
  that worked once and will work again. Example: "Every kit write into
  a user's vault must go through `write_helper.safe_write()`."
- **Gotcha.** A non-obvious cost or constraint that bit you. Example:
  "The journal treats an empty file as zero events — don't error on it."
- **Antipattern.** A shape that looked appealing but rotted. Example:
  "Don't hand-edit `docs/CHARTER.md` substantively — the freeze
  discipline is what keeps scope honest."

If the lesson is about *current code structure*, it belongs in
`docs/architecture/`. If it's a *decision*, it belongs in `docs/adr/`.
If it's a *proposed change*, it belongs in `docs/rfc/`. If it's *how
to use the kit*, it belongs in `docs/guides/` (or the other Diátaxis
buckets). Knowledge entries are the residue that doesn't fit those
buckets — *practice* rather than structure, decision, or instruction.

## Schema

`patterns.jsonl` is line-delimited JSON. Each non-empty line is one
entry:

```json
{"id": "K-0001", "kind": "gotcha", "scope": "llm_wiki_kit/**", "title": "Never bypass write_helper.safe_write() for vault writes", "body": "Drift detection is load-bearing. Any kit code that writes into a user's vault must route through write_helper.safe_write(); raw open()/write_text() calls escape the journal and break wiki doctor's reconciliation. See AGENTS.md § Things you should not do without asking.", "source": "AGENTS.md", "created": "2026-05-16", "updated": "2026-05-16"}
```

<!-- The schema below is enforced by tools/lint-knowledge.sh. Keep each
     field's name backticked in the first column on a single line;
     keep every kind backticked on the kind row. Don't split rows
     across lines. -->

| Field | Type | Notes |
|---|---|---|
| `id` | `K-\d{4,}` | Unique, zero-padded to four digits. Conventionally sequential, but the linter only enforces uniqueness — gaps are fine. |
| `kind` | `pattern` \| `gotcha` \| `antipattern` | Exactly one of these three values. |
| `scope` | glob | Path pattern this applies to — `llm_wiki_kit/**`, `templates/content-types/**`, `core/files/skills/**`, `docs/guides/**`, or `*` for repo-wide. |
| `title` | string | One-line summary; aim for under 80 characters. |
| `body` | string | The lesson itself. A paragraph or two is enough; if you find yourself writing more, the entry probably wants to be split. |
| `source` | string | Where this came from: `AGENTS.md`, `ADR-0003`, `RFC-0002`, `PR#42`, `issue#13`, etc. |
| `created` | `YYYY-MM-DD` | Date the entry was first added. **Kit-specific extension** (not in the upstream `agent-ready-repo` schema). |
| `updated` | `YYYY-MM-DD` | Date the entry was last clerically corrected; equal to `created` until then. **Kit-specific extension** (not in the upstream `agent-ready-repo` schema). |

The format is JSONL (one JSON object per line, no commas, no wrapping
array) so it grows by append and reads line-by-line.
[`tools/lint-knowledge.sh`](../../tools/lint-knowledge.sh) validates
the file and [`tools/hooks/session-start.sh`](../../tools/hooks/session-start.sh)
reads it.

## Curation

Entries are *append-only by default*. If a lesson stops being true (the
underlying code changed, the constraint went away), the right move is
to **add a new entry** that says so, citing the old `id` in the body —
not to edit the old one. This keeps the knowledge base honest about
*when* a lesson was true.

**Supersession lives in the body, by design.** The schema has no
`supersedes` field; the linter rejects unknown keys. Citing the old
entry's id in the new entry's `body` is the convention. We chose
human-readable prose over a machine-checkable field because
supersession is rare enough that the cost of curating a separate
field outweighed the legibility gain.

Genuine corrections (typo, wrong file path) are fine to fix in place;
those are clerical, not historical. Bump `updated` when you make one.

When an entry's scope no longer matches anything (the module was
removed), leave it as-is. The next reader can see the path is gone and
infer the entry is historical. Removing entries hides the history of
what you used to worry about.

## Where this fits in the work-loop

AGENTS.md § Workflow ends with "Capture what you learned before
opening the PR." When that learning fits the pattern/gotcha/antipattern
shape, the canonical home is here. Other kinds of learning still go
where they already belong (AGENTS.md, ADRs, `docs/architecture/`,
skill bodies).

The session-start hook
([`tools/hooks/session-start.sh`](../../tools/hooks/session-start.sh))
reads this file and prints the entries — optionally filtered by glob
— so a fresh agent session starts with the relevant patterns already
in context.
