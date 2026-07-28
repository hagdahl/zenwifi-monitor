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
import ast
import gc
import importlib.util
import json
import os
import sqlite3
import subprocess
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


# --- what the health monitor is allowed to be, checked structurally ----------
#
# These two properties were checked by searching the source text for "keyring",
# "asusrouter" and "reboot". Both scans are worthless in the direction that
# matters. A health monitor that spawned the watchdog with --execute would
# contain none of those words and would pass the check named "the health
# monitor cannot restart the router" while restarting the router. The module is
# therefore parsed and its structure examined.

source = (SRC / "health.py").read_text(encoding="utf-8")
tree = ast.parse(source)

# Independence: every import must be the standard library or this project's own
# seam. That is stronger than naming two forbidden packages, because the point
# is that the observer keeps working when the watchdog's dependencies do not —
# which any third-party import breaks, not only the two anybody thought of.
OWN_MODULES = {"_defaults", "_logrotate", "_platform", "_secrets"}
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        imported.add(node.module.split(".")[0])
foreign = sorted(imported - sys.stdlib_module_names - OWN_MODULES)
record("the health monitor imports only the standard library and its own seam",
       not foreign, f"foreign imports: {foreign}")

# It can start exactly one thing: itself, to draw a notice. Anything else it
# could start is a way to act on the machine it is only supposed to observe.
NOTICE_FLAGS = {"--notice", "--detail"}
spawns = [node for node in ast.walk(tree)
          if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
          and node.func.id == "spawn_detached"]
record("the health monitor spawns something", len(spawns) == 1, f"{len(spawns)} spawn sites")
for call in spawns:
    argument = call.args[0] if call.args else None
    record("the spawn argument list is written out in full, not built elsewhere",
           isinstance(argument, ast.List), ast.dump(argument) if argument else "no argument")
    interpreter, program = argument.elts[0], argument.elts[1]
    record("the spawn runs an interpreter chosen by the seam",
           isinstance(interpreter, ast.Call) and isinstance(interpreter.func, ast.Name)
           and interpreter.func.id == "windowless_interpreter", ast.dump(interpreter))
    record("and the program it runs is this file",
           isinstance(program, ast.Name) and program.id == "__file__", ast.dump(program))
    literals = {element.value for element in argument.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)}
    record("with no argument beyond its own notice flags", literals <= NOTICE_FLAGS,
           f"unexpected literals: {sorted(literals - NOTICE_FLAGS)}")

# --execute is accepted so the shared silent launcher can forward it, and must
# never be read. A branch on it is how an observer becomes an actor.
reads_execute = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute) and node.attr == "execute"
                 and isinstance(node.value, ast.Name) and node.value.id == "args"]
record("the health monitor never reads the --execute flag it accepts",
       not reads_execute, f"{len(reads_execute)} reads")

# And nothing in it names the watchdog's entry point, which is the other way a
# restart could be reached from here.
record("nothing in the health monitor names the watchdog entry point",
       "watchdog.py" not in source and "watchdog" not in imported,
       "the observer must not be able to start the actor")

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
           "health": {"run_age_minutes": 15, "outbox_age_minutes": 180, "notice_cooldown_minutes": 60},
           "notion": {"enabled": True}, "execution_mode": "execute"}

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
    # A dry-run soak with Notion enabled cannot deliver either, so it must not
    # be reported as a stalled queue with a remedy that does not apply.
    soaking = dict(cfg); soaking["execution_mode"] = "dry-run"
    record("no stalled queue is reported during a dry-run soak",
           "outbox-stalled" not in codes(health.evaluate(db, soaking)),
           f"codes={codes(health.evaluate(db, soaking))}")
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

    # A condition already announced and coming back is damped; recovering is
    # never announced; and once the cooldown has elapsed the return is shown.
    def set_run_age(text):
        connection = sqlite3.connect(database)
        connection.execute("UPDATE runs SET timestamp_utc=?", (text,))
        connection.commit(); connection.close()

    set_run_age(stale)
    run_health()
    record("a condition already announced does not renotify inside the cooldown",
           len(notices) == 2, f"notices={len(notices)}")
    set_run_age(watchdog.utc_text())
    run_health()
    record("recovering to fewer conditions does not notify", len(notices) == 2, f"notices={len(notices)}")

    # The memory now lives in two stores and the newer one wins, so an elapsed
    # cooldown has to be simulated in both. Ageing only the database was enough
    # before the stamp carried a write time; it silently is not any more, which
    # is exactly the kind of half-applied fixture the review warned about.
    elapsed = watchdog.utc_text(watchdog.utc_now() - timedelta(hours=4))
    connection = sqlite3.connect(database)
    connection.execute("UPDATE state SET value=? WHERE key='health_last_notice_utc'", (elapsed,))
    connection.commit(); connection.close()
    stamp_path = health.notification_stamp_path()
    if stamp_path.is_file():
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        stamp["last_notice_utc"] = elapsed
        stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
    set_run_age(stale)
    run_health()
    record("with the cooldown elapsed the return is announced", len(notices) == 3, f"notices={len(notices)}")
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

