"""Per-user data/config directories (Windows AppData / XDG / macOS)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def data_dir(app: str = "esstracker") -> Path:
    """Writable per-user data directory (DB, screenshots, lock file)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(_home() / "AppData" / "Local")
        folder = {
            "esstracker": "esstracker",
            "timetrack": "esstracker",
            "timetrack-server": "esstracker-Server",
        }.get(app, app)
        return Path(base) / folder
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / app
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / app
    return _home() / ".local" / "share" / app


def config_dir(app: str = "esstracker") -> Path:
    """Per-user config directory (agent.toml)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(_home() / "AppData" / "Roaming")
        folder = "esstracker" if app in ("esstracker", "timetrack") else app
        return Path(base) / folder
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / app
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / app
    return _home() / ".config" / app


def install_dir() -> Path | None:
    """Directory containing the frozen EXE (Windows), else None."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def expand_path(path: str | os.PathLike[str]) -> str:
    return os.path.expandvars(os.path.expanduser(str(path)))


def sqlite_uri(db_path: str | os.PathLike[str]) -> str:
    p = Path(expand_path(db_path)).resolve()
    return "sqlite:///" + p.as_posix()


__all__ = [
    "config_dir",
    "data_dir",
    "expand_path",
    "install_dir",
    "sqlite_uri",
]
