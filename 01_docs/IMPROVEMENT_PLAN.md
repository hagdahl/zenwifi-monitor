<!-- ZenWiFi Monitor version: 0.1.0 -->

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
- After connectivity returns, Notion receives it at least once and SQLite marks
  it delivered. Delivery is deliberately at-least-once: a run interrupted
  between a successful response and the delivered mark resends the event,
  which is preferred over dropping it.
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

## 9. Outstanding review findings

Raised by the third independent review round against `fb6b4a3..f51212b` and
carried forward deliberately. Identifiers are the review's own. Each item names
the failure it produces, not only the change to make.

### F1. Delivery readiness is judged on the wrong predicate (medium) - closed

`src/health.py` suppresses the stalled-queue finding, and `purge_events` in
`src/watchdog.py` applies its no-destination retention rule, when
`notion.enabled` is false. Delivery actually requires **both** `notion.enabled`
and `execution_mode` set to `execute`, so an install that has enabled Notion but
is still in its dry-run soak satisfies neither rule: its events are never
delivered, never purged, and reported as a stalled queue for ever, with
`--reset-outbox-attempts` offering no remedy because the events have no failed
attempts. The user guide's own sequence produces this, so it reaches the first
outside user who follows the documented onboarding.

Closed by `delivery_is_configured` in `src/watchdog.py` and the matching
predicate in `src/health.py`, pinned by a case for each of the four combinations
of the two flags and by a health case asserting no stalled finding during a
dry-run soak.

### F2. Condition-change escalation fires on improvement and can oscillate (low) - closed

The notification signature added for the previous round compares joined finding
codes, so a code disappearing counts as a change. Recovering from two conditions
to one raises a notification, and a transient condition beside a persistent one
raises one on every toggle, indefinitely. Codes are also not deduplicated, so two
findings of the same kind change the signature.

Closed by deduplicating codes and escalating only on a code that was not
already present. A condition never announced before is shown at once; one that
has been announced and returns is damped by `health.notice_cooldown_minutes`,
default sixty minutes, so an intermittent fault beside a persistent one cannot
raise a dialog on every toggle. The notification stamp is now JSON and carries
the codes already announced.

### F3. The safety-relevant wiring is only partly pinned by tests (low)

`04_tests/test_watchdog.py` already pins the two-gate restart decision across the
three non-authorized combinations. Not pinned: the delivery gate itself, the
reboot cooldown, the failure-window threshold, and the rotation-failure tolerance
in `src/_logrotate.py`. Each can be reverted with every suite still green.

Action: extend the existing `main()` harness, which already stubs the secret
store and the probes, with an offline sequence covering the cooldown and the
failure window, an execute-mode run with `notion_event` stubbed asserting the
delivery gate, and a case where `rotate_log` raises and the line is still
written.

### F4. A health run that fails after the database opens still dies silently (low)

The guarded open closes the case where a locked or corrupt database aborted the
run before any check. A failure in the recording block that follows, a lock
acquired between open and insert, a full disk, or an unwritable log directory,
still propagates to the top-level handler and exits 2 with no notification.

Action: wrap the recording and logging block. The findings and the notification
decision are already computed at that point, so notify first, or tolerate the
write failure the way the notification stamp already does.

### F5. Smaller items (low)

- A non-numeric `health` threshold raises inside a check, which `evaluate` does
  not catch, so the health run exits 2. `validate_config` rejects such a
  configuration loudly, so the exposure is a hand-edited file, but the health
  monitor's independence goal argues for the same fallback it already uses for
  the retry bound.
- ADR-012 still describes the wrapper as taking a path relative to the project
  root, without mentioning the two-entry whitelist that now defines it.
- The wrapper ignores unrecognized arguments instead of refusing them, so a
  mistyped `-script=` silently launches the default entry point. A strict refusal
  would match the fail-closed posture the whitelist established.

### Carried forward from earlier rounds

- A valid launch through the wrapper still discards the child's exit code, and
  the wrapper returns before the child, so the task's single-instance policy does
  not prevent overlapping monitor processes. Overlapping delivery runs have no
  per-row claim and could deliver twice. The health monitor compensates by
  observing run freshness rather than exit codes.
- Dependencies are pinned by version but not by hash, as `README.md` records.

## Implementation order

1. Notion outbox, bootstrap rotation, and health monitor. Delivered; the outbox,
   the rotation bound and `src/health.py` are in place and the health task is
   registered.
2. Section 9. F1 and F2 are closed; F3, F4 and F5 remain.
3. Platform interfaces and dependency locking.
4. Debian service, timer, credential, notification, and documentation path.
5. Cross-platform CI and independent pre-publication review.
