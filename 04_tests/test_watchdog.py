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


def age_runs(database, minutes_ago):
    """Backdate the recorded runs so elapsed time can be simulated.

    The runs themselves are written by main() on the real code path; only the
    clock is manipulated. That is the difference between exercising the code and
    seeding the value the code is supposed to derive — the mistake this suite
    made before, which let the line that stamps an outage be deleted with every
    suite still green.
    """
    db = watchdog.open_db(database)
    rows = db.execute("SELECT id FROM runs ORDER BY id").fetchall()
    for offset, (row_id,) in zip(minutes_ago, rows):
        db.execute("UPDATE runs SET timestamp_utc=? WHERE id=?",
                   (watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=offset)), row_id))
    db.commit(); db.close(); gc.collect()


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

# The restart decision is taken from observed runs, not from a stored marker.
# Each case drives main() repeatedly so the run history is written by the code
# under test, and only the recorded timestamps are moved to simulate elapsed
# time.

# Not enough elapsed time, even with enough observations.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "dry-run", False, online=False)
    for _ in range(3):
        assert call_main(config) == 0
    age_runs(database, [8, 6, 4])
    assert call_main(config) == 0
    assert events(database, "Dry-run") == 0, "the decision must wait for the failure window"
    gc.collect()

# Enough elapsed time AND enough observations.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "dry-run", False, online=False)
    for _ in range(3):
        assert call_main(config) == 0
    age_runs(database, [20, 12, 6])
    assert call_main(config) == 0
    assert events(database, "Dry-run") == 1, "the decision must be reached on real evidence"
    gc.collect()

# THE REGRESSION THIS REPLACES. A single failed observation after a long gap
# used to reach the decision, because the age of a stored marker was the whole
# test. One failure now cannot satisfy the count however old the outage looks.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "dry-run", False, online=False)
    assert call_main(config) == 0          # one failure, long ago
    age_runs(database, [9 * 60])
    assert call_main(config) == 0          # the machine wakes; one failed probe
    assert events(database, "Dry-run") == 0, \
        "a single observation after a monitoring gap must not restart anything"
    gc.collect()

# Flapping is not an outage: one successful run moves the reference forward.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "dry-run", False, online=False)
    for _ in range(3):
        assert call_main(config) == 0
    age_runs(database, [25, 20, 15])
    watchdog.internet_available = lambda _: True
    assert call_main(config) == 0          # connectivity returns
    watchdog.internet_available = lambda _: False
    for _ in range(3):
        assert call_main(config) == 0      # and fails again, three times, just now
    assert events(database, "Dry-run") == 0, \
        "a successful run between failures must reset the duration"
    gc.collect()

# Recovery must clear the announcement marker, so the next outage is announced
# again. Since the marker is no longer a decision input this is a log defect
# rather than a safety one, but an outage that starts without a "Monitoring
# started" line is a gap in the record.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "dry-run", False, online=False)
    assert call_main(config) == 0
    assert events(database, "Monitoring started") == 1, "the first outage is announced"
    watchdog.internet_available = lambda _: True
    assert call_main(config) == 0
    db = watchdog.open_db(database)
    assert watchdog.get_state(db, "first_failure_utc") == "", \
        "recovery must clear the announcement marker"
    db.close(); gc.collect()
    watchdog.internet_available = lambda _: False
    assert call_main(config) == 0
    assert events(database, "Monitoring started") == 2, \
        "a later outage must be announced again"
    gc.collect()

