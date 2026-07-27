<!-- ZenWiFi Monitor version: 0.1.0 -->

# Changelog

## 0.1.0 - Unreleased

### Added

- Project baseline with Git, a secret-safe configuration template, and a separate runtime log location.
- Local-first watchdog with SQLite logging, dry-run as the default, Windows Credential Manager, and explicit execute mode.

### Changed

- Machine-specific addresses, host names, and local paths were replaced with generic configuration values before publication.
- Keyring is now a fail-closed requirement for every authenticated interface.
- Documentation is synchronized with JSON configuration, the Python monitor, and the interactive run identity.
- Watchdog error tracing, effective mode, restart throttling, and dry-run regression tests were strengthened after independent review.
- The Notion payload was verified against the connected data source with the current parent structure.
- AsusRouter connections are closed through the library's verified `async_disconnect` method.
- Logic, persisted output, and project documentation were standardized to English; credential setup now retains an existing secret when its prompt is left empty.
- Added a public-use disclaimer, line-ending rules, and a Windows GitHub Actions smoke test workflow.
- Added a router configuration setup script that discovers the Windows default gateway and requires explicit `-WriteConfig` before changing local configuration.
- Router setup now verifies an HTTPS/TLS handshake, provides step-by-step usage instructions, and refuses configuration writes when TLS is unavailable.
- Added an explicitly confirmed, last-resort insecure HTTP exception for router setup; TLS remains the default.
- Added guided local data-path setup and optional Notion logging; SQLite remains the primary log.
- Documented Python 3.11 prerequisites, the governing project-instructions reference, and the complete Install.ps1 workflow; hardened Install.ps1 validation and guidance.
- Corrected authenticated router connection initialization and session cleanup before an authorized restart command.
- Documented the upstream open-source ASUSWRT router-library dependency and its role in the implementation.
- Added version-bound third-party notices and preservation rules for direct Python dependencies.
- Added the Apache-2.0 project license with David Hagdahl as copyright owner.
- Added a dry-run-first local configuration migration and a commit-bound independent pre-publication review prompt.
- Added a history-free `_public` staging workflow for public GitHub publication.
- Added a persistent local notification when Internet connectivity is confirmed after a monitor-initiated router restart.
- Added the explicit `-EnableExecution` task-registration gate and recorded the production-activation decision model.
- Added a documented plan for durable Notion delivery, local health monitoring, dependency locking, and Debian support.
- Generalized `scripts/RouterWatchdog.vbs` into the single silent launcher for every scheduled job: it accepts `--script=<path relative to the project root>`, still forwards `--execute`, and keeps window mode 0 with no wait. `Install.ps1 -RegisterTask` now fails unless the registered task action is `wscript.exe`, and reboot and recovery notices are spawned through `pythonw.exe` with `CREATE_NO_WINDOW` so no console flashes. Affected: `scripts/RouterWatchdog.vbs`, `scripts/Install.ps1`, `src/watchdog.py`, `00_admin/DECISIONS.md` (ADR-012), `01_docs/ARCHITECTURE.md`, `00_admin/HANDOVER.md`, `01_docs/USER_GUIDE.md`, `AGENTS.md`. Rationale: an unattended job must raise no UI surface and steal no focus, and a second scheduled job is planned for health monitoring. Verified in the real spawn context on the Windows host: launching the wrapper with no arguments added exactly one `runs` row with effective mode `dry-run` and left zero visible windows; launching it with `--script=` and `--execute` against a temporary probe script showed the wrapped script received `--config <path> --execute`, and the probe was removed afterwards. Rollback: restore the previous single-purpose wrapper and the previous `launch_*_notice` bodies; the registered task needs no change because its action and arguments are unchanged.
- Added project-wide file version marking: a root `VERSION` file as the single source of truth, a version marker in every tracked file, `config_version` in the configuration schema, `scripts/check_versions.py` as a standard-library drift check with `--json` output and exit codes 0/1/2, and a CI gate that fails the build on drift. Affected: `VERSION`, `scripts/check_versions.py`, `04_tests/test_check_versions.py`, `.github/workflows/test.yml`, `scripts/Migrate-LocalConfig.ps1`, `config.example.json` and a one-line marker in every other tracked file. Rationale: the governing standard prescribes no versioning scheme for downstream projects, so the scheme is chosen here and recorded as ADR-011. Verified by running `python scripts/check_versions.py` (ALL GREEN, 30 tracked files checked) and `python 04_tests/test_check_versions.py` against a temporary Git repository covering the green, stale, missing-marker and changelog-drift branches.
- Hardened Windows PowerShell compatibility, configuration validation, task registration, TLS setup messaging, CI permissions, and bootstrap-failure documentation after independent review.

### Rollback

- Remove the version marking by reverting the commit that introduced it. The marker lines are comments in every file type except `config.example.json`, where `config_version` is an additive key that `validate_config` ignores, so reverting cannot change monitoring behaviour. Deleting `VERSION` alone makes `check_versions.py` exit 2.
- Restore the project baseline by removing the local Git commit that introduced the change. No scheduled task, router configuration, or external account was changed in this step.
