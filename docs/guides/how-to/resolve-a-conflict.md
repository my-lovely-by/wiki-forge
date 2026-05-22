# How-to — Resolve a conflict

You have a `.proposed` sidecar in your vault (`wiki doctor` flagged it,
or Claude told you, or you noticed it yourself). This page walks
through reconciling it without losing your edits and without losing
the kit's update.

This how-to demonstrates the workflow against the
`examples/conflict-pending/` vault in the `llm-wiki-kit` source
repository, so you can follow along without having to construct a
drift situation yourself. The same `wiki resolve` flow applies in any
vault.

**Prerequisite — a clone of the repo.** The example vault is not
shipped in the pip-installed wheel; you need the source tree to
follow along. If you don't have it yet:

```sh
git clone https://github.com/eugenelim/llm-wiki-kit
```

If you're encountering a `.proposed` file in your own vault, skip
the copy step and apply steps 2–5 to your vault directly — the
commands are the same, just substitute your real page path.

## Step 1 — Copy the drifted example vault

```bash
$ rm -rf /tmp/conflict-demo
$ cp -R <repo-root>/examples/conflict-pending /tmp/conflict-demo
$ cd /tmp/conflict-demo
```

Replace `<repo-root>` with the absolute path to your `llm-wiki-kit`
checkout. The `rm -rf` is belt-and-suspenders so re-running the
how-to gets a fresh demo vault each time.

You now have a vault with one drifted page: `wiki/people/example-contact.md`
has been edited on disk, while the kit's journaled baseline plus the
kit's most recent attempted update both live in
`wiki/people/example-contact.md.proposed` and the journal.

## Step 2 — Confirm the conflict

```bash
$ ls wiki/people/*.proposed
```

You should see `wiki/people/example-contact.md.proposed` listed.
That's the kit telling you: "I tried to update this page, found your
edits, and saved my version next to yours rather than overwriting."
Running `wiki doctor` against this vault would also flag the
proposal with a `pending-proposal:` line (and exit non-zero). The
`ls` check above is enough for following along; in a real workflow
you'd see this surfaced by `wiki doctor` or by Claude noticing the
sidecar.

## Step 3 — Read the three versions

For any conflict, three versions exist:

| Version    | Where                                            |
|------------|--------------------------------------------------|
| **Baseline** | The journal — most recent `page.write` for the path. |
| **On-disk**  | `wiki/people/example-contact.md` — what you have now. |
| **Proposed** | `wiki/people/example-contact.md.proposed` — what the kit wanted to write. |

```bash
$ cat wiki/people/example-contact.md
$ cat wiki/people/example-contact.md.proposed
```

Diff them side-by-side mentally. The two files differ; the baseline
sits in the journal as a hash (the planned `wiki journal explain`
command will resolve it to content; until then, the journal hash is
what the kit uses internally — you can still see the *event* with
`grep page.write .wiki.journal/journal.jsonl`).

## Step 4 — Pick a resolution mode

`wiki resolve` accepts three flavors:

- **`wiki resolve <path>` (no flag)** — read the merged content from
  stdin. The kit writes that to `<path>`, deletes the sidecar, and
  journals the resolution. Use this when you've crafted a merge by
  hand (or had Claude propose one).
- **`wiki resolve <path> --accept`** — discard your edits; the kit
  writes the sidecar's bytes verbatim to `<path>`.
- **`wiki resolve <path> --keep`** — discard the kit's proposal;
  re-baseline to the on-disk content as it stands.

Pick `--accept` to follow along — it's the simplest path and lets
you verify the resolution worked without composing a merge:

```bash
$ wiki resolve wiki/people/example-contact.md --accept
```

The kit replaces `wiki/people/example-contact.md` with the proposed
version, deletes the sidecar, and appends a `page.conflict_resolved`
event to the journal.

## Step 5 — Confirm

```bash
$ wiki doctor
```

`wiki doctor` exits 0 now — no more pending proposal.

```bash
$ tail .wiki.journal/journal.jsonl
```

The tail shows a `page.conflict_resolved` event plus the
`page.write` event from the merge. The vault is back to a clean state.

## What if the merge isn't mechanical?

Sometimes your edits and the kit's proposal genuinely overlap and
neither `--accept` nor `--keep` is right. Two options:

1. **Walk it with Claude.** Open the vault in Claude Code and Claude
   loads the `wiki-conflict` skill automatically when it sees a
   `.proposed` file. It will walk the three versions with you,
   propose a merge, and pipe the result into `wiki resolve <path>`
   via stdin. See `core/files/skills/wiki-conflict/SKILL.md` for the
   full contract.
2. **Write the merge by hand.** Compose the merged file content,
   then:
   ```sh
   cat my-merged-version.md | wiki resolve wiki/people/example-contact.md
   ```

In either case, the kit's invariant holds: every resolution lands a
`page.conflict_resolved` event in the journal, so `wiki doctor`
agrees with reality afterwards.

## Don't bypass the resolve command

A few shortcuts look tempting but break drift detection going forward:

- **Don't `rm <path>.proposed`** — the journal still carries a
  `page.proposal` event; `wiki doctor` will keep flagging it.
- **Don't `mv <path>.proposed <path>`** — that writes the proposed
  content without journaling the resolution; drift detection loses
  its baseline for that file forever.
- **Don't edit `<path>` to match `<path>.proposed`** — same issue.

`wiki resolve` is the only way to durably close a conflict.
