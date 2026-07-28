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

# --- the installer, actually run ----------------------------------------------
#
# Everything above reads the script. That is how an earlier revision concluded
# the installed code was safe: `cp -a` looks harmless in a diff, and only the
# resulting file modes show that it hands an ordinary account write access to
# code systemd then runs as the service user with the router credentials
# decrypted into the process. Ownership is a property of the install, not of
# the source, so it has to be observed on an install.
#
# The script is run verbatim, at its real hardcoded paths, inside a throwaway
# mount namespace: an overlay over /etc and tmpfs over /opt, /var/lib and
# /var/log. Nothing survives the namespace, so this cannot touch a real
# installation even when the suite is run as root on a host that has one.
# Two commands are substituted on PATH and nothing else is: systemctl, which
# would talk to a running init, and the venv branch of python3, which would
# otherwise download and hash-check the whole dependency set on every run.
#
# What this does NOT establish: that systemd starts the units, or that pip
# accepts the lock file. Both are covered elsewhere — the first not at all,
# which is stated above and in the improvement plan.

def _namespace_prerequisites():
    if os.name != "posix":
        return "not a POSIX host"
    if os.geteuid() != 0:
        return "not root"
    for tool in ("unshare", "bash", "install", "find", "runuser", "useradd"):
        if not shutil.which(tool):
            return f"{tool} is unavailable"
    if not Path("/etc/systemd/system").is_dir():
        return "/etc/systemd/system does not exist"
    probe = subprocess.run(
        ["unshare", "--mount", "--propagation", "private", "true"],
        capture_output=True, text=True, timeout=60)
    if probe.returncode != 0:
        return "mount namespaces are unavailable here"
    return None


def _run_installer(work: Path, log_directory_mode: int | None = None):
    """Run install.sh --install (and --enable-execution) in a fresh namespace.

    Returns the parsed report, or the completed process when the install was
    expected to refuse. `log_directory_mode` restricts /var/log before the
    install, which is how the refusal path is reached: the service account owns
    its own log directory but cannot traverse into it.
    """
    stub = work / "stub"
    stub.mkdir(parents=True)
    (stub / "systemctl").write_text(
        f"#!/bin/sh\necho \"$@\" >> {work}/systemctl.log\nexit 0\n", encoding="utf-8")
    # Delegates to the real interpreter for everything except creating the
    # environment, so the script's own Python version gate stays real.
    (stub / "python3").write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"venv\" ]; then\n"
        "  mkdir -p \"$3/bin\"\n"
        "  printf '#!/bin/sh\\nexit 0\\n' > \"$3/bin/python\"\n"
        "  chmod 0755 \"$3/bin/python\"\n"
        "  exit 0\n"
        "fi\n"
        f"exec {sys.executable} \"$@\"\n", encoding="utf-8")
    for path in stub.iterdir():
        path.chmod(0o755)
    (work / "upper").mkdir()
    (work / "workdir").mkdir()

    probe = (
        "import json, os, pwd, stat\n"
        "from pathlib import Path\n"
        "def entry(name):\n"
        "    p = Path(name)\n"
        "    if not p.exists(): return None\n"
        "    st = p.stat()\n"
        "    try: owner = pwd.getpwuid(st.st_uid).pw_name\n"
        "    except KeyError: owner = str(st.st_uid)\n"
        "    try: group = __import__('grp').getgrgid(st.st_gid).gr_name\n"
        "    except KeyError: group = str(st.st_gid)\n"
        "    return {'mode': stat.S_IMODE(st.st_mode), 'owner': owner, 'group': group,\n"
        "            'dir': p.is_dir()}\n"
        "P = '/opt/zenwifi-monitor'\n"
        "report = {'entries': {}}\n"
        "for name in [P, P + '/src', P + '/src/watchdog.py', P + '/src/health.py',\n"
        "             P + '/src/_secrets.py', P + '/scripts/configure.py', P + '/VERSION',\n"
        "             P + '/config.example.json', P + '/requirements.lock.txt',\n"
        "             '/etc/zenwifi-monitor', '/etc/zenwifi-monitor/config.json',\n"
        "             '/etc/systemd/system/zenwifi-monitor.service',\n"
        "             '/etc/systemd/system/zenwifi-monitor.timer',\n"
        "             '/etc/systemd/system/zenwifi-monitor.service.d/10-execute.conf',\n"
        "             '/var/lib/zenwifi-monitor', '/var/log/zenwifi-monitor']:\n"
        "    report['entries'][name] = entry(name)\n"
        "report['src'] = sorted(x.name for x in Path(P + '/src').iterdir())\n"
        "report['unit'] = Path('/etc/systemd/system/zenwifi-monitor.service').read_text()\n"
        "drop = Path('/etc/systemd/system/zenwifi-monitor.service.d/10-execute.conf')\n"
        "report['dropin'] = drop.read_text() if drop.is_file() else None\n"
        "Path(os.environ['REPORT']).write_text(json.dumps(report))\n")
    (work / "probe.py").write_text(probe, encoding="utf-8")

    restrict = (f"chmod {log_directory_mode:o} /var/log\n"
                if log_directory_mode is not None else "")
    driver = (
        "set -eu\n"
        f"mount -t overlay overlay -o lowerdir=/etc,upperdir={work}/upper,workdir={work}/workdir /etc\n"
        "mount -t tmpfs tmpfs /opt\n"
        "mount -t tmpfs tmpfs /var/lib\n"
        "mount -t tmpfs tmpfs /var/log\n"
        # A module left behind by an older version must not survive an upgrade,
        # so plant one and let the report say whether it is still importable.
        "mkdir -p /opt/zenwifi-monitor/src\n"
        "touch /opt/zenwifi-monitor/src/_removed_in_a_later_version.py\n"
        f"{restrict}"
        f"export PATH={work}/stub:$PATH\n"
        f"REPORT={work}/report.json\n"
        "export REPORT\n"
        f"if ! bash {ROOT}/deploy/debian/install.sh --install > {work}/install.out 2> {work}/install.err; then\n"
        f"  echo refused > {work}/refused\n"
        "  exit 0\n"
        "fi\n"
        f"runuser -u zenwifi -- test -w /var/log/zenwifi-monitor && echo yes > {work}/log_writable || true\n"
        f"bash {ROOT}/deploy/debian/install.sh --enable-execution > {work}/enable.out 2>&1\n"
        f"{sys.executable} {work}/probe.py\n")
    (work / "driver.sh").write_text(driver, encoding="utf-8")

    completed = subprocess.run(
        ["unshare", "--mount", "--propagation", "private", "bash", str(work / "driver.sh")],
        capture_output=True, text=True, timeout=600)
    report_path = work / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
    return completed, report


