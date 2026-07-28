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

# --- first-time setup ---------------------------------------------------------
# --init writes a configuration for a host that has none. The properties that
# matter are the ones a first install cannot recover from: a monitor that could
# act immediately, or credentials sent over an unencrypted transport because
# refusing was inconvenient. The TLS probe is substituted so the posture can be
# exercised without a server; the refusal path is also checked for real against
# a port with nothing on it.

import importlib.util

spec = importlib.util.spec_from_file_location("configure", TOOL)
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


def init_module(target, *, tls, extra=()):
    """Call --init in-process so the TLS probe can be substituted."""
    original_probe = configure.PROBE_TLS
    original_argv = sys.argv
    try:
        configure.PROBE_TLS = (lambda host, port, timeout=10.0:
                               {"protocol": "TLSv1.3", "cipher": "test", "subject": "CN=test"}
                               if tls else None)
        sys.argv = ["configure.py", "--config", str(target), "--init",
                    "--router-host", "192.0.2.1", "--router-model", "test-model",
                    "--state-database", str(target.parent / "state.sqlite3"),
                    "--log-directory", str(target.parent / "logs"), *extra]
        return configure.main()
    finally:
        configure.PROBE_TLS = original_probe
        sys.argv = original_argv


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    target = Path(temp) / "config.local.json"

    record("--init with TLS present succeeds as a dry run",
           init_module(target, tls=True) == 0)
    record("--init writes nothing without --apply", not target.exists())

    record("--init with --apply succeeds and the result validates",
           init_module(target, tls=True, extra=("--apply",)) == 0)
    written = json.loads(target.read_text(encoding="utf-8"))
    record("a new configuration is always dry-run",
           written["execution_mode"] == "dry-run", written["execution_mode"])
    record("a new configuration never enables Notion",
           written["notion"]["enabled"] is False)
    record("a new configuration records TLS", written["router"]["use_tls"] is True)
    record("a new configuration carries no template placeholder",
           "<" not in json.dumps(written), json.dumps(written)[:200])

    record("--init refuses to overwrite an existing configuration",
           init_module(target, tls=True, extra=("--apply",)) == 2)

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    target = Path(temp) / "config.local.json"
    record("--init refuses to write when no TLS handshake succeeds",
           init_module(target, tls=False, extra=("--apply",)) == 1)
    record("nothing is written when TLS is refused", not target.exists())

# The same refusal, without substituting anything: port 9 discards traffic and
# never completes a TLS handshake.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    target = Path(temp) / "config.local.json"
    real = run("--config", str(target), "--init", "--router-host", "127.0.0.1",
               "--management-port", "9", "--router-model", "m",
               "--state-database", str(Path(temp) / "s.sqlite3"),
               "--log-directory", str(Path(temp) / "logs"), "--apply")
    record("a real failed handshake refuses too", real.returncode == 1,
           f"exit {real.returncode}: {real.stderr[:160]}")
    record("the refusal explains what to fix",
           "Enable or repair HTTPS" in real.stderr, real.stderr[:200])
    record("the insecure exception is named but not taken",
           "--allow-insecure-http" in real.stderr and not target.exists())

# The insecure path must need more than a flag.
record("the insecure exception requires a typed confirmation",
       "INSECURE_CONFIRMATION" in TOOL.read_text(encoding="utf-8")
       and "input(" in TOOL.read_text(encoding="utf-8"))

# A prompt with nobody to answer it must refuse, not hang. This was found by
# reverting the TLS refusal: the suite stopped responding instead of failing,
# because the confirmation prompt blocked on a stdin no test was going to feed.
# A provisioning run would have hung the same way, silently.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    target = Path(temp) / "config.local.json"
    hung = subprocess.run(
        [sys.executable, str(TOOL), "--config", str(target), "--init",
         "--router-host", "127.0.0.1", "--management-port", "9", "--router-model", "m",
         "--state-database", str(Path(temp) / "s.sqlite3"),
         "--log-directory", str(Path(temp) / "logs"),
         "--allow-insecure-http", "--apply"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    record("a prompt with no terminal refuses instead of blocking",
           hung.returncode != 0, f"exit {hung.returncode}")
    record("the refusal names what it needed and how to supply it",
           "nobody to ask" in hung.stderr and "--help" in hung.stderr, hung.stderr[:200])
    record("nothing is written when the confirmation cannot be given",
           not target.exists())

# --- the router's certificate, recorded rather than verified -----------------
# Nothing here validates the certificate, by design: a home router's is
# self-signed. Recording the fingerprint is what turns "no trust at all" into
# trust on first use, and this is the deliberate half of it.

def certificate_module(target, *, fingerprint, extra=()):
    original_probe = configure.PROBE_TLS
    original_argv = sys.argv
    try:
        configure.PROBE_TLS = (lambda host, port, timeout=10.0:
                               {"protocol": "TLSv1.3", "cipher": "test", "subject": "CN=test",
                                "fingerprint_sha256": fingerprint}
                               if fingerprint else None)
        sys.argv = ["configure.py", "--config", str(target),
                    "--accept-router-certificate", *extra]
        return configure.main()
    finally:
        configure.PROBE_TLS = original_probe
        sys.argv = original_argv


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    target = Path(temp) / "config.local.json"

    record("--init records the fingerprint the router presented",
           init_module(target, tls=True, extra=("--apply",), ) == 0)
    written = json.loads(target.read_text(encoding="utf-8"))
    # The stub in init_module returns no fingerprint, so the key must be absent
    # rather than empty: an empty string is a placeholder, and the monitor's own
    # validator rejects those. Absent means "not recorded yet".
    record("an absent fingerprint is omitted rather than written empty",
           written["router"].get("tls_fingerprint_sha256") is None,
           str(written["router"]))

    record("--accept-router-certificate is a dry run by default",
           certificate_module(target, fingerprint="c" * 64) == 0)
    unchanged = json.loads(target.read_text(encoding="utf-8"))
    record("and it changes nothing without --apply",
           unchanged["router"].get("tls_fingerprint_sha256") is None,
           str(unchanged["router"]))

    record("--accept-router-certificate records it with --apply",
           certificate_module(target, fingerprint="c" * 64, extra=("--apply",)) == 0)
    recorded = json.loads(target.read_text(encoding="utf-8"))
    record("the fingerprint is written to the configuration",
           recorded["router"]["tls_fingerprint_sha256"] == "c" * 64,
           str(recorded["router"]))
    record("and the result still satisfies the monitor's own validator",
           configure.validate(target) == 0)

    record("a router that will not complete a handshake records nothing",
           certificate_module(target, fingerprint=None, extra=("--apply",)) == 1)
    record("and the previously recorded value survives that refusal",
           json.loads(target.read_text(encoding="utf-8"))["router"]["tls_fingerprint_sha256"]
           == "c" * 64)

    # There is no certificate to record on a plain-HTTP configuration, and
    # pretending otherwise would suggest the transport had been checked.
    plain = json.loads(target.read_text(encoding="utf-8"))
    plain["router"]["use_tls"] = False
    plain["router"]["insecure_http_acknowledged"] = True
    target.write_text(json.dumps(plain, indent=2) + "\n", encoding="utf-8")
    record("a plain-HTTP configuration has no certificate to record",
           certificate_module(target, fingerprint="d" * 64, extra=("--apply",)) == 2)

print(json.dumps({"suite": "configure", "results": results}, indent=2))
print("configuration tool smoke test: OK")
