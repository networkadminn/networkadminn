"""Build esstracker release artifacts (.exe on Windows, .deb on Linux).

Usage:
    python packaging/build.py exe          # Windows: standalone .exe files
    python packaging/build.py deb          # Linux: .deb with desktop + tray
    python packaging/build.py binaries     # PyInstaller only (current OS)

Build on the target OS (or CI runners for that OS).
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PACKAGING = ROOT / "packaging"
LINUX = PACKAGING / "linux"
VERSION = "0.2.6"
APP_ID = "com.euclidee.esstracker"
PKG_NAME = "esstracker"

# Full set (Windows / local binaries). Employee .deb is client-only.
APPS = (
    ("timetrack", "timetrack.__main__:main"),
    ("timetrack-agent", "timetrack.agent.__main__:main"),
    ("timetrack-server", "timetrack.server.__main__:main"),
)
CLIENT_APPS = (
    ("timetrack-agent", "timetrack.agent.__main__:main"),
)

DATAS = [
    (str(ROOT / "timetrack" / "dashboard" / "templates"), "timetrack/dashboard/templates"),
    (str(ROOT / "timetrack" / "dashboard" / "static"), "timetrack/dashboard/static"),
    (str(ROOT / "timetrack" / "server" / "templates"), "timetrack/server/templates"),
    (str(ROOT / "timetrack" / "server" / "static"), "timetrack/server/static"),
    (str(ROOT / "timetrack" / "agent" / "assets"), "timetrack/agent/assets"),
]

HIDDEN = [
    "engineio.async_drivers.threading",
    "mss",
    "PIL",
    "PIL.ImageTk",
    "pystray",
    "pystray._win32",
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "flask_login",
    "flask_sqlalchemy",
    "sqlalchemy.sql.default_comparator",
    "optparse",
    "gettext",
    "copy",
    "xml",
    "xml.etree",
    "xml.etree.ElementTree",
    "win32api",
    "win32gui",
    "win32process",
    "win32con",
    "pywintypes",
    "pythoncom",
]

WINDOWS = PACKAGING / "windows"


def _run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=ROOT, **kwargs)


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        _run([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"])


def _sep() -> str:
    return ";" if platform.system() == "Windows" else ":"


def _windows_icon() -> Path | None:
    """Build packaging/windows/esstracker.ico from PNG if needed."""
    ico = WINDOWS / "esstracker.ico"
    if ico.is_file():
        return ico
    png = ROOT / "timetrack" / "agent" / "assets" / "ess-mark.png"
    if not png.is_file():
        png = LINUX / "icons" / "esstracker-256.png"
    if not png.is_file():
        return None
    try:
        from PIL import Image

        WINDOWS.mkdir(parents=True, exist_ok=True)
        img = Image.open(png).convert("RGBA")
        sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(ico, format="ICO", sizes=sizes)
        print(f"Wrote Windows icon: {ico}")
        return ico
    except Exception as exc:
        print(f"WARNING: could not build .ico ({exc!r})", file=sys.stderr)
        return None


def build_binaries(*, onefile: bool, apps: tuple[tuple[str, str], ...] | None = None) -> Path:
    """Build PyInstaller binaries for the current platform. Returns output dir."""
    _ensure_pyinstaller()
    out = DIST / ("windows" if platform.system() == "Windows" else "linux")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    work = BUILD / "pyinstaller"
    if work.exists():
        shutil.rmtree(work)

    sep = _sep()
    selected = apps or APPS
    win_icon = _windows_icon() if platform.system() == "Windows" else None
    for name, entry in selected:
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--name",
            name,
            "--distpath",
            str(out),
            "--workpath",
            str(work),
            "--specpath",
            str(work),
            "--paths",
            str(ROOT),
        ]
        if onefile:
            cmd.append("--onefile")
        else:
            cmd.append("--onedir")
        # Agent: no console when launched from Start Menu / Startup
        if name == "timetrack-agent" and platform.system() == "Windows":
            cmd.append("--windowed")
            if win_icon is not None:
                cmd.extend(["--icon", str(win_icon)])
            cmd.extend(["--collect-all", "tkinter", "--collect-all", "pystray"])
        for src, dest in DATAS:
            if Path(src).exists():
                cmd.extend(["--add-data", f"{src}{sep}{dest}"])
        for mod in HIDDEN:
            # Skip win32-only modules on non-Windows hosts
            if platform.system() != "Windows" and mod.startswith(
                ("win32", "pywintypes", "pythoncom", "pystray._win32")
            ):
                continue
            cmd.extend(["--hidden-import", mod])
        for collect in ("flask", "mss", "jinja2", "tzdata"):
            cmd.extend(["--collect-submodules", collect])
        cmd.extend(["--collect-all", "tzdata"])
        # Login window uses Tk on frozen Linux builds (system gi is ABI-incompatible).
        if name == "timetrack-agent" and platform.system() == "Linux":
            cmd.extend(["--collect-all", "tkinter"])
        launcher = work / f"_launch_{name}.py"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        module, func = entry.rsplit(":", 1)
        launcher.write_text(
            f"from {module} import {func}\n\nif __name__ == '__main__':\n    {func}()\n",
            encoding="utf-8",
        )
        cmd.append(str(launcher))
        _run(cmd)

    print(f"Binaries written to {out}")
    return out


def build_exe() -> Path:
    if platform.system() != "Windows":
        print(
            "WARNING: building Windows .exe on non-Windows produces a native binary,\n"
            "not a PE .exe. Run this on Windows (or a Windows CI runner).",
            file=sys.stderr,
        )
    out = build_binaries(onefile=True)
    mapping = {
        "timetrack.exe": "TimeTrack.exe",
        "timetrack-agent.exe": "esstracker-Agent.exe",
        "timetrack-server.exe": "esstracker-Server.exe",
        "timetrack": "TimeTrack.exe" if platform.system() == "Windows" else "TimeTrack",
        "timetrack-agent": (
            "esstracker-Agent.exe" if platform.system() == "Windows" else "esstracker-Agent"
        ),
        "timetrack-server": (
            "esstracker-Server.exe" if platform.system() == "Windows" else "esstracker-Server"
        ),
    }
    for src_name, dst_name in mapping.items():
        src = out / src_name
        dst = out / dst_name
        if src.exists() and src.resolve() != dst.resolve():
            if dst.exists():
                dst.unlink()
            src.rename(dst)
    print(f"Windows release folder: {out}")
    return out


def _install_icons(deb_root: Path) -> None:
    icon_src = LINUX / "icons"
    hicolor = deb_root / "usr" / "share" / "icons" / "hicolor"
    sizes = (16, 22, 24, 32, 48, 64, 128, 256, 512)
    for size in sizes:
        src = icon_src / f"esstracker-{size}.png"
        if not src.exists():
            src = icon_src / "esstracker.png"
        if not src.exists():
            continue
        dest_dir = hicolor / f"{size}x{size}" / "apps"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_dir / "esstracker.png")
    # Also ship a pixmaps fallback
    pix = deb_root / "usr" / "share" / "pixmaps"
    pix.mkdir(parents=True, exist_ok=True)
    master = icon_src / "esstracker-256.png"
    if not master.exists():
        master = icon_src / "esstracker.png"
    if master.exists():
        shutil.copy2(master, pix / "esstracker.png")


def _write_wrapper(path: Path, target: str, *, default_args: str = "") -> None:
    path.write_text(
        "#!/bin/sh\n"
        "# esstracker launcher — Ubuntu 20.04 / 22.04 / 24.04 compatible\n"
        'export ESSTRACKER_ICON="${ESSTRACKER_ICON:-/usr/share/icons/hicolor/256x256/apps/esstracker.png}"\n'
        # Prefer X11 for active-window, idle, and tray backends across Ubuntu releases
        'if [ -z "${ESSTRACKER_FORCE_WAYLAND:-}" ]; then\n'
        '  export GDK_BACKEND="${GDK_BACKEND:-x11}"\n'
        "fi\n"
        # Help AppIndicator / notifications on GNOME
        'export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-$XDG_SESSION_DESKTOP}"\n'
        f'BIN="/opt/esstracker/{target}"\n'
        'if [ ! -x "$BIN" ]; then echo "missing $BIN" >&2; exit 127; fi\n'
        + (
            'if [ "$#" -eq 0 ]; then\n'
            f'  exec "$BIN" {default_args}\n'
            "fi\n"
            if default_args
            else ""
        )
        + 'exec "$BIN" "$@"\n',
        encoding="utf-8",
    )
    os.chmod(path, 0o755)


def build_deb() -> Path:
    if platform.system() != "Linux":
        sys.exit("build deb: only supported on Linux")

    # Employee package: client/agent only (one Applications entry)
    bin_dir = build_binaries(onefile=True, apps=CLIENT_APPS)
    arch = platform.machine()
    deb_arch = {
        "x86_64": "amd64",
        "aarch64": "arm64",
        "armv7l": "armhf",
    }.get(arch, arch)

    deb_root = BUILD / "deb" / f"{PKG_NAME}_{VERSION}_{deb_arch}"
    if deb_root.exists():
        shutil.rmtree(deb_root)

    bindir = deb_root / "usr" / "bin"
    optdir = deb_root / "opt" / "esstracker"
    etcdir = deb_root / "etc" / "esstracker"
    docdir = deb_root / "usr" / "share" / "doc" / PKG_NAME
    appsdir = deb_root / "usr" / "share" / "applications"
    metadird = deb_root / "usr" / "share" / "metainfo"
    autostart = deb_root / "etc" / "xdg" / "autostart"
    debiandir = deb_root / "DEBIAN"
    for d in (bindir, optdir, etcdir, docdir, appsdir, metadird, autostart, debiandir):
        d.mkdir(parents=True)

    for name, _ in CLIENT_APPS:
        src = bin_dir / name
        if not src.exists():
            sys.exit(f"missing binary: {src}")
        dst = optdir / name
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)

    # Client wrappers only
    _write_wrapper(bindir / "esstracker-agent", "timetrack-agent", default_args="run")
    _write_wrapper(bindir / "esstracker", "timetrack-agent", default_args="run")
    _write_wrapper(bindir / "timetrack-agent", "timetrack-agent")

    # Desktop integration — ONE app entry (no Server in Applications)
    _install_icons(deb_root)
    shutil.copy2(
        LINUX / "com.euclidee.esstracker.Agent.desktop",
        appsdir / "com.euclidee.esstracker.Agent.desktop",
    )
    shutil.copy2(
        LINUX / "com.euclidee.esstracker.Agent-autostart.desktop",
        autostart / "com.euclidee.esstracker.Agent.desktop",
    )
    shutil.copy2(
        LINUX / "com.euclidee.esstracker.metainfo.xml",
        metadird / "com.euclidee.esstracker.metainfo.xml",
    )

    # Config examples + baked company server (zero-touch)
    if (ROOT / "agent.example.toml").exists():
        shutil.copy2(ROOT / "agent.example.toml", etcdir / "agent.example.toml")
    if (ROOT / "config.example.toml").exists():
        shutil.copy2(ROOT / "config.example.toml", etcdir / "config.example.toml")
    defaults = LINUX / "defaults.toml"
    if defaults.exists():
        shutil.copy2(defaults, etcdir / "defaults.toml")
    # Keep legacy path symlink note
    legacy_etc = deb_root / "etc" / "timetrack"
    legacy_etc.mkdir(parents=True, exist_ok=True)
    (legacy_etc / "README").write_text(
        "Config moved to /etc/esstracker/ — just Sign in from the app.\n",
        encoding="utf-8",
    )

    readme = docdir / "README.md"
    if (ROOT / "TIMETRACK.md").exists():
        shutil.copy2(ROOT / "TIMETRACK.md", readme)
    else:
        readme.write_text("esstracker — Euclidee Software Solutions\n", encoding="utf-8")
    (docdir / "copyright").write_text(
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        f"Upstream-Name: {PKG_NAME}\n"
        "Source: https://tracker.euclideesolutions.com/\n\n"
        "Files: *\nCopyright: Euclidee Software Solutions\nLicense: MIT\n",
        encoding="utf-8",
    )

    control = f"""Package: {PKG_NAME}
