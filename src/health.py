"""Local health monitor for ZenWiFi Monitor. It never restarts the router.

This script is deliberately standard-library only. A health check that
imported the project's third-party dependencies would share the fate of the
very environment it is meant to observe.

Normal operation is silent. A persistent notification is raised only when the
health state becomes unhealthy or escalates, and the alert is cleared once
healthy runs resume.
"""
# ZenWiFi Monitor version: 0.1.0
import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _defaults import (MAX_ATTEMPTS_PER_EVENT, NOTICE_COOLDOWN_MINUTES,  # noqa: E402
                       OUTBOX_AGE_MINUTES, RUN_AGE_MINUTES)
from _logrotate import append_log, bootstrap_log_path  # noqa: E402
from _platform import LEVEL_WARNING, show_notice, spawn_detached, windowless_interpreter  # noqa: E402

HEALTH_LOG_NAME = "health.log"


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()



def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def open_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE IF NOT EXISTS health (id INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL, "
               "state TEXT NOT NULL, severity INTEGER NOT NULL, detail TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return db


def get_state(db, key):
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_state(db, key, value) -> None:
    db.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))


def notification_stamp_path() -> Path:
    """A last-notified marker that survives the local database being absent."""
    return bootstrap_log_path().with_name("health-notified.txt")


def read_notification_stamp() -> dict:
    """Read the last recorded state. An unreadable or corrupt stamp reads as unknown."""
    try:
        value = json.loads(notification_stamp_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_notification_stamp(state: str, severity: int, codes: list[str],
                             last_notice_utc: str | None, notified_codes: list[str]) -> bool:
    """Record the last state and notification. Returns False when it cannot be written."""
    path = notification_stamp_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"state": state, "severity": severity, "codes": codes,
                                    "last_notice_utc": last_notice_utc,
                                    "notified_codes": notified_codes}), encoding="utf-8")
        return True
    except OSError:
        return False


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


