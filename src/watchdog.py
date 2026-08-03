"""Local-first ZenWiFi Internet watchdog. Defaults to dry-run."""
# ZenWiFi Monitor version: 0.1.0
import argparse
import asyncio
import hashlib
import os
import json
import re
import socket
import sqlite3
import ssl
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _defaults import (FAILURE_WINDOW_MINUTES, MAX_ATTEMPTS_PER_EVENT,  # noqa: E402
                       MAX_DELIVERIES_PER_RUN, MAX_RESTARTS_PER_WINDOW,
                       MAX_RUN_LEASE_MINUTES, MIN_THRESHOLD_MINUTES,
                       REQUIRED_FAILED_RUNS, RESTART_WINDOW_HOURS,
                       RETENTION_DAYS, RUN_LEASE_MINUTES, positive_int)
from _logrotate import append_log, bootstrap_log_path  # noqa: E402
from _platform import LEVEL_INFO, LEVEL_WARNING, show_notice, spawn_detached, windowless_interpreter  # noqa: E402
from _secrets import SERVICE, get_secret, require_persistent_secret_store  # noqa: E402

__all__ = ["SERVICE", "require_persistent_secret_store"]

def utc_now() -> datetime: return datetime.now(UTC)
def utc_text(value: datetime | None = None) -> str: return (value or utc_now()).isoformat()

def write_bootstrap_error(error: Exception) -> None:
    """Record failures that happen before the configured SQLite store is available.

    The log is size-bounded and rotated before writing, so a persistent failure
    cannot grow it without limit and the active record is never truncated.

    A failure to write is swallowed on purpose. This runs inside the top-level
    exception handler, so an OSError raised here would propagate in place of the
    error being recorded: the operator would see a permission problem on a log
    directory instead of the fault that actually stopped the run, and the
    non-zero exit that the handler is there to produce would never be reached.
    Losing the record is bad; losing the original diagnosis and the exit code is
    worse.
    """
    try:
        append_log(bootstrap_log_path(), f"{utc_text()} {type(error).__name__}: {error}")
    except OSError:
        pass


def sanitize_error(error: Exception) -> str:
    """Describe a failure without persisting a credential.

    Two passes, because they catch different things. The pattern removes an
    authorization header, which has a recognisable shape. The value pass removes
    the credentials this run actually holds, which do not: a router password can
    look like anything, so no regular expression can find it. The values are
    already in memory; reading them again here costs nothing and is the only way
    to be sure the string that leaves the machine does not contain them.
    """
    text = f"{type(error).__name__}: {error}"
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", text)
    for name in ("router_password", "router_username", "notion_token"):
        try:
            value = get_secret(name)
        except Exception:
            # The store being unreachable must not stop a failure from being
            # recorded; it only means this pass cannot help.
            continue
        if value and len(value) > 2 and value in text:
            text = text.replace(value, f"<{name} redacted>")
    return text[:400]

def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))