Version: {VERSION}
Section: utils
Priority: optional
Architecture: {deb_arch}
Maintainer: Euclidee Software Solutions <noreply@euclideesolutions.com>
Depends: libc6 (>= 2.31), libgtk-3-0t64 | libgtk-3-0, libnotify-bin, libayatana-appindicator3-1 | libappindicator3-1, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1 | gir1.2-appindicator3-0.1, python3-gi, libx11-6
Recommends: xdotool, x11-utils, xprintidle, gnome-shell-extension-appindicator | gnome-shell-extension-ubuntu-appindicators
Provides: timetrack
Conflicts: timetrack
Replaces: timetrack
Description: esstracker (ESS) — employee desktop tracker
 Client app with system-tray status, notifications, and sync to your
 company esstracker server. Compatible with Ubuntu 20.04 / 22.04 / 24.04
 (X11 recommended; Wayland tray needs AppIndicator).
 .
 Homepage: https://tracker.euclideesolutions.com/
"""
    (debiandir / "control").write_text(control, encoding="utf-8")

    postinst = """#!/bin/sh
set -e
# Remove leftover Server menu entry from older packages
rm -f /usr/share/applications/com.euclidee.esstracker.Server.desktop
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
if command -v update-icon-caches >/dev/null 2>&1; then
  update-icon-caches /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
