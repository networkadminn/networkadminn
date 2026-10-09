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

# Browsers append their own name (and sometimes a profile) to every tab title.
_BROWSER_SUFFIX_RE = re.compile(
    r"(?:\s+[-–—]\s+(?:personal|work|profile\s*\d+))?"
    r"\s+[-–—]\s+(?:google chrome|chromium|mozilla firefox|firefox|brave|"
    r"microsoft[\s\u200b]*edge|opera|vivaldi|safari|arc|zen browser)\s*$",
    re.IGNORECASE,
)
_TITLE_SEPARATORS = (" - ", " – ", " — ", " | ", " · ", " • ")

# Tab-title name -> site, for pages whose titles carry no domain.
_KNOWN_SITES = {
    "youtube": "youtube.com",
    "youtube music": "music.youtube.com",
    "gmail": "mail.google.com",
    "google search": "google.com",
    "google": "google.com",
    "google sheets": "docs.google.com",
    "google docs": "docs.google.com",
    "google slides": "docs.google.com",
    "google forms": "docs.google.com",
    "google drive": "drive.google.com",
    "google calendar": "calendar.google.com",
    "google meet": "meet.google.com",
    "google ads": "ads.google.com",
    "google analytics": "analytics.google.com",
    "google cloud": "cloud.google.com",
    "google accounts": "accounts.google.com",
    "google maps": "google.com/maps",
    "google translate": "translate.google.com",
    "search console": "search.google.com",
    "gemini": "gemini.google.com",
    "chatgpt": "chatgpt.com",
    "claude": "claude.ai",
    "deepseek": "chat.deepseek.com",
    "perplexity": "perplexity.ai",
    "figma": "figma.com",
    "canva": "canva.com",
    "github": "github.com",
    "gitlab": "gitlab.com",
    "bitbucket": "bitbucket.org",
    "stack overflow": "stackoverflow.com",
    "whatsapp": "web.whatsapp.com",
    "whatsapp business": "web.whatsapp.com",
    "facebook": "facebook.com",
    "messenger": "messenger.com",
    "instagram": "instagram.com",
    "linkedin": "linkedin.com",
    "twitter": "twitter.com",
    "reddit": "reddit.com",
    "pinterest": "pinterest.com",
    "telegram web": "web.telegram.org",
    "upwork": "upwork.com",
    "fiverr": "fiverr.com",
    "envato": "envato.com",
    "elevenlabs": "elevenlabs.io",
    "trello": "trello.com",
    "jira": "atlassian.net",
    "confluence": "atlassian.net",
    "notion": "notion.so",
    "slack": "app.slack.com",
    "microsoft teams": "teams.microsoft.com",
    "outlook": "outlook.office.com",
    "zoom": "zoom.us",
    "netflix": "netflix.com",
    "prime video": "primevideo.com",
    "jiohotstar": "hotstar.com",
    "hotstar": "hotstar.com",
    "spotify": "open.spotify.com",
    "cricbuzz": "cricbuzz.com",
    "wikipedia": "wikipedia.org",
    "amazon web services sign-in": "aws.amazon.com",
    "aws management console": "aws.amazon.com",
}
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

    browser = is_browser(app)
    if browser:
        title = strip_browser_suffix(title)

    # Prefer domain extraction for browsers; still try for Electron apps.
    if browser or "." in title:
        # Take last segment after common separators (Chrome: "Title - Site")
        for sep in _TITLE_SEPARATORS:
            if sep in title:
                candidate = title.rsplit(sep, 1)[-1].strip()
                if _looks_like_host(candidate):
                    return candidate.lower()
        dm = _DOMAIN_RE.search(f" {title} ")
        if dm:
            return dm.group(1).lower()

    if browser:
        known = _known_site(title)
        if known:
            return known
    return ""


def strip_browser_suffix(title: str) -> str:
    """``"Inbox - Gmail - Google Chrome"`` -> ``"Inbox - Gmail"``."""
    return _BROWSER_SUFFIX_RE.sub("", (title or "").strip()).strip()


def _known_site(title: str) -> str:
    segments = [title]
    for sep in _TITLE_SEPARATORS:
        segments = [part for seg in segments for part in seg.split(sep)]
    # Site names usually trail the page title, so check from the end.
    for seg in reversed(segments):
        key = re.sub(r"^\(\d+\)\s*", "", seg).strip().lower()
        if key in _KNOWN_SITES:
            return _KNOWN_SITES[key]
    return ""


def extract_domain(url_or_host: str) -> str:
    raw = (url_or_host or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        try:
            host = urlparse(raw).hostname or ""
            return host.lower().removeprefix("www.")
        except Exception:
            return ""
    host = raw.lower().split("/", 1)[0].split("?", 1)[0]
    return host.removeprefix("www.")


def _looks_like_host(text: str) -> bool:
    t = text.strip().lower()
    if " " in t or len(t) < 3 or len(t) > 120:
        return False
    if t.count(".") < 1:
        return False
    return bool(re.fullmatch(r"[a-z0-9.-]+", t))


def clean_url(raw: str) -> str:
    """Normalise an address-bar value; drop query/fragment (search terms, tokens)."""
    raw = (raw or "").strip()
    if not raw or " " in raw or len(raw) > 2048:
        return ""
    low = raw.lower()
    if low.startswith(("chrome://", "edge://", "brave://", "about:", "file:", "view-source:")):
        return raw.split("?", 1)[0].split("#", 1)[0][:300]
    if "://" not in raw:
        host = low.split("/", 1)[0]
        if not _looks_like_host(host.split(":", 1)[0]) and not host.startswith("localhost"):
            return ""
        raw = "https://" + raw
    try:
        p = urlparse(raw)
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    netloc = p.hostname.lower() + (f":{p.port}" if p.port else "")
    return f"{p.scheme}://{netloc}{p.path or '/'}"[:300]


__all__ = ["extract_url", "extract_domain", "is_browser", "strip_browser_suffix", "clean_url"]
