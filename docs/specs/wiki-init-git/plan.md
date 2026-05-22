# Plan: wiki init --no-git (default git-init on new vaults)

> **Implementation plan paired with `spec.md`.** The spec says *what*;
> the plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** `docs/specs/wiki-init-git/spec.md`
- **Owner:** `eugenelim`

## Approach

One mechanical change to the CLI surface, one small new module, one new journal event, and a docs sweep. The ordering is dictated by the journal-first invariant from ADR-0002: `VaultInitEvent` + `install_primitives` continue to land first (unchanged); the git init + initial commit + `VaultGitInitializedEvent` append at the tail of `_cmd_init` so a crash mid-install can never leave a `.git/` containing references to non-existent files.

Git-aware logic lives in a new `llm_wiki_kit/git_init.py` so the two subprocess shell-outs are isolated and mockable, and so `_cmd_init` reads as a sequence of named operations rather than a long inline subprocess script. The new module exports one public function; no class hierarchy.

The default branch resolution is deliberately punted to git itself — the kit does not capture or journal the branch name. Whatever `init.defaultBranch` produces (`master` pre-2.28, `main` post-2.28, or any user override) becomes the working branch with no kit-level introspection. See spec §Outputs for the rationale behind the bare event shape.

## Pre-conditions

- ADR-0002 (journal as state truth) and ADR-0008 (init-adopt ownership policy) already landed (they are).
- **PR #65 (README onboarding restructure) must land before this plan's docs sweep (Step 6) executes.** PR #65 introduces the manual `git init && git add . && git commit -m "init vault"` block in the README's quick-start; Step 6 here rewrites that block to describe the new default. If PR #65 lands after the implementation PR, Step 6's edit will operate on a section that doesn't yet exist and silently land in the wrong place. Enforcement mechanism: the implementation PR's body declares `Depends on: #65` and the maintainer manually gates merge — no CI hook is added for a one-shot dependency. (If PR #65 has already merged by the time the implementation PR opens, the dependency line still records the lineage.)
- The `wiki-init-adopt` spec is landed (it is, as of commit `226d9ee`). Its implementation is **not** required for this work to ship. Full `--no-git + --adopt` integration testing waits on adopt implementation.

## Steps

> Cross-reference for the spec's contract-tests table: Step 1 covers #12, Step 2 covers #8 (direct-call form) + drives the other event invariants, Step 3 covers #1–#5, Step 4 covers #6, #7, #9, #10 (and the end-to-end form of #8), Step 5 covers #11. Step 6 has no contract tests; it's the docs sweep.

1. **`VaultGitInitializedEvent` round-trips through the journal.**
   - Depends on: none.
   - Verification: TDD (`tests/unit/test_models.py`).
   - Changes: add the event model to `llm_wiki_kit/models.py`, register it in the `Event` discriminated union. Add an inline marker comment on `llm_wiki_kit/models.py` next to `Recipe.name`'s `NAME_PATTERN` constant noting "load-bearing for `git_init.initialize_git`'s commit-message argv — see `docs/specs/wiki-init-git/spec.md` §Behavior step 6" so a future relaxation of recipe-name validation can find this site via grep.
   - Tests:
     - `test_vault_git_initialized_event_roundtrip` — instantiate (with only `type`, `timestamp`, `by`, `schema_version` — no `commit_sha` or `branch`; see spec §Outputs), serialize via `model_dump_json`, parse back through the union, assert equality.
     - `test_vault_git_initialized_event_discriminator` — assert `type` is the `"vault.git_initialized"` literal and the union dispatches correctly.
   - Done when: both tests pass and `mypy llm_wiki_kit tests` is clean.