def check_recent_run(db, threshold_minutes: int) -> list[tuple[str, str]]:
    row = db.execute("SELECT timestamp_utc FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return [("no-runs", "The runs table holds no monitoring run at all.")]
    age = utc_now() - datetime.fromisoformat(row[0])
    if age > timedelta(minutes=threshold_minutes):
        return [("stale-run", f"The newest monitoring run is {int(age.total_seconds() // 60)} minutes old; "
                              f"the threshold is {threshold_minutes}.")]
    return []


def check_outbox(db, age_minutes: int, max_attempts: int,
                 delivery_configured: bool = True) -> list[tuple[str, str]]:
    """Report a stalled queue and an exhausted queue as separate conditions.

    An exhausted event is never retried, so counting it as a waiting event
    would hold the health state unhealthy with no action available. When no
    remote destination is configured there is no queue to stall either: such
    events are local records awaiting retention, not a fault.
    """
    findings = []
    row = db.execute("SELECT COUNT(*), MIN(timestamp_utc) FROM events "
                     "WHERE delivered_to_notion=0 AND delivery_attempts < ?", (max_attempts,)).fetchone()
    if delivery_configured and row and row[0]:
        age = utc_now() - datetime.fromisoformat(row[1])
        if age > timedelta(minutes=age_minutes):
            findings.append(("outbox-stalled", f"{row[0]} deliverable events have waited undelivered for "
                                               f"{int(age.total_seconds() // 60)} minutes."))
    exhausted = db.execute("SELECT COUNT(*) FROM events "
                           "WHERE delivered_to_notion=0 AND delivery_attempts >= ?",
                           (max_attempts,)).fetchone()[0]
    if exhausted:
        findings.append(("outbox-exhausted",
                         f"{exhausted} events exhausted their delivery retries. Fix the remote destination, "
                         "then run the watchdog once with --reset-outbox-attempts."))
    return findings


def check_bootstrap_log(db) -> list[tuple[str, str]]:
    path = bootstrap_log_path()
    if not path.is_file():
        return []
    stamp = f"{path.stat().st_mtime_ns}:{path.stat().st_size}"
    previous = get_state(db, "health_bootstrap_stamp")
    set_state(db, "health_bootstrap_stamp", stamp)
    if previous is not None and previous != stamp:
        return [("bootstrap-changed",
                 f"The bootstrap error log changed since the previous health check: {path.name}")]
    return []


def evaluate(db, cfg: dict) -> list[tuple[str, str]]:
    health_cfg = cfg.get("health", {}) if isinstance(cfg.get("health"), dict) else {}
    outbox_cfg = cfg.get("outbox", {}) if isinstance(cfg.get("outbox"), dict) else {}
    run_threshold = positive_int(health_cfg.get("run_age_minutes"), RUN_AGE_MINUTES)
    outbox_threshold = positive_int(health_cfg.get("outbox_age_minutes"), OUTBOX_AGE_MINUTES)
    max_attempts = positive_int(outbox_cfg.get("max_attempts_per_event"), MAX_ATTEMPTS_PER_EVENT)
    # Delivery needs both a destination and production activation, so an install
    # still held in dry-run has no queue to stall.
    delivery_configured = (bool(cfg.get("notion", {}).get("enabled", False))
                           if isinstance(cfg.get("notion"), dict) else False) \
        and cfg.get("execution_mode") == "execute"
    findings = []
    for check, arguments in ((check_recent_run, (run_threshold,)),
                             (check_outbox, (outbox_threshold, max_attempts, delivery_configured))):
        try:
            findings.extend(check(db, *arguments))
        except sqlite3.Error as error:
            findings.append(("db-unreadable",
                             f"{check.__name__} could not read the local database: {type(error).__name__}."))
    try:
        findings.extend(check_bootstrap_log(db))
    except OSError as error:
        findings.append(("bootstrap-unreadable",
                         f"The bootstrap error log could not be inspected: {type(error).__name__}."))
    return findings


def visible_health_notice(detail: str) -> None:
    show_notice("Router Watchdog health", detail, LEVEL_WARNING)


def launch_health_notice(detail: str) -> None:
    """Start the notice detached and windowless so the health job stays silent.

    The notice runs in a child rather than inline because a message box is
    modal: shown from the health run itself it would hold the process open
    until dismissed, and a scheduled job with no visible desktop would then
    never exit. That is the failure mode this monitor exists to report, so it
    must not reproduce it.
    """
    spawn_detached([windowless_interpreter(), __file__, "--notice", "--detail", detail])


def main() -> int:
    parser = argparse.ArgumentParser(description="Report local ZenWiFi Monitor health. Never restarts the router.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--notice", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--detail", default="", help=argparse.SUPPRESS)
    parser.add_argument("--execute", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.notice:
        visible_health_notice(args.detail or "The local watchdog health check reported a problem.")
        return 0
    if args.config is None:
        raise RuntimeError("--config is required for a health check.")

    cfg = load_config(args.config)
    database = Path(cfg["paths"]["state_database"])
    log_directory = Path(cfg["paths"].get("log_directory", database.parent))
    findings = []
    if not database.is_file():
        findings.append(("db-missing",
                         f"The local state database is missing at the configured path: {database.name}"))
        db = None
    else:
        try:
            db = open_db(database)
            findings = evaluate(db, cfg)
        except sqlite3.Error as error:
            # A locked or corrupt database is exactly the condition this monitor
            # exists to report, so it must reach the notification path.
            findings = [("db-unreadable",
                         f"The local state database could not be opened or read: {type(error).__name__}.")]
            db = None

    state = "unhealthy" if findings else "healthy"
    severity = len(findings)
    # Deduplicated, so two findings of the same kind do not read as a change.
    codes = sorted({code for code, _ in findings})
    detail = " ".join(message for _, message in findings) if findings else "All local health checks passed."
    health_cfg = cfg.get("health", {}) if isinstance(cfg.get("health"), dict) else {}
    cooldown = positive_int(health_cfg.get("notice_cooldown_minutes"), NOTICE_COOLDOWN_MINUTES)

    if db is not None:
        try:
            db.execute("INSERT INTO health(timestamp_utc,state,severity,detail) VALUES(?,?,?,?)",
                       (utc_text(), state, severity, detail[:1900]))
            db.commit()
            previous_state = get_state(db, "health_last_state") or "healthy"
            previous_codes = [code for code in (get_state(db, "health_last_codes") or "").split(",") if code]
            last_notice = get_state(db, "health_last_notice_utc")
            notified_codes = [code for code in (get_state(db, "health_notified_codes") or "").split(",") if code]
        except sqlite3.Error as error:
            # A lock taken between the open and the write, or a full disk, is a
            # real fault. It must be announced, not exit the run silently.
            state = "unhealthy"
            codes = sorted(set(codes) | {"health-store-unwritable"})
            severity = len(codes)
            detail = f"{detail} The health record could not be written: {type(error).__name__}."
            db = None
    if db is None:
        # Without the database the stamp file is the only memory of the last
        # notification, so a missing database still notifies once, not every run.
        stamp = read_notification_stamp()
        previous_state = stamp.get("state") or "healthy"
        previous_codes = stamp.get("codes") or []
        last_notice = stamp.get("last_notice_utc")
        notified_codes = stamp.get("notified_codes") or []

    # Only a condition that was not already present is an escalation, so
    # recovering from two conditions to one is not announced as a new problem.
    appeared = set(codes) - set(previous_codes)
    # A condition never announced before is worth showing at once. One that has
    # been announced and is coming back is damped, so an intermittent fault
    # beside a persistent one cannot raise a dialog on every toggle.
    novel = appeared - set(notified_codes)
    returning = appeared & set(notified_codes)
    if last_notice:
        try:
            due = utc_now() - datetime.fromisoformat(last_notice) >= timedelta(minutes=cooldown)
        except ValueError:
            due = True
    else:
        due = True
    escalating = state == "unhealthy" and (previous_state == "healthy" or bool(novel)
                                           or (bool(returning) and due))
    notice_stamp = utc_text() if escalating else last_notice
    if state == "healthy":
        notified_codes = []
    elif escalating:
        notified_codes = sorted(set(notified_codes) | set(codes))

    if db is not None:
        try:
            set_state(db, "health_last_state", state)
            set_state(db, "health_last_codes", ",".join(codes))
            if notice_stamp:
                set_state(db, "health_last_notice_utc", notice_stamp)
            set_state(db, "health_notified_codes", ",".join(notified_codes))
            db.commit()
        except sqlite3.Error as error:
            # The findings and the notification decision are already made at
            # this point, so a write that fails here must not end the run
            # before the notice. Announce the fault instead of swallowing it.
            state = "unhealthy"
            detail = f"{detail} The health state could not be recorded: {type(error).__name__}."
            escalating = True
            try:
                db.close()
            except sqlite3.Error:
                pass
            db = None
    if not write_notification_stamp(state, severity, codes, notice_stamp, notified_codes) and db is None:
        # Without a database and without a stamp there is no memory of the last
        # notification, so say so rather than silently notifying on every run.
        detail = f"{detail} The notification stamp could not be written, so repeat notices are possible."

    try:
        append_log(log_directory / HEALTH_LOG_NAME, f"{utc_text()} {state} severity={severity} {detail}")
    except OSError as error:
        # The notification matters more than the log line, so a log directory
        # that cannot be written is reported rather than allowed to end the run.
        # An unattended job that cannot record its own evidence is itself a
        # fault, so the run reports unhealthy and exits non-zero.
        state = "unhealthy"
        detail = f"{detail} The health log could not be written: {type(error).__name__}."
        escalating = True
    if escalating:
        launch_health_notice(detail)
    if args.as_json:
        print(json.dumps({"state": state, "severity": severity, "notified": escalating,
                          "findings": [{"code": code, "message": message} for code, message in findings]},
                         indent=2, sort_keys=True))
    if db is not None:
        db.close()
    return 0 if state == "healthy" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        append_log(bootstrap_log_path(), f"{utc_text()} health {type(error).__name__}: {error}")
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
