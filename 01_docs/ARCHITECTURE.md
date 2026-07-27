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
8. When explicitly enabled, each relevant event is also logged in Notion with data minimization. SQLite remains the primary log.

## Components

| Component | Responsibility | Data |
|---|---|---|
| Python monitor | Orchestration, probes, decisions, local log | Operations status |
| Python/AsusRouter adapter | Authenticated router restart through the pinned [asusrouter](https://github.com/Vaskivskyi/asusrouter) dependency | Router secret, not logged |
| VBS wrapper | Silent background execution for every scheduled job; selects the target with `--script=` and forwards `--execute` | None |
| Windows Credential Manager | Passwords and Notion token | Secrets |
| Notion (optional) | Remote log of minimized events when enabled | Operations status |
| Version marking | Single project version in `VERSION`, mirrored into every tracked file and verified by `scripts/check_versions.py` | No operational data |
