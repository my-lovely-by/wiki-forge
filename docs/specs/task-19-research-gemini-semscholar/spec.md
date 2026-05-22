# Spec: Gemini + Semantic Scholar research providers

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit.research.providers.gemini`,
  `llm_wiki_kit.research.providers.semantic_scholar`,
  `templates/infrastructure/research-gemini`,
  `templates/infrastructure/research-semantic-scholar`
- **Related:** RFC-0001 §"Task 19 — Gemini Deep Research + Semantic
  Scholar providers", `docs/specs/task-19-research-gemini-semscholar/plan.md`,
  `docs/specs/task-18-research-perplexity/spec.md` (the precedent this
  spec extends).
- **Constrained by:** ADR-0001 (stdlib rendering), ADR-0002 (journal as
  state truth — additive event-schema only), ADR-0003 (managed
  regions), ADR-0004 (drift detection + `safe_write`), ADR-0005
  (Pydantic for disk-bound schemas), ADR-0006 (additive managed-region
  contributions), ADR-0007 (shared infra config files at vault root).
  AGENTS.md "Runtime dependencies" — no new runtime deps; both
  providers reuse `llm_wiki_kit/research/http.py` (with one
  backwards-compatible default-`None` addition to its `json_body`
  parameter so Semantic Scholar's GET path doesn't ship a body
  the server is not contractually obliged to ignore — see
  §Constraints).

## What this is

Two new infrastructure primitives that plug into the dispatch contract
Task 18 shipped: `infrastructure:research-gemini` and
`infrastructure:research-semantic-scholar`. Each ships one
`primitive.yaml` that declares `requires: [research]` and contributes
one block to the `providers` managed region of
`<vault_root>/research-providers.yaml`, plus one Python module under
`llm_wiki_kit/research/providers/` that exposes
`dispatch(config, query) -> <ProviderResult>` and is registered in the
dispatcher's `_PROVIDER_REGISTRY`. Both primitives stay **opt-in**:
neither appears in `family.yaml`, `work-os.yaml`, or `personal.yaml`.

This spec defines *only* the two new providers, their primitive
templates, the two `_PROVIDER_REGISTRY` entries that route to them,
and one backwards-compatible additive change to
`research/http.py:request_json` — `json_body: dict[str, Any] | None
= None`. The default preserves existing callers' behaviour
(Perplexity continues to send a JSON body); Semantic Scholar's GET
path passes `json_body=None` so no `data=` is attached to the
`urllib.request.Request`, keeping the wire-level shape an honest
GET. The spec does **not** alter the dispatcher's resolution logic
(`research/dispatch.py:dispatch_query` body), the CLI surface
(`wiki research` is unchanged), the `ResearchQueryEvent` schema,
the markdown frontmatter contract, the journal-locking behaviour
around `--out`, or any of the spec/plan artifacts under
`docs/specs/task-18-research-perplexity/`. Every behaviour pinned
by Task 18 holds for these two providers by inheritance.

## Inputs

### From the user (CLI surface)

Unchanged from Task 18:

```
wiki research <query> [--provider <name>] [--out <path>]
```

After both primitives are installed, the new valid `--provider` slugs
are `gemini` and `semantic-scholar` (alongside `perplexity` if
installed). With more than one provider installed, `--provider` is
required — the dispatcher's existing "pass --provider" error path
already lists installed slugs (Task 18 invariant); no new error
message is added here.

### From the vault filesystem

- `./research-providers.yaml` at the vault root. The same flat
  `<provider_slug>: ProviderConfig` mapping Task 18 pinned, now with
  one or two additional blocks contributed by the new primitives.
- `./.wiki.journal/journal.jsonl` — the canonical journal. Unchanged.

### `ProviderConfig` schema

Unchanged from Task 18. Each new provider's contributed block names
its own `api_key_env`, `endpoint`, `model`, `cost_signal`, and
`strengths` values; no new fields are added to the `ProviderConfig`
Pydantic model.

The Semantic Scholar block's `api_key_env: SEMANTIC_SCHOLAR_API_KEY`
field is **declarative**, not a hard requirement: Task 18 already
schema-allowed `api_key_env: str | None = None` precisely so a
keyless provider can ship. The Semantic Scholar `dispatch` reads
`os.environ.get(env_var)` and proceeds either way — see §Behavior
below. The block still declares the env var name so a user who *does*
set it benefits from the elevated rate limit without editing the
managed region.

### From the environment

| Env var | Required? | Read by |
|---|---|---|
| `GEMINI_API_KEY` | **Required.** Missing → `WikiError(f"set GEMINI_API_KEY in the environment")` before any HTTP call. | `gemini.dispatch` |
| `SEMANTIC_SCHOLAR_API_KEY` | **Optional.** When set: sent as the `x-api-key` header and `max_retries` stays at the default 3. When unset: no auth header sent; `max_retries` is bumped to 5 (one extra failure-tolerance for the aggressive keyless tier — see §"HTTP behavior (Semantic Scholar)"). | `semantic_scholar.dispatch` |

Both keys are read at dispatch time only. Neither is logged,
journaled, written to a markdown output page, or stored on any
exception object — same surface-rules as the Perplexity provider
(Task 18 spec invariant 2). The Semantic Scholar key, when sent, is
in the `x-api-key` header (not `Authorization: Bearer`) per the
Semantic Scholar Graph API's documented auth scheme; redaction
treatment is identical.

### From other primitives

Each new provider primitive declares `requires: [research]`. The
dependency edge is load-bearing for the same two reasons Task 18 spec
§"From other primitives" enumerates — seed-file pre-condition (the
aggregator can't write a managed region into a file that doesn't
exist) and topological ordering (`research` strictly before any
`research-*` provider). `_cmd_add`'s requires-closure picks both up;
no CLI changes.

## Outputs

### To stdout (default) / `--out <path>`

Same markdown document the Task 18 dispatcher renders — frontmatter
block (provider, model, query, fetched_at, citations) followed by an
answer body. The new providers each return a result with `answer`,
`citations`, and `model` so the dispatcher's existing
`_ProviderOutput` adapter and `_render_markdown` helper produce the
same shape they do for Perplexity.

The answer body's *content* differs by provider:

- **Gemini** — the assistant's grounded synthesis verbatim, exactly
  as Perplexity's body.
- **Semantic Scholar** — a kit-rendered markdown block summarising
  the top-N papers Semantic Scholar's `paper/search` endpoint
  returned, formatted as a numbered list:

  ```markdown
  1. **<title>** (<year>) — <authors-joined>. *<venue>*. <abstract-or-empty>
     <url>
  2. **<title>** (<year>) …
  ```

  The provider flattens the structured response on its side so the
  dispatcher's `_ProviderOutput.answer` stays a single string. Each
  paper's URL is added to the `citations` list. The body is
  generated by the kit (not by Semantic Scholar) but is committed
  to the vault verbatim when `--out` is used, so the user sees what
  the API actually returned, formatted for reading.

### To the journal

Exactly one `ResearchQueryEvent` per invocation, with the existing
fields. `provider` is `"gemini"` or `"semantic-scholar"`; `model`
holds the provider's resolved model name (Gemini's
`gemini-2.5-pro` or whatever override; Semantic Scholar's literal
`graph-v1` constant, since Semantic Scholar's Graph API has no
"model" concept and a stable placeholder keeps the audit trail
greppable). `status` is `"ok"` or `"error"` per Task 18's contract.
No new event types.

### To `research-providers.yaml`

Each new primitive contributes one block into the `providers`
managed region. After both providers are installed (alongside
Perplexity), the region body contains three top-level keys.
Example after `wiki add infrastructure:research-gemini`:

```yaml
# BEGIN MANAGED: providers
gemini:
  api_key_env: GEMINI_API_KEY
  endpoint: https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent
  model: gemini-2.5-pro
  cost_signal: medium
  strengths:
    - long_synthesis
    - grounded_web_search
