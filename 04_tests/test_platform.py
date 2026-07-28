# ZenWiFi Monitor version: 0.1.0
"""Smoke test for the platform seam.

`src/_platform.py` and `src/_secrets.py` exist so that exactly two behaviours
branch on the operating system — showing a notice and starting a detached
child — and so the credential rule is enforced in one place. This suite pins
the properties that make that seam worth having:

* the seam is standard-library-only, so the health monitor keeps its
  independence from the watchdog's third-party dependencies;
* no other module reaches around it to a platform primitive;
* a notice never raises and never blocks, because a monitor that dies while
  reporting a fault is worse than one that stays quiet;
* the secret store fails closed on the backend rather than on the read.

The suite runs on Windows and on Linux and asserts the same properties on
both. It touches no real machine surface: the backend predicate is exercised
against substituted objects, and the notification surface is substituted too.
That second point is not tidiness. On Windows the real notice is a modal
message box, so a test that called it would block until somebody clicked OK,
hanging an unattended run and interrupting whoever was at the machine. An
earlier revision of this suite did exactly that.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


platform_module = load("_platform")
secrets_module = load("_secrets")
results = []


def record(name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})
    if not passed:
        raise AssertionError(f"{name}: {detail}")


# --- the seam is dependency-free and nothing bypasses it ---------------------

platform_source = (SRC / "_platform.py").read_text(encoding="utf-8")
record("the platform module imports no third-party dependency",
       "keyring" not in platform_source and "asusrouter" not in platform_source)

# The seam is only worth having if the modules behind it stop reaching for the
# primitives directly. A future edit that reintroduces ctypes or pythonw into a
# core module must fail here rather than quietly re-splitting the platforms.
for module_name in ("watchdog", "health"):
    text = (SRC / f"{module_name}.py").read_text(encoding="utf-8")
    for primitive in ("ctypes", "pythonw", "CREATE_NO_WINDOW", "windll"):
        record(f"{module_name}.py does not use {primitive} directly",
               primitive not in text,
               f"{primitive} found in src/{module_name}.py")

record("the health monitor still imports no third-party dependency",
       "keyring" not in (SRC / "health.py").read_text(encoding="utf-8"))

# --- a notice is best effort and never raises -------------------------------

# The real surface is NEVER invoked here. On Windows it is a modal message box:
# a test that calls it blocks until somebody clicks OK, which hangs an
# unattended CI run and interrupts whoever happens to be at the machine. Both
# branches are therefore exercised through a substituted implementation, and
# the seam is what makes that substitution possible at all.
original_posix = platform_module._posix_notice
original_is_windows = platform_module.IS_WINDOWS
try:
    platform_module.IS_WINDOWS = False

    platform_module._posix_notice = lambda title, message, level: True
    shown = platform_module.show_notice("test", "message", platform_module.LEVEL_INFO)
    record("a notice reports whether it was shown", shown is True)

    seen = {}

    def _capture(title, message, level):
        seen["message"] = message
        return True

    platform_module._posix_notice = _capture
    platform_module.show_notice("test", "x" * 5000)
    record("an over-long message is truncated rather than rejected",
           len(seen.get("message", "")) == platform_module.MESSAGE_LIMIT,
           f"passed {len(seen.get('message', ''))} characters")

    # A notification surface that fails must not propagate. Substituting a
    # raising implementation proves the guard, rather than trusting that none
    # of the real surfaces ever fails.
    def _raising(title, message, level):
        raise RuntimeError("notification surface unavailable")

    platform_module._posix_notice = _raising
    record("a failing notification surface is reported, not raised",
           platform_module.show_notice("test", "message") is False)
finally:
    platform_module._posix_notice = original_posix
    platform_module.IS_WINDOWS = original_is_windows

# --- spawning is best effort too --------------------------------------------

record("spawning a program that does not exist reports failure rather than raising",
       platform_module.spawn_detached([str(ROOT / "no-such-program-40e1f2")]) is False)

record("the windowless interpreter exists",
       Path(platform_module.windowless_interpreter()).is_file())

# --- the secret store fails closed on the backend ---------------------------

class _WrongBackend:
    """Stands in for keyring's plaintext or in-memory fallback."""


module_name, class_name = secrets_module.accepted_backend()
record("the accepted backend is named for this platform",
       bool(module_name) and bool(class_name),
       f"{module_name}.{class_name}")


class _RightBackend:
    pass


_RightBackend.__module__ = module_name
_RightBackend.__name__ = class_name
_RightBackend.__qualname__ = class_name


class _FakeKeyring:
    def __init__(self, backend):
        self._backend = backend

    def get_keyring(self):
        return self._backend


original_keyring = sys.modules.get("keyring")
try:
    sys.modules["keyring"] = _FakeKeyring(_WrongBackend())
    refused = False
    try:
        secrets_module.require_persistent_secret_store()
    except RuntimeError as error:
        refused = "protected credential store" in str(error)
    record("a fallback backend is refused", refused)

    sys.modules["keyring"] = _FakeKeyring(_RightBackend())
    accepted = True
    try:
        secrets_module.require_persistent_secret_store()
    except RuntimeError as error:
        accepted = False
        detail = str(error)
    record("this platform's protected store is accepted", accepted,
           "" if accepted else detail)