# The cooldown: a second decision inside the window is suppressed.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "dry-run", False, online=False)
    for _ in range(3):
        assert call_main(config) == 0
    age_runs(database, [40, 30, 20])
    db = watchdog.open_db(database)
    watchdog.set_state(db, "last_reboot_attempt_utc", watchdog.utc_text())
    db.commit(); db.close()
    assert call_main(config) == 0
    assert events(database, "Dry-run") == 0, "the cooldown must suppress a second decision"

    # 20 minutes lies between the failure window (15) and the cooldown (30), so
    # this probe point fails if the code reads the wrong threshold. The previous
    # points, 0 and 45, fell the same side of both and could not tell them apart.
    db = watchdog.open_db(database)
    watchdog.set_state(db, "last_reboot_attempt_utc",
                       watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=20)))
    db.commit(); db.close()
    assert call_main(config) == 0
    assert events(database, "Dry-run") == 0, "20 minutes is inside the 30-minute cooldown"

    db = watchdog.open_db(database)
    watchdog.set_state(db, "last_reboot_attempt_utc",
                       watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=45)))
    db.commit(); db.close()
    assert call_main(config) == 0
    assert events(database, "Dry-run") == 1, "an elapsed cooldown must allow the decision again"
    gc.collect()

# --- the run lease -----------------------------------------------------------
# The scheduler cannot enforce one run at a time: the silent launcher returns
# before its child, so the task reads as finished while the run is still going.
# Two concurrent runs would each drain the outbox and an event would be
# delivered twice. These cases pin the lease that closes it, including that a
# stale lease is reclaimed, because a lock a crashed run can hold for ever
# would replace double delivery with no monitoring at all.

def runs_recorded(database):
    db = watchdog.open_db(database)
    count = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    db.close()
    gc.collect()
    return count


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "dry-run", False, True)
    assert call_main(config) == 0
    assert runs_recorded(database) == 1, "a normal run must record itself"

    # A normal run releases the lease, so the next one proceeds.
    assert call_main(config) == 0
    assert runs_recorded(database) == 2, "the lease must be released after a run"

    # A lease held by a live run makes the next run skip without recording.
    db = watchdog.open_db(database)
    watchdog.acquire_run_lease(db, "someone-else", watchdog.RUN_LEASE_MINUTES)
    db.close(); gc.collect()
    assert call_main(config) == 0, "a skipped run is not an error"
    assert runs_recorded(database) == 2, "a run must not proceed while another holds the lease"

    db = watchdog.open_db(database)
    assert watchdog.get_state(db, "last_skipped_run_utc"), "a skip must leave evidence"
    db.close(); gc.collect()

    # A lease older than the bound is taken over, so a killed run cannot stop
    # monitoring for ever.
    stale = watchdog.utc_text(watchdog.utc_now() - timedelta(minutes=watchdog.RUN_LEASE_MINUTES + 1))
    db = watchdog.open_db(database)
    db.execute("UPDATE run_lease SET acquired_utc = ? WHERE id = 1", (stale,)); db.commit()
    db.close(); gc.collect()
    assert call_main(config) == 0
    assert runs_recorded(database) == 3, "a stale lease must be reclaimed"

    # A run that lost its lease to a later owner must not delete that owner's
    # lease when it finishes.
    db = watchdog.open_db(database)
    watchdog.acquire_run_lease(db, "later-owner", watchdog.RUN_LEASE_MINUTES)
    watchdog.release_run_lease(db, "earlier-owner")
    held = db.execute("SELECT owner FROM run_lease WHERE id = 1").fetchone()
    assert held is not None and held[0] == "later-owner", "release must be owner-scoped"
    db.close(); gc.collect()

    # A malformed bound falls back to the shared default rather than raising
    # inside a monitoring run.
    assert watchdog.run_lease_minutes({"monitor": {"run_lease_minutes": "soon"}}) == watchdog.RUN_LEASE_MINUTES
    assert watchdog.run_lease_minutes({"monitor": {"run_lease_minutes": 0}}) == watchdog.RUN_LEASE_MINUTES
    assert watchdog.run_lease_minutes({"monitor": {"run_lease_minutes": 3}}) == 3

