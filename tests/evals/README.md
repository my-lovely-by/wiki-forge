# tests/evals/

Eval suite — pytest tests that drive `claude` via subprocess against
fixture vaults, or drive the kit's CLI directly to assert on the
dispatcher's contract. Every file carries `pytestmark =
pytest.mark.eval`; the suite runs in its own GitHub Actions
workflow (`.github/workflows/evals.yml`).

- Spec: [`docs/specs/task-20-eval-harness/spec.md`](../../docs/specs/task-20-eval-harness/spec.md)
- Authoring guide: [`docs/guides/explanation/evals.md`](../../docs/guides/explanation/evals.md)
- Run locally: `pytest tests/evals -m eval` (requires
  `ANTHROPIC_API_KEY` + `claude` on `$PATH` for the integrated-
  journey scenarios; the dispatch-contract scenario at
  `research/test_dispatch_contract.py` runs without any env vars).
