# ZenWiFi Monitor version: 0.1.0
"""Smoke test for the local health monitor.

The health monitor must never restart the router and must stay silent while
healthy. This test runs the real evaluation path against a temporary database.

Every path the monitor touches is redirected into the temporary directory, so
the suite never reads or writes real machine state and cannot race the live
scheduled job. Temporary directories tolerate cleanup errors because SQLite on
Windows can hold the write-ahead log briefly after the last connection closes;
that is a teardown artefact and never affects an assertion.
"""
import gc
import importlib.util
import json
import sqlite3
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


health = load("health")
watchdog = load("watchdog")
results = []


def record(name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(f"{name}: {detail}")


def codes(findings):
    return sorted(code for code, _ in findings)


def messages(findings):
    return " ".join(message for _, message in findings)


source = (SRC / "health.py").read_text(encoding="utf-8")
record("the health monitor imports no third-party dependency",
       "keyring" not in source and "asusrouter" not in source)
record("the health monitor cannot restart the router", "reboot" not in source.lower())

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    # Redirect every real-machine path into the temporary directory.
    health.bootstrap_log_path = lambda: root / "bootstrap-errors.log"
    health.notification_stamp_path = lambda: root / "health-notified.txt"

    database = root / "state.sqlite3"
    db = watchdog.open_db(database)
    watchdog.log_run(db, True, "dry-run", "dry-run")
    db.commit(); db.close()

    cfg = {"paths": {"state_database": str(database), "log_directory": str(root)},
           "health": {"run_age_minutes": 15, "outbox_age_minutes": 180},
           "notion": {"enabled": True}}

    db = health.open_db(database)
    record("a fresh run is healthy", health.evaluate(db, cfg) == [], f"findings={health.evaluate(db, cfg)}")

    stale = watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=45))
    db.execute("UPDATE runs SET timestamp_utc=?", (stale,)); db.commit()
    record("a stale run is reported", codes(health.evaluate(db, cfg)) == ["stale-run"],
           f"codes={codes(health.evaluate(db, cfg))}")

    db.execute("INSERT INTO events(timestamp_utc,status,action,detail) VALUES(?,?,?,?)",
               (watchdog.utc_text(watchdog.utc_now() - timedelta(hours=6)), "Offline", "Monitoring started", "x"))
    db.commit()
    record("a stalled outbox is reported", "outbox-stalled" in codes(health.evaluate(db, cfg)),
           f"codes={codes(health.evaluate(db, cfg))}")

    db.execute("UPDATE events SET delivery_attempts=99"); db.commit()
    found = health.evaluate(db, cfg)
    record("an exhausted event is not counted as waiting", "outbox-stalled" not in codes(found), f"codes={codes(found)}")
    record("an exhausted event is reported with a remedy", "--reset-outbox-attempts" in messages(found))

    db.execute("UPDATE events SET delivery_attempts=0"); db.commit()
    without_notion = dict(cfg); without_notion["notion"] = {"enabled": False}
    record("no stalled queue is reported when Notion is disabled",
           "outbox-stalled" not in codes(health.evaluate(db, without_notion)),
           f"codes={codes(health.evaluate(db, without_notion))}")
    db.execute("DELETE FROM events"); db.commit()
    db.close()

    config_path = root / "config.json"; config_path.write_text(json.dumps(cfg), encoding="utf-8")
    notices = []
    health.launch_health_notice = lambda detail: notices.append(detail)

    def run_health():
        argv = sys.argv
        try:
            sys.argv = ["health.py", "--config", str(config_path)]
            return health.main()
        finally:
            sys.argv = argv

    code = run_health()
    record("an unhealthy check exits 1", code == 1, f"exit was {code}")
    record("an escalation notifies exactly once", len(notices) == 1, f"notices={len(notices)}")
    record("the health log is written", (root / "health.log").is_file())
    connection = sqlite3.connect(database)
    row = connection.execute("SELECT state, severity FROM health ORDER BY id DESC LIMIT 1").fetchone()
    connection.close()
    record("the health state is persisted", row == ("unhealthy", 1), f"row={row}")

    run_health()
    record("a steady unhealthy state does not renotify", len(notices) == 1, f"notices={len(notices)}")

    # A different condition at the same severity is still an escalation.
    connection = sqlite3.connect(database)
    connection.execute("UPDATE runs SET timestamp_utc=?", (watchdog.utc_text(),))
    connection.execute("INSERT INTO events(timestamp_utc,status,action,detail,delivery_attempts) "
                       "VALUES(?,?,?,?,?)", (watchdog.utc_text(), "Error", "Poison", "x", 99))
    connection.commit(); connection.close()
    run_health()
    record("a different condition at equal severity notifies", len(notices) == 2, f"notices={len(notices)}")
    connection = sqlite3.connect(database)
    row = connection.execute("SELECT state, severity FROM health ORDER BY id DESC LIMIT 1").fetchone()
    connection.close()
    record("the replacement condition is recorded at equal severity", row == ("unhealthy", 1), f"row={row}")
    gc.collect()

# An unreadable database is the condition this monitor exists to report.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    health.bootstrap_log_path = lambda: root / "bootstrap-errors.log"
    health.notification_stamp_path = lambda: root / "health-notified.txt"
    database = root / "corrupt.sqlite3"
    database.write_bytes(b"this is not a database" * 64)
    config_path = root / "config.json"
    config_path.write_text(json.dumps({"paths": {"state_database": str(database),
                                                 "log_directory": str(root)}}), encoding="utf-8")
    notices = []
    health.launch_health_notice = lambda detail: notices.append(detail)
    argv = sys.argv
    try:
        sys.argv = ["health.py", "--config", str(config_path)]
        code = health.main()
    finally:
        sys.argv = argv
    record("a corrupt database exits 1 rather than dying silently", code == 1, f"exit was {code}")
    record("a corrupt database raises a notification", len(notices) == 1, f"notices={len(notices)}")
    record("the corrupt-database finding names the condition",
           "could not be opened or read" in notices[0], f"detail={notices[0]!r}")
    gc.collect()

# A missing database must still notify once, not on every single run.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    health.bootstrap_log_path = lambda: root / "bootstrap-errors.log"
    health.notification_stamp_path = lambda: root / "health-notified.txt"
    config_path = root / "config.json"
    config_path.write_text(json.dumps({"paths": {"state_database": str(root / "absent.sqlite3"),
                                                 "log_directory": str(root)}}), encoding="utf-8")
    notices = []
    health.launch_health_notice = lambda detail: notices.append(detail)
    for _ in range(3):
        argv = sys.argv
        try:
            sys.argv = ["health.py", "--config", str(config_path)]
            code = health.main()
        finally:
            sys.argv = argv
    record("a missing database exits 1", code == 1, f"exit was {code}")
    record("a missing database notifies once, not once per run", len(notices) == 1, f"notices={len(notices)}")
    gc.collect()

print(json.dumps({"suite": "health", "checks": len(results), "failures": 0,
                  "result": "green", "findings": results}, indent=2))
print("health monitor smoke test: OK")
