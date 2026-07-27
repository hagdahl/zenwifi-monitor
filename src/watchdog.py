"""Local-first ZenWiFi Internet watchdog. Defaults to dry-run."""
# ZenWiFi Monitor version: 0.1.0
import argparse
import asyncio
import json
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _logrotate import append_log, bootstrap_log_path  # noqa: E402

import keyring
from keyring.backends.Windows import WinVaultKeyring

SERVICE = "ZenWiFiMonitor"

def utc_now() -> datetime: return datetime.now(UTC)
def utc_text(value: datetime | None = None) -> str: return (value or utc_now()).isoformat()

def write_bootstrap_error(error: Exception) -> None:
    """Record failures that happen before the configured SQLite store is available.

    The log is size-bounded and rotated before writing, so a persistent failure
    cannot grow it without limit and the active record is never truncated.
    """
    append_log(bootstrap_log_path(), f"{utc_text()} {type(error).__name__}: {error}")


def sanitize_error(error: Exception) -> str:
    """Describe a failure without ever persisting an authorization header."""
    text = f"{type(error).__name__}: {error}"
    return re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", text)[:400]

def require_persistent_secret_store() -> None:
    """Fail closed if Python selected a backend other than Windows Credential Manager."""
    if not isinstance(keyring.get_keyring(), WinVaultKeyring):
        raise RuntimeError("Windows Credential Manager is required; fallback to files or environment variables is not allowed.")

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
    for key in ("failure_minutes_before_reboot", "reboot_cooldown_minutes"):
        if not isinstance(cfg["monitor"].get(key), int) or cfg["monitor"][key] <= 0:
            raise RuntimeError(f"monitor.{key} must be a positive integer.")
    if cfg.get("execution_mode") not in ("dry-run", "execute"):
        raise RuntimeError("execution_mode must be 'dry-run' or 'execute'.")

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
        db.execute("ALTER TABLE runs ADD COLUMN configured_execution_mode TEXT NOT NULL DEFAULT 'unknown'")
    event_columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
    for column, definition in (("delivery_attempts", "INTEGER NOT NULL DEFAULT 0"),
                               ("last_delivery_attempt_utc", "TEXT"),
                               ("last_delivery_error", "TEXT")):
        if column not in event_columns:
            db.execute(f"ALTER TABLE events ADD COLUMN {column} {definition}")
    db.execute("CREATE TABLE IF NOT EXISTS health (id INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL, "
               "state TEXT NOT NULL, severity INTEGER NOT NULL, detail TEXT NOT NULL)")
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
    username = keyring.get_password(SERVICE, "router_username")
    password = keyring.get_password(SERVICE, "router_password")
    if not username or not password: raise RuntimeError("Router credentials are missing from Windows Credential Manager.")
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
    token = keyring.get_password(SERVICE, "notion_token")
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
    return (int(settings.get("max_deliveries_per_run", 20)),
            int(settings.get("max_attempts_per_event", 5)),
            int(settings.get("retention_days", 30)))


def purge_events(db, retention_days: int, max_attempts: int) -> dict:
    """Drop delivered events past retention, and undelivered events that have
    exhausted their retry bound and are also past retention.

    Without the second rule an undeliverable event would occupy the queue for
    ever, because an exhausted event is never retried and was never purged.
    """
    cutoff = utc_text(utc_now() - timedelta(days=retention_days))
    delivered = db.execute("DELETE FROM events WHERE delivered_to_notion=1 AND timestamp_utc < ?",
                           (cutoff,)).rowcount
    abandoned = db.execute("DELETE FROM events WHERE delivered_to_notion=0 AND delivery_attempts >= ? "
                           "AND timestamp_utc < ?", (max_attempts, cutoff)).rowcount
    db.commit()
    return {"delivered": delivered, "abandoned": abandoned}


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
    pending = db.execute("SELECT COUNT(*) FROM events WHERE delivered_to_notion=0").fetchone()[0]
    exhausted = db.execute("SELECT COUNT(*) FROM events WHERE delivered_to_notion=0 AND delivery_attempts >= ?",
                           (max_attempts,)).fetchone()[0]
    purged = purge_events(db, retention_days, max_attempts)
    return {"delivered": delivered, "pending": pending, "exhausted": exhausted,
            "purged": purged["delivered"], "abandoned": purged["abandoned"], "stopped_on": stopped_on}


