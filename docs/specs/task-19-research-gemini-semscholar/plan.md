# Plan: Gemini + Semantic Scholar research providers

> **Implementation plan paired with `spec.md`.** The spec says *what*;
> the plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** `docs/specs/task-19-research-gemini-semscholar/spec.md`
- **Owner:** primary loop on
  `eugenelim/v2-task-19-research-gemini-semscholar`

## Approach

One PR, six steps, TDD-default. Build inside-out: the additive
`request_json(json_body=None)` change first (the helper's contract
extension Semantic Scholar's GET path depends on), then Gemini
(simpler — same shape as Perplexity), then Semantic Scholar (the
variable-retry + keyless-tier path), then the registry wiring, then
the two template primitives, finally the end-to-end CLI integration
tests.

Conflict surface vs. Task 18 is small: `research/http.py` gains one
default-`None` parameter (additive); `research/dispatch.py` gains
two wrappers and two registry entries (additive). `models.py`,
`cli.py`, the markdown renderer, and the `ResearchQueryEvent` schema
are unchanged. No ADR is needed — ADR-0007 already covers vault-root
config placement; no new runtime dep lands.

## Pre-conditions

- `eugenelim/v2-task-19-research-gemini-semscholar` branched off
  `origin/main` (already done — current branch).
- `gh auth status` shows `eugenelim` as active (verified).
- Task 18 has shipped on main (it has — commit 9808170). The
  dispatcher, HTTP helper, Perplexity provider, the seed primitive
  `infrastructure:research`, and `ResearchQueryEvent`'s `model` /
  `status` fields are all in place.
- No test runs require live `GEMINI_API_KEY` or
  `SEMANTIC_SCHOLAR_API_KEY` — every HTTP test patches
  `urllib.request.urlopen` or `request_json` directly.

## Steps

Each step's `Tests:` block comes before `Approach:` per CONVENTIONS
§"Contract tests vs. construction tests". Construction tests are in
`tests/unit/...` or `tests/integration/...`; contract tests are the
acceptance criteria in `spec.md`.

### Step 1 — `research.http.request_json` accepts `json_body=None` and omits `data=` from the underlying `Request` when so.

**Depends on:** none.

**Verification mode:** TDD.

**Tests** (extend `tests/unit/test_research_http.py`):

- `test_request_json_none_body_omits_data_arg` — patch `urlopen`
  with a recorder that captures the `Request` instance.
  Call `request_json(method="GET", url="https://api.example/x",
  headers={}, json_body=None)`. Assert the captured
  `request.data is None` and `request.get_method() == "GET"`.
- `test_request_json_dict_body_unchanged` — same recorder; call
  with `json_body={"k": 1}`. Assert `request.data ==
  b'{"k": 1}'` (the existing contract; preserves the
  Perplexity callers' behaviour byte-for-byte).
- `test_request_json_empty_dict_body_still_sends_data` —
  `json_body={}` produces `request.data == b'{}'`. The
  distinction between "no body" and "empty JSON object body" is
  preserved.
- `test_request_json_default_json_body_is_none` — call
  `request_json(method="GET", url=..., headers={})` with no
  `json_body=` kwarg; assert `request.data is None`. Pins the
  signature change includes a default value, not just a wider
  type annotation.

**Approach:** Single signature change in
`llm_wiki_kit/research/http.py`:

```python
def request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
```

Inside the loop, replace the unconditional
`body_bytes = json.dumps(json_body).encode("utf-8")` plus
`Request(url=url, data=body_bytes, ...)` with:

```python
if json_body is None:
    body_bytes = None
else:
    body_bytes = json.dumps(json_body).encode("utf-8")
...
request_kwargs: dict[str, Any] = {"url": url, "headers": dict(headers), "method": method}
if body_bytes is not None:
    request_kwargs["data"] = body_bytes
request = Request(**request_kwargs)
```

(Or build the Request unconditionally and conditionally pass
`data=`; either shape is fine — the construction-test recorder
asserts the resulting `request.data`.)

The docstring gains a one-line note: "Pass `json_body=None` for
true GETs that should not ship a body. Defaults to `None`; the
Perplexity provider passes a dict and continues to send JSON."

**Gate:** `pytest tests/unit/test_research_http.py && mypy
llm_wiki_kit/research/http.py && ruff check
llm_wiki_kit/research/http.py`. No existing Perplexity test
should change behaviour — verified by also running
`pytest tests/unit/test_research_perplexity.py`.

---

### Step 2 — `providers/gemini.py:dispatch` calls the Gemini API correctly, parses the grounded response, never logs the key.

**Depends on:** Step 1 (the helper signature accommodates the
provider call; though Gemini does pass a JSON body, having the
signature already widened means the provider doesn't drift).

**Verification mode:** TDD.

**Tests** (`tests/unit/test_research_gemini.py`, new file):

- `test_gemini_dispatch_happy_path` — fixture sets `GEMINI_API_KEY`
  via `monkeypatch.setenv`. Patch `gemini.request_json` to a recorder
  returning `{"candidates":[{"content":{"parts":[{"text":"answer
  body"}]},"groundingMetadata":{"groundingChunks":[{"web":{"uri":"https://a"}},{"web":{"uri":"https://b"}}]}}]}`.
  Assert `GeminiResult.answer == "answer body"`, `.citations ==
  ["https://a", "https://b"]`, `.model == DEFAULT_MODEL`. Assert
  the recorded call had `method="POST"`, the resolved URL ended
  with `models/gemini-2.5-pro:generateContent`, headers contained
  `x-goog-api-key: <key>`, and the body matched
  `{"contents":[{"role":"user","parts":[{"text":"<query>"}]}],"tools":[{"google_search":{}}]}`.
- `test_gemini_dispatch_url_omits_api_key_value` — recorded URL
  contains none of `?key=`, `&key=`, `?api_key=`, AND the
  literal API-key value (`gk-DO-NOT-LOG`) is not a substring of
  the URL. (Spec invariant 4.)
- `test_gemini_dispatch_missing_env_var_raises_before_http` — no
  env var → `WikiError("set GEMINI_API_KEY in the environment")`;
  `request_json` recorder confirms zero calls.
- `test_gemini_dispatch_missing_env_var_uses_resolved_name` —
  `config.api_key_env="MY_GEMINI_KEY"` with `MY_GEMINI_KEY`
  unset → `WikiError("set MY_GEMINI_KEY in the environment")`
  (the resolved override, not the default literal).
- `test_gemini_dispatch_no_grounding_metadata_returns_empty_citations`
  — response without `groundingMetadata`; result has
  `citations == []`, `answer` still populated, no exception.
- `test_gemini_dispatch_non_web_chunks_skipped` —
  `groundingChunks: [{"web":{"uri":"https://a"}}, {"retrievedContext":{"uri":"corpus://x"}}, {}, {"web":{"uri":"https://b"}}]`
  yields `citations == ["https://a", "https://b"]`. No exception.
- `test_gemini_dispatch_multiple_text_parts_concatenated` — response
  with `parts: [{"text":"a"}, {"thoughtSignature":"…"}, {"text":"b"}]`
  yields `answer == "ab"` (non-text parts ignored).
- `test_gemini_dispatch_duplicate_citation_uris_deduped` —
  `groundingChunks: [{"web":{"uri":"https://a"}}, {"web":{"uri":"https://a"}}, {"web":{"uri":"https://b"}}]`
  yields `citations == ["https://a", "https://b"]` (first-seen
  order preserved).
- `test_gemini_dispatch_malformed_response_no_text_parts` —
  `parts: [{"thoughtSignature":"x"}]` (no string-typed `text` in
  any part) raises `ResearchHTTPError("gemini: malformed
  response")`.
- `test_gemini_dispatch_malformed_response_missing_candidates` —
  response without `candidates` raises malformed.
- `test_gemini_dispatch_endpoint_override` — `config.endpoint =
  "https://proxy.example/v1/models/gemini-2.5-pro:generateContent"`
  flows through verbatim.
- `test_gemini_dispatch_model_override_builds_url` — `config.model
  = "gemini-2.5-flash"` with `config.endpoint` unset composes
  `.../models/gemini-2.5-flash:generateContent`. Result's `.model`
  reads back `"gemini-2.5-flash"`.
- `test_gemini_dispatch_wraps_http_error_with_prefix` — fake
  `request_json` raises `ResearchHTTPError("HTTP 401", status=401)`;
  provider re-raises with `"gemini: HTTP 401"` and `status=401`.
- `test_gemini_dispatch_key_redacted_in_errors` — fixture key
  `gk-DO-NOT-LOG`; the wrapped exception's `str`, `repr`, and
  `args` contain none of that literal even when `__cause__` would
  otherwise carry the bytes. `exc.__cause__ is None` (helper uses
  `raise … from None`).
- `test_gemini_dispatch_real_http_helper_passes_through` — like
  Perplexity's analogue, patch `research.http.urlopen` to return
  a canonical Gemini response; confirm one urlopen call happens
  (the request flowed through `request_json`).
- `test_gemini_module_imports_no_urllib_request` — read
  `llm_wiki_kit/research/providers/gemini.py` source, parse with
  `ast.parse`, walk for `ast.ImportFrom` nodes with `module ==
  "urllib.request"` and `ast.Import` nodes with any alias whose
  `name == "urllib.request"`. Assert the matching list is empty.
  (Spec invariant 9 verification — closes the "patched `urlopen`
  test doesn't actually prove absence of import" gap.)
- `test_gemini_module_namespace_has_no_urlopen` — `from
  llm_wiki_kit.research.providers import gemini`; assert
  `"urlopen" not in gemini.__dict__`.

**Approach:** New file `llm_wiki_kit/research/providers/gemini.py`
exposing:

```python
PROVIDER_SLUG = "gemini"
DEFAULT_API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_MODEL = "gemini-2.5-pro"
DEFAULT_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

@dataclass(frozen=True)
class GeminiResult:
    answer: str
    citations: list[str]
    model: str

def dispatch(config: ProviderConfig, query: str) -> GeminiResult: ...
```

`dispatch` reads the env var (resolved name, not hardcoded
literal), composes the URL (override-or-default-template), builds
headers + body, calls `request_json` with `json_body=<body dict>`,
parses the response in a `_parse_response(payload, model)` helper.

`_parse_response` walks `candidates[0].content.parts` once,
appending each `part["text"]` when it's a string into an
`answer_chunks: list[str]`; non-string `text` and non-text parts
are skipped. If `answer_chunks` is empty, raise
`ResearchHTTPError("gemini: malformed response")`. Otherwise the
answer is `"".join(answer_chunks)`.

Citation extraction walks
`candidates[0].get("groundingMetadata", {}).get("groundingChunks",
[])` defensively. For each chunk, the parser checks:
`isinstance(chunk, dict)` AND
`isinstance(chunk.get("web"), dict)` AND
`isinstance(chunk["web"].get("uri"), str)` — only then does it
include the URI. Non-`web` chunks (e.g. `retrievedContext`),
malformed chunks, and missing `groundingMetadata` all produce an
empty / partial citations list without raising. Deduplication via
a `seen: set[str]` plus output `list[str]` preserves first-seen
order.

Re-raise pattern mirrors Perplexity: `except ResearchHTTPError as
exc: raise ResearchHTTPError(f"gemini: {exc}", status=exc.status)
from None`.

**Gate:** `pytest tests/unit/test_research_gemini.py && ruff check
llm_wiki_kit/research/providers/gemini.py && mypy
llm_wiki_kit/research/providers/gemini.py`.

---

### Step 3 — `providers/semantic_scholar.py:dispatch` calls the Graph API, supports keyless/keyed modes with the right retry budget, renders the deterministic paper-list body.

**Depends on:** Step 1 (provider passes `json_body=None` — needs
the helper's widened signature).

**Verification mode:** TDD.

**Tests** (`tests/unit/test_research_semantic_scholar.py`, new file):

- `test_semantic_scholar_dispatch_happy_path_keyed` — fixture sets
  `SEMANTIC_SCHOLAR_API_KEY=ss-DO-NOT-LOG`. Patch
  `semantic_scholar.request_json` with a recorder returning a
  two-paper canonical response (matches spec acceptance fixture).
  Assert `.answer` equals the spec'd snapshot string,
  `.citations == ["https://a", "https://b"]`, `.model ==
  "graph-v1"`. Assert recorded call had `method="GET"`,
  URL contained `query=<urlencoded-query>&limit=10&fields=title,authors,year,abstract,url,venue`
  (exact substring match), headers contained `x-api-key:
  ss-DO-NOT-LOG`, and `max_retries == 3`.
- `test_semantic_scholar_dispatch_happy_path_keyless` — env var
  unset. Same canonical response. Assert headers dict does **not**
  contain the key `"x-api-key"` (verified by `assert "x-api-key"
  not in call["headers"]`). Assert `max_retries == 5`.
- `test_semantic_scholar_dispatch_empty_data_returns_no_papers_found`
  — response is `{"total": 0, "offset": 0, "data": []}`; result
  `.answer == "No papers found.\n"`, `.citations == []`.
- `test_semantic_scholar_dispatch_renders_snapshot` — fixture
  response → byte-for-byte equal to `EXPECTED_BODY`, a multi-line
  string **hand-authored in the test module** (not regenerated
  from the implementation). The comparison uses `==` not `in`.
  A header comment on `EXPECTED_BODY` reads: "Hand-authored
  snapshot. If the renderer template changes, re-author this
  constant in the same commit — do not paste the implementation's
  output. Tautology defense per spec invariant 8."
- `test_semantic_scholar_dispatch_missing_scalar_fields_render_empty_slots`
  — paper with no `abstract`, no `venue`, no `year`, no `url`
  but a valid `title` and `authors` → renders without raising;
  the paper appears in the body but not in `citations`.
- `test_semantic_scholar_dispatch_all_fields_missing_renders_no_metadata`
  — paper with every scalar field missing and empty `authors`
  list renders as `<n>. *(no metadata)*` (snapshot-pinned).
- `test_semantic_scholar_dispatch_empty_authors_renders_unknown_authors`
  — paper with `authors: []` renders the author slot as
  `unknown authors` (snapshot-pinned).
- `test_semantic_scholar_dispatch_skips_non_string_author_names`
  — paper has `authors: [{"name": "A"}, {"name": null}, {"name":
  123}, {}]`; the rendered author-list contains only `"A"`.
- `test_semantic_scholar_dispatch_endpoint_with_query_string_rejected`
  — `config.endpoint = "https://proxy.example/paper/search?internal=1"`
  raises `WikiError("research-providers.yaml: semantic-scholar
  endpoint must be a bare scheme://host/path (no query, fragment,
  or userinfo)")` *before* any recorder call (`request_json`
  recorder shows zero calls).
- `test_semantic_scholar_dispatch_endpoint_with_fragment_rejected`
  — same shape, `config.endpoint =
  "https://proxy.example/paper/search#section"`.
- `test_semantic_scholar_dispatch_endpoint_with_userinfo_rejected`
  — `config.endpoint =
  "https://user:tok@proxy.example/paper/search"`.
- `test_semantic_scholar_dispatch_malformed_top_level` — response
  is a list (not a dict) → `ResearchHTTPError("semantic-scholar:
  malformed response")`.
- `test_semantic_scholar_dispatch_malformed_data_key_missing` —
  response is `{"total": 5}` (no `data` key) → malformed.
- `test_semantic_scholar_dispatch_url_encodes_query` — query
  containing `"machine learning & ai"` → recorded URL contains
  `query=machine+learning+%26+ai` (or the `urllib.parse.quote_plus`
  equivalent).
- `test_semantic_scholar_dispatch_wraps_http_error_with_prefix` —
  fake `request_json` raises `ResearchHTTPError("HTTP 429 after 5
  retries", status=429)`; provider re-raises with `"semantic-scholar:
  HTTP 429 after 5 retries"`.
- `test_semantic_scholar_dispatch_key_redacted_in_errors` — keyed
  fixture; the wrapped exception's `str`/`repr`/`args` contain
  none of `ss-DO-NOT-LOG`. `exc.__cause__ is None`.
- `test_semantic_scholar_dispatch_real_http_helper_passes_through`
  — patch `research.http.urlopen` returning the canonical
  response; assert one urlopen call (no bypass of the helper).
- `test_semantic_scholar_module_imports_no_urllib_request` —
  AST-grep over
  `llm_wiki_kit/research/providers/semantic_scholar.py`
  asserting no `urllib.request` imports (same shape as Step 2's
  equivalent for Gemini).
- `test_semantic_scholar_module_namespace_has_no_urlopen` —
  `from llm_wiki_kit.research.providers import semantic_scholar`;
  assert `"urlopen" not in semantic_scholar.__dict__`.
- `test_semantic_scholar_keyless_real_retries_use_correct_budget` —
  patch `urlopen` to raise `HTTPError(429)` for every call and a
  fake `time.sleep` recording history. Run keyless `dispatch` to
  exhaustion; assert sleep history equals `[1.0, 2.0, 4.0, 8.0,
  16.0]` (five retries) and the final exception's message
  contains `"semantic-scholar: HTTP 429 after 5 retries"`.

**Approach:** New file
`llm_wiki_kit/research/providers/semantic_scholar.py`:

```python
PROVIDER_SLUG = "semantic-scholar"
DEFAULT_API_KEY_ENV = "SEMANTIC_SCHOLAR_API_KEY"
DEFAULT_ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"
DEFAULT_MODEL = "graph-v1"
DEFAULT_FIELDS = "title,authors,year,abstract,url,venue"
DEFAULT_LIMIT = 10
KEYLESS_MAX_RETRIES = 5
KEYED_MAX_RETRIES = 3

@dataclass(frozen=True)
class SemanticScholarResult:
    answer: str
    citations: list[str]
    model: str

def dispatch(config: ProviderConfig, query: str) -> SemanticScholarResult: ...
```

URL composition: validate `config.endpoint` (when set) via
`urllib.parse.urlsplit` — reject the override when
`split.query`, `split.fragment`, or `split.username` is
non-empty, with the spec'd `WikiError` message. On the clean
endpoint (override or default), append
`"?" + urllib.parse.urlencode([("query", query), ("limit",
str(DEFAULT_LIMIT)), ("fields", DEFAULT_FIELDS)],
quote_via=urllib.parse.quote_plus)`. Deterministic ordering
keeps the substring-match acceptance test reliable.

Headers are built into a `dict[str, str]` then optionally
augmented with `x-api-key` when the env var resolves to a
non-empty string. Empty string and `None` both omit the header
entirely; never sent with an empty value.

The provider calls `request_json(method="GET", url=url,
headers=headers, json_body=None, max_retries=<3 or 5>)`. The
`json_body=None` arg is the load-bearing call into Step 1's
widened helper.

The `answer` rendering walks `data` once via a `_render_paper(n,
paper) -> str` helper. The helper first builds extracted-string
versions of `title`, `year`, `venue`, `abstract`, `url` (each
defaulting to `""` when missing or non-string) and a comma-joined
`authors_str` from `paper["authors"]` entries whose `name` is a
non-empty string. If `authors_str == ""`, replace with the literal
`"unknown authors"`. If *all* of `title`, `year`, `venue`,
`abstract`, `url` are empty AND `authors_str == "unknown authors"`,
emit the degenerate-line `f"{n}. *(no metadata)*\n"` and return.
Otherwise emit the full template
`f"{n}. **{title}** ({year}) — {authors_str}. *{venue}*. {abstract}\n   {url}\n"`.

Edge: empty `data` list short-circuits in `dispatch` itself and
returns `SemanticScholarResult(answer="No papers found.\n",
citations=[], model=DEFAULT_MODEL)`.

Error wrapping mirrors Gemini: `except ResearchHTTPError as exc:
raise ResearchHTTPError(f"semantic-scholar: {exc}", status=exc.status)
from None`.

**Gate:** `pytest tests/unit/test_research_semantic_scholar.py &&
ruff check llm_wiki_kit/research/providers/semantic_scholar.py &&
mypy llm_wiki_kit/research/providers/semantic_scholar.py`.

---

### Step 4 — `_PROVIDER_REGISTRY` registers both new providers and dispatcher integration tests pass.

**Depends on:** Steps 2, 3 (the provider modules must exist before
the registry can import them).

**Verification mode:** TDD.

**Tests** (extend `tests/unit/test_research_dispatch.py`):

- `test_dispatch_query_routes_to_gemini_via_registry` — vault with
  a `gemini:` block in the providers region; patch
  `gemini.dispatch` to return a fake `GeminiResult`; call
  `dispatch_query(query, "gemini", vault_root, now=...)`. Assert
  the returned `DispatchResult.event.provider == "gemini"`,
  `.event.model == "gemini-2.5-pro"`, `.event.status == "ok"`;
  assert the rendered markdown frontmatter contains `provider:
  gemini` and `model: gemini-2.5-pro`.
- `test_dispatch_query_routes_to_semantic_scholar_via_registry` —
  analogous for `semantic-scholar`; the event's
  `model == "graph-v1"`.
- `test_dispatch_query_three_providers_no_flag_lists_all_three` —
  config has all three blocks; without `--provider`, the
  `WikiError`'s message contains `"gemini"`, `"perplexity"`, and
  `"semantic-scholar"` in `config.slugs()` (sorted) order.
- `test_dispatch_query_unknown_implementation_after_registry_extension`
  — a config block named `"future-provider"` is not in the
  registry; raises `WikiError("provider 'future-provider' has no
  implementation in this kit version")`. Confirms the registry
  resolution path still rejects unregistered slugs even after we
  add two new known ones.
- `test_dispatch_query_gemini_http_error_wraps_as_dispatch_error`
  — patch `gemini.dispatch` to raise
  `ResearchHTTPError("gemini: HTTP 401", status=401)`. Test
  fixture YAML must include `model: gemini-2.5-pro` in the
  gemini block (otherwise `provider_config.model` is `None`).
  Assert the dispatcher raises `ResearchDispatchError` with
  `.event.status == "error"`, `.event.provider == "gemini"`,
  `.event.model == "gemini-2.5-pro"` (the config's model — the
  dispatcher uses `provider_config.model` for the error event,
  matching the Perplexity error-path in Task 18 dispatch.py).
- `test_dispatch_query_semantic_scholar_http_error_wraps_as_dispatch_error`
  — analogous; test fixture YAML must include `model: graph-v1`
  in the semantic-scholar block, then `.event.model == "graph-v1"`.

**Approach:** Edit `llm_wiki_kit/research/dispatch.py`:

1. Add imports: `from llm_wiki_kit.research.providers import gemini`
   and `from llm_wiki_kit.research.providers import
   semantic_scholar` next to the existing `perplexity` import.
2. Add two wrapper functions `_call_gemini(config, query) ->
   _ProviderOutput` and `_call_semantic_scholar(config, query) ->
   _ProviderOutput`, each adapting the provider's result class to
   the shared `_ProviderOutput` shape — same pattern as
   `_call_perplexity`.
3. Extend `_PROVIDER_REGISTRY` with `gemini.PROVIDER_SLUG:
   _call_gemini` and `semantic_scholar.PROVIDER_SLUG:
   _call_semantic_scholar`.
4. Update the dispatcher module's docstring's Task 19 note from
   "Task 19 adds Gemini and Semantic Scholar by adding entries to
   `_PROVIDER_REGISTRY` directly in this file" to "Gemini and
   Semantic Scholar are registered alongside Perplexity; new
   providers join by adding a re-binding wrapper above and one
   entry to `_PROVIDER_REGISTRY`." The spec invariant is the
   dispatcher's structure didn't move; the docstring update is
   doc drift maintenance per AGENTS.md.

**Gate:** `pytest tests/unit/test_research_dispatch.py && mypy
llm_wiki_kit/research && ruff check llm_wiki_kit/research`.

---

### Step 5 — Both template primitives exist on disk and validate against ADR-0006's contribution shape.

**Depends on:** none (the primitive templates are pure data; they
can be authored in parallel with the provider code).

**Verification mode:** goal-based for the template-loads tests
(the contract is "primitive.yaml validates," verified via
`pytest -k`); TDD for the requires-closure event-sequence test
that exercises the install pipeline.

**Tests** (extend `tests/unit/test_primitives.py` and
`tests/integration/test_wiki_research.py`):

- `test_primitive_research_gemini_loads` — `load_primitive(templates/
  infrastructure/research-gemini)` yields a `Primitive` with kind
  `infrastructure`, `requires == ["research"]`, one contribution
  to `research-providers.yaml`'s `providers` region, no `files/`
  tree (matches Perplexity's shape).
- `test_primitive_research_semantic_scholar_loads` — analogous.
- `test_primitive_research_gemini_validate_contributions_clean` —
  `install.validate_contributions(primitive, root)` passes.
- `test_primitive_research_semantic_scholar_validate_contributions_clean`
  — same.
- `test_wiki_add_gemini_renders_config_round_trip` (integration) —
  init a bare vault, `wiki add infrastructure:research-gemini`,
  parse the rendered `research-providers.yaml`'s managed region
  via `ResearchProvidersConfig.model_validate(yaml.safe_load(
  managed_regions.parse(open(...).read())["providers"]))`. Assert
  the result has exactly one provider entry `"gemini"` with
  `api_key_env="GEMINI_API_KEY"`,
  `endpoint=...generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent`,
  `model="gemini-2.5-pro"`, `cost_signal="medium"`,
  `strengths=["long_synthesis","grounded_web_search"]`.
- `test_wiki_add_semantic_scholar_renders_config_round_trip` —
  analogous; `cost_signal="free"`,
  `strengths=["peer_reviewed","structured_metadata"]`,
  `model="graph-v1"`.
- `test_wiki_add_both_providers_aggregates_blocks` — `wiki add
  infrastructure:research-gemini` then `wiki add
  infrastructure:research-semantic-scholar`; parse the region;
  assert the result has exactly two keys (`gemini` and
  `semantic-scholar`), both validating cleanly.
- `test_wiki_add_research_gemini_pulls_research_via_requires` —
  fresh vault, install only `infrastructure:research-gemini`;
  journal shows two `primitive.install` events
  (`research` before `research-gemini`) plus one
  `managed_region.write` event for
  `research-providers.yaml:providers`.
- `test_recipes_do_not_include_research_gemini_or_semantic_scholar`
  (unit) — load each of `recipes/family.yaml`,
  `recipes/work-os.yaml`, `recipes/personal.yaml`; assert neither
  primitive appears in `primitives:`. Also a static grep guard:
  `Grep("infrastructure:research-(gemini|semantic-scholar)",
  recipes/)` returns nothing.

**Approach:** Create the two primitive directories under the
existing `templates/infrastructure/`:

```
templates/infrastructure/research-gemini/
├── primitive.yaml
└── regions/
    └── research-providers.yaml.providers   # the gemini: block

templates/infrastructure/research-semantic-scholar/
├── primitive.yaml
└── regions/
    └── research-providers.yaml.providers   # the semantic-scholar: block
```

`primitive.yaml` shape (gemini):

```yaml
name: research-gemini
kind: infrastructure
version: 0.1.0
description: >-
  Gemini Deep Research provider for `wiki research`. Contributes one
  block to the shared research-providers.yaml managed region
  "providers" naming the Generative Language API endpoint, the
  default model (gemini-2.5-pro), and the env var the dispatcher
  reads (GEMINI_API_KEY). Uses grounded generation (the
  google_search tool) so responses carry citation URIs. Requires
  the seed primitive (infrastructure:research) so the shared config
  file exists on disk before the aggregator writes the region.
requires:
  - research
contributes_to:
  - file: research-providers.yaml
    region: providers
```

`primitive.yaml` shape (semantic-scholar) is analogous; the
description names the Graph API's `paper/search` endpoint and
notes that `SEMANTIC_SCHOLAR_API_KEY` is optional.

Region snippets are the literal YAML blocks shown in spec §Outputs.

**Gate:** `pytest tests/unit/test_primitives.py
tests/integration/test_wiki_research.py`.

---

### Step 6 — `wiki research` end-to-end works for both new providers; all mechanical gates green.

**Depends on:** Steps 1–5.

**Verification mode:** integration-TDD. Each test exercises a
full vault-init → primitive-install → CLI-invoke flow against
`tmp_path` and mocked `urlopen` / patched `provider.dispatch`.

**Tests** (extend `tests/integration/test_wiki_research.py`):

- `test_wiki_research_gemini_happy_path_stdout` — install both
  primitives via in-process `_cmd_init` + `_cmd_add` against a
  `tmp_path` vault; `monkeypatch.setenv("GEMINI_API_KEY",
  "gk-DO-NOT-LOG")`; patch
  `research.providers.gemini.dispatch` to a fake returning
  `GeminiResult("answer", ["https://a"], "gemini-2.5-pro")`;
  invoke `cli.main(["research", "q", "--provider", "gemini"])`.
  Assert exit 0; stdout matches the spec'd frontmatter +
  body shape; exactly one `research.query` event with
  `provider="gemini"`, `model="gemini-2.5-pro"`, `status="ok"`,
  `result_path=None`.
- `test_wiki_research_semantic_scholar_happy_path_stdout` —
  analogous, **without** setting `SEMANTIC_SCHOLAR_API_KEY`.
  Patch `research.providers.semantic_scholar.dispatch` to a fake
  returning `SemanticScholarResult("rendered body\n",
  ["https://a"], "graph-v1")`. Assert `event.model == "graph-v1"`.
- `test_wiki_research_gemini_missing_env_var_exits_2_no_event` —
  install both primitives; `monkeypatch.delenv("GEMINI_API_KEY",
  raising=False)`; **don't** patch `gemini.dispatch` (so the real
  env-var check fires). Assert exit 2; stderr contains `"set
  GEMINI_API_KEY in the environment"`; the journal has zero
  `research.query` events appended by this invocation. (Mirrors
  Task 18's `test_wiki_research_typo_in_config_message_quotes_field`
  shape — config-shaped errors don't journal.)
- `test_wiki_research_semantic_scholar_keyless_real_request_succeeds`
  — install both primitives; do **not** set
  `SEMANTIC_SCHOLAR_API_KEY`; patch
  `llm_wiki_kit.research.http.urlopen` to return a canonical
  Semantic Scholar response. Run `cli.main(["research", "q",
  "--provider", "semantic-scholar"])` and confirm exit 0 (the
  keyless tier proceeds) and one `research.query` event with
  `status="ok"`.
- `test_wiki_research_three_providers_pass_provider_required` —
  install all three (`research-perplexity`, `research-gemini`,
  `research-semantic-scholar`); `cli.main(["research", "q"])`
  without `--provider` exits 2 with stderr containing all three
  slugs in `sorted` order (`gemini, perplexity, semantic-scholar`).
- `test_wiki_research_apikey_never_in_journal_or_stdout_gemini` —
  set `GEMINI_API_KEY=gk-DO-NOT-LOG`; install both primitives;
  patch `urlopen` to return a canonical Gemini response whose
  body bytes contain the literal substring `gk-DO-NOT-LOG`
  injected into the answer text (simulating a future server-side
  echo bug — the real Gemini API doesn't echo headers, but the
  injection defends against a variant that might). Run the CLI
  with `--out research/x.md`. Grep the journal file, the
  written page on disk, stdout, and stderr for `gk-DO-NOT-LOG`.
  Note that the answer body **will** contain the literal — that
  is the test fixture's point — so the assertion checks only
  that the *journal lines* and stderr/log surfaces are free
  of it (the markdown page is the answer; the journal is the
  audit trail). This pin defends against journal-leakage
  regressions specifically.
- `test_wiki_research_http_error_journals_error_event_gemini` —
  install both primitives; set `GEMINI_API_KEY`; patch
  `gemini.dispatch` to raise `ResearchHTTPError("gemini: HTTP 401",
  status=401)`. Assert exit 2; exactly one `research.query` event
  with `status="error"`, `provider="gemini"`, `result_path=None`.

**Approach:** Five new integration tests under
`tests/integration/test_wiki_research.py`, reusing the existing
test-file fixtures (`vault_with_journal`, etc.) and the Task 18
in-process `_cmd_init`/`_cmd_add` pattern. No CLI source changes —
the CLI's `_cmd_research` is provider-agnostic; the new tests
exercise paths that already work end-to-end once the primitives
are in tree and the registry is wired.

Documentation drift:

- Add a one-line mention of `research-gemini` and
  `research-semantic-scholar` to `docs/architecture/overview.md`'s
  primitive-catalog section, immediately after the existing
  `research-perplexity` mention. Each entry says what the provider
  does in one phrase ("Gemini Deep Research via grounded
  generateContent" / "Semantic Scholar Graph paper search; keyless
  tier supported").
- No ADR changes. ADR-0007 already covers placement.
- No `docs/architecture/security.md` changes; the API-key surface
  is unchanged from Task 18.
- Leave the spec's `Status:` at `Draft` — specs are living per
  CONVENTIONS §"The doc hierarchy"; Task 18's spec is also still
  `Draft` post-merge. No status transition.
- No new `docs/guides/reference/` doc lands in this PR — see
  Task 18 plan step 7, which deferred that surface; this PR
  inherits the deferral.

**Gate (whole-PR convergence):**

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

All green, plus every contract test enumerated in spec.md §"Acceptance
criteria" passing.

---

## Verification gate

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

The `not slow` filter excludes the wheel-acceptance suite, which is
unaffected by this change. CI runs both `-m 'not slow'` and the
ruff/mypy gates per the established workflow.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Gemini's grounded-generation response shape differs from the documented `groundingChunks[*].web.uri` path — real responses often include non-`web` chunks (`retrievedContext`) and a strict parser would reject every other query. | high | The Step 2 parser explicitly **skips** non-`web` chunks rather than raising; the contract test `test_gemini_dispatch_non_web_chunks_skipped` pins this. Only a missing `parts[*].text` raises malformed — the answer-body case is the load-bearing one. |
| Semantic Scholar's keyless tier returns 429 on every retry attempt during normal use. | medium | `max_retries=5` (extra budget) plus the spec's `cost_signal: free` strength signalling so a future picker can de-prioritise the keyless path. If users complain, a follow-up ADR/spec can either raise the budget further or add an explicit "you should set the env var" UX. |
| Gemini API key leaks into a markdown page, journal, exception `repr`, or `--verbose` traceback. | high impact, low likelihood | Three dedicated tests: `test_gemini_dispatch_key_redacted_in_errors`, the CLI-level `test_wiki_research_apikey_never_in_journal_or_stdout_gemini`, and a `request.full_url`-check test asserting no `?key=` in the URL. |
| Semantic Scholar's body-rendering snapshot drifts on a stylistic tweak. | low | The snapshot is committed in-tree as a Python constant; a stylistic change requires updating both the renderer and the expected literal in the same commit. Whitespace differences fail loudly rather than being a `re.match` blind spot. |
| `_PROVIDER_REGISTRY` import order — provider modules cycle-import through `dispatch.py`. | low | Both new providers import only from `llm_wiki_kit.errors`, `llm_wiki_kit.models`, `llm_wiki_kit.research.http`, and stdlib. The dispatcher imports the provider modules; no provider imports the dispatcher. Same shape as Perplexity. |
| Conflict with concurrent Tasks 20–22 work on the same `cli.py`/`models.py`. | low | This PR doesn't touch `cli.py` or `models.py`. Surface area is `research/providers/`, `research/dispatch.py`, and `templates/infrastructure/research-{gemini,semantic-scholar}/`. |
| Semantic Scholar's "keyless" rate-limit budget changes (Semantic Scholar moves to harder enforcement). | low | The `KEYLESS_MAX_RETRIES = 5` constant is a one-line tweak; a future spec can wire a per-config `retries:` override into `ProviderConfig` if real usage justifies it. |
| Gemini's "Deep Research" later ships as a dedicated API endpoint different from `generateContent` + grounding. | medium | The kit's `config.endpoint` override lets a user point at the future URL without a kit release; a full migration to the new endpoint can land as a follow-up spec. |

## Out of scope

- Gemini streaming (`streamGenerateContent`).
- Semantic Scholar pagination beyond `limit=10`.
- Per-provider rate-limit budgets configurable via
  `ProviderConfig` — `max_retries` stays a kit-internal constant
  for now.
- Vault-side `wiki-research` SKILL.md (still deferred to Task 20
  or a follow-up).
- A cross-provider deduplication layer for citations.
- Picker / scoring across providers.
- A `research-source` content-type primitive.
- Endpoint allowlist / SSRF protection (still parked for a future
  ADR).
- `GET`-specialised path in `research.http.request_json` (deferred
  until a second non-POST caller justifies it).
- A reference doc under `docs/guides/reference/` for the three
  providers. (Same deferral as Task 18.)
