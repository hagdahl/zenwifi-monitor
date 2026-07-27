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
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _logrotate import append_log, bootstrap_log_path  # noqa: E402

DEFAULT_RUN_AGE_MINUTES = 15
DEFAULT_OUTBOX_AGE_MINUTES = 180
DEFAULT_MAX_ATTEMPTS = 5
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


def read_notification_stamp() -> str:
    try:
        return notification_stamp_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_notification_stamp(signature: str) -> None:
    path = notification_stamp_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(signature, encoding="utf-8")
    except OSError:
        pass


def check_recent_run(db, threshold_minutes: int) -> list[str]:
    row = db.execute("SELECT timestamp_utc FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return ["The runs table holds no monitoring run at all."]
    age = utc_now() - datetime.fromisoformat(row[0])
    if age > timedelta(minutes=threshold_minutes):
        return [f"The newest monitoring run is {int(age.total_seconds() // 60)} minutes old; "
                f"the threshold is {threshold_minutes}."]
    return []


def check_outbox(db, age_minutes: int, max_attempts: int) -> list[str]:
    """Report a stalled queue and an exhausted queue as separate conditions.

    An exhausted event is never retried, so counting it as a waiting event
    would hold the health state unhealthy with no action available.
    """
    findings = []
    row = db.execute("SELECT COUNT(*), MIN(timestamp_utc) FROM events "
                     "WHERE delivered_to_notion=0 AND delivery_attempts < ?", (max_attempts,)).fetchone()
    if row and row[0]:
        age = utc_now() - datetime.fromisoformat(row[1])
        if age > timedelta(minutes=age_minutes):
            findings.append(f"{row[0]} deliverable events have waited undelivered for "
                            f"{int(age.total_seconds() // 60)} minutes.")
    exhausted = db.execute("SELECT COUNT(*) FROM events "
                           "WHERE delivered_to_notion=0 AND delivery_attempts >= ?",
                           (max_attempts,)).fetchone()[0]
    if exhausted:
        findings.append(f"{exhausted} events exhausted their delivery retries. Fix the remote destination, "
                        "then run the watchdog once with --reset-outbox-attempts.")
    return findings


def check_bootstrap_log(db) -> list[str]:
    path = bootstrap_log_path()
    if not path.is_file():
        return []
    stamp = f"{path.stat().st_mtime_ns}:{path.stat().st_size}"
    previous = get_state(db, "health_bootstrap_stamp")
    set_state(db, "health_bootstrap_stamp", stamp)
    if previous is not None and previous != stamp:
        return [f"The bootstrap error log changed since the previous health check: {path.name}"]
    return []


def evaluate(db, cfg: dict) -> list[str]:
    health_cfg = cfg.get("health", {}) if isinstance(cfg.get("health"), dict) else {}
    outbox_cfg = cfg.get("outbox", {}) if isinstance(cfg.get("outbox"), dict) else {}
    run_threshold = health_cfg.get("run_age_minutes", DEFAULT_RUN_AGE_MINUTES)
    outbox_threshold = health_cfg.get("outbox_age_minutes", DEFAULT_OUTBOX_AGE_MINUTES)
    max_attempts = int(outbox_cfg.get("max_attempts_per_event", DEFAULT_MAX_ATTEMPTS))
    findings = []
    for check, arguments in ((check_recent_run, (run_threshold,)),
                             (check_outbox, (outbox_threshold, max_attempts))):
        try:
            findings.extend(check(db, *arguments))
        except sqlite3.Error as error:
            findings.append(f"{check.__name__} could not read the local database: {error}")
    try:
        findings.extend(check_bootstrap_log(db))
    except OSError as error:
        findings.append(f"The bootstrap error log could not be inspected: {error}")
    return findings


def visible_health_notice(detail: str) -> None:
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, detail[:900], "Router Watchdog health", 0x30)


def launch_health_notice(detail: str) -> None:
    """Start the notice windowless so the health job itself stays silent."""
    windowless = Path(sys.executable).with_name("pythonw.exe")
    interpreter = str(windowless) if windowless.is_file() else sys.executable
    subprocess.Popen([interpreter, __file__, "--notice", "--detail", detail], close_fds=True,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


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
        findings.append(f"The local state database is missing at the configured path: {database.name}")
        db = None
    else:
        db = open_db(database)
        findings = evaluate(db, cfg)

    state = "unhealthy" if findings else "healthy"
    severity = len(findings)
    detail = " ".join(findings) if findings else "All local health checks passed."

    if db is not None:
        db.execute("INSERT INTO health(timestamp_utc,state,severity,detail) VALUES(?,?,?,?)",
                   (utc_text(), state, severity, detail[:1900]))
        previous_state = get_state(db, "health_last_state") or "healthy"
        previous_severity = int(get_state(db, "health_last_severity") or 0)
        set_state(db, "health_last_state", state)
        set_state(db, "health_last_severity", str(severity))
        db.commit()
    else:
        # Without the database the stamp file is the only memory of the last
        # notification, so a missing database still notifies once, not every run.
        stamp = read_notification_stamp().split(":")
        previous_state = stamp[0] if stamp and stamp[0] else "healthy"
        previous_severity = int(stamp[1]) if len(stamp) > 1 and stamp[1].isdigit() else 0
    escalating = state == "unhealthy" and (previous_state == "healthy" or severity > previous_severity)
    write_notification_stamp(f"{state}:{severity}")

    append_log(log_directory / HEALTH_LOG_NAME, f"{utc_text()} {state} severity={severity} {detail}")
    if escalating:
        launch_health_notice(detail)
    if args.as_json:
        print(json.dumps({"state": state, "severity": severity, "findings": findings,
                          "notified": escalating}, indent=2, sort_keys=True))
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
