# ZenWiFi Monitor version: 0.1.0
import gc
import importlib.util
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
spec = importlib.util.spec_from_file_location("watchdog", Path(__file__).parents[1] / "src" / "watchdog.py")
watchdog = importlib.util.module_from_spec(spec); spec.loader.exec_module(watchdog)
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    db = watchdog.open_db(Path(temp) / "state.sqlite3")
    watchdog.set_state(db, "x", "y"); db.commit()
    assert watchdog.get_state(db, "x") == "y"
    watchdog.log_event(db, "Online", "Test", "ok")
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    watchdog.log_run(db, True, "dry-run", "dry-run")
    assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    db.close()

for invalid in (
    {"paths": {}, "router": {}, "monitor": {"probe_urls": [], "failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30}, "execution_mode": "dry-run"},
    {"paths": {"state_database": "<database>"}, "router": {"host": "<router>"}, "monitor": {"probe_urls": ["https://example.invalid"], "failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30}, "execution_mode": "dry-run"},
):
    try: watchdog.validate_config(invalid)
    except RuntimeError: pass
    else: raise AssertionError("invalid configuration must fail closed")

def assert_no_reboot(configured_mode, pass_execute):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        root = Path(temp); database = root / "state.sqlite3"; config = root / "config.json"
        config.write_text(json.dumps({"paths": {"state_database": str(database)}, "router": {"host": "router.invalid"}, "monitor": {"probe_urls": ["https://invalid.example"], "failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30}, "notion": {}, "execution_mode": configured_mode}), encoding="utf-8")
        db = watchdog.open_db(database)
        watchdog.set_state(db, "first_failure_utc", watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=16))); db.commit(); db.close()
        watchdog.require_persistent_secret_store = lambda: None
        watchdog.internet_available = lambda _: False
        async def forbidden_reboot(_): raise AssertionError("router reboot must not be called")
        watchdog.reboot_router = forbidden_reboot
        watchdog.launch_reboot_notice = lambda: (_ for _ in ()).throw(AssertionError("notice must not be called"))
        old_argv = sys.argv; sys.argv = ["watchdog.py", "--config", str(config)] + (["--execute"] if pass_execute else [])
        try: assert watchdog.main() == 0
        finally: sys.argv = old_argv
        gc.collect()
        db = watchdog.open_db(database)
        assert db.execute("SELECT execution_mode FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0] == "dry-run"
        db.close()

assert_no_reboot("dry-run", False)
assert_no_reboot("dry-run", True)
assert_no_reboot("execute", False)

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp); database = root / "state.sqlite3"; config = root / "config.json"
    config.write_text(json.dumps({"paths": {"state_database": str(database)}, "router": {"host": "router.invalid"}, "monitor": {"probe_urls": ["https://invalid.example"], "failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30}, "notion": {}, "execution_mode": "dry-run"}), encoding="utf-8")
    db = watchdog.open_db(database); watchdog.set_state(db, "pending_recovery_notification", "1"); db.commit(); db.close()
    watchdog.require_persistent_secret_store = lambda: None
    watchdog.internet_available = lambda _: True
    notices = []; watchdog.launch_recovery_notice = lambda: notices.append("recovery")
    old_argv = sys.argv; sys.argv = ["watchdog.py", "--config", str(config)]
    try: assert watchdog.main() == 0
    finally: sys.argv = old_argv
    db = watchdog.open_db(database)
    assert notices == ["recovery"] and watchdog.get_state(db, "pending_recovery_notification") == ""
    db.close()
    gc.collect()

def build(temp, mode, notion_enabled, online):
    """A complete valid configuration driving main() without any real I/O."""
    root = Path(temp); database = root / "state.sqlite3"; config = root / "config.json"
    config.write_text(json.dumps({
        "paths": {"state_database": str(database)},
        "router": {"host": "router.invalid", "management_port": 8443, "use_tls": True},
        "monitor": {"probe_urls": ["https://invalid.example"],
                    "failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30},
        "notion": {"enabled": notion_enabled, "data_source_id": "d", "api_version": "v"},
        "execution_mode": mode,
    }), encoding="utf-8")
    watchdog.require_persistent_secret_store = lambda: None
    watchdog.internet_available = lambda _: online
    return database, config


def call_main(config, extra=()):
    old_argv = sys.argv
    try:
        sys.argv = ["watchdog.py", "--config", str(config)] + list(extra)
        return watchdog.main()
    finally:
        sys.argv = old_argv


def events(database, action):
    db = watchdog.open_db(database)
    count = db.execute("SELECT COUNT(*) FROM events WHERE action=?", (action,)).fetchone()[0]
    db.close()
    return count


# The remote delivery gate: both the flag and the configured mode are required,
# exactly like the restart gate above.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "execute", True, online=True)
    delivered = []
    watchdog.notion_event = lambda cfg, status, action, detail: delivered.append(action)
    db = watchdog.open_db(database)
    watchdog.set_state(db, "first_failure_utc", watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=30)))
    db.commit(); db.close()
    assert call_main(config) == 0
    assert not delivered, f"dry-run must not deliver, delivered {delivered}"

    db = watchdog.open_db(database)
    watchdog.set_state(db, "first_failure_utc", watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=30)))
    db.commit(); db.close()
    assert call_main(config, ["--execute"]) == 0
    assert delivered, "an authorized online run must drain the outbox"
    gc.collect()

# The failure window: nothing is decided before the configured threshold.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "dry-run", False, online=False)
    db = watchdog.open_db(database)
    watchdog.set_state(db, "first_failure_utc", watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=5)))
    db.commit(); db.close()
    assert call_main(config) == 0
    assert events(database, "Dry-run") == 0, "the restart decision must wait for the failure window"

    db = watchdog.open_db(database)
    watchdog.set_state(db, "first_failure_utc", watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=16)))
    db.commit(); db.close()
    assert call_main(config) == 0
    assert events(database, "Dry-run") == 1, "the decision must be reached once the window has passed"
    gc.collect()

# The cooldown: a second decision inside the window is suppressed.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "dry-run", False, online=False)
    db = watchdog.open_db(database)
    watchdog.set_state(db, "first_failure_utc", watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=60)))
    watchdog.set_state(db, "last_reboot_attempt_utc", watchdog.utc_text())
    db.commit(); db.close()
    assert call_main(config) == 0
    assert events(database, "Dry-run") == 0, "the cooldown must suppress a second decision"

    db = watchdog.open_db(database)
    watchdog.set_state(db, "last_reboot_attempt_utc",
                       watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=45)))
    db.commit(); db.close()
    assert call_main(config) == 0
    assert events(database, "Dry-run") == 1, "an elapsed cooldown must allow the decision again"
    gc.collect()

print("watchdog storage smoke test: OK")
