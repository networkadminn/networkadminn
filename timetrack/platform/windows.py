"""Windows backend for active-window and idle detection.

Uses the Win32 API via ``pywin32`` when available and falls back to
``psutil`` for the process name. All imports are performed lazily so the
module can be imported on non-Windows hosts (e.g. for tests) without error.
"""

from __future__ import annotations

import ctypes
import os
import threading
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


_uia_ready = threading.local()
_address_bars: dict[int, object] = {}
_last_urls: dict[int, str] = {}


def get_browser_url(window: ActiveWindow | None) -> str:
    """Address-bar text of the foreground browser via UI Automation ('' if unknown).

    Chromium (Chrome / Edge / Brave / Opera / Vivaldi) and Firefox expose the
    address bar as an Edit control. The control is cached per window so a
    normal sample is a single property read.
    """
    if window is None:
        return ""
    try:
        import uiautomation as auto  # type: ignore
    except Exception:
        return ""
    try:
        hwnd = int(ctypes.windll.user32.GetForegroundWindow())  # type: ignore[attr-defined]
        if not hwnd:
            return ""
        if not getattr(_uia_ready, "done", False):
            auto.InitializeUIAutomationInCurrentThread()
            auto.SetGlobalSearchTimeout(1.0)
            _uia_ready.done = True

        if len(_address_bars) > 64:
            _address_bars.clear()
            _last_urls.clear()

        edit = _address_bars.get(hwnd)
        value = _read_value(edit) if edit is not None else None
        if value is None:
            edit = _find_address_bar(auto, hwnd, window.app)
            if edit is None:
                return _last_urls.get(hwnd, "")
            _address_bars[hwnd] = edit
            value = _read_value(edit)
        if value:
            _last_urls[hwnd] = value
        return _last_urls.get(hwnd, "")
    except Exception:
        return ""


def _find_address_bar(auto, hwnd: int, app: str):
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return None
    candidates = []
    if "firefox" in (app or "").lower():
        candidates.append(root.EditControl(searchDepth=16, AutomationId="urlbar-input"))
    candidates.append(root.EditControl(searchDepth=16, foundIndex=1))
    for edit in candidates:
        try:
            if edit.Exists(0.5, 0.1):
                return edit
        except Exception:
            continue
    return None


def _read_value(edit) -> str | None:
    """Current address-bar text; None when the cached control is gone."""
    try:
        return edit.GetValuePattern().Value or ""
    except Exception:
        return None


# Silence "imported but unused" for os on some linters; kept for parity.
_ = os
