# Tutorial 1 — Create your first vault

A first walkthrough: you'll go from "the kit is installed" to "I have a
vault on disk that I can read, and I understand the four moving parts."
About 20 minutes of doing time. No API keys required.

## Prerequisites

- **Python 3.11 or newer.** Check with `python --version`.
- **`llm-wiki-kit` installed and on `PATH`.** Confirm with
  `wiki --version`.

If you don't have it yet, install it like this (this block is shown
for reference — the tutorial gate skips it and assumes you've already
installed):

```sh
pip install llm-wiki-kit
```

If you're working from a clone of the repo (the contributor path), use
the editable install instead:

```sh
pip install -e '.[dev]'
```

## What you'll build

A small vault under `my-first-vault/` shaped by the **personal**
recipe (the smallest of the three the kit ships). When you're done
you'll have:

- An initialized vault tree with the kit's skills, templates, and
  schema in place.
- One source file under `raw/`.
- Two journal entries showing the kit's dispatch decisions — the
  ingest route and the operation run.
- A clear picture of the two surfaces you'll use day-to-day: the
  `wiki` CLI for vault state, and your Claude Code session for
  content work.

## Step 1 — Initialize the vault

```bash
$ wiki init my-first-vault --recipe personal
```

You'll see a short summary of installed primitives. The kit creates
the vault directory, lays down `wiki/`, `skills/`, `_templates/`, and
the journal under `.wiki.journal/journal.jsonl`.

Because `--no-git` was not passed, the kit also initialized a git
repository for you and made one initial commit covering the
freshly-rendered tree. `git log --oneline` should show one commit
named *Initialize wiki vault from personal recipe*. The rest of this
tutorial is git-agnostic; any subsequent commits are yours to make.

If you'd rather manage versions yourself (or your global git config
isn't set up), pass `--no-git` to step 1 instead:

```text
wiki init my-first-vault --recipe personal --no-git
```

`personal` is the smallest recipe and the right starting point for a
first vault. The other two shipped recipes — `family` and `work-os` —
each ship more primitives; pick those when you're ready for more
shape.

## Step 2 — Verify with `wiki doctor`

```bash
$ cd my-first-vault
$ wiki doctor
```

`wiki doctor` walks the vault's journal, compares it against on-disk
reality, and reports anything inconsistent. A clean vault produces no
output and exits 0 — no news is good news here.

## Step 3 — Read the journal

The journal is the kit's single source of truth: every state-changing
action appends one line of JSON before touching disk. Read it as plain
text any time:

```bash
$ cat .wiki.journal/journal.jsonl
```

You should see one `vault.init` line, one `primitive.install` line
per primitive the recipe installed, and (because step 1 didn't pass
`--no-git`) one `vault.git_initialized` line marking the kit's
initial commit. Each line has a `timestamp`, a `by` field (the kit's
name for whoever performed the action), and type-specific fields.
The richer surfaces (`wiki journal tail`, `wiki journal grep`,
`wiki journal explain`) are planned but not yet shipped in v2.0.0 —
until they land, `cat` and `tail` are how you read the journal.

## Step 4 — Ingest a source

Create a small fixture file:

```bash
$ mkdir -p raw
$ printf '# Standup notes\n\nDiscussed Q3 priorities.\n' > raw/note.md
```

Now route it to a content-type:

```bash
$ wiki ingest --as meeting raw/note.md
```

You'll see a line confirming the routed content-type and naming the
skill to run next — something like *"Routed raw/note.md →
content-type:meeting. Run `ingest-meeting` in your Claude session."*
The kit has journaled this dispatch but it has *not* created the
synthesized meeting page yet — that happens in your Claude Code
session.

Check the journal — the last line should be the `ingest.routed`
event you just produced:

```bash
$ tail -1 .wiki.journal/journal.jsonl
```

`--as meeting` names the content-type explicitly so the kit doesn't
have to infer one from the filename.

To actually synthesize the meeting page from this source, open the
vault in Claude Code and tell Claude:

```bash
> Read raw/note.md and run the ingest-meeting skill to produce a
> meeting page under wiki/meetings/.
```

Claude reads `skills/ingest-meeting/SKILL.md` for the contract and
writes the page through the kit's `safe_write` path so the journal
stays consistent. The rest of this tutorial does not depend on that
page existing — every step below still works whether or not you ran
the Claude part.

## Step 5 — Run an operation

Operations are recurring tasks the kit knows how to dispatch. Try the
`weekly-digest`:

```bash
$ wiki run weekly-digest
```

The kit prints a one-line dispatch summary listing the operation's
inputs (e.g. `sources=meeting`) and journals an `operation.run` event.
As with `wiki ingest`, the actual synthesis happens in your Claude
Code session:

```bash
> Run the weekly-digest skill against the meetings from the last
> seven days.
```

Confirm the dispatch landed in the journal:

```bash
$ tail -1 .wiki.journal/journal.jsonl
```

## Step 6 — Read the journal one more time

```bash
$ cat .wiki.journal/journal.jsonl
```

This file is the source of truth for what's happened in your vault.
Skim through and you'll see the full sequence: vault init, primitive
installs (one per primitive the recipe loaded), your ingest route,
and your operation dispatch.

## See also

- **[Tutorial 2 — work-os walkthrough](tutorial-2-work-os-walkthrough.md)**
  — the same flow with the `work-os` recipe, walking the
  stakeholder-update → stakeholder-map-refresh pipeline.
- **[How-to: resolve a conflict](../how-to/resolve-a-conflict.md)** —
  when the kit and disk disagree about a file, the kit writes a
  `.proposed` sidecar; this walks through reconciling it.
- **`wiki journal tail`, `wiki journal grep`, `wiki journal explain`,
  `wiki search`, `wiki upgrade`** — planned commands for a richer
  journal-reading UX. Until they ship, read the journal file directly
  with `cat` or `tail`.
- **The `examples/family-mini/` and `examples/work-os-mini/` vaults**
  in the source repo — populated vaults you can browse for a more
  developed reference (each shaped by the recipe named in its
  directory name).
