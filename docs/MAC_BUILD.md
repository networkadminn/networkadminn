# Build esstracker on macOS (employee client .dmg)

## Who needs which DMG

| Mac type | DMG file | How to get it |
|----------|----------|----------------|
| **Apple Silicon** (M1/M2/M3) | `esstracker-*-arm64.dmg` | GitHub Actions → workflow **macOS dmg** (tag `v*`), or build on an ARM Mac |
| **Intel** | `esstracker-*-x86_64.dmg` | **Build on an Intel Mac** (see below). Hosted GitHub runners do **not** produce Intel DMGs. |

Installing an **arm64** app on an **Intel** Mac shows: *"not supported on this Mac"*.

## Prerequisites (especially Intel Macs)

1. **Xcode Command Line Tools** — System Settings → Software Update, or:
   ```bash
   sudo xcode-select --install
   ```
2. **Python 3.12** — do not rely on Homebrew **3.14** for packaging without a venv.
   - **Recommended:** [python.org macOS installer](https://www.python.org/downloads/macos/) (3.12.x)
   - **Optional:** `brew install python@3.12` only after Command Line Tools are current

## Build steps (Intel → `x86_64`)

```bash
cd networkadminn
git checkout release/0.3.0
git pull

# Pick ONE Python 3.12 path:

# A) python.org
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12

# B) Homebrew (if installed)
# PY="$(brew --prefix python@3.12)/bin/python3.12"

rm -rf .venv
"$PY" -m venv .venv
source .venv/bin/activate
which python      # must be .../networkadminn/.venv/bin/python
python --version    # must be 3.12.x

python -m pip install -U pip wheel
python -m pip install -r requirements.txt "pyinstaller>=6.0" Pillow pyobjc-framework-Quartz
python -c "import PyInstaller; print('OK')"

python packaging/build.py dmg --arch x86_64
```

**Output:** `dist/releases/esstracker-<version>-x86_64.dmg`

Prefer **`python packaging/build.py dmg`** from an activated venv. `packaging/build_mac.sh` uses system `python3` and often fails on Homebrew (PEP 668) unless you fix PATH or use the venv workflow above.

## Build steps (Apple Silicon → `arm64`)

On an M-series Mac, from repo root with venv (same pip installs as above):

```bash
bash packaging/build_mac.sh
# or: python packaging/build.py dmg --arch arm64
```

Or use CI: push tag `vX.Y.Z` and download artifact from **macOS dmg**.

## Install on employee Macs

1. Open the **correct** DMG (Intel vs Apple Silicon).
2. Drag **esstracker** to **Applications** (required).
3. First launch: right-click → **Open** if Gatekeeper blocks an unsigned build.
4. **System Settings → Privacy & Security:** grant **Accessibility** and **Screen Recording** to esstracker (needed for window titles and screenshots).

## Upload to production download server

```bash
scp dist/releases/esstracker-*-x86_64.dmg root@31.97.239.250:/www/wwwroot/timetrack/data/releases/
# arm64:
scp dist/releases/esstracker-*-arm64.dmg root@31.97.239.250:/www/wwwroot/timetrack/data/releases/
```

Public page: https://tracker.euclideesolutions.com/download

## CI (GitHub Actions)

| Workflow | Runner | Artifact |
|----------|--------|----------|
| **Linux deb** | `ubuntu-22.04` / `24.04` | `.deb` |
| **Windows installer** | `windows-latest` | `esstracker-Setup-*.exe` |
| **macOS dmg** | `macos-14` (ARM) | `*-arm64.dmg` only |

Release a version:

```bash
# bump timetrack/__init__.py __version__, then:
git tag vX.Y.Z && git push origin release/0.3.0 && git push origin vX.Y.Z
```

On the server (with `gh` authenticated):

```bash
./scripts/deploy-server.sh --tag vX.Y.Z
```

### Intel builds in CI (optional)

GitHub-hosted runners are Apple Silicon only. For **Intel DMG in Actions**, add a **self-hosted runner** on an Intel Mac (Settings → Actions → Runners) and a job with `runs-on: [self-hosted, macOS, X64]`.

## Common errors

| Error | Fix |
|-------|-----|
| *not supported on this Mac* | Wrong architecture — use **x86_64** on Intel, **arm64** on Apple Silicon |
| PEP 668 / `externally-managed-environment` | Use **venv** + Python **3.12**, not bare Homebrew 3.14 |
| `python@3.12` missing (brew) | Update Command Line Tools or use python.org installer |
| `No module named 'PyInstaller'` | Install deps inside **activated** `.venv` before `build.py dmg` |
| `Command Line Tools are too outdated` (brew) | Run `sudo xcode-select --install` or Software Update |

## Related files

- `packaging/build_mac.sh` — macOS DMG wrapper (Darwin only)
- `packaging/build.py dmg` — PyInstaller + `hdiutil`
- `packaging/PACKAGING.md` — all platforms
- `.github/workflows/macos-dmg.yml` — arm64 CI
