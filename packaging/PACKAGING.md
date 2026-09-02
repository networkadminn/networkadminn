# Cross-platform client packaging (accuracy notes)

esstracker follows the same distribution model as DeskTime-class trackers:

| OS | Artifact | Build host | Notes |
|----|----------|------------|-------|
| Linux | `.deb` | Linux amd64 | Ready — Applications + tray + Sign in |
| Windows | `.exe` | Windows 10/11 | SmartScreen until Authenticode-signed |
| macOS | `.dmg` | macOS | Must codesign + notarize; drag to `/Applications` |

## Why we cannot “just build everything” on one Linux box

- **PyInstaller is not a cross-compiler.** A Linux host produces Linux ELF binaries only.
- **macOS Gatekeeper** rejects unsigned apps; notarization needs an Apple Developer ID and a Mac.
- **Windows SmartScreen** warns on unsigned PyInstaller EXEs until Authenticode signing.

## Tracking accuracy (real OS limits)

### Windows
- Foreground window + idle via Win32 are reliable on Win10/11.
- Signed installer preferred for enterprise (MSI later); `.exe` is fine for most users.

### macOS
- **Accessibility** permission required for window titles.
- **Screen Recording** required for screenshots.
- App must live in `/Applications` (DeskTime documents the same).
- Build **arm64** and **x86_64** separately when supporting both chips.

### Linux
- Best on **X11** with `xdotool` + `xprintidle`.
- **Wayland** often hides window titles from unprivileged clients.
- GNOME needs **AppIndicator** support for the tray.
- Deb Depends already pulls tray/notification libraries.

## Linux (Ubuntu 20.04 / 22.04 / 24.04)

| Goal | Command |
|------|---------|
| **Compatible .deb for 20 + 22 + 24** | `bash packaging/build_deb_compat.sh` (Docker; first run compiles Python 3.11 on Ubuntu 20.04) |
| Quick .deb on this machine | `bash packaging/build_deb.sh` (blocked on Ubuntu 24.04 host — use compat script) |
| 22.04 + 24.04 only (faster Docker) | `ESSTRACKER_BUILD_IMAGE=ubuntu:22.04 bash packaging/build_deb_compat.sh` |

Compatible packaging details:

- **Build on Ubuntu 20.04** (glibc 2.31). A `.deb` built on Ubuntu **24.04** may *install* but the frozen agent needs **GLIBC 2.38** and will not start on 20.04/22.04.
- **gzip** `.deb` (works with older `dpkg` on 20.04; zstd-only packages can fail)
- Depends use **OR** alternatives: `libgtk-3-0t64 \| libgtk-3-0`, Ayatana **or** classic AppIndicator
- Launcher prefers **X11** (`GDK_BACKEND=x11`) for window titles, idle, and tray
- Tray falls back to **xorg** when system PyGObject does not match the frozen Python

```bash
# Recommended: one package for all LTS clients
bash packaging/build_deb_compat.sh
# → dist/esstracker_*.deb and dist/releases/

# Windows (on a Windows PC)
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# → dist/windows/esstracker-Agent.exe + install.ps1
# → dist/releases/esstracker-*-windows.zip
# Install on an employee PC:
#   powershell -ExecutionPolicy Bypass -File dist\windows\install.ps1 -Launch

# macOS (on a Mac)
bash packaging/build_mac.sh
# then codesign + notarize before public download
```

### Windows client (parity with Ubuntu .deb)

| Ubuntu | Windows |
|--------|---------|
| Applications → esstracker | Start Menu → esstracker |
| `/etc/xdg/autostart/…` | Startup folder shortcut |
| `/etc/esstracker/defaults.toml` | `%LOCALAPPDATA%\Programs\esstracker\defaults.toml` |
| `~/.config/esstracker/agent.toml` | `%APPDATA%\esstracker\agent.toml` |
| `~/.local/share/esstracker/` | `%LOCALAPPDATA%\esstracker\` |
| Tray (AppIndicator / xorg) | Tray (win32 notification area) |

Upload artifacts to the server:

```bash
scp dist/releases/* root@SERVER:/www/wwwroot/timetrack/data/releases/
```

The public page is `/download`.
