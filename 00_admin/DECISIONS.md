# Decisions and assumptions

| ID | Date | Status | Decision or assumption | Rationale |
|---|---|---|---|---|
| ADR-001 | 2026-07-26 | Approved | Project logic is stored in the project root and operations logs/state use a separate, locally configured path outside the project tree. | Logs must remain available during an Internet outage. |
| ADR-002 | 2026-07-26 | Approved | Windows Credential Manager stores router and Notion secrets. | Secrets must not be stored in code, configuration files, or logs. |
| ADR-003 | 2026-07-26 | Approved | Router restart and minimized Notion writes are allowed only with explicit `--execute`, every five minutes, after 15 continuous failure minutes and with a 30-minute cooldown. Authorization remains valid until revoked by the project owner. | Standing authorization under A1. |
| ADR-004 | 2026-07-26 | Superseded by ADR-010 | Tests, dry-run, and the current scheduled task must never request a router restart. | Independent safety boundary requested by the project owner. |
| ADR-005 | 2026-07-26 | Approved | The scheduled task runs only under the project owner's logged-in Windows account. | The same user profile can read Credential Manager and display a persistent restart notification. |
| ADR-006 | 2026-07-26 | Approved | Local SQLite logging is mandatory; Notion logging is an opt-in secondary destination. | Monitoring must preserve local operational evidence during Internet outages and must not depend on a remote integration. |
| ADR-007 | 2026-07-26 | Approved | ZenWiFi Monitor is published under Apache License 2.0 with David Hagdahl as the copyright owner. | The license permits broad reuse with an express patent grant and aligns with the Apache-2.0 router-library dependency. |
| ADR-008 | 2026-07-26 | Approved | Local configuration schema migrations are dry-run-first, create an ignored backup before writing, preserve TLS, and require an explicit opt-in for Notion logging. | Migration must not activate external behavior or silently change optional remote logging. |
| ADR-009 | 2026-07-26 | Approved | Public release uses an ignored, history-free `_public` staging tree generated only from a clean current source tree. | Earlier local Git history contains environment-specific and personal metadata that must never reach the public remote. |
| ADR-010 | 2026-07-27 | Approved | Production activation requires a separate owner decision, task registration with `-EnableExecution`, and local `execution_mode` set to `execute`. Tests and dry-run must never request a router restart. | The explicit task argument and local configuration are independent activation gates. |
