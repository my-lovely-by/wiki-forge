# Perplexity

Current-state web research with first-class citations. The default research provider for "what's happening now" questions — vendor benchmarks, recent product releases, news, current best practices.

## When it shines

- "Current state of X" — news, releases, market state
- "Compare {tool A} vs {tool B} as of today"
- Vendor benchmarks and feature comparisons
- Cited factual lookups where source attribution matters
- Local services and product comparisons (family variant)

## When not to use

- Academic-literature questions → use [Semantic Scholar](semantic-scholar.md)
- Long-form strategic synthesis spanning hundreds of sources → use [Gemini Deep Research](gemini.md)
- Specific known URLs → use [[ingest-website]] (no research effort needed)

## Configuration

Edit `.claude/research-providers.yaml`:

```yaml
research_providers:
  perplexity:
    enabled: true
    api_key_env: PERPLEXITY_API_KEY
    model: sonar-pro     # or sonar (cheaper) or sonar-deep-research (more thorough)
```

Set the API key in your shell environment:

```bash
export PERPLEXITY_API_KEY="..."
```

Free anonymous tier works for very low volume but is rate-limited. The Starter tier ($0.003/fetch, 60 req/min, $1 free credit) is recommended for active use.

## What it returns

Per query, the provider returns:

- A direct answer to the query
- A list of source URLs with snippets — these populate the source page's `citations:` frontmatter as first-class data, not just inline footnotes

The research orchestrator tags the resulting page with `source_kind: web` and assigns `verification_strength` based on the citation set: `primary` if the citations themselves point to vendor docs / official sources / peer-reviewed venues; `secondary` for industry blogs and consolidator sites; `hearsay` if the citations are weak.

## Chronology handling

Each Perplexity result is freshly fetched, so `published_at:` reflects the *cited articles*' publication dates (not the API call date). The orchestrator surfaces stale citations during Synthesize.

## Cost signal: low

Perplexity calls are cheap and fast. The orchestrator can run several per research project without budget concern. Use it freely during the Capture phase, especially for the entity inventory and attribute datapoints.
