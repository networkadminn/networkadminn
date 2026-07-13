"""Aggregation and productivity analytics over recorded activities."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from .config import NEUTRAL, PRODUCTIVE, UNPRODUCTIVE
from .storage import Activity, Storage


@dataclass
class AppSummary:
    app: str
    category: str
    seconds: float = 0.0


@dataclass
class Summary:
    """Aggregated stats for a time window."""

    total_seconds: float = 0.0
    active_seconds: float = 0.0
    idle_seconds: float = 0.0
    productive_seconds: float = 0.0
    unproductive_seconds: float = 0.0
    neutral_seconds: float = 0.0
    apps: list[AppSummary] = field(default_factory=list)
    arrival_ts: float | None = None
    last_seen_ts: float | None = None

    @property
    def productivity_pct(self) -> float:
        """Productive share of *categorized* (productive+unproductive) time."""
        base = self.productive_seconds + self.unproductive_seconds
        if base <= 0:
            return 0.0
        return round(100.0 * self.productive_seconds / base, 1)

    @property
    def effectiveness_pct(self) -> float:
        """Active (non-idle) share of total tracked time."""
        if self.total_seconds <= 0:
            return 0.0
        return round(100.0 * self.active_seconds / self.total_seconds, 1)

    @property
    def span_seconds(self) -> float:
        """Wall-clock time between first arrival and last activity."""
        if self.arrival_ts is None or self.last_seen_ts is None:
            return 0.0
        return max(0.0, self.last_seen_ts - self.arrival_ts)


def summarize(activities: list[Activity]) -> Summary:
    s = Summary()
    per_app: dict[str, AppSummary] = {}

    for a in activities:
        s.total_seconds += a.duration
        if a.idle:
            s.idle_seconds += a.duration
            continue

        s.active_seconds += a.duration
        if a.category == PRODUCTIVE:
            s.productive_seconds += a.duration
        elif a.category == UNPRODUCTIVE:
            s.unproductive_seconds += a.duration
        else:
            s.neutral_seconds += a.duration

        summ = per_app.get(a.app)
        if summ is None:
            summ = AppSummary(app=a.app, category=a.category)
            per_app[a.app] = summ
        summ.seconds += a.duration
        # Prefer a non-neutral category label if we ever saw one.
        if summ.category == NEUTRAL and a.category != NEUTRAL:
            summ.category = a.category

        if s.arrival_ts is None or a.start_ts < s.arrival_ts:
            s.arrival_ts = a.start_ts
        if s.last_seen_ts is None or a.end_ts > s.last_seen_ts:
            s.last_seen_ts = a.end_ts

    s.apps = sorted(per_app.values(), key=lambda x: x.seconds, reverse=True)
    return s


def day_bounds(day: datetime | None = None) -> tuple[float, float]:
    """Return the UTC start/end timestamps for the local calendar day."""
    now = day or datetime.now()
    start = datetime.combine(now.date(), time.min).astimezone()
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def summarize_day(storage: Storage, day: datetime | None = None) -> Summary:
    start, end = day_bounds(day)
    return summarize(storage.query(start, end))


def timeline_buckets(
    activities: list[Activity],
    start_ts: float,
    end_ts: float,
    bucket_seconds: float = 1800.0,
) -> list[dict[str, float]]:
    """Bucket active seconds by category for a simple timeline chart."""
    span = max(0.0, end_ts - start_ts)
    n = max(1, math.ceil(span / bucket_seconds))
    buckets = [
        {"start": start_ts + i * bucket_seconds,
         PRODUCTIVE: 0.0, UNPRODUCTIVE: 0.0, NEUTRAL: 0.0, "idle": 0.0}
        for i in range(n)
    ]

    for a in activities:
        idx = int((a.start_ts - start_ts) // bucket_seconds)
        if idx < 0 or idx >= n:
            continue
        key = "idle" if a.idle else a.category
        buckets[idx][key] = buckets[idx].get(key, 0.0) + a.duration

    return buckets


def humanize(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


__all__ = [
    "Summary",
    "AppSummary",
    "summarize",
    "summarize_day",
    "day_bounds",
    "timeline_buckets",
    "humanize",
]
