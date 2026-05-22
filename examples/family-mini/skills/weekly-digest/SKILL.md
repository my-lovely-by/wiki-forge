---
name: weekly-digest
description: "Produce one weekly digest page summarizing meetings (and later, other content-types) inside an ISO-week window. Load when the user asks for a weekly summary, when `wiki run weekly-digest` invokes you, or on a scheduled sweep. Writes one page to `outputs/digests/<window>.md`; idempotent for a given window — re-running overwrites the same page rather than producing a new one."
license: MIT
---

# weekly-digest

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run weekly-digest`, and `wiki run` is a stub in v2.0.0.dev:
> it prints `wiki run: not yet implemented (v2 migration in progress,
> see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Walk the week's meeting pages (later: more content-types), pull out
what mattered, and write one durable digest page citing the inputs.

## When to load

- The user asks for "the digest", "last week's summary", "what
  happened this week", etc.
- `wiki run weekly-digest` runs you with the contract from
  `contract.yaml`.
- A scheduled invocation (cron-like) on a configured day.

If the user wants a one-off summary across a different time range,
load this skill and pass the explicit window — don't invent a new
skill.

## Inputs

From the operation contract:

- **`window`** — ISO week (e.g. `2026-W20`). Defaults to the most
  recent *complete* week (i.e. last Monday → Sunday in the vault's
  timezone). Today is in the *current* week; the current week is
  surveyed but not finalized.
- **`sources`** — content-types to include. v0.1 ships with
  `[meeting]`; later content-types extend this list.

## Procedure

1. **Find the input pages.** For each content-type in `sources`,
   walk its directory under `wiki/` and select pages whose
   type-specific date field falls inside the window. For meetings
   that's `meeting_date`. Use the `wiki-search` skill with
   `--type` and `--frontmatter` filters; do not hand-grep.
2. **Extract the signal, not the prose.** For each input page,
   pull:
   - Title and date.
   - Decisions (`meeting_decisions`).
   - Follow-ups (`meeting_follow_ups`), grouped by owner.
3. **Compose the digest.** One page at `outputs/digests/<window>.md`
   with sections:
   - **Decisions this week.** Bullet per decision, cite the source
     page as a wikilink.
   - **Follow-ups by owner.** Group by `@owner`, show due dates,
     cite source page.
   - **Loose ends.** Decisions or follow-ups from earlier weeks that
     are still open (cross-reference the `follow-up-tracker`
     operation once it ships).
4. **Idempotence.** If the digest page for this window already
   exists, the kit's `safe_write` will hash-compare and either
   rewrite (if you and the kit agree on the content) or sidecar
   (if the user has edited the digest by hand). Both cases are
   correct — don't bypass.

## Frontmatter for the digest page

```yaml
type: digest
status: active
provenance: synthesized
created: <today>
modified: <today>
tags: [weekly-digest, <window>]
digest_window: <window>
```

The `digest` type may not yet exist in `frontmatter.schema.yaml`'s
managed `types` region — that's fine for v0.1. A later task ships a
content-type primitive for digests that registers the type properly;
until then, the operation writes pages with a type the schema doesn't
yet validate, and `wiki-lint` flags it as a known gap.

## When you can't produce a meaningful digest

If the window is empty (no meetings, no other input), produce a
minimal page noting "no in-scope activity for <window>." A real
digest with one bullet beats no page at all, because the act of
running the operation is itself a journaled event that downstream
tooling can pick up.
