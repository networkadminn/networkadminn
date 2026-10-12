"""Best-effort URL of the focused browser tab on Linux.

Linux has no portable way to read another app's address bar, so we match the
window's page title against the browser's own history database (Chrome,
Chromium, Brave, Edge, Firefox). The live DB is locked by the browser, so a copy
is refreshed at most every few seconds.
"""

from __future__ import annotations

import glob
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from ..monitor import strip_browser_suffix

_HOME = Path.home()
_CHROMIUM_ROOTS = {
    "chrome": [".config/google-chrome", ".config/google-chrome-beta"],
    "chromium": [".config/chromium", "snap/chromium/common/chromium"],
    "brave": [".config/BraveSoftware/Brave-Browser", "snap/brave/current/.config/BraveSoftware/Brave-Browser"],
    "edge": [".config/microsoft-edge"],
}
_FIREFOX_ROOTS = [".mozilla/firefox", "snap/firefox/common/.mozilla/firefox"]

_REFRESH_SECONDS = 5.0
_copies: dict[str, tuple[float, float, str]] = {}  # src -> (src_mtime, copied_at, copy path)
_tmpdir = tempfile.mkdtemp(prefix="esstracker-hist-")


def _family(app: str) -> str:
    a = (app or "").lower()
    if "firefox" in a:
        return "firefox"
    if "brave" in a:
        return "brave"
    if "edge" in a:
        return "edge"
    if "chromium" in a:
        return "chromium"
    if "chrome" in a:
        return "chrome"
    return ""


def _history_files(family: str) -> list[str]:
    files: list[str] = []
    if family == "firefox":
        for root in _FIREFOX_ROOTS:
            files += glob.glob(str(_HOME / root / "*" / "places.sqlite"))
    else:
        for root in _CHROMIUM_ROOTS.get(family, []):
            files += glob.glob(str(_HOME / root / "*" / "History"))
    # Most recently used profile first.
    return sorted(files, key=lambda p: os.path.getmtime(p), reverse=True)[:3]


def _fresh_copy(src: str) -> str | None:
    now = time.time()
    try:
        mtime = max(os.path.getmtime(p) for p in (src, src + "-wal") if os.path.exists(p))
    except (OSError, ValueError):
        return None
    cached = _copies.get(src)
    if cached and (cached[0] == mtime or now - cached[1] < _REFRESH_SECONDS):
        return cached[2]
    dest = os.path.join(_tmpdir, f"{abs(hash(src))}-{os.path.basename(src)}")
    try:
        shutil.copyfile(src, dest)
        for ext in ("-wal", "-journal"):
            if os.path.exists(src + ext):
                shutil.copyfile(src + ext, dest + ext)
            elif os.path.exists(dest + ext):
                os.remove(dest + ext)
    except OSError:
        return None
    _copies[src] = (mtime, now, dest)
    return dest


def _lookup(db: str, family: str, title: str) -> str:
    sql = (
        "SELECT url FROM moz_places WHERE title = ? ORDER BY last_visit_date DESC LIMIT 1"
        if family == "firefox"
        else "SELECT url FROM urls WHERE title = ? ORDER BY last_visit_time DESC LIMIT 1"
    )
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
        try:
            row = con.execute(sql, (title,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row else ""


def browser_url_from_history(app: str, window_title: str) -> str:
    family = _family(app)
    title = strip_browser_suffix(window_title)
    if not family or not title:
        return ""
    for src in _history_files(family):
        db = _fresh_copy(src)
        if db:
            url = _lookup(db, family, title)
            if url.startswith(("http://", "https://")):
                return url
    return ""


__all__ = ["browser_url_from_history"]
