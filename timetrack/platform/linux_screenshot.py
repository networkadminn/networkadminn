"""Linux desktop capture (X11 via mss; Wayland via gnome-screenshot / spectacle / grim)."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile


def _is_wayland() -> bool:
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _image_mostly_black(img, *, threshold: float = 14.0) -> bool:
    """True when the *whole* frame is near-black (failed XWayland/permission capture).

    Averages every pixel (computed in C from the histogram), so a dark top panel
    or a dark-themed editor at the top of the screen does not reject a real capture.
    """
    from PIL import ImageStat  # type: ignore

    if img.width <= 0 or img.height <= 0:
        return True
    mean = ImageStat.Stat(img.convert("RGB")).mean
    return (sum(mean) / 3.0) < threshold


def _capture_grim():
    from PIL import Image  # type: ignore

    grim = shutil.which("grim")
    if not grim:
        return None
    try:
        proc = subprocess.run(
            [grim, "-"],
            capture_output=True,
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or len(proc.stdout) < 500:
        return None
    try:
        return Image.open(io.BytesIO(proc.stdout)).convert("RGB")
    except Exception:
        return None


def _capture_gnome_screenshot():
    from PIL import Image  # type: ignore

    tool = shutil.which("gnome-screenshot")
    if not tool:
        return None
    fd, path = tempfile.mkstemp(prefix="esstracker-shot-", suffix=".png")
    os.close(fd)
    try:
        # CANBERRA_DRIVER=null suppresses the camera-shutter click (no --silent flag).
        env = os.environ.copy()
        env["CANBERRA_DRIVER"] = "null"
        proc = subprocess.run(
            [tool, "-f", path],
            capture_output=True,
            timeout=15,
            check=False,
            env=env,
        )
        if proc.returncode != 0 or not os.path.isfile(path):
            return None
        return Image.open(path).convert("RGB")
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _capture_import_root():
    """ImageMagick ``import -window root`` (X11)."""
    from PIL import Image  # type: ignore

    tool = shutil.which("import")
    if not tool or not os.environ.get("DISPLAY"):
        return None
    fd, path = tempfile.mkstemp(prefix="esstracker-shot-", suffix=".png")
    os.close(fd)
    try:
        proc = subprocess.run(
            [tool, "-window", "root", path],
            capture_output=True,
            timeout=12,
            check=False,
        )
        if proc.returncode != 0 or not os.path.isfile(path):
            return None
        return Image.open(path).convert("RGB")
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _capture_mss():
    import mss  # type: ignore
    from PIL import Image  # type: ignore

    with mss.mss() as sct:
        # monitors[0] is the union of all displays, so a second screen is not missed.
        raw = sct.grab(sct.monitors[0])
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _capture_spectacle():
    """KDE Plasma (Wayland or X11): ``spectacle`` in background mode, full screen."""
    from PIL import Image  # type: ignore

    tool = shutil.which("spectacle")
    if not tool:
        return None
    fd, path = tempfile.mkstemp(prefix="esstracker-shot-", suffix=".png")
    os.close(fd)
    try:
        proc = subprocess.run(
            [tool, "--background", "--nonotify", "--fullscreen", "--output", path],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0 or not os.path.getsize(path):
            return None
        return Image.open(path).convert("RGB")
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _desktop() -> str:
    return (
        os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("XDG_SESSION_DESKTOP") or ""
    ).lower()


def capture_linux_pil():
    """Return a PIL RGB image of the desktop (all monitors where the tool allows), or ``None``."""
    if _is_wayland():
        desktop = _desktop()
        if "kde" in desktop or "plasma" in desktop:
            first = (_capture_spectacle,)
        else:
            # Prefer grim (silent). gnome-screenshot plays a shutter sound and is fallback only.
            first = (_capture_grim,)
        rest = tuple(
            f
            for f in (_capture_grim, _capture_gnome_screenshot, _capture_spectacle, _capture_mss, _capture_import_root)
            if f not in first
        )
        order = first + rest
    else:
        order = (_capture_mss, _capture_import_root, _capture_grim, _capture_gnome_screenshot, _capture_spectacle)
    for fn in order:
        try:
            img = fn()
        except Exception:
            img = None
        if img is None:
            continue
        if _image_mostly_black(img):
            continue
        return img
    return None
