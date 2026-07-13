"""macOS backend for active-window and idle detection.

Active window: uses Quartz (``pyobjc-framework-Quartz``) to read the
front-most on-screen window's owner + title. Falls back to an AppleScript
query for the frontmost application name when Quartz is unavailable.

Idle: uses ``CGEventSourceSecondsSinceLastEventType`` via Quartz, falling
back to parsing ``ioreg`` ``HIDIdleTime``.

All heavy imports are lazy so this module imports cleanly on non-macOS hosts.
"""

from __future__ import annotations

import subprocess

from .common import ActiveWindow

BACKEND_NAME = "macos"

_TIMEOUT = 1.0


def get_active_window() -> ActiveWindow | None:
    win = _active_via_quartz()
    if win is not None:
        return win
    return _active_via_applescript()


def _active_via_quartz() -> ActiveWindow | None:
    try:
        from Quartz import (  # type: ignore
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )
    except Exception:
        return None

    try:
        options = kCGWindowListOptionOnScreenOnly
        windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []
        for w in windows:
            # Layer 0 == normal application windows; skip menu bar/dock etc.
            if w.get("kCGWindowLayer", 1) != 0:
                continue
            app = w.get("kCGWindowOwnerName") or "unknown"
            title = w.get("kCGWindowName") or ""
            pid = w.get("kCGWindowOwnerPID")
            return ActiveWindow(app=str(app), title=str(title),
                                pid=int(pid) if pid else None)
    except Exception:
        return None
    return None


def _active_via_applescript() -> ActiveWindow | None:
    script = (
        'tell application "System Events" to get name of first application '
        "process whose frontmost is true"
    )
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    app = out.stdout.strip() or "unknown"
    return ActiveWindow(app=app, title="", pid=None)


def get_idle_seconds() -> float:
    try:
        from Quartz import (  # type: ignore
            CGEventSourceSecondsSinceLastEventType,
            kCGAnyInputEventType,
            kCGEventSourceStateHIDSystemState,
        )

        return float(
            CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateHIDSystemState, kCGAnyInputEventType
            )
        )
    except Exception:
        pass
    return _idle_via_ioreg()


def _idle_via_ioreg() -> float:
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    for line in out.stdout.splitlines():
        if "HIDIdleTime" in line and "=" in line:
            raw = line.split("=", 1)[1].strip()
            if raw.isdigit():
                return int(raw) / 1_000_000_000.0  # nanoseconds -> seconds
    return 0.0