echo ""
echo "  esstracker (ESS) client installed."
echo "  Open Applications → esstracker → Sign in"
echo "  Supported: Ubuntu 20.04 / 22.04 / 24.04 (amd64)"
echo "  Tip: X11 session gives the best window-title + tray support."
echo ""
"""
    (debiandir / "postinst").write_text(postinst, encoding="utf-8")
    os.chmod(debiandir / "postinst", 0o755)

    postrm = """#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
"""
    (debiandir / "postrm").write_text(postrm, encoding="utf-8")
    os.chmod(debiandir / "postrm", 0o755)

    DIST.mkdir(parents=True, exist_ok=True)
    deb_path = DIST / f"{PKG_NAME}_{VERSION}_{deb_arch}.deb"
    if deb_path.exists():
        deb_path.unlink()

    try:
        # gzip: Ubuntu 20.04 apt/dpkg installs reliably (zstd debs can fail on older dpkg)
        _run(
            [
                "dpkg-deb",
                "--build",
                "--root-owner-group",
                "-Zgzip",
                str(deb_root),
                str(deb_path),
            ]
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        _build_deb_manual(deb_root, deb_path)

    print(f"Debian package: {deb_path}")
    print(f"Install with: sudo apt install ./{deb_path.name}")
    _publish_release(deb_path)
    return deb_path


def _build_deb_manual(deb_root: Path, deb_path: Path) -> None:
    """Create a .deb from a package tree without dpkg-deb."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        debian = deb_root / "DEBIAN"
        control_tar = tmp_path / "control.tar.gz"
        _run(["tar", "-C", str(debian), "-czf", str(control_tar), "."])
        data_tar = tmp_path / "data.tar.gz"
        members = [p.name for p in deb_root.iterdir() if p.name != "DEBIAN"]
        _run(["tar", "-C", str(deb_root), "-czf", str(data_tar), *members])
        debian_binary = tmp_path / "debian-binary"
        debian_binary.write_text("2.0\n", encoding="utf-8")
        if deb_path.exists():
            deb_path.unlink()
        _run(
            [
                "ar",
                "r",
                str(deb_path),
                str(debian_binary),
                str(control_tar),
                str(data_tar),
            ]
        )


