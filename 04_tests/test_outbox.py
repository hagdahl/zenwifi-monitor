# ZenWiFi Monitor version: 0.1.0
"""Smoke test for the durable Notion outbox and bootstrap log rotation.

Runs the real delivery code path with the remote call replaced by a counting
stub, so no network request is ever made. Emits a machine-readable result.
"""
import gc
import importlib.util
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
spec = importlib.util.spec_from_file_location("watchdog", SRC / "watchdog.py")
watchdog = importlib.util.module_from_spec(spec); spec.loader.exec_module(watchdog)

results = []


def record(name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(f"{name}: {detail}")


CFG = {"notion": {"enabled": True, "data_source_id": "d", "api_version": "v"},
       "outbox": {"max_deliveries_per_run": 20, "max_attempts_per_event": 3, "retention_days": 30}}

with tempfile.TemporaryDirectory() as temp:
    db = watchdog.open_db(Path(temp) / "state.sqlite3")

    # An outage records events locally with no delivery attempted.
    offline_id = watchdog.log_event(db, "Offline", "Monitoring started", "All external HTTPS probes failed.")
    watchdog.log_event(db, "Online", "Restored", "Internet connectivity has been restored.")
    row = db.execute("SELECT delivered_to_notion, delivery_attempts FROM events WHERE id=?", (offline_id,)).fetchone()
    record("offline event is stored undelivered", row == (0, 0), f"row was {row}")

    # Delivery fails: nothing is marked delivered, the attempt and a sanitized error are recorded.
    calls = []
    def failing(cfg, status, action, detail):
        calls.append(status)
        raise RuntimeError("HTTP Error 401: Unauthorized for Bearer redact-me-0000")
    watchdog.notion_event = failing
    outcome = watchdog.deliver_outbox(db, CFG)
    record("delivery stops on the first failure", outcome["delivered"] == 0 and len(calls) == 1,
           f"outcome={outcome} calls={calls}")
    record("both events remain pending", outcome["pending"] == 2, f"outcome={outcome}")
    stored = db.execute("SELECT delivery_attempts, last_delivery_error FROM events WHERE id=?", (offline_id,)).fetchone()
    record("the attempt is counted", stored[0] == 1, f"stored={stored}")
    record("no token is persisted in the error detail",
           "redact-me-0000" not in stored[1] and "<redacted>" in stored[1], f"stored error was {stored[1]!r}")

    # Delivery succeeds: oldest first, at least once, and a second run re-sends nothing.
    delivered_order = []
    watchdog.notion_event = lambda cfg, status, action, detail: delivered_order.append(status)
    outcome = watchdog.deliver_outbox(db, CFG)
    record("queued events are delivered oldest first",
           delivered_order == ["Offline", "Online"], f"order was {delivered_order}")
    record("the queue drains", outcome["delivered"] == 2 and outcome["pending"] == 0, f"outcome={outcome}")
    again = watchdog.deliver_outbox(db, CFG)
    record("a second run delivers nothing again",
           again["delivered"] == 0 and delivered_order == ["Offline", "Online"], f"order was {delivered_order}")

    # A permanently failing event stops consuming the queue once attempts are exhausted.
    watchdog.log_event(db, "Error", "Poison", "always fails")
    def always_fail(cfg, status, action, detail):
        raise RuntimeError("HTTP Error 400: Bad Request")
    watchdog.notion_event = always_fail
    for _ in range(5):
        outcome = watchdog.deliver_outbox(db, CFG)
    record("a poison event is retried only up to the bound",
           outcome["exhausted"] == 1 and outcome["delivered"] == 0, f"outcome={outcome}")
    attempts = db.execute("SELECT MAX(delivery_attempts) FROM events").fetchone()[0]
    record("attempts never exceed the configured bound", attempts == 3, f"attempts={attempts}")

    # An exhausted event can be released again by the operator escape hatch.
    released = watchdog.reset_exhausted_events(db, 3)
    still_exhausted = db.execute("SELECT COUNT(*) FROM events WHERE delivered_to_notion=0 AND delivery_attempts >= 3").fetchone()[0]
    record("exhausted events can be released for another attempt",
           released == 1 and still_exhausted == 0, f"released={released} still_exhausted={still_exhausted}")
    for _ in range(3):
        watchdog.deliver_outbox(db, CFG)

    # Retention removes delivered events and abandoned ones, but never a fresh
    # undelivered event that has attempts left.
    aged = watchdog.utc_text(watchdog.utc_now() - timedelta(days=90))
    db.execute("UPDATE events SET timestamp_utc=?", (aged,)); db.commit()
    fresh = watchdog.log_event(db, "Offline", "Fresh", "still deliverable")
    purged = watchdog.purge_events(db, 30, 3)
    remaining = [row[0] for row in db.execute("SELECT id FROM events")]
    record("retention purges delivered and abandoned events",
           purged["delivered"] == 2 and purged["abandoned"] == 1, f"purged={purged}")
    record("a fresh deliverable event survives retention", remaining == [fresh],
           f"remaining ids={remaining}, fresh={fresh}")

    # With no remote destination an event can never be delivered, so retention
    # must reclaim it or the table grows for ever in a supported configuration.
    db.execute("UPDATE events SET timestamp_utc=?", (aged,)); db.commit()
    kept = watchdog.purge_events(db, 30, 3, notion_enabled=True)
    still = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    record("with Notion enabled a never-attempted event is kept", still == 1, f"purged={kept} remaining={still}")
    dropped = watchdog.purge_events(db, 30, 3, notion_enabled=False)
    still = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    record("with Notion disabled retention reclaims it", dropped["abandoned"] == 1 and still == 0,
           f"purged={dropped} remaining={still}")
    db.close()

# Bootstrap log rotation is bounded and never truncates the active record.
with tempfile.TemporaryDirectory() as temp:
    import _logrotate
    log = Path(temp) / "bootstrap-errors.log"
    for index in range(400):
        _logrotate.append_log(log, f"line {index} " + "x" * 200, max_bytes=8192, retained=2)
    record("the active log stays under the bound", log.stat().st_size < 8192 + 4096, f"size={log.stat().st_size}")
    record("exactly the retained files are kept",
           log.with_name(log.name + ".1").is_file() and log.with_name(log.name + ".2").is_file()
           and not log.with_name(log.name + ".3").is_file())
    record("the newest line survives rotation", "line 399" in log.read_text(encoding="utf-8"))

# The wiring in main() is pinned, not only the helpers it calls. Reverting the
# every-run purge or the reset branch must fail a test, not pass unnoticed.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp); database = root / "state.sqlite3"; config = root / "config.json"
    config.write_text(json.dumps({
        "paths": {"state_database": str(database)},
        "router": {"host": "router.invalid", "management_port": 8443, "use_tls": True},
        "monitor": {"probe_urls": ["https://invalid.example"],
                    "failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30},
        "notion": {"enabled": False}, "execution_mode": "dry-run",
        "outbox": {"max_deliveries_per_run": 20, "max_attempts_per_event": 3, "retention_days": 30},
    }), encoding="utf-8")
    db = watchdog.open_db(database)
    aged = watchdog.utc_text(watchdog.utc_now() - timedelta(days=90))
    db.execute("INSERT INTO events(timestamp_utc,status,action,detail,delivered_to_notion) VALUES(?,?,?,?,1)",
               (aged, "Online", "Restored", "old and delivered"))
    db.execute("INSERT INTO events(timestamp_utc,status,action,detail,delivery_attempts) VALUES(?,?,?,?,99)",
               (aged, "Error", "Poison", "old and exhausted"))
    db.commit(); db.close()

    watchdog.require_persistent_secret_store = lambda: None
    watchdog.internet_available = lambda _: True

    def run_main(extra=()):
        old_argv = sys.argv
        try:
            sys.argv = ["watchdog.py", "--config", str(config)] + list(extra)
            return watchdog.main()
        finally:
            sys.argv = old_argv

    code = run_main()
    db = watchdog.open_db(database)
    remaining = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    runs = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    db.close()
    record("a monitoring run purges aged events without any remote destination",
           code == 0 and remaining == 0, f"exit={code} remaining={remaining}")
    record("that run still recorded itself", runs == 1, f"runs={runs}")

    db = watchdog.open_db(database)
    db.execute("INSERT INTO events(timestamp_utc,status,action,detail,delivery_attempts) VALUES(?,?,?,?,99)",
               (watchdog.utc_text(), "Error", "Poison", "fresh and exhausted"))
    db.commit(); db.close()
    code = run_main(["--reset-outbox-attempts"])
    db = watchdog.open_db(database)
    attempts = db.execute("SELECT delivery_attempts FROM events ORDER BY id DESC LIMIT 1").fetchone()[0]
    runs_after = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    db.close()
    record("the reset flag releases exhausted events", code == 0 and attempts == 0,
           f"exit={code} attempts={attempts}")
    record("the reset flag exits without recording a monitoring run", runs_after == 1, f"runs={runs_after}")
    gc.collect()

print(json.dumps({"suite": "outbox", "checks": len(results), "failures": 0,
                  "result": "green", "findings": results}, indent=2))
print("outbox and rotation smoke test: OK")
