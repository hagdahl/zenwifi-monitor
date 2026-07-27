<!-- ZenWiFi Monitor version: 0.1.0 -->

# Handover

## Runtime environment

- Host: Windows 11 with wired Ethernet.
- Router: ASUSWRT-compatible router through a local HTTPS interface.
- Project logic: project root.
- Operations logs and state: separately configured local path.

## External dependencies

- Windows Task Scheduler targets `scripts/RouterWatchdog.vbs` through an absolute path. Every scheduled job of this project shall be registered through that wrapper; pass `--script=<path relative to the project root>` to point it at another entry point. `Install.ps1 -RegisterTask` fails if the registered action is not `wscript.exe`.
- The Python environment is project-local and must be referenced by absolute path.
- The Notion integration and router account are provided through Windows Credential Manager.
- Python 3.11 with the Windows Python Launcher is a prerequisite. `Install.ps1 -InstallDependencies` creates the project-local virtual environment.
- Notion is optional; SQLite remains the local primary log even when no Notion integration is configured.
- Direct dependency license and copyright notices are recorded in `THIRD_PARTY_NOTICES.md` and must be reviewed when dependencies change.
- `Migrate-LocalConfig.ps1` upgrades earlier ignored local configuration safely; it is dry-run by default and writes a timestamped ignored backup only with `-WriteConfig`.
- `src/health.py` is the local health monitor. It is standard-library only by design, runs on its own `ZenWiFiMonitorHealth` task through the silent wrapper, and can never restart the router. Register it with `Install.ps1 -RegisterHealthTask`.
- Undelivered Notion events queue in the SQLite `events` table. Inspect `delivered_to_notion`, `delivery_attempts` and `last_delivery_error` when remote logging looks stalled.
- Bootstrap and health logs rotate at 1 MiB and keep two previous files. Windows writes them under `%LOCALAPPDATA%\ZenWiFiMonitor`; Debian and other POSIX hosts use the home directory.
- The project version lives in the root `VERSION` file. Every tracked file mirrors it; run `python scripts/check_versions.py` after any change and before any release. It needs only the standard library and Git.
- Bootstrap failures before SQLite opens are recorded at `%LOCALAPPDATA%\ZenWiFiMonitor\bootstrap-errors.log`; this is the first troubleshooting location when regular run rows stop advancing.

## Recovery

1. Disable the scheduled task.
2. Preserve runtime logs for troubleshooting.
3. Restore the latest known-good Git commit.
4. Recreate the task only after dry-run validation.