def _publish_release(path: Path) -> Path:
    """Copy artifact into dist/releases for the public /download page."""
    rel = DIST / "releases"
    rel.mkdir(parents=True, exist_ok=True)
    dest = rel / path.name
    shutil.copy2(path, dest)
    print(f"Published for download page: {dest}")
    return dest


def build_exe_client() -> Path:
    """Windows employee client only (tray + login + install helpers)."""
    if platform.system() != "Windows":
        print(
            "WARNING: building Windows client on non-Windows is not a usable .exe.\n"
            "Run packaging/build_windows.ps1 on a Windows machine.",
            file=sys.stderr,
        )
    out = build_binaries(onefile=True, apps=CLIENT_APPS)
    src = out / "timetrack-agent.exe"
    if not src.exists():
        src = out / "timetrack-agent"
    dst = out / "esstracker-Agent.exe"
    if src.exists():
        if dst.exists():
            dst.unlink()
        src.rename(dst)

    # Ship company defaults + installer next to the agent (Ubuntu /etc equivalent)
    defaults = WINDOWS / "defaults.toml"
    if not defaults.is_file():
        defaults = LINUX / "defaults.toml"
    if defaults.is_file():
        shutil.copy2(defaults, out / "defaults.toml")
    install_ps1 = WINDOWS / "install.ps1"
    if install_ps1.is_file():
        shutil.copy2(install_ps1, out / "install.ps1")
    uninstall_ps1 = WINDOWS / "uninstall.ps1"
    if uninstall_ps1.is_file():
        shutil.copy2(uninstall_ps1, out / "uninstall.ps1")
    readme = out / "README-WINDOWS.txt"
    readme.write_text(
        "esstracker (ESS) — Windows client\n"
        "=================================\n\n"
        "1. Right-click install.ps1 → Run with PowerShell\n"
        "   (or: powershell -ExecutionPolicy Bypass -File .\\install.ps1)\n"
        "2. Open Start → esstracker → Sign in\n"
        "3. The ESS icon appears in the system tray (notification area).\n"
        "4. Autostart at logon is enabled (like Ubuntu xdg-autostart).\n\n"
        f"Version: {VERSION}\n"
        "Server: https://tracker.euclideesolutions.com/\n",
        encoding="utf-8",
    )

    print(f"Windows client: {dst}")
    if dst.exists():
        _publish_release(dst)
        # Also zip the install kit for the download page
        kit = DIST / "releases" / f"esstracker-{VERSION}-windows.zip"
        if kit.exists():
            kit.unlink()
        shutil.make_archive(str(kit.with_suffix("")), "zip", out)
        print(f"Windows install kit: {kit}")
    return dst


