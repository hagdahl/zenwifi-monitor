<!-- ZenWiFi Monitor version: 0.1.0 -->

# Authentication

## Principle

All authenticated interfaces must work without interactive input during a scheduled run. Secrets are stored persistently in Windows Credential Manager under the same Windows identity that runs the task.

The scheduled task must use **Run only when user is logged on**. The selected user sets secrets once and may then lock the session without the job prompting for credentials.

| Interface | Secret | Storage | Runtime access |
|---|---|---|---|
| ASUSWRT router | Username and password | Windows Credential Manager | Keyring, run identity |
| Notion API | Integration token | Windows Credential Manager, or encrypted `systemd` credentials on Debian | Keyring on a desktop session; `$CREDENTIALS_DIRECTORY` under `systemd` |

## Prohibitions

- No secrets in Git, configuration, SQLite, local logs, or Notion.
- No fallback to `.env`, environment variables, or ordinary text files for secrets. The one file-shaped exception is `systemd`'s own credentials directory, which is a private tmpfs created per run, owned by the service user with mode 0700, never written to disk and never swapped. `src/_secrets.py` accepts it only when it has exactly those properties, and refuses a directory that merely exists — because a plain directory of plaintext files would otherwise satisfy this rule while breaking it.
- No secret in an environment variable. `$CREDENTIALS_DIRECTORY` names a location; it never carries a value.
- No secret in Task Scheduler arguments or in a `systemd` unit.
- Technical error detail sent to Notion is not content-minimized and can include the router endpoint. Credentials are still excluded; see ADR-014 before pointing this project at a shared Notion database.

## Operating rule

`watchdog.py` and `Setup-Secrets.py` fail closed on the store rather than on the read. There are exactly two acceptable stores, and which one applies is decided by how the run was started, not by a setting anybody could get wrong:

- A desktop session's protected store, through Keyring — Windows Credential Manager on Windows, the Secret Service on Linux, the Keychain on macOS. Any other Keyring backend, including its plaintext, in-memory, null and chained fallbacks, is refused: a fallback that satisfies the code while breaking the rule is the failure this design exists to prevent.
- `systemd`'s encrypted credentials, which is what an unattended Debian service has, because a service has no desktop keyring to talk to. Its presence — `$CREDENTIALS_DIRECTORY`, set only by `systemd` — is what selects it. A unit that declares credentials whose directory is missing, or is not owned by the run's own user with no group or other access, is a misconfiguration and is refused rather than quietly falling back.

On Windows, secrets are set once interactively by the identity that subsequently runs the task. On Debian they are encrypted at install time with `systemd-creds` and decrypted into the private directory for each run. Monitoring then requires no user interaction on either platform.
