<!-- ZenWiFi Monitor version: 0.1.0 -->

# Glossary

- **Dry-run:** A run that measures and logs but does not restart the router or write to Notion.
- **Execute:** Explicit mode that may perform authorized external actions.
- **Failure window:** Continuous time during which all external probes fail.
- **Reboot cooldown:** Minimum time between automatic router restart attempts.
- **Outbox:** The undelivered events in the local SQLite `events` table, delivered to Notion oldest first once connectivity returns.
- **Poison event:** An event whose delivery keeps failing; it is retried up to a bound and then skipped so it cannot block the queue.
- **Health state:** The healthy or unhealthy verdict recorded by `src/health.py`, with a severity used to detect escalation.
- **Project version:** The single semantic version in the root `VERSION` file that every tracked file mirrors.
- **Version marker:** The `ZenWiFi Monitor version: MAJOR.MINOR.PATCH` line carried by each tracked file in its native comment syntax.
- **Version drift:** A state in which a file's version marker, the `VERSION` file or the newest changelog heading disagree.
