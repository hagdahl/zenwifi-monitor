<!-- ZenWiFi Monitor version: 0.1.0 -->

# Improvement plan: reliability and Debian support

## Objective

Strengthen reliable local operation, deliver queued Notion events after
connectivity returns, add local health monitoring, and support both Windows 11
and Debian Linux without weakening the existing restart safety model.

## 1. Cross-platform operating model - partially delivered

Refactor the monitor into a platform-neutral core and small platform adapters.

Delivered: the code seam. `src/_platform.py` holds the two behaviours that
differ per platform, showing a notice and starting a detached child, and is
standard-library-only so the health monitor keeps its independence from the
watchdog's third-party dependencies. `src/_secrets.py` holds the credential
rule, naming the one acceptable backend per platform and refusing every
fallback, and is imported lazily so the core stays importable where the
dependency is absent. `04_tests/test_platform.py` pins that no other module
reaches around the seam to a platform primitive, that a notice never raises,
and that a fallback backend is refused.

Not delivered: the scheduler and credential *deployment* halves of the table
below. A systemd service, timer and encrypted credentials are part of section
6, and the POSIX notice path has been exercised only against a substituted
failing surface, never against a real desktop session.

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

## 5. Locked dependencies and CI supply chain - delivered except item 6

1. Introduce a direct-dependency input file. Delivered: `requirements.in`.
2. Generate a fully resolved, hash-locked requirements file for supported
   Python versions and platforms. Delivered: `requirements.lock.txt`, one
   universal file whose environment markers cover Windows and Linux, so the two
   platforms cannot drift apart into two files.
3. Install production dependencies with `--require-hashes`. Delivered in
   `Install.ps1` and in both CI jobs.
4. Pin GitHub Actions to immutable commit hashes and document their upstream
   release names. Delivered.
5. Extend CI to Windows and Ubuntu/Debian-compatible execution. Delivered as
   two jobs running the same suites. An earlier revision excluded two suites
   from the Linux job on the stated ground that `src/watchdog.py` imported the
   Windows Credential Manager backend at module scope. That was asserted
   without being tested and was wrong: the module imported and both suites
   passed on Linux unchanged. The exclusion removed real coverage for no
   reason and has been withdrawn.
6. Add a scheduled dependency-review workflow; upgrades remain deliberate pull
   requests with regenerated hashes and tests. Not started.

## 6. Debian deployment path - partially delivered

Delivered: `deploy/debian/` carries the two services, the two timers and an
installer that creates the service account, the environment from the
hash-locked file with `--require-hashes`, the directories and the units, and
that writes the `--execute` drop-in only when asked. Credentials come from
systemd's encrypted store, which `src/_secrets.py` reads and which it refuses
to substitute. `01_docs/DEBIAN.md` documents the install, the two gates and
the `journalctl` route. `04_tests/test_deploy.py` validates every unit with
`systemd-analyze verify` and pins the gates and the least-privilege settings.

Also delivered: `scripts/configure.py` is now the single implementation of
configuration migration and validation for both platforms, standard-library
only so it works before the environment exists, and
`scripts/Migrate-LocalConfig.ps1` is a thin wrapper that calls it. `--validate`
runs the monitor's own predicate rather than a second opinion about it, and
`--discover-gateway` covers the Linux half of router setup. The installer
places the tool on the target host.

Not delivered: the interactive first-time setup on Linux is still a matter of
editing the template by hand, where Windows has `Setup-LocalConfig.ps1` and
`Setup-RouterConfig.ps1` with its TLS handshake check. And nothing here has
run under a real systemd: no timer has fired on a Debian host, the install run
substituted `systemctl`, and the desktop notification path has never met a
real session bus. The first install is a soak, in dry-run.

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

### F3. The safety-relevant wiring is only partly pinned by tests (low) - closed

`04_tests/test_watchdog.py` already pins the two-gate restart decision across the
three non-authorized combinations. Not pinned: the delivery gate itself, the
reboot cooldown, the failure-window threshold, and the rotation-failure tolerance
in `src/_logrotate.py`. Each can be reverted with every suite still green.

Closed. `04_tests/test_watchdog.py` now drives `main()` through the delivery
gate, the failure window and the reboot cooldown; `04_tests/test_outbox.py`
covers a `rotate_log` that raises; and `04_tests/test_wrapper.py` runs the
launcher itself through `cscript` and asserts that fourteen refused argument shapes
each exit 2. Only the refusal cases are exercised, because an accepted value
makes the wrapper resolve and launch the real monitor against the real local
configuration. Each pin was verified by reverting the fix in a temporary copy
and confirming the suite fails: removing the delivery gate, the cooldown check
and the rotation tolerance each produced a failing assertion.

### F4. A health run that fails after the database opens still dies silently (low) - closed

The guarded open closes the case where a locked or corrupt database aborted the
run before any check. A failure in the recording block that follows, a lock
acquired between open and insert, a full disk, or an unwritable log directory,
still propagates to the top-level handler and exits 2 with no notification.

