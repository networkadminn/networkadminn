"""Windows backend for active-window and idle detection.

Uses the Win32 API via ``pywin32`` when available and falls back to
``psutil`` for the process name. All imports are performed lazily so the
module can be imported on non-Windows hosts (e.g. for tests) without error.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from .common import ActiveWindow

BACKEND_NAME = "windows"


def get_active_window() -> ActiveWindow | None:
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except Exception:
        return _get_active_window_ctypes()

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd) or ""
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        app = _process_name(pid)
        return ActiveWindow(app=app, title=title, pid=pid or None)
    except Exception:
        return None


def _get_active_window_ctypes() -> ActiveWindow | None:
    """Pure-ctypes fallback that avoids the pywin32 dependency."""
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value or ""

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = _process_name(pid.value)
        return ActiveWindow(app=app, title=title, pid=pid.value or None)
    except Exception:
        return None


def _process_name(pid: int | None) -> str:
    if not pid:
        return "unknown"
    try:
        import psutil  # type: ignore

        return psutil.Process(pid).name()
    except Exception:
        return "unknown"


def get_idle_seconds() -> float:
    """Seconds since last input via ``GetLastInputInfo``."""

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return 0.0


# Silence "imported but unused" for os on some linters; kept for parity.
_ = os