# A threshold that is not a positive whole number falls back to the shared
# default. The health monitor is the thing that reports when the watchdog is
# not running, so a hand-edited value must not stop it from running at all.
record("a non-numeric threshold falls back", health.positive_int("fifteen", 15) == 15)
record("an empty threshold falls back", health.positive_int("", 15) == 15)
record("a missing threshold falls back", health.positive_int(None, 15) == 15)
record("a zero threshold falls back", health.positive_int(0, 15) == 15)
record("a negative threshold falls back", health.positive_int(-5, 15) == 15)
record("a numeric string threshold is honoured", health.positive_int("30", 15) == 30)
record("a whole number threshold is honoured", health.positive_int(45, 15) == 45)


class FailingConnection:
    """A connection that fails one statement and forwards the rest.

    This models a lock taken, or a disk filled, between opening the database
    and writing to it, which is exactly the window the monitor must survive.
    """

    def __init__(self, real, failing_fragment):
        self._real = real
        self._fragment = failing_fragment

    def execute(self, sql, *parameters):
        if self._fragment in sql:
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *parameters)

    def __getattr__(self, name):
        return getattr(self._real, name)


def health_run(config_path):
    argv = sys.argv
    try:
        sys.argv = ["health.py", "--config", str(config_path)]
        return health.main()
    finally:
        sys.argv = argv


def healthy_fixture(root):
    """A database and configuration on which every check passes."""
    database = root / "state.sqlite3"
    db = watchdog.open_db(database)
    watchdog.log_run(db, True, "dry-run", "dry-run")
    db.commit(); db.close()
    config_path = root / "config.json"
    config_path.write_text(json.dumps(
        {"paths": {"state_database": str(database), "log_directory": str(root)},
         "health": {"run_age_minutes": 15, "outbox_age_minutes": 180, "notice_cooldown_minutes": 60},
         "notion": {"enabled": True}, "execution_mode": "execute"}), encoding="utf-8")
    return database, config_path


# A malformed threshold must not abort the run: the monitor still evaluates
# and still exits on the state it found, using the shared default instead.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    health.bootstrap_log_path = lambda: root / "bootstrap-errors.log"
    health.notification_stamp_path = lambda: root / "health-notified.txt"
    database, _ = healthy_fixture(root)
    config_path = root / "config.json"
    config_path.write_text(json.dumps(
        {"paths": {"state_database": str(database), "log_directory": str(root)},
         "health": {"run_age_minutes": "fifteen", "outbox_age_minutes": None,
                    "notice_cooldown_minutes": "-"},
         "outbox": {"max_attempts_per_event": "many"},
         "notion": {"enabled": True}, "execution_mode": "execute"}), encoding="utf-8")
    notices = []
    health.launch_health_notice = lambda detail: notices.append(detail)
    code = health_run(config_path)
    record("a malformed threshold does not abort the health run", code == 0, f"exit was {code}")
    record("a malformed threshold raises no notice on a healthy system",
           notices == [], f"notices={notices}")
    gc.collect()