# --- what "online" actually means --------------------------------------------
# `probe` and `internet_available` had no coverage at all, so `any` becoming
# `all` passed every suite. That mutation turns one CDN's bad minute into an
# outage, and an outage is what restarts a router. Everything below substitutes
# the transport; no request leaves the machine.
#
# A second, untouched instance of the module is loaded for these cases. The
# sections above substitute `internet_available` wholesale to drive main(), so
# asserting on the real one through the same object would silently assert on a
# stub -- which is the class of mistake this whole finding is about.
pristine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pristine)

class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False


probe_calls = []
original_urlopen = pristine.urllib.request.urlopen


def _fake_urlopen(request, timeout=None):
    probe_calls.append({"url": request.full_url, "timeout": timeout,
                        "agent": request.get_header("User-agent")})
    token = request.full_url.rsplit("/", 1)[-1]
    if token == "urlerror":
        raise pristine.urllib.error.URLError("no route to host")
    if token == "timeout":
        raise TimeoutError("timed out")
    if token == "value":
        raise ValueError("unknown url type")
    if token == "boom":
        raise RuntimeError("an error probe() does not claim to handle")
    return _Response(int(token))


try:
    pristine.urllib.request.urlopen = _fake_urlopen
    for token, expected in (("200", True), ("204", True), ("301", True), ("399", True),
                            ("400", False), ("404", False), ("500", False),
                            ("urlerror", False), ("timeout", False), ("value", False)):
        actual = pristine.probe(f"https://probe.invalid/{token}")
        assert actual is expected, f"probe({token}) returned {actual}, expected {expected}"

    # A probe without a timeout would hang a five-minute job indefinitely, and
    # the run holds the lease while it hangs, so monitoring stops altogether.
    assert all(call["timeout"] for call in probe_calls), "every probe must carry a timeout"
    assert all(call["agent"] for call in probe_calls), "every probe must identify itself"

    # The exception list is deliberately narrow. An error probe() does not claim
    # to handle must reach the caller rather than be reported as "offline",
    # because a bug that silently reads as no connectivity restarts a router.
    unexpected_reached_caller = False
    try:
        pristine.probe("https://probe.invalid/boom")
    except RuntimeError:
        unexpected_reached_caller = True
    assert unexpected_reached_caller, "an unhandled error must not be reported as offline"
finally:
    pristine.urllib.request.urlopen = original_urlopen

original_probe = pristine.probe
try:
    reachable = {"https://one.invalid": False, "https://two.invalid": True}
    pristine.probe = lambda url: reachable[url]
    assert pristine.internet_available(list(reachable)) is True, \
        "one reachable probe is connectivity; requiring all of them turns a CDN's bad minute into an outage"
    reachable["https://two.invalid"] = False
    assert pristine.internet_available(list(reachable)) is False, \
        "no reachable probe is an outage"
    assert pristine.internet_available([]) is False, \
        "no probes at all cannot read as connectivity"

    # Short-circuiting matters: the first success ends the round, so a run does
    # not pay ten seconds per remaining probe while holding the lease.
    asked = []
    pristine.probe = lambda url: asked.append(url) or True
    pristine.internet_available(["https://first.invalid", "https://second.invalid"])
    assert asked == ["https://first.invalid"], f"probing stopped short of {asked}"
finally:
    pristine.probe = original_probe

# --- configuration validation, rule by rule ----------------------------------
# Two cases used to stand for the whole function, so most of its rules could be
# deleted with every suite green. Each case below breaks exactly one rule.

VALID = {
    "paths": {"state_database": "/tmp/state.sqlite3", "log_directory": "/tmp/logs"},
    "router": {"host": "router.invalid", "management_port": 8443, "use_tls": True},
    "monitor": {"probe_urls": ["https://one.invalid", "https://two.invalid"],
                "failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30,
                "failure_window_minutes": 30, "required_failed_runs": 3},
    "notion": {"enabled": False, "data_source_id": "d", "api_version": "v"},
    "execution_mode": "dry-run",
    "outbox": {"max_deliveries_per_run": 20, "max_attempts_per_event": 5, "retention_days": 30},
    "health": {"run_age_minutes": 15, "outbox_age_minutes": 180, "notice_cooldown_minutes": 60},
}
pristine.validate_config(json.loads(json.dumps(VALID)))


