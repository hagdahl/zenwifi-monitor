# ZenWiFi Monitor version: 0.1.0
"""Smoke test for the scheduled dependency review.

The tool it covers is the only part of this project that deliberately reaches
out to the public internet, so the first property this suite establishes is
that no test ever does. Every request goes through one seam, `FETCH`, and every
case here substitutes it. A case that reached PyPI or OSV would be slow,
flaky, and would make the suite's result depend on what somebody else published
this morning.

What the substitution leaves real: the parsing of both requirements files
against the ones actually in the repository, the version comparison, the
severity ordering of the exit codes, and the report.
"""
import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
TOOL = ROOT / "scripts" / "check_dependencies.py"
spec = importlib.util.spec_from_file_location("check_dependencies", TOOL)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

results = []


def record(name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(f"{name}: {detail}")


# --- the files in this repository, parsed for real -----------------------------

direct = checker.read_pins(ROOT / "requirements.in")
locked = checker.read_pins(ROOT / "requirements.lock.txt")
record("the direct dependencies are read", bool(direct), str(direct))
record("the lock file is read and is larger than the direct set",
       len(locked) > len(direct), f"{len(direct)} direct, {len(locked)} locked")
record("every direct dependency appears in the lock file",
       set(direct) <= set(locked), str(sorted(set(direct) - set(locked))))
record("the lock file's comment lines are not read as pins",
       "via" not in locked and "hash" not in locked, str(sorted(locked))[:200])

# Normalisation, because `Foo.Bar`, `foo_bar` and `foo-bar` are one project and
# comparing them as strings would report a phantom drift for ever.
record("names are normalised the way PyPI normalises them",
       checker.normalise("Foo.Bar_baz") == "foo-bar-baz",
       checker.normalise("Foo.Bar_baz"))

# --- version comparison, and what it refuses to compare ------------------------

record("a plain release parses", checker.release_tuple("1.21.3") == (1, 21, 3))
record("an ordinary comparison holds",
       checker.release_tuple("2.0.0") > checker.release_tuple("1.99.99"))
# Numeric segments, not text: "1.10" is newer than "1.9", which string
# comparison gets backwards.
record("ten is newer than nine",
       checker.release_tuple("1.10.0") > checker.release_tuple("1.9.0"))
for candidate in ("2.0.0rc1", "1.0.0b2", "2026.1.dev0", "1.0.0.post1", "not-a-version"):
    record(f"{candidate} is refused rather than guessed at",
           checker.release_tuple(candidate) is None, candidate)
record("and a pre-release is therefore never reported as an upgrade",
       checker._behind("1.0.0", "2.0.0rc1") is False)
record("while a real newer release is", checker._behind("1.0.0", "1.0.1") is True)
record("and an equal one is not", checker._behind("1.0.1", "1.0.1") is False)
record("and an older one is not", checker._behind("2.0.0", "1.9.9") is False)

# --- the newest release, chosen from a substituted index -----------------------

original_fetch = checker.FETCH
try:
    checker.FETCH = lambda url, payload=None: {"releases": {
        "1.0.0": [{"yanked": False}],
        "1.2.0": [{"yanked": False}],
        "1.3.0rc1": [{"yanked": False}],
        # Withdrawn. Recommending it would be worse than saying nothing.
        "1.4.0": [{"yanked": True}, {"yanked": True}],
    }}
    newest, problem = checker.newest_release("anything")
    record("the newest plain, unyanked release is chosen",
           newest == "1.2.0" and problem is None, f"{newest} {problem}")

    checker.FETCH = lambda url, payload=None: {"releases": {"2.0.0rc1": [{"yanked": False}]}}
    newest, problem = checker.newest_release("anything")
    record("an index with nothing comparable is reported, not guessed",
           newest is None and problem == "no comparable release", f"{newest} {problem}")

    def _unreachable(url, payload=None):
        raise OSError("the index could not be reached")

    checker.FETCH = _unreachable
    newest, problem = checker.newest_release("anything")
    record("an unreachable index is reported rather than raised",
           newest is None and problem == "OSError", f"{newest} {problem}")
finally:
    checker.FETCH = original_fetch

# --- vulnerabilities, and that every pin is asked about ------------------------

try:
    asked = {}

    def _osv(url, payload=None):
        body = json.loads(payload.decode("utf-8"))
        asked["queries"] = body["queries"]
        return {"results": [{"vulns": [{"id": "GHSA-test-0001"}]} if index == 0 else {}
                            for index, _ in enumerate(body["queries"])]}

    checker.FETCH = _osv
    findings, problem = checker.known_vulnerabilities({"beta": "2.0", "alpha": "1.0"})
    record("the vulnerability query is answered", problem is None, str(problem))
    # Sorted, so the query order is stable and the answers can be zipped back
    # onto the packages they were asked about.
    record("every pinned distribution is asked about, in a stable order",
           [q["package"]["name"] for q in asked["queries"]] == ["alpha", "beta"],
           str(asked["queries"]))
    record("the exact pinned version is what is asked about",
           [q["version"] for q in asked["queries"]] == ["1.0", "2.0"],
           str(asked["queries"]))
    record("a vulnerability is attributed to the right distribution",
           findings == {"alpha": ["GHSA-test-0001"]}, str(findings))

    checker.FETCH = _unreachable
    findings, problem = checker.known_vulnerabilities({"alpha": "1.0"})
    record("an unreachable vulnerability database is reported rather than raised",
           findings == {} and problem == "OSError", f"{findings} {problem}")
finally:
    checker.FETCH = original_fetch

# --- the verdict, which is what the workflow acts on ---------------------------
# The exit code is the contract, not the wording. A change to the report must
# not be able to change the outcome.

CLEAN = {"direct": {"a": {"pinned": "1.0", "latest": "1.0", "behind": False}},
         "vulnerable": {}, "unreadable": {}, "locked_count": 1}
record("a clean review reports nothing", checker.verdict(CLEAN) == checker.NOTHING_TO_REPORT)

behind = json.loads(json.dumps(CLEAN))
behind["direct"]["a"] = {"pinned": "1.0", "latest": "1.1", "behind": True}
record("an available upgrade is reported", checker.verdict(behind) == checker.UPGRADE_AVAILABLE)

unreadable = json.loads(json.dumps(behind))
unreadable["unreadable"]["a"] = "OSError"
record("a question that could not be answered outranks an available upgrade",
       checker.verdict(unreadable) == checker.COULD_NOT_CHECK,
       "an unreachable vulnerability database looks exactly like a clean one")

vulnerable = json.loads(json.dumps(behind))
vulnerable["unreadable"]["a"] = "OSError"
vulnerable["vulnerable"]["a"] = {"pinned": "1.0", "ids": ["GHSA-test-0001"]}
record("a known vulnerability outranks everything else",
       checker.verdict(vulnerable) == checker.VULNERABILITY_KNOWN)
record("and the severity order is strict",
       checker.VULNERABILITY_KNOWN > checker.COULD_NOT_CHECK > checker.UPGRADE_AVAILABLE
       > checker.NOTHING_TO_REPORT)

# --- the report a person reads -------------------------------------------------

clean_text = checker.describe(CLEAN)
record("a clean report says what was checked",
       "Nothing is behind" in clean_text and "1 direct" in clean_text, clean_text)
record("and does not tell anybody to do anything",
       "What to do" not in clean_text, clean_text)

full_text = checker.describe(vulnerable)
record("a vulnerability is named with a link", "osv.dev/vulnerability/GHSA-test-0001" in full_text)
record("an upgrade names both versions", "1.0 → 1.1" in full_text, full_text)
record("the report never suggests editing the lock by hand",
       "uv pip compile" in full_text and "--generate-hashes" in full_text, full_text)

# --- the tool changes nothing --------------------------------------------------
# It exists because the lock file must not update itself. A version of it that
# wrote to either requirements file would defeat its own purpose.

# Checked through the syntax tree rather than by searching the text. A scan for
# "open(" matches "urlopen(", which the tool must use; this project has been
# caught by that class of check often enough to stop writing them.
source = TOOL.read_text(encoding="utf-8")
tree = ast.parse(source)

WRITING = {"write_text", "write_bytes", "mkdir", "unlink", "rename", "replace",
           "touch", "rmdir", "chmod"}
writes = [node.func.attr for node in ast.walk(tree)
          if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and node.func.attr in WRITING]
record("the tool writes nothing", not writes, f"calls: {writes}")

opens = [node for node in ast.walk(tree)
         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
         and node.func.id == "open"]
record("and opens no file at all", not opens, f"{len(opens)} calls to open()")

OWN = {"_defaults", "_logrotate", "_platform", "_secrets"}
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        imported.add(node.module.split(".")[0])
foreign = sorted(imported - sys.stdlib_module_names - OWN)
record("and imports only the standard library", not foreign, f"foreign: {foreign}")

print(json.dumps({"suite": "dependencies", "checks": len(results), "failures": 0,
                  "result": "green", "findings": results}, indent=2))
print("dependency review smoke test: OK")
