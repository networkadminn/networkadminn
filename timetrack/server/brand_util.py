"""Company logo storage for first-run setup / branding."""

from __future__ import annotations

import io
from pathlib import Path

from werkzeug.datastructures import FileStorage

_MAX_BYTES = 2 * 1024 * 1024
_ALLOWED_MIME = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/svg+xml", "image/x-icon"}
)
_EXT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "SVG": ".svg",
}


def brand_root(data_dir: str) -> Path:
    root = Path(data_dir) / "brand"
    root.mkdir(parents=True, exist_ok=True)
    return root


def clear_company_logo_files(data_dir: str) -> None:
    root = brand_root(data_dir)
    for path in root.glob("logo.*"):
        try:
            path.unlink()
        except OSError:
            pass


def save_company_logo(upload: FileStorage, data_dir: str) -> str:
    """Save logo under data/brand/logo.<ext>. Returns relative path for DB."""
    if not upload or not upload.filename:
        raise ValueError("No logo file selected.")
    raw = upload.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("Logo must be 2 MB or smaller.")
    if not raw:
        raise ValueError("Empty logo file.")

    mime = (upload.mimetype or "").split(";")[0].strip().lower()
    name = (upload.filename or "").lower()
    is_svg = mime == "image/svg+xml" or name.endswith(".svg")

    clear_company_logo_files(data_dir)
    root = brand_root(data_dir)

    if is_svg:
        # Basic sanity: must look like SVG text.
        text = raw.decode("utf-8", errors="ignore")
        if "<svg" not in text.lower():
            raise ValueError("Invalid SVG logo.")
        out = root / "logo.svg"
        out.write_bytes(raw)
        return "brand/logo.svg"

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ValueError("Could not read logo. Use PNG, JPG, WebP, or SVG.") from exc

    if mime and mime not in _ALLOWED_MIME:
        fmt = (img.format or "").upper()
        if fmt not in _EXT:
            raise ValueError("Use a PNG, JPG, WebP, or SVG logo.")

    fmt = (img.format or "PNG").upper()
    if fmt == "JPEG":
        out = root / "logo.jpg"
        img.convert("RGB").save(out, format="JPEG", quality=90, optimize=True)
        return "brand/logo.jpg"
    if fmt == "WEBP":
        out = root / "logo.webp"
        img.save(out, format="WEBP", quality=90)
        return "brand/logo.webp"

    out = root / "logo.png"
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    img.save(out, format="PNG", optimize=True)
    return "brand/logo.png"
