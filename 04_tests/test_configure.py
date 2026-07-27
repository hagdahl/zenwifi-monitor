# ZenWiFi Monitor version: 0.1.0
"""Smoke test for the configuration tool.

`scripts/configure.py` is the single implementation of migration and
validation for both platforms; `scripts/Migrate-LocalConfig.ps1` calls it. The
properties worth pinning are the ones a migration can quietly get wrong:
promoting an install into production, turning on remote logging nobody asked
for, overwriting a working value with a template placeholder, or writing
anything at all during a dry run.

Every case runs against a temporary file. Nothing here reads or writes a real
configuration.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOOL = ROOT / "scripts" / "configure.py"
results = []


def record(name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(f"{name}: {detail}")


def run(*arguments):
    return subprocess.run([sys.executable, str(TOOL), *arguments],
                          capture_output=True, text=True, timeout=120)


LEGACY = {
    "paths": {"log_directory": "/tmp/logs", "state_database": "/tmp/state.sqlite3"},
    "router": {"host": "192.0.2.1", "https_port": 8443},
    "monitor": {"failure_minutes_before_reboot": 15, "reboot_cooldown_minutes": 30,
                "probe_urls": ["https://example.invalid/probe"]},
    "execution_mode": "execute",
}

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    path = Path(temp) / "config.json"
    path.write_text(json.dumps(LEGACY, indent=2), encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    dry = run("--config", str(path), "--migrate")
    record("a migration dry run succeeds", dry.returncode == 0, dry.stderr[:200])
    record("a dry run writes nothing", path.read_text(encoding="utf-8") == before)
    record("a dry run says so", "Nothing was written" in dry.stdout, dry.stdout[-200:])
    record("a dry run lists what it would change",
           "Mapped router.https_port" in dry.stdout, dry.stdout[:300])

    applied = run("--config", str(path), "--migrate", "--apply")
    record("applying the migration succeeds", applied.returncode == 0, applied.stderr[:200])
    migrated = json.loads(path.read_text(encoding="utf-8"))

    # The two things a migration must never do on its own.
    record("execution_mode is never changed when present",
           migrated["execution_mode"] == "execute", migrated["execution_mode"])
    record("Notion is never enabled without being asked",
           migrated["notion"]["enabled"] is False, str(migrated["notion"]))

    # A template placeholder must never replace a working value. `paths` and
    # `router` carry placeholders in config.example.json, so filling them would
    # break a live install at its next run.
    record("the configured router host survives migration",
           migrated["router"]["host"] == "192.0.2.1", migrated["router"]["host"])
    record("the configured state path survives migration",
           migrated["paths"]["state_database"] == "/tmp/state.sqlite3",
           migrated["paths"]["state_database"])
    record("no template placeholder was written",
           "<" not in json.dumps(migrated), json.dumps(migrated)[:200])

    # The schema work itself.
    record("the legacy port key is mapped and removed",
           migrated["router"]["management_port"] == 8443
           and "https_port" not in migrated["router"], str(migrated["router"]))
    record("TLS is asserted", migrated["router"]["use_tls"] is True)
    record("optional sections are added from the template",
           "outbox" in migrated and "health" in migrated)
    record("a new key in an existing section is added too",
           "run_lease_minutes" in migrated["monitor"], str(migrated["monitor"]))
    record("the configuration is stamped with the project version",
           migrated["config_version"] == (ROOT / "VERSION").read_text(encoding="utf-8").strip())

    backups = list(Path(temp).glob("config.json.*.bak"))
    record("applying writes a timestamped backup", len(backups) == 1, str(backups))
    record("the backup holds the pre-migration content",
           json.loads(backups[0].read_text(encoding="utf-8")) == LEGACY)

    # Running it twice must be a no-op, otherwise every run looks like a
    # migration and the one that matters is lost in the noise.
    again = run("--config", str(path), "--migrate")
    record("a second migration reports no changes",
           "No schema changes are required" in again.stdout, again.stdout[:300])

    # Notion only on request, and only when applied.
    asked = run("--config", str(path), "--migrate", "--enable-notion")
    record("--enable-notion is reported in the plan",
           "Enabled optional Notion logging" in asked.stdout, asked.stdout[:300])
    record("--enable-notion still writes nothing without --apply",
           json.loads(path.read_text(encoding="utf-8"))["notion"]["enabled"] is False)

    valid = run("--config", str(path), "--validate")
    record("the migrated configuration validates", valid.returncode == 0,
           valid.stdout + valid.stderr)

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    path = Path(temp) / "config.json"
    broken = dict(LEGACY)
    broken["monitor"] = {"failure_minutes_before_reboot": 15,
                         "reboot_cooldown_minutes": 30, "probe_urls": []}
    path.write_text(json.dumps(broken), encoding="utf-8")
    invalid = run("--config", str(path), "--validate")
    record("an invalid configuration is refused", invalid.returncode == 1,
           f"exit {invalid.returncode}")
    record("the refusal names the predicate that failed",
           "probe_urls" in invalid.stderr, invalid.stderr[:200])

missing = run("--config", str(ROOT / "no-such-config-4f1a.json"), "--validate")
record("a missing configuration is refused", missing.returncode == 2,
       f"exit {missing.returncode}")

no_mode = run("--config", str(ROOT / "config.example.json"))
record("the tool refuses to guess what it was asked to do", no_mode.returncode != 0,
       f"exit {no_mode.returncode}")

# The PowerShell entry point must delegate rather than reimplement, or the two
# platforms drift into disagreeing about what a current configuration is.
wrapper = (ROOT / "scripts" / "Migrate-LocalConfig.ps1").read_text(encoding="utf-8")
record("the PowerShell entry point delegates to this tool",
       "configure.py" in wrapper, "no reference to configure.py found")
record("the PowerShell entry point holds no migration logic of its own",
       "https_port" not in wrapper and "config_version" not in wrapper,
       "the wrapper still contains schema knowledge")

print(json.dumps({"suite": "configure", "results": results}, indent=2))
print("configuration tool smoke test: OK")
