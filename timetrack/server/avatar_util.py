"""Profile photo storage (JPEG, resized square)."""

from __future__ import annotations

import io
import time
from pathlib import Path

from werkzeug.datastructures import FileStorage

from .models import User

_MAX_BYTES = 5 * 1024 * 1024
_ALLOWED = frozenset({"image/jpeg", "image/png", "image/webp"})
_AVATAR_SIZE = 256


def avatars_root(data_dir: str) -> Path:
    root = Path(data_dir) / "avatars"
    root.mkdir(parents=True, exist_ok=True)
    return root


def avatar_disk_path(data_dir: str, user_id: int) -> Path:
    return avatars_root(data_dir) / f"{user_id}.jpg"


def user_has_avatar(user: User) -> bool:
    return bool((getattr(user, "avatar_filename", None) or "").strip())


def remove_avatar_file(user: User, data_dir: str) -> None:
    path = avatar_disk_path(data_dir, user.id)
    if path.is_file():
        path.unlink()
    user.avatar_filename = ""
    user.avatar_updated_at = None


def save_avatar_upload(user: User, upload: FileStorage, data_dir: str) -> None:
    if not upload or not upload.filename:
        raise ValueError("No file selected.")
    raw = upload.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("Image must be 5 MB or smaller.")
    if not raw:
        raise ValueError("Empty file.")

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise ValueError("Could not read image. Use JPEG, PNG, or WebP.") from None

    mime = (upload.mimetype or "").split(";")[0].strip().lower()
    if mime not in _ALLOWED:
        fmt = (img.format or "").upper()
        if fmt not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("Use a JPEG, PNG, or WebP image.")

    img = img.convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((_AVATAR_SIZE, _AVATAR_SIZE), Image.Resampling.LANCZOS)

    out = avatar_disk_path(data_dir, user.id)
    img.save(out, format="JPEG", quality=85, optimize=True)
    user.avatar_filename = f"{user.id}.jpg"
    user.avatar_updated_at = time.time()