# A write that fails after the database opened must still reach the notice.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    health.bootstrap_log_path = lambda: root / "bootstrap-errors.log"
    health.notification_stamp_path = lambda: root / "health-notified.txt"
    database, config_path = healthy_fixture(root)
    notices = []
    health.launch_health_notice = lambda detail: notices.append(detail)
    real_open_db = health.open_db
    health.open_db = lambda path: FailingConnection(real_open_db(path), "INSERT INTO health")
    try:
        code = health_run(config_path)
    finally:
        health.open_db = real_open_db
    record("an unwritable health record exits 1", code == 1, f"exit was {code}")
    record("an unwritable health record notifies", len(notices) == 1, f"notices={len(notices)}")
    record("an unwritable health record names the condition",
           notices and "could not be written" in notices[0], f"detail={notices[:1]}")
    gc.collect()

# The same holds for the closing state write, which happens after the
# notification decision is already made.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    health.bootstrap_log_path = lambda: root / "bootstrap-errors.log"
    health.notification_stamp_path = lambda: root / "health-notified.txt"
    database, config_path = healthy_fixture(root)
    notices = []
    health.launch_health_notice = lambda detail: notices.append(detail)
    real_set_state = health.set_state

    def refuse(db, key, value):
        raise sqlite3.OperationalError("database is locked")

    health.set_state = refuse
    try:
        code = health_run(config_path)
    finally:
        health.set_state = real_set_state
    record("an unwritable state row exits 1", code == 1, f"exit was {code}")
    record("an unwritable state row notifies", len(notices) == 1, f"notices={len(notices)}")
    record("an unwritable state row names the condition",
           notices and "could not be recorded" in notices[0], f"detail={notices[:1]}")
    gc.collect()

# A log directory that cannot be written must be reported, not end the run
# before the notice: the notification matters more than the log line.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    health.bootstrap_log_path = lambda: root / "bootstrap-errors.log"
    health.notification_stamp_path = lambda: root / "health-notified.txt"
    database, config_path = healthy_fixture(root)
    notices = []
    health.launch_health_notice = lambda detail: notices.append(detail)
    real_append_log = health.append_log

    def refuse_log(path, line):
        raise OSError("the log directory is not writable")

    health.append_log = refuse_log
    try:
        code = health_run(config_path)
    finally:
        health.append_log = real_append_log
    record("an unwritable health log exits 1", code == 1, f"exit was {code}")
    record("an unwritable health log still notifies", len(notices) == 1, f"notices={len(notices)}")
    record("an unwritable health log names the condition",
           notices and "could not be written" in notices[0], f"detail={notices[:1]}")
    gc.collect()

