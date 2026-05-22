---
name: wiki-doctor
description: "Validate vault state against the journal. Replays the journal, computes the expected on-disk set, and reports drift — orphan files (on disk but not in the journal), missing files (in the journal but not on disk), managed-region damage (markers deleted or misaligned), pending proposals, stale locks, and uninstalled-but-referenced primitives. Use to diagnose \"something feels off\" symptoms, after a manual edit, after a git merge, or whenever the user asks \"is the vault healthy?\"."
license: MIT
---

# wiki-doctor

> **⚠️ Some companion surfaces are not yet shipped in v2.0.0.dev.**
> The primary `wiki doctor` action this skill describes is shipped
> and operational; the `--strict` flag this skill mentions is *not*
> currently accepted by argparse and will exit with an `unrecognized
> arguments` error. The recovery commands referenced below —
> `wiki upgrade`, `wiki upgrade --primitive`, `wiki run`,
> `wiki journal append`, and `wiki journal repair` — are Phase D/E
> not-yet-shipped surfaces: the first three exit `wiki <cmd>: not yet
> implemented (v2 migration in progress, see RFC-0001).`; the latter
> two are not registered subcommands and argparse rejects them with
> `invalid choice`. Until they land, read those commands as design
> references and surface the gap to the user when a recovery step
> needs one. Tracked under retro-review concern C7 (issue #23).

The vault's source of truth is the journal at
`.wiki.journal/journal.jsonl`. `wiki doctor` replays the journal,
computes the state the kit thinks the vault should be in, diffs that
against what's actually on disk, and reports the differences.

## When to load this skill

- User asks "is the vault healthy?" / "run doctor" / "check
  consistency".
- A `wiki run` or `wiki upgrade` exits with warnings.
- After a `git pull` / `git merge` that touched many files at once.
- After the user hand-edits files outside the kit's flow.
- When something feels off — search returns surprising results, an
  ingest fails for unclear reasons, a page seems to have been
  reverted.

## What it checks

| Category               | What it catches                                                    |
|------------------------|--------------------------------------------------------------------|
| Orphan files           | Files on disk under `wiki/` not journaled by any `page.write`.     |
| Missing files          | `page.write` recorded but `<path>` absent on disk.                 |
| Drift                  | On-disk hash differs from the latest `page.write` hash. (`wiki ingest` already detects per-write drift; doctor catches batch drift.) |
| Managed-region damage  | A shared file is missing markers the kit expects, has duplicate region ids, or has unclosed markers. |
| Pending proposals      | `<path>.proposed` files with corresponding `page.proposal` events. |
| Stale locks            | `lock.acquired` with no matching `lock.released` older than a threshold. |
| Schema violations      | Journal events that no longer validate (e.g. after a kit upgrade). |
| Uninstalled primitives | A page's `type:` references a content type whose primitive isn't installed. |

## How to run

```bash
wiki doctor
```

Exits 0 if the vault is clean. Non-zero if there are issues. The
output is markdown — sectioned by category, with paths and journal
line numbers where applicable.

`--strict` raises minor warnings (stale `modified` dates, etc.) into
errors. Skip it for a routine check; use it before a release / commit.

## Workflow

1. **Run `wiki doctor` without flags.** Read the output.
2. **Triage by category.** Some categories are routine (a few stale
   pages are normal); others are urgent (managed-region damage
   means subsequent kit writes will fail).
3. **Surface the findings.** Summarize in chat: counts per category,
   the urgent items, anything ambiguous.
4. **Walk fixes with the user.** Most categories have a clear next
   step (see below).
5. **Re-run `wiki doctor`.** Confirm green.

## Triage cheat sheet

- **Orphan file.** Either the user copied it in manually (legitimate;
  ingest it via `wiki ingest` to journal it) or the journal lost
  the corresponding event (rarer; `wiki journal repair` can
  back-fill). Ask the user which.

- **Missing file.** The user deleted a file the kit thinks should
  exist. Confirm intent — they may have meant to. If yes, journal
  the deletion via `wiki journal append page.delete`. If no, restore
  from git or from the on-disk content.

- **Drift on a file with no `.proposed` sidecar.** The user edited a
  page after the last kit write, and nothing has tried to re-write
  since. Not an error; `wiki upgrade` (or the next operation) will
  surface a sidecar.

- **Managed-region damage.** The markers got mangled — usually a
  bad copy/paste or a merge conflict resolved by hand. Open the
  file; restore the markers. If you can't tell what should be
  inside, run `wiki upgrade --primitive core` (or the primitive
  that owns the region) and reconcile via `wiki-conflict`.

- **Pending proposals.** Load `wiki-conflict` and walk them.

- **Stale lock.** Confirm no other session is mid-flight, then
  `wiki lock release --force --by <name>`.

- **Schema violation.** The kit upgraded and the journal grew an
  event type it doesn't recognize, or vice versa. The kit's
  forward-compat policy is to keep schemas additive — if this
  fires, surface it and ask the user to file a bug.

- **Uninstalled primitive referenced by a page.** A page's `type:`
  points to a primitive that isn't installed in this recipe.
  Either install it (`wiki add content-type:<name>`) or rewrite
  the page to use an installed type.

## Anti-patterns

- Don't act on doctor output silently. The user owns the
  reconciliation calls.
- Don't `rm` files to "fix" orphans. Confirm intent first.
- Don't hand-edit the journal to make a category go away. Use
  `wiki journal repair` (additive append) instead.

## Output

The doctor output itself is ephemeral — printed to stdout. If the
user wants a record, redirect to `log/doctor-YYYY-MM-DD.md`.
