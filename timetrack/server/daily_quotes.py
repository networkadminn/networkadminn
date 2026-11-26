"""Rotating daily motivation quotes for the employee My Day dashboard."""

from __future__ import annotations

from datetime import datetime

from ..tzutil import now_tz

_QUOTES: list[dict[str, str]] = [
    {"text": "Small steps every day beat perfect plans you never start.", "author": ""},
    {"text": "Focus on progress, not perfection.", "author": "Unknown"},
    {"text": "Your future self will thank you for the work you do today.", "author": ""},
    {"text": "Discipline is choosing what you want most over what you want now.", "author": ""},
    {"text": "Done is better than perfect when the clock is ticking.", "author": ""},
    {"text": "Energy flows where attention goes—guard your deep work.", "author": ""},
    {"text": "One clear priority beats ten half-finished tasks.", "author": ""},
    {"text": "Rest is part of performance, not the opposite of it.", "author": ""},
    {"text": "Show up consistently; motivation follows action.", "author": ""},
    {"text": "What you track, you improve.", "author": ""},
    {"text": "Turn distractions into breaks, not defaults.", "author": ""},
    {"text": "Excellence is a habit built in ordinary hours.", "author": "Aristotle"},
    {"text": "Start where you are. Use what you have. Do what you can.", "author": "Arthur Ashe"},
    {"text": "The secret of getting ahead is getting started.", "author": "Mark Twain"},
    {"text": "You don’t have to be extreme—just consistent.", "author": ""},
    {"text": "Protect the first hour; it sets the tone for the day.", "author": ""},
    {"text": "Clarity comes from doing, not from waiting to feel ready.", "author": ""},
    {"text": "A good day at work is a series of small wins.", "author": ""},
    {"text": "Be kind to yourself, then get back to the task.", "author": ""},
    {"text": "Your team counts on your steady effort—not your burnout.", "author": ""},
    {"text": "Finish one thing before you celebrate the next.", "author": ""},
    {"text": "Deep work is a gift you give your future calendar.", "author": ""},
    {"text": "Today’s effort is tomorrow’s momentum.", "author": ""},
    {"text": "When in doubt, simplify the next step.", "author": ""},
    {"text": "Professionalism is reliability in small moments.", "author": ""},
    {"text": "You are building a reputation one hour at a time.", "author": ""},
    {"text": "Pause, breathe, then return with intention.", "author": ""},
    {"text": "Hard days still count if you show up honestly.", "author": ""},
    {"text": "Let your calendar reflect your real priorities.", "author": ""},
    {"text": "Gratitude plus grit gets a lot done.", "author": ""},
    {"text": "Make it easy to start; make it hard to quit mid-flow.", "author": ""},
    {"text": "The best time to log your work is while it’s fresh.", "author": ""},
]


def daily_motivation_quote(day: datetime, user_id: int) -> dict[str, str]:
    """Stable quote for a calendar day per user (changes daily, not on every refresh)."""
    if not _QUOTES:
        return {"text": "Make today count.", "author": ""}
    key = day.date().toordinal() + (user_id * 17)
    q = _QUOTES[key % len(_QUOTES)]
    return {"text": q["text"], "author": q.get("author") or ""}


def quote_card_greeting(
    display_name: str,
    *,
    tz_name: str,
    is_today: bool,
    day_label: str,
) -> str:
    """Line above the daily quote: time-of-day + name when viewing today."""
    name = (display_name or "").strip() or "there"
    if not is_today:
        label = (day_label or "").strip() or "this day"
        return f"For {label}, {name}"
    hour = now_tz(tz_name).hour
    if 5 <= hour < 12:
        part = "Good morning"
    elif 12 <= hour < 17:
        part = "Good afternoon"
    elif 17 <= hour < 22:
        part = "Good evening"
    else:
        part = "Hello"
    return f"{part}, {name}"
