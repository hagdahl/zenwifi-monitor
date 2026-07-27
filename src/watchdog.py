"""Local-first ZenWiFi Internet watchdog. Defaults to dry-run."""
import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import keyring
from keyring.backends.Windows import WinVaultKeyring

SERVICE = "ZenWiFiMonitor"

def utc_now() -> datetime: return datetime.now(UTC)
def utc_text(value: datetime | None = None) -> str: return (value or utc_now()).isoformat()

def write_bootstrap_error(error: Exception) -> None:
    """Record failures that happen before the configured SQLite store is available."""
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ZenWiFiMonitor"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "bootstrap-errors.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_text()} {type(error).__name__}: {error}\n")

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
    db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL, status TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, delivered_to_notion INTEGER NOT NULL DEFAULT 0)")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL, internet_available INTEGER NOT NULL, execution_mode TEXT NOT NULL, configured_execution_mode TEXT NOT NULL)")
    columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
    if "configured_execution_mode" not in columns:
        db.execute("ALTER TABLE runs ADD COLUMN configured_execution_mode TEXT NOT NULL DEFAULT 'unknown'")
    return db

def get_state(db, key):
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None

def set_state(db, key, value):
    db.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

def log_event(db, status, action, detail):
    db.execute("INSERT INTO events(timestamp_utc,status,action,detail) VALUES(?,?,?,?)", (utc_text(), status, action, detail[:1900]))
    db.commit()

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

def notion_is_enabled(cfg: dict) -> bool:
    """Notion is an optional secondary log destination; SQLite is always primary."""
    return bool(cfg.get("notion", {}).get("enabled", False))

def visible_reboot_notice():
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, "Internet has been unavailable for at least 15 minutes. The router is now restarting.", "Router Watchdog", 0x30)

def visible_recovery_notice():
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, "Internet connectivity has been restored after the router restart.", "Router Watchdog", 0x40)

def launch_reboot_notice() -> None:
    subprocess.Popen([sys.executable, __file__, "--notice"], close_fds=True)

def launch_recovery_notice() -> None:
    subprocess.Popen([sys.executable, __file__, "--recovery-notice"], close_fds=True)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--execute", action="store_true", help="Allows authorized router and Notion actions.")
    parser.add_argument("--notice", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recovery-notice", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.notice:
        visible_reboot_notice(); return 0
    if args.recovery_notice:
        visible_recovery_notice(); return 0
    if args.config is None:
        raise RuntimeError("--config is required for a monitoring run.")
    require_persistent_secret_store(); cfg = load_config(args.config); validate_config(cfg); db = open_db(Path(cfg["paths"]["state_database"]))
    effective_mode = "execute" if args.execute and cfg["execution_mode"] == "execute" else "dry-run"
    online = internet_available(cfg["monitor"]["probe_urls"])
    log_run(db, online, effective_mode, cfg["execution_mode"])
    first_failure = get_state(db, "first_failure_utc")
    if online:
        if get_state(db, "pending_recovery_notification"):
            launch_recovery_notice()
            set_state(db, "pending_recovery_notification", "")
        if first_failure:
            detail = "Internet connectivity has been restored."
            log_event(db, "Online", "Restored", detail)
            if args.execute and cfg["execution_mode"] == "execute" and notion_is_enabled(cfg):
                try: notion_event(cfg, "Online", "Restored", detail)
                except Exception as error: log_event(db, "Error", "Notion logging failed", str(error))
        set_state(db, "first_failure_utc", ""); db.commit(); return 0
    if not first_failure:
        detail = "All external HTTPS probes failed."
        set_state(db, "first_failure_utc", utc_text()); log_event(db, "Offline", "Monitoring started", detail)
        if args.execute and cfg["execution_mode"] == "execute" and notion_is_enabled(cfg):
            try: notion_event(cfg, "Offline", "Monitoring started", detail)
            except Exception as error: log_event(db, "Error", "Notion logging failed", str(error))
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
        try: notion_event(cfg, "Restarted", "Router restarted", "Internet was unavailable for at least 15 minutes.")
        except Exception as error: log_event(db, "Error", "Notion logging failed", str(error))
    return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as error:
        write_bootstrap_error(error)
        print(f"ERROR: {error}", file=sys.stderr); raise SystemExit(1)
