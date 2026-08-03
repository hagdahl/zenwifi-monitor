# ZenWiFi Monitor version: 0.1.0
"""Smoke test for the Debian deployment artefacts.

These files decide, on a platform this project cannot yet run its full suite
on, whether an unattended service can restart a router. The suite therefore
pins the properties that carry the safety model across, and validates the
units with systemd's own parser rather than by reading them hopefully.

What this suite does NOT establish, stated once here rather than implied
anywhere:

* That the units behave correctly under a running systemd on a real Debian
  host. `systemd-analyze verify` parses and resolves; it does not start
  anything, and no timer has ever fired on a real installation.
* That the dependency set installs. The installer is run for real, but the
  environment step is substituted: the stub interpreter fabricates
  `venv/bin/python` as a script that exits zero, so all three `pip` commands —
  including the `--require-hashes` install this project treats as a supply
  chain control — are no-ops here. CI covers that separately by installing the
  lock file for the suite's own interpreter.
* That the service account is created. `useradd` runs only when the account is
  absent, so on a host that already has it the account work is skipped.

The file, ownership and activation work is exercised, on the real script at its
real paths. That gap list is otherwise stated in the improvement plan; it is not
closed by this suite passing.
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
# There used to be a check here named "only --enable-execution writes the
# drop-in", satisfied by finding the string `ENABLE_EXECUTION" -eq 1` anywhere
# in the script. It could not fail for the reason its name gave: an installer
# that also wrote the drop-in from the --install branch still contained that
# string. The property is now established by running a plain --install and
# looking at the machine, further down.

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
    # systemd applies these on EVERY start, not only when it creates the
    # directory, and both default to 0755. Without them the installer's 0750 is
    # widened the first time a timer fires — observed on a real systemd on
    # 3 August 2026, by setting 0750 by hand, starting the service and watching
    # both come back 0755. The behavioural check cannot be reproduced in the
    # mount-namespace harness below, which has no init, so the declaration is
    # what is pinned here and the observation is recorded in the changelog.
    for key in ("StateDirectoryMode", "LogsDirectoryMode"):
        record(f"the {name} unit sets {key}=0750",
               directive(text, key) == ["0750"], str(directive(text, key)))
    record(f"the {name} unit creates files private to the service account",
           directive(text, "UMask") == ["0077"], str(directive(text, "UMask")))

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

# --- the supply chain the installer enforces ----------------------------------
# `pip install --upgrade pip` fetched an unpinned, unhashed distribution and
# then used it to enforce the hash checking below. This is a text check by
# necessity: the behavioural run substitutes the environment step, so the only
# place the absence is observable is the script.
# Comments are stripped first. The script explains at that very line why the
# upgrade was removed, so scanning the whole file would find the words in the
# explanation and fail — the same trap `directive` above exists to avoid, and
# this check walked straight into it on first run.
INSTALLER_CODE = "\n".join(line for line in INSTALLER.splitlines()
                           if not line.lstrip().startswith("#"))
record("the installer does not fetch an unpinned pip before enforcing hashes",
       "--upgrade pip" not in INSTALLER_CODE,
       "pip must not be installed outside the hash-locked set it then verifies")
record("and every dependency install requires hashes",
       INSTALLER_CODE.count("pip install") == INSTALLER_CODE.count("--require-hashes"),
       f"{INSTALLER_CODE.count('pip install')} installs, "
       f"{INSTALLER_CODE.count('--require-hashes')} hash-checked")

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

OVERLAID = ("/etc", "/opt", "/var/lib", "/var/log")

# An identity that is not root and is not the service account. The number is
# arbitrary and nothing on the host needs to own it: the copy it owns lives in
# a temporary directory and is deleted with it.
UNPRIVILEGED_UID = 65123


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


# Everything this install can write, and everything a previous one may have
# left. All of it is removed inside the namespace before the installer runs.
# The unit directory belongs on this list for the same reason as the rest: an
# overlay shows the host's files through its lower layer, so a machine that
# really runs the monitor would otherwise supply the very drop-in whose absence
# is the safety property under test.
INSTALLED_PATHS = (
    "/opt/zenwifi-monitor",
    "/etc/zenwifi-monitor",
    "/var/lib/zenwifi-monitor",
    "/var/log/zenwifi-monitor",
    "/etc/systemd/system/zenwifi-monitor.service",
    "/etc/systemd/system/zenwifi-monitor.timer",
    "/etc/systemd/system/zenwifi-monitor-health.service",
    "/etc/systemd/system/zenwifi-monitor-health.timer",
    "/etc/systemd/system/zenwifi-monitor.service.d",
)


def _run_installer(work: Path, log_directory_mode: int | None = None):
    """Run install.sh in a fresh namespace, probing after each phase.

    Two probes, not one. The property that matters most is what a plain
    `--install` leaves behind, and a harness that runs `--enable-execution`
    before looking cannot see it: an installer that opened activation gate 1
    during a default install would pass. So the state is recorded after
    `--install`, and again after `--enable-execution`.

    Returns the completed process and the two reports. `log_directory_mode`
    restricts /var/log before the install, which is how the refusal path is
    reached: the service account owns its own log directory but cannot traverse
    into it.
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
    for name in OVERLAID:
        (work / "layers" / name.strip("/").replace("/", "-") / "upper").mkdir(parents=True)
        (work / "layers" / name.strip("/").replace("/", "-") / "work").mkdir(parents=True)

    probe = (
        "import json, os, pwd, stat, sys\n"
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
        "report['dropin_directory'] = "
        "Path('/etc/systemd/system/zenwifi-monitor.service.d').is_dir()\n"
        # Gate 2 lives in the installed configuration, so it is read back from
        # the installed file rather than from the template in the checkout.
        "installed = Path('/etc/zenwifi-monitor/config.json')\n"
        "report['execution_mode'] = (json.loads(installed.read_text(encoding='utf-8-sig'))\n"
        "                            .get('execution_mode') if installed.is_file() else None)\n"
        "Path(sys.argv[1]).write_text(json.dumps(report))\n")
    (work / "probe.py").write_text(probe, encoding="utf-8")

    restrict = (f"chmod {log_directory_mode:o} /var/log\n"
                if log_directory_mode is not None else "")
    # The installer is run against a copy of the checkout owned by somebody
    # other than root, because that is the documented situation: `sudo` from a
    # user-owned clone. Run from a root-owned clone the ownership assertions
    # cannot discriminate at all — `cp -a` would preserve root and every check
    # would pass — so the finding they exist to catch would be invisible on any
    # machine where the suite is run with sudo from a root-owned tree.
    source = work / "source"
    shutil.copytree(ROOT, source,
                    ignore=shutil.ignore_patterns("__pycache__", ".git", "_public", "*.sqlite3"))
    for path in [source, *source.rglob("*")]:
        os.chown(path, UNPRIVILEGED_UID, UNPRIVILEGED_UID)
    # Overlays rather than tmpfs, including over /opt. A tmpfs there also hides
    # whatever else the host keeps under /opt — on a GitHub runner that is the
    # Python toolchain this very test runs, so the installer's interpreter
    # vanished mid-install. An overlay masks only what the install writes.
    overlays = "".join(
        f"mount -t overlay overlay -o lowerdir={name},"
        f"upperdir={work}/layers/{name.strip('/').replace('/', '-')}/upper,"
        f"workdir={work}/layers/{name.strip('/').replace('/', '-')}/work {name} "
        f"|| {{ echo \"could not overlay {name}\" >&2; exit 90; }}\n"
        for name in OVERLAID)
    driver = (
        "set -eu\n"
        f"{overlays}"
        # The overlay shows the host's own directories through, so an existing
        # installation on the machine running the suite would otherwise be read
        # as this install's output. Removing them touches the upper layer only.
        f"rm -rf {' '.join(INSTALLED_PATHS)}\n"
        # And prove the slate is clean, rather than assuming the list above is
        # complete. A path that survives means this run would be reporting on
        # somebody else's install, which is worse than not running at all.
        "for path in " + " ".join(INSTALLED_PATHS) + "; do\n"
        "  if [ -e \"$path\" ]; then echo \"$path survived the reset\" >&2; exit 91; fi\n"
        "done\n"
        # A module left behind by an older version must not survive an upgrade,
        # so plant one and let the report say whether it is still importable.
        "mkdir -p /opt/zenwifi-monitor/src\n"
        "touch /opt/zenwifi-monitor/src/_removed_in_a_later_version.py\n"
        f"{restrict}"
        f"export PATH={work}/stub:$PATH\n"
        f"if ! bash {source}/deploy/debian/install.sh --install > {work}/install.out 2> {work}/install.err; then\n"
        f"  echo refused > {work}/refused\n"
        "  exit 0\n"
        "fi\n"
        # Probed before anything is activated. This is the state a default
        # install leaves, and the only place the absence of gate 1 can be seen.
        f"{sys.executable} {work}/probe.py {work}/report-install.json\n"
        f"runuser -u zenwifi -- test -w /var/log/zenwifi-monitor && echo yes > {work}/log_writable || true\n"
        f"bash {source}/deploy/debian/install.sh --enable-execution > {work}/enable.out 2>&1\n"
        f"{sys.executable} {work}/probe.py {work}/report-enable.json\n"
        # Re-installing over an open gate must leave it open — reinstalling is
        # not a request to change the activation state — but it must say so
        # rather than print that execution is off regardless.
        f"bash {source}/deploy/debian/install.sh --install > {work}/reinstall.out 2>&1\n"
        f"{sys.executable} {work}/probe.py {work}/report-reinstall.json\n"
        # And the gate must be closable. It was one-way: nothing in the project
        # removed the drop-in, so the only documented way back was editing
        # systemd's directories by hand.
        f"bash {source}/deploy/debian/install.sh --disable-execution > {work}/disable.out 2>&1\n"
        f"{sys.executable} {work}/probe.py {work}/report-disable.json\n")
    (work / "driver.sh").write_text(driver, encoding="utf-8")

    completed = subprocess.run(
        ["unshare", "--mount", "--propagation", "private", "bash", str(work / "driver.sh")],
        capture_output=True, text=True, timeout=600)

    def _read(name):
        path = work / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    return (completed, _read("report-install.json"), _read("report-enable.json"),
            _read("report-reinstall.json"), _read("report-disable.json"))


