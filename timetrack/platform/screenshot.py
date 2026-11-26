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
    """All active displays via Quartz, stitched in point coordinates.

    Needs Screen Recording permission on Catalina+. Each display is captured with
    ``CGDisplayCreateImage`` (Retina displays come back at pixel size) and scaled
    to its size in points so mixed-DPI setups line up.
    """
    try:
        from PIL import Image  # type: ignore
        from Quartz import (  # type: ignore
            CGDisplayBounds,
            CGDisplayCreateImage,
            CGGetActiveDisplayList,
            CGMainDisplayID,
        )
    except Exception:
        return None

    try:
        err, ids, count = CGGetActiveDisplayList(16, None, None)
        displays = list(ids[:count]) if err == 0 and count else [CGMainDisplayID()]
    except Exception:
        displays = [CGMainDisplayID()]

    parts = []
    for did in displays:
        cg = CGDisplayCreateImage(did)
        if cg is None:
            continue
        img = _cgimage_to_image(cg)
        if img is None:
            continue
        b = CGDisplayBounds(did)
        parts.append((img, int(b.origin.x), int(b.origin.y), int(b.size.width), int(b.size.height)))
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0][0]

    min_x = min(p[1] for p in parts)
    min_y = min(p[2] for p in parts)
    width = max(p[1] + p[3] for p in parts) - min_x
    height = max(p[2] + p[4] for p in parts) - min_y
    canvas = Image.new("RGB", (max(1, width), max(1, height)), (0, 0, 0))
    for img, x, y, w, h in parts:
        if img.size != (w, h) and w > 0 and h > 0:
            img = img.resize((w, h))
        canvas.paste(img, (x - min_x, y - min_y))
    return canvas


def _capture_pil_mss():
    import mss  # type: ignore
    from PIL import Image  # type: ignore

    with mss.mss() as sct:
        # monitors[0] is the union of all displays, so a second screen is not missed.
        raw = sct.grab(sct.monitors[0])
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def effective_max_width(width: int, height: int, max_width: int) -> int:
    """Allow wider output for side-by-side multi-monitor captures (up to 2x).

    One 16:9 screen keeps ``max_width``; a 32:9 two-screen desktop gets twice the
    width so each screen stays readable instead of being squeezed to half size.
    """
    if width <= 0 or height <= 0:
        return max_width
    screens_wide = max(1, round((width / height) / (16 / 9)))
    return max_width * min(2, screens_wide)


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
    limit = effective_max_width(width, height, max_width)
    if width > limit and width > 0:
        ratio = limit / float(width)
        img = img.resize((limit, max(1, int(height * ratio))))
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