def notion_is_enabled(cfg: dict) -> bool:
    """Notion is an optional secondary log destination; SQLite is always primary."""
    return bool(cfg.get("notion", {}).get("enabled", False))

def visible_reboot_notice():
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, "Internet has been unavailable for at least 15 minutes. The router is now restarting.", "Router Watchdog", 0x30)

def visible_recovery_notice():
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, "Internet connectivity has been restored after the router restart.", "Router Watchdog", 0x40)

def _notice_interpreter() -> str:
    """Prefer the windowless interpreter so a notice never flashes a console."""
    windowless = Path(sys.executable).with_name("pythonw.exe")
    return str(windowless) if windowless.is_file() else sys.executable

def _spawn_notice(flag: str) -> None:
    """Start a notice in its own process without creating a console window."""
    subprocess.Popen([_notice_interpreter(), __file__, flag], close_fds=True,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

def launch_reboot_notice() -> None:
    _spawn_notice("--notice")

def launch_recovery_notice() -> None:
    _spawn_notice("--recovery-notice")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--execute", action="store_true", help="Allows authorized router and Notion actions.")
    parser.add_argument("--notice", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recovery-notice", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reset-outbox-attempts", action="store_true",
                        help="Clear the retry bound on events that exhausted it, then exit without monitoring.")
    args = parser.parse_args()
    if args.notice:
        visible_reboot_notice(); return 0
    if args.recovery_notice:
        visible_recovery_notice(); return 0
    if args.config is None:
        raise RuntimeError("--config is required for a monitoring run.")
    require_persistent_secret_store(); cfg = load_config(args.config); validate_config(cfg); db = open_db(Path(cfg["paths"]["state_database"]))
    max_per_run, max_attempts, retention_days = outbox_settings(cfg)
    if args.reset_outbox_attempts:
        reset = reset_exhausted_events(db, max_attempts)
        print(f"Cleared the retry bound on {reset} outbox events.")
        return 0
    effective_mode = "execute" if args.execute and cfg["execution_mode"] == "execute" else "dry-run"
    online = internet_available(cfg["monitor"]["probe_urls"])
    log_run(db, online, effective_mode, cfg["execution_mode"])
    # Retention runs on every monitoring run, not only when remote delivery is
    # enabled, so the local event table cannot grow without bound in dry-run.
    purge_events(db, retention_days, max_attempts)
    first_failure = get_state(db, "first_failure_utc")
    if online:
        if get_state(db, "pending_recovery_notification"):
            launch_recovery_notice()
            set_state(db, "pending_recovery_notification", "")
        if first_failure:
            detail = "Internet connectivity has been restored."
            log_event(db, "Online", "Restored", detail)
        if effective_mode == "execute" and notion_is_enabled(cfg):
            deliver_outbox(db, cfg)
        set_state(db, "first_failure_utc", ""); db.commit(); return 0
    if not first_failure:
        detail = "All external HTTPS probes failed."
        set_state(db, "first_failure_utc", utc_text()); log_event(db, "Offline", "Monitoring started", detail)
        return 0
    failure_age = utc_now() - datetime.fromisoformat(first_failure)
    if failure_age < timedelta(minutes=cfg["monitor"]["failure_minutes_before_reboot"]): return 0
    last_attempt = get_state(db, "last_reboot_attempt_utc")
    if last_attempt and utc_now() - datetime.fromisoformat(last_attempt) < timedelta(minutes=cfg["monitor"]["reboot_cooldown_minutes"]): return 0
    if effective_mode != "execute":
        set_state(db, "last_reboot_attempt_utc", utc_text())
        log_event(db, "Offline", "Dry-run", "Restart condition met; no external action in dry-run."); return 0
    set_state(db, "last_reboot_attempt_utc", utc_text()); db.commit()
    launch_reboot_notice()
    try:
        if not asyncio.run(reboot_router(cfg)): raise RuntimeError("The router rejected the restart request.")
    except Exception as error:
        log_event(db, "Error", "Restart failed", str(error)); return 1
    set_state(db, "last_reboot_utc", utc_text()); set_state(db, "pending_recovery_notification", "1"); set_state(db, "first_failure_utc", ""); log_event(db, "Restarted", "Router restarted", "Internet was unavailable for at least 15 minutes.")
    if notion_is_enabled(cfg):
        deliver_outbox(db, cfg)
    return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as error:
        write_bootstrap_error(error)
        print(f"ERROR: {error}", file=sys.stderr); raise SystemExit(1)
