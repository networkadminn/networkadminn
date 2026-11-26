"""Best-effort URL of the focused browser tab on Linux.

Linux has no portable way to read another app's address bar, so we match the
window's page title against the browser's own history database (Chrome,
Chromium, Brave, Edge, Firefox; deb, snap and Flatpak installs). The live DB is
locked by the browser, so we read a private copy.

Copying is kept cheap: a title already resolved is answered from memory, and the
history file is only re-copied when it changed, at most every
``_REFRESH_SECONDS``, and never when it is unreasonably large. The copies live in
one temp folder that is removed when the agent exits.
"""

from __future__ import annotations

import atexit
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
    "chrome": [
        ".config/google-chrome",
        ".config/google-chrome-beta",
        ".var/app/com.google.Chrome/config/google-chrome",
    ],
    "chromium": [
        ".config/chromium",
        "snap/chromium/common/chromium",
        ".var/app/org.chromium.Chromium/config/chromium",
    ],
    "brave": [
        ".config/BraveSoftware/Brave-Browser",
        "snap/brave/current/.config/BraveSoftware/Brave-Browser",
        ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
    ],
    "edge": [
        ".config/microsoft-edge",
        ".var/app/com.microsoft.Edge/config/microsoft-edge",
    ],
}
_FIREFOX_ROOTS = [
    ".mozilla/firefox",
    "snap/firefox/common/.mozilla/firefox",
    ".var/app/org.mozilla.firefox/.mozilla/firefox",
]

_REFRESH_SECONDS = 15.0
_MEMO_SECONDS = 600.0
_MAX_HISTORY_BYTES = 256 * 1024 * 1024
_GENERIC_TITLES = {"new tab", "new private tab", "untitled", "loading…", "loading..."}

_copies: dict[str, tuple[float, float, str]] = {}  # src -> (src_mtime, copied_at, copy path)
_memo: dict[tuple[str, str], tuple[str, float]] = {}  # (family, title) -> (url, resolved_at)
_tmpdir: str | None = None


def _workdir() -> str:
    global _tmpdir
    if _tmpdir is None or not os.path.isdir(_tmpdir):
        _tmpdir = tempfile.mkdtemp(prefix="esstracker-hist-")
        atexit.register(shutil.rmtree, _tmpdir, True)
    return _tmpdir


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

    def _mtime(p: str) -> float:
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0.0

    # Most recently used profile first.
    return sorted(files, key=_mtime, reverse=True)[:3]


def _fresh_copy(src: str) -> str | None:
    now = time.time()
    try:
        mtime = max(os.path.getmtime(p) for p in (src, src + "-wal") if os.path.exists(p))
        if os.path.getsize(src) > _MAX_HISTORY_BYTES:
            return None
    except (OSError, ValueError):
        return None
    cached = _copies.get(src)
    if cached and (cached[0] == mtime or now - cached[1] < _REFRESH_SECONDS):
        return cached[2]
    dest = os.path.join(_workdir(), f"{abs(hash(src))}-{os.path.basename(src)}")
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
    if not family or not title or title.strip().lower() in _GENERIC_TITLES:
        return ""
    now = time.time()
    key = (family, title)
    hit = _memo.get(key)
    if hit and now - hit[1] < _MEMO_SECONDS:
        return hit[0]
    if len(_memo) > 512:
        _memo.clear()
    for src in _history_files(family):
        db = _fresh_copy(src)
        if db:
            url = _lookup(db, family, title)
            if url.startswith(("http://", "https://")):
                _memo[key] = (url, now)
                return url
    return ""


__all__ = ["browser_url_from_history"]
