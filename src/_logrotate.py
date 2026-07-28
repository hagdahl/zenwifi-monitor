"""Bounded log writing shared by the watchdog and the health monitor.

Standard library only: these helpers must keep working when the project
virtual environment does not.
"""
# ZenWiFi Monitor version: 0.1.0
import os
from pathlib import Path

MAX_LOG_BYTES = 1024 * 1024
RETAINED_LOG_FILES = 2
BOOTSTRAP_DIRECTORY_NAME = "ZenWiFiMonitor"
BOOTSTRAP_LOG_NAME = "bootstrap-errors.log"


def bootstrap_log_path() -> Path:
    """Where a failure can be recorded before the configured store is available.

    Order matters, and the first entry is the one that was missing. systemd sets
    LOGS_DIRECTORY from `LogsDirectory=`, and on the Debian units that is the
    only place the service may write: the account is created with no home and
    both units set `ProtectHome=yes`, so the previous fallback to the home
    directory resolved somewhere unwritable. That was not merely inconvenient.
    `append_log` is called from inside the top-level exception handlers, so it
    raised over the original error and the crash record was lost entirely, and
    the health monitor's bootstrap-log check short-circuited on a file that
    could never exist — leaving no way at all to see a watchdog that crashed
    after writing its run row.
    """
    logs_directory = os.environ.get("LOGS_DIRECTORY")
    if logs_directory:
        # systemd may pass a colon-separated list; the first entry is ours.
        return Path(logs_directory.split(os.pathsep)[0]) / BOOTSTRAP_LOG_NAME
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / BOOTSTRAP_DIRECTORY_NAME / BOOTSTRAP_LOG_NAME
    return Path.home() / BOOTSTRAP_DIRECTORY_NAME / BOOTSTRAP_LOG_NAME


def rotate_log(path: Path, max_bytes: int = MAX_LOG_BYTES, retained: int = RETAINED_LOG_FILES) -> bool:
    """Rotate before writing so an active record is never overwritten in place."""
    if not path.is_file() or path.stat().st_size < max_bytes:
        return False
    oldest = path.with_name(f"{path.name}.{retained}")
    if oldest.is_file():
        oldest.unlink()
    for index in range(retained - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.is_file():
            os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
    os.replace(path, path.with_name(f"{path.name}.1"))
    return True


def append_log(path: Path, line: str, max_bytes: int = MAX_LOG_BYTES,
               retained: int = RETAINED_LOG_FILES) -> None:
    """Append a line, rotating first when the file has grown past the bound.

    Two processes write these logs, so a rotation can lose the race and fail on
    a file the other one holds open. Appending matters more than rotating, so a
    failed rotation is tolerated and the line is still written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        rotate_log(path, max_bytes, retained)
    except OSError:
        pass
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