2. **`git_init.initialize_git` shells out, makes one commit, journals the event, and skips when `.git/` exists.**
   - Depends on: Step 1.
   - Verification: TDD throughout (`tests/integration/test_git_init.py`, real `git` available). All assertions are outcome-shape — no subprocess-call counting.
   - Changes: new module `llm_wiki_kit/git_init.py` with the signature from spec §Contracts; internal helpers `_run_git(args: list[str], cwd: Path) -> CompletedProcess` and a private `_dot_git_exists(target: Path) -> bool` if the call-site bool-check needs naming. No `_capture_head_sha_and_branch` helper — the event carries no SHA or branch, so no rev-parse is needed.
   - Tests (outcome-shape — drive the real `git` binary inside `tmp_path`; assert resulting state, not subprocess call counts):
     - `test_initialize_git_creates_repo_with_one_commit` — happy path; after the call, `tmp_path/.git/` exists, `git log --oneline` shows exactly one commit with the documented message, `VaultGitInitializedEvent` is in the journal (appended before the commit; see spec), and `git status --porcelain` returns empty bytes.
     - `test_initialize_git_surfaces_init_failure` — pre-create `tmp_path/.git` as a *file* (so `git init` fails); assert `WikiError`, message contains stderr verbatim, message does NOT contain the literal substring `pass --no-git to skip git initialization` (the kit-authored anchor — git's own stderr is out of the kit's control), no event journaled.
     - `test_initialize_git_surfaces_commit_failure` — call with empty git config (`monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")` + clean `HOME`); assert `WikiError`, message contains BOTH the stderr AND the literal substring `pass --no-git to skip git initialization`, no event journaled.
     - `test_initialize_git_skips_when_dot_git_exists` — pre-create a valid `tmp_path/.git/` with one known seed commit (e.g. via `git init && touch seed && git add seed && git commit -m seed` from a fixture). Snapshot `(.git/HEAD).read_bytes()` and `git rev-parse HEAD` before the call. Call `initialize_git`. Assert (a) `git rev-parse HEAD` equals the seed SHA, (b) `(.git/HEAD).read_bytes()` is byte-identical to the snapshot, (c) `git log --oneline` lists only the seed commit, (d) no `VaultGitInitializedEvent` is journaled, (e) function returns normally. Outcome-shape only.
   - Done when: all four tests pass on a sandbox with `git` available.

