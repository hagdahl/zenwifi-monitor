<!-- ZenWiFi Monitor version: 0.1.0 -->

# ZenWiFi Monitor

## Data classification and access

| Data category | Content | Sensitivity | Read access |
|---|---|---|---|
| Source code and documentation | Monitoring logic, configuration templates, and operating instructions | Internal | Project owner and authorized maintainers |
| Local operations log | UTC time, probe outcome, action type, and technical errors | Internal operations data | Local Windows account running the job |
| Secrets | Router account and Notion token | Confidential | Windows Credential Manager for the run identity only |
| Notion log (optional) | Minimized status and restart events, no secrets | Internal operations data | Shared Notion data source when enabled |

## Purpose

An agent-independent Windows job monitors Internet connectivity through an ASUSWRT-compatible router. After a verified outage lasting more than 15 minutes, it may request a main-router restart under an explicitly documented standing authorization and log the outcome locally and, when enabled, in Notion.

Project logic is stored here. Runtime logs and state are stored outside the project tree at a locally configured path so that they remain available during an Internet outage.

## Prerequisites

- Windows 11 and a Windows account that remains signed in while monitoring is required. Run all setup steps under that same account.
- Python 3.11 (64-bit) must be installed with the Windows Python Launcher. Verify it before installation with `py -3.11 --version`. Obtain Python from [python.org](https://www.python.org/downloads/windows/) if the command is unavailable.
- Internet access is required only while Python dependencies are being installed. The monitor itself records failures locally when Internet access is unavailable.
- Router credentials are required for an eventual authorized restart. They are stored only in Windows Credential Manager.
- Notion is optional. Enabling it requires an integration token and a data source ID; disabling it has no effect on local SQLite logging.

## Project basis

This project applies the working and publication principles from [hagdahl/cowork-project-instructions](https://github.com/hagdahl/cowork-project-instructions). That repository is an inspiration and governing process reference, not a runtime dependency and not a source of router credentials or operational data.

Router communication uses the open-source [Vaskivskyi/asusrouter](https://github.com/Vaskivskyi/asusrouter) library, pinned in `requirements.txt`. The monitor uses that library for authenticated ASUSWRT-compatible router communication; it does not copy or vendor the upstream project code.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the preserved copyright notice and license handling for direct dependencies.

## License

Copyright 2026 David Hagdahl. This project is licensed under the [Apache License 2.0](LICENSE). Third-party dependency notices are maintained separately in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Versioning

The project carries one semantic version. The root `VERSION` file is the single source of truth; every tracked file
except `LICENSE` and `VERSION` mirrors it in a `ZenWiFi Monitor version: MAJOR.MINOR.PATCH` marker written in that
file's own comment syntax, and `config.example.json` carries the same value as `config_version`. The newest release
heading in [CHANGELOG.md](CHANGELOG.md) names the same version.

Run `python scripts/check_versions.py` to verify this. It uses only the standard library and Git, reads without
writing, prints a per-file summary, and accepts `--json` for a machine-readable result. Exit codes are 0 when every
marker agrees, 1 on drift, and 2 when `VERSION` is missing or malformed or Git cannot list tracked files. The same
check runs in CI and fails the build on drift. A release bumps `VERSION`, the changelog heading and every marker
together; see ADR-011 in [00_admin/DECISIONS.md](00_admin/DECISIONS.md).

## Setup and operation

The local SQLite database is the primary operational record. Notion is optional: when disabled, the monitor runs and records all events locally without a Notion token or network dependency.

Open PowerShell in the project root and run these scripts under the same Windows account that will own the scheduled task:

1. Run `& .\scripts\Install.ps1 -InstallDependencies`. It verifies Python 3.11, creates `.venv`, installs the pinned dependencies, and checks the installed dependency set.
2. Run `& .\scripts\Setup-LocalConfig.ps1`. It asks where local runtime data should be stored, creates or updates `config.local.json`, and lets the user enable or skip Notion logging. The selected folder contains `logs` and the SQLite database; it is never committed to Git.
3. If upgrading from an earlier configuration, run `& .\scripts\Migrate-LocalConfig.ps1` first. Review its dry-run plan. Run it again with `-WriteConfig` to create an ignored backup and apply the schema migration. Add `-EnableNotion` only when optional Notion logging should remain enabled.
4. Run `& .\scripts\Setup-RouterConfig.ps1` for safe discovery. After reviewing its result, run `& .\scripts\Setup-RouterConfig.ps1 -WriteConfig` to store the router endpoint. HTTPS/TLS is the normal path. The documented HTTP exception is available only after a typed acknowledgement when TLS cannot be used.
5. Run `.\.venv\Scripts\python.exe .\scripts\Setup-Secrets.py`. It stores router credentials in Windows Credential Manager. It asks separately whether the optional Notion token should be configured and never replaces a stored value when its prompt is left blank.
6. Run `.\.venv\Scripts\python.exe .\04_tests\test_watchdog.py` and a dry-run monitoring invocation. Confirm the local SQLite database receives a run record. Neither step restarts the router.
7. Run `& .\scripts\Install.ps1 -RegisterTask` only after the dry-run checks pass. It verifies that the private Python runtime exists, then creates the silent five-minute scheduled task. The task remains dry-run until a separate production decision enables execution.

`Install.ps1` accepts either switch independently. It does not configure router credentials, select a data directory, enable Notion, or activate restart capability; the numbered setup steps keep those decisions explicit.

Public GitHub release uses the history-free `_public` staging process in [01_docs/PUBLICATION.md](01_docs/PUBLICATION.md). Never push this source repository's local Git history.

The planned reliability and Debian support work is tracked in [01_docs/IMPROVEMENT_PLAN.md](01_docs/IMPROVEMENT_PLAN.md).

Before public release, use [01_docs/INDEPENDENT_REVIEW_PROMPT.md](01_docs/INDEPENDENT_REVIEW_PROMPT.md) with an independent reviewer and bind the review to the intended commit.

`scripts/RouterWatchdog.vbs` is the quiet task wrapper. It produces no visible window for routine monitoring; a persistent Windows notification is reserved for an authorized restart.

## Safety boundaries

- The default mode is `dry-run`; restart and Notion writes require `--execute`.
- Passwords and tokens are never stored in the repository, logs, or Notion.
- Every authenticated interface uses Windows Credential Manager through Keyring. Environment variables, configuration files, and command-line arguments are not used for secrets.
- Router management uses HTTPS/TLS by default. The setup helper permits HTTP only after an explicit, exact interactive risk acknowledgement when TLS is unavailable.
- A restart may occur only after at least 15 minutes of continuous external probe failures and is protected against restart loops.
- A restart and its later confirmed recovery each show a persistent notification; all other runs are silent and observable through the local log.
- Failures before the configured SQLite database opens are recorded in `%LOCALAPPDATA%\ZenWiFiMonitor\bootstrap-errors.log`; check that file when the normal run log stops advancing.
- Read [DISCLAIMER.md](DISCLAIMER.md) before using this project.

## Status

Standing authorization exists. The registered Windows task remains intentionally blocked from router restart until the project owner explicitly approves production deployment. Tests and dry-runs must never request a router restart.

`requirements.txt` contains pinned direct dependencies. Generate a fully hash-locked transitive dependency file after the first verified installation and before dependency updates. Notion logging uses the Notion REST API directly with an internal integration or personal access token in Windows Credential Manager; MCP and Pipedream are not required.
