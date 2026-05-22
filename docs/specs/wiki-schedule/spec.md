# Spec: wiki-schedule

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Draft
- **Owner:** `llm_wiki_kit/schedule/`, `llm_wiki_kit/cli.py:_cmd_schedule_*`
- **Related:** [RFC-0003](../../rfc/0003-scheduling-and-autonomous-execution.md),
  [`docs/specs/wiki-run-exec/spec.md`](../wiki-run-exec/spec.md),
  [`docs/specs/wiki-schedule/plan.md`](plan.md)
- **Constrained by:** ADR-0002 (journal as state truth), ADR-0004
  (safe-write), ADR-0005 (Pydantic v2),
  [RFC-0003](../../rfc/0003-scheduling-and-autonomous-execution.md)
  §"Decisions already made" (shim-not-SDK, library-not-application,
  identity deferred), [`AGENTS.md` §"Runtime dependencies"](../../../AGENTS.md#runtime-dependencies)
  (no new runtime dep without an ADR).

## What this is

`wiki schedule {install, uninstall, list}` is the CLI verb that makes an
operation primitive's `period:` declaration *fire*. The kit's job is to
record one `ScheduleInstalledEvent` per (operation, machine) pair in the
journal, materialise an OS-native artifact (launchd plist on macOS,
systemd `.service` + `.timer` on Linux, Task Scheduler XML on Windows)
under the user's home, and surface drift between the two in `wiki
doctor`. The kit ships no scheduler daemon and runs no long-lived
process; the OS fires the scheduled command, which is `wiki run --exec
<operation>` (specified separately in [`wiki-run-exec`](../wiki-run-exec/spec.md)).

`wiki schedule` is the **schedule-administration boundary**, structurally
parallel to `wiki add`: journal the intent, write the OS-side projection,
exit. The act of *executing* the operation is `wiki-run-exec`'s job,
not this spec's.

## Inputs

CLI invocations:

```
wiki schedule install <operation> [--at "<dsl>"] [--machine <name>]
wiki schedule uninstall <operation> [--machine <name>]
wiki schedule list [--machine <name>] [--all-machines]
```

- `<operation>` — a kebab-case operation primitive name. Must be present
  in `replay_state(events).installed_primitives` and resolve to an
  operation-kind primitive (same gate `wiki run` uses).
- `--at "<dsl>"` — optional cadence override. Grammar:
  - `daily <HH:MM>` — every day at HH:MM local time.
  - `<DAY> <HH:MM>` — every week on `<DAY>` (SUN/MON/.../SAT,
    case-insensitive) at HH:MM. The day token is required for weekly
    cadences.
  - `monthly <DD> <HH:MM>` — every month on day `<DD>` (1–28, no
    end-of-month surprises) at HH:MM.
  - `quarterly <DD> <HH:MM>` — every quarter on the `<DD>`-th day of
    Jan/Apr/Jul/Oct at HH:MM (1–28).
  Anything else → `WikiError("--at: unrecognised cadence DSL; accepted
  forms: 'daily HH:MM', '<DAY> HH:MM', 'monthly <DD> HH:MM',
  'quarterly <DD> HH:MM'")`. **Cron strings are not accepted** (RFC-0003
  §"Cadence vocabulary").
- `--machine <name>` — defaults to `socket.gethostname()`. Explicit
  override exists so the same vault on a synced laptop + Mac Mini can
  carry two distinct schedule entries without collision.
- `--all-machines` (list only) — show schedules for every machine the
  journal has seen, not just the current host.
- Vault root: `Path.cwd()`. Must contain `.wiki.journal/journal.jsonl`.

Default cadence (when `--at` is omitted) comes from the operation
contract's `period:` (and a new optional `default_time:` field on the
contract — see §"Contracts with other modules"). The resolution table
mirrors RFC-0003 §"Cadence vocabulary":

| `period:`     | Default DSL when `--at` is omitted |
|---------------|------------------------------------|
| `daily`       | `daily <default_time or 07:00>`    |
| `weekly`      | `SUN <default_time or 09:00>`      |
| `monthly`     | `monthly 1 <default_time or 09:00>`|
| `quarterly`   | `quarterly 1 <default_time or 09:00>` |
| `on-demand`   | refuse — never schedulable         |
| absent / other | refuse — operation declares no cadence |

## Outputs

### `install`

- **Journal append** — exactly one `ScheduleInstalledEvent`:

  ```
  type: "schedule.installed"
  timestamp: <UTC now>
  by: "wiki-schedule"
  operation: "<operation>"
  machine_id: "<hostname>"
  cadence_dsl: "<resolved DSL>"
  os_artifact_path: "<absolute path to plist/timer/xml>"
  exec_command: ["<absolute path to wiki binary>", "run", "--exec", "<operation>"]
  ```

  Appended **last**, after the OS-artifact file is written *and* the
  OS-side activation subprocess returns success. The journal is
  append-only ([ADR-0002](../../adr/0002-journal-as-state-truth.md));
  there is no rollback. Pre-journal failures leave no event; the rare
  post-activation-pre-journal failure window is detected by
  `wiki doctor` as "loaded artifact, no journal entry" (see
  §"Edge cases / Stale schedule"). See §Invariants for the full
  durability rule.

