"""Per-user "launch on startup" switch for the agent.

Windows: the installer adds a Startup-folder shortcut; we toggle that plus an
HKCU Run entry. Linux: the .deb ships /etc/xdg/autostart; a per-user copy in
~/.config/autostart with Hidden=true/false overrides it. macOS: a per-user
LaunchAgent in ~/Library/LaunchAgents (enabled once on first launch of the app).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DESKTOP_ID = "com.euclidee.esstracker.Agent.desktop"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "esstracker"
MAC_LABEL = "com.euclidee.esstracker.agent"


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


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def _agent_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "timetrack.agent", "run"]


def is_supported() -> bool:
    from .msix import is_packaged

    # Store build: the package's startup task owns this (Settings > Apps > Startup).
    if is_packaged():
        return False
    return sys.platform in ("win32", "darwin") or sys.platform.startswith("linux")


def is_enabled() -> bool:
    if sys.platform == "win32":
        from .msix import is_packaged

        if is_packaged():
            return True
        return _startup_shortcut().is_file() or _win_run_value() is not None
    if sys.platform.startswith("linux"):
        user = _user_desktop()
        if user.is_file():
            text = user.read_text(encoding="utf-8", errors="ignore")
            return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text
        return _system_desktop().is_file()
    if sys.platform == "darwin":
        return _mac_plist().is_file()
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
    if sys.platform == "darwin":
        import plistlib

        plist = _mac_plist()
        if not enabled:
            plist.unlink(missing_ok=True)
            return
        plist.parent.mkdir(parents=True, exist_ok=True)
        with open(plist, "wb") as fh:
            plistlib.dump(
                {
                    "Label": MAC_LABEL,
                    "ProgramArguments": _agent_argv(),
                    "RunAtLoad": True,
                    "ProcessType": "Interactive",
                    "LimitLoadToSessionType": "Aqua",
                },
                fh,
            )
        return
    raise OSError("launch on startup is not supported on this platform")


def _mac_temporary_location(path: str) -> bool:
    """Running from the mounted DMG or an App Translocation copy: path won't survive."""
    return path.startswith("/Volumes/") or "/AppTranslocation/" in path


def enable_once_on_macos(state_dir: str) -> None:
    """Packaged macOS app: turn "Launch at login" on the first time it runs.

    Matches the Windows installer and the .deb, which enable autostart by default.
    A marker file means a later "off" from the menu is respected. If the app has
    moved (e.g. dragged to /Applications after a first run elsewhere), an existing
    login item is re-pointed at the current location.
    """
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return
    if _mac_temporary_location(sys.executable):
        print("[esstracker] running from the disk image: move esstracker to Applications")
        return
    plist = _mac_plist()
    if plist.is_file():
        try:
            import plistlib

            with open(plist, "rb") as fh:
                current = plistlib.load(fh).get("ProgramArguments")
            if current != _agent_argv():
                set_enabled(True)
        except Exception as exc:
            print(f"[esstracker] could not update launch at login: {exc!r}")
    marker = Path(state_dir) / "autostart_initialized"
    if marker.exists():
        return
    try:
        set_enabled(True)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
    except OSError as exc:
        print(f"[esstracker] could not enable launch at login: {exc!r}")


__all__ = ["enable_once_on_macos", "is_enabled", "is_supported", "set_enabled"]
