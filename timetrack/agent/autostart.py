"""Per-user "launch on startup" switch for the agent.

Windows: the installer adds a Startup-folder shortcut; we toggle that plus an
HKCU Run entry. Linux: the .deb ships /etc/xdg/autostart; a per-user copy in
~/.config/autostart with Hidden=true/false overrides it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DESKTOP_ID = "com.euclidee.esstracker.Agent.desktop"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "esstracker"


def _startup_shortcut() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "esstracker.lnk"


def _win_run_value() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, _RUN_NAME)[0])
    except OSError:
        return None


def _user_desktop() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / DESKTOP_ID


def _system_desktop() -> Path:
    return Path("/etc/xdg/autostart") / DESKTOP_ID


def is_supported() -> bool:
    return sys.platform == "win32" or sys.platform.startswith("linux")


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _startup_shortcut().is_file() or _win_run_value() is not None
    if sys.platform.startswith("linux"):
        user = _user_desktop()
        if user.is_file():
            text = user.read_text(encoding="utf-8", errors="ignore")
            return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text
        return _system_desktop().is_file()
    return False


def set_enabled(enabled: bool) -> None:
    if sys.platform == "win32":
        import winreg

        if enabled:
            if getattr(sys, "frozen", False):
                exe = f'"{sys.executable}"'
            else:
                exe = f'"{sys.executable}" -m timetrack.agent run'
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, exe)
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, _RUN_NAME)
            except OSError:
                pass
            try:
                _startup_shortcut().unlink()
            except OSError:
                pass
        return
    if sys.platform.startswith("linux"):
        user = _user_desktop()
        user.parent.mkdir(parents=True, exist_ok=True)
        user.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=esstracker\n"
            "Exec=esstracker-agent run\n"
            "Icon=esstracker\n"
            "Terminal=false\n"
            f"Hidden={'false' if enabled else 'true'}\n"
            f"X-GNOME-Autostart-enabled={'true' if enabled else 'false'}\n",
            encoding="utf-8",
        )
        return
    raise OSError("launch on startup is not supported on this platform")


__all__ = ["is_enabled", "is_supported", "set_enabled"]
