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

# How long one monitoring run may hold the exclusive lease before another run
# is entitled to take it. A normal run finishes in seconds; the bound exists so
# a run killed without releasing cannot block the schedule for ever. Two missed
# five-minute cycles is the trade: long enough to cover a slow restart attempt,
# short enough that the health monitor's stale-run threshold still fires first.
RUN_LEASE_MINUTES = 10

# Health thresholds.
RUN_AGE_MINUTES = 15
OUTBOX_AGE_MINUTES = 180
NOTICE_COOLDOWN_MINUTES = 60

# How the restart decision is evidenced. A restart requires BOTH that the last
# successful run is at least failure_minutes_before_reboot old, and that at
# least REQUIRED_FAILED_RUNS failed runs fall inside the last
# FAILURE_WINDOW_MINUTES. The second condition is what makes a gap in
# monitoring harmless: a machine that was asleep contributes no observations,
# so history alone can never meet the count. Thirty minutes is six intervals at
# the shipped five-minute cadence, so half the runs may be missed and the
# decision still stands.
FAILURE_WINDOW_MINUTES = 30
REQUIRED_FAILED_RUNS = 3


def positive_int(value, fallback: int) -> int:
    """Read a threshold defensively.

    The watchdog rejects a malformed configuration loudly at start, but the
    health monitor is the thing that reports when the watchdog is not running,
    so it must survive a hand-edited value rather than exit on it.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback
