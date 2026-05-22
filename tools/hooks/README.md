# Lifecycle hooks

Two agent-lifecycle hooks ship in this directory. Runtime: bash plus
`python3` (already required by the artifact linters and
`check-done.py`); neither depends on any one agent tool's runtime.
Wiring lives in the consumer's hook surface (Claude Code's
`.claude/settings.json`, Gemini CLI's config, etc.); this README
documents the contracts and shows an example wiring.

## What's here

### `session-start.sh`

Runs at the open of an agent session. Reads
`docs/knowledge/patterns.jsonl` and prints the entries — optionally
filtered to a path or narrower glob — so the agent starts with
accumulated patterns / gotchas / antipatterns already in context.

Output goes to stdout as a `=== knowledge ===` block. Empty knowledge
file produces no output and exits 0 — wire it unconditionally; the
hook is a no-op until you start accumulating entries.

Usage:

```bash
bash tools/hooks/session-start.sh                                  # every entry
bash tools/hooks/session-start.sh --scope llm_wiki_kit/journal/    # entries whose stored scope covers this path
```

The `--scope` argument is the caller's path or narrower glob; the
hook returns every entry whose **stored** scope covers it. A caller
of `llm_wiki_kit/journal.py` gets entries scoped to
`llm_wiki_kit/**` plus the repo-wide `*`. An empty or dash-prefixed
value exits 2 with `--scope requires a path or glob value`.

See [`docs/knowledge/README.md`](../../docs/knowledge/README.md) for
the schema and curation conventions.

### `pre-pr.sh`

Runs before a PR opens — the local mirror of CI's artifact-hygiene
checks plus the kit's language gates and the work-loop's mechanical
termination check.

What it runs, in order:

1. `tools/lint-agents-md.sh` — root `AGENTS.md` hygiene, drift-watch
2. `tools/lint-agent-artifacts.sh` — skill/agent/command frontmatter
3. `tools/lint-skill-deps.sh` — manifest dependency resolution
4. `tools/lint-knowledge.sh` — `patterns.jsonl` validation
5. `tools/check-done.py` against every `docs/specs/*/state.json`, in
   both `--phase implement` and `--phase review` modes
6. `ruff check llm_wiki_kit tests` — kit lint (CI parity)
7. `ruff format --check llm_wiki_kit tests` — format check (CI parity)
8. `mypy llm_wiki_kit tests` — type check (CI parity)
9. `pytest` — behaviour

Exits non-zero on the first failure with a one-line reason. If there
are no active `state.json` files, the check-done step is skipped.

These three layers — `check-done.py` (caps) + the four linters
(artifact hygiene) + `pre-pr.sh` (the gate that runs them together) —
make up the project's **enforcement triplet**. Documented in
[`docs/CONVENTIONS.md` § Enforcement](../../docs/CONVENTIONS.md#enforcement-the-triplet).

## Wiring

The hooks are configured at the consumer side. The kit does not
ship a committed `.claude/settings.json` (or equivalent for other
tools) — consumers may want to customize differently, and config
files are not portable across agent tools.

### Claude Code

Add to your project-local `.claude/settings.json` (gitignored):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "bash tools/hooks/session-start.sh" }
        ]
      }
    ]
  }
}
```

`pre-pr.sh` is most useful as a manual or git-hook command rather than
an agent-lifecycle hook — Claude Code doesn't fire on `git push`, so
wire it via `.git/hooks/pre-push` if you want it automatic, or run it
by hand before opening a PR:

```bash
bash tools/hooks/pre-pr.sh
```

### Other tools

Gemini CLI, Codex, Kiro, and other agent tools each have their own
hook surfaces. The scripts are bash plus `python3` — wire whatever
event your tool exposes (session-open, pre-commit, etc.) to invoke
them.

## Testing the hooks

Run them directly against the working tree:

```bash
bash tools/hooks/session-start.sh
bash tools/hooks/pre-pr.sh
```

**CI parity.** `pre-pr.sh` and CI run the same set of checks. CI's
[`.github/workflows/agent-artifacts.yml`](../../.github/workflows/agent-artifacts.yml)
mirrors the enforcement triplet: an `artifacts` job runs the four
linters, a `caps` job exercises `check-done.py` against a seeded
healthy `state.json`, and an `aggregation` job runs `pre-pr.sh`
end-to-end. The existing
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) keeps
running `ruff` / `mypy` / `pytest` on its own; the new workflow does
not duplicate the matrix.