Closed. Each of the three writes that follow the open is now tolerated rather
than fatal. A health record that cannot be inserted adds the
`health-store-unwritable` condition and drops to the database-free path, which
still reads the notification stamp, so the run keeps its memory of the last
notice. A closing state write that fails reports the fault and escalates,
because the findings and the notification decision are already made at that
point. A health log that cannot be written is reported the same way, on the
principle that an unattended job unable to record its own evidence is itself a
fault. All three report unhealthy and exit 1 instead of exiting 2 in silence,
and each is pinned by a case in `04_tests/test_health.py` that injects the
failure into an otherwise healthy system.

### F5. Smaller items (low) - closed

- A non-numeric `health` threshold raises inside a check, which `evaluate` does
  not catch, so the health run exits 2. `validate_config` rejects such a
  configuration loudly, so the exposure is a hand-edited file, but the health
  monitor's independence goal argues for the same fallback it already uses for
  the retry bound. Closed by `positive_int` in `src/health.py`, applied to the
  run age, the outbox age, the retry bound and the notice cooldown; a value that
  is absent, non-numeric or not positive falls back to the shared default.
- ADR-012 still describes the wrapper as taking a path relative to the project
  root, without mentioning the two-entry whitelist that now defines it. Closed;
  ADR-012 now names both allowed entry points and the refusal behaviour.
- The wrapper ignores unrecognized arguments instead of refusing them, so a
  mistyped `-script=` silently launches the default entry point. A strict refusal
  would match the fail-closed posture the whitelist established. Closed; the
  wrapper exits 2 on any argument other than `--execute` or `--script=`, and
  `04_tests/test_wrapper.py` covers six unrecognized shapes alongside the eight
  rejected whitelist values. Both registered tasks pass only accepted arguments,
  so neither live job is affected.

### F6. The health monitor shares the launcher's fate (medium)

Found in operation on 2026-07-27, not by review. The health monitor exists to
report faults the watchdog cannot report about itself, and it is registered
through the same silent launcher on the same host. When the launcher failed,
because the interpreter had been moved out from under it, both jobs failed
identically and for the same reason. Each left an invisible script-host dialog
that held its task open, so the single-instance policy then refused every later
trigger. The observer was blind to exactly the fault it exists to observe, and
nothing said so for roughly forty minutes.

The launcher now refuses rather than launching what is not there, and records
the refusal, so this specific cause is closed. The structural point is not: an
observer that shares its subject's start path is not independent against faults
in that start path, and the project currently claims independence without
qualifying it.

Action, for an owner decision rather than an implementation to be assumed:

- state the shared failure mode plainly in `01_docs/ARCHITECTURE.md` and
  `00_admin/HANDOVER.md`, so nobody over-trusts the monitor; or
- give the health job a different start mechanism from the watchdog, so a fault
  in one launcher cannot silence both; or
- add an observer outside this host entirely, which is the only option that also
  covers the machine being off, and the only one that costs a new moving part.

The first is cheap and honest. The third is what the failure actually argues
for. Recording the choice matters more than which is chosen.

**Accepted by the project owner on 2026-07-27**, with the first remedy: the
shared failure mode is stated plainly rather than engineered away. It is
recorded in the Debian health unit, in `01_docs/DEBIAN.md` and as ADR-021, so
the limitation travels with the code rather than living in a chat transcript.
An observer outside the host remains the only thing that would close it, and
the only thing that would also notice the machine being off.

### Carried forward from earlier rounds

- A valid launch through the wrapper still discards the child's exit code, and
  the wrapper returns before the child, so the task's single-instance policy does
  not prevent overlapping monitor processes. The *harm* is closed: an exclusive
  run lease in SQLite means two runs cannot both proceed, so overlapping delivery
  and the double delivery it allowed can no longer happen, and the lease works
  the same under `systemd` later. The exit code is still discarded, deliberately:
  making the launcher wait would let one hung run block every later trigger,
  which is the worse failure. A run that fails is detected by its missing `runs`
  row, which the health monitor already reports as a stale run.
- Dependencies are hash-locked as of `requirements.lock.txt`; this entry
  previously said they were pinned by version only and is now closed.

## Implementation order

1. Notion outbox, bootstrap rotation, and health monitor. Delivered; the outbox,
   the rotation bound and `src/health.py` are in place and the health task is
   registered.
2. Section 9. F1 to F6 are closed except F6's remedy, which is a decision for
   the owner. The carried-forward items are closed too: overlapping runs are
   prevented by the run lease, and dependencies are hash-locked.
3. Platform interfaces and dependency locking. Dependency locking is
   delivered. The platform seam in section 1 is delivered; what remains of
   section 1 is the deployment half, which belongs with step 4.
4. Debian service, timer, credential, notification, and documentation path.
5. Cross-platform CI and independent pre-publication review.