# --- F10: a write failure must not bypass the notice cooldown -----------------
# The state used to be persisted before it was decided, so the stored memory
# disagreed with the run, every later run saw a fresh transition, and a
# persistently unwritable surface notified on every firing. On the shipped
# fifteen-minute timer that is about ninety-six dialogs a day.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    blocker = root / "not-a-directory"
    blocker.write_text("a file where a log directory is expected", encoding="utf-8")
    database = root / "state.sqlite3"
    config = root / "config.json"
    config.write_text(json.dumps({
        "paths": {"log_directory": str(blocker), "state_database": str(database)},
        "router": {"host": "router.invalid", "model": "m"},
        "monitor": {"probe_urls": ["https://invalid.example"],
                    "failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30},
        "notion": {"enabled": False}, "execution_mode": "dry-run",
        "health": {"run_age_minutes": 15, "outbox_age_minutes": 180,
                   "notice_cooldown_minutes": 60}}), encoding="utf-8")
    db = watchdog.open_db(database)
    db.execute("INSERT INTO runs(timestamp_utc,internet_available,execution_mode,"
               "configured_execution_mode) VALUES(?,1,'dry-run','dry-run')", (watchdog.utc_text(),))
    db.commit(); db.close(); gc.collect()

    health.bootstrap_log_path = lambda: root / "bootstrap-errors.log"
    health.notification_stamp_path = lambda: root / "health-notified.txt"
    storm = []
    health.launch_health_notice = lambda detail: storm.append(detail)
    log_exits = []
    for _ in range(5):
        old_argv = sys.argv
        try:
            sys.argv = ["health.py", "--config", str(config)]
            log_exits.append(health.main())
        finally:
            sys.argv = old_argv
    record("an unwritable health log notifies once, not once per run",
           len(storm) == 1, f"notices={len(storm)} across {len(log_exits)} runs")
    record("and the run still reports unhealthy every time",
           log_exits == [1, 1, 1, 1, 1], str(log_exits))
    record("the reported detail is not self-contradictory",
           "All local health checks passed" not in storm[0], storm[0][:120])
    gc.collect()

# The harder case: a database that accepts reads but refuses every memory write.
# Without a way to tell which memory is newer, the stale database view would be
# handed to every run and the storm would return one firing later.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    root = Path(temp)
    database = root / "state.sqlite3"
    config = root / "config.json"
    config.write_text(json.dumps({
        "paths": {"log_directory": str(root), "state_database": str(database)},
        "router": {"host": "router.invalid", "model": "m"},
        "monitor": {"probe_urls": ["https://invalid.example"],
                    "failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30},
        "notion": {"enabled": False}, "execution_mode": "dry-run",
        "health": {"run_age_minutes": 15, "outbox_age_minutes": 180,
                   "notice_cooldown_minutes": 60}}), encoding="utf-8")
    db = watchdog.open_db(database)
    db.execute("INSERT INTO runs(timestamp_utc,internet_available,execution_mode,"
               "configured_execution_mode) VALUES(?,1,'dry-run','dry-run')", (watchdog.utc_text(),))
    db.commit(); db.close(); gc.collect()

    health.bootstrap_log_path = lambda: root / "bootstrap-errors.log"
    health.notification_stamp_path = lambda: root / "health-notified.txt"
    refused = []
    health.launch_health_notice = lambda detail: refused.append(detail)
    writable_set_state = health.set_state

    def refuse_health_writes(connection, key, value):
        if key.startswith("health_"):
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return writable_set_state(connection, key, value)

    health.set_state = refuse_health_writes
    try:
        exits = []
        for _ in range(5):
            old_argv = sys.argv
            try:
                sys.argv = ["health.py", "--config", str(config)]
                exits.append(health.main())
            finally:
                sys.argv = old_argv
    finally:
        health.set_state = writable_set_state
    record("a database that refuses memory writes still notifies only once",
           len(refused) == 1, f"notices={len(refused)} across {len(exits)} runs")
    record("and it is reported as a fault rather than passed over",
           exits == [1, 1, 1, 1, 1], str(exits))
    gc.collect()

# The health monitor's own top-level handler, exercised as a process because
# that is the only place it runs. An unwritable bootstrap log must not raise
# from inside the handler: doing so replaces the error that actually stopped
# the run and skips the non-zero exit the timer reads. LOGS_DIRECTORY is
# pointed at a path underneath a regular file, which cannot be created as a
# directory on any platform this project supports.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    blocker = Path(temp) / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    environment = dict(os.environ)
    environment["LOGS_DIRECTORY"] = str(blocker / "logs")
    environment.pop("LOCALAPPDATA", None)
    completed = subprocess.run([sys.executable, str(SRC / "health.py")],
                               capture_output=True, text=True, timeout=120,
                               env=environment)
    record("a health run that fails before its config exits non-zero",
           completed.returncode == 2, f"exit {completed.returncode}: {completed.stderr[-300:]}")
    record("and it reports the error that stopped it, not the log directory",
           "--config is required" in completed.stderr,
           completed.stderr[-300:])

print(json.dumps({"suite": "health", "checks": len(results), "failures": 0,
                  "result": "green", "findings": results}, indent=2))
print("health monitor smoke test: OK")