def broken(**changes):
    """A copy of the valid configuration with one section replaced."""
    candidate = json.loads(json.dumps(VALID))
    for section, value in changes.items():
        if value is None:
            candidate.pop(section, None)
        elif isinstance(value, dict) and isinstance(candidate.get(section), dict):
            candidate[section] = {**candidate[section], **value}
        else:
            candidate[section] = value
    return candidate


INVALID = [
    ("configuration is not an object", []),
    ("paths section missing", broken(paths=None)),
    ("router section missing", broken(router=None)),
    ("monitor section missing", broken(monitor=None)),
    ("paths section is not an object", broken(paths="/tmp")),
    ("a path is still a placeholder", broken(paths={"state_database": "<absolute path>"})),
    ("a path is blank", broken(paths={"state_database": "   "})),
    ("the router host is still a placeholder", broken(router={"host": "<router>"})),
    ("the state database is absent", broken(paths={"state_database": None})),
    ("probe_urls is not a list", broken(monitor={"probe_urls": "https://one.invalid"})),
    ("probe_urls is empty", broken(monitor={"probe_urls": []})),
    ("a probe url is not HTTPS", broken(monitor={"probe_urls": ["http://one.invalid"]})),
    ("the failure threshold is not an integer", broken(monitor={"failure_minutes_before_reboot": "15"})),
    ("the failure threshold is zero", broken(monitor={"failure_minutes_before_reboot": 0})),
    ("the cooldown is negative", broken(monitor={"reboot_cooldown_minutes": -1})),
    ("the failure window is zero", broken(monitor={"failure_window_minutes": 0})),
    ("the failure window is a boolean", broken(monitor={"failure_window_minutes": True})),
    # The floor of two is the F7 fix expressed as a rule: one observation after
    # a monitoring gap is exactly the behaviour that restarted a router without
    # evidence, and no configuration may restore it.
    ("one required failed run", broken(monitor={"required_failed_runs": 1})),
    ("zero required failed runs", broken(monitor={"required_failed_runs": 0})),
    ("required failed runs is a boolean", broken(monitor={"required_failed_runs": True})),
    ("execution_mode is unknown", broken(execution_mode="on")),
    ("execution_mode is absent", broken(execution_mode=None)),
    ("the outbox section is not an object", broken(outbox=20)),
    ("a retry bound of zero", broken(outbox={"max_attempts_per_event": 0})),
    ("a retry bound that is a boolean", broken(outbox={"max_attempts_per_event": True})),
    ("a retention window of zero", broken(outbox={"retention_days": 0})),
    ("the health section is not an object", broken(health="on")),
    ("a negative health age", broken(health={"run_age_minutes": -5})),
]
for label, candidate in INVALID:
    try:
        pristine.validate_config(candidate)
    except RuntimeError:
        continue
    raise AssertionError(f"validate_config accepted an invalid configuration: {label}")

# The optional sections are optional, not merely tolerated when complete.
pristine.validate_config(broken(outbox=None, health=None))
without_window = json.loads(json.dumps(VALID))
without_window["monitor"].pop("failure_window_minutes")
without_window["monitor"].pop("required_failed_runs")
pristine.validate_config(without_window)