- **OS-side artifact** — one file written via a new helper
  `write_helper.write_os_artifact()` that is the kit-blessed exemption
  from `safe_write`'s in-vault constraint (`_relative_to_vault` rejects
  paths outside `vault_root`; see `llm_wiki_kit/write_helper.py:454`).
  Precedent: `write_helper._ensure_obsidianignore` is the same shape of
  exemption ([`safe-write-ordering`](../safe-write-ordering/spec.md)
  §"Documented exceptions"). The helper:
  - Writes the file atomically (`os.replace` from a same-directory
    tempfile) — no drift detection, no proposal sidecars (the artifact
    is wholly kit-owned and lives outside the vault, so user edits are
    not the kit's concern).
  - Refuses to write under any path that *is* inside the vault — that
    would route around `safe_write`.
  Artifact paths:
  - macOS: `~/Library/LaunchAgents/com.llm-wiki-kit.<vault-id>.<operation>.plist`
  - Linux: `~/.config/systemd/user/llm-wiki-kit-<vault-id>-<operation>.service`
    and `…@.timer` (two files).
  - Windows: `%LOCALAPPDATA%/llm-wiki-kit/schedules/<vault-id>-<operation>.xml`
    (Task Scheduler XML; activation via `schtasks /Create /XML` is the
    user's last step until Windows end-to-end ships — see RFC-0003
    §"OS coverage").
  The `<vault-id>` is the first 12 hex chars of the SHA-256 of the
  absolute vault path. The same vault on the same machine produces
  the same id deterministically; two vaults never collide.

- **OS-side activation** (macOS, Linux):
  - macOS: `launchctl bootstrap gui/<uid> <plist>` (replaces deprecated
    `load`).
  - Linux: `systemctl --user daemon-reload && systemctl --user enable
    --now <timer>`.
  - Windows: print the `schtasks /Create /XML "<path>" /TN
    "llm-wiki-kit-<…>"` command for the user to run; the kit does not
    invoke `schtasks` itself in v1. Documented under §"Non-goals".
  Activation failure semantics: the kit attempts a best-effort
  cleanup (`unlink` the artifact file written in the previous step),
  raises `WikiError`, and **does not journal** the install event.
  The journal stays clean. Because the artifact write precedes
  activation, a *cleanup-after-activation-failure* hop that itself
  fails (file write succeeded, activation failed, unlink failed)
  leaves a stale artifact on disk; the next `wiki doctor` flags
  it as "stale artifact / no journal entry" (§"Stale schedule").

  **Windows v1 special case.** `taskscheduler.activate()` returns
  `None` without spawning a subprocess on Windows v1 — the
  orchestrator's `write → activate → journal` ordering stays uniform
  across OSes, but `activate()`'s implementation is a no-op (plus a
  printed `schtasks /Create /XML` instruction for the user to run
  by hand). The kit therefore journals the install event right
  after the write succeeds — the no-op `activate()` cannot fail,
  so CT-12's "activation failure leaves no event" path is not
  reachable on Windows at v1. The
  user-running-`schtasks` step is **out of band**; the kit cannot
  detect whether the user actually activated the task. The trade-off
  is documented at install time in the stdout summary
  (`activation: run 'schtasks /Create /XML ...' to enable`). The
  same special case applies symmetrically to uninstall on Windows
  (artifact deleted → journal event appended → `schtasks /Delete`
  printed).

- **stdout** — one summary block:
  ```
  Installed schedule for <operation> on <machine>.
    cadence: <resolved DSL>
    artifact: <absolute path>
    next run: <ISO local timestamp>
  ```
  (`next run` is computed from the DSL; documented as advisory, not
  promised to match the OS scheduler exactly — DST transitions, system
  sleep, etc.)

### `uninstall`

- **Journal append** — exactly one `ScheduleUninstalledEvent`:

  ```
  type: "schedule.uninstalled"
  timestamp: <UTC now>
  by: "wiki-schedule"
  operation: "<operation>"
  machine_id: "<hostname>"
  removed_artifact: <true|false>
  ```

  `removed_artifact: true` when the kit successfully deleted the
  OS-side file; `false` when the file was already missing (drift case,
  proceed anyway). Both are appended; both exit `0`.

- **OS-side deactivation**:
  - macOS: `launchctl bootout gui/<uid> <plist>` before deleting.
  - Linux: `systemctl --user disable --now <timer>` before deleting.
  - Windows: print the `schtasks /Delete` command; don't invoke.

- **stdout** — `Uninstalled schedule for <operation> on <machine>.`

