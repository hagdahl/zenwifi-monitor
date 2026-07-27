# ZenWiFi Monitor version: 0.1.0
"""Smoke test for the Debian deployment artefacts.

These files decide, on a platform this project cannot yet run its full suite
on, whether an unattended service can restart a router. The suite therefore
pins the properties that carry the safety model across, and validates the
units with systemd's own parser rather than by reading them hopefully.

What this suite does NOT establish: that the units behave correctly under a
running systemd on a real Debian host. `systemd-analyze verify` parses and
resolves; it does not start anything. The installer has been run end to end on
a Linux system with systemctl substituted, so its file, account and dependency
work is exercised, but no timer has ever fired on a real installation. That
gap is stated in the improvement plan; it is not closed by this suite passing.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
DEPLOY = ROOT / "deploy" / "debian"
results = []


def record(name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(f"{name}: {detail}")


def unit(name):
    return (DEPLOY / name).read_text(encoding="utf-8")


def directive(text, key):
    """Every value assigned to a directive, ignoring comments.

    Reading the whole file would let an explanatory comment satisfy or fail a
    check about behaviour. An earlier revision of this project counted
    `--execute` across a whole unit and got the answer right for the wrong
    reason; the mistake is cheap to make and expensive to trust.
    """
    return [m.group(1).strip() for m in
            re.finditer(rf"(?m)^{re.escape(key)}=(.*)$", text)]


WATCHDOG = unit("zenwifi-monitor.service")
HEALTH = unit("zenwifi-monitor-health.service")
INSTALLER = (DEPLOY / "install.sh").read_text(encoding="utf-8")

# --- activation gate 1: the shipped unit cannot execute ----------------------

exec_lines = directive(WATCHDOG, "ExecStart")
record("the watchdog unit declares exactly one ExecStart", len(exec_lines) == 1,
       f"found {len(exec_lines)}")
record("the shipped watchdog unit does not carry --execute",
       "--execute" not in exec_lines[0], exec_lines[0])
record("the shipped watchdog unit names the real entry point",
       exec_lines[0].endswith("src/watchdog.py --config /etc/zenwifi-monitor/config.json"),
       exec_lines[0])

# The drop-in is the only thing that opens gate 1, and it must clear ExecStart
# first: systemd appends to a list-valued directive, so a drop-in that only adds
# a line would make every timer firing run the monitor twice, once without
# --execute and once with it.
dropin = INSTALLER.split("DROPIN_BODY")[1] if "DROPIN_BODY" in INSTALLER else ""
dropin_exec = directive(dropin, "ExecStart")
record("the execution drop-in clears ExecStart before setting it",
       len(dropin_exec) == 2 and dropin_exec[0] == "",
       f"ExecStart values in the drop-in: {dropin_exec}")
record("the execution drop-in is what adds --execute",
       len(dropin_exec) == 2 and dropin_exec[1].endswith("--execute"),
       str(dropin_exec[-1:]))
record("only --enable-execution writes the drop-in",
       'ENABLE_EXECUTION" -eq 1' in INSTALLER or 'ENABLE_EXECUTION}" -eq 1' in INSTALLER)

# --- the health observer stays an observer -----------------------------------

health_exec = directive(HEALTH, "ExecStart")
record("the health unit runs the health entry point", len(health_exec) == 1
       and health_exec[0].endswith("src/health.py --config /etc/zenwifi-monitor/config.json"),
       str(health_exec))
record("the health unit never carries --execute",
       all("--execute" not in line for line in health_exec))
record("the health unit is given no credentials",
       not directive(HEALTH, "LoadCredentialEncrypted")
       and not directive(HEALTH, "LoadCredential"),
       "the health monitor reads local state only; credentials would widen its blast radius")
record("the health unit is denied network address families",
       directive(HEALTH, "RestrictAddressFamilies") == ["AF_UNIX"],
       str(directive(HEALTH, "RestrictAddressFamilies")))

# --- least privilege on both services ----------------------------------------

for name, text in (("watchdog", WATCHDOG), ("health", HEALTH)):
    for key, expected in (("NoNewPrivileges", "yes"),
                          ("ProtectSystem", "strict"),
                          ("ProtectHome", "yes"),
                          ("CapabilityBoundingSet", ""),
                          ("PrivateTmp", "yes"),
                          ("LockPersonality", "yes"),
                          ("SystemCallArchitectures", "native")):
        record(f"the {name} unit sets {key}={expected or '(empty)'}",
               directive(text, key) == [expected], str(directive(text, key)))
    record(f"the {name} unit runs as the service account",
           directive(text, "User") == ["zenwifi"], str(directive(text, "User")))
    record(f"the {name} unit names every writable path explicitly",
           directive(text, "ReadWritePaths") == ["/var/lib/zenwifi-monitor /var/log/zenwifi-monitor"],
           str(directive(text, "ReadWritePaths")))

# --- the watchdog gets its credentials from systemd, not from a file ---------

credentials = directive(WATCHDOG, "LoadCredentialEncrypted")
record("the watchdog unit loads encrypted credentials", len(credentials) == 3, str(credentials))
record("the credentials are named as src/_secrets.py reads them",
       sorted(c.split(":")[0] for c in credentials)
       == ["notion_token", "router_password", "router_username"], str(credentials))
record("no credential is passed on the command line",
       not any("password" in line.lower() or "token" in line.lower() for line in exec_lines),
       exec_lines[0])

# --- the timers ---------------------------------------------------------------

WATCHDOG_TIMER = unit("zenwifi-monitor.timer")
HEALTH_TIMER = unit("zenwifi-monitor-health.timer")
record("the watchdog runs every five minutes",
       directive(WATCHDOG_TIMER, "OnUnitActiveSec") == ["5min"])
record("the health check runs every fifteen minutes",
       directive(HEALTH_TIMER, "OnUnitActiveSec") == ["15min"])
record("the watchdog timer does not replay missed runs",
       not directive(WATCHDOG_TIMER, "Persistent"),
       "a machine that was off must not write monitoring history it never observed")

# --- the installer defaults to changing nothing -------------------------------

# The installer is a POSIX shell script. A Git Bash on Windows would run the
# dry-run path, but the account and permission work it exists to do has no
# meaning there, so the checks are skipped rather than half-run.
if os.name == "posix" and shutil.which("bash"):
    completed = subprocess.run(["bash", str(DEPLOY / "install.sh")],
                               capture_output=True, text=True, timeout=60)
    record("the installer with no arguments succeeds", completed.returncode == 0,
           completed.stderr[:200])
    record("the installer with no arguments changes nothing and says so",
           "Nothing was changed" in completed.stdout, completed.stdout[:200])
    record("the installer states that execution is off by default",
           "Production execution is OFF" in completed.stdout)

    refused = subprocess.run(["bash", str(DEPLOY / "install.sh"), "--wat"],
                             capture_output=True, text=True, timeout=60)
    record("the installer refuses an unrecognised argument", refused.returncode == 2,
           f"exit {refused.returncode}")

    syntax = subprocess.run(["bash", "-n", str(DEPLOY / "install.sh")],
                            capture_output=True, text=True, timeout=60)
    record("the installer parses", syntax.returncode == 0, syntax.stderr[:200])
else:
    record("installer checks skipped: not a POSIX host", True, "skipped")

# --- systemd's own parser, not our reading of the file -----------------------

analyzer = shutil.which("systemd-analyze")
if analyzer:
    for path in sorted(DEPLOY.glob("zenwifi-monitor*")):
        if path.suffix not in {".service", ".timer"}:
            continue
        completed = subprocess.run([analyzer, "verify", str(path)],
                                   capture_output=True, text=True, timeout=120)
        output = (completed.stdout + completed.stderr).strip()
        # The interpreter lives at an install path that does not exist until
        # install.sh has run, so that one note is expected off a real host and
        # is not a defect in the unit.
        noise = [line for line in output.splitlines()
                 if line.strip() and "is not executable" not in line]
        record(f"systemd accepts {path.name}", not noise, "\n".join(noise))
else:
    record("unit validation skipped: systemd-analyze is unavailable here", True, "skipped")

print(json.dumps({"suite": "deploy", "results": results}, indent=2))
print("debian deployment smoke test: OK")
