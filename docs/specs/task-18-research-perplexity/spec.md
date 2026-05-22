# Spec: research dispatch + Perplexity provider

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit.research`, `templates/infrastructure/research`,
  `templates/infrastructure/research-perplexity`
- **Related:** RFC-0001 §"Task 18 — Research dispatch + Perplexity",
  `docs/specs/task-18-research-perplexity/plan.md`
- **Constrained by:** ADR-0001 (stdlib rendering), ADR-0002 (journal as
  state truth), ADR-0003 (managed regions), ADR-0004 (drift detection +
  `safe_write`), ADR-0005 (Pydantic for disk-bound schemas), ADR-0006
  (additive managed-region contributions), ADR-0007 (shared infra
  config files at vault root — drafted alongside this spec).
  AGENTS.md "Runtime dependencies" (no new runtime deps without an ADR).

## What this is

The kit-side surface that lets `wiki research <query>` route a query to
a configured research provider and journal the dispatch, plus the two
infrastructure primitives that make Perplexity the first such provider
(`infrastructure:research` for the shared config, `infrastructure:research-perplexity`
for the provider block). The kit calls the provider's HTTP API
**in-process** from `llm_wiki_kit/research.py`; no subprocess, no
`wiki-research` skill in scope, no vault-side LLM orchestration. The
CLI emits the provider's markdown answer to stdout by default — or
to a vault path via `--out`, in which case the write goes through
`safe_write` like every other kit-to-vault write.

This spec does **not** define a content-type (`research-source` page
shape), the picker logic (v1's `pick-provider.py` scoring table), or
Gemini / Semantic Scholar providers — Task 19 owns the latter.

## Inputs

### From the user (CLI surface)

```
wiki research <query> [--provider <name>] [--out <path>]
```

| Arg | Type | Required | Meaning |
|---|---|---|---|
| `query` | positional string | yes | the research query, passed to the provider verbatim |
| `--provider` | string | no | provider slug (e.g. `perplexity`); resolves against installed `research-providers.yaml` blocks. Required when more than one provider is installed |
| `--out` | path | no | vault-relative output path; when set, the markdown answer is written there via `safe_write` and `result_path` is recorded in the journal |

### From the vault filesystem

- `./research-providers.yaml` at the vault root, owned by the
  `infrastructure:research` primitive's seed file (containing one
  managed region named `providers`) and contributed-into by every
  installed `infrastructure:research-*` provider primitive. See
  §"Constraints" below for why this lands at vault root rather than
  `.claude/research-providers.yaml` as ADR-0003's illustrative
  examples suggest; an ADR-0007 in the same PR pins the rule.
- `./.wiki.journal/journal.jsonl` — the canonical journal.

### `ProviderConfig` schema

Each block under the managed region is one `ProviderConfig`. The
fields:

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `api_key_env` | `str \| None` | no | `None` | environment-variable name to read the provider's API key from at dispatch time. Optional at the schema level so future providers (e.g. Task 19's Semantic Scholar, which works without a key) can omit it; per-provider code (e.g. Perplexity's `dispatch`) raises `WikiError` if its own requirements aren't met. |
| `endpoint` | `str` | no | provider-specific (e.g. `https://api.perplexity.ai/chat/completions` for Perplexity) | provider HTTP endpoint; users override here to point at a proxy or alternate region |
| `model` | `str \| None` | no | provider-specific (e.g. `sonar-pro` for Perplexity) | model name passed verbatim to the provider |
| `cost_signal` | `Literal["free","low","medium","high"] \| None` | no | `None` | informational tag used by future picker logic; the v0.1 dispatcher ignores it |
| `strengths` | `list[str]` | no | `[]` | informational tags (`current_web_state`, `peer_reviewed`, …); future picker reads them |

`extra="forbid"` is inherited from `_StrictModel`. The CLI surfaces
validator errors as one-line `WikiError("invalid research-providers.yaml: <field>: <message>")`.

The top-level shape is a flat mapping `<provider_slug>: ProviderConfig`
— no wrapping `providers:` key. The dispatcher reads only the
managed-region body via `managed_regions.parse` and YAML-loads that
slice; the host file's `# BEGIN MANAGED` / `# END MANAGED` markers
and any user-added text outside the managed region are preserved on
disk (ADR-0003) but ignored by the dispatcher.

### From the environment

- `PERPLEXITY_API_KEY` — Perplexity API key. Read at dispatch time
  only; never logged, never journaled, never written to a markdown
  output page. Missing env var raises `WikiError` *before* the HTTP
  call.

### From other primitives

`infrastructure:research-perplexity` declares `requires: [research]`.
The dependency edge is load-bearing for two reasons:

