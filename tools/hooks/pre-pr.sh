#!/usr/bin/env bash
# Pre-PR hook: runs every artifact linter, the work-loop's mechanical
# termination check against any active spec state, and the kit's
# language gates (ruff / mypy / pytest). Exits non-zero on the first
# failure so a contributor can't open a PR whose artifacts are
# inconsistent with the conventions, or whose Python doesn't pass CI.
#
# What it runs:
#   - tools/lint-agents-md.sh        — root AGENTS.md hygiene, drift-watch
#   - tools/lint-agent-artifacts.sh  — skill/agent/command frontmatter
#   - tools/lint-skill-deps.sh       — manifest dependency resolution
#   - tools/lint-knowledge.sh        — docs/knowledge/patterns.jsonl
#   - tools/check-done.py            — for each docs/specs/*/state.json,
#                                       --phase implement and --phase review
#   - ruff check llm_wiki_kit tests
#   - ruff format --check llm_wiki_kit tests
#   - mypy llm_wiki_kit tests
#   - pytest
#
# The ruff/mypy/pytest commands mirror .github/workflows/ci.yml so the
# local gate catches what CI catches. Mypy includes tests/ because the
# CI job does — running the narrower `mypy llm_wiki_kit/` would let
# test-only type errors through.
#
# Runtime: bash + python3 (already required by the artifact linters and
# check-done.py) + the kit's dev deps (`pip install -e .[dev]`). Wiring
# lives in each tool's hook surface (Claude Code: .claude/settings.json;
# see tools/hooks/README.md).

# Intentionally no `set -e`: every command flows through `run` (which
# exits on failure) or an explicit `if ! …` guard, and the explicit
# control is what lets us print captured output on failure.
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

# Suppress output on success (keeps the summary tight) but dump the
# captured stdout+stderr on failure so the contributor sees what
# ruff/mypy/pytest actually complained about without re-running. Also
# surface any `⚠` warn-only lines on success — the lint scripts use
# them for non-fatal signals (stale doc, drift watch) and silencing
# them defeats the purpose.
run() {
  local label="$1"
  shift
  local out
  if ! out=$("$@" 2>&1); then
    echo "pre-pr: ✖ $label failed" >&2
    if [[ -n "$out" ]]; then
      printf '%s\n' "$out" >&2
    fi
    exit 1
  fi
  echo "pre-pr: ✓ $label"
  if [[ -n "$out" ]] && printf '%s\n' "$out" | grep -q '⚠'; then
    printf '%s\n' "$out" | grep '⚠' >&2
  fi
}

run "agents-md hygiene"    bash tools/lint-agents-md.sh
run "agent-artifact lint"  bash tools/lint-agent-artifacts.sh
run "skill-deps lint"      bash tools/lint-skill-deps.sh
run "knowledge lint"       bash tools/lint-knowledge.sh

shopt -s nullglob
state_files=(docs/specs/*/state.json)
shopt -u nullglob

if (( ${#state_files[@]} == 0 )); then
  echo "pre-pr: (no active state.json — skipping check-done)"
else
  for state in "${state_files[@]}"; do
    for phase in implement review; do
      if ! python3 tools/check-done.py "$state" --phase "$phase" > /dev/null; then
        echo "pre-pr: ✖ check-done.py $state --phase $phase failed" >&2
        exit 1
      fi
      echo "pre-pr: ✓ check-done $state ($phase)"
    done
  done
fi

run "ruff check"           ruff check llm_wiki_kit tests
run "ruff format check"    ruff format --check llm_wiki_kit tests
run "mypy"                 mypy llm_wiki_kit tests
run "pytest"               pytest

echo "pre-pr: all checks passed"
