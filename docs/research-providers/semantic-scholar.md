# Semantic Scholar

Academic literature, citation graphs, paper recommendations. The default research provider for "what does the literature say" questions.

## When it shines

- Literature reviews — find the seminal papers in a domain
- Citation backtracking — given a paper, find what cited it and what it cited
- Paper recommendations — given a topic or seed paper, find related work
- Author and venue search — track a researcher or conference
- Medical research with peer-reviewed evidence (family variant)
- Educational deep-dives when a family member is researching a topic

## When not to use

- Current state / news / very recent releases → use [Perplexity](perplexity.md)
- Long-form strategic synthesis → use [Gemini Deep Research](gemini.md)
- Topics where peer-reviewed literature is sparse (most consumer products) → use Perplexity

## Configuration

Edit `.claude/research-providers.yaml`:

```yaml
research_providers:
  semantic_scholar:
    enabled: true
    api_key_env: SEMANTIC_SCHOLAR_API_KEY    # optional but raises rate limits
```

Get a free API key at https://www.semanticscholar.org/product/api. Anonymous calls work but are rate-limited (1 req/sec); authenticated calls get 100 req/sec.

```bash
export SEMANTIC_SCHOLAR_API_KEY="..."
```

## What it returns

Per query:

- A list of relevant papers with paper IDs (S2's stable identifiers), titles, abstracts, authors, year, venue
- For seed papers: the citation graph (what cited the seed; what the seed cited)
- For author queries: paper list across the author's career

The research orchestrator tags the resulting page with `source_kind: paper` and `verification_strength: primary` for peer-reviewed venues or `secondary` for preprints / working papers. The S2 paper IDs populate the source page's `citations:` frontmatter as first-class data — stable identifiers, not just URLs.

## Chronology handling

Each paper has a publication year populated as `published_at:`. The orchestrator surfaces stale literature (e.g., a 2018 benchmark in 2026) as a chronology flag during Synthesize, since methodology can shift quickly in active fields.

## Cost signal: free

Semantic Scholar's API is free. The API key only raises rate limits. Use freely during Capture phase. Pairs well with Perplexity for cross-evidence: Perplexity covers current state, S2 covers evidentiary depth.
