#!/usr/bin/env python3
# ZenWiFi Monitor version: 0.1.0
"""Report dependency drift and known vulnerabilities. Changes nothing.

The dependency set is hash-locked, which is what stops a substituted artefact
reaching a service that can restart a router. It is also what stops the set
ever updating on its own, so the same property that makes the install
trustworthy makes it quietly age. This tool is the counterweight: it says what
has moved and what is known to be vulnerable, and then stops.

It deliberately does not upgrade anything. An upgrade means regenerating
`requirements.lock.txt` with `uv pip compile --universal --generate-hashes`,
running both platforms' suites, and deciding. That is a person's job, and a bot
that opened pull requests would either bypass the hash regeneration or guess at
it. What this produces is a list to act on.

Standard library only, for the same reason as the rest of the project's tools:
it must work before the environment exists, and a dependency checker that needs
its own dependencies has a problem it cannot report.

Two questions are asked, of two different services:

* PyPI, for each DIRECT dependency: is there a newer release than the one
  pinned? Transitive pins are not compared, because they are chosen by the
  resolver from the direct constraints; upgrading them is a consequence of a
  direct upgrade, not a decision on its own.
* OSV, for EVERY pinned distribution, direct and transitive: is this exact
  version known to be affected by anything? A vulnerability in a transitive
  dependency is still in the process that holds the router password.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPI = "https://pypi.org/pypi/{name}/json"
OSV = "https://api.osv.dev/v1/querybatch"
USER_AGENT = "ZenWiFiMonitor-dependency-review/1.0"
TIMEOUT = 30

# Exit codes, in increasing severity. The workflow reads these rather than
# parsing the report, so a change to the wording cannot change the outcome.
NOTHING_TO_REPORT = 0
UPGRADE_AVAILABLE = 1
COULD_NOT_CHECK = 2
VULNERABILITY_KNOWN = 3

PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;\\#]+)")


def normalise(name: str) -> str:
    """PEP 503 normalisation, so `Foo.Bar` and `foo-bar` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path: Path) -> dict[str, str]:
    """Every `name==version` in a requirements file, normalised."""
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = PIN.match(line)
        if match:
            pins[normalise(match.group(1))] = match.group(2)
    return pins


def release_tuple(version: str) -> tuple[int, ...] | None:
    """The numeric release segment, or None when this is not a plain release.

    Deliberately crude, and the crudeness is the point: anything carrying a
    letter — a pre-release, a release candidate, a development build — is
    treated as not comparable and skipped rather than guessed at. A full PEP 440
    implementation belongs in `packaging`, which is a dependency this tool
    cannot have. Skipping is the safe direction: it can only cause a newer
    version to go unreported, never a wrong one to be recommended.
    """
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


def fetch(url: str, payload: bytes | None = None) -> dict:
    """One request. Substituted by the suite so no test reaches the network."""
    request = urllib.request.Request(
        url, data=payload,
        headers={"User-Agent": USER_AGENT,
                 **({"Content-Type": "application/json"} if payload else {})})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


# The seam. Production always calls the real one.
FETCH = fetch


def newest_release(name: str) -> tuple[str | None, str | None]:
    """The newest plain release on PyPI, or a reason it could not be read."""
    try:
        data = FETCH(PYPI.format(name=name))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        return None, f"{type(error).__name__}"
    candidates = []
    for version, files in (data.get("releases") or {}).items():
        # A release whose every file is yanked is withdrawn, and recommending
        # it would be worse than saying nothing.
        if isinstance(files, list) and files and all(f.get("yanked") for f in files):
            continue
        parsed = release_tuple(version)
        if parsed is not None:
            candidates.append((parsed, version))
    if not candidates:
        return None, "no comparable release"
    return max(candidates)[1], None


