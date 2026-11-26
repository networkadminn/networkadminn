"""Windows backend for active-window and idle detection.

Uses the Win32 API via ``pywin32`` when available and falls back to
``psutil`` for the process name. All imports are performed lazily so the
module can be imported on non-Windows hosts (e.g. for tests) without error.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from .common import ActiveWindow

BACKEND_NAME = "windows"


def _dll(name: str):
    """Private DLL handle: setting restype/argtypes here can't affect other libraries
    (uiautomation, pystray) that share ``ctypes.windll``."""
    return ctypes.WinDLL(name, use_last_error=True)  # type: ignore[attr-defined]


try:
    _user32 = _dll("user32")
    _kernel32 = _dll("kernel32")
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _kernel32.GetTickCount.restype = wintypes.DWORD
except (AttributeError, OSError):  # non-Windows host (tests import this module)
    _user32 = _kernel32 = None

# Store / UWP apps (Settings, Calculator, Photos, Mail…) are hosted in a frame
# window owned by this process; the real app is a child window.
_UWP_HOST = "applicationframehost.exe"


def get_active_window() -> ActiveWindow | None:
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        title = _window_text(hwnd)
        pid = _window_pid(hwnd)
        app = _process_name(pid)
        if app.lower() == _UWP_HOST:
            child_pid = _uwp_child_pid(hwnd, pid)
            if child_pid:
                pid = child_pid
                app = _process_name(child_pid)
            elif title:
                # App still starting / suspended: its window title ("Settings",
                # "Calculator") says more than the shared host process name.
                app = title
        return ActiveWindow(app=app, title=title, pid=pid or None)
    except Exception:
        return None


def _window_text(hwnd) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    buff = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buff, length + 1)
    return buff.value or ""


def _window_pid(hwnd) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


_WNDENUMPROC = (
    ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[attr-defined]
    if hasattr(ctypes, "WINFUNCTYPE")
    else None
)


def _uwp_child_pid(hwnd, host_pid: int) -> int:
    """PID of the app hosted inside an ApplicationFrameHost window (0 if none)."""
    found = {"pid": 0}

    def _cb(child, _lparam) -> bool:
        pid = _window_pid(child)
        if pid and pid != host_pid:
            found["pid"] = pid
            return False  # stop enumerating
        return True

    try:
        _user32.EnumChildWindows(hwnd, _WNDENUMPROC(_cb), 0)
    except Exception:
        return 0
    return found["pid"]


def _process_name(pid: int | None) -> str:
    if not pid:
        return "unknown"
    try:
        import psutil  # type: ignore

        return psutil.Process(pid).name()
    except Exception:
        return "unknown"


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def get_idle_seconds() -> float:
    """Seconds since last input via ``GetLastInputInfo``.

    Both tick counts are unsigned 32-bit and wrap every ~49.7 days, so the
    difference is taken modulo 2**32. (ctypes' default *signed* return type for
    ``GetTickCount`` made this negative after ~24.9 days of uptime, which read as
    "never idle".)
    """
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return idle_millis(int(_kernel32.GetTickCount()), int(info.dwTime)) / 1000.0
    except Exception:
        return 0.0


def idle_millis(tick_now: int, last_input_tick: int) -> int:
    """Wrap-safe ``tick_now - last_input_tick`` for 32-bit tick counters."""
    return (tick_now - last_input_tick) & 0xFFFFFFFF


_uia_ready = threading.local()
_address_bars: dict[int, object] = {}
_last_urls: dict[int, str] = {}


def _looks_like_address(value: str) -> bool:
    """Address-bar text, not a page search box or a half-typed query."""
    v = (value or "").strip()
    return bool(v) and " " not in v and ("." in v or "://" in v or v.startswith("localhost"))


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
        hwnd = int(_user32.GetForegroundWindow() or 0)
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
            _last_urls.pop(hwnd, None)
            edit = _find_address_bar(auto, hwnd, window.app)
            if edit is None:
                return ""
            value = _read_value(edit)
            if value is None or (value and not _looks_like_address(value)):
                # First Edit found isn't the address bar (e.g. a find box): don't cache it.
                return ""
            _address_bars[hwnd] = edit
        if _looks_like_address(value):
            _last_urls[hwnd] = value
        # While the user is typing in the address bar, keep the page they are on.
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


def idle_detection_available() -> bool:
    return True
