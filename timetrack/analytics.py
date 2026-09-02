"""Aggregation and productivity analytics over recorded activities."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from types import SimpleNamespace

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
    def desktime_productivity_pct(self) -> float:
        """DeskTime-style: productive time / time at the computer (active)."""
        if self.active_seconds <= 0:
            return 0.0
        return round(100.0 * self.productive_seconds / self.active_seconds, 1)

    def desktime_effectiveness_pct(self, expected_seconds: float = 8 * 3600) -> float:
        """DeskTime-style: productive / expected hours (may exceed 100% with overtime)."""
        if expected_seconds <= 0:
            return 0.0
        return round(100.0 * self.productive_seconds / expected_seconds, 1)

    @property
    def span_seconds(self) -> float:
        """Wall-clock time between first and last tracked sample (time at work)."""
        if self.arrival_ts is None or self.last_seen_ts is None:
            return 0.0
        return max(0.0, self.last_seen_ts - self.arrival_ts)

    @property
    def unproductivity_pct(self) -> float:
        if self.active_seconds <= 0:
            return 0.0
        return round(100.0 * self.unproductive_seconds / self.active_seconds, 1)

    def desktime_seconds(self, manual_seconds: float = 0.0) -> float:
        """DeskTime time = computer (active) + manually entered offline/project time."""
        return self.active_seconds + max(0.0, manual_seconds)


def _as_span(a: Activity | SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        app=getattr(a, "app", "") or "",
        title=getattr(a, "title", "") or "",
        url=getattr(a, "url", "") or "",
        category=getattr(a, "category", NEUTRAL) or NEUTRAL,
        idle=bool(getattr(a, "idle", False)),
        start_ts=float(a.start_ts),
        end_ts=float(a.end_ts),
        duration=float(getattr(a, "duration", 0) or max(0.0, float(a.end_ts) - float(a.start_ts))),
    )


def prepare_activities(
    activities: list[Activity],
    window_start: float | None = None,
    window_end: float | None = None,
) -> list[SimpleNamespace]:
    """Clip to window and remove overlapping double-counts (first-wins)."""
    clipped: list[SimpleNamespace] = []
    for raw in activities:
        a = _as_span(raw)
        s, e = a.start_ts, a.end_ts
        if window_start is not None:
            s = max(s, window_start)
        if window_end is not None:
            e = min(e, window_end)
        if e <= s:
            continue
        a.start_ts = s
        a.end_ts = e
        a.duration = e - s
        clipped.append(a)

    clipped.sort(key=lambda x: (x.start_ts, x.end_ts, 0 if not x.idle else 1))
    out: list[SimpleNamespace] = []
    cursor = float("-inf")
    for a in clipped:
        if a.end_ts <= cursor:
            continue
        if a.start_ts < cursor:
            a.start_ts = cursor
            a.duration = a.end_ts - a.start_ts
            if a.duration <= 0:
                continue
        out.append(a)
        cursor = a.end_ts
    return out


def summarize(
    activities: list[Activity],
    window_start: float | None = None,
    window_end: float | None = None,
) -> Summary:
    s = Summary()
    per_app: dict[str, AppSummary] = {}
    prepared = prepare_activities(activities, window_start, window_end)

    for a in prepared:
        s.total_seconds += a.duration
        # Arrival / left include idle — matches DeskTime "first and last tracked time"
        if s.arrival_ts is None or a.start_ts < s.arrival_ts:
            s.arrival_ts = a.start_ts
        if s.last_seen_ts is None or a.end_ts > s.last_seen_ts:
            s.last_seen_ts = a.end_ts

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
        if summ.category == NEUTRAL and a.category != NEUTRAL:
            summ.category = a.category

    s.apps = sorted(per_app.values(), key=lambda x: x.seconds, reverse=True)
    return s


def day_bounds(
    day: datetime | None = None,
    tz_name: str | None = None,
) -> tuple[float, float]:
    """Return start/end timestamps for a calendar day in company TZ (default IST)."""
    from .tzutil import day_bounds_tz

    return day_bounds_tz(day, tz_name=tz_name)


def summarize_day(storage: Storage, day: datetime | None = None) -> Summary:
    start, end = day_bounds(day)
    return summarize(storage.query(start, end), start, end)


def timeline_buckets(
    activities: list[Activity],
    start_ts: float,
    end_ts: float,
    bucket_seconds: float = 1800.0,
) -> list[dict[str, float]]:
    """Bucket seconds by category, splitting spans that cross bucket boundaries."""
    span = max(0.0, end_ts - start_ts)
    n = max(1, math.ceil(span / bucket_seconds))
    buckets = [
        {
            "start": start_ts + i * bucket_seconds,
            PRODUCTIVE: 0.0,
            UNPRODUCTIVE: 0.0,
            NEUTRAL: 0.0,
            "idle": 0.0,
        }
        for i in range(n)
    ]

    prepared = prepare_activities(activities, start_ts, end_ts)
    for a in prepared:
        t0, t1 = a.start_ts, a.end_ts
        key = "idle" if a.idle else a.category
        while t0 < t1 - 1e-9:
            idx = int((t0 - start_ts) // bucket_seconds)
            if idx < 0:
                t0 = start_ts
                continue
            if idx >= n:
                break
            bucket_end = min(end_ts, start_ts + (idx + 1) * bucket_seconds)
            piece = min(t1, bucket_end) - t0
            if piece <= 0:
                break
            buckets[idx][key] = buckets[idx].get(key, 0.0) + piece
            t0 += piece

    return buckets


def humanize(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{s}s"


def format_clock(ts: float | None, tz_name: str | None = None) -> str:
    """DeskTime-style clock in company TZ (default IST), e.g. 9:10 AM."""
    from .tzutil import format_clock_tz

    return format_clock_tz(ts, tz_name=tz_name)


def apps_with_share(summary: Summary) -> list[dict]:
    """Top apps with DeskTime-style percent-of-active share."""
    base = summary.active_seconds or 1.0
    rows = []
    for a in summary.apps:
        rows.append(
            {
                "app": a.app,
                "category": a.category,
                "seconds": a.seconds,
                "pct": round(100.0 * a.seconds / base, 1),
            }
        )
    return rows


def bucket_top_apps(
    activities: list[Activity],
    start_ts: float,
    end_ts: float,
    bucket_seconds: float,
) -> list[dict]:
    """Dominant app/domain per time bucket (for timeline tooltips)."""
    from .monitor import extract_domain, extract_url

    n = max(1, int(math.ceil((end_ts - start_ts) / bucket_seconds)))
    tops: list[dict] = [{"top_app": "", "top_domain": ""} for _ in range(n)]
    prepared = prepare_activities(activities, start_ts, end_ts)
    per_bucket: list[dict[str, float]] = [dict() for _ in range(n)]
    per_domain: list[dict[str, float]] = [dict() for _ in range(n)]

    for a in prepared:
        if a.idle:
            continue
        t0, t1 = a.start_ts, a.end_ts
        app = (a.app or "Unknown").strip()
        url = getattr(a, "url", "") or extract_url(a.app, a.title)
        domain = extract_domain(url)
        while t0 < t1 - 1e-9:
            idx = int((t0 - start_ts) // bucket_seconds)
            if idx < 0:
                t0 = start_ts
                continue
            if idx >= n:
                break
            bucket_end = min(end_ts, start_ts + (idx + 1) * bucket_seconds)
            piece = min(t1, bucket_end) - t0
            if piece <= 0:
                break
            per_bucket[idx][app] = per_bucket[idx].get(app, 0.0) + piece
            if domain:
                per_domain[idx][domain] = per_domain[idx].get(domain, 0.0) + piece
            t0 += piece

    for i in range(n):
        if per_bucket[i]:
            tops[i]["top_app"] = max(per_bucket[i], key=per_bucket[i].get)
        if per_domain[i]:
            tops[i]["top_domain"] = max(per_domain[i], key=per_domain[i].get)
    return tops


def productivity_bar_segments(
    activities: list[Activity],
    start_ts: float,
    end_ts: float,
    bucket_seconds: float = 300.0,
    tz_name: str | None = None,
    *,
    now_ts: float | None = None,
) -> list[dict]:
    """Fine timeline segments for a DeskTime-like productivity bar."""
    buckets = timeline_buckets(activities, start_ts, end_ts, bucket_seconds)
    tops = bucket_top_apps(activities, start_ts, end_ts, bucket_seconds)
    segments: list[dict] = []
    for i, b in enumerate(buckets):
        prod = float(b.get(PRODUCTIVE, 0.0))
        unprod = float(b.get(UNPRODUCTIVE, 0.0))
        neut = float(b.get(NEUTRAL, 0.0))
        idle = float(b.get("idle", 0.0))
        active = prod + unprod + neut
        if active <= 0 and idle <= 0:
            kind = "empty"
        elif idle >= active and idle >= bucket_seconds * 0.35:
            kind = "idle"
        elif prod >= unprod and prod >= neut:
            kind = "productive"
        elif unprod >= prod and unprod >= neut:
            kind = "unproductive"
        elif neut > 0:
            kind = "neutral"
        else:
            kind = "empty"
        bucket_end = b["start"] + bucket_seconds
        tip = tops[i] if i < len(tops) else {}
        segments.append(
            {
                "start": b["start"],
                "end": bucket_end,
                "label": format_clock(b["start"], tz_name=tz_name),
                "label_end": format_clock(bucket_end, tz_name=tz_name),
                "kind": kind,
                "productive": prod,
                "unproductive": unprod,
                "neutral": neut,
                "idle": idle,
                "top_app": tip.get("top_app") or "",
                "top_domain": tip.get("top_domain") or "",
                "fillable": False,
                "gap_start": None,
                "gap_end": None,
            }
        )
    return annotate_idle_gaps(
        segments, min_gap_seconds=bucket_seconds, now_ts=now_ts
    )


def annotate_idle_gaps(
    segments: list[dict],
    *,
    min_gap_seconds: float = 300.0,
    now_ts: float | None = None,
) -> list[dict]:
    """Mark consecutive empty bar buckets as one fillable idle/offline gap.

    Buckets that start in the future (after ``now_ts``) are never fillable.
    Gaps that straddle "now" are clipped so ``gap_end`` does not exceed now.
    """
    i = 0
    n = len(segments)
    while i < n:
        if segments[i].get("kind") != "empty":
            i += 1
            continue
        j = i
        while j < n and segments[j].get("kind") == "empty":
            j += 1
        gap_start = float(segments[i]["start"])
        gap_end = float(segments[j - 1].get("end") or segments[j - 1]["start"])
        if now_ts is not None:
            if gap_start >= now_ts:
                i = j
                continue
            gap_end = min(gap_end, float(now_ts))
        duration = gap_end - gap_start
        if duration >= min_gap_seconds:
            for k in range(i, j):
                seg_start = float(segments[k]["start"])
                if now_ts is not None and seg_start >= now_ts:
                    continue
                segments[k]["fillable"] = True
                segments[k]["gap_start"] = gap_start
                segments[k]["gap_end"] = gap_end
        i = j
    return segments


def idle_gap_list(segments: list[dict], tz_name: str | None = None) -> list[dict]:
    """Deduped fillable gaps from an annotated productivity bar."""
    gaps: list[dict] = []
    seen: set[tuple[float, float]] = set()
    for seg in segments:
        if not seg.get("fillable"):
            continue
        key = (float(seg["gap_start"]), float(seg["gap_end"]))
        if key in seen:
            continue
        seen.add(key)
        start, end = key
        gaps.append(
            {
                "start": start,
                "end": end,
                "duration": end - start,
                "label_start": format_clock(start, tz_name=tz_name),
                "label_end": format_clock(end, tz_name=tz_name),
            }
        )
    return gaps


def suppress_covered_gaps(
    segments: list[dict],
    covered_ranges: list[tuple[float, float]],
) -> list[dict]:
    """Clear fillable flags for ranges already submitted (pending/approved).

    Once a user requests offline/idle time — or an admin approves it — those
    buckets must not stay clickable as empty gaps.
    """
    if not segments or not covered_ranges:
        return segments
    for seg in segments:
        if not seg.get("fillable"):
            continue
        s = float(seg.get("start") or 0)
        e = float(seg.get("end") or s)
        for cs, ce in covered_ranges:
            if s < ce and e > cs:
                seg["fillable"] = False
                seg["gap_start"] = None
                seg["gap_end"] = None
                seg["requested"] = True
                break
    return segments


def merge_bar_segments(segments: list[dict]) -> list[dict]:
    """Collapse adjacent same-kind buckets into solid blocks (cleaner bar)."""
    if not segments:
        return []
    out: list[dict] = []
    cur = dict(segments[0])
    cur["buckets"] = 1
    for seg in segments[1:]:
        same_kind = seg.get("kind") == cur.get("kind")
        same_gap = (
            bool(seg.get("fillable")) == bool(cur.get("fillable"))
            and (
                not seg.get("fillable")
                or (
                    seg.get("gap_start") == cur.get("gap_start")
                    and seg.get("gap_end") == cur.get("gap_end")
                )
            )
        )
        if same_kind and same_gap:
            cur["end"] = seg.get("end", seg["start"])
            cur["buckets"] += 1
            # Keep first label; end label for tooltip range
            cur["label_end"] = seg.get("label") or cur.get("label")
        else:
            out.append(cur)
            cur = dict(seg)
            cur["buckets"] = 1
            cur["label_end"] = seg.get("label")
    out.append(cur)
    return out


def activity_timeline(
    activities: list[Activity],
    *,
    limit: int = 80,
) -> list[dict]:
    """Merged window/app/URL rows for detailed My DeskTime monitoring."""
    from .monitor import extract_domain, extract_url

    prepared = prepare_activities(activities)
    # Merge adjacent identical rows for readable table.
    rows: list[dict] = []
    for a in prepared:
        if a.idle:
            continue
        url = getattr(a, "url", "") or extract_url(a.app, a.title)
        domain = extract_domain(url)
        entry = {
            "app": a.app,
            "title": (a.title or "")[:180],
            "url": url[:300],
            "domain": domain,
            "category": a.category,
            "start_ts": a.start_ts,
            "end_ts": a.end_ts,
            "seconds": a.duration,
        }
        if (
            rows
            and rows[-1]["app"] == entry["app"]
            and rows[-1]["title"] == entry["title"]
            and rows[-1]["url"] == entry["url"]
            and rows[-1]["category"] == entry["category"]
            and abs(rows[-1]["end_ts"] - entry["start_ts"]) <= 2.0
        ):
            rows[-1]["end_ts"] = entry["end_ts"]
            rows[-1]["seconds"] += entry["seconds"]
        else:
            rows.append(entry)
    rows.sort(key=lambda r: r["start_ts"], reverse=True)
    return rows[:limit]


def top_sites(
    activities: list[Activity],
    *,
    limit: int = 12,
) -> list[dict]:
    """Aggregate inferred websites/domains by active time."""
    from .monitor import extract_domain, extract_url

    totals: dict[str, dict] = {}
    for a in prepare_activities(activities):
        if a.idle:
            continue
        url = getattr(a, "url", "") or extract_url(a.app, a.title)
        domain = extract_domain(url)
        if not domain:
            continue
        slot = totals.setdefault(
            domain,
            {"domain": domain, "seconds": 0.0, "category": a.category},
        )
        slot["seconds"] += a.duration
        if slot["category"] == NEUTRAL and a.category != NEUTRAL:
            slot["category"] = a.category
    ranked = sorted(totals.values(), key=lambda x: x["seconds"], reverse=True)[:limit]
    base = sum(r["seconds"] for r in ranked) or 1.0
    for r in ranked:
        r["pct"] = round(100.0 * r["seconds"] / base, 1)
    return ranked


def bar_view_hours(
    *,
    office_start_h: float,
    office_end_h: float,
    day_start_ts: float,
    arrival_ts: float | None,
    last_seen_ts: float | None,
    manual_ranges: list[tuple[float, float]] | None = None,
    is_today: bool = False,
    is_online: bool = False,
    now_ts: float | None = None,
    bar_mode: str = "work",
    office_pad_h: float = 1.5,
    activity_pad_h: float = 0.5,
    min_span_h: float = 4.0,
) -> tuple[float, float]:
    """Pick productivity-bar X-axis hours for one user-day.

    Default window: office hours ± ``office_pad_h`` (e.g. 9:30–6:30 → ~8:00–8:00).
    Expands only for real tracked activity (arrival, manual time, evening sessions).
    Idle fillable gaps alone do not stretch the chart back to midnight.
    """
    if bar_mode == "day":
        return 0.0, 24.0

    base_start_h = max(0.0, office_start_h - office_pad_h)
    base_end_h = min(24.0, office_end_h + office_pad_h)

    activity_start_ts = arrival_ts
    activity_end_ts = last_seen_ts
    for start_ts, end_ts in manual_ranges or []:
        if activity_start_ts is None or start_ts < activity_start_ts:
            activity_start_ts = start_ts
        if activity_end_ts is None or end_ts > activity_end_ts:
            activity_end_ts = end_ts

    has_real_activity = activity_start_ts is not None
    if is_today and now_ts is not None and has_real_activity:
        if activity_end_ts is None or now_ts > activity_end_ts:
            activity_end_ts = now_ts

    if not has_real_activity:
        view_start_h, view_end_h = base_start_h, base_end_h
    else:
        first_h = (activity_start_ts - day_start_ts) / 3600.0
        last_h = (
            (activity_end_ts - day_start_ts) / 3600.0
            if activity_end_ts is not None
            else first_h
        )
        if is_today and is_online and now_ts is not None:
            last_h = max(last_h, (now_ts - day_start_ts) / 3600.0)

        act_start_h = max(0.0, first_h - activity_pad_h)
        act_end_h = min(24.0, last_h + activity_pad_h)

        # Night / split shift entirely outside the office band → zoom to activity.
        outside_office = last_h < base_start_h - 0.25 or first_h > base_end_h + 0.25
        if outside_office:
            view_start_h, view_end_h = act_start_h, act_end_h
        else:
            view_start_h = min(base_start_h, act_start_h)
            view_end_h = max(base_end_h, act_end_h)

    if view_end_h - view_start_h < min_span_h:
        mid = (view_start_h + view_end_h) / 2.0
        view_start_h = max(0.0, mid - min_span_h / 2.0)
        view_end_h = min(24.0, mid + min_span_h / 2.0)

    return view_start_h, view_end_h


__all__ = [
    "Summary",
    "AppSummary",
    "summarize",
    "summarize_day",
    "day_bounds",
    "timeline_buckets",
    "humanize",
    "format_clock",
    "apps_with_share",
    "bar_view_hours",
    "productivity_bar_segments",
    "annotate_idle_gaps",
    "idle_gap_list",
    "suppress_covered_gaps",
    "merge_bar_segments",
    "prepare_activities",
    "activity_timeline",
    "top_sites",
]
