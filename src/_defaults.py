"""Default values shared by the watchdog and the health monitor.

The health monitor must keep working when the project virtual environment does
not, so it cannot import the watchdog, which depends on Keyring. Both therefore
read their shared defaults from this module rather than each carrying a private
copy that can drift: a health monitor and a watchdog that disagree about what
"exhausted" means would report and act on different queues.

Standard library only, and no logic.
"""
# ZenWiFi Monitor version: 0.1.0

# Outbox delivery bounds.
MAX_DELIVERIES_PER_RUN = 20
MAX_ATTEMPTS_PER_EVENT = 5
RETENTION_DAYS = 30

# Health thresholds.
RUN_AGE_MINUTES = 15
OUTBOX_AGE_MINUTES = 180
