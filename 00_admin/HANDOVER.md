<!-- ZenWiFi Monitor version: 0.1.0 -->

# Handover

## Runtime environment

- Host: Windows 11 with wired Ethernet.
- Router: ASUSWRT-compatible router through a local HTTPS interface.
- Project logic: `src/` for the Python modules and `scripts/` for the Windows launchers and setup scripts. This deviates from the standard's numbered source tree; ADR-015 records why and `01_docs/ARCHITECTURE.md` carries the mapping and edit status per directory.
- Operations logs and state: separately configured local path.

## External dependencies

- Windows Task Scheduler targets `scripts/RouterWatchdog.vbs` through an absolute path. Every scheduled job of this project shall be registered through that wrapper; pass `--script=` with one of the two entry points it allows, `src\\watchdog.py` or `src\\health.py`. It is an allowlist, not a path parameter: anything else, including a traversal, a trailing dot or an unrecognised argument, is refused with exit code 2. `Install.ps1 -RegisterTask` fails if the registered action is not `wscript.exe`.
- The Python environment lives at `%LOCALAPPDATA%\ZenWiFiMonitor\.venv`, outside the project tree, and must be referenced by absolute path. It is not project-local: a folder-hygiene routine moved a project-local `.venv` out of the cloud-synced tree while the scheduled jobs were running, and both jobs then had no interpreter. A project-local environment remains a development fallback only.
- The Notion integration and router account are provided through Windows Credential Manager.
- Python 3.11 with the Windows Python Launcher is a prerequisite. `Install.ps1 -InstallDependencies` creates the virtual environment at `%LOCALAPPDATA%\ZenWiFiMonitor\.venv` and installs the hash-locked dependency set.
- Notion is optional; SQLite remains the local primary log even when no Notion integration is configured.
- Direct dependency license and copyright notices are recorded in `THIRD_PARTY_NOTICES.md` and must be reviewed when dependencies change.
- `Migrate-LocalConfig.ps1` upgrades earlier ignored local configuration safely; it is dry-run by default and writes a timestamped ignored backup only with `-WriteConfig`.
- `src/health.py` is the local health monitor. It is standard-library only by design, runs on its own `ZenWiFiMonitorHealth` task through the silent wrapper, and can never restart the router. Register it with `Install.ps1 -RegisterHealthTask`. It shares a failure mode with the watchdog and this is accepted rather than solved: same host, same scheduler, same install, so a fault that stops both from starting leaves nobody to report it. That happened once, when a missing interpreter wedged both jobs. Detecting it would need something outside the host, which this project does not have. Recorded as ADR-021.
- Undelivered Notion events queue in the SQLite `events` table. Inspect `delivered_to_notion`, `delivery_attempts` and `last_delivery_error` when remote logging looks stalled.
- Logs rotate at 1 MiB and keep two previous files. The bootstrap error log and the health notification stamp follow `$LOGS_DIRECTORY` when `systemd` sets it, then `%LOCALAPPDATA%\ZenWiFiMonitor`, then the home directory. On the Debian units that first branch is the only one that works: the service account has no home and both units set `ProtectHome=yes`, so the home directory is unwritable there. The health log itself is written to the configured `paths.log_directory`.
- `scripts/check_dependencies.py` is the scheduled dependency review. It asks PyPI whether a direct dependency has a newer release and OSV whether any pinned version, direct or transitive, is known to be vulnerable, and it changes nothing: an upgrade means regenerating `requirements.lock.txt` with `uv pip compile --universal --generate-hashes` and running every suite on both platforms. `.github/workflows/dependency-review.yml` runs it weekly and keeps one tracking issue up to date. Standard library only, so it works before the environment exists.
- The project version lives in the root `VERSION` file. Every tracked file mirrors it; run `python scripts/check_versions.py` after any change and before any release. It needs only the standard library and Git.
- Bootstrap failures before SQLite opens are recorded at `%LOCALAPPDATA%\ZenWiFiMonitor\bootstrap-errors.log` on Windows and `/var/log/zenwifi-monitor/bootstrap-errors.log` on Debian; this is the first troubleshooting location when regular run rows stop advancing. `install.sh --install` refuses when the service account cannot write that directory, because a failure the monitor hit before opening its database leaves nothing else to read.
- **`%LOCALAPPDATA%` cannot be inspected from a remote or agent shell and be believed.** A shell reaching this machine through an assistant integration and the scheduled tasks resolve that path to different files. On 3 August the same path held 3458 bytes modified 27 July for the scheduled task and 29661 bytes modified 28 July for the inspecting shell, each with its own `health-notified.txt` beside it. That covers the bootstrap error log, the health notification stamp and the virtual environment at `%LOCALAPPDATA%\ZenWiFiMonitor\.venv`. To learn what a scheduled task sees, ask a scheduled task: register a temporary task that stats or copies the file and read its output, then delete that task — never the project's own two. The SQLite database is outside this path and both views agree on it. This has now cost two investigations, the July interpreter incident and A-10.

## Last verified deployment

On Windows there is no separate install step: the scheduled tasks run the
working tree through the silent launcher, so whatever is checked out is what
runs every five minutes. Verify it rather than assume it, and record the result
here.

**3 August 2026, commit `69121ac`, on the monitoring host.** Working tree clean. Both tasks
registered against this folder through `wscript.exe`, only `ZenWiFiMonitor`
carrying `--execute`, last result 0. Runs five minutes apart and current; 97 in
the previous twenty-four hours, which is the machine being off for two thirds of
the day rather than a fault. All events delivered to Notion, none pending or
exhausted.

One observation in that check was recorded as a gap and has since been
withdrawn. The `health_bootstrap_stamp` had not moved since 27 July although the
bootstrap error log appeared to change on 28 July, and no health row had ever
been anything but healthy. That was not a fault: the two files are different
files, seen from two different views of `%LOCALAPPDATA%`, and the health monitor
was reading its own correctly. Closed as A-10 in `01_docs/NEXT_ACTIONS.md` on 3
August, no defect. **The qualification that survives is about the instrument,
not the monitor: any part of this record that came from inspecting
`%LOCALAPPDATA%` through a remote shell describes that shell's view and not the
scheduled jobs'.** The task registrations, the run rows, the event delivery and
the working-tree state above were read from Task Scheduler and from SQLite, both
of which the two views agree on.

## Recovery

1. Disable the scheduled task.
2. Preserve runtime logs for troubleshooting.
3. Restore the latest known-good Git commit.
4. Recreate the task only after dry-run validation.
