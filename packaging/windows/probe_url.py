"""CI check: open Edge on a known page and read it back via the agent's UIA reader.

Usage (Windows): python packaging/windows/probe_url.py https://example.com/esstracker-probe
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, ".")

from timetrack.monitor import clean_url  # noqa: E402
from timetrack.platform import windows as win  # noqa: E402


def _edge_windows() -> list[int]:
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if "msedge" in win._process_name(pid.value).lower():
            found.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return found


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "https://example.com/esstracker-probe"
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    subprocess.Popen([edge, "--no-first-run", "--no-default-browser-check", "--new-window", target])

    import uiautomation as auto

    auto.InitializeUIAutomationInCurrentThread()
    auto.SetGlobalSearchTimeout(1.0)
    want = clean_url(target)
    deadline = time.time() + 60
    seen = ""
    while time.time() < deadline:
        time.sleep(3)
        for hwnd in _edge_windows():
            edit = win._find_address_bar(auto, hwnd, "msedge.exe")
            value = win._read_value(edit) if edit is not None else None
            if value:
                seen = clean_url(value)
                print(f"address bar: {value!r} -> {seen!r}")
                if seen == want:
                    print("PROBE OK")
                    return 0
    print(f"PROBE FAILED: wanted {want!r}, last saw {seen!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