def known_vulnerabilities(pins: dict[str, str]) -> tuple[dict[str, list[str]], str | None]:
    """OSV identifiers per distribution, for these exact versions."""
    queries = [{"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
               for name, version in sorted(pins.items())]
    try:
        data = FETCH(OSV, json.dumps({"queries": queries}).encode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        return {}, f"{type(error).__name__}"
    findings = {}
    for query, result in zip(queries, data.get("results") or []):
        identifiers = [v.get("id") for v in (result.get("vulns") or []) if v.get("id")]
        if identifiers:
            findings[query["package"]["name"]] = sorted(identifiers)
    return findings, None


def review() -> dict:
    """The whole report as data. Formatting is a separate concern."""
    direct = read_pins(ROOT / "requirements.in")
    locked = read_pins(ROOT / "requirements.lock.txt")
    report = {"direct": {}, "vulnerable": {}, "unreadable": {}, "locked_count": len(locked)}

    for name, pinned in sorted(direct.items()):
        if name not in locked:
            report["unreadable"][name] = "pinned in requirements.in but absent from the lock file"
            continue
        latest, problem = newest_release(name)
        if problem:
            report["unreadable"][name] = problem
            continue
        report["direct"][name] = {"pinned": locked[name], "latest": latest,
                                  "behind": _behind(locked[name], latest)}

    vulnerable, problem = known_vulnerabilities(locked)
    if problem:
        report["unreadable"]["osv"] = problem
    else:
        report["vulnerable"] = {name: {"pinned": locked[name], "ids": ids}
                                for name, ids in vulnerable.items()}
    return report


def _behind(pinned: str, latest: str | None) -> bool:
    pinned_parts = release_tuple(pinned)
    latest_parts = release_tuple(latest or "")
    if pinned_parts is None or latest_parts is None:
        return False
    return latest_parts > pinned_parts


def verdict(report: dict) -> int:
    """The most severe thing found. The numbers are ordered by severity.

    A question that could not be answered outranks an available upgrade: an
    unreachable vulnerability database looks exactly like a clean one from
    here, and reporting nothing would be reporting a result nobody obtained.
    """
    if report["vulnerable"]:
        return VULNERABILITY_KNOWN
    if report["unreadable"]:
        return COULD_NOT_CHECK
    if any(entry["behind"] for entry in report["direct"].values()):
        return UPGRADE_AVAILABLE
    return NOTHING_TO_REPORT


def describe(report: dict) -> str:
    """The report a person reads. Written to be pasted into an issue."""
    lines = []
    behind = {name: entry for name, entry in report["direct"].items() if entry["behind"]}
    if report["vulnerable"]:
        lines.append("## Known vulnerabilities in the locked set")
        lines.append("")
        for name, entry in sorted(report["vulnerable"].items()):
            identifiers = ", ".join(
                f"[{i}](https://osv.dev/vulnerability/{i})" for i in entry["ids"])
            lines.append(f"- `{name}` {entry['pinned']} — {identifiers}")
        lines.append("")
        lines.append("These are the versions this project installs. A transitive "
                     "dependency counts: it runs in the process that holds the router "
                     "credentials.")
        lines.append("")
    if behind:
        lines.append("## Newer releases of direct dependencies")
        lines.append("")
        for name, entry in sorted(behind.items()):
            lines.append(f"- `{name}` {entry['pinned']} → {entry['latest']}")
        lines.append("")
    if report["unreadable"]:
        lines.append("## Could not be checked")
        lines.append("")
        for name, reason in sorted(report["unreadable"].items()):
            lines.append(f"- `{name}`: {reason}")
        lines.append("")
        lines.append("An unanswered question is not a clean result, which is why this "
                     "exits non-zero rather than reporting nothing.")
        lines.append("")
    if not (report["vulnerable"] or behind or report["unreadable"]):
        return (f"{len(report['direct'])} direct and "
                f"{report['locked_count']} locked distributions checked. "
                "Nothing is behind and nothing is known to be vulnerable.")
    lines.append("## What to do")
    lines.append("")
    lines.append("Nothing here has been changed. An upgrade means editing "
                 "`requirements.in`, regenerating the lock with")
    lines.append("")
    lines.append("```")
    lines.append("uv pip compile requirements.in --universal --generate-hashes \\")
    lines.append("    --python-version 3.11 -o requirements.lock.txt")
    lines.append("```")
    lines.append("")
    lines.append("running all suites on Windows and on Linux, and deciding. The hashes "
                 "are the point: a lock file edited by hand is a lock file that no "
                 "longer proves anything.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit the report as JSON instead of prose.")
    args = parser.parse_args()
    report = review()
    result = verdict(report)
    if args.as_json:
        print(json.dumps({"verdict": result, "report": report}, indent=2, sort_keys=True))
    else:
        print(describe(report))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