# END MANAGED: providers
```

And after `wiki add infrastructure:research-semantic-scholar`:

```yaml
# BEGIN MANAGED: providers
semantic-scholar:
  api_key_env: SEMANTIC_SCHOLAR_API_KEY
  endpoint: https://api.semanticscholar.org/graph/v1/paper/search
  model: graph-v1
  cost_signal: free
  strengths:
    - peer_reviewed
    - structured_metadata
# END MANAGED: providers
```

The aggregator (ADR-0006) composes the snippets in install order
when more than one provider ships into the same region.

## Behavior

### Gemini provider — happy path

1. `dispatch(config, query)` resolves the env-var name from
   `config.api_key_env or "GEMINI_API_KEY"`, then reads
   `os.environ.get(env_var)`. Missing or empty env var raises
   `WikiError(f"set {env_var} in the environment")` — *the
   resolved env-var name, not the literal `"GEMINI_API_KEY"`* —
   *before* any HTTP attempt. A config override
   `api_key_env: MY_GEMINI_KEY` whose backing variable isn't set
   surfaces a message naming `MY_GEMINI_KEY`, mirroring the
   Perplexity provider.
2. Resolves the endpoint URL. If `config.endpoint` is set, use it
   verbatim — the user pinned a model into the URL on purpose.
   Otherwise compose the default URL from `config.model or
   "gemini-2.5-pro"` and the documented Generative Language API base:
   `https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent`.
3. Builds the request:
   - `method = "POST"`
   - Headers: `x-goog-api-key: <api_key>`, `Content-Type: application/json`,
     `User-Agent: llm-wiki-kit/<version>`. The key goes in the header,
     **not** the URL's `?key=` query parameter, so it never lands in
     `urllib`'s connection-log buffers and never leaks into the
     `request.full_url` repr.
   - Body: `{"contents": [{"role": "user", "parts": [{"text": <query>}]}], "tools": [{"google_search": {}}]}`.
     The `google_search` tool toggles Gemini's grounded-generation
     mode — the API responds with citations attached to the
     candidate's `groundingMetadata`. No other knobs (temperature,
     safety settings, system instructions) are passed; the kit's
     contract with Gemini is "ground the answer to web sources, give
     us text plus URLs."
4. Calls `request_json(...)` with the default `max_retries=3`. HTTP
   errors / network failures wrap with the `"gemini: "` prefix and
   re-raise as `ResearchHTTPError`, matching the Perplexity provider
   pattern.
5. Parses the response:
   - `answer` concatenates the `text` value from every entry in
     `candidates[0].content.parts` whose `text` is a string;
     non-text parts (e.g. `inlineData`, `functionCall`,
     `thoughtSignature`) are skipped silently. If *no* part
     carries a string `text`, raises
     `ResearchHTTPError("gemini: malformed response")` — the kit
     cannot synthesize an answer body without text.
   - `citations` is built by iterating
     `candidates[0].groundingMetadata.groundingChunks` and
     including `chunk["web"]["uri"]` **only when** the `chunk`
     is a dict, `chunk["web"]` is a dict, and `chunk["web"]["uri"]`
     is a string. Non-`web` chunk shapes (e.g.
     `retrievedContext` chunks pointing at corpus URIs) and
     malformed chunks are **skipped without raising** — they are
     a normal Gemini response shape, not an error. The collected
     URIs are deduplicated while preserving first-seen order via
     a `seen: set[str]` plus an output `list[str]`. The Gemini
     API also exposes `groundingSupports` linking citation
     ranges to text spans, but the kit's frontmatter contract is
     "flat URL list" — the spans are out of scope (see §Non-goals).
     Missing `groundingMetadata` entirely (Gemini may omit it for
     queries the model answers without grounding) returns
     `citations=[]`, not an error.
   - `model` is the resolved model name passed into the URL (either
     the config override or `gemini-2.5-pro`).

### Semantic Scholar provider — happy path

1. `dispatch(config, query)` reads
   `os.environ.get(config.api_key_env or "SEMANTIC_SCHOLAR_API_KEY")`.
   Result is `None` *or* a string; the missing case is **not** an
   error — Semantic Scholar's Graph API has a keyless tier.
2. Resolves the endpoint URL. If `config.endpoint` is set,
   validate it via `urllib.parse.urlsplit`: reject when the
   parsed result has a non-empty `query`, `fragment`, or
   `username` component. (The first prevents the kit from
   appending `&query=...` onto an existing query; the second
   prevents the `#fragment` from accidentally swallowing the
   appended query; the third prevents `https://user:tok@host/...`
   from leaking credentials into the recorded URL.) On any of
   those, raise `WikiError("research-providers.yaml:
   semantic-scholar endpoint must be a bare scheme://host/path
   (no query, fragment, or userinfo)")` *before* any HTTP
   attempt. The provider owns the full query string; merging an
   override's existing params or fragments is out of scope.
   Default endpoint when override absent:
   `https://api.semanticscholar.org/graph/v1/paper/search`.
