# ZenWiFi Monitor version: 0.1.0
"""Regression test for the silent launcher's entry-point whitelist.

Only the refusal cases are exercised. An accepted value makes the wrapper launch
the real monitor against the real local configuration, because the wrapper
resolves both from its own location, so exercising the accept path here would
run production code as a side effect of the test suite.

The test is Windows-only and reports itself as skipped elsewhere, because the
wrapper is a Windows Script Host file.
"""
import json
import subprocess
import sys
from pathlib import Path

WRAPPER = Path(__file__).parents[1] / "scripts" / "RouterWatchdog.vbs"
REFUSED = [
    "--script=src\\evil.py",
    "--script=..\\..\\evil.py",
    "--script=src\\..\\..\\evil.py",
    "--script=C:\\Windows\\System32\\evil.py",
    "--script=src/health.py.",
    "--script=src\\health.py ",
    "--script=src\\health.py:stream",
    "--script=",
]
results = []

if sys.platform != "win32":
    print(json.dumps({"suite": "wrapper", "result": "skipped",
                      "reason": "the launcher is a Windows Script Host file"}, indent=2))
    print("wrapper whitelist test: SKIPPED")
    raise SystemExit(0)

for argument in REFUSED:
    completed = subprocess.run(["cscript.exe", "//nologo", "//B", str(WRAPPER), argument],
                               capture_output=True, text=True, timeout=60)
    passed = completed.returncode == 2
    results.append({"argument": argument, "exit_code": completed.returncode, "passed": passed})
    if not passed:
        raise AssertionError(f"{argument!r} was not refused; exit code was {completed.returncode}")

print(json.dumps({"suite": "wrapper", "checks": len(results), "failures": 0,
                  "result": "green", "findings": results}, indent=2))
print("wrapper whitelist test: OK")
