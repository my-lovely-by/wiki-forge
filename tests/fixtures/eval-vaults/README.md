# Eval-vault seeds

Per-family seed material for `tests/evals/`. Seeds are *built* by
`tests/evals/conftest.py` factories at session start — they are not
pre-baked vaults committed to git. The directories below exist as
documentation hooks (one README per family) and as the home for any
user-content pages a factory needs to copy in.

See `docs/specs/task-20-eval-harness/spec.md` for the contract and
`docs/specs/task-20-eval-harness/plan.md` Step 4 for the factory
shape.

- `minimal/` — core-only vault for trigger evals.
- `weekly-digest/` — core + meeting + weekly-digest, plus one
  fixture meeting page inside the W20 window.
- `research-cited/` — core + meeting + research +
  research-perplexity, for provenance evals.
- `conflict-pending/` — core, then a real drift replay landing a
  `PageProposalEvent` in the journal plus a matching `.proposed`
  sidecar on disk. Per the `wiki-conflict` SKILL's documented
  failure-mode, hand-authored sidecars without a matching event are
  rejected by `wiki doctor` — hence the replay.
- `research-dispatch/` — core + research + research-perplexity,
  for the dispatch-contract scenario (5e) and live Perplexity
  scenario (5f).