### `list`

- **No journal write** (read-only).
- **stdout** — tab-separated rows, header line first:

  Default invocation (current host only):
  ```
  OPERATION    MACHINE      CADENCE                ARTIFACT       STATUS
  weekly-digest tower.local  SUN 09:00              ~/Library/...  ok
  meal-planning tower.local  daily 06:30            ~/Library/...  drift:missing-file
  ```

  With `--all-machines`, foreign-host rows are appended with
  `STATUS=unknown`:
  ```
  OPERATION    MACHINE      CADENCE                ARTIFACT       STATUS
  weekly-digest tower.local  SUN 09:00              ~/Library/...  ok
  meal-planning tower.local  daily 06:30            ~/Library/...  drift:missing-file
  follow-up    laptop.local  SUN 09:00              ~/Library/LaunchAgents/... unknown
  ```

  `STATUS` values: `ok` (event + artifact agree), `drift:missing-file`
  (event present, file absent), `drift:disabled` (file present but
  OS reports the schedule disabled — macOS/Linux only), `unknown`
  (schedule lives on a different machine; current host can't
  inspect). The ARTIFACT column always prints the journaled
  `os_artifact_path` verbatim, including for `unknown` rows — the
  user needs that path to SSH and remove it manually.

## Behavior

### `install` happy path

1. Parse argv. Resolve `<operation>`, `--at`, `--machine`.
2. Resolve vault root + journal path. Raise `WikiError` on a non-vault
   directory (standard "not a wiki vault" message).
3. Replay journal → `VaultState`. Verify `<operation>` is installed
   *and* of kind `operation` (same gate `wiki run` uses; reuse
   `run._resolve_operation_kind` after extracting it to
   `llm_wiki_kit/operations.py` — see §Constraints / construction plan).
4. Load the operation contract (`templates/operations/<operation>/contract.yaml`).
   Refuse if `contract.period in {None, "on-demand"}` — operation
   declared no cadence.
5. Compute the effective cadence DSL: `--at` if supplied, otherwise
   the table in §Inputs. Validate against the DSL grammar; raise
   `WikiError` on malformed `--at`.
6. Resolve `<machine>`: `--machine` if supplied, else
   `socket.gethostname()`.
7. Check for a prior unrevoked `ScheduleInstalledEvent` with the same
   `(operation, machine_id)`. If present:
   - If `cadence_dsl` matches the new one → no-op; print
     `Schedule already installed for <op> on <machine> (no change).`
     Exit `0`, **no journal event**.
   - If `cadence_dsl` differs → refuse with `WikiError("schedule
     already installed for <op> on <machine> with cadence '<old>';
     uninstall first or pass --at to change")`. Idempotent re-install
     is supported; silent reconfiguration is not.
8. Compute artifact path; render the artifact body (per-OS template;
   see §"Contracts with other modules"). The install sequence is
   **write → activate → journal**, executed under
   `journal.transaction(by="wiki-schedule", reason="install <op>")`
   which holds the journal flock for the duration so two concurrent
   installs serialise:
   - **Write** the artifact via `write_os_artifact()` (atomic
     `os.replace`). Failure → raise `WikiError`, no event journaled.
   - **Activate** via the OS subprocess (`launchctl bootstrap`,
     `systemctl --user enable --now`). Failure → best-effort
     `unlink(artifact_path)`, raise `WikiError`, no event journaled.
   - **Journal** `ScheduleInstalledEvent` last via `append_event`.
     Failure here is the rare post-activation-pre-journal window;
     `wiki doctor` reports the stale OS-side state on the next run.
   The transaction's `LockAcquiredEvent` / `LockReleasedEvent` pair
   is still emitted (journal-locking spec): a clean install produces
   `lock.acquired → schedule.installed → lock.released`; a failed
   install produces `lock.acquired → lock.released` with no
   `schedule.installed` in between, which `journal grep` can
   distinguish by the absence.
9. Compute and print the `next run` advisory.

### `uninstall` happy path

1. Parse argv. Resolve `<operation>`, `--machine`.
2. Same vault / journal-path checks.
3. Locate the most recent `ScheduleInstalledEvent` for
   `(operation, machine_id)`. If none (or if a later
   `ScheduleUninstalledEvent` already exists for the same pair), raise
   `WikiError("no schedule installed for <op> on <machine>")`. No
   journal write.
4. Determine `machine_id == socket.gethostname()` (current host) vs.
   foreign-machine uninstall:
   - **Current host**: under
     `journal.transaction(by="wiki-schedule", reason="uninstall <op>")`,
     run **deactivate → delete → journal**:
     - Run OS deactivation (`launchctl bootout` / `systemctl --user
       disable --now`). Non-zero exit logged but does not abort —
       stale OS state is recoverable from `wiki doctor`; the journal
       must still record the user's intent.
     - Delete the OS artifact file if present. Record whether the
       file existed (`removed_artifact: bool`).
     - Append `ScheduleUninstalledEvent` with the recorded
       `removed_artifact`.
   - **Foreign machine** (`--machine` differs from
     `socket.gethostname()`): no OS access possible. Append
     `ScheduleUninstalledEvent(removed_artifact=False)` and print a
     warning to stderr: `note: schedule was installed on <machine>;
     remove the artifact at <path> manually on that host`. Exit `0`.
