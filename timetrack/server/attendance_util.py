"""Shared attendance / absence calendar logic."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta

from ..analytics import day_bounds, format_clock, humanize, summarize


def attendance_cell(user_id: int, day: date, *, tz_name: str) -> dict:
    """Classify one calendar day and attach arrival / left / desk / productive / idle."""
    from .views import _activities_for, _is_late, _is_off_day, _manual_for

    d = datetime.combine(day, datetime.min.time())
    acts = _activities_for(user_id, d)
    start, end = day_bounds(d, tz_name=tz_name)
    s = summarize(acts, start, end)
    manual = _manual_for(user_id, d)
    manual_seconds = sum(m.duration for m in manual)
    desktime = s.desktime_seconds(manual_seconds)
    idle = float(s.idle_seconds or 0.0)
    # Significant idle = gap worth showing on the calendar (≥ 10 minutes)
    has_gap = idle >= 600.0 and s.total_seconds > 0

    if _is_off_day(d):
        status = "off"
    elif s.total_seconds <= 0 and manual_seconds <= 0:
        status = "absent"
    elif has_gap and idle >= max(s.productive_seconds, 1.0):
        # Mostly idle day — still mark attendance but surface as idle/gap
        status = "idle"
    elif _is_late(s.arrival_ts, d):
        status = "late"
    else:
        status = "present"

    return {
        "day": day.strftime("%Y-%m-%d"),
        "status": status,
        "arrived": format_clock(s.arrival_ts, tz_name=tz_name),
        "left": format_clock(s.last_seen_ts, tz_name=tz_name),
        "active": s.active_seconds,
        "desktime": desktime,
        "desktime_label": humanize(desktime) if desktime > 0 else "—",
        "productive": s.productive_seconds,
        "productive_label": humanize(s.productive_seconds) if s.productive_seconds > 0 else "—",
        "idle": idle,
        "idle_label": humanize(idle) if idle > 0 else "—",
        "has_gap": has_gap,
    }


def week_matrix(users: list, week_days: list[date], *, tz_name: str) -> tuple[list, dict]:
    totals = {"present": 0, "late": 0, "absent": 0, "off": 0, "idle": 0}
    matrix = []
    for u in users:
        cells = []
        for d in week_days:
            c = attendance_cell(u.id, d, tz_name=tz_name)
            totals[c["status"]] = totals.get(c["status"], 0) + 1
            cells.append(c)
        matrix.append({"user": u, "cells": cells})
    return matrix, totals


def month_weeks(year: int, month: int) -> list[list[date | None]]:
    """Calendar weeks (Mon–Sun) for a month; leading/trailing days padded with None."""
    first = date(year, month, 1)
    start = first - timedelta(days=first.weekday())
    _, last_day = monthrange(year, month)
    last = date(year, month, last_day)
    end = last + timedelta(days=(6 - last.weekday()))
    weeks: list[list[date | None]] = []
    cur = start
    while cur <= end:
        row: list[date | None] = []
        for _ in range(7):
            if cur.month == month:
                row.append(cur)
            else:
                row.append(None)
            cur += timedelta(days=1)
        weeks.append(row)
    return weeks


def month_summary(user_id: int, year: int, month: int, *, tz_name: str) -> dict:
    _, days_in_month = monthrange(year, month)
    counts = {"present": 0, "late": 0, "absent": 0, "off": 0, "idle": 0}
    for day_num in range(1, days_in_month + 1):
        c = attendance_cell(user_id, date(year, month, day_num), tz_name=tz_name)
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return counts
