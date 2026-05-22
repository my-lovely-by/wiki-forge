# Tutorial 2 — A work-os walkthrough

You've worked through [Tutorial 1](tutorial-1-first-vault.md). Now
you'll see what a richer recipe — `work-os` — looks like, and walk one
content-type and one operation in depth: a stakeholder update flowing
into a stakeholder-map refresh. About 30 minutes of doing time. No API
keys required.

## Prerequisites

- You've read [Tutorial 1](tutorial-1-first-vault.md). This tutorial
  reuses that vocabulary.
- **Python 3.11 or newer** and **`llm-wiki-kit`** installed; see
  Tutorial 1's prerequisites.

The reference output: when you're done, your vault has the same
*shape* (areas, skills, schema) as `examples/work-os-mini/` in the
repo. The content is yours, not pre-baked.

## Step 1 — Initialize the vault

```bash
$ wiki init my-work-os --recipe work-os
$ cd my-work-os
$ wiki doctor
```

`wiki doctor` exits 0 — your fresh vault is clean.

## Step 2 — Orient yourself

The `work-os` recipe lays down more areas than the `personal` recipe
did. Take a look at the wiki tree:

```bash
$ ls wiki/
```

You'll see directories for `people`, `meetings`, `projects`,
`customers`, `domains`, `stakeholder-updates`, `decisions`,
`interviews`, `customer-feedback`, `vendor-contracts`. Each
corresponds to a primitive the recipe installed.

```bash
$ ls skills/
```

These are the skills your Claude Code session will load when you work
in this vault. Each ships with a `SKILL.md` Claude reads to understand
what the skill does.

## Step 3 — Ingest a stakeholder update

The work-os recipe's primary content-type for capturing executive
context is `stakeholder-update`. Let's feed it one.

Create a source:

```bash
$ mkdir -p raw
$ printf 'Priya wants Atlas migration done by end of Q2. Renewals secondary.\n' > raw/q3-board-sync.md
```

Route it:

```bash
$ wiki ingest --as stakeholder-update raw/q3-board-sync.md
```

The kit prints the dispatch line and appends an `ingest.routed` event
to the journal:

```bash
$ tail -1 .wiki.journal/journal.jsonl
```

This is the same shape you saw in tutorial 1 — the CLI's job is the
boundary (route + journal); the actual page synthesis happens in
Claude:

```bash
> Read raw/q3-board-sync.md and run the ingest-stakeholder-update
> skill to produce a page under wiki/stakeholder-updates/.
```


## Step 4 — Run the stakeholder-map-refresh operation

Once you have stakeholder-update pages in the vault (one is enough),
the `stakeholder-map-refresh` operation rebuilds a synthesis of who
matters, what they care about, and what's open with each:

```bash
$ wiki run stakeholder-map-refresh
```

The kit dispatches and journals. Confirm in the journal:

```bash
$ tail -1 .wiki.journal/journal.jsonl
```

In your Claude Code session:

```bash
> Run the stakeholder-map-refresh skill — pull from the
> wiki/stakeholder-updates/ pages and write to wiki/people/.
```

## Step 5 — Inspect the journal

```bash
$ ls .wiki.journal/
$ cat .wiki.journal/journal.jsonl
```

You'll see the full sequence of what just happened: `vault.init`, one
`primitive.install` per primitive the recipe loaded, your
`ingest.routed` event, and your `operation.run` event. Your vault now
has the same shape as `examples/work-os-mini/` (different content,
same areas). If you cloned the repo, open that example in another
window for a side-by-side.

## See also

- **[Tutorial 1 — Create your first vault](tutorial-1-first-vault.md)**
  — the foundational walkthrough this tutorial builds on.
- **[How-to: resolve a conflict](../how-to/resolve-a-conflict.md)** —
  when the kit and disk disagree, the kit writes a `.proposed`
  sidecar; this walks through reconciling it.
- **`wiki research`** — the kit can dispatch research queries to
  Perplexity / Gemini / Semantic Scholar (each requires its own API
  key). Out of scope for this tutorial; see the research provider's
  module docstring in `llm_wiki_kit/research/` for the setup.
- **Tutorial 3 (family walkthrough)** — coming next. The family
  recipe focuses on household OS (meals, medical, trips, vendors).
- **The `examples/work-os-mini/` vault** in the repo — a populated
  vault with seed pages across every area for browsing.
