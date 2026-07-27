<!-- ZenWiFi Monitor version: 0.1.0 -->

# Authentication

## Principle

All authenticated interfaces must work without interactive input during a scheduled run. Secrets are stored persistently in Windows Credential Manager under the same Windows identity that runs the task.

The scheduled task must use **Run only when user is logged on**. The selected user sets secrets once and may then lock the session without the job prompting for credentials.

| Interface | Secret | Storage | Runtime access |
|---|---|---|---|
| ASUSWRT router | Username and password | Windows Credential Manager | Keyring, run identity |
| Notion API | Integration token | Windows Credential Manager | Keyring, run identity |

## Prohibitions

- No secrets in Git, configuration, SQLite, local logs, or Notion.
- No fallback to `.env`, environment variables, or text files for secrets.
- No secret in Task Scheduler arguments.
- Technical error detail sent to Notion is not content-minimized and can include the router endpoint. Credentials are still excluded; see ADR-014 before pointing this project at a shared Notion database.

## Operating rule

`watchdog.py` and `Setup-Secrets.py` refuse to run unless Keyring uses Windows Credential Manager. Secrets are set once interactively by the Windows identity that subsequently runs the task; monitoring then requires no user interaction.
