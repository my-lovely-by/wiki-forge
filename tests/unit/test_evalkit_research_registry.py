"""Provider-agnosticism unit test (RFC-0001 Task 20).

The eval 5e dispatch-contract scenario could in principle assert
that every Task 19 provider stays wired up, but doing so at the
eval level requires installing each ``infrastructure:research-*``
primitive in the factory and monkeypatching each module — heavy
ceremony for what is structurally a registry check. We assert it
here directly against ``_PROVIDER_REGISTRY``.

Spec: docs/specs/task-20-eval-harness/spec.md
Plan: docs/specs/task-20-eval-harness/plan.md Step 5 §"Additional deliverable"
"""

from __future__ import annotations

from llm_wiki_kit.research.dispatch import _PROVIDER_REGISTRY
from llm_wiki_kit.research.providers import gemini, perplexity, semantic_scholar


def test_perplexity_provider_registered() -> None:
    assert perplexity.PROVIDER_SLUG in _PROVIDER_REGISTRY


def test_gemini_provider_registered() -> None:
    assert gemini.PROVIDER_SLUG in _PROVIDER_REGISTRY


def test_semantic_scholar_provider_registered() -> None:
    assert semantic_scholar.PROVIDER_SLUG in _PROVIDER_REGISTRY
