# examples/

Three committed, regenerable vaults you can browse to see what
`llm-wiki-kit` produces. They exist so a non-engineer can `cd` into a
folder, run `wiki doctor`, and read a couple of pages to understand the
shape — without having to run `wiki init` first.

| Vault                | Recipe     | Doctor exit | What it shows                                                                 |
|----------------------|------------|-------------|-------------------------------------------------------------------------------|
| `family-mini/`       | `family`   | clean       | Household OS — people, meals, medical, trips, vendors, receipts, taxes, actions. |
| `work-os-mini/`      | `work-os`  | clean       | Professional OS — stakeholders, projects, customers, domains, decisions, meetings. |
| `conflict-pending/`  | `personal` | reports `pending-proposal` | A vault with one drifted page — the worked example for `docs/guides/how-to/resolve-a-conflict.md`. |

All three are produced by `regenerate.py`. They are committed to the
repo (not gitignored) so a reader cloning the repo sees them
immediately, but they are *not* hand-edited — the only way to change
their contents is to edit a recipe / primitive / seed page and re-run
the regenerator.

## Layout

```
examples/
├── README.md            # this file
├── regenerate.py        # rebuild all three vaults
├── _seed/               # hand-authored seed pages copied into the rendered vaults
│   ├── family/wiki/<area>/<page>.md
│   └── work-os/wiki/<area>/<page>.md
├── family-mini/         # rendered output for the family recipe + family seeds
├── work-os-mini/        # rendered output for the work-os recipe + work-os seeds
└── conflict-pending/    # rendered output for the personal recipe + one drifted page
```

Seed pages are plain markdown with the YAML frontmatter shape each
content-type primitive expects. The regenerator copies them into the
rendered vault via the kit's own `safe_write` so every seed page lands
with a matching `PageWriteEvent` in the journal.

## Regenerating

The regenerator has two modes:

```sh
# Verify the committed trees match a fresh rebuild. CI runs this.
python examples/regenerate.py --check

# Rebuild the committed trees in place. Run this after you edit a
# recipe, a primitive's templated files, or a `_seed/<recipe>/` page.
python examples/regenerate.py --apply
```

`--apply` builds each vault into a temp directory, then swaps it
into place via a two-`os.rename` sequence (committed → backup,
staged → committed). POSIX disallows a single atomic replace of a
non-empty directory, so the swap has a sub-millisecond window where
the committed path is absent; an in-process failure during the
second rename is rolled back automatically. See
`examples/regenerate.py::apply_vault` for the full contract.

Multi-vault runs are not transactional: if vault #2's build fails
after vault #1 has already swapped, vault #1 retains its new bytes
while vaults #2 and #3 retain their old bytes. Re-run `--apply`
after fixing the failing vault to converge.

## Tutorials and how-tos that point here

- `docs/guides/tutorials/tutorial-1-first-vault.md` — builds a vault
  from scratch; does not depend on any committed example vault.
- `docs/guides/tutorials/tutorial-2-work-os-walkthrough.md` — the
  reader produces a vault whose shape mirrors `work-os-mini/`.
- `docs/guides/how-to/resolve-a-conflict.md` — operates on a copy of
  `conflict-pending/`.

## Why `conflict-pending/` uses the `personal` recipe

`conflict-pending/`'s job is showing a `.proposed` sidecar in action —
not a recipe-shape. The `personal` recipe is the smallest of the three
shipped recipes, so the example vault stays compact while still
carrying a real `PageProposalEvent` in its journal. The asymmetric name
(no `personal-mini`) is intentional; see
`docs/specs/task-21-examples-tutorials/spec.md` §Constraints.
