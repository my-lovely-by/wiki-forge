"""Research dispatch primitives (RFC-0001 Task 18).

The kit-side surface for ``wiki research <query>``. Re-exports the
dispatcher's public API; provider implementations live under
``research.providers`` and the HTTP retry helper under
``research.http``.

The provider registry inside ``dispatch.py`` is module-private
(``_PROVIDER_REGISTRY``); Task 19 extends it by editing that file.

See ``docs/specs/task-18-research-perplexity/spec.md``.
"""

from llm_wiki_kit.research.dispatch import (
    DispatchResult,
    ResearchDispatchError,
    dispatch_query,
)

__all__ = ["DispatchResult", "ResearchDispatchError", "dispatch_query"]
