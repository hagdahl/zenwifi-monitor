# ZenWiFi Monitor version: 0.1.0
"""Regression tests for the Windows activation surface.

Two things are covered here, both about how a router restart is authorized on
Windows: the silent launcher's argument handling, and the structure of
`scripts/Install.ps1`, which decides what the scheduled task is registered
with.

For the launcher, only the refusal cases are exercised, covering both a value
outside the two-entry whitelist and an argument the wrapper does not recognise
at all. An accepted value makes the wrapper launch the real monitor against the
real local configuration, because the wrapper resolves both from its own
location, so exercising the accept path here would run production code as a
side effect of the test suite. That part is Windows-only and reports itself as
skipped elsewhere, because the wrapper is a Windows Script Host file.

For the installer, the script is parsed with PowerShell's own parser and its
syntax tree examined. It is NOT run: `Install.ps1 -RegisterTask` registers a
scheduled task on the machine executing it, and the machine executing the suite
is the one being monitored. This is therefore weaker than the Debian
counterpart in `test_deploy.py`, which does run its installer inside a
throwaway mount namespace, and the difference is stated rather than papered
over. What the parse establishes is that the only path from `-EnableExecution`
to a registered `--execute` is the one gate, and that the health task cannot
reach it at all.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
WRAPPER = ROOT / "scripts" / "RouterWatchdog.vbs"
INSTALLER = ROOT / "scripts" / "Install.ps1"
results = []


def record(name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(f"{name}: {detail}")


# --- the installer's activation gate, read by PowerShell's own parser --------

ANALYSER = r"""
param([string]$Path)
$parseErrors = $null
$tokens = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$parseErrors)
$report = [ordered]@{}
$report.parseErrors = @($parseErrors | ForEach-Object { $_.Message })
$report.parameters = @($tree.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })

$commands = $tree.FindAll({ param($node)
  $node -is [System.Management.Automation.Language.CommandAst] }, $true)
$report.schtasks = @($commands |
  Where-Object { $_.GetCommandName() -and $_.GetCommandName() -like 'schtasks*' } |
  ForEach-Object { $_.Extent.Text })

$report.guards = @($tree.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.IfStatementAst] }, $true) |
  ForEach-Object {
    [ordered]@{
      condition = $_.Clauses[0].Item1.Extent.Text
      throws = @($_.FindAll({ param($inner)
        $inner -is [System.Management.Automation.Language.ThrowStatementAst] }, $true)).Count
    }
  })

$report.executeLiterals = @($tree.FindAll({ param($node)
    ($node -is [System.Management.Automation.Language.StringConstantExpressionAst]) }, $true) |
  Where-Object { $_.Value -like '*--execute*' } |
  ForEach-Object {
    $enclosing = $null
    $parent = $_.Parent
    while ($parent) {
      if ($parent -is [System.Management.Automation.Language.IfStatementAst]) {
        $enclosing = $parent.Clauses[0].Item1.Extent.Text
        break
      }
      $parent = $parent.Parent
    }
    [ordered]@{ value = $_.Value; enclosingCondition = $enclosing }
  })