5. Print the success line and exit `0`.

### `list` happy path

1. Replay journal → list of `(operation, machine_id)` pairs with the
   most recent `ScheduleInstalledEvent` (and no later
   `ScheduleUninstalledEvent`).
2. For each, compare against OS-side reality (only for entries whose
   `machine_id == socket.gethostname()`).
3. Render the tab-separated table.

### Edge cases

- **Operation has no SKILL** — install proceeds; `wiki run --exec`
  will fail at exec time with a clear message. The schedule is still
  worth installing (operations are added/removed independently of
  their SKILLs in some vaults).
- **`--at` includes seconds (`SUN 09:00:30`)** — refuse with the
  grammar error; minute precision is the contract.
- **Cron-string in `--at` (`0 9 * * 0`)** — refuse with the grammar
  error; suggest the equivalent DSL form in the message. Pinned by
  RFC-0003 §"Cadence vocabulary".
- **Machine name collision via `--machine`** — the kit doesn't
  enforce that `--machine your-hostname` actually matches the host
  it's running on. The user assumes the consequences (an artifact
  written under the wrong name on this machine; `wiki doctor` flags
  the drift).
- **Repeat install on the same `(operation, machine)` with the same
  cadence** — idempotent no-op. Re-running the install is a common
  recovery path after a manual `rm` of the OS artifact; the kit
  detects the missing file and rewrites it, journaling a *new*
  `ScheduleInstalledEvent` only if the previous one had been
  uninstalled. Spec'd this way so a `wiki doctor --fix` (future) can
  re-materialise schedules from the journal without producing event
  noise.
- **Stale schedule (artifact present but no journal entry)** — `list`
  reports `unknown` for the artifact's `(operation, machine)`;
  `uninstall` refuses with the "no schedule installed" message
  rather than silently removing the file. The user must
  `rm <artifact>` manually or write a `ScheduleInstalledEvent` by
  hand (out of scope; `wiki doctor` flags it).
- **Running on an unsupported OS** (anything not in `{Darwin, Linux,
  Windows}`) — refuse install with `WikiError("scheduling is not
  supported on <platform>; see RFC-0003 §'OS coverage'")`.
  `list` works on any OS (read-only); `uninstall` works (file delete
  + journal append). Install is the only OS-gated verb.