def build_dmg(*, arch: str = "") -> Path:
    """macOS .app + .dmg (must run on Darwin)."""
    if platform.system() != "Darwin":
        sys.exit("build dmg: only supported on macOS")

    arch = arch or platform.machine()
    tag = {"arm64": "arm64", "x86_64": "x86_64"}.get(arch, arch)
    out = build_binaries(onefile=False, apps=CLIENT_APPS)
    # Prefer .app from --windowed; fall back to onedir folder
    app_dir = out / "timetrack-agent.app"
    if not app_dir.exists():
        # Rebuild agent windowed as .app
        work = BUILD / "pyinstaller-mac"
        if work.exists():
            shutil.rmtree(work)
        sep = _sep()
        launcher = work / "_launch_agent.py"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(
            "from timetrack.agent.__main__ import main\n"
            "if __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )
        icon_png = LINUX / "icons" / "esstracker-512.png"
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--windowed",
            "--name",
            "esstracker",
            "--osx-bundle-identifier",
            APP_ID,
            "--distpath",
            str(out),
            "--workpath",
            str(work),
            "--specpath",
            str(work),
            "--paths",
            str(ROOT),
            "--onedir",
        ]
        assets = ROOT / "timetrack" / "agent" / "assets"
        if assets.exists():
            cmd.extend(["--add-data", f"{assets}{sep}timetrack/agent/assets"])
        for mod in HIDDEN:
            cmd.extend(["--hidden-import", mod])
        cmd.append(str(launcher))
        _run(cmd)
        app_dir = out / "esstracker.app"

    if not app_dir.exists():
        sys.exit(f"missing macOS app bundle under {out}")

    # Stage DMG contents
    stage = BUILD / "dmg-stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copytree(app_dir, stage / app_dir.name)
    # Symlink Applications for drag-install (DeskTime pattern)
    try:
        os.symlink("/Applications", stage / "Applications")
    except OSError:
        pass

    rel = DIST / "releases"
    rel.mkdir(parents=True, exist_ok=True)
    dmg_path = rel / f"esstracker-{VERSION}-{tag}.dmg"
    if dmg_path.exists():
        dmg_path.unlink()
    _run(
        [
            "hdiutil",
            "create",
            "-volname",
            "esstracker",
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ]
    )
    print(f"macOS DMG: {dmg_path}")
    print(
        "Next (required for other Macs): codesign + notarize with Apple Developer ID."
    )
    return dmg_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        choices=("exe", "exe-client", "deb", "dmg", "binaries"),
        help="exe=all Windows bins, exe-client=employee .exe, deb=Linux, "
        "dmg=macOS (Darwin only), binaries=PyInstaller only",
    )
    parser.add_argument("--arch", default="", help="macOS arch tag for dmg name")
    args = parser.parse_args()
    if args.target == "exe":
        build_exe()
    elif args.target == "exe-client":
        build_exe_client()
    elif args.target == "deb":
        build_deb()
    elif args.target == "dmg":
        build_dmg(arch=args.arch)
    else:
        onefile = platform.system() == "Windows"
        build_binaries(onefile=onefile)


if __name__ == "__main__":
    main()
