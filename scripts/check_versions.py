"""Verify that every tracked project file carries the current project version.

Design constraints follow the governing work standard:
  * standard library only, so the check keeps working when the project
    virtual environment does not;
  * explicit UTF-8 on every read;
  * read-only, because the task is to check, not to remediate;
  * human summary on stdout plus --json for machine-readable comparison;
  * exit code 0 = green, 1 = drift found, 2 = usage or environment error.

Verified predicate: every path reported by `git ls-files`, excluding the
paths listed in EXEMPT_PATHS, carries a version marker equal to the version
in the VERSION file, and the newest CHANGELOG.md release heading names that
same version.
"""
# ZenWiFi Monitor version: 0.1.0
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MARKER_PATTERN = re.compile(r"ZenWiFi Monitor version:\s*(\d+\.\d+\.\d+)")
CHANGELOG_PATTERN = re.compile(r"^##\s+(\d+\.\d+\.\d+)\b", re.MULTILINE)
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
JSON_VERSION_PATTERN = re.compile(r'"config_version"\s*:\s*"(\d+\.\d+\.\d+)"')

# LICENSE is the verbatim Apache-2.0 text and must not be altered.
# VERSION is the source of truth and needs no marker of its own.
EXEMPT_PATHS = frozenset({"LICENSE", "VERSION"})

# Marker syntax per file name or suffix. A tracked file whose type is absent
# here is reported as unclassified rather than silently skipped.
COMMENT_SYNTAX = {
    ".py": "#", ".ps1": "#", ".yml": "#", ".yaml": "#", ".txt": "#",
    ".gitignore": "#", ".gitattributes": "#",
    ".vbs": "'",
    ".md": "<!--",
    ".json": "json",
}


def classify(relative_path: str) -> str:
    """Return the marker style for a tracked path, or an empty string."""
    name = Path(relative_path).name
    if name in COMMENT_SYNTAX:
        return COMMENT_SYNTAX[name]
    return COMMENT_SYNTAX.get(Path(relative_path).suffix, "")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def tracked_files(root: Path) -> list[str]:
    """List tracked paths. Raises RuntimeError when Git cannot answer."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
    except OSError as error:
        raise RuntimeError(f"Git is required to list tracked files: {error}") from error
    if completed.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {completed.stderr.strip()}")
    return [line for line in completed.stdout.splitlines() if line]


def project_version(root: Path) -> str:
    version_file = root / "VERSION"
    if not version_file.is_file():
        raise RuntimeError("VERSION is missing from the project root.")
    value = read_text(version_file).strip()
    if not SEMVER_PATTERN.match(value):
        raise RuntimeError(f"VERSION must contain a single MAJOR.MINOR.PATCH value, found {value!r}.")
    return value


def changelog_version(root: Path) -> str | None:
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file():
        return None
    match = CHANGELOG_PATTERN.search(read_text(changelog))
    return match.group(1) if match else None


def inspect(root: Path, expected: str) -> list[dict]:
    findings = []
    for relative_path in tracked_files(root):
        if relative_path in EXEMPT_PATHS:
            continue
        path = root / relative_path
        if not path.is_file():
            findings.append({"file": relative_path, "status": "missing", "found": None,
                             "detail": "Tracked file is absent from the working tree."})
            continue
        style = classify(relative_path)
        if not style:
            findings.append({"file": relative_path, "status": "unclassified", "found": None,
                             "detail": "No marker syntax is defined for this file type."})
            continue
        try:
            content = read_text(path)
        except (OSError, UnicodeDecodeError) as error:
            findings.append({"file": relative_path, "status": "unreadable", "found": None,
                             "detail": f"{type(error).__name__}: {error}"})
            continue
        pattern = JSON_VERSION_PATTERN if style == "json" else MARKER_PATTERN
        match = pattern.search(content)
        if match is None:
            findings.append({"file": relative_path, "status": "missing-marker", "found": None,
                             "detail": "No version marker was found."})
        elif match.group(1) != expected:
            findings.append({"file": relative_path, "status": "stale", "found": match.group(1),
                             "detail": f"Marker says {match.group(1)}; VERSION says {expected}."})
        else:
            findings.append({"file": relative_path, "status": "ok", "found": match.group(1), "detail": ""})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Check project version markers against VERSION.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Project root. Defaults to the parent of this script's directory.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit a machine-readable result on stdout.")
    args = parser.parse_args()

    try:
        expected = project_version(args.root)
        findings = inspect(args.root, expected)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    changelog = changelog_version(args.root)
    changelog_ok = changelog == expected
    failures = [item for item in findings if item["status"] != "ok"]
    green = not failures and changelog_ok

    if args.as_json:
        print(json.dumps({
            "predicate": "every git-tracked file except LICENSE and VERSION carries the VERSION marker",
            "expected_version": expected,
            "changelog_version": changelog,
            "changelog_matches": changelog_ok,
            "checked": len(findings),
            "failures": len(failures),
            "result": "green" if green else "failed",
            "findings": findings,
        }, indent=2, sort_keys=True))
    else:
        print(f"Project version (VERSION): {expected}")
        print(f"Newest CHANGELOG release heading: {changelog or 'not found'}")
        print(f"Predicate: every git-tracked file except {', '.join(sorted(EXEMPT_PATHS))} carries that version.")
        print(f"Checked {len(findings)} tracked files; {len(failures)} need attention.")
        if not changelog_ok:
            print(f"  changelog-drift CHANGELOG.md - newest release heading is {changelog or 'absent'}, expected {expected}")
        for item in failures:
            print(f"  {item['status']:<15} {item['file']} - {item['detail']}")
        print("ALL GREEN" if green else "FAILED")
    return 0 if green else 1


if __name__ == "__main__":
    raise SystemExit(main())
