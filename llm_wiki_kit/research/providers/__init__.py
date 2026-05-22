"""Per-provider HTTP dispatchers for ``wiki research``.

Each provider exposes ``dispatch(config: ProviderConfig, query: str) ->
<ProviderResult>`` where the result dataclass is provider-specific
(``answer``, ``citations``, and ``model`` at minimum). The dispatcher
in ``research.dispatch`` holds a module-private registry mapping slug
to thin re-binding wrappers; Task 19 adds Gemini and Semantic Scholar
by editing ``dispatch.py``'s registry directly.

See ``docs/specs/task-18-research-perplexity/spec.md``.
"""
