# Plan: research dispatch + Perplexity provider

> **Implementation plan paired with `spec.md`.** The spec says *what*;
> the plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** `docs/specs/task-18-research-perplexity/spec.md`
- **Owner:** primary loop on `eugenelim/v2-task-18-research-perplexity`

## Approach

One PR, six steps, TDD-default. Build inside-out: the Pydantic config
type first (every later step reads it), then the provider-agnostic
HTTP retry helper (the only piece with non-trivial control flow),
then the Perplexity provider (calls the helper), then the dispatcher
+ markdown renderer (composes both), then the CLI wiring, finally
the two primitives. Integration test lands last so every layer is
independently green when it composes.

The split between `dispatch_query` (orchestration) and
`providers.perplexity.dispatch` (pure HTTP + parse) is the seam Task
19 reuses for Gemini and Semantic Scholar — those providers add their
own `providers/<slug>.py` and register in the dispatcher's
`PROVIDER_REGISTRY` dict, no other changes. The registry pattern is
data, not code generation; one new dict entry per provider.

Wave-1 coordination with Task 17 (`wiki run`): `cli.py` and
`models.py` are shared, both additively. `_cmd_research` replaces a
stub already in `cli.py`; `ResearchQueryEvent` gains two optional
fields in `models.py`. Both diffs are local. If Task 17 lands first,
this PR rebases on main; if Task 18 lands first, Task 17 rebases.

## Pre-conditions

- `eugenelim/v2-task-18-research-perplexity` branched off `origin/main`
  (already done — current branch).
- `gh auth status` shows `eugenelim` as active (already verified).
- ADR-0003, ADR-0004, ADR-0006 read; the `install.py` constraint on
  contribution `file` paths is understood (vault-root file naming
  documented in spec §Constraints).
- No environment variable named `PERPLEXITY_API_KEY` is required for
  the test run; every HTTP test mocks `urllib.request.urlopen`. The
  integration test sets a dummy value in `monkeypatch.setenv`.

## Steps

Each step's `Tests:` block comes before `Approach:` per CONVENTIONS
§"Contract tests vs. construction tests". Red → green → refactor;
the named tests are construction tests in `tests/unit/...` or
`tests/integration/...`. Contract tests are the acceptance criteria
in `spec.md`; the construction tests are the per-step bar that
proves the step achieves them.

### Step 1 — `ProviderConfig` + `ResearchProvidersConfig` Pydantic models land in `models.py` and round-trip the example YAML.

**Tests** (`tests/unit/test_models.py`, new test class
`TestResearchProvidersConfig`):

