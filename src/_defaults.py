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

# How the restart decision is evidenced. A restart requires BOTH that the
# OLDEST FAILED RUN SINCE the last successful one is at least
# failure_minutes_before_reboot old, and that at least REQUIRED_FAILED_RUNS
# failed runs fall inside the last FAILURE_WINDOW_MINUTES and after that same
# successful run. Measuring from the successful run rather than from the first
# observed failure would count a monitoring gap as outage. The second condition is what makes a gap in
# monitoring harmless: a machine that was asleep contributes no observations,
# so history alone can never meet the count. Thirty minutes is six intervals at
# the shipped five-minute cadence, so half the runs may be missed and the
# decision still stands.
FAILURE_WINDOW_MINUTES = 30
REQUIRED_FAILED_RUNS = 3

# How many restarts the monitor may decide on before it stops trying. An outage
# upstream of the router looks exactly like an outage the router causes, and a
# restart cannot fix it, so without a bound the monitor restarts the router once
# per cooldown for as long as the operator's fault lasts. Three attempts inside
# six hours is the agreed limit: six hours is long enough that several genuinely
# separate outages in a day are each treated on their own, and three is more
# than enough for the fault a restart can actually fix.
#
# The count is taken over restarts since the last successful run, so connectivity
# returning clears it. That is what makes the bound about one episode rather than
# about the clock.
MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW_HOURS = 6

# Floors for the two thresholds the restart decision reads. A one-minute
# threshold with a one-minute cooldown passed validation and would restart the
# router on almost every run. Five minutes is one probe interval: below that the
# value cannot describe an observation the monitor is capable of making.
MIN_THRESHOLD_MINUTES = 5

# An upper bound on the lease as well as a lower one. The lease is honoured
# until it expires, so a hand-edited value of ten thousand would stop monitoring
# for a week the first time a run was killed.
MAX_RUN_LEASE_MINUTES = 60


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