$report | ConvertTo-Json -Depth 8
"""

shell = shutil.which("pwsh") or shutil.which("powershell")
if shell:
    with tempfile.TemporaryDirectory() as temp:
        analyser = Path(temp) / "analyse-installer.ps1"
        analyser.write_text(ANALYSER, encoding="utf-8")
        completed = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(analyser), "-Path", str(INSTALLER)],
            capture_output=True, text=True, timeout=180)
        record("the installer could be parsed", completed.returncode == 0,
               completed.stderr[-400:])
        report = json.loads(completed.stdout)

    # A syntax error in the installer would otherwise only surface when an
    # operator ran it, halfway through an install.
    record("the installer has no syntax errors", not report["parseErrors"],
           str(report["parseErrors"]))

    record("the installer still declares the activation switch",
           "EnableExecution" in report["parameters"], str(report["parameters"]))

    # -EnableExecution alone must not be usable: it only has meaning together
    # with the registration it modifies, and a guard that throws is what says so.
    gate = [guard for guard in report["guards"]
            if "EnableExecution" in guard["condition"] and "RegisterTask" in guard["condition"]]
    record("the activation switch is refused without the registration it modifies",
           gate and all(guard["throws"] for guard in gate), str(report["guards"]))

    creates = [text for text in report["schtasks"] if "/Create" in text]
    record("exactly two scheduled tasks are registered", len(creates) == 2, str(creates))
    watchdog_create = [text for text in creates if "ZenWiFiMonitorHealth" not in text]
    health_create = [text for text in creates if "ZenWiFiMonitorHealth" in text]
    record("one of them is the watchdog task", len(watchdog_create) == 1, str(creates))
    record("and one is the health task", len(health_create) == 1, str(creates))

    # The health task is the observer. There must be no expression in its
    # registration that could ever evaluate to --execute — not the literal, and
    # not the variable that carries it.
    record("the health task registration cannot carry --execute",
           "--execute" not in health_create[0] and "executionArgument" not in health_create[0],
           health_create[0])

    # The watchdog task gets --execute only through the gated variable. A
    # literal here would mean the flag no longer depends on the switch.
    record("the watchdog task takes --execute only from the gated variable",
           "$executionArgument" in watchdog_create[0] and "--execute" not in watchdog_create[0],
           watchdog_create[0])

    # And that variable is assigned only inside a branch on the switch. Prose
    # that mentions the flag — the warning printed after activation, the health
    # task's refusal message — is not the flag, so only literals that are
    # exactly the argument count here.
    flags = [item for item in report["executeLiterals"] if item["value"].strip() == "--execute"]
    gated = [item for item in flags
             if item["enclosingCondition"] and "EnableExecution" in item["enclosingCondition"]]
    other = [item for item in flags if item not in gated]
    record("the --execute argument is produced only under the switch",
           len(gated) == 1, str(flags))
    # The remaining occurrence is the health task's own guard, which exists to
    # refuse the flag rather than to pass it.
    record("any other occurrence of the flag is a refusal, not a use",
           all("-match" in (item["enclosingCondition"] or "") for item in other),
           str(other))

    # Both tasks go through the silent wrapper. A task that ran python.exe
    # directly would flash a console window every five minutes, which is how
    # this project ended up with a VBS launcher in the first place.
    for label, text in (("watchdog", watchdog_create[0]), ("health", health_create[0])):
        record(f"the {label} task runs through the silent wrapper",
               "wscript.exe" in text and "RouterWatchdog.vbs" not in text.replace("$wrapper", ""),
               text)
else:
    record("installer structure checks skipped: no PowerShell on this host", True, "skipped")

# --- the silent launcher's whitelist ----------------------------------------

# The refused values are deliberately synthetic. A realistic system path here
# would trip a publication scan on every future run and train the reader to
# ignore it, so the absolute-path case uses a drive letter that cannot exist.
REFUSED = [
    "--script=src\\evil.py",
    "--script=..\\..\\evil.py",
    "--script=src\\..\\..\\evil.py",
    "--script=X:\\absolute\\evil.py",
    "--script=src/health.py.",
    "--script=src\\health.py ",
    "--script=src\\health.py:stream",
    "--script=",
    # An argument the wrapper does not recognise must be refused, not ignored:
    # a single-dash typo previously launched the default job silently.
    "-script=src\\health.py",
    "--scripts=src\\health.py",
    "/script:src\\health.py",
    "--executenow",
    "--dry-run",
    "src\\health.py",
]

if sys.platform != "win32":
    record("launcher checks skipped: the launcher is a Windows Script Host file",
           True, "skipped")
else:
    for argument in REFUSED:
        completed = subprocess.run(["cscript.exe", "//nologo", "//B", str(WRAPPER), argument],
                                   capture_output=True, text=True, timeout=60)
        record(f"the launcher refuses {argument!r}", completed.returncode == 2,
               f"exit code was {completed.returncode}")

print(json.dumps({"suite": "wrapper", "checks": len(results), "failures": 0,
                  "result": "green", "findings": results}, indent=2))
print("windows activation surface test: OK")
