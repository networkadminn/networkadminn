"""Cross-platform screenshot capture (Windows / macOS / Linux via ``mss``).

Produces a downscaled JPEG to keep uploads small. Supports optional blur
(DeskTime-style privacy). Returns ``None`` when no display is available.

macOS: without **Screen Recording** permission, captures are wallpaper-only.
Call ``ensure_macos_screen_recording_access()`` at agent start, then restart
the app after granting in System Settings.
"""

from __future__ import annotations

import io
import subprocess
import sys

_macos_access_prompted = False


def ensure_macos_screen_recording_access() -> bool:
    """Prompt for Screen Recording (macOS 10.15+). User must restart app after grant."""
    global _macos_access_prompted
    if sys.platform != "darwin":
        return True
    try:
        from Quartz import (  # type: ignore
            CGPreflightScreenCaptureAccess,
            CGRequestScreenCaptureAccess,
        )
    except Exception:
        return True

    if CGPreflightScreenCaptureAccess():
        return True

    if not _macos_access_prompted:
        _macos_access_prompted = True
        CGRequestScreenCaptureAccess()
        try:
            msg = (
                "Open System Settings → Privacy & Security → Screen Recording, "
                "enable esstracker, then quit and reopen the app. "
                "Without this, screenshots show only the wallpaper."
            ).replace('"', "'")
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display alert "esstracker needs Screen Recording" '
                    f'message "{msg}" as warning',
                ],
                check=False,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    return bool(CGPreflightScreenCaptureAccess())


def _cgimage_to_image(cg_image) -> "object | None":
    from PIL import Image  # type: ignore
    from Quartz import (  # type: ignore
        CGDataProviderCopyData,
        CGImageGetBitsPerPixel,
        CGImageGetBytesPerRow,
        CGImageGetDataProvider,
        CGImageGetHeight,
        CGImageGetWidth,
    )

    width = int(CGImageGetWidth(cg_image))
    height = int(CGImageGetHeight(cg_image))
    if width <= 0 or height <= 0:
        return None
    bpp = int(CGImageGetBitsPerPixel(cg_image))
    bpr = int(CGImageGetBytesPerRow(cg_image))
    data = CGDataProviderCopyData(CGImageGetDataProvider(cg_image))
    buf = bytes(data)
    if bpp == 32:
        img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", bpr, 1)
        return img.convert("RGB")
    if bpp == 24:
        return Image.frombuffer("RGB", (width, height), buf, "raw", "BGR", bpr, 1)
    return None


def _capture_pil_darwin():
    """Primary display via Quartz (needs Screen Recording permission on Catalina+)."""
    try:
        from Quartz import CGDisplayCreateImage, CGMainDisplayID  # type: ignore
    except Exception:
        return None
    cg = CGDisplayCreateImage(CGMainDisplayID())
    if cg is None:
        return None
    return _cgimage_to_image(cg)


def _capture_pil_mss():
    import mss  # type: ignore
    from PIL import Image  # type: ignore

    with mss.mss() as sct:
        # monitors[0] = union of all displays; [1] = primary (often better on macOS).
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        raw = sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _capture_pil():
    if sys.platform == "darwin":
        img = _capture_pil_darwin()
        if img is not None:
            return img
    if sys.platform.startswith("linux"):
        try:
            from .linux_screenshot import capture_linux_pil

            img = capture_linux_pil()
            if img is not None:
                return img
        except Exception:
            pass
    try:
        return _capture_pil_mss()
    except Exception:
        return None


def _finish_jpeg(
    img,
    *,
    max_width: int,
    quality: int,
    blur: bool,
    blur_radius: int,
) -> tuple[bytes, int, int] | None:
    from PIL import ImageFilter  # type: ignore

    if img is None:
        return None
    width, height = img.size
    if width > max_width and width > 0:
        ratio = max_width / float(width)
        img = img.resize((max_width, max(1, int(height * ratio))))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(radius=max(4, blur_radius)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    final_w, final_h = img.size
    return buf.getvalue(), final_w, final_h


def capture_jpeg(
    max_width: int = 1920,
    quality: int = 82,
    blur: bool = False,
    blur_radius: int = 12,
) -> tuple[bytes, int, int] | None:
    """Capture the virtual desktop and return ``(jpeg_bytes, width, height)``."""
    try:
        from PIL import Image  # type: ignore  # noqa: F401
    except Exception:
        return None
    try:
        img = _capture_pil()
    except Exception:
        return None
    return _finish_jpeg(
        img,
        max_width=max_width,
        quality=quality,
        blur=blur,
        blur_radius=blur_radius,
    )


def make_thumbnail(jpeg_bytes: bytes, max_width: int = 320, quality: int = 55) -> bytes | None:
    """Return a smaller JPEG thumbnail of ``jpeg_bytes`` (or ``None``)."""
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    try:
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    except Exception:
        return None
    w, h = img.size
    if w > max_width and w > 0:
        ratio = max_width / float(w)
        img = img.resize((max_width, max(1, int(h * ratio))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


__all__ = ["capture_jpeg", "ensure_macos_screen_recording_access", "make_thumbnail"]
