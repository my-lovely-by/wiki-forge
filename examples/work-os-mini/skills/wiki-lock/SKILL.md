---
name: wiki-lock
description: "Coordinate concurrent writes to the vault. Acquire the lock before starting a multi-file operation (a digest, a re-sync, a bulk ingest, an operation run) so two agents working on the same vault at the same time don't trample each other. Load this skill whenever a workflow will write more than a single page, or when running a `wiki run <operation>`."
license: MIT
---

# wiki-lock

A cooperative advisory lock over the vault. Acquire it before a
multi-file write, release it when you're done. The journal records the
acquire/release pair so `wiki doctor` can detect a stale lock from a
crashed session.

## When to acquire

- Running a `wiki run <operation>` (digest, summary, follow-up).
- A bulk ingest (e.g. "process my clippings inbox" — many pages).
- A re-sync, re-tag, or re-link pass touching multiple pages.
- Any workflow whose plan touches > 1 file under `wiki/`.

You don't need the lock for:

- A single page write (a single `wiki ingest`).
- Read-only operations (`wiki search`, `wiki journal tail`, reading
  pages directly).
- Editing your own working notes in chat.

## How to acquire

```bash
wiki lock acquire --by <your-name> --reason "<short description>"
```

`--by` is the operation name or agent name (`weekly-digest`,
`bulk-ingest`, `claude-session-2026-05-15`). `--reason` is one line that
shows up in `wiki journal tail` so the user can see what's running.

The command:

- Writes a `lock.acquired` event to the journal.
- Creates `.wiki.journal/lock` with the lock holder's name and a
  timestamp.
- Exits 0 on success; non-zero if a lock is already held (with the
  current holder printed).

If acquisition fails because another agent holds the lock, **do not
override**. Tell the user, surface the current holder, and ask whether
to wait or abort.

## How to release

```bash
wiki lock release --by <your-name>
```

Always release in the same chat turn the work finishes — a forgotten
lock is the most common cause of stalls. The command writes a
`lock.released` event and removes `.wiki.journal/lock`.

If you crash or get interrupted with a lock held, the lock will be
stale. `wiki doctor` reports stale locks (last acquire with no matching
release, older than a configurable threshold); the user releases them
manually after confirming no other session is mid-flight.

## Typical workflow

```bash
# 1. Acquire
wiki lock acquire --by weekly-digest --reason "Build 2026-W20 digest"

# 2. Do the work
#    (read pages, synthesize, write outputs, append journal events)

# 3. Release
wiki lock release --by weekly-digest
```

If any step fails between acquire and release, release before
surfacing the error — leaving a held lock makes the next session
needlessly painful for the user.

## Failure modes

- **Lock already held** → don't force. Report the holder; ask the user.
- **Lock held by yourself from an earlier session** → suspicious. Don't
  reuse; ask the user to confirm the prior session is done, then run
  `wiki lock release --force --by <your-name>` after explicit
  confirmation.
- **No lock when you try to release** → harmless. Log a warning and
  continue.

## Why this is advisory

Filesystem locking on macOS / iCloud Drive is unreliable in edge cases.
The kit's lock is **advisory** — it relies on every agent that touches
the vault loading this skill and respecting it. That's enough in
practice: the kit is single-user most of the time. The lock catches
the cases where it isn't — two devices syncing, an automation running
in the background, a user resuming a session in a second window.

On filesystems that reject `flock` outright (iCloud Drive, some SMB or
NFS mounts), the kit logs a one-shot `WARNING` and proceeds without
OS-level protection — the lock then becomes purely cooperative and
the burden of coordination falls on you. Acquire anyway: the
`lock.acquired` / `lock.released` journal events still bracket the
work, and `wiki doctor` still surfaces a stale hold.

When in doubt, acquire.
