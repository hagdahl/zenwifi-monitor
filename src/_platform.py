"""Platform-specific behaviour behind one small interface.

Standard library only, deliberately. `src/health.py` is an independent
observer that must keep working when the project's third-party dependencies
do not, so anything it imports has to carry no dependency of its own. Keeping
this module standard-library-only is what lets both entry points share it.

Two behaviours differ per platform and nothing else in the project should
branch on the operating system:

* showing a notice to whoever is sitting at the machine, and
* starting a detached child process without raising a window.

The rule for both is the same: a notice is best effort and never raises. A
monitor that crashes because it could not draw a dialog is worse than a
monitor that stays quiet, and on an unattended host there may be no session
to draw into at all.
"""
# ZenWiFi Monitor version: 0.1.0
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# Severity is expressed platform-neutrally; each implementation maps it to
# whatever its own notification surface understands.
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"

_WINDOWS_ICON = {LEVEL_WARNING: 0x30, LEVEL_INFO: 0x40}
_POSIX_URGENCY = {LEVEL_WARNING: "critical", LEVEL_INFO: "normal"}

# A message box is modal. Under a scheduler with no visible desktop it would
# never be dismissed and the process would never exit, which is exactly how a
# single-instance job wedges itself. Callers therefore always reach the notice
# through a detached child (see spawn_detached), never inline.
MESSAGE_LIMIT = 900


def windowless_interpreter() -> str:
    """The interpreter that starts without a console window, if there is one."""
    if IS_WINDOWS:
        candidate = Path(sys.executable).with_name("pythonw.exe")
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def spawn_detached(arguments: list[str]) -> bool:
    """Start a child process that outlives this one and raises no window.

    Returns True when the child was started. Never raises: the caller is
    always a monitoring path where failing to spawn a notice must not end the
    run that produced the finding.
    """
    try:
        if IS_WINDOWS:
            subprocess.Popen(arguments, close_fds=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            # start_new_session detaches the child from this process group, so
            # it survives the scheduler reaping the parent, and stdio is sent
            # to the null device because a service has no terminal to write to.
            with open("/dev/null", "wb") as sink:
                subprocess.Popen(arguments, close_fds=True, start_new_session=True,
                                 stdout=sink, stderr=sink)
        return True
    except (OSError, ValueError):
        return False


def show_notice(title: str, message: str, level: str = LEVEL_WARNING) -> bool:
    """Show a notice to the interactive user. Returns True if one was shown.

    A False return is a normal outcome, not an error: an unattended host may
    have no session to notify. The caller records the finding regardless; the
    notice is an extra, never the record itself.
    """
    text = message[:MESSAGE_LIMIT]
    try:
        if IS_WINDOWS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, _WINDOWS_ICON.get(level, 0x30))
            return True
        return _posix_notice(title, text, level)
    except Exception:
        # Deliberately broad. Every notification surface has its own failure
        # modes, and none of them justify ending a monitoring run.
        return False


def _posix_notice(title: str, message: str, level: str) -> bool:
    """Use the desktop notification service when a user session is present.

    Without a session bus there is no desktop to notify, so this reports False
    rather than guessing. The caller's log and database record remain the
    durable evidence; on a headless host those are the whole story.
    """
    import os

    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not os.environ.get("WAYLAND_DISPLAY") \
            and not os.environ.get("DISPLAY"):
        return False
    binary = shutil.which("notify-send")
    if binary is None:
        return False
    urgency = _POSIX_URGENCY.get(level, "normal")
    completed = subprocess.run([binary, "--urgency", urgency, title, message],
                               capture_output=True, check=False, timeout=10)
    return completed.returncode == 0