3. **`wiki init` argparse gains `--no-git`; `_cmd_init` calls `initialize_git` by default; pre-flight refuses when git is missing.**
   - Depends on: Steps 1, 2.
   - Verification: TDD (`tests/integration/test_wiki_init_git.py`).
   - Changes:
     - Add `init.add_argument("--no-git", dest="no_git", action="store_true", help=...)` in `_build_parser`.
     - In `_cmd_init`: after the existing empty-target refusal (`target.exists() and any(target.iterdir())`), and when `not args.no_git`, call `shutil.which("git")` and raise `WikiError` if it returns `None`. This pins the spec's order-of-refusals invariant.
     - After `install_primitives` returns inside the journal-cache scope, call `initialize_git(target, recipe_name=recipe.name, journal_path=journal_path, now=now)` when `not args.no_git`. `initialize_git` recomputes `now_git = datetime.now(UTC)` internally per spec.
   - Tests (integration; correspond to spec contract tests #1, #2, #3, #4, #5):
     - `test_wiki_init_default_creates_git_repo`
     - `test_wiki_init_no_git_skips_git`
     - `test_wiki_init_no_git_matches_default_tree`
     - `test_wiki_init_refuses_when_git_missing` — `monkeypatch.setattr(shutil, "which", lambda name: None if name == "git" else "/usr/bin/" + name)`.
     - `test_wiki_init_empty_check_fires_before_git_pre_flight` — pin the refusal order with both conditions failing.
   - Done when: all five tests pass.

4. **End-to-end error surfaces are covered.**
   - Depends on: Step 3.
   - Verification: TDD (`tests/integration/test_wiki_init_git.py`).
   - Changes: extend the same integration file with the remaining contract tests.
   - Tests (correspond to spec contract tests #6–#10):
     - `test_wiki_init_surfaces_commit_failure` — empty git config; assert `WikiError` whose message contains BOTH the verbatim stderr AND the literal substring `pass --no-git to skip git initialization`; `VaultGitInitializedEvent` IS journaled (appended before staging per spec §Behavior step 6); `.git/` exists with no commits; vault otherwise rendered.
     - `test_wiki_init_surfaces_init_failure` — `.git` pre-existing as a file; assert `WikiError` with stderr but no hint; vault rendered; no event.
     - `test_initialize_git_skips_when_dot_git_pre_exists` — end-to-end counterpart to Step 2's direct-call test (same outcome shape, driven through `wiki init` instead of `initialize_git`).
     - `test_wiki_init_initial_commit_excludes_proposed_sidecars` — after default `wiki init`, `git ls-tree -r --name-only HEAD` contains `.gitignore`; contains no path ending in `.proposed`.
     - `test_rendered_gitignore_load_bearing_invariants` — assert the rendered `.gitignore` contains `*.proposed` and contains no remaining `{vault_name}` token.
   - Done when: all five tests pass.

5. **`wiki doctor` remains clean.**
   - Depends on: Step 3.
   - Verification: Goal-based — `wiki doctor` exits 0 on both vault shapes (the `Done when:` line *is* the contract).
   - Changes: no code change expected. The `doctor` orphan check derives owned-directory roots from journaled paths; `.git/` is never journaled, so it cannot surface as orphan.
   - Tests (corresponds to spec contract test #11):
     - `test_wiki_doctor_clean_after_git_init` — runs `wiki init` (default and `--no-git`), then `wiki doctor`; assert exit 0 with no proposed sidecars. On the default-flag run, also assert `git status --porcelain` returns empty bytes — pins the "initial commit captures the full kit shape; nothing landed after" invariant.
   - Done when: the test passes; if `wiki doctor` is unexpectedly noisy on `.git/`-bearing vaults, escalate via a new spec step before adjusting the test.

6. **Docs sweep.**
   - Depends on: Steps 3, 4, 5; PR #65 merged to main (see Pre-conditions).
   - Verification: Manual — read the rendered README and tutorial top-to-bottom against the implemented behavior; no automated check.
   - Changes:
     - **`README.md`** — locate the bash fenced block in the "Version it" step of the Quick start that runs `git init` (introduced by PR #65; whatever phrasing PR #65 settles on by merge time) and replace it with prose describing the new default: "By default, `wiki init` initializes a git repo and makes one initial commit; pass `--no-git` to skip if you'd rather manage versions yourself." Leave the surrounding step heading and adjacent steps intact.
     - **`docs/guides/tutorials/tutorial-1-first-vault.md`** — Step 1 currently says "`wiki init my-first-vault --recipe personal`" with no git mention. Add one paragraph after the journal-summary text: "Because `--no-git` was not passed, the kit also initialized a git repo for you. `git log --oneline` should show one commit named *Initialize wiki vault from personal recipe*. The rest of this tutorial is git-agnostic." A separate paragraph explains `--no-git` for users versioning differently.
     - **`docs/rfc/0001-v2-architecture.md`** — Update the CLI surface table entry for `wiki init` to mention `--no-git`. One line.
     - **`docs/specs/wiki-init-adopt/spec.md`** — one-line cross-reference at the existing "Target contains `.git/`" sentence: "*See also: `docs/specs/wiki-init-git/spec.md` §"Variant — target already contains `.git/`" for how `wiki init`'s git-init phase responds to a pre-existing `.git/` when both flags compose.*" Pure cross-reference; no semantic change. (This is the one adopt-spec touch carved out of the otherwise "no amendment" rule; rationale: the adopt spec is silent on the fact that `.git/` existence is now a control-flow input to `_cmd_init`.)
   - Done when: a fresh tutorial run against the new behavior reads correctly; `mypy`/`ruff`/`pytest` still green; **AND** `! grep -qE 'git init.*git add.*git commit' README.md` (catches both the `&&`-joined and multi-line variants of PR #65's manual block) **AND** `! grep -qE '^\s*git init\b' README.md` (catches a leading-line `git init` that the `-E` pattern above might miss if the lines aren't adjacent) **AND** `grep -q 'no-git' README.md` (the new flag is documented).

## Verification gate

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

Plus all twelve new tests (two model unit tests + ten integration tests) pass against a sandbox with `git` available. CI matrix already has `git` (runner clones the repo); no infra changes.

## Risks

- **Missing global git config breaks the happy path.** Users without `user.name`/`user.email` set globally hit the commit-failure error. Mitigation: the `WikiError` text names the specific commands to run and the `--no-git` escape; this is the documented behavior. Considered, rejected: pre-flighting git config — adds another refusal surface for a transient user state, and the failure mode is already clear from `git`'s own stderr.
- **GPG signing hook conflicts.** `commit.gpgsign=false` is passed inline to disable signing for the kit's one commit; users with `commit.gpgsign=true` globally see signed commits *after* `wiki init`. Risk accepted; no test pins this behavior (would require a per-test gitconfig sandbox and add coverage of a fixed risk).
- **`git` versions older than 2.28** produce `master` instead of `main`. The spec records whatever branch results. Risk is informational only.
- **CI portability.** The `git_init` tests rely on a real `git` binary. GitHub Actions runners have it. Contributor sandboxes that don't would see these tests fail with a clear "git not found" — that's a CI-config issue, not a kit bug.
- **Init-in-progress + git composition is partially exercised.** Step 4's `test_initialize_git_skips_when_dot_git_pre_exists` covers the call shape but not a real crash-then-resume scenario. Full crash-recovery coverage waits on adopt implementation.
- **PR #65 / this work race.** If the implementation PR lands first, Step 6's README edit has no target block. The Pre-conditions section pins PR #65 as a hard dependency; CI should refuse the merge until then.

## Out of scope

- `--adopt` implementation (separate spec at `docs/specs/wiki-init-adopt/`, separate PR).
- Initial-commit author override, signing-key override, or pre-flight of git config.
- Multi-commit phase markers (one commit per primitive, one per region pass, etc.). One initial commit suffices.
- A `wiki uninit` / `wiki nuke-git` reverse-command.
- Auto-creating a remote or pushing.
- Pinning `init.defaultBranch`.