1. **Seed-file pre-condition.** ADR-0006 §Mechanics step 5 requires
   every primitive's `files/` tree on disk before the aggregator runs.
   `research-providers.yaml` is shipped by `infrastructure:research`'s
   `files/` tree, so installing `research-perplexity` alone — without
   `research` — would have the aggregator attempt `safe_write_region`
   on a non-existent file and raise `FileNotFoundError` *after* the
   `primitive.install` event was already journaled (a half-install).
   `_cmd_add` pulls the requires-closure (`cli.py:_expand_closure`) so
   `wiki add infrastructure:research-perplexity` against a fresh vault
   installs both atomically.
2. **Topological order matches alphabetical-by-coincidence today.**
   With `requires:` set, `primitives.resolve_dependencies` puts
   `research` strictly before `research-perplexity` regardless of
   future name choices. Future provider primitives (`research-gemini`,
   `research-semantic-scholar` in Task 19) declare the same
   `requires: [research]` and inherit the guarantee.

## Outputs

### To stdout (default)

A self-contained markdown document — frontmatter block followed by an
answer body — with at minimum these frontmatter keys, in this order:

```markdown
---
provider: perplexity
model: sonar-pro
query: <the user's query, YAML-escaped by yaml.safe_dump>
fetched_at: 2026-05-17T08:51:00+00:00
citations:
  - https://example.com/a
  - https://example.org/b
---

<answer body, verbatim from the provider's `choices[0].message.content`>
```

Output is UTF-8 text, no trailing whitespace policed beyond a single
trailing newline.

**Frontmatter / body boundary.** The closing `---` line is the only
`---`-on-its-own-line between the first line and the start of the
body. Frontmatter is rendered via `yaml.safe_dump`, which never emits
a bare `---` for scalar values. Between the closing `---` and the
body there is exactly one blank line. The body is then the provider's
content **verbatim** — including any `---` it may contain, which
naive frontmatter parsers may misread as a second frontmatter
terminator. The kit considers any line *before* the first blank line
following the closing `---` to be header; the body starts at the
first non-empty line after that. The contract is pinned with a
construction test against a body that contains `---`.

### To `--out <path>`

Same markdown document, written via `write_helper.safe_write` to the
resolved vault-relative path. Drift detection applies — if the path
already has on-disk content differing from a previously journaled
`page.write`, the call falls through to the `.proposed` sidecar flow
(ADR-0004). The CLI returns the same `WriteResult.WRITTEN` /
`WriteResult.PROPOSAL` outcome shape as every other write surface.

### To the journal

Exactly one `ResearchQueryEvent` (literal `research.query`, already
declared in `models.py` from RFC-0001 Task 3) per invocation,
appended **before** the HTTP call returns. The event class gains two
optional fields with defaults so older journal lines keep replaying
(ADR-0002 §Negative additive-schema rule):

| Field | Existing | Added by this spec | Meaning |
|---|---|---|---|
| `query` | yes | — | the user's query string |
| `provider` | yes | — | provider slug (`perplexity`) |
| `result_path` | yes | — | vault-relative path when `--out` was used, else `None` |
| `model` | — | `str \| None = None` | the resolved model (e.g. `sonar-pro`) |
| `status` | — | `Literal["ok","error"] = "ok"` | dispatch outcome |

