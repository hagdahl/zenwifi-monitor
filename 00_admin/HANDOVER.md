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
- The project version lives in the root `VERSION` file. Every tracked file mirrors it; run `python scripts/check_versions.py` after any change and before any release. It needs only the standard library and Git.
- Bootstrap failures before SQLite opens are recorded at `%LOCALAPPDATA%\ZenWiFiMonitor\bootstrap-errors.log` on Windows and `/var/log/zenwifi-monitor/bootstrap-errors.log` on Debian; this is the first troubleshooting location when regular run rows stop advancing. `install.sh --install` refuses when the service account cannot write that directory, because a failure the monitor hit before opening its database leaves nothing else to read.

## Recovery

1. Disable the scheduled task.
2. Preserve runtime logs for troubleshooting.
3. Restore the latest known-good Git commit.
4. Recreate the task only after dry-run validation.