skip_reason = _namespace_prerequisites()
if skip_reason is None:
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        completed, report = _run_installer(work)
        installer_error = (work / "install.err").read_text(encoding="utf-8") if (work / "install.err").is_file() else ""
        record("the installer completes against a clean prefix",
               completed.returncode == 0 and report is not None,
               f"exit {completed.returncode}: {completed.stderr[-400:]} {installer_error[-400:]}")

        entries = report["entries"]

        # F13. The installed code must not be writable by the account that ran
        # the checkout, and must not be group- or world-writable either.
        for name in ("/opt/zenwifi-monitor/src", "/opt/zenwifi-monitor/src/watchdog.py",
                     "/opt/zenwifi-monitor/src/health.py", "/opt/zenwifi-monitor/src/_secrets.py",
                     "/opt/zenwifi-monitor/scripts/configure.py", "/opt/zenwifi-monitor/VERSION",
                     "/opt/zenwifi-monitor/config.example.json",
                     "/opt/zenwifi-monitor/requirements.lock.txt"):
            item = entries[name]
            record(f"{name} was installed", item is not None)
            record(f"{name} is owned by root",
                   item["owner"] == "root" and item["group"] == "root",
                   f"{item['owner']}:{item['group']}")
            record(f"{name} is not writable by group or other",
                   not item["mode"] & 0o022, oct(item["mode"]))

        record("the installed modules are exactly the project's modules",
               report["src"] == sorted(p.name for p in (ROOT / "src").glob("*.py")),
               str(report["src"]))
        record("a module from an older version does not survive the install",
               "_removed_in_a_later_version.py" not in report["src"], str(report["src"]))

        # The operator-facing tools the guide tells people to run must be there.
        record("configure.py is installed and executable",
               entries["/opt/zenwifi-monitor/scripts/configure.py"]["mode"] & 0o111,
               oct(entries["/opt/zenwifi-monitor/scripts/configure.py"]["mode"]))

        # The configuration is the one file the service account may read and
        # nobody else: it will hold a router address and a Notion data source.
        config_entry = entries["/etc/zenwifi-monitor/config.json"]
        record("the configuration template is written for the service account",
               config_entry["owner"] == "root" and config_entry["group"] == "zenwifi"
               and config_entry["mode"] == 0o640,
               f"{config_entry['owner']}:{config_entry['group']} {oct(config_entry['mode'])}")

        for name in ("/var/lib/zenwifi-monitor", "/var/log/zenwifi-monitor"):
            item = entries[name]
            record(f"{name} belongs to the service account",
                   item["owner"] == "zenwifi" and item["group"] == "zenwifi",
                   f"{item['owner']}:{item['group']}")
            record(f"{name} is not readable by other", not item["mode"] & 0o007, oct(item["mode"]))

        # F11. The bootstrap log has nowhere else to go on this platform, so an
        # install that leaves it unwritable leaves crashes unrecorded.
        record("the service account can write its log directory after an install",
               (work / "log_writable").is_file())

        # Both activation gates, observed rather than read.
        installed_exec = directive(report["unit"], "ExecStart")
        record("an installed system cannot restart a router until asked",
               len(installed_exec) == 1 and "--execute" not in installed_exec[0],
               str(installed_exec))
        record("the installer says execution is off",
               "Production execution is OFF" in (work / "install.out").read_text(encoding="utf-8"))
        record("--enable-execution writes the drop-in and nothing else does",
               report["dropin"] is not None and "--execute" in report["dropin"],
               str(report["dropin"]))
        record("the drop-in clears ExecStart before setting it",
               directive(report["dropin"], "ExecStart")[0] == "",
               str(directive(report["dropin"], "ExecStart")))

        calls = (work / "systemctl.log").read_text(encoding="utf-8")
        record("the installer reloads systemd and enables both timers",
               "daemon-reload" in calls
               and "enable --now zenwifi-monitor.timer zenwifi-monitor-health.timer" in calls,
               calls)

    # The refusal path, on its own install: the service account owns its log
    # directory but cannot traverse /var/log to reach it. Nothing later in the
    # script fixes that, so the gate is the only thing that can stop the run.
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        completed, report = _run_installer(work, log_directory_mode=0o700)
        refused = (work / "refused").is_file()
        message = (work / "install.err").read_text(encoding="utf-8") if (work / "install.err").is_file() else ""
        record("an install that would leave the bootstrap log unwritable refuses", refused,
               f"exit {completed.returncode}: {completed.stdout[-300:]}")
        record("and it says which directory the service account cannot write",
               "/var/log/zenwifi-monitor" in message, message[-300:])
else:
    record(f"installer execution checks skipped: {skip_reason}", True, "skipped")

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
