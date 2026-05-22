# Documentation

Reference docs for `llm-wiki-kit`. Start with the [project README](../README.md) at the repo root for the high-level overview; come here for deeper material.

## Mission, decisions, and direction

- [Charter](CHARTER.md) — mission, scope, principles. The "why" of the project.
- [Conventions](CONVENTIONS.md) — how we work in this repo (workflow, gates, templates).
- [Roadmap](ROADMAP.md) — what's next.
- [Changelog](../CHANGELOG.md) — what's shipped.
- [ADRs](adr/) — load-bearing decisions, frozen once accepted.
- [RFCs](rfc/) — substantive proposals and the v2 migration plan ([RFC-0001](rfc/0001-v2-architecture.md)).
- [Specs](specs/) — per-feature contracts (one directory per feature, each with `spec.md` and `plan.md`).

## Architecture

- [Overview](architecture/overview.md) — the map of the repo: modules, primitive catalog, journal, write-safety layers, the kit-vs-vault distinction. Read this first when exploring the code.

## Guides (Diátaxis)

User-facing documentation, organized by [Diátaxis](https://diataxis.fr/):

- [Tutorials](guides/tutorials/) — step-by-step walkthroughs. Start with [Tutorial 1 — first vault](guides/tutorials/tutorial-1-first-vault.md), then [Tutorial 2 — work-os walkthrough](guides/tutorials/tutorial-2-work-os-walkthrough.md).
- [How-to](guides/how-to/) — task recipes: [Resolve a conflict](guides/how-to/resolve-a-conflict.md), [Set up the Obsidian Web Clipper](guides/how-to/web-clipper.md), [Add a typed inventory](guides/how-to/inventories.md).
- [Reference](guides/reference/) — [File formats](guides/reference/file-formats.md) and other schemas/contracts.
- [Explanation](guides/explanation/) — conceptual background ([Evals](guides/explanation/evals.md)).

## Templates

- [`_templates/`](_templates/) — boilerplate for new ADRs, RFCs, specs, and plans. Used by the `new-adr`, `new-rfc`, and `new-spec` skills.
