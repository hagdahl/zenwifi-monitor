# Improvement plan: reliability and Debian support

## Objective

Strengthen reliable local operation, deliver queued Notion events after
connectivity returns, add local health monitoring, and support both Windows 11
and Debian Linux without weakening the existing restart safety model.

## 1. Cross-platform operating model

Refactor the monitor into a platform-neutral core and small platform adapters.

| Concern | Windows 11 | Debian Linux |
|---|---|---|
| Scheduler | Task Scheduler and VBS wrapper | `systemd` service and timer |
| Persistent secret storage | Windows Credential Manager | Encrypted `systemd` credentials for unattended operation |
| Local notification | Persistent Windows notification | Desktop notification when a user session is available; journal and local log otherwise |
| Local state | SQLite | SQLite |
| Diagnostics | Bootstrap error log | Bootstrap error log and `journalctl` integration |

The Python monitoring, SQLite schema, outage decisions, Notion delivery, and
safety gates remain shared. Platform-specific behaviors must be isolated
behind interfaces.

Acceptance criteria:

- The same functional test suite runs on Windows and Debian.
- No platform stores router or Notion credentials in files, environment
  variables, command-line arguments, or the repository.

## 2. Durable Notion outbox and backfill

Use the existing SQLite event delivery state as an outbox.

1. Commit every relevant event locally before any Notion request.
2. Mark an event delivered only after a successful Notion response.
3. On a confirmed online run, deliver undelivered events oldest first before
   creating new remote events.
4. Stop safely on the first remote delivery failure and retain all remaining
   events for a later run.
5. Add retry metadata: delivery attempts, last attempt time, and sanitized
   error detail.
6. Bound retries per run and define retention so one bad event cannot block
   the queue indefinitely.

Acceptance criteria:

- An Offline event created during an Internet outage is recorded in SQLite.
- After connectivity returns, Notion receives it exactly once and SQLite marks
  it delivered.
- No token, authorization header, or full remote response is persisted.

## 3. Local health monitor

Create a separate health-check executable and scheduled job. It must never
restart the router.

Checks:

- the newest `runs` row is no older than the configured threshold, initially
  15 minutes;
- the SQLite database is readable;
- the bootstrap error log has changed since the previous health check;
- optional Notion outbox events exceed an age or retry threshold.

Behavior:

- write health findings to local SQLite and a dedicated health log;
- show a persistent local notification only for a new or escalating unhealthy
  state;
- clear the alert after healthy runs resume;
- keep normal operation silent.

Windows uses a separate Task Scheduler task. Debian uses a separate `systemd`
timer.

## 4. Bootstrap error-log rotation

Replace unbounded bootstrap logging with bounded rotation.

- Set a maximum size, initially 1 MiB.
- Retain one or two numbered previous files.
- Rotate before writing a new entry.
- Never overwrite the active error record before a replacement exists.
- Document the Windows and Debian bootstrap-log locations.

## 5. Locked dependencies and CI supply chain

1. Introduce a direct-dependency input file.
2. Generate a fully resolved, hash-locked requirements file for supported
   Python versions and platforms.
3. Install production dependencies with `--require-hashes`.
4. Pin GitHub Actions to immutable commit hashes and document their upstream
   release names.
5. Extend CI to Windows and Ubuntu/Debian-compatible execution.
6. Add a scheduled dependency-review workflow; upgrades remain deliberate pull
   requests with regenerated hashes and tests.

## 6. Debian deployment path

Create a Debian-specific deployment package containing:

- an installation script for Python, virtual environment, and locked
  dependencies;
- a `systemd` watchdog service and five-minute timer;
- a separate health service and timer;
- encrypted `systemd` credentials for router and optional Notion authentication;
- a least-privilege service account and restrictive filesystem permissions;
- Linux configuration setup, migration, and validation scripts;
- `journalctl` troubleshooting guidance;
- a recovery notification adapter that uses desktop notification only when a
  valid user D-Bus session exists.

The Debian path defaults to dry-run. Production activation requires the same
two explicit gates as Windows: service invocation permits execution and local
configuration enables execution.

## 7. First real recovery verification

After the first real outage and recovery, review local operational evidence:

1. Offline event in SQLite.
2. Router-restart event and cooldown state.
3. Restored event and queued Notion delivery.
4. Persistent recovery notification shown once.
5. Health monitor returning to healthy.

Do not force an outage or restart solely for this verification.

## 8. Governance and release sequence

- Update architecture, authentication, handover, decisions, user guide,
  changelog, and independent-review prompt for each implementation phase.
- Run an independent review against the exact public staging commit after the
  cross-platform refactor.
- Publish from `_public` only after Windows and Debian tests pass and
  public-release scanning is clean.

## Implementation order

1. Notion outbox, bootstrap rotation, and health monitor.
2. Platform interfaces and dependency locking.
3. Debian service, timer, credential, notification, and documentation path.
4. Cross-platform CI and independent pre-publication review.
