# ZenWiFi Monitor version: 0.1.0
"""Smoke test for the local health monitor.

The health monitor must never restart the router and must stay silent while
healthy. This test runs the real evaluation path against a temporary database.
"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
health = importlib.util.module_from_spec(importlib.util.spec_from_file_location("health", SRC / "health.py"))
importlib.util.spec_from_file_location("health", SRC / "health.py").loader.exec_module(health)
watchdog = importlib.util.module_from_spec(importlib.util.spec_from_file_location("watchdog", SRC / "watchdog.py"))
importlib.util.spec_from_file_location("watchdog", SRC / "watchdog.py").loader.exec_module(watchdog)

results = []


def record(name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(f"{name}: {detail}")


record("the health monitor imports no third-party dependency",
       "keyring" not in (SRC / "health.py").read_text(encoding="utf-8")
       and "asusrouter" not in (SRC / "health.py").read_text(encoding="utf-8"))
record("the health monitor cannot restart the router",
       "reboot" not in (SRC / "health.py").read_text(encoding="utf-8").lower())

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp); database = root / "state.sqlite3"
    db = watchdog.open_db(database)
    watchdog.log_run(db, True, "dry-run", "dry-run")
    db.commit(); db.close()

    cfg = {"paths": {"state_database": str(database), "log_directory": str(root)},
           "health": {"run_age_minutes": 15, "outbox_age_minutes": 180}}

    db = health.open_db(database)
    record("a fresh run is healthy", health.evaluate(db, cfg) == [], f"findings={health.evaluate(db, cfg)}")

    # Age the newest run past the threshold.
    stale = watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=45))
    db.execute("UPDATE runs SET timestamp_utc=?", (stale,)); db.commit()
    findings = health.evaluate(db, cfg)
    record("a stale run is reported", len(findings) == 1 and "minutes old" in findings[0], f"findings={findings}")

    # An old undelivered event is reported too.
    db.execute("INSERT INTO events(timestamp_utc,status,action,detail) VALUES(?,?,?,?)",
               (watchdog.utc_text(watchdog.utc_now() - timedelta(hours=6)), "Offline", "Monitoring started", "x"))
    db.commit()
    findings = health.evaluate(db, cfg)
    record("a stalled outbox is reported", any("undelivered" in item for item in findings), f"findings={findings}")
    db.close()

    # The full run records state, writes the health log, and returns non-zero when unhealthy.
    config_path = root / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    notices = []
    health.launch_health_notice = lambda detail: notices.append(detail)
    argv = sys.argv
    try:
        sys.argv = ["health.py", "--config", str(config_path)]
        code = health.main()
    finally:
        sys.argv = argv
    record("an unhealthy check exits 1", code == 1, f"exit was {code}")
    record("an escalation notifies exactly once", len(notices) == 1, f"notices={notices}")
    record("the health log is written", (root / "health.log").is_file())
    connection = sqlite3.connect(database)
    row = connection.execute("SELECT state, severity FROM health ORDER BY id DESC LIMIT 1").fetchone()
    connection.close()
    record("the health state is persisted", row[0] == "unhealthy" and row[1] >= 1, f"row={row}")

    # Repeating the same unhealthy state must not notify again.
    argv = sys.argv
    try:
        sys.argv = ["health.py", "--config", str(config_path)]
        health.main()
    finally:
        sys.argv = argv
    record("a steady unhealthy state does not renotify", len(notices) == 1, f"notices={notices}")

print(json.dumps({"suite": "health", "checks": len(results), "failures": 0,
                  "result": "green", "findings": results}, indent=2))
print("health monitor smoke test: OK")