`citations` are not journaled — they live on the markdown page's
frontmatter as first-class data, which is the same place v1 put them
(per the v1 design doc). Putting them in the journal would either
duplicate the page-side truth (drift surface) or make `journal grep`
the source of truth (which it isn't — ADR-0002).

The event is recorded on **every** outcome — successful dispatch,
HTTP error, malformed provider response — distinguished by `status`.
Missing `PERPLEXITY_API_KEY` and "provider not installed" raise
`WikiError` *before* any journal write, since those are
user-configuration errors and journaling them would pollute the audit
trail.

### To `research-providers.yaml`

Each installed provider primitive contributes one snippet into the
managed region `providers`. After install, the file looks like:

```yaml
# llm-wiki-kit research providers config.
# Edits outside the BEGIN/END markers below are preserved.

# BEGIN MANAGED: providers
perplexity:
  api_key_env: PERPLEXITY_API_KEY
  endpoint: https://api.perplexity.ai/chat/completions
  model: sonar-pro
  cost_signal: low
  strengths:
    - current_web_state
    - cited_factual_lookup
# END MANAGED: providers
```

The seed (from `infrastructure:research`) ships an empty
`# BEGIN MANAGED: providers` / `# END MANAGED: providers` block plus
the heading comment.

## Behavior

### Happy path — stdout dispatch

1. Resolve vault root (`Path.cwd()`). Refuse if there's no
   `.wiki.journal/journal.jsonl` (same boundary check as `wiki add`,
   `wiki doctor`, `wiki ingest`).
2. If `--out` is set, resolve the path under the vault root. Reject
   absolute paths, paths that resolve outside the vault root via
   `..`, and paths whose resolved location escapes via a symlink in
   the parent chain (`Path.resolve(strict=False)` on the parent;
   compare against `vault_root.resolve()`). This check runs *after*
   the vault boundary check and *before* config-load, env-var-read,
   or any HTTP attempt — so an invalid `--out` against a vault with
   no providers config surfaces the path error, not the config
   error.
3. Load and validate `research-providers.yaml` via
   `models.ResearchProvidersConfig`. Missing file is a
   `WikiError("infrastructure:research not installed")`. Empty managed
   region (zero providers) is `WikiError("no research providers
   installed")`.
4. Pick the provider:
   - If `--provider` is set, look it up in the config. Missing →
     `WikiError(f"provider '{name}' not installed")`.
   - If `--provider` is unset and exactly one provider is in the
     config, use it.
   - Otherwise → `WikiError("pass --provider <name>")` (the message
     also lists installed slugs).
5. Call the chosen provider's in-process function
   (`llm_wiki_kit.research.providers.perplexity.dispatch`) with the
   resolved config + query. The provider owns its own
   pre-conditions: Perplexity's `dispatch` raises
   `WikiError(f"set {api_key_env} in the environment")` when its
   configured `api_key_env` is unset, *before* the HTTP call. Future
   providers (Task 19's Semantic Scholar) may not require a key and
   skip that check.
6. Render the markdown document (frontmatter + body) in
   `llm_wiki_kit.research`.
7. Append the `ResearchQueryEvent` (with `status="ok"`, `model=`
   resolved model, `result_path=None` for stdout / `<path>` for
   `--out`).
8. Write — to stdout via `print` *or* to `--out` via `safe_write`.
   `safe_write` may return `WriteResult.PROPOSAL`; the CLI surfaces
   the same one-line `.proposed` message `_cmd_ingest` already uses.
9. Return exit 0.

Event-before-write ordering matches the safe-write-ordering spec:
journal first (fsync'd), disk second. A crash between the two leaves
a journaled intent that `wiki doctor` reconciles.

**Multi-event grouping (`--out` only).** The `--out` flow emits two
events — `research.query` and (via `safe_write`) either `page.write`
or `page.proposal`. The CLI wraps that pair in
`journal.transaction(journal_path, by="wiki-research",
reason="research <slug>")` so a concurrent `wiki add` running in
parallel cannot interleave its own events between the pair. The
stdout flow emits one event and runs bare. The journal-locking spec
(`docs/specs/journal-locking/spec.md`) names `transaction()` as the
bracket for multi-event operations a concurrent writer could
interleave; `wiki research --out` is the first non-`wiki lock`
caller to need it. `_cmd_init` and `_cmd_add` use the
`journal.use_journal_cache(...)` scope rather than `transaction()`
— a baseline-lookup amortisation, not a bracketing primitive;
broader bracketing of those callers is out of scope for Task 18.
The transaction's `lock.acquired` / `lock.released` brackets surface
in `wiki journal tail` so an operator can see which `page.write`
belongs to which research run.

### Error paths

| Cause | Surface | Exit | Journaled? |
|---|---|---|---|
| Not a vault (no `.wiki.journal/`) | `WikiError` → stderr | 2 | no |
| `research-providers.yaml` missing | `WikiError("infrastructure:research not installed")` | 2 | no |
| Config file fails Pydantic validation | `WikiError` wrapping the validator message | 2 | no |
| Managed region empty / no providers | `WikiError("no research providers installed")` | 2 | no |
| `--provider` names a provider not in config | `WikiError(f"provider '{name}' not installed; installed: {names}")` | 2 | no |
| Multiple providers, no `--provider` | `WikiError("pass --provider <name>")` (message also lists installed slugs) | 2 | no |
| API-key env var unset | `WikiError(f"set {api_key_env} in the environment")` | 2 | no |
| Provider HTTP non-2xx after retries | `WikiError("perplexity: HTTP 429 after 3 retries")` (or similar) | 2 | yes, `status="error"`, `result_path=None` |
| Provider malformed JSON | `WikiError("perplexity: malformed response")` | 2 | yes, `status="error"` |
| Network connect/timeout after retries | `WikiError("perplexity: connection failed after 3 retries")` | 2 | yes, `status="error"` |
| Configured provider slug has no registered implementation in this kit version (e.g. user hand-added `gemini:` block before Task 19 lands) | `WikiError(f"provider '{name}' has no implementation in this kit version")` | 2 | no |
| `--out` path resolves outside vault root | `WikiError("--out path must resolve under the vault root")` | 2 | no |
| `--out` write hits drift | one-line "proposal" notice on stdout, exit 0 | 0 | yes, `status="ok"`, `result_path=<requested path, not the .proposed sidecar>`; the `page.proposal` event records the sidecar path separately |

Configuration-shaped errors (rows 1–7) don't journal because they
happen before the dispatch attempt; runtime-shaped errors (HTTP /
network / malformed-response) do journal because the user's intent
("research this query") existed and the audit trail should record
that the kit tried.

### HTTP behavior (Perplexity)

- POST to the configured `endpoint` (default
  `https://api.perplexity.ai/chat/completions`).
- Headers: `Authorization: Bearer <api_key>`, `Content-Type: application/json`,
  `User-Agent: llm-wiki-kit/<version>`.
- Body: JSON with `model` (from config), `messages: [{role: user,
  content: <query>}]`, no other fields. Provider-specific knobs
  (temperature, etc.) are out of scope; the spec doesn't add a knob it
  doesn't need.
- Timeout: 60 seconds per attempt.
- Retry: up to **3 retries after the initial attempt** (4 attempts
  total) on HTTP 429, 5xx, or `URLError`/`socket.timeout`. Backoff
  between retries: 1s, 2s, 4s (`2 ** attempt` with `attempt ∈ {0, 1,
  2}`; no jitter — small N, deterministic is easier to test). Other
  4xx (401, 403, 404, 422) are surfaced immediately without retry.
- `urllib.request.Request` + `urllib.request.urlopen` from stdlib. No
  `requests`, no `httpx`. The retry helper lives in
  `llm_wiki_kit.research.http` and is tested in isolation.

### Dispatcher return-and-raise contract

`research.dispatch_query(query, provider_slug, vault_root, *, now) ->
DispatchResult` is the orchestrator. The result class is:

```python
@dataclass(frozen=True)
class DispatchResult:
    markdown: str
    event: ResearchQueryEvent
```

On any *runtime* failure (HTTP error, malformed response, network)
the dispatcher raises `ResearchDispatchError(WikiError)` carrying the
prepared `status="error"` event:

```python
class ResearchDispatchError(WikiError):
    def __init__(self, message: str, *, event: ResearchQueryEvent) -> None:
        super().__init__(message)
        self.event = event
```

`_cmd_research` catches `ResearchDispatchError`, appends `exc.event`
to the journal *before* re-raising, then the CLI boundary in
`main()` prints `str(exc)` and exits 2. *Configuration* failures
(missing file, no providers, unknown provider, missing env var)
raise plain `WikiError` with no event — they happen before the
dispatch attempt and there's no user intent to audit.

**Provider-author rule for Task 19 and beyond.** A new provider's
`dispatch(config, query)` raises `ResearchHTTPError` for
runtime-shaped failures (HTTP status, malformed response, network)
and plain `WikiError` for config-shaped failures (missing env var,
unsupported model). The dispatcher catches only `ResearchHTTPError`
and wraps it as `ResearchDispatchError`; `WikiError` propagates
unwrapped so the audit trail records only requests the user's
config licensed the kit to make. Mixing the two exception types up
in a new provider will produce phantom `status="error"` events.

### Citations parsing

Perplexity's response shape (v1 confirmed):
`{"choices": [{"message": {"content": "..."}}], "citations": ["url", ...]}`.
The kit reads `citations` from the response and renders them under the
markdown frontmatter's `citations:` key. If `citations` is missing
(some Perplexity variants omit it), the field is rendered as `citations: []`.

### Stdin / paste

Out of scope. v0.1 has no `wiki research -` form. A future task can
add it the same way `wiki ingest -` works.

## Invariants

1. **No new runtime dependency lands.** The HTTP path uses
   `urllib.request` and the JSON path uses `json` from stdlib. The
   only runtime imports outside stdlib are `pydantic` and `pyyaml`,
   already committed. (AGENTS.md §Runtime dependencies.)
2. **API keys never reach disk, logs, journals, or exception
   surfaces.** Not in journal events, not in markdown output, not in
   `WikiError` messages, not in `repr(exc)` for any exception the
   HTTP or provider layers raise. The `ResearchHTTPError` and
   `ResearchDispatchError` constructors **must not** store the
   request headers dict, the `urllib.request.Request` object, the
   request body, or any other key-bearing object in `exc.args`. The
   only fields they carry are a redacted human message, a numeric
   status code (when known), and the prepared event (which itself
   has no key field). The HTTP layer must not `logger.debug(headers)`,
   `print(request)`, or otherwise emit a stringified header dict —
   the headers dict lives in the `urllib.request.Request` local
   variable that goes out of scope on raise, and nothing else holds
   a reference to it. Verified by `repr()`-grep tests in
   `tests/unit/test_research_http.py` and
   `tests/unit/test_research_perplexity.py`.
3. **Exactly one `ResearchQueryEvent` per CLI invocation,** appended
   *before* the disk write (`--out`) or stdout emit. Duplicate-query
   suppression is not a kit concern — every invocation produces one
   event regardless of whether the kit has seen the same `(provider,
   query)` pair before. Audit-trail-first.
4. **The kit is opt-in.** Neither primitive appears in `family.yaml`,
   `work-os.yaml`, or `personal.yaml`. A `wiki init --recipe family`
   on a fresh vault has no `research-providers.yaml` and `wiki
   research` exits 2 with the "not installed" message.
5. **`research-perplexity` cannot install without `research`.** The
   `requires: [research]` edge plus `_cmd_add`'s requires-closure
   make `wiki add infrastructure:research-perplexity` against a fresh
   vault install both primitives atomically. A half-install (provider
   primitive's `primitive.install` event journaled, seed file
   missing, aggregator crashes on `FileNotFoundError`) is not
   reachable.
6. **`safe_write` is the only vault-write path.** The `--out` flow
   uses `safe_write(path, content, by="wiki-research", journal=...)`.
   The dispatcher does not call `Path.write_text` against a vault
   path.
7. **The Pydantic config model rejects unknown keys inside any
   `ProviderConfig` block.** A typo in a managed-region snippet
   (`endpiont:` instead of `endpoint:`) fails at config-load time,
   before the HTTP attempt, with a `WikiError("invalid
   research-providers.yaml: <field>: <message>")` whose `<field>`
   includes the typo. The top-level shape itself is a flat mapping
   `<provider_slug>: ProviderConfig` (a Pydantic `RootModel`) — any
   string key becomes a candidate provider slug; the "unknown
   implementation" path catches unregistered slugs separately (see
   §Error paths).
8. **`--out` paths must resolve under the vault root.** Absolute
   paths, paths starting with `..`, and symlinks that resolve outside
   the vault root are rejected with `WikiError` *before* any HTTP
   call. Pinned by a contract test.
9. **Frontmatter / body boundary is robust to body-side `---`.**
   The kit's renderer emits exactly one closing `---` followed by one
   blank line; any `---` line inside the answer body is body content.
   A round-trip test parses the rendered markdown back via
   `yaml.safe_load` on the frontmatter slice and assert the body
   slice contains the verbatim provider content (including any
   embedded `---`).
10. **Failure during the error-path journal-append surfaces the
    dispatch error, not the journal error.** If `_cmd_research`
    catches a `ResearchDispatchError` and the subsequent
    `append_event(exc.event)` itself raises (fsync failure, lock
    contention), the CLI raises the original
    `ResearchDispatchError` with the journal exception as
    `__cause__`. The user sees the dispatch message; the journal
    failure surfaces via `--verbose` traceback or `wiki doctor`'s
    later inspection. The opposite order (journal exception masks
    the dispatch error) loses the user's actionable signal and is
    explicitly forbidden.

## Contracts with other modules

| Caller / callee | Contract |
|---|---|
| `cli._cmd_research` → `research.dispatch_query` | Pass `(query, provider_slug_or_none, vault_root, now)`. On success: receive a `DispatchResult(markdown, event)`. On runtime failure: catch `ResearchDispatchError`, journal `exc.event`, re-raise. On config failure: catch `WikiError`, let `main()` surface. CLI owns journal append + stdout-vs-`safe_write` write decisions. |
| `research.dispatch_query` → `models.ResearchProvidersConfig.model_validate(yaml.safe_load(...))` | Standard Pydantic load; raises `ValidationError`, caller wraps as `WikiError`. |
| `research.dispatch_query` → `research.providers.perplexity.dispatch` | Pass `(config: ProviderConfig, query: str)`; receive `PerplexityResult(answer: str, citations: list[str], model: str)`. The provider function knows nothing about journals or vaults — pure HTTP + parse. |
| `research.providers.perplexity.dispatch` → `research.http.request_json` | Pass `(method, url, headers, json_body, timeout, retries)`; receive `dict` or raise `ResearchError`. The retry helper is provider-agnostic so Task 19 reuses it. |
| `cli._cmd_research` → `journal.append_event(ResearchQueryEvent(...))` | Standard journal append. The event is fsync'd before the response page is written or stdout flushed. |
| `cli._cmd_research` → `write_helper.safe_write` (only when `--out` set) | Call signature: `safe_write(path, content, by="wiki-research", journal_path=...)` — keyword name `journal_path`, matching `write_helper.safe_write`'s signature. Returns `WriteResult.WRITTEN` or `WriteResult.PROPOSAL`. |
| `infrastructure:research` primitive → seed file | `templates/infrastructure/research/files/research-providers.yaml` is shipped by the primitive's `files/` tree; the install pipeline lays it down before the aggregator runs. |
| Provider primitive → managed region | Each provider primitive declares `contributes_to: [{file: research-providers.yaml, region: providers}]` and ships `regions/research-providers.yaml.providers` with its block. |

## Acceptance criteria

These are the contract tests. Every one of them lives in
`tests/unit/test_research.py`, `tests/unit/test_models.py` (for the
config-shape tests), or `tests/integration/test_wiki_research.py`.
Each is the bar for "done"; plan.md sequences them.

### Config + models

- [ ] `ProviderConfig` requires `api_key_env`; `model`, `endpoint`,
      `cost_signal`, `strengths` are optional with the defaults
      listed in §"ProviderConfig schema".
- [ ] A typo in a snippet (`endpiont: ...`) surfaces from the CLI as
      `WikiError("invalid research-providers.yaml: ...")` and the
      message includes the literal bad-field name. (Verifies user-
      facing error shape, not Pydantic's `extra="forbid"`, which is
      proven by `_StrictModel`'s base test.)
- [ ] `ResearchProvidersConfig` parses a flat YAML mapping
      `<provider_slug>: ProviderConfig` — no wrapping `providers:`
      key. The managed-region body is the mapping; the host file's
      `# BEGIN MANAGED` / `# END MANAGED` comments are ignored by
      YAML.
- [ ] An older journal line (`{"type":"research.query","query":"…",
      "provider":"perplexity","result_path":null,…}` with no
      `model` / `status`) re-parses cleanly after the model gains the
      two new optional fields, preserving ADR-0002's additive-schema
      invariant.

### Dispatcher

- [ ] `research.dispatch_query` raises `WikiError("infrastructure:research not installed")`
      when `research-providers.yaml` is absent.
- [ ] `research.dispatch_query` raises `WikiError("no research providers installed")`
      when the providers region is empty.
- [ ] With one provider installed and no `--provider`, the dispatcher
      uses that provider.
- [ ] With two providers installed and no `--provider`, the dispatcher
      raises `WikiError("pass --provider <name>")` and lists installed
      slugs.
- [ ] `--provider <unknown>` (slug not in the config file) raises
      `WikiError` and lists installed slugs.
- [ ] Slug in config but no registered implementation (e.g. config
      has `gemini:` block, but `gemini` is not in the provider
      registry — Task 18 ships only `perplexity`) raises
      `WikiError(f"provider '{name}' has no implementation in this
      kit version")`. Pinned to defend against hand-edited configs
      that get ahead of the kit's installed provider set.

### Perplexity provider (with mocked urllib)

- [ ] On HTTP 200 with `choices[0].message.content` and `citations`,
      the dispatcher returns a `PerplexityResult` whose fields match
      the response.
- [ ] The request sends `Authorization: Bearer <key>` and reads the
      key from `os.environ[api_key_env]`.
- [ ] Missing env var raises `WikiError(f"set {api_key_env} in the
      environment")` *without* attempting an HTTP request.
- [ ] HTTP 429 → retried up to 3 times after the initial attempt (4
      attempts total) with backoff 1s, 2s, 4s; on the fourth failure
      raises `WikiError("perplexity: HTTP 429 after 3 retries")`.
      Backoff timings are asserted against a fake `time.sleep` so the
      test runs without delay; sleep history equals `[1.0, 2.0, 4.0]`.
- [ ] HTTP 401 → raised immediately (no retry) as `WikiError("perplexity: HTTP 401")`.
- [ ] Malformed JSON in response body → `WikiError("perplexity: malformed response")`.
- [ ] Connect timeout / `URLError` → retried, eventually raised as
      `WikiError("perplexity: connection failed after 3 retries")`.
- [ ] The API key never appears in `str(exc)`, `repr(exc)`, or
      `exc.args` for any `ResearchHTTPError` or `ResearchDispatchError`
      raised by the HTTP or Perplexity layers — even when the
      underlying `HTTPError` / `URLError` chain carries the request
      object. Verified by `assert api_key not in repr(exc)` against a
      recognisable key (`sk-DO-NOT-LOG`).

### Markdown rendering

- [ ] The rendered markdown has YAML frontmatter with `provider`,
      `model`, `query`, `fetched_at`, `citations`, followed by the
      answer body.
- [ ] `fetched_at` is an ISO-8601 string in UTC with the literal
      `T` separator between date and time and a `+00:00` offset
      (matches the kit's other timestamps). The renderer must
      `.isoformat()` the timestamp before passing it to YAML —
      `yaml.safe_dump` on a `datetime.datetime` would otherwise emit
      the space-separated `!!timestamp` form. Verified by
      `assert "T" in fm["fetched_at"]` and `assert
      fm["fetched_at"].endswith("+00:00")`.
- [ ] `citations: []` is rendered when the provider returned no
      citations field; the test pins the literal YAML shape.
- [ ] The body is the provider's content verbatim — no
      post-processing, no quoting, no markdown sanitisation. (Whatever
      the provider returns is what the user gets to review.)
- [ ] **YAML-escape safety on the query field.** A query containing
      double quotes, embedded newlines, a leading `---`, control
      characters, and non-ASCII characters round-trips: render the
      markdown, `yaml.safe_load` the frontmatter slice, assert the
      `query` field equals the original string byte-for-byte.
- [ ] **Body-side `---` does not corrupt the frontmatter boundary.**
      A provider response whose `content` is `"intro\n---\nmore"`
      renders to a document where the closing `---` after the
      frontmatter is the *only* `---`-on-its-own-line before the body
      starts (verified by counting `^---$` lines from start of file
      up to the first blank line) and the body slice equals the
      original `content` verbatim.

### CLI integration

- [ ] `wiki research "test"` against a vault with no
      `research-providers.yaml` prints the
      "infrastructure:research not installed" message to stderr and
      exits 2.
- [ ] `wiki research "test"` against a vault with the seed file but
      no provider blocks exits 2 with "no research providers
      installed".
- [ ] After `wiki init --recipe family && wiki add
      infrastructure:research && wiki add infrastructure:research-perplexity`,
      with `PERPLEXITY_API_KEY` set and Perplexity HTTP mocked to
      return a canonical response, `wiki research "test"` prints the
      expected markdown to stdout and appends one
      `research.query` event with `provider="perplexity"`,
      `model="sonar-pro"`, `status="ok"`, `result_path=None`.
- [ ] The same flow with `--out research/test.md` writes the
      markdown via `safe_write`, journals the event with
      `result_path="research/test.md"`, and emits a `page.write`
      event for that path.
- [ ] The same flow with `--out` to a path that already has on-disk
      content differing from the journaled baseline produces a
      `.proposed` sidecar (and the `page.proposal` event), and the
      CLI exits 0 with the one-line proposal notice. The
      `research.query` event records `result_path="research/test.md"`
      (the *requested* path, not the sidecar — the sidecar path lives
      on the `page.proposal` event's `proposed_path`).
- [ ] `--out /etc/passwd` (and `--out ../outside`) raise `WikiError`
      *before* any HTTP attempt; no `research.query` event is
      appended.
- [ ] Calling `wiki research "X"` twice in a row appends exactly two
      `research.query` events (no deduplication; audit-trail-first).
- [ ] **Journal-append failure on the error path preserves the
      dispatch error.** Patched provider raises `ResearchHTTPError`,
      patched `append_event` raises `OSError` on the
      `status="error"` write. The user sees the dispatch message in
      stderr (and `--verbose` shows the `OSError` as `__cause__` in
      the traceback). The `WikiError` exit code (2) wins over any
      `OSError` propagation.
- [ ] **`--verbose` traceback does not leak the API key.** Set
      `PERPLEXITY_API_KEY=sk-DO-NOT-LOG`, patched provider raises
      `ResearchHTTPError`; run `cli.main(["--verbose", "research",
      "q"])`; assert `sk-DO-NOT-LOG` appears in neither stderr nor
      the journal — even with the full traceback printed. Covers the
      `__cause__` chain (HTTPError → ResearchHTTPError →
      ResearchDispatchError) and the `repr(exc)` content the
      traceback formatter walks.
- [ ] `wiki add infrastructure:research-perplexity` against a fresh
      vault (no `research` installed) installs both primitives
      atomically via `_cmd_add`'s requires-closure; the journal shows
      two `primitive.install` events with `research` strictly before
      `research-perplexity`, plus one `managed_region.write` event
      for `research-providers.yaml:providers`.
- [ ] Neither `family.yaml`, `work-os.yaml`, nor `personal.yaml`
      includes `infrastructure:research` or
      `infrastructure:research-perplexity` in its `primitives:` list.
      (Static check via grep + a unit test that loads each recipe and
      asserts membership.)

## Non-goals

- **Vault-side `wiki-research` skill.** Task 18's surface is
  kit-side; the SKILL.md that decides *when* to call `wiki research`,
  what to do with the markdown, and how to cross-link to projects is
  out of scope. The CLI being usable by an experienced operator
  reading `--help` is enough.
- **Provider picker / scoring.** v1's `pick-provider.py`
  (`QUESTION_TYPE_RANKING` × `cost_signal` × pillar gaps) is not
  ported. Single provider explicit-or-only-installed is the v0.1
  selection rule.
- **Source page content-type / frontmatter contract.** The `--out`
  path writes a markdown document with a frontmatter block this spec
  defines, but no `type: research-source` content-type primitive is
  added. That's a candidate for a follow-up task once two-source
  corroboration and verification-strength tagging come into scope.
- **Gemini Deep Research, Semantic Scholar.** Task 19.
- **Two-source dispatch / corroboration.** v1's "load-bearing
  claim → parallel two-provider call" lives in the future SKILL.md,
  not in the CLI.
- **Streaming output.** Stdout is one batch print after the HTTP
  call completes. If `--stream` ever lands, that's a new CLI flag
  with its own contract test.
- **In-vault config edits outside the managed region.** The kit
  preserves them per ADR-0003; this spec doesn't add a UI to mutate
  them. Use a text editor.
- **`wiki upgrade` semantics for provider primitives.** Task-22-era
  concern.
- **Provider `endpoint` allowlist / SSRF protection.** A user (or a
  prompt-injected agent editing the user's config) could point
  ``endpoint:`` at an internal address and exfiltrate the bearer
  token. The kit's threat model is single-user CLI on a trusted
  endpoint; if the kit ever ships inside a hosted runtime, an
  ``ADR-NNNN: research-provider endpoint allowlist`` should pin a
  per-provider host allowlist via a Pydantic ``field_validator`` and
  reject ``file://``/``http://`` schemes outright. Parked here so the
  next reviewer sees it.
- **Stored prompt injection from provider answers** (OWASP-LLM01).
  Perplexity content is rendered into the user's vault verbatim;
  Claude reading that page later picks up injected instructions. A
  follow-up task can add a "untrusted-content fence" wrapper +
  vault-side skill guidance (`core/files/skills/wiki-research/` or
  similar) telling Claude to treat the fenced section as data, not
  instructions. Out of scope for Task 18 — the rendering contract is
  "verbatim" and adding a fence is a behavior change.

## Constraints

- **No new module boundary beyond `llm_wiki_kit/research/`.** The
  package gets one new top-level subdirectory containing
  `__init__.py` (re-exports), `http.py` (retry helper),
  `providers/__init__.py`, `providers/perplexity.py`. A subdirectory
  rather than a flat `research.py` because Task 19 will add two more
  provider files; a one-file module would force a refactor at that
  point. The aggregator stays in `install.py`; CLI wiring stays in
  `cli.py`.
- **No new runtime dependency.** Stdlib only for HTTP and JSON.
  Adding `requests` or `httpx` requires a new ADR per AGENTS.md.
- **No bypass of `safe_write`.** The `--out` path goes through it.
  The stdout path doesn't write to the vault, so the contract doesn't
  apply.
- **No new top-level directory.** This PR creates the
  `templates/infrastructure/` parent (declared but unmaterialised in
  the RFC's "Phase D" plan and the architecture overview) and two
  primitive directories underneath it: `templates/infrastructure/research/`
  and `templates/infrastructure/research-perplexity/`. No top-level
  repo-root directory is added.
- **No new public CLI verb beyond `wiki research`.** It's already
  declared in `cli.py`'s `build_parser` as a stub; this spec promotes
  the stub. `--provider` and `--out` are new flags on the existing
  subcommand.
- **The journal event literal is `research.query` (dot-namespaced),
  not the brief's `research_query` shorthand.** Task 3 (`models.py`)
  already pinned the dotted form for every event type. The brief's
  underscore form is read as a reference to the existing event
  class.
- **`research-providers.yaml` lands at the vault root, not under
  `.claude/`.** Pinned by **ADR-0007** (drafted in the same PR as
  this spec) which makes vault-root the default location for shared
  infrastructure config files under the current managed-region
  aggregator (ADR-0006 / `install.py:_snippet_filename` rejects `/`
  in contribution `file` fields). ADR-0003's worked examples remain
  accurate for the *mechanism* (managed regions); only the example
  path differs. Future ADRs can revisit `.claude/` placement once an
  aggregator that supports sub-path targets exists.
