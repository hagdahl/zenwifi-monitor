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
    """The systemd credentials directory for this run, if there is one."""
    value = os.environ.get(CREDENTIALS_DIRECTORY)
    if not value:
        return None
    path = Path(value)
    return path if path.is_dir() else None


def require_persistent_secret_store() -> None:
    """Fail closed unless this run has an acceptable protected store.

    A unit that declares credentials but whose directory is missing is a
    misconfiguration, not a reason to quietly fall back to a desktop keyring
    that a service cannot reach anyway; the environment variable is only set
    when systemd created the directory, so its presence and absence are both
    meaningful.
    """
    if credentials_directory() is not None:
        return
    if os.environ.get(CREDENTIALS_DIRECTORY):
        raise RuntimeError(
            "systemd declared a credentials directory that does not exist. "
            "Check LoadCredentialEncrypted in the unit; falling back to another "
            "store is not allowed.")
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