3. Composes the GET URL by appending `?` plus
   `urllib.parse.urlencode([("query", query), ("limit",
   str(DEFAULT_LIMIT)), ("fields", DEFAULT_FIELDS)],
   quote_via=urllib.parse.quote_plus)`. The ordered-tuple form is
   the kit's contract — parameter order is `query`, `limit`,
   `fields` so the acceptance-test substring check is exact-match.
   `limit=10` is the kit's default page size — bounded so the
   rendered markdown stays scannable; large result sets are out of
   scope for v0.1 (see §Non-goals).
4. Builds the request:
   - `method = "GET"`
   - Headers: `Content-Type: application/json`,
     `User-Agent: llm-wiki-kit/<version>`. When the key env var is
     set, add `x-api-key: <api_key>`. When it isn't, omit the header
     entirely (do not send an empty string — that gets treated as a
     malformed key by some Semantic Scholar gateway variants and
     produces a 403 instead of a 200).
   - Body: none. The provider passes `json_body=None` to the
     helper so the underlying `urllib.request.Request` is built
     **without** `data=` — the wire request is an honest GET, not
     a GET-with-empty-JSON-body which some HTTP gateways reject
     or silently coerce to POST.
5. Calls `request_json(...)` with `max_retries=3` (key present) or
   `max_retries=5` (key absent). Pinned in §"HTTP behavior (Semantic
   Scholar)" below.