def validate_config(cfg: dict) -> None:
    """Reject incomplete local configuration before monitoring can infer an outage."""
    if not isinstance(cfg, dict): raise RuntimeError("Configuration must be a JSON object.")
    for section in ("paths", "router", "monitor"):
        if not isinstance(cfg.get(section), dict): raise RuntimeError(f"Configuration section '{section}' is required.")
    for section in ("paths", "router"):
        for key, value in cfg[section].items():
            if isinstance(value, str) and (not value.strip() or value.startswith("<")):
                raise RuntimeError(f"Configuration value '{section}.{key}' is missing or still a placeholder.")
    for section, key in (("paths", "state_database"), ("router", "host")):
        value = cfg[section].get(key)
        if not isinstance(value, str) or not value.strip() or value.startswith("<"):
            raise RuntimeError(f"Configuration value '{section}.{key}' is required.")
    urls = cfg["monitor"].get("probe_urls")
    if not isinstance(urls, list) or not urls or not all(isinstance(url, str) and url.startswith("https://") for url in urls):
        raise RuntimeError("monitor.probe_urls must be a non-empty list of HTTPS URLs.")
    # `isinstance(True, int)` holds, so a bare int check accepted True for both
    # of the thresholds the restart decision reads — and True is 1, which with
    # no floor meant a one-minute outage and a one-minute cooldown. The outbox
    # loop below already excluded booleans; the safety-critical pair did not.
    for key in ("failure_minutes_before_reboot", "reboot_cooldown_minutes"):
        value = cfg["monitor"].get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < MIN_THRESHOLD_MINUTES:
            raise RuntimeError(
                f"monitor.{key} must be an integer of at least {MIN_THRESHOLD_MINUTES}; "
                "a threshold shorter than one probe interval cannot describe an "
                "observation the monitor is able to make.")
    # A cooldown shorter than the outage threshold lets a second restart be
    # decided on evidence the first one already used, because a restart that did
    # not help leaves the same run history in place.
    if cfg["monitor"]["reboot_cooldown_minutes"] < cfg["monitor"]["failure_minutes_before_reboot"]:
        raise RuntimeError(
            "monitor.reboot_cooldown_minutes must be at least "
            "monitor.failure_minutes_before_reboot, or a restart can be repeated "
            "on evidence that has already been acted on.")
    lease = cfg["monitor"].get("run_lease_minutes")
    if lease is not None and (isinstance(lease, bool) or not isinstance(lease, int)
                              or not 1 <= lease <= MAX_RUN_LEASE_MINUTES):
        raise RuntimeError(
            f"monitor.run_lease_minutes must be an integer between 1 and "
            f"{MAX_RUN_LEASE_MINUTES}. The lease is honoured until it expires, so a "
            "larger value stops monitoring for as long as it names.")
    for key, limit in (("max_restarts_per_window", MAX_RESTARTS_PER_WINDOW),
                       ("restart_window_hours", RESTART_WINDOW_HOURS)):
        value = cfg["monitor"].get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or value < 1):
            raise RuntimeError(f"monitor.{key} must be a positive integer.")
    # TLS is the default and the only mode that does not send the router
    # password in clear text. The typed confirmation in configure.py guards the
    # moment it is written; this guards every run afterwards, because the value
    # can be hand-edited and nothing else would notice.
    use_tls = cfg["router"].get("use_tls", True)
    if not isinstance(use_tls, bool):
        raise RuntimeError("router.use_tls must be true or false.")
    if use_tls is False and cfg["router"].get("insecure_http_acknowledged") is not True:
        raise RuntimeError(
            "router.use_tls is false, which sends the router password over plain "
            "HTTP. That is allowed only when router.insecure_http_acknowledged is "
            "true as well. Run configure.py --allow-insecure-http rather than "
            "editing the value by hand.")
    # A floor of two on the required count is deliberate: one observation is
    # exactly the behaviour that made a monitoring gap sufficient to restart a
    # router, so no configuration may restore it.
    window = cfg["monitor"].get("failure_window_minutes", FAILURE_WINDOW_MINUTES)
    required = cfg["monitor"].get("required_failed_runs", REQUIRED_FAILED_RUNS)
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise RuntimeError("monitor.failure_window_minutes must be a positive integer.")
    if isinstance(required, bool) or not isinstance(required, int) or required < 2:
        raise RuntimeError("monitor.required_failed_runs must be an integer of at least 2; "
                           "a single observation must never be enough to restart a router.")
    if cfg.get("execution_mode") not in ("dry-run", "execute"):
        raise RuntimeError("execution_mode must be 'dry-run' or 'execute'.")
    for section, keys in (("outbox", ("max_deliveries_per_run", "max_attempts_per_event", "retention_days")),
                          ("health", ("run_age_minutes", "outbox_age_minutes", "notice_cooldown_minutes"))):
        values = cfg.get(section)
        if values is None:
            continue
        if not isinstance(values, dict):
            raise RuntimeError(f"Configuration section '{section}' must be a JSON object when present.")
        for key in keys:
            if key in values and (not isinstance(values[key], int) or isinstance(values[key], bool) or values[key] <= 0):
                raise RuntimeError(f"{section}.{key} must be a positive integer.")

def add_column(db, table: str, column: str, definition: str) -> None:
    """Add a column, tolerating another process having just added it.

    The watchdog and the health monitor open the same database on their own
    timers, so both can read `PRAGMA table_info` before either writes. The loser
    of that race raised `duplicate column name` and died — a migration that only
    fails when two schedules happen to coincide, which is the kind that is found
    in production rather than in a suite.
    """
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError as error:
        if "duplicate column name" not in str(error).lower():
            raise


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL, status TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, delivered_to_notion INTEGER NOT NULL DEFAULT 0, delivery_attempts INTEGER NOT NULL DEFAULT 0, last_delivery_attempt_utc TEXT, last_delivery_error TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL, internet_available INTEGER NOT NULL, execution_mode TEXT NOT NULL, configured_execution_mode TEXT NOT NULL)")
    columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
    if "configured_execution_mode" not in columns:
        add_column(db, "runs", "configured_execution_mode",
                   "TEXT NOT NULL DEFAULT 'unknown'")
    event_columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
    for column, definition in (("delivery_attempts", "INTEGER NOT NULL DEFAULT 0"),
                               ("last_delivery_attempt_utc", "TEXT"),
                               ("last_delivery_error", "TEXT")):
        if column not in event_columns:
            add_column(db, "events", column, definition)
    db.execute("CREATE TABLE IF NOT EXISTS health (id INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL, "
               "state TEXT NOT NULL, severity INTEGER NOT NULL, detail TEXT NOT NULL)")
    # One row at most. The single-row constraint is the lock: two runs cannot
    # both believe they hold it, whatever the scheduler or the launcher does.
    db.execute("CREATE TABLE IF NOT EXISTS run_lease (id INTEGER PRIMARY KEY CHECK (id = 1), "
               "owner TEXT NOT NULL, acquired_utc TEXT NOT NULL)")
    db.commit()
    return db