- **Hostname rename / DHCP flap.** `machine_id` is captured at install
  time as `socket.gethostname()`. If the host's hostname later
  changes (laptop rename, mDNS flap, container restart), the journaled
  `machine_id` no longer equals the current `socket.gethostname()`.
  `wiki schedule list` shows the schedule as `STATUS=unknown` (the
  current host can't introspect it). `wiki doctor` surfaces the
  apparent mismatch with a one-liner: `current hostname '<new>',
  journaled schedules for '<old>'; pass '--machine <old>' to operate
  on them, or uninstall+reinstall to migrate`. The kit does **not**
  auto-migrate — silent rebinding would corrupt the
  per-machine source of truth.

### Error cases

- Argv-shape errors (unknown operation, kind mismatch, missing
  contract, malformed `--at`) → `WikiError` at the existing CLI
  boundary; no journal event.
- OS-side activation failures (`launchctl bootstrap` returns non-zero,
  `systemctl --user enable` fails) → `WikiError`; `journal.transaction()`
  unwinds the in-flight event.
- File-write failures (permission denied on `~/Library/LaunchAgents/`,
  etc.) → propagate as `OSError`; `journal.transaction()` unwinds.

## Invariants

- One `install` invocation appends **at most one**
  `ScheduleInstalledEvent`. Zero events on the no-op idempotent
  re-install path; exactly one on every other surviving path. Pre-load
  failures (vault check, unknown operation, kind mismatch, refused
  cadence, refused `--at`) abort before any journal write.
- One `uninstall` invocation appends **at most one**
  `ScheduleUninstalledEvent`. Zero events on the "nothing to uninstall"
  refusal; exactly one on every surviving path.
- The journal append is **paired** with the OS-side write inside a
  single `journal.transaction()`. Either both happen or neither does.
  The transaction is the lock-bracket pair specified by the
  [journal-locking spec](../journal-locking/spec.md).
- `ScheduleInstalledEvent.exec_command` is stored as `list[str]` —
  the argv the OS-side artifact runs. `[0]` is the absolute path to
  the kit's `wiki` binary, resolved in this order:
  1. `shutil.which("wiki")` if it returns a non-`None` path.
  2. `sys.argv[0]` resolved to an absolute path via
     `Path(sys.argv[0]).resolve()`.
  3. If neither yields a usable executable, raise `WikiError("cannot
     resolve 'wiki' binary path; install via pipx or pass --wiki-binary")`
     (the `--wiki-binary` override is reserved future surface; v1
     can refuse).
  Rationale: `pipx install`, virtualenv, and `python -m llm_wiki_kit`
  produce three different `sys.argv[0]` values; `shutil.which("wiki")`
  is what the OS scheduler will resolve at fire time anyway, so the
  journaled command matches reality. The OS-side artifact embeds this
  verbatim so a same-style re-install (pipx → pipx) doesn't have to
  rewrite every plist.
- `ScheduleInstalledEvent.machine_id` is what the kit uses to attribute
  schedules across multiple machines sharing one vault. Hostname-based
  is good-enough for v1; a future ADR could move to a stable
  machine-id read from `/etc/machine-id` or equivalent.
- `list` writes no events. `uninstall` writes one event and possibly
  deletes one file. `install` writes one event and writes one file
  (or two on Linux).
- No filesystem writes outside the journal append, the OS-artifact
  path, and the optional OS-activation subprocess. No vault-side
  writes.
- `<vault-id>` is the first 12 hex chars (48 bits) of
  `SHA-256(str(vault_root.resolve()))`. Birthday-bound collision
  probability is ~1 in 16M between two vaults on the same host —
  acceptable for v1's one-user audience. The artifact-path-already-
  exists check in `write_os_artifact()` raises on collision; the
  user can pick a different vault path. Documented here rather than
  pinned in a CT because the collision case is operationally rare
  and easy to recover from.
- Default cadence resolution uses one named constant in
  `schedule/dsl.py`:
  ```python
  DEFAULT_TIME_BY_PERIOD: dict[str, str] = {
      "daily": "07:00",
      "weekly": "09:00",
      "monthly": "09:00",
      "quarterly": "09:00",
  }
  ```
  `resolve_default(contract)` reads `contract.default_time` if set
  and non-`None`, otherwise falls back to
  `DEFAULT_TIME_BY_PERIOD[contract.period]`. The §Inputs table is
  the source of truth; tests on `resolve_default()` pin the
  combination so the constant cannot drift silently.

## Contracts with other modules

- **`cli.py`** — three new handlers `_cmd_schedule_install`,
  `_cmd_schedule_uninstall`, `_cmd_schedule_list` thinly wrapping the
  module API. The verb `schedule` becomes a subparser group with
  three subcommands.
- **`llm_wiki_kit/schedule/__init__.py`** — new package. Public API:
  - `install(operation: str, *, at: str | None, machine: str | None,
    vault_root: Path, kit_root: Path, journal_path: Path,
    now: datetime) -> InstallResult`
  - `uninstall(operation: str, *, machine: str | None, …) -> UninstallResult`
  - `list_schedules(*, machine: str | None, all_machines: bool, …)
    -> list[ScheduleStatus]`
  Inner submodules: `schedule/dsl.py` (parser + default-fill table),
  `schedule/launchd.py`, `schedule/systemd.py`, `schedule/taskscheduler.py`,
  `schedule/emitter.py` (dispatch by `platform.system()`).
- **`_Emitter` protocol** (defined in `schedule/emitter.py`, implemented
  by `launchd.py`, `systemd.py`, `taskscheduler.py`):
  ```python
  class _Emitter(Protocol):
      def artifact_path(self, vault_id: str, operation: str) -> Path: ...
      def render_artifact(
          self,
          *,
          operation: str,
          vault_root: Path,
          vault_id: str,
          cadence: ResolvedCadence,
          exec_command: list[str],
      ) -> str | bytes: ...
      def activate(self, artifact_path: Path) -> None:
          """Raise WikiError on non-zero exit."""
      def deactivate(self, artifact_path: Path) -> None:
          """Best-effort; log non-zero exit but do not raise."""
      def inspect(self, artifact_path: Path) -> Literal[
          "loaded", "not-loaded", "missing-file", "not-inspectable"
      ]: ...
  ```
  `not-inspectable` covers Windows (no `inspect` at v1; file-presence
  is the only signal).
- **`llm_wiki_kit/models.py`** — additive per ADR-0002. Two new
  classes:
  ```python
  class ScheduleInstalledEvent(_EventBase):
      type: Literal["schedule.installed"] = "schedule.installed"
      operation: str
      machine_id: str
      cadence_dsl: str
      os_artifact_path: str
      exec_command: list[str]

  class ScheduleUninstalledEvent(_EventBase):
      type: Literal["schedule.uninstalled"] = "schedule.uninstalled"
      operation: str
      machine_id: str
      removed_artifact: bool
  ```
- **`llm_wiki_kit/models.py:OperationContract`** — additive field:
  `default_time: str | None = None`. Validates against `^([01]\d|2[0-3]):[0-5]\d$`.
  Documentation-only at v1 unless a contract starts using it; existing
  contracts revalidate unchanged (no field changes shape).
- **`llm_wiki_kit/journal.py`** — read by `list` (replay-state), written
  by `install`/`uninstall` via `append_event` inside `transaction()`.
- **`llm_wiki_kit/write_helper.py`** — gains one new helper,
  `write_os_artifact(path: Path, content: str | bytes, *,
  vault_root: Path) -> None`, the kit-blessed exemption from
  `safe_write`'s in-vault constraint.
  Pattern mirrors `_ensure_obsidianignore`'s exemption per
  [`safe-write-ordering`](../safe-write-ordering/spec.md). The helper
  refuses to write inside `vault_root` (routes back through
  `safe_write` for that case) and writes atomically via same-directory
  tempfile + `os.replace`. No proposal sidecars, no drift detection.
- **`llm_wiki_kit/doctor.py`** — gains three new checks (presence,
  liveness, exec-failure-backlog). Spec'd in §"Doctor integration"
  below.
- **`llm_wiki_kit.run`** — `_resolve_operation_kind` extracted to a
  shared helper so `schedule.install` can reuse the same installed-
  primitive + kind check without re-implementing it. Move is a pure
  refactor with no behavior change.

### Doctor integration

`wiki doctor` gains a new section, **Schedules**, after the existing
**Primitives** section:

- For each `ScheduleInstalledEvent` with no later
  `ScheduleUninstalledEvent` and `machine_id == socket.gethostname()`:
  - File present? If not → warning: `schedule for <op> missing
    artifact at <path>; reinstall with 'wiki schedule install <op>'`.
  - macOS: `launchctl print gui/<uid>/com.llm-wiki-kit.<vault-id>.<op>`
    succeeds? If not → warning: `schedule for <op> exists on disk but
    is not loaded; 'launchctl bootstrap gui/<uid> <path>'`.
  - Linux: `systemctl --user is-enabled <timer>` returns `enabled`?
    If not → warning.
  - Windows: skip liveness — file-presence only at v1.
- Count `OperationExecFailedEvent`s in the last 7 days where
  `reason in {"non-zero-exit", "timeout"}` (the two failure modes the
  user can actually act on — `"conflict-refused"` is already
  user-visible via the `.proposed` sidecar; `"binary-missing"` /
  `"skill-missing"` are reserved-but-not-emitted at v1 per
  [`wiki-run-exec`](../wiki-run-exec/spec.md) §"Contracts with other
  modules"). Non-zero → warning: `<N> recent scheduled-exec
  failures for <op>; see inbox/scheduled-failures/`. (The exec event
  shape lives in [`wiki-run-exec`](../wiki-run-exec/spec.md).)

All schedule findings are **warnings**, not failures — `wiki doctor`
already distinguishes the two (per existing `llm_wiki_kit/doctor.py`
convention), and a stale schedule shouldn't fail CI-style checks on
the vault. `wiki doctor` exits `0` when only schedule warnings are
present; warnings are surfaced on stdout, not stderr.

### Artifact templates

Format-only; no behavior. Pinned in this spec so reviewers can audit
without reading the implementation.

- **launchd plist**:
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0">
    <dict>
      <key>Label</key><string>com.llm-wiki-kit.<vault-id>.<op></string>
      <key>ProgramArguments</key>
      <array>
        <string><wiki-binary></string>
        <string>run</string>
        <string>--exec</string>
        <string><op></string>
      </array>
      <key>WorkingDirectory</key><string><vault-root></string>
      <key>StartCalendarInterval</key>
      <dict>
        <!-- one or more of Hour/Minute/Day/Weekday/Month per DSL -->
      </dict>
      <key>StandardOutPath</key><string><vault-root>/.wiki.journal/exec-logs/launchd-stdout.log</string>
      <key>StandardErrorPath</key><string><vault-root>/.wiki.journal/exec-logs/launchd-stderr.log</string>
      <key>RunAtLoad</key><false/>
    </dict>
  </plist>
  ```

- **systemd `.service`** (one-shot):
  ```ini
  [Unit]
  Description=llm-wiki-kit scheduled run: <op> in <vault-root>

  [Service]
  Type=oneshot
  WorkingDirectory=<vault-root>
  ExecStart=<wiki-binary> run --exec <op>
  ```

  **systemd `.timer`**:
  ```ini
  [Unit]
  Description=Timer for llm-wiki-kit <op> in <vault-root>

  [Timer]
  OnCalendar=<systemd OnCalendar from DSL>
  Persistent=true

  [Install]
  WantedBy=timers.target
  ```

- **Task Scheduler XML** — generated via Python `xml.etree.ElementTree`
  from a fixed template; structure follows the standard Task Scheduler
  schema for a daily/weekly/monthly trigger with one exec action.
  Exact body listed in `plan.md` step 6.

## Acceptance criteria

The contract tests below define "done". Construction tests live in
`plan.md`.

- [ ] **CT-1: install on the happy path (macOS).** Given an installed
  `weekly-digest` (period=weekly) and a clean vault, `wiki schedule
  install weekly-digest` (a) appends exactly one
  `ScheduleInstalledEvent` with `cadence_dsl=="SUN 09:00"`,
  `machine_id==socket.gethostname()`, and `os_artifact_path` ending
  `.plist`, (b) writes the plist file at the journaled path with the
  exec-command pointing at `wiki run --exec weekly-digest`, (c) prints
  the summary block, (d) exits `0`.
- [ ] **CT-2: install with `--at` override.** `wiki schedule install
  weekly-digest --at "TUE 18:00"` produces an event with
  `cadence_dsl=="TUE 18:00"` (canonical form, uppercase day), and the
  plist's `StartCalendarInterval` block has `Weekday=2 Hour=18
  Minute=0` (launchd encoding: Sunday=0, Monday=1, …, Saturday=6,
  per the `StartCalendarInterval.Weekday` convention).
- [ ] **CT-3: refuse on-demand period.** Given an operation with
  `period: on-demand`, `wiki schedule install <op>` raises `WikiError`
  matching `/declared no cadence|period=on-demand/`; no journal event.
- [ ] **CT-4: refuse cron strings.** `wiki schedule install
  weekly-digest --at "0 9 * * 0"` raises `WikiError` whose message
  contains `unrecognised cadence DSL`; no journal event.
- [ ] **CT-5: idempotent re-install on identical cadence.** Two
  back-to-back `wiki schedule install weekly-digest` calls produce
  **exactly one** `ScheduleInstalledEvent`; the second call exits `0`
  with the "already installed" message; the plist is unchanged.
- [ ] **CT-6: refuse re-install on different cadence.** `wiki schedule
  install weekly-digest` followed by `wiki schedule install
  weekly-digest --at "MON 09:00"` produces one event from the first
  call, raises `WikiError` containing `uninstall first` from the
  second, and the plist on disk is the first call's output.
- [ ] **CT-7: uninstall succeeds when both event and file exist.**
  After CT-1, `wiki schedule uninstall weekly-digest` (a) appends one
  `ScheduleUninstalledEvent` with `removed_artifact==True`, (b)
  removes the plist file, (c) exits `0`.
- [ ] **CT-8: uninstall succeeds when file is missing (drift).**
  After CT-1 and an out-of-band `rm <plist>`, `wiki schedule uninstall
  weekly-digest` appends one event with `removed_artifact==False`
  and exits `0`.
- [ ] **CT-9: uninstall refuses when no schedule exists.** On a clean
  vault, `wiki schedule uninstall weekly-digest` raises `WikiError`
  with the "no schedule installed" message; no journal event.
- [ ] **CT-10: list reflects journal + disk.** After CT-1, `wiki
  schedule list` prints one row with `STATUS=ok`; after CT-1 plus
  out-of-band `rm <plist>`, the same call prints `STATUS=drift:missing-file`.
- [ ] **CT-11: list on a non-vault directory.** Raises the standard
  `not a wiki vault` `WikiError`.
- [ ] **CT-12: activation failure leaves no install event.** A
  simulated activation failure (subprocess returning non-zero) causes
  (a) zero `ScheduleInstalledEvent`s in the journal, (b) the artifact
  file is unlink'd (best-effort — file does not exist on disk after
  the failed call), (c) a `LockAcquiredEvent` / `LockReleasedEvent`
  pair is present (the transaction still ran), (d) exit non-zero.
  This pins the install sequence's "write → activate → journal"
  ordering against the append-only durability rule (no rollback).
- [ ] **CT-13: machine_id propagation.** `wiki schedule install
  weekly-digest --machine other-box` writes an event with
  `machine_id=="other-box"` and the artifact path includes the
  `<vault-id>` (same digest as a default-host install on this box).
  `wiki schedule list` (no flag) does **not** show the `other-box`
  entry; `wiki schedule list --all-machines` does, with
  `STATUS=unknown`.
- [ ] **CT-14: additive event schema replays cleanly.** A literal
  pre-v3 journal that contains no `schedule.*` events replays under
  the extended Pydantic model with no errors and produces the same
  `VaultState` as before.
- [ ] **CT-15: doctor reports schedule drift as warning.** With a
  schedule installed and the plist removed out of band, `wiki doctor`
  exits `0` and stdout contains both the operation name and the
  suggested fix command (`wiki schedule install <op>`). Stderr is
  empty. Pins the warnings-not-failures convention from §"Doctor
  integration".

- [ ] **CT-16: uninstalling a foreign-machine schedule.** Given an
  installed schedule with `machine_id="other-box"` and
  `socket.gethostname() == "this-box"`, `wiki schedule uninstall
  weekly-digest --machine other-box` (a) appends one
  `ScheduleUninstalledEvent(removed_artifact=False)`, (b) attempts no
  OS-side deactivation, (c) prints the "remove the artifact manually"
  warning to stderr, (d) exits `0`. The artifact file at the journaled
  path is not touched on the local filesystem (it isn't there to
  touch).

- [ ] **CT-17: hostname rename surfaces in doctor.** With a schedule
  installed under `machine_id="old-name"` and the current host's
  `socket.gethostname()` now `"new-name"`, `wiki doctor` exits `0`
  and stdout contains both `old-name` and `new-name` plus the
  `--machine old-name` migration hint. No journal write.

- [ ] **CT-18: `exec_command` resolution prefers `shutil.which`.**
  With `wiki` on `PATH`, the journaled `exec_command` is a
  `list[str]` whose `[0]` equals the path `shutil.which("wiki")`
  returns (not `sys.argv[0]`, which may be a `python -m
  llm_wiki_kit` form in the test runner). Elements `[1:]` equal
  `["run", "--exec", <operation>]`. A follow-on assertion: invoking
  the kit via `python -m llm_wiki_kit schedule install …` still
  produces `exec_command[0]` pointing at the `wiki` binary, not at
  a `python -m …` form. Tests use a `tmp_path` PATH stub
  (`tmp_path / "bin" / "wiki"`) to make `shutil.which` deterministic.

## Non-goals

- **Executing the scheduled operation.** That's `wiki run --exec`'s
  job. See [`wiki-run-exec`](../wiki-run-exec/spec.md).
- **Cron-string acceptance.** Pinned by RFC-0003. A future RFC may add
  `--cron` as an escape hatch.
- **System-wide / multi-user schedules.** v1 is user-scope only
  (`~/Library/LaunchAgents/`, `systemctl --user`, current-user Task
  Scheduler). System-wide would require admin on all three OSes and
  break the "no privileged operations" posture.
- **Sub-minute precision.** The DSL stops at `HH:MM`. Operations that
  need every-minute firing are outside the use cases this kit
  serves.
- **Schedule editing in place.** Re-install on a different cadence is
  `uninstall` + `install`. Silent reconfiguration would mask user
  intent in the journal.
- **Auto-invoking `schtasks /Create /XML` on Windows in v1.**
  File-emission only; the user runs the activation command. The
  Windows end-to-end path is on the roadmap per RFC-0003 §"OS
  coverage".
- **`wiki doctor --fix` re-materialising schedules from the journal.**
  Out of scope here; a separate doctor-fix RFC.
- **Multiple schedules per (operation, machine).** One operation has
  exactly one schedule per machine. A user who wants two daily runs
  installs two operations (or a wrapper operation), not two
  schedules on one.
- **Schedule for a non-operation primitive.** Only `kind: operation`
  primitives are schedulable. `kind: content-type` /
  `kind: ontology` /  `kind: infrastructure` reject at the kind
  check.

## Constraints

- No new runtime dependency. The systemd `.service` / `.timer`
  emitter and the Task Scheduler XML emitter use stdlib only
  (`xml.etree.ElementTree`, `configparser`-style string templates).
  The launchd plist uses stdlib `plistlib`.
- No new top-level repo directory. `llm_wiki_kit/schedule/` is a new
  package under the existing `llm_wiki_kit/`, mirroring
  `llm_wiki_kit/research/`.
- No bypass of `journal.append_event` for the two new event types.
- The OS-artifact write **does not** route through `safe_write`:
  artifact paths fall outside `vault_root`, and `safe_write` rejects
  those by design. The kit grows one new helper,
  `write_helper.write_os_artifact()`, which is the single blessed
  exemption (paralleling `_ensure_obsidianignore`). Direct file
  writes elsewhere in the schedule module are forbidden.
- No new public CLI verb beyond `schedule`. Three subcommands
  (`install`, `uninstall`, `list`) under one verb.
- No daemon process. The kit must not start any long-lived
  subprocess; OS-side activation is a one-shot call.
- No subprocess execution of `claude` from this module — that's
  `wiki-run-exec`'s contract. The kit's `wiki run --exec` is the
  *command* the OS scheduler fires; this spec produces the
  scheduler entry, not the exec.
- No retro-edit of existing journal events. The model changes are
  additive only (two new event classes; one additive
  `OperationContract.default_time` field).
- No vault-side writes from this module. Surfacing exec failures to
  `inbox/scheduled-failures/` is `wiki-run-exec`'s contract.
- No new ADR — the load-bearing decisions (no SDK, no daemon, no
  cron, library-not-application) all trace back to
  [RFC-0003](../../rfc/0003-scheduling-and-autonomous-execution.md).
  A follow-up ADR would only land if the systemd / Task Scheduler
  shape surfaces a decision worth pinning.
