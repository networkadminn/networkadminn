"""Release artifact discovery for the public download page."""

from __future__ import annotations

import os
from pathlib import Path

from flask import current_app

# Semantic version shown on the download page (keep in sync with packaging/build.py)
CLIENT_VERSION = "0.2.6"


def releases_dir() -> Path:
    env = os.environ.get("TIMEFORGE_RELEASES_DIR") or os.environ.get(
        "TIMETRACK_RELEASES_DIR"
    )
    if env:
        return Path(env)
    # Prefer server data dir (production), then repo dist/, then static/releases
    try:
        cfg = current_app.config.get("TIMETRACK_SERVER_CONFIG")
        if cfg and getattr(cfg, "data_dir", None):
            p = Path(cfg.data_dir) / "releases"
            if p.is_dir() or True:
                return p
    except RuntimeError:
        pass
    root = Path(__file__).resolve().parents[2]
    for candidate in (
        root / "dist" / "releases",
        root / "timetrack" / "server" / "static" / "releases",
        Path("/www/wwwroot/timetrack/data/releases"),
    ):
        if candidate.is_dir():
            return candidate
    return root / "dist" / "releases"


def _file_info(path: Path) -> dict | None:
    if not path.is_file():
        return None
    size = path.stat().st_size
    if size >= 1024 * 1024:
        size_label = f"{size / (1024 * 1024):.0f} MB"
    elif size >= 1024:
        size_label = f"{size / 1024:.0f} KB"
    else:
        size_label = f"{size} B"
    return {
        "name": path.name,
        "size": size,
        "size_label": size_label,
        "path": path,
    }


def scan_releases() -> dict:
    """Return platform → list of available downloadable builds."""
    base = releases_dir()
    base.mkdir(parents=True, exist_ok=True)

    def pick(*names: str) -> dict | None:
        for n in names:
            info = _file_info(base / n)
            if info:
                return info
        # also search by prefix
        for n in names:
            stem = n.rsplit(".", 1)[0]
            for p in sorted(base.glob(f"{stem}*"), reverse=True):
                info = _file_info(p)
                if info:
                    return info
        return None

    linux_deb = pick(
        f"timeforge_{CLIENT_VERSION}_amd64.deb",
        "timeforge_amd64.deb",
        "timeforge.deb",
        # legacy package name before Timeforge rebrand
        f"esstracker_{CLIENT_VERSION}_amd64.deb",
        "esstracker_amd64.deb",
        "esstracker.deb",
    )
    # Any timeforge_*.deb, then legacy esstracker_*.deb
    if not linux_deb:
        for p in sorted(base.glob("timeforge_*.deb"), reverse=True):
            linux_deb = _file_info(p)
            if linux_deb:
                break
    if not linux_deb:
        for p in sorted(base.glob("esstracker_*.deb"), reverse=True):
            linux_deb = _file_info(p)
            if linux_deb:
                break

    windows_exe = pick(
        f"timeforge-Setup-{CLIENT_VERSION}.exe",
        "timeforge-Setup.exe",
        f"timeforge-Agent-{CLIENT_VERSION}.exe",
        "timeforge-Agent.exe",
        f"esstracker-Setup-{CLIENT_VERSION}.exe",
        "esstracker-Setup.exe",
        f"esstracker-Agent-{CLIENT_VERSION}.exe",
        "esstracker-Agent.exe",
    )
    if not windows_exe:
        for pattern in ("timeforge*.exe", "esstracker*.exe"):
            for p in sorted(base.glob(pattern), reverse=True):
                windows_exe = _file_info(p)
                break
            if windows_exe:
                break

    # Preferred public artifact: install kit zip (exe + install.ps1 + defaults)
    windows_zip = pick(
        f"timeforge-{CLIENT_VERSION}-windows.zip",
        "timeforge-windows.zip",
        f"esstracker-{CLIENT_VERSION}-windows.zip",
        "esstracker-windows.zip",
    )
    if not windows_zip:
        for pattern in (
            "timeforge*-windows.zip",
            "timeforge*windows*.zip",
            "esstracker*-windows.zip",
            "esstracker*windows*.zip",
        ):
            for p in sorted(base.glob(pattern), reverse=True):
                windows_zip = _file_info(p)
                break
            if windows_zip:
                break

    windows_file = windows_zip or windows_exe
    windows_kind = "zip" if windows_zip else ("exe" if windows_exe else None)

    mac_arm = pick(
        f"timeforge-{CLIENT_VERSION}-arm64.dmg",
        "timeforge-arm64.dmg",
        "timeforge-apple-silicon.dmg",
    )
    mac_intel = pick(
        f"timeforge-{CLIENT_VERSION}-x86_64.dmg",
        "timeforge-intel.dmg",
        "timeforge-x86_64.dmg",
    )
    if not mac_arm and not mac_intel:
        for p in sorted(base.glob("timeforge*.dmg"), reverse=True):
            name = p.name.lower()
            info = _file_info(p)
            if "arm" in name or "silicon" in name or "aarch" in name:
                mac_arm = info
            elif "intel" in name or "x86" in name:
                mac_intel = info
            elif not mac_arm:
                mac_arm = info

    return {
        "version": CLIENT_VERSION,
        "dir": str(base),
        "linux": {
            "deb": linux_deb,
            "ready": linux_deb is not None,
        },
        "windows": {
            "exe": windows_exe,
            "zip": windows_zip,
            "file": windows_file,
            "kind": windows_kind,
            "ready": windows_file is not None,
        },
        "mac": {
            "arm": mac_arm,
            "intel": mac_intel,
            "ready": bool(mac_arm or mac_intel),
        },
    }
