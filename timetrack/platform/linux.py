"""Linux backend for active-window and idle detection.

Active window detection strategy (first that works wins):

1. ``xdotool getactivewindow`` -> window name + PID
2. ``xprop`` on the ``_NET_ACTIVE_WINDOW`` reported by the root window

Idle detection strategy:

1. ``xprintidle`` (milliseconds since last input)
2. X11 Screen Saver extension (``libXss``) when ``xprintidle`` is not installed
3. Fall back to ``0.0`` (treated as "active") if neither works

These rely on an X11 session. Wayland does not expose window titles to
unprivileged clients in a portable way, so on Wayland the app name is
best-effort and the title may be empty.
"""

from __future__ import annotations

import shutil
import subprocess

from .common import ActiveWindow

BACKEND_NAME = "linux"

_TIMEOUT = 1.0


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def get_active_window() -> ActiveWindow | None:
    win = _active_via_xdotool()
    if win is not None:
        return win
    return _active_via_xprop()


def _active_via_xdotool() -> ActiveWindow | None:
    if not _have("xdotool"):
        return None
    win_id = _run(["xdotool", "getactivewindow"])
    if not win_id:
        return None
    win_id = win_id.strip()

    title = (_run(["xdotool", "getwindowname", win_id]) or "").strip()

    pid = None
    pid_out = _run(["xdotool", "getwindowpid", win_id])
    if pid_out and pid_out.strip().isdigit():
        pid = int(pid_out.strip())

    app = _process_name(pid) if pid else "unknown"
    return ActiveWindow(app=app, title=title, pid=pid)


def _active_via_xprop() -> ActiveWindow | None:
    if not _have("xprop"):
        return None
    root = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if not root or "0x" not in root:
        return None
    win_id = "0x" + root.split("0x", 1)[1].strip().split(",")[0].strip()

    props = _run(["xprop", "-id", win_id, "_NET_WM_NAME", "_NET_WM_PID", "WM_CLASS"])
    if not props:
        return None

    title = ""
    pid = None
    app = "unknown"
    for line in props.splitlines():
        if "_NET_WM_NAME" in line and "=" in line:
            title = line.split("=", 1)[1].strip().strip('"')
        elif "_NET_WM_PID" in line and "=" in line:
            raw = line.split("=", 1)[1].strip()
            if raw.isdigit():
                pid = int(raw)
        elif "WM_CLASS" in line and "=" in line:
            parts = [p.strip().strip('"') for p in line.split("=", 1)[1].split(",")]
            if parts:
                app = parts[-1] or app

    if pid:
        app = _process_name(pid)
    return ActiveWindow(app=app, title=title, pid=pid)


def _process_name(pid: int | None) -> str:
    if not pid:
        return "unknown"
    try:
        import psutil  # type: ignore

        return psutil.Process(pid).name()
    except Exception:
        pass
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip() or "unknown"
    except OSError:
        return "unknown"


def get_browser_url(window: ActiveWindow | None) -> str:
    if window is None:
        return ""
    from .linux_browser import browser_url_from_history

    return browser_url_from_history(window.app, window.title)


def _idle_via_xscreensaver() -> float | None:
    """Seconds since last input via XScreenSaverQueryInfo (no xprintidle binary)."""
    try:
        import ctypes
        from ctypes import POINTER, Structure, byref, c_int, c_ulong

        class XScreenSaverInfo(Structure):
            _fields_ = [
                ("window", c_ulong),
                ("state", c_int),
                ("kind", c_int),
                ("til_or_since", c_ulong),
                ("idle", c_ulong),
                ("eventMask", c_ulong),
            ]

        xlib = ctypes.CDLL("libX11.so.6")
        xss = ctypes.CDLL("libXss.so.1")
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XDefaultRootWindow.restype = c_ulong
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xss.XScreenSaverQueryInfo.argtypes = [
            ctypes.c_void_p,
            c_ulong,
            POINTER(XScreenSaverInfo),
        ]
        xss.XScreenSaverQueryInfo.restype = c_int

        display = xlib.XOpenDisplay(None)
        if not display:
            return None
        info = XScreenSaverInfo()
        root = xlib.XDefaultRootWindow(display)
        ok = xss.XScreenSaverQueryInfo(display, root, byref(info))
        xlib.XCloseDisplay(display)
        if not ok:
            return None
        return int(info.idle) / 1000.0
    except (OSError, AttributeError, ValueError):
        return None


def get_idle_seconds() -> float:
    if _have("xprintidle"):
        out = _run(["xprintidle"])
        if out and out.strip().isdigit():
            return int(out.strip()) / 1000.0
    via_xss = _idle_via_xscreensaver()
    if via_xss is not None:
        return via_xss
    return 0.0


def idle_detection_available() -> bool:
    """True when keyboard/mouse idle time can be read on this X11 session."""
    if _have("xprintidle"):
        out = _run(["xprintidle"])
        if out is not None and out.strip().isdigit():
            return True
    return _idle_via_xscreensaver() is not None