def get_state(db, key):
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None

def set_state(db, key, value):
    db.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

def log_event(db, status, action, detail) -> int:
    """Record an event locally and commit it before any remote delivery is attempted."""
    cursor = db.execute("INSERT INTO events(timestamp_utc,status,action,detail) VALUES(?,?,?,?)",
                        (utc_text(), status, action, detail[:1900]))
    db.commit()
    return cursor.lastrowid

def log_run(db, online: bool, effective_mode: str, configured_mode: str):
    db.execute("INSERT INTO runs(timestamp_utc,internet_available,execution_mode,configured_execution_mode) VALUES(?,?,?,?)", (utc_text(), int(online), effective_mode, configured_mode))
    db.commit()

def probe(url: str) -> bool:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ZenWiFiMonitor/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False

def internet_available(urls: list[str]) -> bool:
    return any(probe(url) for url in urls)

async def reboot_router(cfg: dict) -> bool:
    from asusrouter import AsusRouter
    from asusrouter.modules.system import AsusSystem
    username = get_secret("router_username")
    password = get_secret("router_password")
    if not username or not password: raise RuntimeError("Router credentials are missing from the operating system's protected credential store.")
    port = cfg["router"].get("management_port", cfg["router"].get("https_port", 8443))
    use_tls = cfg["router"].get("use_tls", True)
    router = AsusRouter(hostname=cfg["router"]["host"], username=username, password=password, port=port, use_ssl=use_tls)
    try:
        if not await router.async_connect():
            raise RuntimeError("Router authentication failed.")
        return bool(await router.async_set_state(AsusSystem.REBOOT))
    finally:
        await router.async_del_connection()