6. Parses the response:
   - The top-level payload must be a `dict` with a `data` key
     whose value is a `list`. Either condition failing →
     `ResearchHTTPError("semantic-scholar: malformed response")`.
   - Each paper renders as one numbered list item per §Outputs.
     The authors field is the comma-joined `name` values from
     `paper.authors[*].name`, including **only** those entries
     where `name` is a non-empty string (Semantic Scholar's
     documented shape is string-only; the filter keeps the kit
     resilient to upstream variant responses without inventing
     defensive logic the API doesn't motivate). An empty author
     list renders the slot as the literal `"unknown authors"`
     so the markdown line stays scannable. Missing scalar fields
     (`year`, `venue`, `abstract`, `url`) render as the empty
     string in their slot.
   - **Degenerate-paper rendering.** If a paper's `title`,
     `year`, `venue`, `abstract`, and `url` are all
     missing/empty/non-string and `authors` is empty (or
     all-non-string), the paper renders as a single line
     `<n>. *(no metadata)*` rather than `<n>. **** () — . **. `.
     This preserves the spec invariant "renders without
     raising" while keeping the body human-readable on a
     malformed response.
   - `citations` is the deduplicated list of `paper.url` values,
     in `data` order, skipping papers whose `url` field is
     missing, empty, or non-string. (The content body still
     includes the paper as a list entry — we don't omit a paper
     from the body just because it has no URL.)
   - If `data` is an empty list, `answer` is the literal string
     `"No papers found.\n"` (the trailing newline is the
     provider's responsibility — `_render_markdown`'s
     newline-normalisation is then a no-op) and `citations = []`.
     This is a valid "ok" outcome — the user asked, the API
     answered "nothing matched."
   - `model` is the literal `"graph-v1"` (the Graph API's v1
     namespace, pinned in `endpoint`); the field is informational
     for the journal and frontmatter only — Semantic Scholar's
     Graph API exposes no per-model knob, so the audit-trail
     field carries a stable namespace identifier instead of a
     model name.

### Body / answer assembly (Semantic Scholar)

The provider builds the `answer` string in-module by iterating
`data` and appending one list item per paper. The final string ends
with a single trailing newline. **No vault writes happen inside the
provider** — the dispatcher's `_render_markdown` wraps the answer in
frontmatter and the CLI decides stdout-vs-`safe_write`. Spec
invariant 4 (safe_write is the only vault-write path) is preserved.

### Error paths (both providers)

| Cause | Surface | Exit | Journaled? |
|---|---|---|---|
| Gemini env var unset | `WikiError("set GEMINI_API_KEY in the environment")` | 2 | no |
| Semantic Scholar env var unset | (no error — proceeds without auth) | n/a | n/a |
| Provider HTTP non-2xx after retries | `WikiError("gemini: HTTP 429 after 3 retries")` / `"semantic-scholar: HTTP 429 after 5 retries"` | 2 | yes, `status="error"` |
| Provider HTTP 4xx (not 429) | `WikiError("gemini: HTTP 401")` / `"semantic-scholar: HTTP 403"` (immediate, no retry — `_RETRY_HTTP_STATUSES` already excludes them) | 2 | yes, `status="error"` |
| Provider malformed JSON | `WikiError("gemini: malformed response")` / `"semantic-scholar: malformed response"` | 2 | yes, `status="error"` |
| Network connect/timeout after retries | `WikiError("gemini: connection failed after 3 retries")` / `"semantic-scholar: connection failed after 5 retries"` | 2 | yes, `status="error"` |
| Config has block but kit version doesn't register the slug | (already handled by Task 18 dispatcher: `WikiError(f"provider '{slug}' has no implementation in this kit version")`) — relevant only when the user installs a future provider primitive against an older kit | 2 | no |

The error-path *journaling* is the dispatcher's responsibility, not
the provider's. Each provider raises `ResearchHTTPError` for
runtime-shaped failures (HTTP, network, malformed) and `WikiError`
(no `ResearchHTTPError`) for config-shaped failures (env var
missing — Gemini only). The dispatcher catches only
`ResearchHTTPError` and wraps it as `ResearchDispatchError`,
carrying the `status="error"` event the CLI then journals. This is
the same provider-author contract Task 18 §"Dispatcher
return-and-raise contract" pinned.

### HTTP behavior (Gemini)

- POST to the configured / composed `generateContent` URL.
- Headers: `x-goog-api-key: <api_key>`, `Content-Type: application/json`,
  `User-Agent: llm-wiki-kit/<version>`.
- Body: `{"contents": [{"role": "user", "parts": [{"text": <query>}]}], "tools": [{"google_search": {}}]}`.
- Timeout: 60 seconds per attempt (the helper's default).
- Retries: helper defaults (`max_retries=3`, backoff `[1, 2, 4]`s on
  429, 5xx, `URLError`, `TimeoutError`).
- Auth via header, never URL. The endpoint default does **not**
  include `?key=`; that style is rejected by spec invariant 2's
  intent (keys must not reach `urllib.request.Request.full_url`).

### HTTP behavior (Semantic Scholar)

- GET to the configured / default `paper/search` URL with
  query-string args: `query=<urlencoded-query>`, `limit=10`,
  `fields=title,authors,year,abstract,url,venue`. Query is encoded
  by `urllib.parse.quote_plus`; the kit does not splice raw user
  text into the URL.
- Headers: `Content-Type: application/json`,
  `User-Agent: llm-wiki-kit/<version>`. `x-api-key: <api_key>` is
  added when the env var is set; omitted otherwise.
- Body: none. The provider passes `json_body=None` (the
  helper's new default after the additive signature change this
  spec makes) so no `data=` lands on the
  `urllib.request.Request`. The wire request is an HTTP/1.1 GET
  with `Content-Length: 0` and no body. Semantic Scholar's API
  documents GET as the endpoint contract; sending a body would
  be HTTP-incorrect even if the gateway tolerates it.
- Timeout: 60 seconds per attempt (helper default).
- Retries: `max_retries=5` when no `SEMANTIC_SCHOLAR_API_KEY` is
  set (keyless rate-limit is aggressive — ~100 reqs / 5 minutes
  per IP — and one stray request can starve the kit for several
  minutes; the extra retries soak that variance);
  `max_retries=3` when the key is set. Backoff is the helper's
  `2 ** attempt` rule, which is parametric on `max_retries` —
  for `max_retries=5` the sleep sequence between attempts is
  `[1, 2, 4, 8, 16]` seconds; for `max_retries=3` it is
  `[1, 2, 4]`. A future jitter / cap addition to the helper
  would break the assertion, so the contract test pins both
  the parametric rule and the produced sequence.

### Dispatcher-side wiring

`research/dispatch.py` gains two new entries in `_PROVIDER_REGISTRY`,
each pointing at a thin re-binding wrapper that re-reads the
provider module's `dispatch` attribute at call time (so
`monkeypatch.setattr(provider_module, "dispatch", fake)` is seen by
the dispatcher — same shape as Task 18's `_call_perplexity`):

```python
def _call_gemini(config, query):
    from llm_wiki_kit.research.providers import gemini
    return gemini.dispatch(config, query)

def _call_semantic_scholar(config, query):
    from llm_wiki_kit.research.providers import semantic_scholar
    return semantic_scholar.dispatch(config, query)

_PROVIDER_REGISTRY = {
    perplexity.PROVIDER_SLUG: _call_perplexity,
    gemini.PROVIDER_SLUG: _call_gemini,
    semantic_scholar.PROVIDER_SLUG: _call_semantic_scholar,
}
```

The registry stays module-private; only the entries change. No
other dispatcher logic moves. The `_ProviderOutput` adapter dataclass
remains the dispatcher's internal normalisation type — each new
provider's result dataclass (`GeminiResult`,
`SemanticScholarResult`) carries `answer`, `citations`, `model` so
the wrapper's `_ProviderOutput(answer=..., citations=..., model=...)`
construction stays one line.

## Invariants

1. **No new runtime dependency lands.** Both providers use
   `urllib.request` via `research/http.py`. The only runtime imports
   outside stdlib remain `pydantic` and `pyyaml`. (AGENTS.md §Runtime
   dependencies.)
2. **API keys never reach disk, logs, journals, or exception
   surfaces.** Same wording as Task 18 invariant 2. Verified per
   provider by `repr()`-grep tests against a recognisable key value
   (`gemini`'s test uses `gk-DO-NOT-LOG`; `semantic-scholar`'s test
   uses `ss-DO-NOT-LOG`).
3. **The Semantic Scholar provider must not send an empty
   `x-api-key` header.** When `os.environ.get(env_var)` returns
   `None` or an empty string, the header is omitted from the
   request dict entirely (verified by a contract test that
   inspects the recorded `headers` dict for the absence of the
   key). An empty `x-api-key` header produces 403 on some
   Semantic Scholar gateway variants; explicit omission is the
   only safe form.
4. **Gemini API keys are sent in `x-goog-api-key`, not the URL.**
   Verified by a contract test asserting (a) the recorded `url`
   does not contain `?key=`, `&key=`, or `?api_key=`, and (b)
   **the literal API-key value** (`gk-DO-NOT-LOG` in the test
   environment) is not a substring of the recorded URL. The
   second assertion catches a future bug where the provider
   builds the URL with a different query-parameter name.
5. **No primitive auto-installs.** None of `family.yaml`,
   `work-os.yaml`, or `personal.yaml` lists
   `infrastructure:research-gemini` or
   `infrastructure:research-semantic-scholar` in `primitives:`.
   Pinned by a static unit test (mirrors Task 18's equivalent for
   `infrastructure:research-perplexity`).
6. **`requires: [research]` is set on both primitives.** A user
   running `wiki add infrastructure:research-gemini` against a
   fresh vault installs both primitives atomically via the
   requires-closure (`cli.py:_expand_closure`). A half-install
   (provider primitive's `primitive.install` journaled, seed
   file missing, aggregator crashes on `FileNotFoundError`) is
   not reachable. Same invariant Task 18 §"From other primitives"
   pinned, repeated per-primitive.
7. **Provider result dataclasses are frozen and contain *only*
   `answer`, `citations`, `model`.** No extra fields land on
   `GeminiResult` or `SemanticScholarResult` even if Gemini /
   Semantic Scholar return more. Extending the result shape would
   require a corresponding change to `_ProviderOutput` and the
   `_render_markdown` frontmatter contract — both out of scope
   for Task 19.
8. **Semantic Scholar's body is *deterministic* given the API
   response.** The same response renders to the same answer
   string byte-for-byte, locale-independent (the snapshot test
   runs under `LC_ALL=C` to defend against future
   locale-sensitive formatting). The expected snapshot string
   is **hand-authored** in the test module as
   `EXPECTED_BODY = "..."` *before* the renderer is written;
   regenerating it from the implementation would be a
   tautology. A renderer-template change requires re-authoring
   the constant in the same commit.
9. **All retried HTTP attempts share the same backoff helper.**
   Both providers use `research.http.request_json` for *every*
   outbound request; neither imports `urlopen`, `Request`, or
   any other `urllib.request` symbol. Verified two ways: an
   AST-grep contract test over `providers/gemini.py` and
   `providers/semantic_scholar.py` source asserting no
   `from urllib.request import …` / `import urllib.request`
   statements; and a `module.__dict__`-introspection check
   asserting `"urlopen"` is not a public name on either module.
   Both tests are scheduled construction tests in plan Steps 2
   and 3, not merely spec aspirations.
10. **Older journal lines still replay.** The `ResearchQueryEvent`
    schema is unchanged (no new fields), so the ADR-0002 additive-
    schema invariant doesn't gain a new surface. Pinned by
    re-running Task 18's
    `test_research_query_event_additive_fields` after this PR.

## Contracts with other modules

| Caller / callee | Contract |
|---|---|
| `research.dispatch._call_gemini` → `gemini.dispatch` | Pass `(config: ProviderConfig, query: str)`; receive `GeminiResult(answer: str, citations: list[str], model: str)`. The provider knows nothing about journals or vaults. Re-raises `ResearchHTTPError` with `"gemini: "` prefix on runtime failure; `WikiError` (not wrapped) on env-var failure. |
| `research.dispatch._call_semantic_scholar` → `semantic_scholar.dispatch` | Pass `(config: ProviderConfig, query: str)`; receive `SemanticScholarResult(answer: str, citations: list[str], model: str)`. Same exception contract minus the env-var-required path (no `WikiError` env-var raise — keyless mode is supported). |
| Both providers → `research.http.request_json` | Pass `(method, url, headers, json_body, timeout, max_retries)`; receive `dict` or raise `ResearchHTTPError`. The retry helper stays provider-agnostic. |
| Provider primitives → managed region | Each declares `contributes_to: [{file: research-providers.yaml, region: providers}]` and ships `regions/research-providers.yaml.providers` with its block. ADR-0006 aggregator composes. |
| Dispatcher → registry | `_PROVIDER_REGISTRY` gains two entries (`"gemini"`, `"semantic-scholar"`). The dispatcher's existing "no implementation in this kit version" path covers a config slug not present in the registry; that case is now triggered only when the user hand-edits an unrecognised slug into the config (no future-spec slug is unhandled by this PR). |

## Acceptance criteria

These are the contract tests Task 19 ships. Construction tests in
plan.md sequence them; here we name the bar for "done."

### Gemini provider (with mocked `urllib`)

- [ ] On HTTP 200 with
      `{"candidates":[{"content":{"parts":[{"text":"<body>"}]},"groundingMetadata":{"groundingChunks":[{"web":{"uri":"https://a"}}, {"web":{"uri":"https://b"}}]}}]}`,
      `dispatch` returns `GeminiResult(answer="<body>",
      citations=["https://a", "https://b"], model=<resolved>)`.
- [ ] The request sends `x-goog-api-key: <api_key>` (header form);
      the recorded URL contains neither `?key=`, `&key=`, nor
      `?api_key=`, and the literal API-key value
      (`gk-DO-NOT-LOG` in tests) is not a substring of the URL.
- [ ] The request body is
      `{"contents": [{"role": "user", "parts": [{"text": <query>}]}], "tools": [{"google_search": {}}]}`.
- [ ] Missing `GEMINI_API_KEY` raises `WikiError(f"set {env_var}
      in the environment")` *without* attempting an HTTP request.
      A `config.api_key_env: MY_GEMINI_KEY` override whose
      backing variable isn't set surfaces a message naming
      `MY_GEMINI_KEY` (the resolved env-var name, not the literal
      default).
- [ ] HTTP 429 → retried 3 times after the initial attempt (4 total)
      with backoff `[1.0, 2.0, 4.0]`; on the fourth failure raises
      `ResearchHTTPError("gemini: HTTP 429 after 3 retries")`.
- [ ] HTTP 401 → raised immediately as `ResearchHTTPError("gemini:
      HTTP 401")` (no retry).
- [ ] Malformed JSON shape (no `parts[*].text` of string type)
      raises `ResearchHTTPError("gemini: malformed response")`.
- [ ] Missing `groundingMetadata` (Gemini may omit it for
      ungrounded answers) returns `citations=[]`, **not** an error.
- [ ] Non-`web` chunk shapes are skipped without raising. A mixed
      `groundingChunks` list
      `[{"web":{"uri":"https://a"}}, {"retrievedContext":{"uri":"corpus://x"}}, {}]`
      yields `citations=["https://a"]`.
- [ ] Multiple text parts concatenate; non-text parts skip. A
      `parts` list `[{"text":"a"}, {"thoughtSignature":"…"},
      {"text":"b"}]` yields `answer="ab"`.
- [ ] Duplicate citation URIs are deduplicated while preserving
      first-seen order. (`groundingChunks` may name the same URI
      twice when the model cited the same source for two spans.)
- [ ] `config.endpoint` override is used verbatim when set; the
      provider does not append a `?key=` query parameter to it.
- [ ] `config.model` override flows into the default-composed URL
      (`.../models/<model>:generateContent`) when `config.endpoint`
      is unset.
- [ ] **Gemini answer is `parts[*].text` joined with the empty
      string** (no separator, no normalisation, no trim). A
      response with `parts: [{"text":"hello"}, {"text":" world"}]`
      yields `answer="hello world"`. Pinned so a future Gemini
      provider tweak (`.join("\n")`, `.strip()`, etc.) is a
      contract break, not an implementation accident.
- [ ] The provider source file imports no symbol from
      `urllib.request` (AST-grep over `gemini.py`); the
      imported module's `__dict__` does not contain a public
      `urlopen` name.
- [ ] The API key never appears in `str(exc)`, `repr(exc)`, or
      `exc.args` for any `ResearchHTTPError` raised by the gemini
      layer. Verified against a recognisable key
      (`gk-DO-NOT-LOG`).

### Semantic Scholar provider (with mocked `urllib`)

- [ ] On HTTP 200 with
      `{"total": 2, "offset": 0, "data": [{"title":"T1","authors":[{"name":"A1"},{"name":"A2"}],"year":2024,"abstract":"X","url":"https://a","venue":"V1"}, {"title":"T2","authors":[],"year":2023,"abstract":"","url":"https://b","venue":"V2"}]}`,
      `dispatch` returns a `SemanticScholarResult` whose `answer`
      equals the snapshot below byte-for-byte (including
      trailing newline), and `citations == ["https://a",
      "https://b"]`. The snapshot is the spec's load-bearing
      reference — `EXPECTED_BODY` in the test file is a verbatim
      copy of *these* bytes, not regenerated from the
      implementation; a renderer drift surfaces as both a spec
      diff and a test diff:

      ```text
      1. **T1** (2024) — A1, A2. *V1*. X
         https://a
      2. **T2** (2023) — unknown authors. *V2*. 
         https://b
      ```

      (Two trailing-space artefacts after `V2*.` are real — the
      template is `{abstract}\n` with `abstract == ""`. A future
      stripper would change the snapshot bytes and must be
      called out in a spec amendment, not slipped in.)
- [ ] An empty `data` list returns `answer="No papers found.\n"` and
      `citations=[]`, with `status="ok"` recorded by the dispatcher
      downstream.
- [ ] The request URL contains `query=<urlencoded-query>`,
      `limit=10`, and `fields=title,authors,year,abstract,url,venue`,
      in that order (the kit composes the query string
      deterministically so the test can assert exact equality).
- [ ] `config.endpoint` containing any of a `?` query string,
      a `#` fragment, or `user:pass@` userinfo component raises
      `WikiError("research-providers.yaml: semantic-scholar
      endpoint must be a bare scheme://host/path (no query,
      fragment, or userinfo)")` *before* any HTTP attempt; the
      recorded `request_json` call count is zero. (One test
      case per rejected form.)
- [ ] With `SEMANTIC_SCHOLAR_API_KEY` set, the request headers
      include `x-api-key: <value>`; `max_retries` is 3.
- [ ] With `SEMANTIC_SCHOLAR_API_KEY` *unset*, the request headers
      do **not** contain an `x-api-key` key (omitted, not empty);
      `max_retries` is 5.
- [ ] The request `json_body` argument passed to `request_json`
      is `None` (so the underlying `urllib.request.Request` is
      built without `data=` — an honest GET).
- [ ] HTTP 429 keyless → retried 5 times after the initial attempt
      (6 total) with backoff `[1, 2, 4, 8, 16]`; on the sixth
      failure raises `ResearchHTTPError("semantic-scholar: HTTP 429
      after 5 retries")`.
- [ ] HTTP 403 → raised immediately (no retry).
- [ ] Malformed JSON shape (top-level non-dict, or missing `data`
      key, or `data` value not a list) raises
      `ResearchHTTPError("semantic-scholar: malformed response")`.
- [ ] Per-paper missing scalar fields (no abstract, no venue, no
      year, no url) render as empty-string slots without raising;
      a paper with no `url` is *included* in the body but *not*
      in `citations`.
- [ ] A paper with **all** scalar fields missing/empty and no
      string authors renders as `<n>. *(no metadata)*`; the
      deterministic-rendering snapshot pins this case.
- [ ] An empty `authors` list renders the author slot as
      `"unknown authors"` (pinned in the snapshot).
- [ ] Connection timeout / `URLError` keyless → retried 5 times,
      eventually raised as `ResearchHTTPError("semantic-scholar:
      connection failed after 5 retries")`.
- [ ] The provider source file imports no symbol from
      `urllib.request` (AST-grep over `semantic_scholar.py`);
      the imported module's `__dict__` does not contain a
      public `urlopen` name.
- [ ] The API key (when set) never appears in `str(exc)`,
      `repr(exc)`, or `exc.args` of any `ResearchHTTPError` raised.
      Verified against `ss-DO-NOT-LOG`.

### Dispatcher integration

- [ ] `dispatch_query("q", "gemini", vault_root, now=...)` against a
      vault with `gemini:` installed and `gemini.dispatch` patched
      returns a `DispatchResult` whose `event.provider == "gemini"`
      and `event.model == "gemini-2.5-pro"` (the resolved model).
- [ ] `dispatch_query("q", "semantic-scholar", vault_root, now=...)`
      against a vault with `semantic-scholar:` installed and
      `semantic_scholar.dispatch` patched returns a `DispatchResult`
      whose `event.provider == "semantic-scholar"` and
      `event.model == "graph-v1"`.
- [ ] With Perplexity + Gemini + Semantic Scholar all installed and
      no `--provider`, the dispatcher raises `WikiError("pass
      --provider")` whose message lists all three slugs sorted.
- [ ] `--provider gemini` resolves to the gemini wrapper; the
      registry's `_call_gemini` is the entry the dispatcher invokes
      (verified by patching `gemini.dispatch` and observing the
      patched function fires).

### Primitives + recipes

- [ ] `load_primitive(templates/infrastructure/research-gemini)`
      yields a `Primitive` with `kind=infrastructure`,
      `requires=["research"]`,
      `contributes_to=[{file: "research-providers.yaml", region: "providers"}]`,
      and a snippet file at
      `regions/research-providers.yaml.providers` containing the
      gemini block.
- [ ] `load_primitive(templates/infrastructure/research-semantic-scholar)`
      yields the equivalent shape with the `semantic-scholar` block.
- [ ] `install.validate_contributions(primitive, root)` passes for
      both primitives.
- [ ] After `wiki init --recipe family && wiki add
      infrastructure:research-gemini`, the rendered
      `research-providers.yaml`'s `providers` region YAML-parses to
      `{"gemini": ProviderConfig(api_key_env="GEMINI_API_KEY",
      endpoint=..., model="gemini-2.5-pro", cost_signal="medium",
      strengths=[...])}`.
- [ ] After both `wiki add infrastructure:research-gemini` and
      `wiki add infrastructure:research-semantic-scholar`, the
      region YAML-parses to a two-key mapping with both blocks
      validating cleanly.
- [ ] Static check: `family.yaml`, `work-os.yaml`, and
      `personal.yaml`'s `primitives:` lists do **not** contain
      `infrastructure:research-gemini` or
      `infrastructure:research-semantic-scholar`. (One unit test
      per recipe, mirroring Task 18.)

### CLI end-to-end (integration)

- [ ] `wiki research "q" --provider gemini` against a vault with
      both `infrastructure:research` and
      `infrastructure:research-gemini` installed, `GEMINI_API_KEY`
      set in the test env, and `urlopen` patched to return a
      canonical Gemini response, prints the expected markdown to
      stdout (frontmatter `provider: gemini`, `model:
      gemini-2.5-pro`, citations from `groundingChunks`, body from
      `parts[0].text`) and journals one
      `ResearchQueryEvent(provider="gemini", status="ok")`.
- [ ] `wiki research "q" --provider semantic-scholar` against a
      vault with both primitives installed, **no**
      `SEMANTIC_SCHOLAR_API_KEY` set, and `urlopen` patched to
      return a canonical Semantic Scholar response, prints the
      expected markdown (numbered list body, `citations` of paper
      URLs) and journals one `ResearchQueryEvent(provider="semantic-scholar",
      model="graph-v1", status="ok")`.
- [ ] `wiki research "q" --provider gemini` with `GEMINI_API_KEY`
      unset exits 2 with `"set GEMINI_API_KEY in the environment"`
      and journals **no** `research.query` event (config-shaped
      error per Task 18 §"Error paths").

## Non-goals

- **Streaming output.** Gemini's API supports streaming via
  `streamGenerateContent`; the kit reads the batch
  `generateContent` endpoint only. A `--stream` flag is a future
  CLI change with its own contract.
- **Grounding span metadata.** Gemini's
  `groundingMetadata.groundingSupports` links citation ranges to
  text spans. The kit's frontmatter contract is "flat URL list";
  exposing span metadata would change the markdown contract, which
  Task 18 pinned.
- **Multi-page Semantic Scholar pagination.** `limit=10`, no
  `offset` flag, no `--limit` CLI knob. A future task can add it;
  v0.1 reads the first page.
- **Per-paper full text or PDF retrieval.** Semantic Scholar's
  `paper/<id>/pdf` and similar are not used. The kit gives the
  user URLs; what they do with them is out of scope.
- **A unified "research synthesis" output across providers.** Each
  provider returns what it returns; the kit does not normalise
  Gemini's prose against Semantic Scholar's table.
- **Picker / scoring across the three providers.** Already
  out-of-scope per Task 18 §Non-goals; this spec doesn't expand
  the surface.
- **Vault-side `wiki-research` SKILL.md.** Same Task 18 deferral.
- **Cross-provider citation deduplication.** Each provider returns
  its own `citations` list; the kit does not attempt to merge them.
- **Per-field escaping of Semantic Scholar response data.** The
  kit-rendered paper list embeds `title`, `authors[*].name`,
  `venue`, `abstract`, and `url` verbatim into the markdown body.
  Task 18's `Non-goals` already covered stored prompt injection
  from provider answers (OWASP-LLM01) for Perplexity's single
  freeform answer field; Semantic Scholar widens the surface to
  five untrusted fields per paper × `DEFAULT_LIMIT=10` papers.
  The trust contract is unchanged ("the kit treats provider
  responses as user-readable content, not as instructions") but a
  future hardening pass might (a) escape backticks / pipes /
  bare-`---` lines in `_scalar()`, or (b) wrap the rendered paper
  list in an "untrusted content" fence the vault-side
  `wiki-research` SKILL can teach Claude to honor. Both are
  scoped to a follow-up; the v0.1 contract is verbatim.
- **A result-count signal on `ResearchQueryEvent`.** Semantic
  Scholar's empty-data path emits a `status="ok"` event whose
  `query` field carries the user's query but no indication that
  the search returned zero papers. A future additive `n_results:
  int | None = None` field under ADR-0002's additive rule could
  surface that signal; out of scope for Task 19.
- **`gemini-deep-research` as a separate Gemini model.** Google's
  "Deep Research" is the consumer-product feature built on top of
  `gemini-2.5-pro` + grounding; the API surface this spec uses is
  the closest equivalent. If a dedicated Deep Research API endpoint
  ever ships, a follow-up spec adds it as either a new provider
  primitive or a `config.model` override.
- **Allowlist for `endpoint` host values.** Same Task 18
  deferral — single-user CLI on a trusted endpoint; SSRF protection
  is a future ADR.
- **GET-specific paths in `request_json`.** The helper's POST-with-
  empty-body shape is enough for Semantic Scholar today. A second
  GET caller would justify refactoring the helper; one isn't
  enough.

## Constraints

- **No new module boundary beyond
  `llm_wiki_kit/research/providers/`.** Each new provider adds one
  file (`gemini.py`, `semantic_scholar.py`) under the existing
  `providers/` package. The dispatcher gains two private wrappers
  + two registry entries in `dispatch.py`. Nothing else moves.
- **No new top-level directory or package.** `templates/infrastructure/`
  already exists (Task 18 created it); this PR adds two
  subdirectories.
- **No new runtime dependency.** Stdlib only for HTTP and JSON.
- **No bypass of `safe_write`.** The two new providers don't write
  to disk; only the dispatcher's `--out` path goes through
  `safe_write`, unchanged.
- **No new public CLI verb or flag.** `wiki research` is unchanged.
  `--provider gemini` / `--provider semantic-scholar` are
  resolved against the existing flag — no new argparse surface.
- **No change to `ResearchQueryEvent`'s field set.** The event
  schema Task 18 froze is sufficient. Per-provider `model` values
  (`gemini-2.5-pro`, `graph-v1`) are recorded in the existing
  `model` field.
- **One backwards-compatible additive change to
  `research/http.py:request_json`.** The `json_body` parameter
  gains a `| None` annotation and a default of `None`; when
  `None`, the helper builds the `urllib.request.Request` without
  a `data=` argument so the wire request has no body (an honest
  GET for Semantic Scholar; existing Perplexity calls supply a
  dict and continue unchanged). No other signature changes, no
  new `backoff_seconds` parameter, no per-status retry overrides.
  A future spec can extend further; this PR's scope is exactly
  the additive `json_body` default.
- **Provider slug = snake-case → kebab-case rule from Task 18 stands.**
  `gemini` is single-word; `semantic-scholar` is kebab-case (matches
  the directory name `templates/infrastructure/research-semantic-scholar/`).
  The Python module is `semantic_scholar.py` (snake_case per Python
  convention); the registry key is `"semantic-scholar"`; the user-
  facing `--provider` value is `semantic-scholar`. Module-to-slug
  mapping lives in each provider module's `PROVIDER_SLUG` constant,
  same as Perplexity.
