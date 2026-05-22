# Plan: safe-write-ordering

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** Done
- **Spec:** [`docs/specs/safe-write-ordering/spec.md`](spec.md)
- **Owner:** TBD (maintainer)

## Approach

Land the three findings as one coherent change, not three independent
PRs. The reason: they share a single contract surface
(`write_helper.py`'s ordering between `append_event` and `Path.write_*`),
and splitting them invites two intermediate states the maintainer never
wants in `main`:

- *Event-before-disk landed; unjournaled-existing-pages still
  overwrites* — qC6's regression risk grows because every test that
  passes against the reordered code now relies on the silent-overwrite
  semantics.
- *Page-level drift landed; `.obsidianignore` bypass still
  undocumented* — the AGENTS.md anti-pattern ("Bypassing
  `write_helper.safe_write()`") is reinforced inside the same module
  that newly enforces qC6, with no comment naming the exception.

One spec, one PR is the cheapest path to a consistent contract. The
PR amends ADR-0002 §Negative and ADR-0004 §Mechanics + §Negative via
dated §Revisions entries — matching the existing
2026-05-15 / 2026-05-16 precedents on ADR-0004 (`adr/0004…md:182-204`).
This is not a supersession (the §Decision pithy quotes hold; only the
explanatory bodies change), so a new ADR is not warranted per
CONVENTIONS §"ADR vs. RFC vs. spec".

**Ordering inside the PR.** TDD against the spec's *contract* tests
first (crash-recovery shapes); implement the reorderings and predicate
changes; add the *construction* tests (call-sequence snapshots) once
the implementation is settled; pin removals; ADR amendments; AGENTS.md
carve-out; knowledge entry. The split between contract and
construction tests matters because the construction tests are
mock-shape — they pin call sequence, not behavior — and CONVENTIONS
§"Contract tests vs. construction tests" classifies those as plan-level
artifacts that can be revised if the implementation shape changes.

## Pre-conditions

- The journal-locking spec is `Implemented` (it is, per PR #33).
  `append_event` already `fsync`s and takes `LOCK_EX`, so this spec's
  "event durable before disk write" invariant is satisfied for free.
- No outstanding work changes the signatures of `safe_write`,
  `safe_write_region`, or `resolve_proposal` (none open as of branch
  date).
- ADR-0002 carries its 2026-05-16 amendments from PR #33. This plan
  edits the §Negative entry only, leaving the journal-locking pointer
  intact.
- ADR-0004 carries 2026-05-15 and 2026-05-16 §Revisions entries. This
  plan appends a third dated entry.
- Issue [#23](https://github.com/eugenelim/llm-wiki-kit/issues/23)
  still lists qC3, qC6, and C2 as open Concerns. (If they get closed
  by another PR first, fold this work down to whatever remains.)

## Steps

1. **Event-before-disk ordering: contract tests are red, then green.**

   *Depends on:* none.

   *Verification mode:* TDD.

   *Tests (contract — pinned to `spec.md` §Acceptance criteria):*

   Two test families:

   *(a)* Behavioral ordering observed at a black-box surface
   (these go RED against today's write-then-append code and GREEN
   after the reorder):

   - `test_safe_write_event_durable_when_disk_write_raises`
     (`tests/unit/test_write_helper.py`) — inject a *path-scoped*
     failure into `Path.write_bytes`: wrap the original with a
     conditional raise that fires only when `self == abs_path`,
     pass-through otherwise. (Don't monkeypatch `Path.write_bytes`
     globally — `journal.append_event` and `_write_holder_file`
     use the same path machinery, and a blanket raise breaks them
     and produces misleading test failures.) Call `safe_write` for
     a fresh path; expect `OSError`; assert `read_events(journal_path)`
     contains exactly one `PageWriteEvent` for the path AND
     `assert not abs_path.exists()` (so a future refactor that
     ordered append→write-partial→raise can't quietly pass).
     Today's order (write→raise→never-append) fails the
     event-present assertion; reordered (append→write→raise)
     passes both.
   - `test_safe_write_drift_event_durable_when_sidecar_write_raises`
     — same shape for the drift path. Conditional raise on
     `proposed_abs`. Assert `PageProposalEvent` durable AND
     `not proposed_abs.exists()`.
   - `test_safe_write_region_event_durable_when_disk_write_raises` —
     conditional raise on `Path.write_text` for `abs_path`. Assert
     `ManagedRegionWriteEvent` durable AND the file's bytes equal
     the pre-call content (unchanged).
   - `test_resolve_proposal_events_durable_when_disk_write_raises`
     — conditional raise on `abs_path.write_bytes`. Assert both
     `PageWriteEvent` and `PageConflictResolvedEvent` durable AND
     `abs_path.read_bytes()` equals the pre-call content (the
     resolve target was either absent — pre-call check — or was
     the unmerged on-disk version; either way the assertion is
     "unchanged from immediately before the call").

   *Tests (contract — happy-path ordering snapshot, pins the
   "event-before-disk" invariant when disk write SUCCEEDS):*

   The failure-injection family above pins the contract only when
   the disk write raises. A future refactor that did
   `write → if write_succeeded: append_event` would still pass
   every failure-injection test. These tests close that gap:

   - `test_safe_write_event_in_journal_at_moment_of_disk_write` —
     wrap `Path.write_bytes` with a path-scoped recorder that, on
     the call against `abs_path`, snapshots
     `read_events(journal_path)` and then delegates to the
     original implementation. Call `safe_write` on a fresh path;
     expect success; assert the snapshotted event list contains a
     `PageWriteEvent` for `relative_path` whose `hash` matches the
     new content. Catches the happy-path reorder.
   - `test_safe_write_region_event_in_journal_at_moment_of_disk_write`
     — same shape; snapshot taken on the `Path.write_text` call;
     `ManagedRegionWriteEvent` present.
   - `test_resolve_proposal_events_in_journal_at_moment_of_disk_write`
     — same shape; both `PageWriteEvent` and
     `PageConflictResolvedEvent` present at the snapshot.

   *(b)* Recovery-path observation in
   `tests/integration/test_wiki_doctor.py` (pass today, pin the
   §Edge cases recovery contract so a future refactor can't drop
   the doctor's reconciliation hook):

   - `test_doctor_surfaces_orphan_page_event_as_missing`
     — append a `PageWriteEvent` directly (bypassing `safe_write`)
     for a path that is never written; run `run_doctor`; assert
     `Issue(MISSING, "<path>")` is in the result.
   - `test_doctor_surfaces_orphan_managed_region_event_as_drift`
     — pre-seed a file with a managed region whose on-disk body
     differs from a manually-appended `ManagedRegionWriteEvent`'s
     `content_hash`; run `run_doctor`; assert
     `Issue(MANAGED_REGION_DRIFT, "<file>:<region>")` is reported
     (no trailing detail for hash-mismatch per `doctor.py:176`).
   - `test_doctor_surfaces_orphan_resolve_events`
     — append `PageWriteEvent` + `PageConflictResolvedEvent` for a
     path; do not write the file; assert `run_doctor` reports
     `MISSING` (or `PAGE_DRIFT` if the path exists with non-matching
     bytes).

   *Tests (contract — crash-recovery retry, pins the predicate's
   third disjunct landed in step 2):*

   - `test_safe_write_recovers_missing_file_when_baseline_journaled`
     (`tests/unit/test_write_helper.py`) — append a
     `PageWriteEvent` directly for a fresh path; call `safe_write`
     with the same content; assert `WriteResult.WRITTEN`, file is
     on disk with the journaled content, no `.proposed` sidecar
     created, exactly one *new* `PageWriteEvent` appended (two
     total in journal: the seed and the recovery write).
   - `test_resolve_proposal_crash_recovery_produces_idempotent_state`
     (`tests/unit/test_write_helper.py`) — drive a
     `resolve_proposal` call that crashes after events are durable
     but before `abs_path.write_bytes`. Use a **call-counted**
     path-scoped raise: "raise on the first invocation against
     `abs_path`, pass-through on subsequent invocations" (the
     family-(a) recipe is unconditional on the target path and
     would crash the retry too). Re-run `resolve_proposal` with
     the same content; assert `replay_state(read_events(journal))`
     produces a `VaultState` whose `page_writes[path]` and
     `pending_proposals` entries match the single-call outcome.
   - `test_safe_write_region_crash_recovery_routes_to_proposal`
     (`tests/unit/test_write_helper.py`) — pre-seed a
     managed-region file; journal a `ManagedRegionWriteEvent`
     whose `content_hash` differs from the on-disk region body
     (simulating "event durable, write partial or missed"); call
     `safe_write_region` with the same `new_content`; assert
     `WriteResult.PROPOSAL`, a `<file>.proposed` sidecar with the
     kit's intended rewrite, a `PageProposalEvent` appended. Pins
     spec §Edge cases sub-case 2's "recovery is proposal flow, not
     direct retry".

   *Tests (construction — call-sequence snapshots, plan-level only):*

   - `test_safe_write_calls_append_event_before_write_bytes` —
     monkeypatch `journal.append_event` with a recorder that
     snapshots `abs_path.exists()` at call time; assert the snapshot
     is `False` (or, for the repeat-write path, that the on-disk
     hash equals the pre-write baseline).
   - `test_safe_write_drift_calls_append_event_before_proposed_write_bytes`
     — same for the drift path; snapshot
     `proposed_abs.exists()`.
   - `test_safe_write_region_calls_append_event_before_write_text` —
     monkeypatch the recorder; assert on-disk file content unchanged
     at append-event time.
   - `test_resolve_proposal_calls_append_events_before_rewrite_and_unlink`
     — recorder snapshots both `abs_path.read_bytes()` and
     `sidecar.exists()` at append-event time.

   *Approach:*

   - Run the contract tests in family (a); confirm they fail today
     (today's `safe_write` writes the file before appending, so
     `OSError`-injected calls never reach `append_event`).
   - Reorder the three public functions in `llm_wiki_kit/write_helper.py`:
     `append_event` calls move above `abs_path.write_bytes(...)` /
     `proposed_abs.write_bytes(...)` / `abs_path.write_text(rewritten,
     ...)`. The `mkdir(parents=True, exist_ok=True)` calls stay
     where they are — they're idempotent and don't materially write
     the target file.
   - Update the docstrings on `safe_write`, `safe_write_region`, and
     `resolve_proposal` to name the event-before-disk invariant and
     cite this spec.

   *Done when:* family (a) tests turn green; family (b) tests stay
   green; the four construction tests pass; full `pytest
   tests/unit/test_write_helper.py tests/integration/test_wiki_doctor.py`
   passes; no other test regresses.

1. **Unjournaled existing pages trigger drift; byte-identical adopts.**

   *Depends on:* step 1 (the reorder is a precondition — this step
   touches the same predicate region of `safe_write` and would
   conflict with step 1's reorder if landed first).

   *Verification mode:* TDD.

   *Tests (contract — `spec.md` §Acceptance criteria):*

   - `test_safe_write_to_unjournaled_existing_file_writes_proposal`
     (differing bytes) — `tests/unit/test_write_helper.py`.
   - `test_safe_write_to_unjournaled_existing_file_does_not_touch_original`
     — same file.
   - `test_safe_write_first_write_to_absent_file_still_writes_directly`
     — guards against over-broadening.
   - `test_safe_write_adopt_fastpath_byte_identical_existing_file_writes_no_sidecar`
     — bytes match exactly; no sidecar; one event; the disk write
     is skipped. Assert *inode preservation*:
     `pre_ino = target.stat().st_ino` before the call;
     `target.stat().st_ino == pre_ino` after. Inode preservation
     is the load-bearing observable (Obsidian / inotify consumers
     react to inode changes). Additionally pin a path-scoped
     recorder on `Path.write_bytes` (same pattern as step 1's
     failure injection: pass-through except for `target`, where
     the call is recorded) and assert zero invocations for
     `target` — the recorder is the cross-platform pin for "the
     write was actually skipped" since mtime resolution varies by
     filesystem.
   - `test_safe_write_adopt_fastpath_baseline_becomes_journaled` —
     adopt fast-path → repeat-write path on next call.
   - `test_safe_write_adopt_fastpath_abandons_when_disk_changes_between_reads`
     — pre-seed an unjournaled file whose bytes match the kit's
     proposed content; install a path-scoped `Path.read_bytes`
     recorder that flips the on-disk content on its second
     invocation (the re-read inside `safe_write`'s adopt branch);
     call `safe_write`; assert `WriteResult.PROPOSAL`, a
     `.proposed` sidecar exists, the journal's last event is a
     `PageProposalEvent` (not `PageWriteEvent`). Pins the adopt
     fast-path's abandon branch — the only test that catches an
     implementer who drops the re-read.
   - `test_safe_write_adopt_fastpath_records_post_reread_timestamp`
     — pin spec §Behavior step 3's `now` recompute. Freeze
     `datetime.now` with a recorder that appends each call to a
     list and returns the next of `[T0, T1, T2, …]`; call
     `safe_write` along the adopt path; assert
     `len(now_calls) == 2` (call-entry + post-re-read recompute,
     no more) AND the journaled `PageWriteEvent.timestamp == T1`.
     The call-count assertion catches a future refactor that adds
     or drops a `now` call; the timestamp-equality assertion
     catches the original "implementer reused call-entry `now`"
     case.
   - `test_wiki_add_over_unjournaled_user_file_proposes_not_overwrites`
     — `tests/integration/test_wiki_add.py`. Pre-seed a vault with
     user content at a path a primitive will render to; run
     `_cmd_add`; assert `.proposed` sidecar, untouched user file.
   - `test_safe_write_region_unjournaled_region_byte_identical_still_writes_directly`
     — covers the byte-identical complement to the existing
     differing-bytes pin (page-vs-region guard, see Approach
     below).

   *Approach:*

   - Run — the contract tests fail.
   - Tighten the drift predicate in `safe_write`. Spell both
     predicates explicitly so the implementer can read them off:
     ```python
     no_history = baseline_hash is None
     file_present = abs_path.exists()
     bytes_match = on_disk_hash == new_hash

     direct_write = (
         (no_history and not file_present)              # fresh path
         or (not no_history and on_disk_hash == baseline_hash)  # no drift
         or (not no_history and not file_present)       # crash recovery: event durable, file absent
     )
     adopt = no_history and file_present and bytes_match
     # everything else → proposal
     ```
     The third disjunct is the crash-recovery branch: the journal
     records a `PageWriteEvent` for a path the file never
     materialized for (the §Edge cases case from `spec.md`). A
     plain re-run lands here — direct-write, no proposal — and
     `check_missing` clears on the next doctor pass.
   - The adopt branch: **re-read** `abs_path` and recompute its
     hash inside the same logical step (right before the
     `append_event` call) per spec §Behavior "Adopt fast-path"
     step 2; if the re-read hash diverges from `new_hash`, fall
     through to the proposal branch. Otherwise: **recompute `now =
     datetime.now(UTC)`** so the journaled timestamp reflects the
     adoption decision (per spec §Behavior step 3); append
     `PageWriteEvent(timestamp=now, …)`; skip `write_bytes`;
     return `WriteResult.WRITTEN`. The direct-write branch: append
     event, write file, return `WriteResult.WRITTEN`. The proposal
     branch: as today (after step 1's reorder).
   - `safe_write_region` is **unchanged** in this step. The page-vs-
     region distinction lives in spec §Non-goals; the existing
     `safe_write_region_first_write_with_drifted_baseline_is_written`
     pin (`tests/unit/test_write_helper.py:466-473`) stays as-is. It
     pins the differing-bytes unjournaled-region case (the one a
     region-scoped qC6 inversion would break in
     `aggregate_region_contributions`). The *new* guard
     `test_safe_write_region_unjournaled_region_byte_identical_still_writes_directly`
     covers the complementary byte-identical-region case (the one a
     future "let's mirror the page-level adopt fast-path into
     regions" refactor could regress); seed a file where the region
     body already equals `new_content`, call `safe_write_region`
     with no prior event, assert WRITTEN and one
     `ManagedRegionWriteEvent` appended.
   - **Remove** the obsolete pin
     `test_first_write_overwrites_existing_file_without_journal_entry`
     from `tests/unit/test_write_helper.py`. Replace its block-
     comment reference to "ADR-0004 §Mechanics step 2" with the new
     inverted test's docstring. Surface the removal in the PR
     description ("removed: pinned old wrong contract; replaced by
     `test_safe_write_to_unjournaled_existing_file_writes_proposal`
     and `test_safe_write_adopt_fastpath_byte_identical_existing_file_writes_no_sidecar`").

   *Done when:* all seven contract tests pass; the obsolete pin is
   removed; the region pin stays; full
   `pytest tests/unit/test_write_helper.py tests/integration/`
   passes. No other test regresses.

1. **`.obsidianignore` is a documented non-journaled bypass.**

   *Depends on:* none (touches `_ensure_obsidianignore`'s docstring
   and `AGENTS.md`, no overlap with steps 1–2's predicate region).

   *Verification mode:* goal-based (a docstring + an AGENTS.md edit
   + tests asserting the explicit choice).

   *Tests (contract):*

   - `test_ensure_obsidianignore_does_not_journal` — first drift on
     a fresh vault produces zero `PageWriteEvent`s whose `path` is
     `".obsidianignore"`.
   - `test_ensure_obsidianignore_remains_idempotent_via_pattern_check`
     — second drift event does not rewrite the file once the
     pattern is present.
   - `test_obsidianignore_bypass_doc_constant_points_at_this_spec` —
     `from llm_wiki_kit.write_helper import OBSIDIANIGNORE_BYPASS_DOC`;
     assert `OBSIDIANIGNORE_BYPASS_DOC == "docs/specs/safe-write-ordering/spec.md"`.
     The constant is the load-bearing pin; grep-discoverable.
   - `test_ensure_obsidianignore_docstring_references_bypass_constant`
     — `"OBSIDIANIGNORE_BYPASS_DOC"` appears in
     `_ensure_obsidianignore.__doc__`. Paired with the constant
     test so a docstring paraphrase that drops the reference
     fails red.
   - `test_doctor_does_not_flag_obsidianignore_as_orphan` —
     `.obsidianignore` exists in a vault with no journal entry;
     `run_doctor` does not produce `Issue(ORPHAN,
     ".obsidianignore")`.

   *Approach:*

   - Run — the docstring test fails; the others already pass
     (current behavior is non-journaled by accident; the spec
     promotes it to non-journaled by design).
   - Introduce a module-level constant in `write_helper.py`:
     ```python
     OBSIDIANIGNORE_BYPASS_DOC = "docs/specs/safe-write-ordering/spec.md"
     ```
     Place it near `OBSIDIAN_IGNORE_PROPOSED_PATTERN` so the two
     `.obsidianignore`-adjacent constants live together.
   - Add a docstring to `_ensure_obsidianignore` that cites the
     constant by name:
     ```python
     def _ensure_obsidianignore(vault_root: Path) -> None:
         """Append the ``\\.proposed$`` pattern to ``.obsidianignore`` if absent.

         **Documented non-journaled bypass** — the only one alongside
         ``resolve_proposal`` (which is journaled but bypasses drift).
         See ``OBSIDIANIGNORE_BYPASS_DOC`` §Non-goals "Why
         ``.obsidianignore`` is not journaled" and ADR-0004
         §Negative for the rationale.
         """
     ```
     The constant is the load-bearing anchor for the bypass; the
     docstring's reference to it is what
     `test_ensure_obsidianignore_docstring_references_bypass_constant`
     pins. A paraphrase that drops the constant name fails red.
   - Edit the kit's repo-root `AGENTS.md` §"Things you should not do
     without asking" — "Don't bypass `write_helper.safe_write()`"
     bullet (no carve-out exists in the current text; this step
     introduces one). Replace the bullet's body verbatim with:
     ```
     - **Don't bypass `write_helper.safe_write()`** for any file
       write that lands in a user's vault. Drift detection is
       load-bearing. (Documented exceptions:
       `write_helper.resolve_proposal` for user-mediated merges,
       `write_helper._ensure_obsidianignore` for the additive
       Obsidian-index config — see
       `docs/specs/safe-write-ordering/spec.md`.)
     ```

   *Done when:* all four tests pass; `AGENTS.md` carve-out updated;
   `grep -rn "_ensure_obsidianignore" llm_wiki_kit/ docs/` surfaces
   the docstring pointer; no other test regresses.

1. **ADR-0002 and ADR-0004 carry dated §Revisions entries.**

   *Depends on:* step 1, step 2 (ADRs document the contract those
   steps land; amend after the code is in place to avoid an
   intermediate state where the ADR claims a contract the code
   hasn't shipped).

   *Verification mode:* goal-based (grep + visual review).

   *Approach:*

   - `docs/adr/0002-journal-as-state-truth.md` §Negative: drop the
     sentence "Mitigated: `safe_write` writes the file *after*
     validating the event but reconciles on next run via `wiki
     doctor`." Replace with: "Mitigated: `safe_write` appends the
     event before touching disk per
     [`docs/specs/safe-write-ordering/spec.md`](../specs/safe-write-ordering/spec.md);
     a crash between the event append and the disk write is
     reconciled by `wiki doctor`'s `missing` / `page-drift` /
     `managed-region-drift` checks." Keep every other consequence
     and revision intact (the journal-locking pointer added in PR
     #33 stays).
   - `docs/adr/0004-drift-detection-and-proposal-flow.md` §Mechanics:
     amend step 2 from "If none, this is a first write — go to step
     4." to:
     ```
     2. It walks the journal backward to find the latest `PageWrite`
        event whose `path` matches. Four sub-cases follow:
        - None found, file absent on disk → first write; go to step 4.
        - None found, file present, bytes already match `content` →
          adopt the file as the kit's baseline; append `PageWrite`,
          skip the disk write, return `WriteResult.WRITTEN`.
        - None found, file present, bytes differ → treat as drift;
          go to step 5.
        - Found, on-disk hash matches the journaled hash → no drift;
          go to step 4.
        - Found, on-disk hash differs → drift; go to step 5.
     ```
     Reorder steps 4 and 5 internally so the `PageWrite` /
     `PageProposal` append happens *before* the disk write line.
     Steps 1, 3, and 6 are unchanged.
   - ADR-0004 §Negative: append two bullets — (i) "The
     noisy-existing-folder case extends to every CLI command, not
     just `wiki init`. Mitigated by the per-file adopt fast-path
     (byte-identical existing files journal without surfacing as
     proposals); residual case (differing bytes) routes through the
     standard proposal flow." (ii) "`.obsidianignore` is a
     documented non-journaled bypass. The file is small, additive,
     and user-editable; journaling it would register every user
     edit as `page-drift` in `wiki doctor`. See
     `docs/specs/safe-write-ordering/spec.md` §Non-goals."
   - ADR-0004 §Revisions: append a new dated entry
     `**YYYY-MM-DD** — Step 2 amended for safe-write-ordering spec.
     Page-level safe_write no longer silently overwrites unjournaled
     existing files; byte-identical content adopts via the
     fast-path, differing bytes route through `.proposed`. `_ensure_obsidianignore`
     promoted from quiet bypass to documented bypass. See
     `docs/specs/safe-write-ordering/spec.md`.` Use the date the
     §Revisions entry is committed (matches the precedent set by
     the existing 2026-05-15 / 2026-05-16 entries, which used
     commit date rather than PR merge date).

   *Done when:*
   - `grep -rn "writes the file \*after\* validating" docs/adr/`
     returns no hits (the §Negative sentence in ADR-0002 is gone).
   - `grep -rn "If none, this is a first write" docs/adr/` returns
     no hits (ADR-0004 §Mechanics step 2 is rewritten).
   - `grep -rn "safe-write-ordering" docs/adr/` returns at least two
     hits (one in ADR-0002, one+ in ADR-0004).
   - The spec's §Contracts with other modules names exactly the
     edits this step lands; no extra edits sneak in.

1. **Knowledge entry captures the load-bearing invariant.**

   *Depends on:* step 4 (the entry cites the implemented contract;
   appending it before step 4 lands would point at unmerged ADR
   text).

   *Verification mode:* goal-based (file parses; linter passes).

   *Approach:*

   - Append one entry to `docs/knowledge/patterns.jsonl`. The
     canonical schema (per `tools/lint-knowledge.sh` and the K-0001
     through K-0007 entries already in the file) is
     `{id, kind, scope, title, body, source, created, updated}`.
     The next free id at branch-cut time is `K-0008` (the existing
     range is K-0001 through K-0007). Re-check with
     `grep -c '^{' docs/knowledge/patterns.jsonl` immediately before
     committing in case another PR landed an entry first; bump to
     the next free integer if so.
     The body should be readable standalone (a future reader hits
     K-0008 first because they're scoped to `write_helper.py`, and
     should not need to chase K-0002 to understand it). State the
     invariant; name the two exceptions inline with one-line
     rationale; flag the gotcha ("don't add a third bypass
     casually"); then cite K-0002 as the parent entry. Example:
     ```json
     {"id": "K-0008", "kind": "pattern", "scope": "llm_wiki_kit/write_helper.py", "title": "Event-before-disk ordering, with two documented bypasses", "body": "Every kit-to-vault write in write_helper.py appends its journal event before opening the target for write — so a crash between the two leaves a recoverable state (wiki doctor's missing / page-drift / managed-region-drift checks reconcile the gap; no rollback). Two documented bypasses: (1) resolve_proposal still appends events before the disk write but bypasses the drift check, because conflict resolution is the user-mediated acknowledgement that overwrite is intended; (2) _ensure_obsidianignore writes .obsidianignore without journaling, because journaling would register every user edit as page-drift in wiki doctor (the file is user-editable by design) — see OBSIDIANIGNORE_BYPASS_DOC. Gotcha: adding a third bypass requires amending docs/specs/safe-write-ordering/spec.md; the journaling story is load-bearing. Parent entry: K-0002 (forbids bypasses categorically); this entry refines K-0002 with the two sanctioned exceptions.", "source": "docs/specs/safe-write-ordering/spec.md", "created": "YYYY-MM-DD", "updated": "YYYY-MM-DD"}
     ```
     Use the commit date for both `created` and `updated`.

   *Done when:*
   - `python -c "import json; [json.loads(l) for l in open('docs/knowledge/patterns.jsonl')]"`
     parses every line.
   - `bash tools/lint-knowledge.sh` passes (the artifact linters run
     in CI; this is the local mirror).

1. **Spec status flips; issue checkboxes tick.**

   *Depends on:* steps 1–5 (status flip is the cap of the chain).

   *Verification mode:* goal-based.

   *Approach:*

   - Spec frontmatter `Status: Draft` → `Implemented`.
   - Plan frontmatter `Status: Drafting` → `Done`.
   - Issue [#23](https://github.com/eugenelim/llm-wiki-kit/issues/23)
     bodies for qC3, qC6, C2 ticked in the PR description (the issue
     itself updates when the PR merges).

   *Done when:*
   - Spec / plan frontmatter as above.
   - Full suite: `pytest -q`, `ruff check llm_wiki_kit tests`,
     `ruff format --check llm_wiki_kit tests`, `mypy llm_wiki_kit tests`.

## Verification gate

The whole plan succeeds when:

```
pytest tests/unit/test_write_helper.py tests/unit/test_doctor.py tests/integration/test_wiki_doctor.py tests/integration/test_wiki_add.py
pytest -q                              # full suite, no regressions
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
bash tools/hooks/pre-pr.sh             # CONVENTIONS §Enforcement: runs all artifact linters + gates
grep -rn "writes the file \*after\* validating" docs/adr/  # no hits
grep -rn "If none, this is a first write" docs/adr/        # no hits
grep -rn "safe-write-ordering" docs/adr/                   # ≥2 hits
```

And the spec's acceptance-criteria checkboxes are all ticked.

## Risks

- **Inverting the qC6 pin test breaks one or more integration tests
  that implicitly rely on the silent-overwrite semantics.** The
  at-risk set in `tests/integration/`: `test_wiki_add.py`,
  `test_wiki_init.py`, `test_wiki_init_primitives.py`,
  `test_personal_recipe.py`, `test_family_recipe.py`,
  `test_work_os_recipe.py`, and `test_wiki_doctor.py` — anything
  that pre-seeds vault files before driving a CLI handler. (Note:
  there is no `tests/integration/test_install.py`; install-pipeline
  coverage is split across the recipe-driven integration tests.)
  *Recovery:* triage each failure; either the test was encoding the
  old wrong contract (delete or invert it) or the fixture needs to
  journal its setup writes via `safe_write` itself so the test sees
  a clean baseline. No silent test deletions — every removed test
  gets a one-line note in the PR description.
- **Event-before-disk introduces a new "event without file" crash
  window.** Today's window is "file without event" (qC6's silent
  overwrite). The trade is recoverable-on-doctor instead of
  silently-lost. *Recovery:* the spec's
  `test_doctor_surfaces_orphan_*` family pins the contract; the user-facing recovery is "re-run the
  kit operation, doctor's `missing` / `*-drift` issue clears." Worth
  one line in the user-facing release notes once those exist.
- **Adopt fast-path race between read and locked append.** A
  user edit landing between `safe_write`'s top-of-function
  `read_bytes` and the `append_event` call would let the kit
  journal a hash that's no longer on disk — kit-attributed phantom
  state. *Recovery:* spec §Behavior "Adopt fast-path" step 2
  prescribes a *re-read* of `abs_path` immediately before
  `append_event`; if the re-read hash differs from `new_hash`, the
  fast-path is abandoned and the call falls through to the drift
  predicate. The residual window is bounded by `append_event`'s
  internal `flock(LOCK_EX)` acquisition (one syscall) — too small
  for normal editors to land inside. The residual race is
  documented in spec §Behavior. If a real user reports an observed
  "adopted then drifted" sequence, the recovery is the doctor's
  `page-drift` issue on the next pass plus a `wiki-conflict`
  resolution; no kit-side state corruption.
- **Contract test families serve different purposes; reviewer
  may expect both to flip red→green.** The recovery-path family
  (step 1's family (b)) pre-seeds the journal manually and asserts
  the doctor reports the gap — this passes both before and after
  the reorder (the doctor's `check_missing` / `check_*_drift` are
  unchanged). The behavioral ordering family (step 1's family (a))
  monkeypatches `Path.write_bytes` to raise and asserts the event
  is in the journal — this is RED against today's
  write-then-append code and GREEN after the reorder, behaviorally
  observing the qC3 contract without resorting to mock-shape
  call-sequence snapshots. *Recovery:* none needed — the split is
  intentional. Document it in the PR description so a reviewer
  doesn't assume the recovery-path tests are the qC3 pin.
- **The `_ensure_obsidianignore` docstring pin
  (`test_ensure_obsidianignore_docstring_references_bypass_constant`)
  is intentionally brittle, but paired with the
  `test_obsidianignore_bypass_doc_constant_points_at_this_spec`
  constant-value test.** *Recovery:* the constant is the
  load-bearing anchor; the docstring's reference to it is the
  observable that catches paraphrase regressions. A maintainer who
  legitimately renames the constant updates both tests together;
  a maintainer who paraphrases away from the constant fails the
  docstring test. The invariant ("the file points at the
  authority for the bypass via an addressable constant") is what
  survives.

## Out of scope

- **`wiki init --adopt`.** The page-level adopt fast-path covers the
  byte-identical case; vault-wide adoption (which would journal every
  pre-existing file as a `PageWriteEvent` instead of surfacing each
  as a proposal) remains an unresolved RFC-0001 question. The spec
  deliberately does not block it; landing `--adopt` later is purely
  additive against this contract.
- **C10, C7, and other Concerns from issue #23.** Each lands in its
  own PR per the drain plan. This spec is qC3 + qC6 + C2 only.
- **Region-level qC6 inversion.** Spec §Non-goals "Why qC6 is
  page-scoped" names the install-pipeline regression that a region-
  scoped inversion would cause. If future ADR-0003 changes shift the
  managed-region ownership model, revisit.
- **A `KitInfraWriteEvent` discriminator.** Spec §Non-goals rejects
  journaling `.obsidianignore` outright; the discriminator question
  is moot.
- **Rollback or two-phase commit semantics.** Event-before-disk plus
  doctor reconciliation is the recovery model (consistent with
  ADR-0002).
- **Routing `.obsidianignore` through `safe_write` proper.** Worse
  UX (self-reference bootstrap problem); spec §Non-goals documents
  the rejection.
