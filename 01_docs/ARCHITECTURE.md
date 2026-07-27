<!-- ZenWiFi Monitor version: 0.1.0 -->

# Architecture

## Flow

1. A scheduled Windows task starts the hidden VBS wrapper every five minutes. Every scheduled job of this project is registered through that one wrapper, so no job can raise a console window.
2. The wrapper starts Python through an absolute path.
3. The monitor reads `config.local.json` and retrieves secrets from Credential Manager.
   Older local configurations are migrated by a separate, dry-run-first script that preserves a local backup and does not access external systems.
4. Two independent HTTPS probes determine whether Internet connectivity exists.
5. Results and state are stored atomically on the local log disk.
6. After 15 continuous failure minutes, the monitor establishes an authenticated router session and at most one router restart occurs per cooldown window when `--execute` and standing authorization apply. The session is closed afterwards.
7. After a successful monitor-initiated restart, the next confirmed online result shows a persistent local recovery notification.
8. Every relevant event is committed to SQLite first. When Notion is enabled and the run is authorized, the outbox delivers undelivered events oldest first, marks each delivered only after a successful response, and stops on the first failure. SQLite remains the primary log.
9. A separate fifteen-minute health task inspects run freshness, database readability, the bootstrap error log and the outbox backlog, and raises a persistent notification only when the state becomes unhealthy or escalates.

## Repository layout

The layout is adapted from the governing standard's prescribed tree; ADR-015 records the deviation and its reasons.
The table below was verified against an actual filesystem inventory, not carried over from a template.

| Directory | Contents | Standard's equivalent | Edit status | Classification |
|---|---|---|---|---|
| `00_admin/` | Decisions, glossary, handover | `00_admin/` | Editable | Logic |
| `01_docs/` | Architecture, authentication, user guide, publication, review prompt, improvement plan | `01_docs/` | Editable | Logic |
| `04_tests/` | Smoke and regression suites | `04_tests/` | Editable | Logic |
| `src/` | `watchdog.py`, `health.py`, `_platform.py`, `_secrets.py`, `_defaults.py`, `_logrotate.py` | `03_src/modules` and `03_src/scripts` | Editable | Logic |
| `scripts/` | PowerShell setup and staging, the VBS launcher, and `configure.py`, the cross-platform migration and validation tool | `03_src/scripts` | Editable | Logic |
| `deploy/debian/` | `systemd` units, timers and the Debian installer | `03_src/scripts` | Editable | Logic |
| `.github/` | Continuous integration workflow | Not in the tree | Editable | Logic |
| project root | README, licence, disclaimer, security, changelog, configuration template, `VERSION` | Same | Editable | Logic |
| `_public/` | Generated history-free publication staging | Not in the tree | Never edited by hand | Derived |
| `_backups/` | Dated pre-change copies | Not in the tree | Read-only once written | Data |
| `.venv/` | Development-only Python runtime; the runtime environment lives under `%LOCALAPPDATA%\ZenWiFiMonitor\.venv` per ADR-016 | Not in the tree | Never touch | External tool state |

There is no `02_data/`, `05_logs/` or `06_exports/`. The project ingests no data and produces no exports, and its only
persistent runtime state, the SQLite database and the log directory, is placed outside the project tree by ADR-001 so
that it survives an outage and does not sit in a cloud-synced directory. Everything under `_public/`, `_backups/`,
`.venv/` and `config.local.json` is ignored by Git.

## Components

| Component | Responsibility | Data |
|---|---|---|
| Python monitor | Orchestration, probes, decisions, local log | Operations status |
| Python/AsusRouter adapter | Authenticated router restart through the pinned [asusrouter](https://github.com/Vaskivskyi/asusrouter) dependency | Router secret, not logged |
| VBS wrapper | Silent background execution for every scheduled job; selects the target with `--script=` and forwards `--execute` | None |
| Windows Credential Manager | Passwords and Notion token | Secrets |
| Notion (optional) | Remote log of status, action and technical error detail when enabled | Operations status; error text may include the router endpoint; never credentials |
| Notion outbox | Durable SQLite delivery queue with bounded retries, retention and sanitized error detail | Operations status, no secrets |
| Health monitor | Standard-library-only observer on its own silent task; never restarts the router | Health state |
| Version marking | Single project version in `VERSION`, mirrored into every tracked file and verified by `scripts/check_versions.py` | No operational data |
