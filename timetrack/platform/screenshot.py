"""Cross-platform screenshot capture (Windows / macOS / Linux via ``mss``).

Produces a downscaled JPEG to keep uploads small. Returns ``None`` when no
display is available (e.g. headless servers/CI) so callers can skip silently.
"""

from __future__ import annotations

import io


def capture_jpeg(max_width: int = 1280, quality: int = 60) -> tuple[bytes, int, int] | None:
    """Capture the virtual desktop and return ``(jpeg_bytes, width, height)``.

    ``None`` is returned when capture is not possible.
    """
    try:
        import mss  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        return None

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[0]  # [0] is the full virtual screen
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except Exception:
        return None

    width, height = img.size
    if width > max_width and width > 0:
        ratio = max_width / float(width)
        img = img.resize((max_width, max(1, int(height * ratio))))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    final_w, final_h = img.size
    return buf.getvalue(), final_w, final_h


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


__all__ = ["capture_jpeg", "make_thumbnail"]
