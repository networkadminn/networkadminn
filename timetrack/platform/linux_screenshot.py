"""Linux desktop capture (X11 via mss, Wayland via grim / gnome-screenshot)."""

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


def _image_mostly_black(img, *, sample: int = 4000, threshold: float = 14.0) -> bool:
    px = list(img.getdata())
    if not px:
        return True
    n = min(sample, len(px))
    avg = sum(sum(p) for p in px[:n]) / (n * 3)
    return avg < threshold


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
        proc = subprocess.run(
            [tool, "-f", path],
            capture_output=True,
            timeout=15,
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
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        raw = sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def capture_linux_pil():
    """Return a PIL RGB image of the primary display, or ``None``."""
    if _is_wayland():
        order = (_capture_grim, _capture_gnome_screenshot, _capture_mss, _capture_import_root)
    else:
        order = (_capture_mss, _capture_import_root, _capture_grim, _capture_gnome_screenshot)

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