# --- the delivery gate has two halves, and only one was pinned ---------------
# An authorized production run with Notion switched off must still deliver
# nothing. The previous case only varied the flag, so removing the Notion half
# of the gate left every suite green.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    database, config = build(temp, "execute", notion_enabled=False, online=True)
    delivered = []
    watchdog.notion_event = lambda cfg, status, action, detail: delivered.append(action)
    db = watchdog.open_db(database)
    watchdog.log_event(db, "Offline", "Monitoring started", "queued while the destination was off")
    db.commit(); db.close(); gc.collect()
    assert call_main(config, ["--execute"]) == 0
    assert not delivered, f"an install without a destination must not deliver, delivered {delivered}"

    # And the queue is still there afterwards: not delivering is not discarding.
    db = watchdog.open_db(database)
    assert db.execute("SELECT COUNT(*) FROM events WHERE delivered_to_notion=0").fetchone()[0] >= 1, \
        "events must survive a run that cannot deliver them"
    db.close(); gc.collect()

# --- what happens after a restart --------------------------------------------
# The whole post-restart block was unreachable in tests, because every case
# forbade the reboot. Substituting reboot_router rather than forbidding it makes
# the block reachable without any router existing.

def drive_to_restart(temp, reboot, notion_enabled=False):
    """Drive main() on the real path until the restart decision is reached."""
    database, config = build(temp, "execute", notion_enabled, online=False)
    watchdog.reboot_router = reboot
    notices = []
    watchdog.launch_reboot_notice = lambda detail="": notices.append(detail)
    for _ in range(3):
        assert call_main(config, ["--execute"]) == 0
    age_runs(database, [20, 12, 6])
    return database, config, notices


original_reboot = watchdog.reboot_router
original_reboot_notice = watchdog.launch_reboot_notice
try:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        async def succeed(_cfg):
            return True

        database, config, notices = drive_to_restart(temp, succeed)
        assert call_main(config, ["--execute"]) == 0
        db = watchdog.open_db(database)
        assert db.execute("SELECT COUNT(*) FROM events WHERE action='Router restarted'").fetchone()[0] == 1, \
            "a successful restart must be recorded"
        assert watchdog.get_state(db, "last_reboot_utc"), "a successful restart must be stamped"
        assert watchdog.get_state(db, "pending_recovery_notification") == "1", \
            "a restart must arm the recovery notice, or a restored connection is never announced"
        assert watchdog.get_state(db, "first_failure_utc") == "", \
            "a restart ends the announced outage"
        db.close(); gc.collect()
        assert notices and notices[-1], "the person at the machine must be told, with what was observed"

    # A restart that fails must be recorded, must exit non-zero, and must not
    # claim any of the success state. This is also the one error that leaves the
    # machine, so it must be sanitized.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        async def fail(_cfg):
            raise RuntimeError("Authentication failed for hunter2 at router.invalid")

        watchdog.get_secret = lambda name: "hunter2" if name == "router_password" else "someone"
        database, config, notices = drive_to_restart(temp, fail)
        assert call_main(config, ["--execute"]) == 1, "a failed restart is an error"
        db = watchdog.open_db(database)
        detail = db.execute("SELECT detail FROM events WHERE action='Restart failed'").fetchone()
        assert detail is not None, "a failed restart must be recorded"
        assert "hunter2" not in detail[0], f"the credential reached the event detail: {detail[0]}"
        assert watchdog.get_state(db, "last_reboot_utc") == "" or \
            watchdog.get_state(db, "last_reboot_utc") is None, \
            "a failed restart must not stamp a success"
        assert not watchdog.get_state(db, "pending_recovery_notification"), \
            "a failed restart must not arm the recovery notice"
        db.close(); gc.collect()

    # A router that refuses the request is a failure, not a silent success.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        async def refuse(_cfg):
            return False

        database, config, notices = drive_to_restart(temp, refuse)
        assert call_main(config, ["--execute"]) == 1, "a refused restart is an error"
        db = watchdog.open_db(database)
        assert db.execute("SELECT COUNT(*) FROM events WHERE action='Restart failed'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM events WHERE action='Router restarted'").fetchone()[0] == 0
        db.close(); gc.collect()
finally:
    watchdog.reboot_router = original_reboot
    watchdog.launch_reboot_notice = original_reboot_notice

print("watchdog storage smoke test: OK")
