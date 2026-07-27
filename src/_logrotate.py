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
    """Windows uses %LOCALAPPDATA%; Debian and other POSIX hosts use the home directory."""
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / BOOTSTRAP_DIRECTORY_NAME / BOOTSTRAP_LOG_NAME


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
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate_log(path, max_bytes, retained)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
