# Handover

## Runtime environment

- Host: Windows 11 with wired Ethernet.
- Router: ASUSWRT-compatible router through a local HTTPS interface.
- Project logic: project root.
- Operations logs and state: separately configured local path.

## External dependencies

- Windows Task Scheduler targets a VBS launcher through an absolute path.
- The Python environment is project-local and must be referenced by absolute path.
- The Notion integration and router account are provided through Windows Credential Manager.
- Python 3.11 with the Windows Python Launcher is a prerequisite. `Install.ps1 -InstallDependencies` creates the project-local virtual environment.
- Notion is optional; SQLite remains the local primary log even when no Notion integration is configured.
- Direct dependency license and copyright notices are recorded in `THIRD_PARTY_NOTICES.md` and must be reviewed when dependencies change.
- `Migrate-LocalConfig.ps1` upgrades earlier ignored local configuration safely; it is dry-run by default and writes a timestamped ignored backup only with `-WriteConfig`.
- Bootstrap failures before SQLite opens are recorded at `%LOCALAPPDATA%\ZenWiFiMonitor\bootstrap-errors.log`; this is the first troubleshooting location when regular run rows stop advancing.

## Recovery

1. Disable the scheduled task.
2. Preserve runtime logs for troubleshooting.
3. Restore the latest known-good Git commit.
4. Recreate the task only after dry-run validation.
