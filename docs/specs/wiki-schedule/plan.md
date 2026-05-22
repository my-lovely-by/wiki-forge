# Plan: wiki-schedule

> **Implementation plan paired with `spec.md`.** The spec says *what*;
> the plan says *how, in what order, with what verification*.

- **Status:** Drafting
- **Spec:** [`docs/specs/wiki-schedule/spec.md`](spec.md)
- **Owner:** maintainer

## Approach

Eight PRs land the `wiki schedule` verb top-down: the new `write_helper`
helper that paves over the non-vault-path constraint, the journal-event
vocabulary, the platform-agnostic module skeleton, then the three per-OS
emitters in dependency order (launchd → systemd → Task Scheduler), then
`wiki doctor` integration. Each PR is one Claude Code session.
Post-v2.0 commits use the
[conventional-commits format from `docs/CONVENTIONS.md` § Commit messages](../../CONVENTIONS.md#commit-messages)
(`feat(schedule):`, `fix(schedule):`, etc.).

Why this order: every per-OS emitter consumes the DSL parser, the
module API, and the new `write_helper` exemption, so those land first;
macOS is the only end-to-end testable target, so it sets the
integration-test shape that systemd and Windows inherit at the
file-emission layer.

The companion spec [`wiki-run-exec`](../wiki-run-exec/spec.md) lands
in parallel — it has no module-level dependency on this work (the
`exec_command` string this plan writes into artifacts is just text;
the OS invokes it independently of how it was authored).

## Pre-conditions

- [RFC-0003](../../rfc/0003-scheduling-and-autonomous-execution.md)
  Accepted — done.
- `journal.transaction()` available — done (`journal-locking` spec).
- Reviewer agreement on the `write_os_artifact()` exemption pattern
  (parallel to `_ensure_obsidianignore`). Step 1 is where this lands.

## Steps

1. **`write_helper.write_os_artifact()` exists and refuses in-vault paths.**
   - **Depends on:** none.
   - **Tests:** new `tests/unit/test_write_os_artifact.py`:
     - happy-path: writes a file under a `tmp_path` simulating
       `~/Library/LaunchAgents/`, content round-trips byte-for-byte;
     - post-condition replace: writing to an existing path produces
       a file whose final bytes match the new content and leaves no
       `.tmp*` siblings in the directory (atomicity-of-`os.replace`
       is a stdlib guarantee on POSIX and on Windows when the
       destination exists; the test asserts the observable
       post-condition, not a racy mid-write check);
     - in-vault refusal: `write_os_artifact(vault_root / "x.plist",
       …, vault_root=vault_root)` raises `WikiError` with a clear
       "route through safe_write instead" message;
     - permission failures bubble as `OSError` (the helper does not
       swallow them).
   - **Approach:** add `write_os_artifact(path: Path, content: str |
     bytes, *, vault_root: Path) -> None` to
     `llm_wiki_kit/write_helper.py`. Uses `tempfile.NamedTemporaryFile`
     in the artifact's parent directory + `os.replace()` for the
     atomic swap. Documented as the second blessed exemption from
     `safe_write` (the first is `_ensure_obsidianignore`), citing
     [`safe-write-ordering`](../safe-write-ordering/spec.md) §"Documented
     exceptions" — spec amendment in the same PR if a third exemption
     needs a new ADR.

2. **`ScheduleInstalledEvent` + `ScheduleUninstalledEvent` +
   `OperationContract.default_time` field round-trip.**
   - **Depends on:** none.
   - **Tests:** new `tests/unit/test_models_schedule_events.py`:
     - both event types round-trip through `model_validate_json` →
       `model_dump_json` (mirrors spec CT-14);
     - a literal pre-v3 journal line with no `schedule.*` events
       replays to the same `VaultState` as before;
     - every existing `templates/operations/*/contract.yaml` validates
       under the extended `OperationContract` (no behavior change);
     - `default_time` accepts `"07:00"`, rejects `"7:00"`, `"7"`,
       `"25:00"`.
   - **Approach:** extend `llm_wiki_kit/models.py` with the two new
     event classes and the new contract field; update the
     discriminated `Event` union.

3. **DSL parser + default-fill table.**
   - **Depends on:** Step 2.
   - **Tests:** new `tests/unit/test_schedule_dsl.py`:
     - happy-path parse for each of `daily`, `<DAY>`, `monthly`,
       `quarterly`;
     - day-of-week case-insensitive;
     - rejection of cron strings (canary inputs from spec CT-4);
     - rejection of seconds and other malformed forms;
     - `resolve_default()` matches the §Inputs table for each
       `period:` value — pinned against a single named constant
       `DEFAULT_TIME_BY_PERIOD`;
     - refusal on `period: on-demand` (mirrors spec CT-3);
     - refusal on absent/other periods.
   - **Approach:** new module `llm_wiki_kit/schedule/dsl.py`. Public
     surface: `parse(dsl: str) -> ResolvedCadence`,
     `resolve_default(contract: OperationContract) -> ResolvedCadence`,
     `to_systemd_oncalendar(cadence: ResolvedCadence) -> str`,
     `to_launchd_calendar_interval(cadence: ResolvedCadence) ->
     dict[str, int]`, `to_task_scheduler_trigger(cadence:
     ResolvedCadence) -> ET.Element`.

4. **macOS launchd emitter — file emission + activation.**
   - **Depends on:** Step 1, Step 3.
   - **Tests:** new `tests/unit/test_schedule_launchd.py`:
     - `render_plist()` golden-string assertions for each cadence
       kind (CT-2 derivable from these);
     - argv-block contents pin `wiki run --exec <op>` shape;
     - `inspect()` returns each of the four states given fixture
       `launchctl print` outputs (mocked via subprocess monkeypatch).
   - And an opt-in `@pytest.mark.slow`
     `tests/integration/test_schedule_launchd_macos.py` (gated on
     `platform.system() == "Darwin"`): installs a no-op plist that
     `echo`'s on fire, calls real `launchctl bootstrap`, verifies
     `inspect()` returns `loaded`, then `bootout`'s.
   - **Approach:** new module `llm_wiki_kit/schedule/launchd.py`
     implementing the `_Emitter` Protocol from `spec.md` §"Contracts
     with other modules". Plist rendering via stdlib `plistlib`.

5. **Module orchestration: `schedule.install`, `schedule.uninstall`,
   `schedule.list_schedules`.**
   - **Depends on:** Step 1, Step 2, Step 3, Step 4.
   - **Tests:**
     - `tests/unit/test_schedule_install.py` covering spec CT-1, CT-2,
       CT-3, CT-4, CT-5, CT-6, **CT-12** (activation failure leaves
       no install event — verified by injecting a stub `_Emitter`
       whose `activate()` raises), and CT-18 (exec_command
       resolution).
     - `tests/unit/test_schedule_uninstall.py` covering CT-7, CT-8,
       CT-9, and **CT-16** (foreign-machine uninstall journals event,
       skips activation).
     - `tests/unit/test_schedule_list.py` covering CT-10, CT-11,
       CT-13.
     - `tests/integration/test_cli_schedule.py` driving
       `python -m llm_wiki_kit schedule install …` against a real
       `tmp_path` vault, asserting on stdout + journal + artifact
       file.
   - **Approach:** new `llm_wiki_kit/schedule/__init__.py` wiring DSL
     + emitter + journal + state-replay together. Install sequence
     **write → activate → journal** per spec §"install happy path"
     step 8 (no rollback; activation failure unlinks the artifact and
     skips the journal append). systemd / Task Scheduler emitters
     present as `NotImplementedError` stubs.

6. **Linux systemd emitter — file emission only.**
   - **Depends on:** Step 5.
   - **Tests:** new `tests/unit/test_schedule_systemd.py`:
     - golden-string assertions for `.service` + `.timer` per cadence
       kind, including the OnCalendar string;
     - `inspect()` returns each state given fixture `systemctl --user
       is-enabled` outputs (mocked).
   - And an opt-in `@pytest.mark.slow` integration test (gated on
     `platform.system() == "Linux"` *and* `systemd-run --user`
     available) — no CI gate; documented in spec §"OS coverage".
   - **Approach:** new module `llm_wiki_kit/schedule/systemd.py`
     implementing the `_Emitter` Protocol. Renders `.service` +
     `.timer` as plain strings (systemd's INI dialect doesn't
     round-trip cleanly through stdlib `configparser`).

7. **Windows Task Scheduler emitter — file emission only.**
   - **Depends on:** Step 5.
   - **Tests:** new `tests/unit/test_schedule_taskscheduler.py`:
     - golden-XML assertions per cadence kind;
     - the XML round-trips through `xml.etree.ElementTree` parsing
       without diff;
     - `activate()` prints (does not invoke) the expected
       `schtasks /Create /XML` command.
   - **Approach:** new module
     `llm_wiki_kit/schedule/taskscheduler.py` implementing
     `_Emitter`. XML rendering via stdlib `ElementTree`.

8. **`wiki doctor` schedule section.**
   - **Depends on:** Step 5, Step 6, Step 7, **and** the
     `OperationExecFailedEvent` model from
     [`wiki-run-exec`](../wiki-run-exec/spec.md) — that model is
     defined in the sibling spec's plan, not in this one. Step 8's
     exec-failure-backlog test cannot run until that PR series
     lands the model in `models.py`. The two plans land in
     parallel (per §Approach); coordinate the merge order so this
     step is last.
   - **Tests:** new `tests/unit/test_doctor_schedules.py` covering:
     - spec CT-15 (drift surfaces as warning, exit 0, stdout
       carries operation name + fix command);
     - CT-17 (hostname rename produces the `--machine <old>` hint);
     - the three drift modes (`missing-file`, `disabled`, `unknown`);
     - exec-failure backlog filters on `reason in {"non-zero-exit",
       "timeout"}` per spec §"Doctor integration".
   - **Approach:** extend `llm_wiki_kit/doctor.py` with
     `_check_schedules(state, journal_path)`. Reuses each
     `_Emitter.inspect()` for OS-side liveness. Output formatting
     mirrors the existing doctor warning shape.

## Verification gate

Each PR runs the standard gate sequence per
[`AGENTS.md` § Commands you'll need](../../../AGENTS.md#commands-youll-need):

```
ruff check llm_wiki_kit tests
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
pytest -m 'not slow'
```

The final PR (Step 8) additionally runs `pytest -m slow` on macOS so
the launchd integration test executes. CI does not gate on
`pytest -m slow`; the maintainer runs it locally.

End-to-end verification (post Step 8):

- All 18 contract tests from `spec.md` pass.
- `wiki schedule install <op>` on a fresh family-recipe vault on
  macOS produces a working launchd plist that, when manually
  kicked via `launchctl kickstart`, invokes `wiki run --exec <op>`
  and writes the expected `OperationRunEvent`. (`--exec` itself
  ships from [`wiki-run-exec`](../wiki-run-exec/spec.md); this
  plan's verification stops at the `wiki run` invocation.)
- `wiki doctor` on the same vault reports the schedule as `ok`,
  flips to `drift:missing-file` after `rm <plist>`, and flips back
  to `ok` after `wiki schedule install <op>`.

## Risks

- **`write_os_artifact()` exemption is precedent-light.** Only one
  prior exemption (`_ensure_obsidianignore`) exists. Reviewer push-
  back would land in Step 1's PR; the spec absorbs the amendment in
  the same PR rather than backing out the design.
- **`socket.gethostname()` is non-stable across DHCP renews.**
  Mitigated by exposing `--machine` and surfacing rename-detection
  in `wiki doctor` (spec §"Edge cases / Hostname rename" + CT-17).
  Migration to stable machine-id (`/etc/machine-id`,
  `IOPlatformUUID`, `MachineGuid`) deferred to a future ADR.
- **systemd `OnCalendar=` syntax has corner cases.** Mitigated by
  running real `systemd-analyze calendar` against the rendered
  string in Step 6's golden tests when systemd is available.
- **launchd `bootstrap`/`bootout` semantics differ across macOS
  versions.** `bootstrap` (10.10+) covers the kit's Python ≥3.11
  floor. Spec is silent on the deprecated `launchctl load`
  fallback — intentional.
- **Vault path with non-ASCII / spaces.** Mitigated by computing
  `<vault-id>` from the path's SHA-256 — the artifact label is
  always ASCII-safe.
- **Race between two `wiki schedule install` calls.** Mitigated by
  `journal.transaction()`'s flock. Cross-vault races out of scope
  (different locks).

## Out of scope

- Vault-side `wiki-schedule` SKILL.md (RFC-0003 §"Migration path"
  task 8). Ships in a follow-up after this plan completes.
- `wiki schedule edit`. Pinned in spec §"Non-goals".
- `wiki doctor --fix` re-materialising schedules. Future doctor-fix
  RFC.
- End-to-end CI for systemd / Windows. RFC-0003 §"OS coverage".
- Cron-string DSL acceptance. RFC-0003 §"Cadence vocabulary".
- Migration to stable machine-id. Future ADR.
