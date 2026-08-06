"""Company timezone + office-hours helpers (default IST)."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Kolkata"
DEFAULT_WORK_DAYS = "0,1,2,3,4"  # Mon–Fri (Python weekday: Mon=0)

WEEKDAY_LABELS = (
    ("0", "Monday"),
    ("1", "Tuesday"),
    ("2", "Wednesday"),
    ("3", "Thursday"),
    ("4", "Friday"),
    ("5", "Saturday"),
    ("6", "Sunday"),
)


def zone(name: str | None = None) -> ZoneInfo:
    try:
        return ZoneInfo((name or DEFAULT_TZ).strip() or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def now_tz(tz_name: str | None = None) -> datetime:
    return datetime.now(zone(tz_name))


def today_tz(tz_name: str | None = None) -> date:
    return now_tz(tz_name).date()


def parse_work_days(raw: str | None) -> set[int]:
    text = (raw or DEFAULT_WORK_DAYS).strip()
    days: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if part.isdigit():
            n = int(part)
            if 0 <= n <= 6:
                days.add(n)
    return days or {0, 1, 2, 3, 4}


def work_days_csv(days: set[int] | list[int]) -> str:
    return ",".join(str(d) for d in sorted(days))


def is_work_day(day: datetime | date, work_days_raw: str | None) -> bool:
    d = day.date() if isinstance(day, datetime) else day
    return d.weekday() in parse_work_days(work_days_raw)


def hour_parts(hour_float: float) -> tuple[int, int]:
    h = int(hour_float)
    m = int(round((hour_float - h) * 60))
    if m >= 60:
        h += 1
        m = 0
    return max(0, min(h, 23)), max(0, min(m, 59))


def hour_to_time(hour_float: float) -> time:
    h, m = hour_parts(hour_float)
    return time(hour=h, minute=m)


def office_datetime(
    day: datetime | date,
    hour_float: float,
    tz_name: str | None = None,
) -> datetime:
    """Combine a calendar day with an office hour in the company timezone."""
    tz = zone(tz_name)
    d = day.date() if isinstance(day, datetime) else day
    return datetime.combine(d, hour_to_time(hour_float), tzinfo=tz)


def format_hour_label(hour_float: float) -> str:
    dt = datetime.combine(date(2000, 1, 1), hour_to_time(hour_float))
    return dt.strftime("%I:%M %p").lstrip("0")


def format_hour_short(hour_float: float) -> str:
    """Compact axis label: ``9 AM``, ``12 PM`` (no :00 clutter)."""
    h = int(math.floor(float(hour_float))) % 24
    m = int(round((float(hour_float) - math.floor(float(hour_float))) * 60)) % 60
    if m >= 55:
        h = (h + 1) % 24
        m = 0
    suffix = "AM" if h < 12 else "PM"
    hr = h % 12 or 12
    if m and m not in (0, 60):
        return f"{hr}:{m:02d} {suffix}"
    return f"{hr} {suffix}"


def day_bounds_tz(
    day: datetime | date | None = None,
    tz_name: str | None = None,
) -> tuple[float, float]:
    """Midnight→midnight timestamps for a calendar day in company TZ (IST default)."""
    tz = zone(tz_name)
    if day is None:
        local = now_tz(tz_name)
    elif isinstance(day, date) and not isinstance(day, datetime):
        local = datetime.combine(day, time.min, tzinfo=tz)
    else:
        if day.tzinfo is None:
            local = day.replace(tzinfo=tz)
        else:
            local = day.astimezone(tz)
    start = datetime.combine(local.date(), time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def format_clock_tz(ts: float | None, tz_name: str | None = None) -> str:
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(float(ts), tz=zone(tz_name))
    return dt.strftime("%I:%M %p").lstrip("0")


def format_iso_tz(ts: float, tz_name: str | None = None) -> str:
    return datetime.fromtimestamp(float(ts), tz=zone(tz_name)).isoformat(
        timespec="seconds"
    )


def format_datetime_local(ts: float | None, tz_name: str | None = None) -> str:
    """Value for HTML datetime-local inputs in company timezone."""
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz=zone(tz_name)).strftime("%Y-%m-%dT%H:%M")


__all__ = [
    "DEFAULT_TZ",
    "DEFAULT_WORK_DAYS",
    "WEEKDAY_LABELS",
    "zone",
    "now_tz",
    "today_tz",
    "parse_work_days",
    "work_days_csv",
    "is_work_day",
    "hour_parts",
    "hour_to_time",
    "office_datetime",
    "format_hour_label",
    "format_hour_short",
    "day_bounds_tz",
    "format_clock_tz",
    "format_iso_tz",
    "format_datetime_local",
]
