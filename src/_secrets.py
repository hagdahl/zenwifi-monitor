"""The persistent secret store, behind one predicate and one accessor.

The project's rule is that a credential lives in the operating system's own
protected store and nowhere else: not in a file, not in an environment
variable, not in the repository, not on a command line. Keyring will happily
fall back to a plaintext or in-memory backend when the real one is missing,
which would satisfy the code while breaking the rule silently. This module
therefore fails closed on the backend rather than on the read.

There are two acceptable stores, chosen by how the run was started rather than
by preference:

* a desktop session's protected store, through keyring, which is what an
  interactive Windows or Linux install has; and
* systemd's encrypted credentials, which is what an unattended Debian service
  has, since a service has no desktop keyring to talk to. systemd decrypts them
  into a private tmpfs directory readable only by the service user, never
  written to disk and never swapped, so it satisfies the same rule.

Keyring is imported inside the functions rather than at module scope. That is
not decoration: it keeps `src/watchdog.py` importable, and its test suite
runnable, on a platform where the dependency is absent or a backend cannot be
constructed, without ever letting a real monitoring run proceed on an
unacceptable store. It also means a systemd service never imports keyring at
all, because it never needs it.
"""
# ZenWiFi Monitor version: 0.1.0
import os
import sys
from pathlib import Path

SERVICE = "ZenWiFiMonitor"

# systemd sets this for a unit carrying LoadCredential or LoadCredentialEncrypted.
# Its presence is what tells us this run is a service rather than a desktop
# session, so it decides the store without a configuration flag anybody could
# set wrongly.
CREDENTIALS_DIRECTORY = "CREDENTIALS_DIRECTORY"

# Backends that actually delegate to an OS-protected store. Anything outside
# this set — including keyring's plaintext, in-memory, null and chained
# fallbacks — is refused, because a credential that reaches disk unprotected
# is the failure this project exists to avoid.
_ACCEPTED_BACKENDS = {
    "win32": ("keyring.backends.Windows", "WinVaultKeyring"),
    "linux": ("keyring.backends.SecretService", "Keyring"),
    "darwin": ("keyring.backends.macOS", "Keyring"),
}


def accepted_backend() -> tuple[str, str]:
    """The module and class name of the only backend accepted on this platform."""
    key = "linux" if sys.platform.startswith("linux") else sys.platform
    try:
        return _ACCEPTED_BACKENDS[key]
    except KeyError:
        raise RuntimeError(
            f"No persistent secret store is defined for platform {sys.platform!r}. "
            "Add one before running here; falling back to files or environment "
            "variables is not allowed.") from None


def credentials_directory() -> Path | None:
    """The systemd credentials directory for this run, if there is a real one.

    Being a directory is not evidence. systemd decrypts credentials onto a
    private tmpfs owned by the run's own user with mode 0700, and that is the
    whole basis for treating this store as equivalent to a desktop keyring. A
    plain directory of plaintext files satisfies none of it, and accepting one
    would put credentials in exactly the place the module docstring says they
    may never be — while passing this project's own fail-closed gate, because
    the gate asks this function.

    Ownership and mode are checked on POSIX, where systemd exists and where
    those bits mean what they say. Elsewhere nothing sets the variable, so the
    branch is unreachable in practice and the check is skipped rather than
    approximated against a permission model it does not fit.
    """
    value = os.environ.get(CREDENTIALS_DIRECTORY)
    if not value:
        return None
    path = Path(value)
    if not path.is_dir():
        return None
    if os.name != "posix":
        return path
    try:
        info = path.stat()
    except OSError:
        return None
    if info.st_uid != os.getuid():
        return None
    # Any group or other bit at all: not the directory systemd creates.
    if info.st_mode & 0o077:
        return None
    return path


def require_persistent_secret_store() -> None:
    """Fail closed unless this run has an acceptable protected store.

    A unit that declares credentials but whose directory is missing, or is not
    the private per-run directory systemd creates, is a misconfiguration rather
    than a reason to quietly fall back to a desktop keyring a service cannot
    reach anyway; the environment variable is only set when systemd created the
    directory, so its presence and absence are both meaningful.
    """
    if credentials_directory() is not None:
        return
    if os.environ.get(CREDENTIALS_DIRECTORY):
        raise RuntimeError(
            "CREDENTIALS_DIRECTORY is set but does not name a directory owned by "
            "this user with no group or other access. Either the unit's "
            "LoadCredentialEncrypted is wrong or the variable was set by "
            "something that is not systemd; falling back to another store is "
            "not allowed.")
    import keyring

    module_name, class_name = accepted_backend()
    active = keyring.get_keyring()
    expected = f"{module_name}.{class_name}"
    actual = f"{type(active).__module__}.{type(active).__name__}"
    if actual != expected:
        raise RuntimeError(
            f"The operating system's protected credential store is required. "
            f"Expected {expected}, found {actual}; fallback to files or "
            f"environment variables is not allowed.")


def get_secret(name: str) -> str | None:
    """Read one secret from whichever protected store this run has.

    A credential file's trailing newline is stripped, because `systemd-creds
    encrypt` is normally fed from a here-string or a file that ends in one and
    a password with a stray newline fails authentication in a way that is
    tedious to diagnose.
    """
    directory = credentials_directory()
    if directory is not None:
        candidate = directory / name
        if not candidate.is_file():
            return None
        return candidate.read_text(encoding="utf-8").rstrip("\r\n") or None

    import keyring

    return keyring.get_password(SERVICE, name)