def notion_event(cfg: dict, status: str, action: str, detail: str) -> None:
    token = get_secret("notion_token")
    data_source_id = cfg["notion"]["data_source_id"]
    if not token or data_source_id.startswith("<"): raise RuntimeError("Notion token or data source ID is missing.")
    body = {"parent": {"data_source_id": data_source_id}, "properties": {
        "Name": {"title": [{"text": {"content": f"Router watchdog {utc_text()}"}}]},
        "Status": {"select": {"name": status}},
        "Action": {"rich_text": [{"text": {"content": action}}]},
        "Detail": {"rich_text": [{"text": {"content": detail[:1900]}}]},
        "Timestamp": {"date": {"start": utc_text()}}
    }}
    request = urllib.request.Request("https://api.notion.com/v1/pages", data=json.dumps(body).encode(), method="POST", headers={"Authorization": f"Bearer {token}", "Notion-Version": cfg["notion"]["api_version"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20): pass

def outbox_settings(cfg: dict) -> tuple[int, int, int]:
    """Bounded retry and retention limits, so one bad event cannot block the queue."""
    settings = cfg.get("outbox", {}) if isinstance(cfg.get("outbox"), dict) else {}
    return (int(settings.get("max_deliveries_per_run", MAX_DELIVERIES_PER_RUN)),
            int(settings.get("max_attempts_per_event", MAX_ATTEMPTS_PER_EVENT)),
            int(settings.get("retention_days", RETENTION_DAYS)))


def purge_events(db, retention_days: int, max_attempts: int, delivery_configured: bool = True) -> dict:
    """Enforce the retention window as an absolute bound on any event's age.

    The window used to depend on why an event was still here: delivered events
    were dropped, exhausted ones were dropped, and undelivered ones only when
    the installation was judged unable to deliver at all. That judgement read
    the configuration alone, so an install with `execution_mode` set to execute
    but whose invocation never carried `--execute` — a natural half-step,
    because the two gates are opened by different commands — delivered nothing,
    accumulated events that never reached a single attempt, and reported a
    stalled queue for ever.

    The window is now what it says: no event outlives it, whatever the reason it
    is still here. That has no dependency on either gate, which is the point.
    The alternative, judging retention on whether this particular run could
    deliver, would let a manual dry-run against a production install purge the
    events the scheduled run was about to send.

    Events dropped without ever being delivered are counted separately and the
    caller records one aggregated event for them, so the loss is visible rather
    than silent.

    `max_attempts` and `delivery_configured` are deliberately unused. They read
    as dead parameters and the A-17 sweep flagged them as such: a mutation that
    forces `delivery_configured` true changes nothing, because nothing consults
    it. They are kept because `04_tests/test_outbox.py` passes the flag both
    ways and asserts the same outcome, which is how the rule "no event outlives
    the window, whatever the reason it is still here" is pinned. Deleting them
    would delete the only place that property is stated.
    """
    cutoff = utc_text(utc_now() - timedelta(days=retention_days))
    delivered = db.execute("DELETE FROM events WHERE delivered_to_notion=1 AND timestamp_utc < ?",
                           (cutoff,)).rowcount
    undelivered = db.execute("SELECT COUNT(*) FROM events WHERE delivered_to_notion=0 "
                             "AND timestamp_utc < ?", (cutoff,)).fetchone()[0]
    abandoned = db.execute("DELETE FROM events WHERE delivered_to_notion=0 AND timestamp_utc < ?",
                           (cutoff,)).rowcount
    db.commit()
    return {"delivered": delivered, "abandoned": abandoned, "undelivered": undelivered}


def reset_exhausted_events(db, max_attempts: int) -> int:
    """Clear the retry bound so exhausted events are attempted again.

    This is the operator escape hatch after the remote destination is fixed.
    """
    cursor = db.execute("UPDATE events SET delivery_attempts=0, last_delivery_error=NULL "
                        "WHERE delivered_to_notion=0 AND delivery_attempts >= ?", (max_attempts,))
    db.commit()
    return cursor.rowcount


def deliver_outbox(db, cfg: dict) -> dict:
    """Deliver undelivered events oldest first, stopping safely on the first failure.

    Delivery is at-least-once, not exactly-once. An event is marked delivered
    only after a successful Notion response, so nothing is lost; but a process
    that dies between a successful response and that mark will resend the event
    on the next run. Duplicates in the remote log are therefore possible after
    an interrupted run, and are preferred over silently dropping an event.
    """
    max_per_run, max_attempts, retention_days = outbox_settings(cfg)
    rows = db.execute("SELECT id,status,action,detail FROM events "
                      "WHERE delivered_to_notion=0 AND delivery_attempts < ? ORDER BY id ASC LIMIT ?",
                      (max_attempts, max_per_run)).fetchall()
    delivered = 0
    stopped_on = None
    for row_id, status, action, detail in rows:
        db.execute("UPDATE events SET delivery_attempts=delivery_attempts+1, last_delivery_attempt_utc=? WHERE id=?",
                   (utc_text(), row_id))
        db.commit()
        try:
            notion_event(cfg, status, action, detail)
        except Exception as error:
            db.execute("UPDATE events SET last_delivery_error=? WHERE id=?", (sanitize_error(error), row_id))
            db.commit()
            stopped_on = row_id
            break
        db.execute("UPDATE events SET delivered_to_notion=1, last_delivery_error=NULL WHERE id=?", (row_id,))
        db.commit()
        delivered += 1
    # Retention runs before the counts, so the returned figures describe rows
    # that still exist rather than rows this call has just deleted.
    purged = purge_events(db, retention_days, max_attempts, delivery_is_configured(cfg))
    pending = db.execute("SELECT COUNT(*) FROM events WHERE delivered_to_notion=0").fetchone()[0]
    exhausted = db.execute("SELECT COUNT(*) FROM events WHERE delivered_to_notion=0 AND delivery_attempts >= ?",
                           (max_attempts,)).fetchone()[0]
    return {"delivered": delivered, "pending": pending, "exhausted": exhausted,
            "purged": purged["delivered"], "abandoned": purged["abandoned"], "stopped_on": stopped_on}


def lease_owner() -> str:
    """Identify this run well enough to tell a stale lease from a live one."""
    return f"{os.getpid()}@{utc_text()}"


def run_lease_minutes(cfg: dict) -> int:
    """The lease bound, falling back to the shared default on a bad value."""
    try:
        value = int(cfg.get("monitor", {}).get("run_lease_minutes", RUN_LEASE_MINUTES))
    except (TypeError, ValueError):
        return RUN_LEASE_MINUTES
    return value if value > 0 else RUN_LEASE_MINUTES


def acquire_run_lease(db, owner: str, minutes: int) -> bool:
    """Take the exclusive right to run, or report that another run holds it.

    The scheduler's own single-instance policy cannot be relied on: the silent
    launcher returns before its child, so the task is finished while the run is
    still going and the next trigger starts a second one. Two concurrent runs
    would each drain the outbox, and an event delivered by both is delivered
    twice. The lease closes that regardless of how the run was started, which
    also means it keeps working under systemd later.

    A lease older than the bound is taken over. A run killed without releasing
    must not stop monitoring for ever, and stealing a stale lease is safer than
    honouring one whose owner may no longer exist.
    """
    cutoff = utc_now() - timedelta(minutes=minutes)
    try:
        # IMMEDIATE, not deferred: the write lock is taken before the read, so
        # two runs cannot both read "no lease" and then both write one. The A-17
        # sweep could not construct a case where deferred changes the observable
        # outcome here — SQLite refuses a deferred read-to-write upgrade outright
        # rather than honouring `busy_timeout`, so the loser still declines — and
        # the concurrency case in the suite passes either way. It is kept because
        # it states the intent at the point where the intent matters, and because
        # that equivalence is a property of the current pragmas rather than of
        # this function. Recorded honestly: this line is unpinned, and the sweep
        # says so rather than pretending otherwise.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT owner, acquired_utc FROM run_lease WHERE id = 1").fetchone()
        if row is not None:
            try:
                held_since = datetime.fromisoformat(row[1])
            except (TypeError, ValueError):
                held_since = None
            # An unparseable timestamp is treated as stale rather than as a
            # permanent lock, so a corrupt row cannot silently stop monitoring.
            # So is one in the future: a clock that jumped forward, or a lease
            # written by a host whose clock is wrong, would otherwise lock this
            # monitor out for the whole of the skew with nothing to show for it.
            if held_since is not None and held_since > utc_now():
                held_since = None
            if held_since is not None and held_since > cutoff:
                db.rollback()
                return False
        db.execute("INSERT INTO run_lease (id, owner, acquired_utc) VALUES (1, ?, ?) "
                   "ON CONFLICT(id) DO UPDATE SET owner = excluded.owner, "
                   "acquired_utc = excluded.acquired_utc", (owner, utc_text()))
        db.commit()
        return True
    except sqlite3.OperationalError:
        # Another run holds the write lock at this instant. That is itself
        # evidence of a concurrent run, so decline rather than wait.
        return False


def release_run_lease(db, owner: str) -> None:
    """Release the lease, but only if this run still owns it.

    A run whose lease was stolen while it was slow must not delete the new
    owner's lease on its way out.
    """
    try:
        db.execute("DELETE FROM run_lease WHERE id = 1 AND owner = ?", (owner,))
        db.commit()
    except sqlite3.Error:
        # The next run reclaims the lease once it goes stale, so failing to
        # release is recoverable and must not mask the run's own outcome.
        pass


def outage_evidence(db, cfg: dict) -> dict:
    """Describe the outage the recorded runs actually support.

    The restart decision used to be the age of a stored marker that only a
    successful run cleared. Nothing required a run to have happened in between,
    so any gap in monitoring — a sleeping machine, a logged-off user, a run that
    declined the lease — turned a stale marker into a restart on the first
    failed probe afterwards, on evidence of no outage at all.

    The decision is now taken from the `runs` table, which already records every
    run and its connectivity result before the decision point. Two independent
    conditions, both required:

    * duration — the most recent successful run is at least
      `failure_minutes_before_reboot` old. This preserves the promise the notice
      and the event text make, and it is what breaks on flapping: one successful
      run in between moves the reference forward, which is correct, because
      flapping is not an outage.
    * evidence — at least `required_failed_runs` failed runs fall within the
      last `failure_window_minutes` **and after the most recent successful
      run**. This is what makes a gap harmless. History cannot satisfy a count
      taken over a recent window.

    That second clause was missing until the sixth review round found it, and
    its absence is worth spelling out because the two conditions looked
    independent and were not. The duration was measured from the current
    episode while the count was taken over the window regardless of episode, so
    failures from a previous outage that had already ended could make up the
    shortfall in a new one. Two failures at 09:50 and 09:55, connectivity back
    at 09:57, then failures at 10:00 and 10:20 across a sleep, and the monitor
    saw four failures where the current episode held two — enough to restart the
    router on evidence half of which described an outage that was over. Both
    figures now come from the same episode, which is the only reading under
    which the two conditions mean what the docstring says they mean.

    Returns the figures rather than a verdict, so the caller can report what was
    observed instead of asserting a constant.
    """
    now = utc_now()
    window = positive_int(cfg.get("monitor", {}).get("failure_window_minutes"),
                          FAILURE_WINDOW_MINUTES)
    required = positive_int(cfg.get("monitor", {}).get("required_failed_runs"),
                            REQUIRED_FAILED_RUNS)
    window_start = utc_text(now - timedelta(minutes=window))

    # The successful run is found first, because both figures are scoped to the
    # episode it ends. Counting before knowing where the episode starts is the
    # defect this ordering makes impossible to reintroduce by accident.
    last_online_row = db.execute(
        "SELECT MAX(timestamp_utc) FROM runs WHERE internet_available = 1").fetchone()
    last_online = last_online_row[0] if last_online_row else None

    if last_online:
        # The window is still applied. It is a lower bound on recency, not a
        # substitute for the episode boundary: a failure from this episode that
        # is older than the window is not evidence that the outage is current.
        failed_in_window = db.execute(
            "SELECT COUNT(*) FROM runs WHERE internet_available = 0 "
            "AND timestamp_utc >= ? AND timestamp_utc > ?",
            (window_start, last_online)).fetchone()[0]
    else:
        # Nothing has ever succeeded, so there is no episode boundary to apply
        # and the window is the whole of the constraint. A host that has been
        # offline since installation must still be able to gather its evidence.
        failed_in_window = db.execute(
            "SELECT COUNT(*) FROM runs WHERE internet_available = 0 AND timestamp_utc >= ?",
            (window_start,)).fetchone()[0]

    if last_online:
        first_failure_row = db.execute(
            "SELECT MIN(timestamp_utc) FROM runs WHERE internet_available = 0 "
            "AND timestamp_utc > ?", (last_online,)).fetchone()
    else:
        # No successful run has ever been recorded. The outage is as old as the
        # oldest failed observation, which is the honest reading for a host that
        # has been offline since the monitor was installed.
        first_failure_row = db.execute(
            "SELECT MIN(timestamp_utc) FROM runs WHERE internet_available = 0").fetchone()
    outage_started = first_failure_row[0] if first_failure_row else None

    minutes = 0.0
    if outage_started:
        try:
            minutes = (now - datetime.fromisoformat(outage_started)).total_seconds() / 60
        except (TypeError, ValueError):
            # A corrupt timestamp must not be read as a long outage. Treating it
            # as no evidence fails safe: the run records nothing and waits.
            minutes = 0.0
    return {"minutes": max(0.0, minutes), "failed_in_window": failed_in_window,
            "window": window, "required": required, "last_online": last_online}


def restart_is_warranted(evidence: dict, cfg: dict) -> bool:
    """True only when both the duration and the evidence conditions hold."""
    long_enough = evidence["minutes"] >= cfg["monitor"]["failure_minutes_before_reboot"]
    witnessed = evidence["failed_in_window"] >= evidence["required"]
    return long_enough and witnessed


def outage_description(evidence: dict) -> str:
    """State what was observed, rather than asserting the configured constant."""
    return (f"Internet was unavailable for {int(evidence['minutes'])} minutes, "
            f"across {evidence['failed_in_window']} failed checks in the last "
            f"{evidence['window']} minutes.")


def restart_bound(cfg: dict) -> tuple[int, int]:
    """How many restarts are allowed, and over how many hours."""
    monitor = cfg.get("monitor", {})
    return (positive_int(monitor.get("max_restarts_per_window"), MAX_RESTARTS_PER_WINDOW),
            positive_int(monitor.get("restart_window_hours"), RESTART_WINDOW_HOURS))


def restarts_this_episode(db, cfg: dict, evidence: dict) -> int:
    """Restart decisions taken since connectivity was last seen, inside the window.

    An outage upstream of the router is indistinguishable, from here, from one
    the router causes — and a restart cannot fix it. Without a bound the monitor
    restarts the router once per cooldown for as long as the operator's fault
    lasts, which is neither useful nor harmless.

    The count starts at the last successful run rather than at a fixed point in
    the past, so connectivity returning clears it: the bound is about one
    episode, not about the clock. The window is a second, outer limit, so a host
    that has never once been online cannot accumulate a count reaching back for
    ever.

    Dry-run decisions are counted alongside real restarts. A soak should show
    the operator exactly the behaviour production would have, including the
    point at which it gives up.
    """
    _, window_hours = restart_bound(cfg)
    since = utc_text(utc_now() - timedelta(hours=window_hours))
    if evidence.get("last_online") and evidence["last_online"] > since:
        since = evidence["last_online"]
    return db.execute(
        "SELECT COUNT(*) FROM events WHERE action IN ('Router restarted', 'Dry-run') "
        "AND timestamp_utc > ?", (since,)).fetchone()[0]


ROUTER_FINGERPRINT_KEY = "router_tls_fingerprint"
ROUTER_FINGERPRINT_SEEN_KEY = "router_tls_fingerprint_seen"


def router_fingerprint(cfg: dict, timeout: float = 10.0) -> str | None:
    """The SHA-256 of the certificate the router is presenting, or None.

    Standard library only and deliberately unvalidated, for the same reason
    `scripts/configure.py` does not validate it: a home router presents a
    self-signed certificate, and refusing it would push the operator to plain
    HTTP. The handshake proves the transport is encrypted and nothing else, so
    the fingerprint is what makes a change visible at all.
    """
    if not cfg.get("router", {}).get("use_tls", True):
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    host = cfg["router"]["host"]
    port = cfg["router"].get("management_port", cfg["router"].get("https_port", 8443))
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as secure:
                der = secure.getpeercert(binary_form=True)
                return hashlib.sha256(der).hexdigest() if der else None
    except (OSError, ssl.SSLError, ValueError):
        return None


def check_router_identity(db, cfg: dict) -> bool:
    """Compare the router's certificate with the one previously recorded.

    Trust on first use: the first fingerprint seen is recorded, and every later
    run compares against it. A change is reported and the run continues — a
    self-signed certificate that is simply renewed must not silence monitoring,
    and by the time this runs the alternative is leaving a router unrestarted
    during an outage. What it must not do is pass in silence.

    The configured value wins when there is one, because that is a deliberate
    statement by the operator; otherwise the recorded one does, so an install
    that predates this check still gains it without being reconfigured.
    """
    actual = router_fingerprint(cfg)
    if not actual:
        # Unreachable, or plain HTTP by explicit acknowledgement. Neither is
        # evidence of a changed identity, and neither is a reason to leave a
        # router unrestarted during an outage.
        return True
    expected = (cfg.get("router", {}).get("tls_fingerprint_sha256")
                or get_state(db, ROUTER_FINGERPRINT_KEY))
    set_state(db, ROUTER_FINGERPRINT_SEEN_KEY, actual)
    if not expected:
        set_state(db, ROUTER_FINGERPRINT_KEY, actual)
        db.commit()
        log_event(db, "Info", "Router certificate recorded",
                  f"First observation of the router's certificate: SHA-256 {actual}. "
                  "Later runs compare against it.")
        return True
    db.commit()
    if actual == expected:
        return True
    log_event(db, "Warning", "Router certificate changed",
              f"The router is presenting a different certificate. Recorded SHA-256 "
              f"{expected}, now {actual}. The restart proceeds, because a renewed "
              "self-signed certificate is the ordinary explanation, but nothing here "
              "verified either one. Confirm the router, then run configure.py "
              "--accept-router-certificate to record the new value.")
    return False


def notion_is_enabled(cfg: dict) -> bool:
    """Notion is an optional secondary log destination; SQLite is always primary."""
    return bool(cfg.get("notion", {}).get("enabled", False))


def delivery_is_configured(cfg: dict) -> bool:
    """Whether this installation can ever deliver an event to Notion.

    Delivery needs both a destination and production activation. An install
    that has enabled Notion but is still held in dry-run cannot deliver, so its
    events are neither a stalled queue nor rows worth keeping for ever. The
    predicate reads the configuration only, never the invocation flag, so a
    manual dry-run of a production install does not reclaim its pending events.
    """
    return notion_is_enabled(cfg) and cfg.get("execution_mode") == "execute"

def visible_reboot_notice(detail: str = ""):
    """Say what was observed. The threshold is configurable, so asserting a
    constant here can make the durable record of why a router was restarted
    wrong."""
    show_notice("Router Watchdog",
                detail or "Internet has been unavailable long enough to warrant a "
                          "restart. The router is now restarting.",
                LEVEL_WARNING)

def visible_recovery_notice():
    show_notice("Router Watchdog",
                "Internet connectivity has been restored after the router restart.",
                LEVEL_INFO)

def _spawn_notice(flag: str, detail: str = "") -> None:
    """Start a notice in its own process, detached and without a window.

    The notice runs in a child rather than inline because a message box is
    modal: shown from the monitoring run itself it would hold the process open
    until somebody dismissed it, and on a scheduled job with no visible desktop
    that is indefinitely. A failure to spawn is deliberately not fatal.
    """
    arguments = [windowless_interpreter(), __file__, flag]
    if detail:
        arguments += ["--detail", detail]
    spawn_detached(arguments)

def launch_reboot_notice(detail: str = "") -> None:
    _spawn_notice("--notice", detail)

def launch_recovery_notice() -> None:
    _spawn_notice("--recovery-notice")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--execute", action="store_true", help="Allows authorized router and Notion actions.")
    parser.add_argument("--notice", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recovery-notice", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--detail", default="", help=argparse.SUPPRESS)
    parser.add_argument("--reset-outbox-attempts", action="store_true",
                        help="Clear the retry bound on events that exhausted it, then exit without monitoring.")
    args = parser.parse_args()
    if args.notice:
        visible_reboot_notice(args.detail); return 0
    if args.recovery_notice:
        visible_recovery_notice(); return 0
    if args.config is None:
        raise RuntimeError("--config is required for a monitoring run.")
    require_persistent_secret_store(); cfg = load_config(args.config); validate_config(cfg); db = open_db(Path(cfg["paths"]["state_database"]))
    _, max_attempts, retention_days = outbox_settings(cfg)
    if args.reset_outbox_attempts:
        # This branch mutates the same rows a delivery run reads, so it takes
        # the lease as well. Unlike a monitoring run it says so out loud and
        # fails, because an operator running it by hand needs to know it did
        # nothing rather than to be told a silent zero.
        owner = lease_owner()
        if not acquire_run_lease(db, owner, run_lease_minutes(cfg)):
            print("A monitoring run is in progress; no outbox event was changed. Try again shortly.")
            return 1
        try:
            reset = reset_exhausted_events(db, max_attempts)
            print(f"Cleared the retry bound on {reset} outbox events.")
            return 0
        finally:
            release_run_lease(db, owner)
    # The lease is taken before any monitoring work and released in the finally
    # below, so an early return, a raised exception and a normal end all release
    # it on the same path.
    owner = lease_owner()
    if not acquire_run_lease(db, owner, run_lease_minutes(cfg)):
        # Another run is in progress. Skipping is the correct outcome, not an
        # error: the run that holds the lease is doing the work. The skip is
        # recorded so a lease that is taken every cycle is visible as evidence
        # rather than only as absent runs.
        # Counted as well as stamped. One skip is ordinary; a lease taken every
        # cycle means a run is leaking it, and the health monitor can only see
        # that if the number is recorded. The counter is cleared by the next run
        # that actually proceeds.
        skipped = positive_int(get_state(db, "consecutive_skipped_runs"), 0)
        set_state(db, "consecutive_skipped_runs", str(skipped + 1))
        set_state(db, "last_skipped_run_utc", utc_text()); db.commit()
        return 0
    try:
        if get_state(db, "consecutive_skipped_runs") not in (None, "0"):
            set_state(db, "consecutive_skipped_runs", "0"); db.commit()
        effective_mode = "execute" if args.execute and cfg["execution_mode"] == "execute" else "dry-run"
        online = internet_available(cfg["monitor"]["probe_urls"])
        log_run(db, online, effective_mode, cfg["execution_mode"])
        # Retention runs on every monitoring run, not only when remote delivery is
        # enabled, so the local event table cannot grow without bound in dry-run or
        # in an install that never configured Notion.
        purged = purge_events(db, retention_days, max_attempts, delivery_is_configured(cfg))
        if purged["undelivered"]:
            # Dropping evidence is worth a line of evidence. Aggregated, so a
            # large purge cannot itself flood the table it just trimmed.
            log_event(db, "Warning", "Outbox retention",
                      f"{purged['undelivered']} events passed the {retention_days}-day retention "
                      f"window without being delivered and were removed.")
        # first_failure_utc is no longer a decision input. It records only
        # whether the start of this outage has already been announced, so a
        # marker left behind by a partial failure can cause at most a duplicate
        # log line, never a restart. That is what closes F8.
        first_failure = get_state(db, "first_failure_utc")
        if online:
            if get_state(db, "pending_recovery_notification"):
                launch_recovery_notice()
                set_state(db, "pending_recovery_notification", "")
            # Cleared before the work that can fail, and committed with the
            # event, so an exception in delivery cannot leave a half-recorded
            # recovery behind.
            set_state(db, "first_failure_utc", "")
            if first_failure:
                log_event(db, "Online", "Restored", "Internet connectivity has been restored.")
            db.commit()
            if effective_mode == "execute" and notion_is_enabled(cfg):
                deliver_outbox(db, cfg)
            return 0
        if not first_failure:
            detail = "All external HTTPS probes failed."
            set_state(db, "first_failure_utc", utc_text()); log_event(db, "Offline", "Monitoring started", detail)
            return 0
        evidence = outage_evidence(db, cfg)
        if not restart_is_warranted(evidence, cfg): return 0
        last_attempt = get_state(db, "last_reboot_attempt_utc")
        if last_attempt and utc_now() - datetime.fromisoformat(last_attempt) < timedelta(minutes=cfg["monitor"]["reboot_cooldown_minutes"]): return 0
        # The bound on repetition. An outage a restart cannot fix looks exactly
        # like one it can, so without this the router is restarted once per
        # cooldown for as long as an upstream fault lasts. Announced once per
        # episode, because the point is to be noticed, not to fill the log.
        allowed, window_hours = restart_bound(cfg)
        already = restarts_this_episode(db, cfg, evidence)
        if already >= allowed:
            if get_state(db, "restart_bound_reached_utc") != str(evidence.get("last_online") or ""):
                set_state(db, "restart_bound_reached_utc", str(evidence.get("last_online") or ""))
                log_event(db, "Warning", "Restart bound reached",
                          f"{already} restarts have already been decided since connectivity "
                          f"was last seen, which is the limit of {allowed} in "
                          f"{window_hours} hours. No further restart will be attempted "
                          f"until the connection returns. {outage_description(evidence)} "
                          "An outage upstream of the router cannot be fixed by restarting it.")
                launch_reboot_notice(
                    f"The Internet has been down for {int(evidence['minutes'])} minutes and "
                    f"{already} restarts have not helped. No further restart will be "
                    "attempted until the connection returns. This usually means the fault "
                    "is with the operator rather than the router.")
            return 0
        if effective_mode != "execute":
            set_state(db, "last_reboot_attempt_utc", utc_text())
            log_event(db, "Offline", "Dry-run",
                      f"Restart condition met; no external action in dry-run. {outage_description(evidence)}")
            return 0
        set_state(db, "last_reboot_attempt_utc", utc_text()); db.commit()
        launch_reboot_notice(outage_description(evidence))
        # Checked here, immediately before the credentials are sent, because
        # that is the moment a substituted endpoint would matter. It reports and
        # continues; leaving a router unrestarted during an outage on the
        # evidence of a renewed self-signed certificate would be the worse call.
        check_router_identity(db, cfg)
        try:
            if not asyncio.run(reboot_router(cfg)): raise RuntimeError("The router rejected the restart request.")
        except Exception as error:
            # Sanitized: this is the one error that is both persisted AND
            # delivered to Notion, and it comes from a call constructed with the
            # router credentials.
            log_event(db, "Error", "Restart failed", sanitize_error(error)); return 1
        set_state(db, "last_reboot_utc", utc_text()); set_state(db, "pending_recovery_notification", "1"); set_state(db, "first_failure_utc", ""); log_event(db, "Restarted", "Router restarted", outage_description(evidence))
        if notion_is_enabled(cfg):
            deliver_outbox(db, cfg)
        return 0
    finally:
        release_run_lease(db, owner)


if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as error:
        write_bootstrap_error(error)
        print(f"ERROR: {error}", file=sys.stderr); raise SystemExit(1)
