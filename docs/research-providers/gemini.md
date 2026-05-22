# Gemini Deep Research

Long-form exhaustive synthesis spanning hundreds of sources. The research provider for strategic landscape analyses where breadth and synthesis matter more than speed.

## When it shines

- Comprehensive landscape analyses — "the state of agentic AI tooling in 2026"
- Strategic deep dives where you need a 30-50 page report, not a 1-page answer
- Multi-source synthesis where the value is in *connecting* sources, not retrieving them
- Cross-cutting questions that span disciplines

## When not to use

- Quick questions — overkill; use [Perplexity](perplexity.md)
- Academic citation work — use [Semantic Scholar](semantic-scholar.md)
- Anything time-sensitive — Deep Research takes minutes per query, not seconds
- Routine attribute lookups — burn rate is too high

## Configuration

Edit `.claude/research-providers.yaml`:

```yaml
research_providers:
  gemini:
    enabled: true
    api_key_env: GOOGLE_API_KEY
    model: gemini-2.5-pro
```

Get an API key at https://aistudio.google.com/.

```bash
export GOOGLE_API_KEY="..."
```

## What it returns

A long-form report with embedded citations. The research orchestrator extracts:

- The report's own claims as `key_claims:` (each one becomes a candidate verdict claim)
- Cited URLs and source documents as `citations:`
- Verifying chronology where the report references publication dates

The resulting source page is `source_kind: report` with `verification_strength: secondary` (Gemini synthesizes; primary corroboration still requires checking cited sources directly via Perplexity or S2 follow-ups).

## Cost signal: high

Gemini Deep Research is the kit's most expensive research provider. Reserve for the most important pillar gaps — the load-bearing verdict claim, the strategic context-setting, the cross-cutting synthesis. The orchestrator's strategy-selection logic checks `cost_signal: high` and prefers cheaper providers for routine attribute lookups.

A typical research project should use Gemini once or twice (for orientation at the start, possibly once at the end for synthesis verification), not as a default capture tool.

## Variant defaults

- **Work variant:** enable when doing quarterly strategy work, board-level analyses, major architectural pivots, or comprehensive market landscape work.
- **Family variant:** typically disabled. Most family research efforts don't need this depth (or cost). Enable case-by-case for major decisions like multi-kid school enrollment, multi-year financial plans, or complex medical workups where literature spans several disciplines.