- `test_provider_config_minimal_yaml_round_trip` — load an empty
  block (no `api_key_env`, no other keys); defaults are
  `api_key_env=None`, `endpoint=None`, `model=None`,
  `cost_signal=None`, `strengths=[]`. (Key-optional shape supports
  Task 19's Semantic Scholar.)
- `test_provider_config_with_api_key_env_set` — `api_key_env:
  PERPLEXITY_API_KEY` round-trips as the literal string.
- `test_research_providers_config_one_provider` — top-level mapping
  parses, the `perplexity` key resolves to a `ProviderConfig`.
- `test_research_providers_config_two_providers` — `perplexity` +
  `gemini` (placeholder; just a config block, no implementation) both
  parse.
- `test_research_query_event_additive_fields` — an older JSONL line
  `{"type":"research.query","timestamp":"...","by":"wiki-research","query":"q","provider":"perplexity","result_path":null}`
  round-trips through `Event` validation after the new fields are
  added (ADR-0002 invariant).

(The "unknown-key rejection" contract is covered at the CLI surface
in step 5's `test_wiki_research_typo_in_config_message_quotes_field`,
not here — `_StrictModel`'s base test already proves
`extra="forbid"` repo-wide; re-asserting it would test Pydantic, not
this spec.)

**Approach:** Append two classes to the existing models module —
`ProviderConfig` (StrictModel; fields per spec §"ProviderConfig
schema" — `api_key_env: str | None = None`, `endpoint: str | None
= None`, `model: str | None = None`, `cost_signal: Literal[…] |
None = None`, `strengths: list[str] = Field(default_factory=list)`)
and
`ResearchProvidersConfig` with a `RootModel[dict[str,
ProviderConfig]]` shape so the YAML is read directly as a flat
mapping (no wrapping `providers:` key — see spec §Outputs YAML
example). Extend `ResearchQueryEvent` with `model: str | None = None`
and `status: Literal["ok","error"] = "ok"`.

**Gate:** `pytest tests/unit/test_models.py -k "research or providers"
&& mypy llm_wiki_kit/models.py && ruff check llm_wiki_kit/models.py`.

---

### Step 2 — `llm_wiki_kit/research/http.py:request_json` retries 429/5xx with the spec'd backoff and surfaces 4xx immediately.

**Tests** (`tests/unit/test_research_http.py`, new file):

- `test_request_json_200_returns_parsed_dict` — mock urlopen returns
  a response whose `.read()` is `b'{"k": 1}'`; `request_json` returns
  `{"k": 1}`.
- `test_request_json_429_then_200_retries_with_backoff` — first
  urlopen call raises `HTTPError(429)`, second returns 200. Use a
  fake `time.sleep` (monkeypatched) recorded into a list; assert
  the list equals `[1.0]` after the call returns (one retry after
  the initial attempt → one backoff interval at `2 ** 0 = 1.0`s).
- `test_request_json_429_until_exhausted_raises_after_three_retries`
  — every urlopen call raises `HTTPError(429)`; assert
  `ResearchHTTPError` message contains "after 3 retries"; assert
  sleep history is `[1.0, 2.0, 4.0]` (three retries after the initial
  attempt → three backoff intervals at `2 ** {0,1,2}` seconds; four
  total HTTP attempts).
- `test_request_json_401_no_retry` — single urlopen call,
  `HTTPError(401)` propagates immediately as `ResearchHTTPError`.
- `test_request_json_500_retries` — same shape as 429 path.
- `test_request_json_urlerror_retries` — `URLError("connection
  refused")` retried 3 times, then raised.
- `test_request_json_timeout_retries` — `socket.timeout` retried.
- `test_request_json_malformed_json_no_retry` — HTTP 200 with body
  `b'not json'` raises `ResearchHTTPError("malformed response")`
  immediately (no retry; the server said 200, the bytes lied).
- `test_research_http_error_repr_omits_request_objects` — raise a
  `ResearchHTTPError` from inside the retry loop after an
  `HTTPError(401)` whose `read()` body contains the literal string
  `"Authorization: Bearer sk-DO-NOT-LOG"`; assert `repr(exc)` does
  not contain `sk-DO-NOT-LOG` and `exc.args` contains only the
  human-readable message and a numeric status code (no
  `urllib.request.Request`, no headers dict, no body bytes).

**Approach:** New package `llm_wiki_kit/research/` with
`__init__.py` (empty for now), `http.py` containing `request_json(*,
method, url, headers, json_body, timeout, max_retries=3)` plus a
narrow `ResearchHTTPError(WikiError)` subclass. Constructor
signature `ResearchHTTPError(message: str, *, status: int | None =
None)`; nothing else stored on the exception. Use
`urllib.request.Request` + `urllib.request.urlopen`. Retry loop:
for `attempt in range(max_retries + 1)`, try the request; on
retry-eligible exception (429, 5xx, `URLError`, `socket.timeout`)
sleep `2 ** attempt` and continue when there are retries left
(`attempt < max_retries`), otherwise raise with the "after N
retries" message; on fatal exception (other 4xx, malformed JSON)
raise immediately. With `max_retries=3` that yields four attempts
(0,1,2,3), three sleeps `[1.0, 2.0, 4.0]` between them, and the
final failure raised on attempt 3.

**Gate:** `pytest tests/unit/test_research_http.py && mypy
llm_wiki_kit/research && ruff check llm_wiki_kit/research`.

---

### Step 3 — `providers/perplexity.py:dispatch` calls Perplexity's API correctly, parses the response, never logs the key.

**Tests** (`tests/unit/test_research_perplexity.py`, new file):

- `test_perplexity_dispatch_happy_path` — monkeypatch
  `research.http.request_json` to a fake that records the call args
  and returns `{"choices":[{"message":{"content":"answer"}}],"citations":["https://a","https://b"]}`.
  Assert the returned `PerplexityResult.answer == "answer"`,
  `.citations == ["https://a", "https://b"]`, `.model ==
  "sonar-pro"`. Assert the recorded request had `Authorization:
  Bearer testkey`, `model: sonar-pro`, body `{"model": "sonar-pro",
  "messages": [{"role": "user", "content": "<query>"}]}`.
- `test_perplexity_dispatch_missing_env_var_raises_before_http` —
  no `PERPLEXITY_API_KEY` set; assert `WikiError` is raised; assert
  the fake `request_json` was not called.
- `test_perplexity_dispatch_no_citations_field` — response without
  `citations` returns `citations=[]`.
- `test_perplexity_dispatch_key_redacted_in_errors` — fake
  `request_json` raises `ResearchHTTPError("HTTP 401")`; the wrapped
  `WikiError` message does not contain `"testkey"`.
- `test_perplexity_dispatch_uses_endpoint_override` — config sets
  `endpoint: https://custom.example/v1/chat`; assert the recorded
  url matches.
- `test_perplexity_dispatch_default_model_when_unset` — config has
  no `model:`; the dispatcher uses `sonar-pro` (the documented
  default).

**Approach:** `providers/perplexity.py` exposes `dispatch(config:
ProviderConfig, query: str) -> PerplexityResult` (a frozen dataclass
with `answer`, `citations`, `model`). Pre-conditions enforced here
(not in `http.request_json`, which stays provider-agnostic per spec
§Constraints): reads `os.environ[config.api_key_env]` and raises
`WikiError` if unset; catches `ResearchHTTPError` from the helper
and re-raises it with the literal `"perplexity: "` prefix attached
so the user sees provider context in the message. Builds the
request body and headers, calls `request_json`, parses the
response. Provider-default endpoint and model live as module
constants (`DEFAULT_ENDPOINT`, `DEFAULT_MODEL = "sonar-pro"`); the
spec pins them.

**Gate:** `pytest tests/unit/test_research_perplexity.py`.

---

### Step 4 — `research.dispatch_query` orchestrates config-load → provider-pick → dispatch → markdown-render and returns the page text plus a `ResearchQueryEvent` ready for the CLI to journal.

**Tests** (`tests/unit/test_research_dispatch.py`, new file):

- `test_dispatch_query_no_config_file_raises` — vault tmp_path with
  no `research-providers.yaml` → `WikiError("infrastructure:research
  not installed")`.
- `test_dispatch_query_empty_region_raises` — file with the seed
  empty-region body → `WikiError("no research providers installed")`.
- `test_dispatch_query_invalid_config_raises_with_validator_message`
  — file with `endpiont:` typo → `WikiError` whose `str()` includes
  the field name `endpiont`.
- `test_dispatch_query_one_provider_no_flag_picks_it` — file with
  only `perplexity:`; assert the dispatcher routes to perplexity.
- `test_dispatch_query_two_providers_no_flag_raises` — two blocks,
  no `--provider`; assert `WikiError("pass --provider")` and message
  includes both slugs.
- `test_dispatch_query_unknown_provider_raises` — `--provider
  gemini` against a config with only `perplexity` → `WikiError`
  naming installed slugs.
- `test_dispatch_query_renders_markdown_and_event` — patch
  `providers.perplexity.dispatch` to return a fake `PerplexityResult`;
  call `dispatch_query`; assert the returned `(markdown, event)`
  pair where the markdown has the expected frontmatter keys and the
  event is a `ResearchQueryEvent(provider="perplexity",
  model="sonar-pro", status="ok", result_path=None, query=<q>)`.
- `test_dispatch_query_renders_yaml_safe_query_field` — query
  containing `"quote"\n---\n` round-trips through
  `yaml.safe_load(frontmatter_slice)` byte-for-byte.
- `test_dispatch_query_body_with_dashes_preserves_boundary` —
  provider returns content `"intro\n---\nmore"`; rendered document
  has exactly one `^---$` line between the start and the first
  blank line; body slice equals the original content verbatim.
- `test_dispatch_query_raises_research_dispatch_error_on_http_failure`
  — patched provider raises `ResearchHTTPError("perplexity: HTTP
  401")`; the dispatcher raises `ResearchDispatchError` whose
  `.event` is a `ResearchQueryEvent(status="error", model="sonar-pro",
  result_path=None)` and whose `str(exc)` matches the provider error
  message.

**Approach:** `llm_wiki_kit/research/__init__.py` re-exports
`dispatch_query`, `DispatchResult`, and `ResearchDispatchError`
only. The provider registry is a module-private `_PROVIDER_REGISTRY:
dict[str, Callable[[ProviderConfig, str], object]]` defined in
`research/dispatch.py`; Task 19 adds its entry by editing that file
directly. Keeping it private prevents downstream consumers (or a
future eval) from coupling to a registry shape this PR hasn't
committed to.

**Registry resolution shape — pinned so monkeypatch tests stay
honest.** The registry maps `slug → callable`. Direct binding
(`_PROVIDER_REGISTRY = {"perplexity": perplexity.dispatch}`) freezes
the function object at import time and silently bypasses any later
`monkeypatch.setattr("llm_wiki_kit.research.providers.perplexity.dispatch", fake)`.
To keep `monkeypatch.setattr` against the provider module work as
expected (and the Step 5 integration tests are written that way),
the registry holds *thin wrappers* that look up the dispatch
attribute at call time:

```python
def _call_perplexity(config, query):
    from llm_wiki_kit.research.providers import perplexity
    return perplexity.dispatch(config, query)

_PROVIDER_REGISTRY = {"perplexity": _call_perplexity}
```

Tests that need to patch the provider use
`monkeypatch.setattr(perplexity, "dispatch", fake)` — the wrapper
re-reads the attribute each call. Tests that need to patch the
*registry* itself (e.g. to inject a fake slug) use
`monkeypatch.setitem(_PROVIDER_REGISTRY, "fake", fake_callable)`. `dispatch_query(query, provider_slug,
vault_root, *, now)` reads `<vault_root>/research-providers.yaml`,
parses managed-region body via `managed_regions.parse`, loads
`ResearchProvidersConfig` over the YAML inside the region, picks
the provider (registry lookup), calls its
`dispatch(config, query)`, and composes the markdown via a tiny
`_render_markdown(query, provider_slug, result, fetched_at)`
helper that uses `yaml.safe_dump` for the frontmatter slice. The
`fetched_at` arg is passed as an `.isoformat()` string, not a
`datetime`, so the frontmatter preserves the literal `T` separator
the spec acceptance test pins. The env-var read
**lives in the provider** (`providers.perplexity.dispatch`), not in
`dispatch_query` — future providers (Task 19's Semantic Scholar)
may not need a key, and per-provider pre-conditions stay co-located
with the provider's HTTP logic. On success returns
`DispatchResult(markdown, event)` with `status="ok"`. On provider
`WikiError`/`ResearchHTTPError`, builds a `status="error"` event
and raises `ResearchDispatchError(message, event=event)`. The CLI
is responsible for journal-append before re-raising — that keeps
event-before-write ordering inside the I/O boundary (`cli.py`)
rather than burying it in the dispatcher.

**Gate:** `pytest tests/unit/test_research_dispatch.py`.

---

### Step 5 — `cli._cmd_research` wires `wiki research` through the dispatcher, journals first, then writes (stdout or `safe_write`).

**Tests** (`tests/integration/test_wiki_research.py`, new file):

- `test_wiki_research_no_vault_exits_2` — invoke `cli.main(["research","q"])` with `cwd=tmp_path` (no journal) → exit 2, stderr contains "not a wiki vault".
- `test_wiki_research_no_config_exits_2` — vault but no
  `research-providers.yaml` → exit 2, stderr contains "infrastructure:research not installed".
- `test_wiki_research_typo_in_config_message_quotes_field` —
  seed file's managed region contains `endpiont: ...`; CLI exits 2
  with stderr containing both "invalid research-providers.yaml" and
  the literal `endpiont` token (verifies the user-facing Pydantic-
  error formatting contract).
- `test_wiki_research_happy_path_stdout` — full pipeline: install
  the two primitives via in-process `_cmd_init` + `_cmd_add` calls,
  monkeypatch `research.providers.perplexity.dispatch` to return a
  canonical fake result, invoke `cli.main(["research","what is
  X"])`. (No `setenv` needed — the patched `dispatch` doesn't read
  env vars; env-var reading is covered by Step 3's
  `test_perplexity_dispatch_missing_env_var_raises_before_http`.)
  Assert exit 0, stdout matches the spec'd markdown shape, and one
  `research.query` event with `status="ok"` was appended.
- `test_wiki_research_happy_path_out_writes_via_safe_write` — same
  setup (patched `dispatch`, no env var needed) with `--out
  research/x.md`; assert the page exists on disk with the expected
  content, a `page.write` event was appended for that path, and the
  `research.query` event's `result_path == "research/x.md"`.
- `test_wiki_research_out_wraps_events_in_transaction` — same
  happy-path setup with `--out research/x.md`; assert the journal
  shows a `lock.acquired(by="wiki-research", reason="research
  perplexity")` event, then the `research.query` event, then the
  `page.write` event, then a `lock.released(by="wiki-research")`
  event, in that order. (No analogous test for the stdout flow
  because it emits one event and runs bare.)
- `test_wiki_research_out_drift_routes_to_proposal` — pre-seed
  `research/x.md` with different content + no journaled `PageWrite`
  baseline; assert `.proposed` sidecar appears, the CLI prints the
  one-line proposal notice, exits 0, journals one `research.query`
  event with `result_path="research/x.md"` (the *requested* path),
  and one `page.proposal` event whose `proposed_path` is the
  sidecar path.
- `test_wiki_research_out_absolute_path_rejected` — `--out
  /etc/passwd` exits 2 *before* any HTTP attempt; no
  `research.query` event is appended (the journal-event tail
  pre-call equals the tail post-call).
- `test_wiki_research_out_traversal_path_rejected` — `--out
  ../outside.md` exits 2; same no-event assertion.
- `test_wiki_research_two_invocations_journal_two_events` — call
  `cli.main(["research","X"])` twice in the same vault; assert
  exactly two `research.query` events with matching queries (no
  deduplication).
- `test_wiki_research_http_error_journals_error_event` — patched
  provider raises `ResearchHTTPError`; assert exit 2 and exactly
  one `research.query` event with `status="error"` and
  `result_path=None`.
- `test_wiki_research_apikey_never_in_journal_or_stdout` — set
  `PERPLEXITY_API_KEY=sk-DO-NOT-LOG`, install the primitives, run
  `wiki research "q"` against the *unpatched* `providers.perplexity.dispatch`
  with `request_json` patched to return a canonical Perplexity
  response; this exercises the real env-var read and HTTP path
  with key in headers. Grep the journal file, stdout, and stderr
  for the string. All three empty.
- `test_wiki_research_apikey_not_in_verbose_traceback` — same key,
  patched provider raises `ResearchHTTPError` whose `__cause__` is
  an `HTTPError` carrying the literal Authorization header in its
  bytes; run `cli.main(["--verbose", "research", "q"])`; capture
  stderr (which now includes the full traceback per `cli.py`'s
  `traceback.print_exc`); assert `sk-DO-NOT-LOG` appears in neither
  stderr nor the journal. Drives the `__cause__` chain explicitly.
- `test_wiki_research_journal_append_failure_preserves_dispatch_error`
  — patched provider raises `ResearchHTTPError`; patched
  `append_event` raises `OSError("fsync failed")` on the error-path
  write. Assert: exit 2 with the dispatch error message in stderr
  (`"perplexity: HTTP 401"` or similar), `OSError` appears as
  `__cause__` in the traceback when `--verbose` is set, and the
  WikiError exit code (2) wins over any `OSError` propagation.
- `test_wiki_add_research_perplexity_pulls_research_via_requires` —
  `wiki add infrastructure:research-perplexity` against a fresh
  vault installs both primitives; the journal shows two
  `primitive.install` events with `research` strictly before
  `research-perplexity`, plus one `managed_region.write` event for
  `research-providers.yaml:providers`.
- `test_wiki_research_recipes_do_not_include_primitives` — load
  `recipes/family.yaml`, `recipes/work-os.yaml`, `recipes/personal.yaml`;
  assert neither `infrastructure:research` nor
  `infrastructure:research-perplexity` is in `primitives:`.

**Approach:** Replace the `_cmd_research` stub. Boundary-check
vault, validate `--out` path resolves under vault root (reject
absolute, `..`, and symlink-out-of-tree at the CLI boundary), call
`research.dispatch_query`.

- **Stdout flow:** one journal event total. Append `result.event`
  via `append_event(journal_path, ...)` (bare — no `transaction`),
  then `print(result.markdown)`. Exit 0.
- **`--out` flow:** two journal events (`research.query` plus
  `page.write` or `page.proposal`). Wrap the pair in
  `journal.transaction(journal_path, by="wiki-research",
  reason=f"research {provider_slug}")` so a concurrent `wiki add`
  cannot interleave its events between them. Inside the
  transaction: `append_event(result.event)` then
  `safe_write(out_path, result.markdown, by="wiki-research",
  journal_path=journal_path)`.
- **Error path:** on `ResearchDispatchError`, append `exc.event` —
  wrapped in `try/except OSError: raise dispatch_exc from
  journal_exc` so the user sees the dispatch message, not the
  journal error (spec invariant 10). Then re-raise; CLI boundary
  in `main()` catches as `WikiError` and exits 2.

Add `--provider` and `--out` flags to the existing subparser, both
optional. The `_cmd_research` body is ~50 lines; everything
substantive lives in `research/`.

**Gate:** `pytest tests/integration/test_wiki_research.py
&& mypy llm_wiki_kit/cli.py && ruff check llm_wiki_kit/cli.py
&& ruff format --check llm_wiki_kit tests`.

---

### Step 6 — ADR-0007 (shared infra config files at vault root) is drafted and accepted in this PR.

**Tests:** none new — the ADR is documentation. The acceptance
check is the `lint-agent-artifacts.sh` linter (frontmatter shape)
and a human reviewer accepting the rationale on PR review.

**Approach:** Copy `docs/_templates/adr.md` to
`docs/adr/0007-shared-infra-config-files-at-vault-root.md`. Fill in
Context (the `install.py:_snippet_filename` no-`/` rule, ADR-0003's
illustrative `.claude/` mention, the ADR-0006 aggregator
constraint), Decision (vault-root is the default; one rule for all
multi-provider configs; revisit when an aggregator that supports
sub-path targets exists), Consequences (positive: works today with
zero `install.py` changes; negative: vault root accumulates infra
files as more provider primitives ship, mitigated by the
managed-region delimiters keeping them visibly kit-owned), and
Alternatives (extend aggregator to encode sub-paths via flat
naming like `.claude__file.region`; ship a parallel write path that
bypasses the aggregator for cross-dir targets — both deferred).
Mark `Status: Accepted` on merge (step 8's checklist enforces).
Reference from spec §Constraints and from `recipes/family.yaml` /
similar at the point where any future research recipe declares the
primitive.

**Gate:** `bash tools/lint-agent-artifacts.sh` (or whichever ADR
linter runs in CI).

---

### Step 7 — Both primitives exist on disk, validate against ADR-0006's contribution shape, and a fresh vault with both installed produces the expected `research-providers.yaml`.

**Tests** (`tests/integration/test_wiki_research.py` continued, and
`tests/unit/test_primitives.py` for shape):

- `test_primitive_research_loads` — `load_primitive(templates/infrastructure/research)`
  yields a `Primitive` with kind `infrastructure`, no
  `contributes_to`, a `files/` tree containing
  `research-providers.yaml` (with empty managed region).
- `test_primitive_research_perplexity_loads` — `load_primitive(templates/infrastructure/research-perplexity)`
  yields a `Primitive` with `requires: ["research"]`,
  `contributes_to: [{file: research-providers.yaml, region:
  providers}]`, and a snippet at
  `regions/research-providers.yaml.providers`.
- `test_primitive_research_perplexity_validate_contributions_clean`
  — `install.validate_contributions(primitive, root)` passes.
- `test_wiki_add_renders_config_round_trip` — init a bare vault,
  install both primitives, then `ResearchProvidersConfig.model_validate(
  yaml.safe_load(managed_regions.parse(open(...).read())["providers"]))`
  yields exactly one provider entry (`perplexity`) with
  `api_key_env="PERPLEXITY_API_KEY"`, `endpoint=DEFAULT_ENDPOINT`,
  `model="sonar-pro"`, `cost_signal="low"`, and a non-empty
  `strengths` list. Disjoint from Step 5's
  `test_wiki_add_research_perplexity_pulls_research_via_requires`,
  which asserts the *journal event ordering* and requires-closure
  behaviour but does not inspect the rendered config body.

**Approach:** Create the `templates/infrastructure/` parent dir
(does not exist yet) plus the two primitive directories:

```
templates/infrastructure/research/
├── primitive.yaml                     # kind: infrastructure, no contributes_to
└── files/
    └── research-providers.yaml        # seed with empty managed region

templates/infrastructure/research-perplexity/
├── primitive.yaml                     # kind: infrastructure,
│                                       # requires: [research],
│                                       # contributes_to: [{file: ...}]
└── regions/
    └── research-providers.yaml.providers   # the perplexity: block
```

`research-perplexity` declares `requires: [research]` so the
requires-closure pulls in the seed-file primitive whenever a user
installs only the provider. No `files/` tree on `research-perplexity`
— the primitive's only job is to contribute the region snippet, per
spec §Non-goals ("Vault-side wiki-research skill"). A kit-side
reference doc explaining what Perplexity does and what env var it
reads is out of scope for this PR; a future task can add one under
`docs/guides/reference/` if user-facing docs gain that surface.

**Gate:** `pytest tests/unit/test_primitives.py tests/integration/test_wiki_research.py`.

---

### Step 8 — All gates green; PR opened; docs minimally updated.

**Tests:** none new — this step is the convergence check.

**Approach:**

- Run the full gate suite (CONVENTIONS §"Commands you'll need"):
  - `ruff check llm_wiki_kit/`
  - `ruff format --check llm_wiki_kit tests`
  - `mypy llm_wiki_kit tests`
  - `pytest -m 'not slow'`
- Update `docs/architecture/overview.md` (one-paragraph mention of
  the new `research/` subpackage in the module table; ADR-0006
  reference stays unchanged because the aggregator is unchanged).
- If a journal-events reference doc lands in tree later, add the
  two new `ResearchQueryEvent` fields (`model`, `status`) to the
  `research.query` row. No such doc exists today; this is a
  forward-looking reminder, not a Step 8 action.
- **Flip `docs/adr/0007-shared-infra-config-files-at-vault-root.md`
  from `Status: Proposed` to `Status: Accepted`** (CONVENTIONS §
  "How to add an ADR" step 5 — on merge, flip Status and don't
  touch the ADR again).
- `git commit -m "v2: task 18 - research dispatch + perplexity provider"`
  (no Claude co-author trailer per memory).
- `gh pr create --base main` against `eugenelim` account (no Claude
  Code footer in body per memory).

**Gate:** Pre-PR hook (`tools/hooks/pre-pr.sh`) clean. CI green on
opening.

## Verification gate

The whole plan succeeds when:

```
ruff check llm_wiki_kit/
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

are all green, and every contract test enumerated in
`spec.md` §"Acceptance criteria" passes. The `not slow` filter
excludes the wheel-acceptance suite, which is unaffected by this
change.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| The vault-root-vs-`.claude/` decision is the wrong call long-term. | medium | ADR-0007 (drafted in this PR) documents the call, the constraint it lives under (aggregator's no-`/` rule), and the trigger for revisiting (an aggregator that supports sub-path targets). The path is a one-line primitive-yaml change if the decision flips. |
| Task 17 lands first and conflicts on `cli.py` / `models.py`. | medium | Both diffs are additive. Likely conflict surface: the import block at the top of `cli.py` (both add imports near each other) and adjacent `_cmd_*` definitions / subparser registration in `build_parser`. Three-way merge usually handles both; manual fix is mechanical. Whichever lands second rebases on main. |
| Perplexity API shape drifts. | low | The provider function is the only place touching the wire format; isolated fixture lets us update one spot. |
| Retry helper subtly retries something it shouldn't (e.g. 422 on bad model name). | medium | The retry-eligible set is explicit (429, 5xx, URLError, socket.timeout); tests pin 401 and 500 individually, 422 falls through to "not in retry-eligible set → raise immediately." |
| API key leaks into a markdown page, log, journal, or exception `repr`. | high impact, low likelihood | Spec invariant 2 names all four surfaces. Three dedicated tests (`test_research_http_error_repr_omits_request_objects`, `test_perplexity_dispatch_key_redacted_in_errors`, `test_wiki_research_apikey_never_in_journal_or_stdout`, the last covering stdout+stderr including `--verbose` traceback paths). `ResearchHTTPError`/`ResearchDispatchError` constructors are constrained to message + status code + event; nothing else stored. |
| stdlib `urllib` + TLS misconfiguration on user machines. | low | We rely on default OS trust store; if a user hits this, error message names `urllib.request.urlopen` so they can diagnose. Not Task 18's job to ship a CA bundle. |

## Out of scope

- Vault-side `wiki-research` SKILL.md and its eval fixtures. Lands
  separately (likely Task 19 or 20).
- `--stream` flag, partial output.
- A `type: research-source` content-type primitive that captures the
  markdown answer with a richer frontmatter (`verification_strength`,
  `source_kind`, cross-link to a project page).
- Picker / two-source dispatch.
- Gemini + Semantic Scholar providers (Task 19).
- `wiki upgrade` semantics for adding/removing providers from an
  existing vault (covered by the generic upgrade task later in
  Phase E).
- The "providers config under `.claude/`" path question — ADR-0007
  pins the current vault-root rule and names the revisit trigger.
