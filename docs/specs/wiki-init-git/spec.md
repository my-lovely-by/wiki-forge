# Spec: wiki init --no-git (default git-init on new vaults)

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit.cli._cmd_init` (orchestration) + `llm_wiki_kit.git_init` (new module)
- **Related:** RFC-0001 §"CLI surface (target)", PR #65 (README onboarding restructure), `docs/specs/wiki-init-git/plan.md`, `docs/specs/wiki-init-adopt/spec.md` (composition).
- **Constrained by:** ADR-0002 (journal as state truth), ADR-0008 (init-adopt ownership policy), `docs/specs/safe-write-ordering/spec.md` (the documented-exceptions list — this spec adds no new entry).

## What this is

A behavior change to `wiki init`: when the user does not pass `--no-git`, the kit initializes a git repository in the new vault and makes one initial commit covering the freshly-rendered tree. The kit's existing `.gitignore` continues to land through the normal render path — the spec does not modify its content. The new flag `--no-git` opts out of the git operations only; vault content is unchanged.

The thing this spec *is*: a tight bolt-on at the tail of `_cmd_init` that runs `git init` + `git commit` via subprocess and journals one new event. The thing this spec *is not*: a generic git wrapper, a remote-management feature, or a re-architecture of how `.git/`-style metadata directories interact with `safe_write`.

## Inputs

- **CLI args** — existing `path: str` and `--recipe: str`; new optional `--no-git: bool` (defaults to `False`, i.e. git initialization is the default).
- **Environment** — presence of a `git` binary on `$PATH`, queried via `shutil.which("git")`. Only consulted when `--no-git` is not in effect.
- **Filesystem** — target directory state (the existing empty-target refusal still applies). For the `--adopt` composition path (out of scope to implement here; out of scope to test until adopt lands), whether `.git/` already exists.
- **Git config (indirect)** — `user.name` and `user.email`, used by `git commit`. The kit does not read them directly; if they are unset, the commit subprocess fails and the kit surfaces that failure.

## Outputs

In addition to today's `VaultInitEvent`, `PrimitiveInstallEvent`(s), `PageWriteEvent`(s), and `ManagedRegionWriteEvent`(s):

- **Filesystem** — a `.git/` directory containing a fresh git repository with exactly one commit, when `--no-git` was not passed and no pre-existing `.git/` was present. The repository uses whatever default branch and config the user's `git` produces; no kit-specific overrides.
- **Journal** — one new event type:

  ```
  VaultGitInitializedEvent
    type: "vault.git_initialized"
    by: "wiki-init"
    timestamp: datetime
    schema_version: int = 1
  ```

  Deliberately carries no `commit_sha` or `branch` field. The event is appended **before** `git add -A`, so its line is part of the tree the initial commit captures — i.e. `git status --porcelain` is empty immediately after `wiki init`. Recording the commit SHA on the event would either (a) require committing twice (violating the "one initial commit" non-goal) or (b) leave the journal one event ahead of HEAD until the user's next commit (violating the "porcelain clean" acceptance criterion). The SHA is trivially recoverable from `git log --grep "Initialize wiki vault from"` when needed; this spec optimizes for the workflow contract over the audit-trail convenience.

  Emitted only after the journal-cache scope reaches the git phase and the subsequent commit succeeds. Absent if `--no-git` was passed, if `.git/` pre-existed, or if `git init`/`git commit` failed (see Edge cases for the partial-state matrix).

## Behavior

### Happy path — empty target, default flags, git present

Step ordering is load-bearing: pre-flights run first (cheap, no side effects, fail-fast); journal-and-render second (the existing flow); git operations last (so a mid-install crash cannot leave a partial `.git/` referencing non-existent files). Pre-flight order is also load-bearing: the empty-target refusal runs **before** the `git` pre-flight, so a user who passes a non-empty path without `--adopt` sees the existing "not empty" error rather than a misleading "git missing" error when both conditions fail.

1. Resolve target path; refuse if it is a file or non-empty (existing behavior, unchanged).
2. Pre-flight `git` availability: `shutil.which("git")` must return a path; otherwise refuse with the error in [Error cases](#error-cases). This check happens **after** the empty-target refusal (above) but **before** any directory creation or journal append, so a refusal leaves the filesystem untouched.
3. Open the journal cache scope (existing behavior).
4. Append `VaultInitEvent` (existing behavior).
5. Run `install_primitives` (existing behavior). The kit's `core/files/.gitignore` lands via the normal render path through `safe_write` and is on disk before step 6, so the initial commit excludes patterns like `*.proposed`. (See AC and Contract tests for the invariant.)
6. Inside the still-open journal-cache scope, call `initialize_git(target, recipe_name=recipe.name, journal_path=journal_path, now=now)`. That function:
   - Runs `git init` in the target via `subprocess.run`, argv-list form, `shell=False`.
   - Recomputes `now_git = datetime.now(UTC)` (long `git init` on slow disks must not journal a stale timestamp; precedent: safe-write-ordering spec's adopt fast-path recomputes `now` for the same reason). Appends `VaultGitInitializedEvent(timestamp=now_git, by="wiki-init")` via `append_event(journal_path, ...)`. The event lands **before** staging so the journal line it adds is captured by the initial commit's tree.
   - Runs `git add -A` (on the empty-target happy path; the `--adopt` variant uses a narrowed path set per its dedicated section below), then `git -c commit.gpgsign=false commit -m "Initialize wiki vault from <recipe> recipe"`. Argv-list form, `shell=False`; the commit message is a single argv element so quoting is git's problem, not the kit's. GPG signing is explicitly disabled on this one commit so a user with a misconfigured signing key still gets a working vault; subsequent user-made commits honor their normal config. Recipe names are constrained to `[a-z][a-z0-9-]*` by Pydantic validation in `llm_wiki_kit/models.py:Recipe.name` (load-bearing for this call site — see the inline marker comment that the implementation PR adds).
7. Close the cache scope; return 0.

The append-before-stage ordering is the only ordering that satisfies both *single initial commit* and *clean `git status --porcelain` immediately after `wiki init`*. It does mean a crash between the journal append (step 6's second sub-bullet) and the commit (third sub-bullet) leaves a journal line claiming git initialization succeeded while no commit landed. This is documented in [Edge cases](#edge-cases) and is recoverable via the manual `git add -A && git commit` path; the kit does not automatically detect or reconcile this state.

### Variant — `--no-git`

Skip steps 2 and 6 entirely. Do not check for a `git` binary. Do not journal `VaultGitInitializedEvent`. The kit's `.gitignore` still ships via render (step 5).

### Variant — `--adopt` with non-empty target, no `.git/`

Reachable only once `--adopt` is implemented. The adopt branch journals `PageAdoptedEvent` / `ManagedRegionAdoptedEvent` baselines for pre-existing kit-owned files before `install_primitives` renders. After install completes, the kit reaches step 6 with a target that contains both user-territory files and kit-rendered files but no `.git/`:

- The kit runs `git init` followed by a *narrowed* `git add` that stages only kit-owned paths — the recipe's rendered closure plus `.gitignore` plus the journal — and then `git -c commit.gpgsign=false commit -m "Initialize wiki vault from <recipe> recipe"`.
- User-territory files are deliberately **not** staged. The adopt spec's invariant 3 ("Files outside the recipe's rendered closure are left strictly alone, no event, no touch") forbids the kit from creating a journaled, kit-attributable artifact whose content depends on bytes the kit was forbidden to journal. Staging user files into the initial commit would do exactly that. The user runs `git add . && git commit -m "..."` themselves to track their own content; the kit's initial commit is a pure "kit shape" baseline.
- The commit message is identical to the empty-target happy path; the spec does not distinguish "initialized from scratch" vs "initialized from adopted folder" at the commit-message level. Users who care about that distinction can grep the journal for `PageAdoptedEvent`s alongside the `VaultGitInitializedEvent`.
- `VaultGitInitializedEvent` is emitted normally.

The path set staged by `git add` in this variant is principle-driven, not a literal enumeration in the spec — the adopt implementation owns the exact closure walk. The constraints are:

- **In:** every kit-owned path from this `wiki init` run. Concretely: `PageWriteEvent.path` and `PageAdoptedEvent.path` from this run; the `file` attribute of `ManagedRegionWriteEvent` / `ManagedRegionAdoptedEvent` *only when that host file is also a `PageWriteEvent`-or-`PageAdoptedEvent` target from this run* (the kit-owned-host invariant; today's flow satisfies it trivially, but the constraint is pinned so a future feature that writes a managed region into a user-territory file doesn't accidentally drag that file into the kit's initial commit). Plus the kit-managed non-journaled files: `.wiki.journal/journal.jsonl`, and `.obsidianignore` if it exists per the documented `write_helper._ensure_obsidianignore` exception.
- **Out:** every user-territory path. Adopt's invariant 3 forbids touching files outside the recipe's closure; this constraint forbids referencing them in a kit-attributable commit. The user runs their own `git add . && git commit -m "..."` to track their content.
- **Out:** `.proposed` sidecars. Their paths are recorded by `PageProposalEvent.proposed_path` but `.proposed` files are transient artifacts for user-mediated merge, never history. The kit's invariant is to never commit a sidecar.

Implementation note: this enumeration runs after `install_primitives` finishes but before the journal-event append (per the new step ordering); the closure is fully determined by the journal at that point.

### Variant — target already contains `.git/`

Reachable only via `--adopt` on a folder where the user already had a git repo or worktree. The short-circuit fires for both shapes of `target/.git`: the directory shape (a normal repo) AND the gitfile shape (a regular file containing `gitdir: <path>`, used by worktrees and submodules). When the kit reaches step 6 and `target/.git` exists in either shape:

- Do **not** modify `.git/` state in any kit-attributable way.
- Do **not** make an initial commit.
- Do **not** emit `VaultGitInitializedEvent`.

The kit treats `.git/` as user territory; once it exists, the observable contract is "no kit-attributable commit lands and no `VaultGitInitializedEvent` is journaled." Whether the implementation also skips a redundant `git init` call (which is a safe no-op on an existing repo) is an internal choice; the spec does not pin it because it is not user-observable. This behavior is independent of whether `--no-git` was passed. The user can `git add -A && git commit -m "Adopt vault"` themselves; the kit will not preempt their git workflow.

### Edge case — `git init` returns non-zero

- Surface the subprocess `stderr` through a `WikiError`.
- Do not emit `VaultGitInitializedEvent` (the event hasn't yet been appended at this point — see the step ordering above).
- The vault has been rendered through step 5; the journal accurately reflects what landed.
- The user can re-run `wiki init` after addressing the issue only if the target is emptied first (today's refusal logic still applies on re-run).

### Edge case — `git commit` returns non-zero (typically: missing global `user.name`/`user.email`)

- Surface the subprocess `stderr` verbatim through a `WikiError`. The error message must include the hint: *"set `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"`, then re-run; or pass `--no-git` to skip git initialization."*
- `.git/` exists (`git init` succeeded) but has no commits.
- `VaultGitInitializedEvent` **was** appended (step 6 appends it before staging/commit), so the journal claims git initialization while no commit lands. This is the partial-state matrix entry the [Journal](#outputs) note above references: the kit chooses "single commit + clean porcelain" over "event-only-if-commit-succeeded."
- Recovery (primary): the user fixes git config and runs `cd <target> && git add -A && git -c commit.gpgsign=false commit -m "Initialize wiki vault from <recipe> recipe"` to complete the commit manually. This honors the journaled event without re-emitting it; the porcelain-clean invariant ends up satisfied.
- Recovery (secondary): the user removes the partial vault and starts over in a fresh directory. The original target's stranded `VaultGitInitializedEvent` is harmless and disappears with the target.
- The kit does **not** automatically detect the partial state. `wiki doctor` does not pin behavior for "journaled event, missing commit" in this spec (see Non-goals). The two recovery paths above are the contract; doctor is not.

### Edge case — crash between `git init` and `git commit`

A SIGKILL or power loss between step 6's `append_event` call and the subsequent `git commit` succeeding leaves the same partial state as the commit-failure case above: a journaled `VaultGitInitializedEvent` with no commit. Recovery is identical — manual `git add -A && git commit` is primary, fresh-directory restart is secondary, doctor is not invoked.

### Edge case — re-run after a crash (init-in-progress, ADR-0008)

Per ADR-0008's init-in-progress semantics, a journal with a `VaultInitEvent` but no `PrimitiveInstallEvent` is re-runnable. Under this spec:

- If `.git/` exists from the prior run, the new run treats it as the "target already contains `.git/`" variant: skip step 6 (the git phase). The user is left with the prior run's `.git/` and the new run's fully-rendered vault. No `VaultGitInitializedEvent` lands.
- If `.git/` does not exist (the crash happened before step 6), the new run reaches step 6 normally and journals `VaultGitInitializedEvent`.

This is a deliberate gap: the spec does not promise that re-runs always end with a journaled `VaultGitInitializedEvent`. The promise is that the kit never re-initializes or overwrites an existing `.git/`.

## Error cases

| Trigger | Behavior | Filesystem effect | Journal effect |
| --- | --- | --- | --- |
| `git` not on `$PATH`, no `--no-git` | `WikiError("git is not on PATH; install git or pass --no-git, then re-run")` | Target unchanged | None |
| Target is a file | Existing refusal | Target unchanged | None |
| Target is non-empty (no `--adopt`) | Existing refusal | Target unchanged | None |
| `git init` fails (e.g., a broken `git` wrapper, permission error on `.git/` create, disk full) | `WikiError("git init failed: <stderr>")` — stderr (trailing whitespace trimmed), no config hint (the failure is not config-shaped). The error message MUST NOT contain the literal substring `pass --no-git to skip git initialization` — that substring is the load-bearing anchor for the negative assertion in contract test #7. | Vault rendered (steps 4–5); no `.git/` (or a partial one git may leave behind) | `VaultInitEvent` + `PrimitiveInstallEvent`(s) present; **no** `VaultGitInitializedEvent` (the event is appended only after `git init` succeeds) |
| `git add` fails (e.g., disk full, permission, filesystem oddity) | `WikiError("git add failed: <stderr>")` — same shape as `git init` failure, no config hint. The plausible causes are not config-shaped; bucketed with init-failure for the user-facing message. | Vault rendered; `.git/` exists; nothing staged | `VaultGitInitializedEvent` IS present (appended before staging) — same partial-state recovery story as commit-failure |
| `git commit` fails (typically: missing global `user.name`/`user.email`) | `WikiError` with stderr (trailing whitespace trimmed) AND a config hint, MUST contain the literal substring `pass --no-git to skip git initialization`, plus the prose: *"set `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"`, then re-run."* The hint is unconditional on this branch — it's the most common cause and a strict superset of what stderr alone communicates. | Vault rendered; `.git/` exists with no commits | `VaultGitInitializedEvent` IS present (appended before staging — see Edge cases for the partial-state recovery story) |

## Invariants

- **Journal lands before disk.** `VaultGitInitializedEvent` is appended **after** `git init` succeeds and **before** `git add -A`/`git commit` runs. A successful `wiki init` therefore guarantees the event AND a commit; a crash after the event but before the commit leaves a documented partial state (see Edge cases); recovery is user-driven, not a doctor responsibility.
- **`.git/` commit history is not retroactively modified.** Once `.git/` exists with any commits, the kit never adds to or rewrites that history. The "target already contains `.git/`" variant is the only branch where the kit could plausibly touch an existing repo; it doesn't. Test #8 pins this via a HEAD snapshot. (Note: this is a deliberate relaxation from "the kit never touches `.git/` at all" — a redundant `git init` call on an existing repo touches `.git/hooks/*.sample` and `.git/config` mtimes but does not alter user-observable git state; the spec promises the user-observable contract, not internal git housekeeping.)
- **`safe_write` is not bypassed.** `.gitignore` continues to land via render → `safe_write` exactly as today. No new entry to the documented-exceptions list in `docs/specs/safe-write-ordering/spec.md`. The `git init` / `git commit` subprocess invocations operate on git's `.git/` metadata directory, not on tracked vault content — they sit outside the safe-write boundary by construction.
- **All subprocess invocations are `shell=False` with argv-list form.** No string-built shell commands; the recipe name flows in as a single argv element to `git commit -m`, so quoting is git's problem, not the kit's. Pinned here so a future maintainer relaxing recipe-name validation doesn't open an injection surface at this site.
- **`--no-git` vault tree matches default-flag vault tree.** Excluding `.git/` and `.wiki.journal/` (which carries per-event timestamps), the rendered file set is byte-identical: same `.gitignore`, same `frontmatter.schema.yaml`, same kit-rendered pages. Journal events from each run are aligned on `(type, by)` tuples separately and match modulo `VaultGitInitializedEvent`, which is present only in the default run.
- **Pre-flight before mutation.** When `--no-git` is not in effect and `git` is missing, the kit refuses **before** creating the target directory or appending any journal event.
- **Clean working tree post-init.** After a successful default-flag `wiki init`, `git status --porcelain` returns empty bytes. The append-before-stage ordering above is the load-bearing choice for this invariant.

## Contracts with other modules

- **`cli.py:_cmd_init`** — orchestrates the new flow; gains an `args.no_git: bool` field via argparse. The `shutil.which("git")` pre-flight lives here (not in `git_init`) so a refusal happens before any state mutation. `initialize_git` is called inside the open `use_journal_cache(journal_path)` scope so the new event flows through the cache like every other init-time append.
- **`cli.py:_build_parser`** — adds `init.add_argument("--no-git", dest="no_git", action="store_true", help="Skip git repo initialization and the initial commit. The kit's .gitignore still ships through the normal render path.")`.
- **`llm_wiki_kit/git_init.py`** (new) — exposes `initialize_git(target: Path, *, recipe_name: str, journal_path: Path, _now: datetime) -> None`. The `_now` argument's leading underscore signals that the value is **not** used for the journal event — the function recomputes its own timestamp immediately before the append so a long `git init` on slow disks doesn't journal a stale time. The parameter is kept for call-site symmetry with `_cmd_init`. Handles the two `subprocess.run` calls (`git init`, `git commit`), the `now`-recompute, and the `VaultGitInitializedEvent` append. Idempotent on the "`.git/` already exists" branch: returns without side effects, no event. No `git rev-parse` captures — the event carries no SHA or branch fields. All subprocess calls use the argv-list form with `shell=False`; recipe name flows in as a single argv element.
- **`models.py`** — gains `VaultGitInitializedEvent`. Registered in the discriminated `Event` union. Old journals without the event still parse — additive change.
- **`journal.py`** — no signature change; `append_event` accepts the new event by virtue of its union membership.
- **`docs/specs/wiki-init-adopt/spec.md`** — adopt composition is documented in this spec's two `--adopt` variants. When `--adopt` is implemented, those branches become the canonical reachable code paths. The implementation PR adds **one line of cross-reference** to the adopt spec's existing "Target contains `.git/`" passage; no semantic change to adopt's behavior, no amendment to adopt's invariants. Full composition testing waits until `--adopt` ships.

## Acceptance criteria

These translate directly into contract tests (next section).

- [ ] `wiki init my-vault --recipe personal` produces `.git/` and an initial commit; `git log --oneline` shows exactly one commit; `git status` is clean.
- [ ] The initial commit message is `Initialize wiki vault from <recipe> recipe` with the recipe name interpolated.
- [ ] `wiki init my-vault --recipe personal --no-git` produces the same vault tree (excluding `.git/` and `.wiki.journal/`) as a default-flags run within the same kit version. Verified by running `wiki init` twice in one test against two `tmp_path`s — once default, once `--no-git` — hashing every file under each vault (excluding `.git/` and `.wiki.journal/`, which carries per-event timestamps), and asserting equal. The journal events from each run are aligned separately on `(type, by)` tuples, with `VaultGitInitializedEvent` present only in the default run.
- [ ] When `git` is not on `$PATH` and `--no-git` was not passed, `wiki init` refuses with the documented error before creating the target directory or appending any journal event. Order-of-refusals: the empty-target refusal fires first when both conditions would trigger.
- [ ] When `git commit` fails, `wiki init` surfaces the underlying stderr alongside the git-config hint, does not journal `VaultGitInitializedEvent`, and leaves the rendered vault in place.
- [ ] When `git init` fails (broken `git` binary, permission error on `.git/` create, etc.), `wiki init` surfaces the stderr verbatim *without* the config hint, does not journal `VaultGitInitializedEvent`, and leaves the rendered vault in place.
- [ ] `VaultGitInitializedEvent` round-trips through the journal (model → JSONL line → model) with `by="wiki-init"`, `timestamp`, and `schema_version=1`. No `commit_sha` or `branch` field; see §Outputs for why.
- [ ] `wiki doctor` reports clean on both a `--no-git` vault and a default-`--git` vault.
- [ ] When `target/.git/` exists (synthesized in tests by pre-creating an empty git repo in a half-rendered vault and calling `initialize_git` directly), the function emits no `VaultGitInitializedEvent` and runs no `git init` / `git commit` subprocess.
- [ ] After a default `wiki init`, the initial commit's tracked-files list (from `git ls-tree -r --name-only HEAD`) includes `.gitignore` AND no path matching `*.proposed` — pinning the invariant that `install_primitives` (which renders `.gitignore`) finishes before `git add -A` runs.
- [ ] The `.gitignore` rendered into a fresh vault contains the literal pattern `*.proposed` (one of the load-bearing lines this spec relies on for the "initial commit excludes sidecars" invariant) and contains no remaining `{vault_name}` token (`.gitignore` is in `render.INTERPOLATED_FILES`; an unsubstituted token would indicate a render-context bug). The full byte content is the render of `core/files/.gitignore` through `render.render_tree` with the vault's context map — pinned by the existing render tests, not duplicated here.

### Contract tests

Live in `tests/integration/test_wiki_init_git.py` (integration, real `git` binary) and `tests/unit/test_models.py` (unit, model only).

| # | Test | Level | Asserts |
| --- | --- | --- | --- |
| 1 | `test_wiki_init_default_creates_git_repo` | integration | End-to-end against a real `git` binary; `.git/` exists; `git log --oneline` shows one commit; commit message matches; `VaultGitInitializedEvent` present in journal with `by="wiki-init"`; `git status --porcelain` returns empty (porcelain-clean invariant). |
| 2 | `test_wiki_init_no_git_skips_git` | integration | `--no-git` produces no `.git/` and no `VaultGitInitializedEvent`; vault content present. |
| 3 | `test_wiki_init_no_git_matches_default_tree` | integration | Two-run comparison inside the same test: default and `--no-git` produce the same set of files (excluding `.git/`) with the same hashes; journals match on `(type, by, fields-other-than-timestamp)` modulo `VaultGitInitializedEvent`. |
| 4 | `test_wiki_init_refuses_when_git_missing` | integration | `shutil.which("git")` monkeypatched to return `None`; assert `WikiError`, assert target directory not created, assert no journal file. |
| 5 | `test_wiki_init_empty_check_fires_before_git_pre_flight` | integration | Target is non-empty AND `shutil.which("git")` returns `None`; assert the empty-target error is raised (pins the order). |
| 6 | `test_wiki_init_surfaces_commit_failure` | integration | Force `git commit` to fail by running with empty git config (`HOME=tmp_path`, `GIT_CONFIG_GLOBAL=/dev/null`); assert `WikiError` whose message contains BOTH the verbatim stderr AND the literal substring `pass --no-git to skip git initialization`; `VaultGitInitializedEvent` IS present (appended before staging — see Edge cases); `.git/` exists with no commits; vault otherwise rendered. |
| 7 | `test_wiki_init_surfaces_init_failure` (CLI) and `test_initialize_git_surfaces_init_failure` (direct-call) | both integration, broken-`git` shim on `$PATH` | Both forms prepend a broken `git` shim to `$PATH` so `shutil.which("git")` finds it (pre-flight passes) and `subprocess.run(["git", "init"], …)` runs the shim and exits non-zero. Asserts `WikiError` whose message does NOT contain the literal kit-authored substring `pass --no-git to skip git initialization`; for the CLI form, the vault is rendered with no `.git/`; no `VaultGitInitializedEvent` in either. The shim approach is the only failure mode that survives both the CLI's empty-target pre-check and the function's `.git/`-already-exists short-circuit (the latter now triggers on both directory and gitfile shapes — see Variant "target already contains `.git/`"). |
| 8 | `test_initialize_git_skips_when_dot_git_exists` | integration (direct-call only, in `test_git_init.py`) | Pre-create a `.git/` repo in a half-rendered vault with one known seed commit; snapshot `(.git/HEAD).read_bytes()` and `git rev-parse HEAD` before the call. Call `initialize_git` directly. Assert (a) `git rev-parse HEAD` equals the seed SHA, (b) `(.git/HEAD).read_bytes()` is byte-identical to the snapshot (rules out a redundant `git init` rewriting HEAD on some git versions), (c) `git log --oneline` lists only the seed commit, (d) no `VaultGitInitializedEvent` was appended. End-to-end CLI form is deferred to `--adopt` implementation — it's the only path that can compose an empty-target check with a pre-existing `.git/`. |
| 9 | `test_wiki_init_initial_commit_excludes_proposed_sidecars` | integration | After a default `wiki init`, `git ls-tree -r --name-only HEAD` contains `.gitignore` and contains no path ending in `.proposed`. Pins the "install before git" ordering. |
| 10 | `test_rendered_gitignore_load_bearing_invariants` | integration | After a default `wiki init`, the rendered `.gitignore` contains `*.proposed` (load-bearing for the "initial commit excludes sidecars" test) and contains no unsubstituted `{vault_name}` token (`.gitignore` is in `render.INTERPOLATED_FILES`). Substantive content is covered by existing render-pipeline tests; this test pins only the two invariants this spec depends on. |
| 11 | `test_wiki_doctor_clean_after_git_init` | integration | `wiki init` then `wiki doctor` exits 0 with no proposed sidecars, both with and without `--no-git`. Additionally, on the default-flag run, `git status --porcelain` is empty — catching regressions where a journal write or render artifact lands after the initial commit. The `--no-git` sub-assertion skips the porcelain check (no repo to query). |
| 12 | `test_vault_git_initialized_event_roundtrip` | unit | Pydantic model (with only `type`, `timestamp`, `by`, `schema_version`) serializes via `model_dump_json` and parses back through the `Event` discriminated union; `type` literal is `"vault.git_initialized"`. |

## Non-goals

- **Setting `git config user.name` / `user.email` in the new repo.** The kit defers to global git config; missing config produces a surfaced error with a hint, not a silent fake-identity commit.
- **Adding a remote, pushing, or scaffolding a GitHub repository.** The kit's git work ends at "you have a local repo with one initial commit." Cloud-side scaffolding is a future RFC if it ever lands.
- **Renaming or pinning the default branch.** `git init` honors `init.defaultBranch`; the kit records whatever branch results.
- **Modifying `.gitignore` content.** The kit's existing `core/files/.gitignore` is sufficient. Specifically: `*.proposed` stays in `.gitignore`. The user brief flagged this as "probably not — the user wants to see those," and that view is overridden here: `wiki doctor` already surfaces `.proposed` sidecars from the journal, so committing them adds churn without surfacing anything new. A user who explicitly wants to commit a sidecar for review can `git add -f path.proposed`.
- **Making subsequent commits.** The kit makes one initial commit. Every later state-changing kit operation continues to write through `safe_write` and the journal; users commit when they choose.
- **Full `--no-git + --adopt` integration testing.** The adopt branch's `.git/`-aware behavior is documented in this spec, but exercising the full composition requires `--adopt` implementation, which is a separate spec/PR. Test #8 covers the `git_init` half of the composition (the `.git/`-pre-exists branch).
- **`--git/--no-git` BooleanOptionalAction shape.** Rejected: the default is settled (git on), and there is no third state worth modeling. A bare `--no-git` flag is simpler and reads naturally in help text.
- **A `wiki uninit` or `wiki nuke-git` command.** Out of scope.
- **`wiki doctor` detection of "event journaled, no commit landed" partial state.** Doctor stays silent on this divergence; the recovery contract is user-driven (manual `git add -A && git commit`, or fresh-directory restart). Adding doctor logic for this case is a follow-up if it ever becomes a real pain point — for now, the partial state is rare (only reachable via `git commit` failure or a kill between two adjacent kit operations) and the recovery is one line.

## Constraints

- **No new top-level runtime dependency.** Git is invoked via `subprocess`; no `gitpython` or other library. `subprocess` + `shutil.which` is sufficient. (Runtime-dep stance is set in AGENTS.md and would otherwise require an ADR.)
- **No bypass of `safe_write`.** `.gitignore` continues to flow through render → `safe_write` exactly as today. No new entry to the documented-exceptions list.
- **One new module boundary, narrowly scoped.** `llm_wiki_kit/git_init.py` is the only new file under `llm_wiki_kit/`. It is a thin subprocess wrapper; it does not introduce a class hierarchy, a registry, or a configurable backend.
- **No new CLI verb.** The change is a flag on `wiki init`.
- **No reordering of the `VaultInitEvent → install_primitives` sequence.** Git operations append after `install_primitives` so a mid-install crash cannot leave a partial `.git/` referencing non-existent files.
- **No new top-level directory.** The spec dir is under `docs/specs/`; the module is under `llm_wiki_kit/`.
