"""Helpers to infer website/URL detail from window titles and app names."""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Common browser process names (case-insensitive substring match on app).
_BROWSER_APPS = (
    "chrome",
    "chromium",
    "firefox",
    "msedge",
    "edge",
    "brave",
    "opera",
    "vivaldi",
    "safari",
    "arc",
    "zen",
)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_DOMAIN_RE = re.compile(
    r"(?:^|[\s\|\-–—•·])"
    r"((?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)"
    r"(?:$|[\s\|\-–—•·/:])",
    re.IGNORECASE,
)


def is_browser(app: str) -> bool:
    low = (app or "").lower()
    return any(b in low for b in _BROWSER_APPS)


def extract_url(app: str, title: str) -> str:
    """Best-effort URL/domain from a window title (browsers especially).

    Real browser URL APIs are OS-restricted; we parse titles like
    ``Page Title - example.com`` or embedded ``https://...``.
    """
    title = (title or "").strip()
    if not title:
        return ""

    m = _URL_RE.search(title)
    if m:
        return m.group(0).rstrip(".,);]")

    # Prefer domain extraction for browsers; still try for Electron apps.
    if is_browser(app) or "." in title:
        # Take last segment after common separators (Chrome: "Title - Site")
        for sep in (" - ", " — ", " | ", " · ", " • "):
            if sep in title:
                candidate = title.rsplit(sep, 1)[-1].strip()
                if _looks_like_host(candidate):
                    return candidate.lower()
        dm = _DOMAIN_RE.search(f" {title} ")
        if dm:
            return dm.group(1).lower()
    return ""


def extract_domain(url_or_host: str) -> str:
    raw = (url_or_host or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        try:
            host = urlparse(raw).hostname or ""
            return host.lower().lstrip("www.")
        except Exception:
            return ""
    return raw.lower().lstrip("www.")


def _looks_like_host(text: str) -> bool:
    t = text.strip().lower()
    if " " in t or len(t) < 3 or len(t) > 120:
        return False
    if t.count(".") < 1:
        return False
    return bool(re.fullmatch(r"[a-z0-9.-]+", t))


__all__ = ["extract_url", "extract_domain", "is_browser"]
