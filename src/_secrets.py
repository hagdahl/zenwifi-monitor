"""The persistent secret store, behind one predicate and one accessor.

The project's rule is that a credential lives in the operating system's own
protected store and nowhere else: not in a file, not in an environment
variable, not in the repository, not on a command line. Keyring will happily
fall back to a plaintext or in-memory backend when the real one is missing,
which would satisfy the code while breaking the rule silently. This module
therefore fails closed on the backend rather than on the read.

Keyring is imported inside the functions rather than at module scope. That is
not decoration: it keeps `src/watchdog.py` importable, and its test suite
runnable, on a platform where the dependency is absent or a backend cannot be
constructed, without ever letting a real monitoring run proceed on an
unacceptable store.
"""
# ZenWiFi Monitor version: 0.1.0
import sys

SERVICE = "ZenWiFiMonitor"

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


def require_persistent_secret_store() -> None:
    """Fail closed unless the active backend is this platform's protected store."""
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
    """Read one secret from the protected store."""
    import keyring

    return keyring.get_password(SERVICE, name)
