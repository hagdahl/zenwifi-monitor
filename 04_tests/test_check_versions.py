# ZenWiFi Monitor version: 0.1.0
"""Smoke test for the version drift check.

Runs the real code path the CI gate uses, against a throwaway Git repository
in a temporary directory, and emits a machine-readable result so runs are
comparable. It never touches the project's own working tree.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_versions", Path(__file__).parents[1] / "scripts" / "check_versions.py")
check_versions = importlib.util.module_from_spec(spec); spec.loader.exec_module(check_versions)

results = []


def record(name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(f"{name}: {detail}")


def build_repo(root: Path, version: str, changelog_version: str) -> None:
    (root / "VERSION").write_text(version + "\n", encoding="utf-8", newline="")
    (root / "CHANGELOG.md").write_text(
        f"<!-- ZenWiFi Monitor version: {version} -->\n\n# Changelog\n\n## {changelog_version} - Unreleased\n",
        encoding="utf-8", newline="")
    (root / "module.py").write_text(
        f'"""Doc."""\n# ZenWiFi Monitor version: {version}\nvalue = 1\n', encoding="utf-8", newline="")
    (root / "notes.md").write_text(
        f"<!-- ZenWiFi Monitor version: {version} -->\n\n# Notes\n", encoding="utf-8", newline="")
    (root / "config.example.json").write_text(
        json.dumps({"config_version": version, "execution_mode": "dry-run"}, indent=2) + "\n",
        encoding="utf-8", newline="")
    (root / "LICENSE").write_text("Apache License text without any marker.\n", encoding="utf-8", newline="")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def statuses(root: Path) -> dict:
    expected = check_versions.project_version(root)
    return {item["file"]: item["status"] for item in check_versions.inspect(root, expected)}


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    build_repo(root, "1.2.3", "1.2.3")

    found = statuses(root)
    record("green tree reports every tracked file ok",
           set(found.values()) == {"ok"}, f"statuses were {found}")
    record("LICENSE is exempt and not reported", "LICENSE" not in found, f"statuses were {found}")
    record("config.example.json is checked via config_version",
           found.get("config.example.json") == "ok", f"statuses were {found}")
    record("changelog heading matches VERSION",
           check_versions.changelog_version(root) == "1.2.3",
           f"changelog reported {check_versions.changelog_version(root)}")

    # A stale marker must be reported, not tolerated.
    (root / "module.py").write_text(
        '"""Doc."""\n# ZenWiFi Monitor version: 1.2.2\nvalue = 1\n', encoding="utf-8", newline="")
    record("stale marker is detected", statuses(root)["module.py"] == "stale",
           f"module.py reported {statuses(root)['module.py']}")

    # A missing marker must be reported, not skipped.
    (root / "module.py").write_text('"""Doc."""\nvalue = 1\n', encoding="utf-8", newline="")
    record("missing marker is detected", statuses(root)["module.py"] == "missing-marker",
           f"module.py reported {statuses(root)['module.py']}")

    # Exit codes: 1 on drift.
    argv = sys.argv
    try:
        sys.argv = ["check_versions.py", "--root", str(root), "--json"]
        record("drift exits 1", check_versions.main() == 1)
    finally:
        sys.argv = argv

    # Restore the marker, then break only the changelog heading.
    (root / "module.py").write_text(
        '"""Doc."""\n# ZenWiFi Monitor version: 1.2.3\nvalue = 1\n', encoding="utf-8", newline="")
    (root / "CHANGELOG.md").write_text(
        "<!-- ZenWiFi Monitor version: 1.2.3 -->\n\n# Changelog\n\n## 9.9.9 - Unreleased\n",
        encoding="utf-8", newline="")
    argv = sys.argv
    try:
        sys.argv = ["check_versions.py", "--root", str(root)]
        record("changelog drift alone fails the check", check_versions.main() == 1)
    finally:
        sys.argv = argv

    # A malformed VERSION is an environment error, not a drift finding.
    (root / "VERSION").write_text("not-a-version\n", encoding="utf-8", newline="")
    argv = sys.argv
    try:
        sys.argv = ["check_versions.py", "--root", str(root)]
        record("malformed VERSION exits 2", check_versions.main() == 2)
    finally:
        sys.argv = argv

print(json.dumps({"suite": "check_versions", "checks": len(results),
                  "failures": 0, "result": "green", "findings": results}, indent=2))
print("version marker smoke test: OK")
