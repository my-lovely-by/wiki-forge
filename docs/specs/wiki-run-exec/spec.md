# Spec: wiki-run-exec

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Implemented
- **Owner:** `llm_wiki_kit/run.py`, `llm_wiki_kit/cli.py:_cmd_run`
- **Related:** [RFC-0003](../../rfc/0003-scheduling-and-autonomous-execution.md),
  [`docs/specs/task-17-wiki-run/spec.md`](../task-17-wiki-run/spec.md),
  [`docs/specs/wiki-schedule/spec.md`](../wiki-schedule/spec.md)
- **Constrained by:** ADR-0002 (journal as state truth), ADR-0004
  (safe-write), [`task-17-wiki-run/spec.md`](../task-17-wiki-run/spec.md)
  (the dispatch contract this spec extends),
  [RFC-0003](../../rfc/0003-scheduling-and-autonomous-execution.md)
  §"Decisions already made" (shim executor, no SDK, conflict-aware
  refusal), [ADR-0009](../../adr/0009-headless-claude-invocation-contract.md)
  (the `claude -p` argv shape this spec emits — pins what CT-13
  previously deferred), [ADR-0010](../../adr/0010-agent-passthrough-via-claude-agent-flag.md)
  (`--agent <name>` passthrough; **not implemented in v1** — the
  resolution chain depends on RFC-0004, which has not landed),
  [`AGENTS.md` §"Runtime dependencies"](../../../AGENTS.md#runtime-dependencies).

## What this is

`wiki run --exec <operation>` is an opt-in extension of the existing
[`wiki run`](../task-17-wiki-run/spec.md) dispatch boundary. After the
standard dispatch sequence (validate args, journal one
`OperationRunEvent(status="dispatched")`, print the SKILL pointer), the
`--exec` flag causes the kit to additionally:

1. Refuse cleanly if the vault has unresolved drift conflicts inside
   the scoped walk (see §"Conflict-refusal walk scope" below).
2. Locate a user-installed `claude` CLI binary.
3. Invoke it in headless mode against the operation's SKILL, streaming
   stdout / stderr to a per-run log under
   `.wiki.journal/exec-logs/<event_id>.log`.
4. On non-zero exit, timeout, or conflict refusal, journal an
   `OperationExecFailedEvent` and write a per-failure markdown file
   under `inbox/scheduled-failures/<event_id>.md` (each file is
   wholly kit-authored, never re-edited; the user resolves by
   deleting the file).

The kit ships no LLM (CHARTER principle: library-not-application); the
shim executor is the **delegation boundary** between the kit and the
user's local Claude binary. It is opt-in per invocation; the
human-attended `wiki run <op>` (no `--exec`) is unchanged.

## Inputs

CLI invocation:

```
wiki run <operation> [args ...] --exec
wiki run <operation> [args ...] --exec --claude-binary <path>
wiki run <operation> [args ...] --exec --skill-path <path>
```

- `<operation>`, `[args ...]` — exactly as the existing
  [`task-17-wiki-run`](../task-17-wiki-run/spec.md) contract. **No
  changes to argument parsing, REMAINDER handling, kebab/snake
  normalisation, or contract validation.**
- `--exec` — boolean flag. When present, triggers the post-dispatch
  exec sequence. When absent, behavior is byte-identical to the
  existing spec.
- `--claude-binary <path>` — optional explicit override of the Claude
  binary location. Resolution order when `--exec` is set:
  1. `--claude-binary` argument (verbatim path; must be executable).
  2. `WIKI_CLAUDE_BINARY` environment variable (verbatim path; must be
     executable).
  3. `shutil.which("claude")`.
  4. None of the above → `WikiError("--exec set but no claude
     binary found; install Claude Code or pass --claude-binary
     <path>")`. The CLI top-level catches the `WikiError` and
     renders it as a one-line stderr message (the standard
     `WikiError` path — no Python traceback unless `--verbose`).
     In **this no-binary-anywhere branch**, the dispatch event
     **is** journaled at status `dispatched` because the
     orchestrator's binary check runs *after* `dispatch()` returns
     (the exec-failure event is **not** journaled — the failure
     happened before exec started — and no exec attempt was made).
     The kit exits `WIKI_ERROR_EXIT` after rendering the stderr
     message. The success-path dispatch line is **not** printed to
     stdout in this branch because `dispatch_and_exec` raises
     before the CLI orchestrator reaches the success print; the
     journaled `OperationRunEvent` is the user's hook for a
     manual re-attempt (`journal grep` or `wiki run <op>` without
     `--exec`).

  **Set-but-invalid override exception.** When `--claude-binary` or
  `WIKI_CLAUDE_BINARY` names a path that is not an executable file,
  `_locate_claude` raises `WikiError` **before** `dispatch()` runs.
  The CLI invokes `_locate_claude` once pre-dispatch for the
  observability print described under §Outputs, and a set-but-
  invalid override fails fast on that call: no dispatch event is
  journaled, no exec event is journaled, exit `WIKI_ERROR_EXIT`.
  This is the same shape as the `WIKI_EXEC_TIMEOUT` /
  `WIKI_EXEC_LOG_RETENTION_DAYS` validation failures — fail fast
  before any state is written. The user-explicit-path case is
  treated as a typo to surface immediately rather than a
  resolution miss to journal.
- `--skill-path <path>` — optional explicit override of the SKILL
  file location. Default resolution:
  `<vault_root>/.claude/skills/<contract.skill or operation>/SKILL.md`
  — i.e. `contract.skill` when non-empty, falling back to the operation
  name itself (matching [task-17 CT-13](../task-17-wiki-run/spec.md)'s
  SKILL-name fallback). When the resolved path doesn't exist, raise
  `WikiError` with the resolved path and a suggestion to pass
  `--skill-path`.
- Vault root: `Path.cwd()`. Must contain `.wiki.journal/journal.jsonl`
  (same gate as plain `wiki run`).

### Environment variables

The kit reads three `WIKI_*` env vars when `--exec` is set. All have
defaults; none is required.

- `WIKI_CLAUDE_BINARY` — explicit path to the Claude binary, second
  in the resolution order described above (after `--claude-binary`,
  before `shutil.which("claude")`).
- `WIKI_EXEC_TIMEOUT` — integer seconds before the subprocess is
  SIGTERM'd. Default `1800` (30 minutes). After a 5-second grace
  period the kit escalates to SIGKILL. The CLI rejects values
  that aren't parseable as `int` or that are `<= 0` with a
  `WikiError` at exec start, **before** `dispatch()` runs (so no
  dispatch event is journaled for an unparseable timeout — the
  failure shape differs from the binary-missing / budget-shape
  cases, which journal the dispatch event before raising).
- `WIKI_EXEC_LOG_RETENTION_DAYS` — integer days. Logs under
  `.wiki.journal/exec-logs/*.log` older than this are deleted at
  the start of every `--exec` invocation. Default `30`. Set to
  `0` to disable rotation entirely (the kit short-circuits the
  walk — no enumeration of the directory at all).
  The CLI rejects values that aren't parseable as `int` or that
  are `< 0` with a `WikiError` at exec start, before `dispatch()`
  runs (same shape as the timeout-validation case above).
- `WIKI_EXEC_MAX_BUDGET_USD` — optional dollar cap. When set, the
  kit emits `--max-budget-usd <value>` in the argv (per ADR-0009's
  optional flag). When unset, the flag is omitted and Claude uses
  its own default. The kit passes the string verbatim **after a
  shape check**: the value must match `^[0-9]+(\.[0-9]+)?$`
  (digits optionally followed by a decimal point and more digits).
  Any other shape — control characters, embedded whitespace,
  negative sign, scientific notation — raises `WikiError` at exec
  start, before the subprocess spawns. Rationale: schedule
  artifacts (launchd plists, systemd units) template this string
  into a text file, so a value carrying `\n` or `\x00` could
  corrupt the artifact on disk; the regex keeps the value
  artifact-safe.

## Outputs

### Dispatch phase

Unchanged from [`task-17-wiki-run`](../task-17-wiki-run/spec.md). One
`OperationRunEvent` per surviving invocation. The dispatch event's
`by` is `"wiki-run"`; this spec does not change that. The downstream
exec events carry `by="wiki-run-exec"` so a `journal grep` can
attribute the exec to its delegate clearly.

**Implementation note (non-normative).** When `--exec` is set and
the Claude binary resolves, the CLI emits an observability line
to stderr — currently `wiki-run-exec: invoking <resolved-binary>`
— before invoking the orchestrator, so a user reviewing recent
runs can tell which binary `--claude-binary`/`WIKI_CLAUDE_BINARY`
resolved to. This is a security-review-driven implementation
detail, not a pinned contract: no CT exercises it, and the exact
text is free to evolve without a spec amendment. The line is
absent when the binary is unresolvable (the subsequent `WikiError`
carries the diagnostic in that case).

### Exec phase (only when `--exec` is set and dispatch succeeded)

- **Conflict-refusal path.** Before invoking `claude`, the kit walks
  the scoped vault tree (see §"Conflict-refusal walk scope" below) for
  `.proposed` sidecars. If any exist:
  1. Append `OperationExecFailedEvent(exit_code=-1,
     reason="conflict-refused", stderr_tail="",
     conflict_sidecars=[<vault-relative path>, …], log_path=None)`.
     The `stderr_tail` field is empty because no subprocess ran;
     the sidecar list lives in its own dedicated field.
  2. Write the per-failure file via the same `safe_write` first-write
     path described in §"Per-failure file format" below. The dispatch
     event id is single-use, so the file is new and `safe_write`'s
     drift-detection has nothing to compare against; the write
     produces a `PageWriteEvent` (and, in the construction-time-
     impossible re-write case, a `PageProposalEvent`).
  3. Print the refusal line to stderr; exit non-zero.
  - No `claude` subprocess is spawned.

- **Happy path.** With no `.proposed` sidecars in scope:
  1. Resolve the Claude binary (per §Inputs resolution order).
  2. Resolve the SKILL path (per §Inputs).
  3. Create `.wiki.journal/exec-logs/` if absent (additive write; the
     directory is gitignored — see `wiki-schedule/spec.md` §Constraints
     for the parallel `.wiki.journal/` precedent). Rotate logs whose
     mtime is more than `WIKI_EXEC_LOG_RETENTION_DAYS` days old
     (default 30) — best-effort delete; per-file `OSError`s
     swallowed silently. See §"Log rotation" below.
  4. Build the argv via `_build_argv(claude_binary, skill_path,
     vault_root, dispatch_event_id, parsed_args)`. The argv shape
     is **pinned by [ADR-0009 §Decision](../../adr/0009-headless-claude-invocation-contract.md)**:
     ```
     <claude_binary> -p
       --add-dir <vault_root>
       --permission-mode dontAsk
       --output-format json
       [--max-budget-usd <cap>]   # optional, only when WIKI_EXEC_MAX_BUDGET_USD is set
       <prompt>                    # trailing positional
     ```
     `<prompt>` is built from a fixed template that names the
     operation, points Claude at the SKILL, and inlines
     `dispatch_event_id` as a substring (per ADR-0009; the
     template body is **not** part of this spec's contract — it
     can evolve as SKILL conventions evolve). The SKILL reads
     `parsed_args` from the journaled `OperationRunEvent.args`
     by `event_id`, **not** from the prompt body — the kit does
     not pin a prompt-side rendering of args. The `[--agent
     <name>]` insertion ADR-0010 documents is **out of scope for
     v1** — that resolution chain comes from RFC-0004, which has
     not landed; v1's `_build_argv` never emits `--agent`. Spawn
     via `subprocess.run(argv, …)`.
     stdout/stderr both redirected to
     `.wiki.journal/exec-logs/<event_id>.log` (truncate-mode,
     UTF-8 — one log per dispatch; new run, new file). Timeout:
     configurable via `WIKI_EXEC_TIMEOUT` env var (default 1800
     seconds = 30 min). On timeout, the subprocess is terminated
     (SIGTERM, then SIGKILL after a 5-second grace) and the timeout
     is recorded as an exec failure (`exit_code=-2`,
     `reason="timeout"`).
  5. On `returncode == 0`:
     - Print one stdout line:
       `Exec succeeded for <op> (exit 0, <duration>s, log: <path>).`
     - **No second journal event** — the dispatch event already
       records the run. A future RFC may add an
       `OperationExecSucceededEvent` if observability needs it;
       deferred per spec §"Non-goals".
     - Exit `0`.
  6. On `returncode != 0`:
     - Append one `OperationExecFailedEvent`:
       ```
       type: "operation.exec_failed"
       timestamp: <UTC now>
       by: "wiki-run-exec"
       operation: "<operation>"
       dispatch_event_id: "<the dispatch event's id>"
       exit_code: <int>
       reason: "non-zero-exit" | "timeout" | "conflict-refused"
       stderr_tail: "<last 4 KB of stderr, UTF-8 lossy>"
       log_path: ".wiki.journal/exec-logs/<event_id>.log" (relative)
       ```
     - Write the per-failure file (see §"Per-failure file format"
       below).
     - Print the failure line to stderr; exit non-zero.

### Conflict-refusal walk scope

The walk for `**/*.proposed` sidecars is bounded to the directories
the kit considers vault content:

- **Included:** every direct child of `vault_root` whose name does
  not start with `.` (e.g. `wiki/`, `inbox/`, `outputs/`,
  `attachments/`). This single rule already excludes dot-prefixed
  directories like `.wiki.journal/`, `.git/`, `.obsidian/`, and
  `.claude/`; no extra dot-prefix list is needed.
- **Explicit nested exclusion:** `inbox/scheduled-failures/` is
  pruned during the walk (it lives under the included `inbox/`).
  This is the only non-redundant entry — the kit's own scratch
  must not trigger refusal.
- **Excluded if present:** any directory or file matched by
  `.obsidianignore` at the vault root, using **exact-prefix
  matching against vault-relative paths, no negation** (same
  subset of `.obsidianignore` semantics the kit already uses
  elsewhere — Obsidian's published grammar). `.gitignore` is
  **not** honored — vault content under `.gitignore` (e.g.
  `attachments/`) can still carry conflicts the user needs to
  resolve before a scheduled run mutates them.

The walk is **deterministic in sorted lexicographic order** —
each top-level subtree is enumerated via `Path.rglob("*.proposed")`
sorted by path, so two runs over the same on-disk state report the
same paths in the same order. (Earlier drafts said "breadth-first";
the implementation's depth-first-with-sorted-ordering produces a
deterministic, replayable result, which is what the contract needs
— the BFS-vs-DFS distinction was unnecessary precision.) It does
**not** short-circuit — it collects up to 20 sidecar paths so the
event and per-failure file can list them, then stops adding to
`paths` while continuing to count `total`. The event's
`conflict_sidecars` field carries the collected paths verbatim
(vault-relative POSIX form). The per-failure file renders them as
a bullet list. If more than 20 sidecars exist, the kit notes
`(…N more)` after the 20th in both the per-failure file and any
user-visible prose, where `N == total - 20`. No 4 KB byte cap —
the 20-path count is the single bound.

Rationale for the scope: prevents the deadlock loop where a sidecar
created by an earlier refusal's failure-file write triggers the next
refusal. Per-failure files live under
`inbox/scheduled-failures/` (excluded above); rotated logs live under
`.wiki.journal/exec-logs/` (excluded above). A user-edited
`inbox/scheduled-failures/<event_id>.md` that produced a `.proposed`
sidecar remains user-visible via `wiki doctor` but does not block
exec.

### Per-failure file format

Each `OperationExecFailedEvent` is paired with one new markdown file
at `inbox/scheduled-failures/<event_id>.md`. The file is
created via the in-vault `safe_write` path. Per-`event_id` file
names mean no two failures collide (each dispatch event id is
single-use). If the file already exists on disk — only possible if a
prior failure's file was preserved across a manual replay —
`safe_write` will treat the second write as a normal update and
produce a `.proposed` sidecar on hash drift, which the walk-scope
excludes from triggering further refusals.

The body is rendered from one of two templates, picked by `reason`:

- **`reason in {"non-zero-exit", "timeout"}`** (subprocess spawned):

  ```markdown
  # Scheduled exec failure

  - **Operation:** weekly-digest
  - **Dispatched:** 2026-05-21T09:00:00Z (event 01J0…)
  - **Failed:** 2026-05-21T09:29:58Z (event 01J0…)
  - **Reason:** timeout (exit -2, duration 1798s)
  - **Log:** [`.wiki.journal/exec-logs/01J0….log`](../../.wiki.journal/exec-logs/01J0….log)
  - **Last non-empty stderr line:** `claude: rate limit exceeded; retry after 60s`

  Resolve by reading the log, fixing the underlying cause, and either
  deleting this file or running the operation manually (`wiki run
  weekly-digest`). The next scheduled run fires normally regardless
  of whether this file is removed.
  ```

- **`reason == "conflict-refused"`** (subprocess never spawned):

  ```markdown
  # Scheduled exec refused: unresolved conflicts

  - **Operation:** weekly-digest
  - **Dispatched:** 2026-05-21T09:00:00Z (event 01J0…)
  - **Refused:** 2026-05-21T09:00:01Z (event 01J0…)
  - **Reason:** conflict-refused — `.proposed` sidecars present in scope.
  - **Sidecars found:**
    - `wiki/notes/foo.md.proposed`
    - `wiki/food/recipes/bar.md.proposed`

  Resolve each sidecar via the `wiki-conflict` SKILL (or delete
  manually), then delete this file. The next scheduled run will
  proceed.
  ```

  Conflict-refused failures have `log_path == None`, empty
  `stderr_tail`, and no duration — the bullet list of sidecars
  comes from the journaled `conflict_sidecars` field (same paths
  the event recorded).

"Last non-empty stderr line" is computed by splitting `stderr_tail`
on `\n`, dropping empty trailing strings, and taking the last
remaining element (or the empty string if none). User-resolution is
"delete the file"; the kit does not read these files back. A `wiki
doctor` count of `inbox/scheduled-failures/*.md` files surfaces the
backlog (orthogonal to the journal-event count in
[`wiki-schedule`](../wiki-schedule/spec.md) §"Doctor integration").

### Log rotation

On every `--exec` invocation, before spawning Claude, the kit walks
`.wiki.journal/exec-logs/` and deletes any `*.log` file whose
`stat().st_mtime` is more than `WIKI_EXEC_LOG_RETENTION_DAYS` days
old (default 30). Per-file `OSError`s (permission denied, file
vanished mid-walk) are **swallowed silently** — rotation is
cache-housekeeping, not a state change, so a failed delete must
not produce stderr noise that operators would learn to ignore.
Rotation runs at most once per `--exec` invocation. No journal
events are emitted for log deletions.

## Behavior

### `--exec` happy path

1. Dispatch phase runs to completion per
   [`task-17-wiki-run`](../task-17-wiki-run/spec.md). Suppose the
   dispatch is `OperationRunEvent(status="dispatched", event_id=E1)`.
2. The kit walks `vault_root` for `**/*.proposed`. If any: §"Exec
   phase / Conflict-refusal path" above.
3. The kit resolves the Claude binary. If not found: print the
   install-pointer warning, exit non-zero. No exec event journaled
   (no exec attempt was made).
4. The kit resolves the SKILL path. If the default doesn't exist
   and no `--skill-path` was supplied: raise `WikiError`. No exec
   event journaled.
5. The kit ensures `.wiki.journal/exec-logs/` exists (mkdir only).
   **v1 does not write `.gitignore`.** Operators who want the
   directory excluded from git add the entry manually; the spec
   defers the additive-write helper to a follow-up because
   ``.wiki.journal/`` is itself a kit-owned tree (the journal lives
   inside it) and users typically already ignore the whole path.
6. `subprocess.run` with the args above. The dispatch event's id is
   inlined into the prompt text the kit constructs (per
   [ADR-0009 §Decision](../../adr/0009-headless-claude-invocation-contract.md);
   asserted by CT-13), so the SKILL can chain its work to the same
   event by reading the id from the prompt.
7. On success: stdout line, exit 0. **No second event.**
8. On failure (non-zero exit / timeout): journal
   `OperationExecFailedEvent`, append failure-page bullet, exit
   non-zero.

### Edge cases

- **`--exec` without an operation contract `skill:` field set.** The
  dispatch SKILL pointer already defaults to `<operation>` per
  task-17 spec CT-13. The exec phase uses the same fallback for the
  `--skill <path>` argument: `<vault_root>/.claude/skills/<op>/SKILL.md`.
- **`--exec` against a SKILL that doesn't exist on disk.** Refuse
  with `WikiError` *before* spawning Claude. Dispatch event is
  already journaled. No exec event.
- **`--exec` combined with `--help` (any form).** The existing
  `--help` short-circuit (task-17 CT-14) wins — print help, exit 0,
  no dispatch, no exec. `--exec` is consumed without effect.
- **`--exec` combined with `invalid_args` dispatch.** The dispatch
  phase journals `status="invalid_args"` and returns exit
  `WIKI_ERROR_EXIT` per task-17 spec. The exec phase **does not
  run** — invalid args mean the SKILL has nothing to act on. No
  exec event.
- **`.proposed` sidecar under `.wiki.journal/`** (vault-internal
  scratch). Excluded from the walk by design — see §"Conflict-refusal
  walk scope". Same for `.git/`, `.obsidian/`, `.claude/`, and
  `inbox/scheduled-failures/`. False positives there are the kit's
  own scratch, not user content.
- **Subprocess timeout fires after Claude has already journaled
  partial work.** Tolerated. The exec failure event records the
  timeout; any `PageWriteEvent`s Claude emitted before SIGTERM
  remain in the journal (the journal is append-only;
  [ADR-0004](../../adr/0004-drift-detection-and-proposal-flow.md)
  drift detection handles half-written pages on next run). The
  failure page bullet notes "partial work may exist; review log".
- **Concurrent `--exec` for two operations against the same
  vault.** Both attempts hold the journal lock for their dispatch
  appends but **not** for the duration of the subprocess. This is
  intentional: holding the lock across a 30-min Claude run would
  starve every other journal writer. Two scheduled execs that
  overlap will race on any pages they both touch; ADR-0004 drift
  detection is the safety net. Documented under spec §"Non-goals"
  for explicit recognition.
- **Vault `.gitignore` doesn't include `.wiki.journal/exec-logs/`.**
  v1 does not write `.gitignore` — the spec was originally going to
  reuse `_ensure_obsidianignore`'s pattern, but `.wiki.journal/` is
  already kit-owned scratch that users typically ignore wholesale
  (the journal itself lives there). Deferred to a follow-up if a
  user-visible drift surface emerges.

### Error cases

- Claude binary not found → `WikiError`; dispatch event remains;
  no exec event. Exit non-zero.
- SKILL file not found → `WikiError`; dispatch event remains;
  no exec event. Exit non-zero.
- Subprocess failures (non-zero exit, timeout) → journaled as
  `OperationExecFailedEvent`. Exit non-zero.
- `safe_write` drift on a per-failure file
  (`inbox/scheduled-failures/<event_id>.md`) is impossible by construction
  — each file is named after a single-use dispatch event id and
  written exactly once. If the user manually re-creates a file with
  the same name (rare), `safe_write` produces a `.proposed` sidecar
  that the walk-scope excludes from triggering further refusals.
- **Per-failure file write failures** (disk full, permission denied,
  vault directory removed mid-run, `safe_write` raising for any
  reason) are best-effort: the kit emits a one-line stderr warning
  (`wiki-run-exec: per-failure file write failed: <repr>; failure
  event will still be journaled.`) and proceeds to append the
  `OperationExecFailedEvent` regardless. Rationale: the journal is
  the authoritative record of the exec attempt; the per-failure
  file is a user-facing breadcrumb. Losing the breadcrumb must not
  also lose the journal record.

## Invariants

- One `--exec` invocation appends **at most two `operation.*`
  events** — zero or one `OperationRunEvent` (per task-17's
  invariants) and zero or one `OperationExecFailedEvent` — **plus**
  whatever `PageWriteEvent` / `PageProposalEvent` the per-failure
  file write through `safe_write` produces (typically one
  `PageWriteEvent` per failure; one `PageProposalEvent` in the
  construction-time-impossible re-write case). The
  `OperationExecFailedEvent` is appended iff a `claude` subprocess
  was actually spawned and exited non-zero, or the conflict-refusal
  path fired.
- The dispatch event is always journaled before the exec
  subprocess spawns. The exec failure event is always journaled
  after the subprocess exits (or after the conflict-refusal check
  fires). Events appear in chronological order in the journal.
- `OperationExecFailedEvent.dispatch_event_id` always references an
  immediately-prior `OperationRunEvent` in the same invocation's
  journal slice. Cross-invocation references are not permitted.
- The exec subprocess is invoked with `cwd=vault_root` and the
  parent process's environment **unchanged** at v1. Per-platform
  env scrubbing (`PATH`, `HOME`, `LC_*`, `XDG_*`, `APPDATA`,
  Claude-specific vars, etc.) is deferred to a future ADR — ADR-0009
  pinned the argv shape but explicitly did not take a position on
  env scrubbing, and getting the right allow-list cross-platform
  requires knowing which env vars Claude actually reads, which the
  kit doesn't own. v1 documents the pass-through; a future ADR
  upgrades to scrubbing if warranted.
- No filesystem writes outside the journal append, the exec log,
  and the failure page. No vault page writes from this module —
  page writes come from `claude` itself, via the kit's
  `safe_write` invoked by SKILL code.
- `--exec`-less behavior is **byte-identical** to the existing
  task-17 contract. The CT-N items from
  [`task-17-wiki-run/spec.md`](../task-17-wiki-run/spec.md)
  continue to pass unchanged.

## Contracts with other modules

- **`cli.py:_cmd_run`** — gains two new argparse flags (`--exec`,
  `--claude-binary`, `--skill-path`). The REMAINDER consumption of
  op-args is unchanged; these flags sit *before* the operation
  name. Example: `wiki run --exec --claude-binary /opt/claude
  weekly-digest --window=2026-W20`.
- **`llm_wiki_kit/run.py`** — `dispatch()` signature is unchanged.
  A new top-level orchestrator `dispatch_and_exec()` is added:
  ```python
  def dispatch_and_exec(
      operation: str,
      raw_args: list[str],
      *,
      vault_root: Path,
      kit_root: Path,
      journal_path: Path,
      now: datetime,
      claude_binary: Path | None = None,
      skill_path_override: Path | None = None,
      timeout_seconds: int = 1800,
      log_retention_days: int = 30,
      max_budget_usd: str | None = None,
      failure_clock: Callable[[], datetime] | None = None,
  ) -> ExecResult
  ```
  Internally: calls `dispatch()`, then if `DispatchResult.status
  == "dispatched"`, runs the exec sequence. `failure_clock` is the
  failure-render clock seam — defaults to `_utc_now`; tests inject
  a fixed callable so the per-failure file's `Failed:` timestamp
  and the rendered duration are deterministic.
  `ExecResult` wraps the `DispatchResult` and adds
  `exec_status: Literal["skipped", "succeeded", "failed_conflict",
  "failed_exit", "failed_timeout"]` plus `exit_code: int | None`,
  `duration_seconds: float | None`, and `log_path: str | None`
  fields populated on the success / exit / timeout paths (the CLI
  reads them to render the success/failure summary line). The
  binary-missing and SKILL-missing branches raise `WikiError`
  instead of returning, so they have no `exec_status` variant.
  Inner helpers (`_locate_claude`, `_locate_skill`,
  `_walk_proposed_sidecars`, `_read_obsidianignore`,
  `_validate_max_budget`, `_build_prompt`, `_build_argv`,
  `_rotate_logs`, `_run_subprocess`, `_render_failure_file`,
  `_write_failure_file`, `_write_failure_file_safe`,
  `_append_failure_event`) are pure-ish and tested directly under
  `tests/unit/test_run_exec.py`. If `dispatch()` raises `WikiError`
  (pre-load failures: not-a-vault, unknown operation, kind
  mismatch, missing contract), `dispatch_and_exec` re-raises
  unchanged — no `dispatch_event_id` exists, no exec attempt is
  made, no failure event journaled.

  **`DispatchResult` extension.** `dispatch()` already returns
  `DispatchResult`; this spec adds one new required field
  `dispatch_event_id: str` that carries the `event_id` the kit
  generated and journaled on the surviving `OperationRunEvent`.
  The field is set on every surviving invocation (both
  `dispatched` and `invalid_args` paths journal an event with a
  fresh `event_id`). **Field-order placement:** the new field
  must precede every existing defaulted field
  (`error: str | None = None`, `produced_pages: list[str] =
  field(default_factory=list)`) in the `@dataclass` definition so
  Python's "non-default argument follows default argument" rule
  holds. All existing call sites use keyword arguments, so
  inserting before defaulted fields is source-compatible.
  `__post_init__` is extended to assert the shape:
  `len(dispatch_event_id) == 12` and every character is in
  `0-9a-f`; any future bug constructing `DispatchResult` without
  setting it (or with a malformed id) raises `ValueError` at
  construction time (existing `__post_init__` already raises
  `ValueError` for the status/error correspondence; same
  exception type). Other string fields on `DispatchResult`
  (`operation`, `skill`, `args_raw`) are not shape-checked here
  because they don't appear in filenames or grep predicates;
  `dispatch_event_id` does, so a malformed value would silently
  corrupt `inbox/scheduled-failures/` and `.wiki.journal/exec-logs/`.
- **`llm_wiki_kit/models.py`** — additive per ADR-0002. One new
  class:
  ```python
  class OperationExecFailedEvent(_EventBase):
      type: Literal["operation.exec_failed"] = "operation.exec_failed"
      operation: str
      dispatch_event_id: str
      exit_code: int
      reason: Literal["non-zero-exit", "timeout", "conflict-refused",
                       "binary-missing", "skill-missing"]
      stderr_tail: str = ""
      log_path: str | None = None
      conflict_sidecars: list[str] = Field(default_factory=list)
  ```
  `conflict_sidecars` is empty for every reason except
  `conflict-refused`; older journal lines (none exist yet at v1)
  replay unchanged under ADR-0002's additive-schema rule.

  **Event identity.** `OperationRunEvent` gains one additive field
  `event_id: str | None = None`. The kit populates it via
  `uuid.uuid4().hex[:12]` whenever `dispatch()` creates a new
  event; older journal lines (no `event_id` key) replay with
  `event_id is None` per ADR-0002's additive-schema rule. The
  field is 12 lowercase hex characters; uniqueness is bounded by
  uuid4's 122 bits of entropy truncated to 48, so collision across
  any plausible volume (cross-process scheduled runs included) is
  ≈1-in-2^48 — strong enough that the kit does not enumerate
  collision recovery. The `DispatchResult.dispatch_event_id: str`
  field carries this id back to callers without re-reading the
  journal. `OperationExecFailedEvent.dispatch_event_id`
  references it verbatim. The per-failure file
  (`inbox/scheduled-failures/<event_id>.md`) and exec-log file
  (`.wiki.journal/exec-logs/<event_id>.log`) use it as the
  filename. The id is **not** derived from event content —
  Pydantic-version drift cannot orphan a journaled id, and two
  invocations with byte-identical events still get distinct ids.
  The kit-wide alternative (an `event_id` field on `_EventBase`
  spanning all event types) is deliberately deferred; only
  `OperationRunEvent` carries it today because it's the only
  event other events reference.
  The `reason="binary-missing"` and `reason="skill-missing"` values
  are reserved for future use — v1 spec'd above says these failures
  do **not** journal an exec event. The reserved variants exist so
  a future spec amendment can opt into journaling them without
  another model change. v1 enforces this at the emit site:
  `_append_failure_event` checks `reason not in {"binary-missing",
  "skill-missing"}` and raises `RuntimeError("v1: reason
  '<reason>' is reserved; no emit path should reach this branch")`
  if a future bug tries to emit one. `RuntimeError` (not `assert`)
  so the check survives `python -O`; not `WikiError` because
  reaching this branch is an internal-invariant violation, not a
  user-actionable error.
- **`llm_wiki_kit/journal.py`** — `append_event(journal_path,
  event)` is called once per dispatch and (optionally) once per
  failure. `append_event` returns `None`; the kit obtains the
  `event_id` by generating it at `OperationRunEvent` construction
  time (`uuid.uuid4().hex[:12]`) and remembering it on the
  `DispatchResult` — no journal re-read needed.
- **`llm_wiki_kit/write_helper.py`** — `safe_write` (the in-vault
  path) for each per-failure file under
  `inbox/scheduled-failures/<event_id>.md`. No managed
  regions, no new helper — each file is single-write by
  construction.
- **The vault-side SKILL** — receives the dispatch event id
  inlined into the prompt text (per ADR-0009 §Decision). The
  SKILL contract is unchanged; spec'd separately
  in the vault-side `wiki-schedule` SKILL.md (out of scope here).

## Acceptance criteria

The contract tests below define "done". Construction tests live in
plan files for the schedule + exec PRs.

- [x] **CT-1: `--exec` happy path.** Given an installed
  `weekly-digest`, a `<vault>/.claude/skills/weekly-digest/SKILL.md`,
  and a fake `claude` binary on `PATH` that exits 0, `wiki run
  --exec weekly-digest --window=2026-W20` (a) appends exactly one
  `OperationRunEvent(status="dispatched", event_id=<12-hex>)`, (b)
  spawns the binary with the exact argv pinned by ADR-0009 — see
  CT-13 — asserted via a script that echoes its argv to a fixture
  file, (c) writes `.wiki.journal/exec-logs/<event_id>.log` where
  `<event_id>` equals the journaled `OperationRunEvent.event_id`,
  (d) appends **no** exec event, (e) exits `0`.

- [x] **CT-1a: `OperationRunEvent.event_id` round-trip.** A `wiki
  run` invocation (with or without `--exec`) produces a
  `DispatchResult.dispatch_event_id` that is exactly 12 lowercase
  hex characters. Re-reading the last journaled
  `OperationRunEvent` line and parsing it via
  `_EVENT_ADAPTER.validate_json` yields `event.event_id ==
  result.dispatch_event_id`. Two consecutive `wiki run`
  invocations produce two distinct ids — the invariant pinned
  here is that distinct dispatches do not reuse ids; the 2^-48
  cross-process collision bound from §"Event identity" makes this
  effectively unconditional. A literal pre-extension journal line
  for `operation.run` with no `event_id` key parses to
  `event.event_id is None` (additive-schema replay).
- [x] **CT-2: `--exec` with `invalid_args` skips the exec phase.**
  `wiki run --exec weekly-digest --frobnicate=x` against a contract
  with no `frobnicate` field appends one
  `OperationRunEvent(status="invalid_args", event_id=<12-hex>)`
  whose `event_id == result.dispatch_event_id`, spawns no
  subprocess, journals no exec event, exits `WIKI_ERROR_EXIT`.
- [x] **CT-3: claude binary not found.** With no `claude` on PATH,
  no `--claude-binary`, and no `WIKI_CLAUDE_BINARY`, `wiki run
  --exec weekly-digest --window=2026-W20` appends the dispatch
  event, raises `WikiError` (caught at the CLI top-level and
  rendered as a one-line stderr message containing both `--exec`
  and `claude` substrings; no Python traceback unless
  `--verbose`), journals **no** exec event, exits
  `WIKI_ERROR_EXIT`.

- [x] **CT-3a: set-but-invalid override fails fast pre-dispatch.**
  With `--claude-binary <path>` (or `WIKI_CLAUDE_BINARY=<path>`)
  pointing at a file that exists but is not executable, the call
  raises `WikiError` at the CLI's pre-dispatch `_locate_claude`
  call (the same pre-dispatch helper whose *successful* return
  drives the observability print; on a set-but-invalid override
  the helper raises before that print is reached). The
  journal contains **zero** `operation.*` events for this
  invocation — neither `operation.run` nor `operation.exec_failed`
  — and the exit is `WIKI_ERROR_EXIT`. Differs from CT-3
  (no-binary-anywhere) where the dispatch event **is** journaled
  because the orchestrator's binary check runs after `dispatch()`
  returns. Pins the §Inputs "Set-but-invalid override exception"
  paragraph.

- [x] **CT-4: SKILL file missing.** With Claude present but no
  `<vault>/.claude/skills/weekly-digest/SKILL.md` and no
  `--skill-path`, the call appends the dispatch event, raises
  `WikiError` naming the resolved path, journals no exec event,
  exits non-zero.
- [x] **CT-5: `--skill-path` override.** With the SKILL stored at
  a non-default location, `--skill-path <path>` causes the kit to
  pass that path to `claude` (asserted via the argv-echo fixture).
- [x] **CT-6: conflict refusal.** With one `.proposed` sidecar in
  scope under `vault_root` (e.g. `wiki/notes/foo.md.proposed`),
  `wiki run --exec weekly-digest --window=2026-W20` (a) appends the
  dispatch event, (b) appends one
  `OperationExecFailedEvent(reason="conflict-refused", exit_code=-1)`,
  (c) writes `inbox/scheduled-failures/<event_id>.md` via
  `safe_write`, (d) spawns no subprocess, (e) exits non-zero.

- [x] **CT-6a: walk-scope excludes own scratch.** With a `.proposed`
  sidecar under any of `.wiki.journal/`, `.git/`, `.obsidian/`,
  `.claude/`, or `inbox/scheduled-failures/`, **and no sidecars
  elsewhere**, `wiki run --exec weekly-digest --window=2026-W20`
  proceeds to spawn the subprocess (no refusal event, no failure
  file).

- [x] **CT-6b: walk-scope honors `.obsidianignore`.** With a
  `.proposed` sidecar under a directory matched by
  `.obsidianignore`, the call proceeds to spawn the subprocess.
- [x] **CT-7: subprocess non-zero exit.** With Claude present and
  a stub binary that exits `137`, the call (a) appends the
  dispatch event (`event_id == result.dispatch_event_id`), (b)
  spawns the binary, (c) appends one
  `OperationExecFailedEvent(reason="non-zero-exit",
  exit_code=137)` whose `dispatch_event_id ==
  result.dispatch_event_id` (which equals the journaled
  `OperationRunEvent.event_id`), (d) writes
  `inbox/scheduled-failures/<event_id>.md` via `safe_write`, (e)
  writes the full log to `.wiki.journal/exec-logs/<event_id>.log`,
  (f) exits non-zero.
- [x] **CT-8: subprocess timeout.** With `WIKI_EXEC_TIMEOUT=1` and
  a stub binary that sleeps 10 seconds, the call terminates the
  subprocess and journals
  `OperationExecFailedEvent(reason="timeout", exit_code=-2)`. Exit
  non-zero.
- [x] **CT-9: byte-identity for non-`--exec` invocations.** The 16
  contract tests in
  [`task-17-wiki-run/spec.md`](../task-17-wiki-run/spec.md) (CT-1
  through CT-16) all pass unchanged after this spec's
  implementation.
- [x] **CT-10: additive schema replays.** A literal pre-extension
  journal line for `operation.run` with no `event_id` key replays
  under the extended Pydantic models as `event.event_id is None`
  (pinned by `tests/unit/test_run_dispatch.py::
  test_legacy_journal_line_without_event_id_replays_as_none`).
  The broader ADR-0002 invariants — `model_dump_json` round-trips
  without spurious keys, `VaultState`-identity across a
  pre-extension-vs-extended replay — follow from ADR-0002's
  additive-schema rule and are not separately pinned here; the v1
  ship deliberately did not add a `model_dump_json` round-trip
  test for `OperationRunEvent` at this layer.
- [x] **CT-11: per-failure file invariants.** Two failures from two
  distinct `--exec` invocations produce exactly two files under
  `inbox/scheduled-failures/`, each named
  `<event_id>.md` where `<event_id>` equals the corresponding
  invocation's `result.dispatch_event_id` (and the journaled
  `OperationRunEvent.event_id`). Every file's body contains the
  operation name, the reason, and the dispatch event id. **For
  `non-zero-exit` / `timeout` failures**, the body also contains
  the exit code, the relative log path, and a "Last non-empty
  stderr line:" field whose value matches the final non-empty line
  of the journaled `stderr_tail`. **For `conflict-refused`
  failures**, the body lists the offending sidecar paths from the
  journaled `conflict_sidecars` field, contains neither a log link
  nor a duration, and the journaled event has `stderr_tail == ""`
  and `conflict_sidecars != []`. A user who deletes one file does
  not affect the other.
- [x] **CT-12: stderr_tail is bounded.** A stub binary that emits
  100 KB of stderr produces a journaled `stderr_tail` of exactly
  the last 4 KB (or fewer if the binary emitted less). UTF-8
  decode errors fall back to lossy decode (no crash).
- [x] **CT-13a: `WIKI_EXEC_MAX_BUDGET_USD` shape check.** With
  `WIKI_EXEC_MAX_BUDGET_USD="5.00"`, the argv contains the pair
  `["--max-budget-usd", "5.00"]` immediately before the prompt
  positional. With `WIKI_EXEC_MAX_BUDGET_USD="not-a-number"`,
  `"5; rm -rf ~"`, or `"5\n"`, the kit raises `WikiError` at exec
  start; **no** subprocess is spawned, **no** exec event is
  journaled (the dispatch event is still journaled because the
  budget check runs after `dispatch()` returns — same shape as
  CT-3 binary-missing). With the env var unset, the argv does
  not contain `--max-budget-usd`.

- [x] **CT-13: argv structure (pinned by ADR-0009).**
  `_build_argv(claude_binary, skill_path, vault_root,
  dispatch_event_id, parsed_args)` returns a `list[str]` whose
  contents exactly match
  [ADR-0009 §Decision](../../adr/0009-headless-claude-invocation-contract.md):
  `[<claude_binary>, "-p", "--add-dir", <vault_root>,
  "--permission-mode", "dontAsk", "--output-format", "json",
  <prompt>]`. The `--max-budget-usd <cap>` pair is appended
  between `"json"` and `<prompt>` iff a `WIKI_EXEC_MAX_BUDGET_USD`
  env var is set (string-passed verbatim). `--agent <name>` is
  **never** emitted by v1's `_build_argv` (ADR-0010's resolution
  chain depends on RFC-0004, which hasn't landed). `<prompt>` is
  the kit's template-rendered string containing the operation
  name, the SKILL path, and the `dispatch_event_id` substring.
  `parsed_args` is **not** rendered into the prompt body — the
  SKILL reads them from the journaled `OperationRunEvent.args`
  by `event_id` instead (§Happy path step 4). The exact prompt
  body is
  **not** part of this CT (ADR-0009 §"What this ADR does not
  cover"); CT-13 asserts only the argv-shape contract, with the
  prompt body checked for the presence of `dispatch_event_id` as
  a substring.

- [x] **CT-14: no `OperationExecSucceededEvent`.** After a
  successful `--exec`, the journal slice for the invocation
  contains exactly one event with `type=="operation.run"` and
  zero with `type=="operation.exec_succeeded"`. Pins spec
  §"Non-goals".

- [x] **CT-15: log rotation.** With a 31-day-old
  `.wiki.journal/exec-logs/old.log` and a fresh `--exec`
  invocation, the old file is deleted before the subprocess
  spawns. A 29-day-old file is preserved.

- [x] **CT-16: SKILL-name fallback at the exec layer.** Given a
  contract with no `skill:` field set and no `--skill-path`
  override, the kit resolves the SKILL path to
  `<vault_root>/.claude/skills/<operation>/SKILL.md` and passes
  that path to `claude` (asserted via the argv-echo fixture from
  CT-5). Matches [task-17 CT-13](../task-17-wiki-run/spec.md)
  fallback behavior.

- [x] **CT-17: failure-file write does not loop refusal.** A
  refusal that writes
  `inbox/scheduled-failures/<event_id>.md` (which is itself a new file
  under `inbox/scheduled-failures/`) does **not** cause the next
  `--exec` invocation to refuse — `inbox/scheduled-failures/` is
  in the unconditional walk-scope exclusion list.

## Non-goals

- **Agent passthrough via `--agent <name>`.** ADR-0010 documents
  the optional flag insertion; the resolution chain (schedule-
  entry agent → recipe-declared mapping → operation's
  `preferred_agent`) lives in RFC-0004, which has not landed.
  v1's `_build_argv` never emits `--agent`. The v2 spec
  amendment that picks this up will additively extend
  `OperationExecFailedEvent` with `agent: str | None = None`
  (so failures can record which agent was active); the field is
  deliberately omitted at v1 to keep the model surface
  minimal.

- **An `OperationExecSucceededEvent`.** Symmetry with the failure
  event is tempting, but the dispatch event already records the
  attempt and a future `journal explain` can correlate it with
  downstream `PageWriteEvent`s emitted by the SKILL. Adding a
  success event doubles journal volume for the common case
  without new information. Future RFC if observability needs
  surface.
- **SDK-based execution.** Out of scope per RFC-0003 §"Decisions
  already made". Adding `anthropic` as a runtime dep would need
  its own ADR.
- **Cost / token tracking.** The kit does not record API spend.
  Users who want that hook can wrap `wiki run --exec` in a script
  that reads the exec log.
- **Streaming exec output to the operator's terminal.** v1 sends
  all output to the log file. A future `--tail` or `--no-log`
  flag could change this; deferred.
- **Holding the journal lock for the duration of the subprocess.**
  Documented under spec §"Edge cases" — overlapping execs are
  expected to be rare; ADR-0004 drift detection is the safety
  net.
- **Recovering partial work from a timed-out exec.** Whatever
  Claude already journaled and wrote stays in the journal /
  drift-detection flow. The kit does not roll back partial work.
- **Auto-retrying failed execs.** A scheduled run that failed
  fails. The user (or a future scheduling-retry RFC) decides
  when to re-attempt.
- **A `wiki run --dry-run` flag.** The dispatch boundary is
  already a no-side-effect read of the contract; a dry-run mode
  would only differ from the current behavior by suppressing the
  journal append, which is an explicit non-goal of the dispatch
  contract (task-17 §Non-goals).
- **Vault-side SKILL contract changes.** This spec passes the
  dispatch event id through to Claude in the prompt; the SKILL
  retrieves parsed args from the journaled
  `OperationRunEvent.args` by `event_id`. The SKILL contract for
  *what to do with them* is described in the vault-side
  `wiki-schedule` SKILL.md (out of scope here).

## Constraints

- No new runtime dependency. `subprocess`, `shutil.which`,
  `pathlib`, `os.environ` are all stdlib.
- No new top-level repo directory. All code changes land in
  `llm_wiki_kit/run.py`, `llm_wiki_kit/cli.py`, and
  `llm_wiki_kit/models.py`.
- No bypass of `journal.append_event` for the new event type.
- No bypass of `write_helper.safe_write` for the failure-page
  append.
- No new public CLI verb. `wiki run` gains three additive flags
  (`--exec`, `--claude-binary`, `--skill-path`) and no new
  subcommand.
- No daemon process. The exec subprocess is one-shot per
  invocation; the kit waits for it and exits.
- No retro-edit of existing journal events. The model changes are
  additive: one new event class (`OperationExecFailedEvent`) and
  one new optional field on `OperationRunEvent`
  (`event_id: str | None = None`, defaulted to None so older
  journal lines replay unchanged per ADR-0002). The new exec
  event references the dispatch event's `event_id`, not the other
  way around — the dispatch event has no reference to the failure
  event.
- No new ADR at v1 — the load-bearing decisions trace back to
  [RFC-0003](../../rfc/0003-scheduling-and-autonomous-execution.md),
  [ADR-0009](../../adr/0009-headless-claude-invocation-contract.md)
  (the argv shape this spec emits), and
  [ADR-0010](../../adr/0010-agent-passthrough-via-claude-agent-flag.md)
  (agent passthrough; deferred to v2 of this spec when RFC-0004
  lands). The v1 implementation creates no new ADR. A v2 amendment
  picking up agent passthrough — or any future env-scrubbing work
  — may require its own ADR; see [`plan.md`](plan.md).