skip_reason = _namespace_prerequisites()
if skip_reason is None:
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        completed, report, after_enable, after_reinstall, after_disable = _run_installer(work)
        installer_error = (work / "install.err").read_text(encoding="utf-8") if (work / "install.err").is_file() else ""
        record("the installer completes against a clean prefix",
               completed.returncode == 0 and all(r is not None for r in
                                                 (report, after_enable, after_reinstall, after_disable)),
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

        # The installer copies `-maxdepth 1 -name '*.py'`. Comparing against a
        # top-level-only glob would restate that rule rather than check it, so
        # a subpackage added under src/ would be dropped from the install with
        # this check still green. The expectation is taken from everything the
        # module tree contains, and the restriction the installer relies on is
        # asserted separately — so adding a subpackage fails here, loudly,
        # rather than shipping a Debian install missing part of the program.
        subdirectories = sorted(p.name for p in (ROOT / "src").iterdir()
                                if p.is_dir() and p.name != "__pycache__")
        record("the module tree is flat, which is what the installer assumes",
               not subdirectories,
               f"{subdirectories} would be silently omitted from a Debian install")
        record("the installed modules are exactly the project's modules",
               report["src"] == sorted(p.name for p in (ROOT / "src").rglob("*.py")
                                       if "__pycache__" not in p.parts),
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

        # --- what a default install leaves behind --------------------------
        #
        # Both gates, observed on the installed machine after `--install` and
        # before anything is activated. This is the project's primary safety
        # property and it was previously unpinned: the harness ran
        # --enable-execution before it looked, so an installer that opened
        # gate 1 during a plain --install passed every check.
        installed_exec = directive(report["unit"], "ExecStart")
        record("a default install leaves one ExecStart and it carries no --execute",
               len(installed_exec) == 1 and "--execute" not in installed_exec[0],
               str(installed_exec))
        record("a default install writes no execution drop-in at all",
               report["dropin"] is None and not report["dropin_directory"],
               f"dropin={report['dropin']!r} directory={report['dropin_directory']}")
        record("and gate 2 is closed in the configuration it installed",
               report["execution_mode"] == "dry-run", str(report["execution_mode"]))
        record("the installer says execution is off",
               "Production execution is OFF" in (work / "install.out").read_text(encoding="utf-8"))

        # --- and what --enable-execution changes, and only it ---------------
        record("--enable-execution is what writes the drop-in",
               after_enable["dropin"] is not None and "--execute" in after_enable["dropin"],
               str(after_enable["dropin"]))
        record("the drop-in clears ExecStart before setting it",
               directive(after_enable["dropin"], "ExecStart")[0] == "",
               str(directive(after_enable["dropin"], "ExecStart")))
        # Opening gate 1 must not quietly open gate 2 as well. The two are
        # independent by design and the installer says so in as many words.
        record("opening gate 1 leaves gate 2 exactly as it was",
               after_enable["execution_mode"] == "dry-run",
               str(after_enable["execution_mode"]))
        record("and it does not rewrite the base unit",
               after_enable["unit"] == report["unit"],
               "the drop-in, not the unit, is what carries --execute")

        # --- reinstalling, and closing the gate again -----------------------
        record("re-installing over an open gate leaves it open",
               after_reinstall["dropin"] is not None and "--execute" in after_reinstall["dropin"],
               str(after_reinstall["dropin"]))
        reinstall_output = (work / "reinstall.out").read_text(encoding="utf-8")
        record("and it says so instead of claiming execution is off",
               "ALREADY OPEN" in reinstall_output
               and "Production execution is OFF" not in reinstall_output,
               reinstall_output[-300:])

        record("--disable-execution removes the drop-in",
               after_disable["dropin"] is None and not after_disable["dropin_directory"],
               f"dropin={after_disable['dropin']!r} "
               f"directory={after_disable['dropin_directory']}")
        record("and it leaves the configuration alone",
               after_disable["execution_mode"] == after_enable["execution_mode"],
               f"{after_enable['execution_mode']} -> {after_disable['execution_mode']}")
        record("and it says which gate it closed and which it did not",
               "no longer carries --execute" in (work / "disable.out").read_text(encoding="utf-8"),
               (work / "disable.out").read_text(encoding="utf-8")[-200:])

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
        completed, report, *_ = _run_installer(work, log_directory_mode=0o700)
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
