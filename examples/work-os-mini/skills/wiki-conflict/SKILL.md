---
name: wiki-conflict
description: "Resolve `<path>.proposed` sidecar conflicts. The kit writes a sidecar when it tried to update a file the user had already edited — it never clobbers. This skill walks through reconciling the on-disk version with the proposed update, then commits the merged result through `wiki resolve` so the journal regains a clean baseline. Use whenever a `.proposed` sidecar appears, or whenever `wiki doctor` reports pending proposals."
license: MIT
---

# wiki-conflict

> **⚠️ Some companion surfaces are not yet shipped in v2.0.0.dev.**
> The primary `wiki resolve` action this skill commits through is
> shipped and operational. The companion commands referenced for
> triage and context — `wiki journal tail`, `wiki journal explain`,
> `wiki upgrade`, and `wiki run` — are Phase D/E stubs that exit
> non-zero with `wiki <cmd>: not yet implemented (v2 migration in
> progress, see RFC-0001).` (see `llm_wiki_kit/cli.py`). Until they
> land, read those commands as design references; for triage, read
> `.wiki.journal/journal.jsonl` directly or run `wiki doctor`.
> Tracked under retro-review concern C7 (issue #23).

A `<path>.proposed` file means the kit wanted to write `<path>` but
detected the on-disk content didn't match the version the kit last
wrote. Rather than overwrite the user's edits, the kit dropped the
proposed version next to the file. The user (with your help)
reconciles.

## When to load this skill

- The user mentions a `.proposed` file.
- `wiki doctor` reports `pending_proposals`.
- A `wiki upgrade` or `wiki run <operation>` outputs `<n>
  proposals written`.
- You see a `page.proposal` event in `wiki journal tail`.

## The three pieces

For any conflict, three versions exist:

| Version    | Where                            | What it is                              |
|------------|----------------------------------|-----------------------------------------|
| Baseline   | The journal — most recent `page.write` for the path | What the kit last wrote.                |
| On-disk    | `<path>` — the user's current file | What the user has now.                  |
| Proposed   | `<path>.proposed`                | What the kit *wanted* to write this run. |

The baseline is the common ancestor; the on-disk and proposed versions
are the divergent branches. A three-way merge is the right mental
model.

## Workflow

For each `.proposed` file:

1. **Read all three.**
   - On-disk: open `<path>`.
   - Proposed: open `<path>.proposed`.
   - Baseline (for context): run
     `wiki journal explain <path>` to see the last `page.write` for
     the path. If you need the exact prior content, the journal
     records hashes, not bodies — ask the user, or git-log if the
     vault is committed.

2. **Diff the two new versions.** Compute the meaningful changes:
   - What did the user change vs. baseline?
   - What did the kit propose changing vs. baseline?
   - Where do those changes intersect?

3. **Explain the conflict to the user.** Two or three sentences:
   > "You edited the synopsis and added tags. The kit proposes
   > replacing the synopsis with a shorter version (no tag changes).
   > Do you want your synopsis kept, the kit's, or a merge?"

4. **Propose a merged version.** Don't pick silently. Show the
   merge as a diff or a full file and ask for confirmation. If the
   merge is mechanical (different sections changed; no overlap),
   say so explicitly: "These changes don't overlap — I can take
   both."

5. **Commit the merge.** Once the user confirms:

   ```bash
   wiki resolve <path>
   ```

   You pass the merged content via stdin or write it to a temp
   file the CLI consumes — see `wiki resolve --help`. The CLI
   writes the merged version to `<path>`, deletes the sidecar,
   and journals both a `page.write` (the new baseline) and a
   `page.conflict_resolved` (audit trail).

6. **Confirm.** Show the user the new file path and note that the
   sidecar is gone.

## Batch resolution

If `wiki doctor` lists many proposals (e.g. after `wiki upgrade`):

1. Group them by file type / similarity. Identical-shape conflicts
   (same managed region modified the same way) can often be
   confirmed in bulk.
2. Walk one representative conflict in detail with the user; ask if
   the same resolution should apply to the others.
3. Resolve each individually — `wiki resolve` doesn't have a batch
   mode by design. The per-file confirmation is the safety.

## When the merge is non-mechanical

Sometimes there is no clean merge — the user's edits and the kit's
proposal genuinely disagree about the same words. Surface this and
**ask**:

- "Keep your version, discard the kit's proposal" — `wiki resolve
  <path> --keep`. The kit re-baselines to the on-disk version
  without writing the proposed content.
- "Accept the kit's proposal, discard your edits" — `wiki resolve
  <path> --accept`. The kit overwrites with the proposed content.
- "Let me edit a merged version" — you write the merged content,
  the user confirms, then `wiki resolve`.

Never pick on the user's behalf when a merge is non-mechanical. The
edits encode their judgment.

## Don't bypass

- Don't `rm <path>.proposed` to make the conflict go away. The
  journal still has a `page.proposal` event pending; `wiki doctor`
  will keep flagging it.
- Don't `mv <path>.proposed <path>`. That writes the proposed
  content without journaling the resolution — drift detection
  loses its baseline for that file forever.
- Don't edit `<path>` to look like `<path>.proposed`. Same issue.

Always go through `wiki resolve`.

## Failure modes

- **Conflict on a shared file (`AGENTS.md`, `frontmatter.schema.yaml`,
  …) with managed regions.** The proposed sidecar contains the file
  as the kit *would* have re-rendered it. Diff the managed region
  the kit owns vs. the on-disk one; the unmanaged content should
  already match (drift detection is region-scoped). Resolve via
  `wiki resolve` the same way.
- **Multiple consecutive proposals for the same file.** The latest
  `<path>.proposed` is always authoritative; the kit overwrites
  it each time. The journal records every attempt.
- **Sidecar without a `page.proposal` event.** Unexpected.
  Investigate via `wiki doctor` before acting.
