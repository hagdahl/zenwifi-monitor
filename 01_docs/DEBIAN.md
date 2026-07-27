<!-- ZenWiFi Monitor version: 0.1.0 -->

# Running ZenWiFi Monitor on Debian

The monitoring logic, the SQLite schema, the outage decision and both safety
gates are the same code as on Windows. What differs is how the work is
scheduled, where credentials come from, and how a notice reaches a person.
This document covers those three and nothing else; for what the monitor does,
read `README.md` and `01_docs/USER_GUIDE.md`.

## What has been verified, and what has not

Read this before trusting anything below.

Verified: the installer has been run end to end on a Linux system, creating the
service account, the directories, the environment and the hash-locked
dependency set, and placing the units. All four units pass `systemd-analyze
verify` against the installed paths. A monitoring run and a health run have
both completed on Linux against a temporary configuration, reading credentials
from a directory shaped exactly as systemd presents one, writing a run record
and a health record and releasing the run lease. `04_tests/test_deploy.py`
pins the activation gates and the least-privilege settings.

Not verified: no timer has ever fired on a real Debian host. `systemd-analyze
verify` parses and resolves a unit; it does not start one. During the install
run `systemctl` was substituted, because the environment used had no systemd as
process one. Nobody has yet observed this project restart a router from Debian,
and the desktop notification path has been exercised only against a substituted
implementation, never against a real session bus. Treat the first real install
as a soak, in dry-run, and read the journal.

## Install

Clone or copy the repository somewhere the root user can read, then:

```
sudo ./deploy/debian/install.sh              # prints what it would do, changes nothing
sudo ./deploy/debian/install.sh --install
```

The install creates a system account `zenwifi` with no login shell and no home
directory, an environment under `/opt/zenwifi-monitor/venv` populated from
`requirements.lock.txt` with `--require-hashes`, state under
`/var/lib/zenwifi-monitor`, logs under `/var/log/zenwifi-monitor`, and a
configuration template at `/etc/zenwifi-monitor/config.json` owned by root and
readable by the service group only. It then enables both timers.

Edit `/etc/zenwifi-monitor/config.json` before going further. The paths in it
must point at the state and log directories the installer created.

Two helpers, both standard-library only so they work before the environment
exists, and both dry runs unless told otherwise:

```
/opt/zenwifi-monitor/venv/bin/python /opt/zenwifi-monitor/scripts/configure.py \
    --config /etc/zenwifi-monitor/config.json --validate
/opt/zenwifi-monitor/venv/bin/python /opt/zenwifi-monitor/scripts/configure.py \
    --config /etc/zenwifi-monitor/config.json --migrate           # shows the plan
/opt/zenwifi-monitor/venv/bin/python /opt/zenwifi-monitor/scripts/configure.py \
    --config /etc/zenwifi-monitor/config.json --migrate --apply   # writes, with a backup
python3 /opt/zenwifi-monitor/scripts/configure.py --discover-gateway
```

`--validate` runs the monitor's own predicate rather than a second opinion
about it, so a configuration that validates here is one the monitor accepts.
`--migrate` brings an earlier configuration up to the current schema, and never
changes `execution_mode` or enables Notion on its own. This is the same tool
`scripts/Migrate-LocalConfig.ps1` calls on Windows.

## Credentials

A service has no desktop keyring to talk to, so credentials come from systemd's
encrypted credential store. systemd decrypts them into a private tmpfs
directory readable only by the service user; they are never written to disk in
the clear and never swapped.

```
sudo ./deploy/debian/install.sh --set-credentials
```

You are prompted for the router username, the router password and optionally a
Notion token. Nothing is echoed, and nothing is passed on a command line where
it would be visible in the process list. Leaving a prompt empty skips that
credential and leaves any existing one alone.

If you do not use Notion, remove the `notion_token` line from
`/etc/systemd/system/zenwifi-monitor.service` and run `systemctl daemon-reload`.
A unit that declares a credential file which does not exist fails to start,
which is deliberate: a missing credential is a configuration error, not a
reason to run without it.

## Turning production execution on

Two gates, both required, exactly as on Windows.

```
sudo ./deploy/debian/install.sh --enable-execution
```

writes a drop-in that adds `--execute` to the unit. That is gate one. Gate two
is `"execution_mode": "execute"` in `/etc/zenwifi-monitor/config.json`. With
either one unset the monitor observes and records but never touches the router,
however often the timer fires.

Leave both off for a soak first. A dry-run install records exactly what a
production install would do, so you can read a week of history before granting
the monitor the ability to act.

## Reading what happened

```
systemctl list-timers 'zenwifi-monitor*'          # when each last ran and next runs
journalctl -u zenwifi-monitor.service -n 50       # the last monitoring runs
journalctl -u zenwifi-monitor-health.service -n 50
journalctl -u zenwifi-monitor.service --since today --priority warning
systemctl status zenwifi-monitor.timer
```

A `oneshot` service that exits zero logs almost nothing, which is correct: the
monitor is meant to be silent while healthy. The durable evidence is the
`runs` table in the SQLite database, not the journal. To read it:

```
sudo -u zenwifi sqlite3 /var/lib/zenwifi-monitor/watchdog.sqlite3 \
  'SELECT timestamp_utc, internet_available, execution_mode FROM runs ORDER BY id DESC LIMIT 10;'
```

If a run failed before it could open the database, look in the bootstrap error
log under `/var/log/zenwifi-monitor`, which is written directly and is
size-bounded.

## What differs from the Windows install

| Concern | Windows | Debian |
|---|---|---|
| Scheduling | Task Scheduler and the VBS launcher | `systemd` service and timer |
| Credentials | Windows Credential Manager, via keyring | Encrypted `systemd` credentials |
| Notice | A message box | `notify-send`, only when a session bus exists |
| Environment | `%LOCALAPPDATA%\ZenWiFiMonitor\.venv` | `/opt/zenwifi-monitor/venv` |
| State | `%LOCALAPPDATA%` and the configured paths | `/var/lib/zenwifi-monitor` |
| Diagnostics | Bootstrap error log | Bootstrap error log and `journalctl` |

On a headless Debian host there is no session bus, so no notice is shown at
all. That is reported, not hidden: `show_notice` returns false and the finding
is still written to the database and the log, which are the durable record on
every platform. A monitor that stayed quiet because it could not draw a dialog
would be the worse outcome.

## Known limitation

The health observer runs on the same host, under the same init system, from the
same install as the thing it observes. It is independent of the watchdog's
third-party dependencies and of its exit codes, but not of a fault that stops
both from starting. This is recorded as F6 in `01_docs/IMPROVEMENT_PLAN.md`
and accepted by the project owner. Only an observer somewhere else would close
it, and that would also be the only thing that notices the machine being off.
