"""Login page copy and media helpers (hero taglines, time greeting)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from flask import current_app, url_for

from ..tzutil import now_tz
from .settings_util import company_tz_name

_LOGIN_TAGLINES: list[str] = [
    "We promise not to judge your forty-seven browser tabs.",
    "Sign in. The productivity bar is patiently waiting.",
    "Idle time is data. Coffee breaks are still idle.",
    "Your desk called. It wants a timestamp.",
    "Screenshots: proof you were in the right spreadsheet.",
    "Office hours respect IST. So should we all.",
    "Deep work starts after login. Usually.",
    "Another day, another chance to fill the gaps.",
    "The agent syncs. You shine.",
    "Time tracking without the awkward eye contact.",
    "Login now. Regret nothing except that meeting.",
    "We count seconds so you can count wins.",
    "Password correct? Clock starts. Metaphorically.",
    "Built for teams who actually build things.",
    "Your future self will thank this login.",
]

_SUBMIT_LABELS: list[str] = [
    "Sign in",
    "Let me in",
    "Start the clock",
    "I'm definitely working",
]


def _today_key() -> int:
    return date.today().toordinal()


def login_hero_tagline() -> str:
    if not _LOGIN_TAGLINES:
        return "Employee time tracking for your team."
    return _LOGIN_TAGLINES[_today_key() % len(_LOGIN_TAGLINES)]


def login_time_greeting() -> str:
    tz = company_tz_name()
    hour = now_tz(tz).hour
    if 5 <= hour < 12:
        return "Coffee first. Login second."
    if 12 <= hour < 17:
        return "Post-lunch comeback tour starts here."
    if 17 <= hour < 22:
        return "Still going? Respect."
    return "Night shift or last deploy? Either way, hi."


def login_submit_label() -> str:
    if not _SUBMIT_LABELS:
        return "Sign in"
    return _SUBMIT_LABELS[_today_key() % len(_SUBMIT_LABELS)]


def login_hero_video_url() -> str | None:
    """Return static URL if brand/login-hero.webm or .mp4 exists."""
    folder = current_app.static_folder
    if not folder:
        return None
    base = Path(folder) / "brand"
    for name in ("login-hero.webm", "login-hero.mp4"):
        if (base / name).is_file():
            return url_for("static", filename=f"brand/{name}")
    return None


def login_page_context() -> dict[str, str | None]:
    return {
        "login_tagline": login_hero_tagline(),
        "login_greeting": login_time_greeting(),
        "login_submit_label": login_submit_label(),
        "login_hero_video_url": login_hero_video_url(),
    }