finally:
    if original_keyring is None:
        sys.modules.pop("keyring", None)
    else:
        sys.modules["keyring"] = original_keyring

# An unsupported platform must be a loud refusal, not a silent fallback to
# whatever backend happens to be active.
original_platform = secrets_module.sys.platform
try:
    secrets_module.sys.platform = "sunos5"
    refused_platform = False
    try:
        secrets_module.accepted_backend()
    except RuntimeError as error:
        refused_platform = "No persistent secret store" in str(error)
    record("an unsupported platform is refused", refused_platform)
finally:
    secrets_module.sys.platform = original_platform

# --- the systemd credential source -------------------------------------------
# An unattended Debian service has no desktop keyring, so systemd's encrypted
# credentials are the store. The presence of the directory is what selects it,
# so these cases substitute the environment rather than a function.

import os
import tempfile

original_environ = os.environ.get(secrets_module.CREDENTIALS_DIRECTORY)
try:
    with tempfile.TemporaryDirectory() as temp:
        credentials = Path(temp)
        (credentials / "router_username").write_text("someone", encoding="utf-8")
        (credentials / "router_password").write_text("secret\n", encoding="utf-8")
        os.environ[secrets_module.CREDENTIALS_DIRECTORY] = str(credentials)

        record("a systemd credential is read from the credentials directory",
               secrets_module.get_secret("router_username") == "someone")
        record("a trailing newline in a credential is stripped",
               secrets_module.get_secret("router_password") == "secret",
               repr(secrets_module.get_secret("router_password")))
        record("an absent credential reads as missing rather than empty",
               secrets_module.get_secret("notion_token") is None)

        # With a credentials directory present the store is acceptable without
        # consulting keyring at all, which is what lets a service run with no
        # desktop session.
        accepted = True
        try:
            secrets_module.require_persistent_secret_store()
        except RuntimeError:
            accepted = False
        record("a systemd credentials directory is an acceptable store", accepted)

    # The directory is gone now, but the variable still points at it: systemd
    # declared credentials and they are not there. That is a misconfiguration
    # and must fail closed rather than fall back to another store.
    refused = False
    try:
        secrets_module.require_persistent_secret_store()
    except RuntimeError as error:
        refused = "CREDENTIALS_DIRECTORY is set but does not name" in str(error)
    record("a declared but missing credentials directory is refused", refused)

    # Being a directory is not evidence. systemd decrypts credentials onto a
    # private tmpfs owned by the run's own user with mode 0700, and that is the
    # entire reason this store is treated as equivalent to a desktop keyring. A
    # plain directory of plaintext files must not be accepted, or a credential
    # ends up on disk unprotected while the project's own fail-closed gate says
    # everything is in order.
    if os.name == "posix":
        with tempfile.TemporaryDirectory() as temp:
            credentials = Path(temp)
            os.environ[secrets_module.CREDENTIALS_DIRECTORY] = str(credentials)
            os.chmod(credentials, 0o700)
            record("a private credentials directory is accepted",
                   secrets_module.credentials_directory() == credentials)

            for mode in (0o750, 0o705, 0o777):
                os.chmod(credentials, mode)
                record(f"a credentials directory with mode {mode:o} is refused",
                       secrets_module.credentials_directory() is None)
                refused_store = False
                try:
                    secrets_module.require_persistent_secret_store()
                except RuntimeError:
                    refused_store = True
                record(f"mode {mode:o} makes the whole store unacceptable rather than a fallback",
                       refused_store)

            os.chmod(credentials, 0o700)
            # Ownership is the other half, and it cannot be tested by creating a
            # directory as somebody else without root. Substituting the identity
            # the check compares against exercises the same branch and restores
            # it immediately, because os is shared with the rest of the process.
            original_getuid = os.getuid
            try:
                os.getuid = lambda: original_getuid() + 1
                record("a credentials directory owned by another user is refused",
                       secrets_module.credentials_directory() is None)
            finally:
                os.getuid = original_getuid
            record("restoring the identity restores acceptance",
                   secrets_module.credentials_directory() == credentials)
    else:
        record("credential directory ownership checks skipped: not a POSIX host", True, "skipped")
finally:
    if original_environ is None:
        os.environ.pop(secrets_module.CREDENTIALS_DIRECTORY, None)
    else:
        os.environ[secrets_module.CREDENTIALS_DIRECTORY] = original_environ

# --- the core modules still import and expose the gate ----------------------

watchdog = load("watchdog")
record("the watchdog imports on this platform without a credential backend",
       hasattr(watchdog, "require_persistent_secret_store"))
record("the watchdog still exposes the shared service name",
       watchdog.SERVICE == secrets_module.SERVICE)

print(json.dumps({"suite": "platform", "results": results}, indent=2))
print("platform seam smoke test: OK")
