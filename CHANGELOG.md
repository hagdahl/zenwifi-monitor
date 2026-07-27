# Changelog

## Unreleased

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
- Hardened Windows PowerShell compatibility, configuration validation, task registration, TLS setup messaging, CI permissions, and bootstrap-failure documentation after independent review.

### Rollback

- Restore the project baseline by removing the local Git commit that introduced the change. No scheduled task, router configuration, or external account was changed in this step.
