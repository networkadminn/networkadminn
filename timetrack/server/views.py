"""Web views: DeskTime-style dashboards, team overview, settings, screenshots."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required

from ..analytics import (
    activity_timeline,
    apps_with_share,
    day_bounds,
    format_clock,
    humanize,
    idle_gap_list,
    merge_bar_segments,
    productivity_bar_segments,
    summarize,
    suppress_covered_gaps,
    timeline_buckets,
    top_sites,
)
from ..config import (
    PRODUCTIVE,
    UNPRODUCTIVE,
    NEUTRAL,
    SITES_PRODUCTIVE,
    SITES_UNPRODUCTIVE,
    DEFAULT_RULES,
    DEFAULT_SITE_RULES,
    Config as RulesConfig,
    merge_rules,
)
from ..tzutil import (
    format_hour_label,
    format_hour_short,
    is_work_day,
    now_tz,
    office_datetime,
    today_tz,
    zone,
)
from .auth import admin_required
from .extensions import db
from .models import (
    Activity,
    ManualEntry,
    OfflineRequest,
    Project,
    Screenshot,
    User,
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    generate_token,
)
from .settings_util import company_tz_name, get_settings, load_server_rules, rules_path

views_bp = Blueprint("views", __name__)


def _tz() -> str:
    return company_tz_name()


def _parse_day(value: str | None) -> datetime:
    if value:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.combine(today_tz(_tz()), datetime.min.time())


def _day_nav(day: datetime) -> dict:
    return {
        "day": day.strftime("%Y-%m-%d"),
        "prev_day": (day - timedelta(days=1)).strftime("%Y-%m-%d"),
        "next_day": (day + timedelta(days=1)).strftime("%Y-%m-%d"),
    }


def _summarize_user_day(user_id: int, day: datetime):
    start, end = day_bounds(day, tz_name=_tz())
    return summarize(_activities_for(user_id, day), start, end)


def _week_strip(user_id: int | None, day: datetime) -> list[dict]:
    """Mon–Sun strip with productivity for DeskTime-style week navigation."""
    monday = day.date() - timedelta(days=day.weekday())
    expected = _expected_seconds()
    out = []
    for i in range(7):
        d = datetime.combine(monday + timedelta(days=i), datetime.min.time())
        if user_id is None:
            # Team average for that day
            users = db.session.execute(db.select(User)).scalars().all()
            prods = []
            for u in users:
                s = _summarize_user_day(u.id, d)
                if s.active_seconds > 0:
                    prods.append(s.desktime_productivity_pct)
            pct = round(sum(prods) / len(prods), 1) if prods else 0.0
            seconds = sum(_summarize_user_day(u.id, d).active_seconds for u in users)
        else:
            s = _summarize_user_day(user_id, d)
            pct = s.desktime_productivity_pct
            seconds = s.active_seconds
        out.append(
            {
                "day": d.strftime("%Y-%m-%d"),
                "label": d.strftime("%a"),
                "date_num": d.strftime("%d"),
                "is_today": d.date() == today_tz(_tz()),
                "is_selected": d.date() == day.date(),
                "is_work_day": is_work_day(d, get_settings().work_days),
                "pct": pct,
                "seconds": seconds,
                "human": humanize(seconds),
            }
        )
    return out


def _spark_polyline(values: list[float], *, width: float = 120.0, height: float = 40.0) -> tuple[str, str]:
    """Build SVG polyline + area polygon points for week productivity sparklines."""
    vals = list(values) or [0.0] * 7
    n = len(vals)
    den = max(n - 1, 1)
    pts: list[str] = []
    for i, raw in enumerate(vals):
        v = max(0.0, min(100.0, float(raw or 0.0)))
        x = round((i / den) * width, 1)
        y = round(height - 2 - (v / 100.0) * (height - 6), 1)
        pts.append(f"{x},{y}")
    line = " ".join(pts)
    area = f"0,{height:.0f} {line} {width:.0f},{height:.0f}"
    return line, area


def _manual_for(user_id: int, day: datetime) -> list[ManualEntry]:
    start, end = day_bounds(day, tz_name=_tz())
    return list(
        db.session.execute(
            db.select(ManualEntry)
            .filter(ManualEntry.user_id == user_id)
            .filter(ManualEntry.start_ts >= start, ManualEntry.start_ts < end)
            .order_by(ManualEntry.start_ts.desc())
        ).scalars()
    )


def _activities_for(user_id: int, day: datetime) -> list[Activity]:
    start, end = day_bounds(day, tz_name=_tz())
    return list(
        db.session.execute(
            db.select(Activity)
            .filter(Activity.user_id == user_id)
            .filter(Activity.end_ts >= start, Activity.start_ts < end)
            .order_by(Activity.start_ts.asc())
        ).scalars()
    )


def _screenshots_for(user_id: int, day: datetime) -> list[Screenshot]:
    start, end = day_bounds(day, tz_name=_tz())
    return list(
        db.session.execute(
            db.select(Screenshot)
            .filter(Screenshot.user_id == user_id)
            .filter(Screenshot.ts >= start, Screenshot.ts < end)
            .order_by(Screenshot.ts.desc())
        ).scalars()
    )


def _last_seen(user_id: int) -> float | None:
    row = db.session.execute(
        db.select(db.func.max(Activity.end_ts)).filter(Activity.user_id == user_id)
    ).scalar_one_or_none()
    return float(row) if row else None


def _latest_activity(user_id: int) -> Activity | None:
    """Most recent activity row for live 'now' display."""
    return db.session.execute(
        db.select(Activity)
        .filter(Activity.user_id == user_id)
        .order_by(Activity.end_ts.desc())
        .limit(1)
    ).scalar_one_or_none()


def _live_snapshot(user_id: int, *, now_ts: float, online_window: float) -> dict:
    """Current app / site / idle for the live online board."""
    from ..monitor import extract_domain

    act = _latest_activity(user_id)
    if act is None:
        return {
            "app": "—",
            "title": "",
            "domain": "",
            "category": "neutral",
            "idle": False,
            "ago": "never",
            "end_ts": None,
        }
    domain = extract_domain(act.url or "") or ""
    if not domain and not act.idle:
        from ..monitor import extract_url

        domain = extract_domain(extract_url(act.app, act.title)) or ""
    ago_s = max(0, int(now_ts - act.end_ts))
    if ago_s < 60:
        ago = f"{ago_s}s ago"
    elif ago_s < 3600:
        ago = f"{ago_s // 60}m ago"
    else:
        ago = f"{ago_s // 3600}h ago"
    return {
        "app": act.app or "—",
        "title": (act.title or "")[:80],
        "domain": domain,
        "category": act.category or "neutral",
        "idle": bool(act.idle),
        "ago": ago,
        "end_ts": act.end_ts,
        "fresh": ago_s <= online_window,
    }


def _expected_seconds() -> float:
    return float(get_settings().expected_hours) * 3600.0


def _is_off_day(day: datetime) -> bool:
    return not is_work_day(day, get_settings().work_days)


def _is_late(arrival_ts: float | None, day: datetime) -> bool:
    if arrival_ts is None or _is_off_day(day):
        return False
    settings = get_settings()
    start_h = float(settings.office_start_hour or settings.expected_arrival_hour or 9.5)
    expected = office_datetime(day, start_h, settings.timezone or _tz())
    return arrival_ts > expected.timestamp()


def _dominant_category(
    prod: float, neut: float, unprod: float, idle: float, *, show_idle: bool
) -> str:
    scores = {
        "productive": prod,
        "neutral": neut,
        "unproductive": unprod,
        "idle": idle if show_idle else 0.0,
    }
    if sum(scores.values()) <= 0:
        return "idle"
    return max(scores, key=scores.get)


def _bar_stack_columns(
    segments: list[dict],
    *,
    bucket_seconds: float = 300.0,
    view_start: float | None = None,
    view_end: float | None = None,
) -> list[dict]:
    """Per-bucket columns: height = productivity %, color = dominant category."""
    cols: list[dict] = []
    total = max(1.0, float(bucket_seconds))
    span = (
        max(1.0, (view_end or 0) - (view_start or 0))
        if view_start is not None and view_end is not None
        else 0.0
    )
    for seg in segments:
        s = float(seg.get("start") or 0)
        e = float(seg.get("end") or (s + bucket_seconds))
        if view_start is not None and e <= view_start:
            continue
        if view_end is not None and s >= view_end:
            continue
        if view_start is not None:
            s = max(s, view_start)
        if view_end is not None:
            e = min(e, view_end)
        prod = float(seg.get("productive") or 0)
        unprod = float(seg.get("unproductive") or 0)
        neut = float(seg.get("neutral") or 0)
        idle = float(seg.get("idle") or 0)
        tracked = prod + unprod + neut + idle
        active = (prod + unprod + neut) > 0
        show_idle = idle > 0 and (active or idle >= total * 0.35)
        is_fill = bool(seg.get("fillable"))
        is_requested = bool(seg.get("requested"))
        if tracked <= 0 and not is_fill and not is_requested:
            continue
        orig_start = float(seg.get("start") or 0)
        left = width = 0.0
        if span > 0 and view_start is not None:
            slot = max(0.0, min(100.0, (total / span) * 100.0))
            # Fill the slot — nearly continuous bars across the day
            width = max(0.2, slot * 0.96)
            left = max(0.0, min(100.0 - width, ((orig_start - view_start) / span) * 100.0))

        active_sec = prod + unprod + neut
        qual = round(100.0 * prod / active_sec, 1) if active_sec > 0 else 0.0
        category = _dominant_category(prod, neut, unprod, idle, show_idle=show_idle)
        if is_fill and tracked <= 0:
            category = "gap"
        elif is_requested and tracked <= 0:
            category = "requested"

        # Height = how full the 5-min slot was; color = dominant activity type
        fill = round(min(100.0, 100.0 * tracked / total), 1) if tracked > 0 else 0.0
        if fill > 0:
            height = round(max(fill, 8.0), 1)
        else:
            height = 0.0

        cols.append(
            {
                "start": s,
                "end": e,
                "left": round(left, 4),
                "width": round(width, 4),
                "quality": qual,
                "productivity": qual,
                "height_pct": height if (active or show_idle or tracked > 0) else 0.0,
                "stack_p": round(100.0 * prod / total, 2) if prod else 0.0,
                "stack_n": round(100.0 * neut / total, 2) if neut else 0.0,
                "stack_u": round(100.0 * unprod / total, 2) if unprod else 0.0,
                "stack_i": round(100.0 * idle / total, 2) if idle else 0.0,
                "category": category,
                "label": seg.get("label") or "",
                "label_end": seg.get("label_end") or seg.get("label") or "",
                "fillable": is_fill,
                "requested": is_requested,
                "gap_start": seg.get("gap_start"),
                "gap_end": seg.get("gap_end"),
                "productive_m": round(prod / 60.0, 1),
                "neutral_m": round(neut / 60.0, 1),
                "unproductive_m": round(unprod / 60.0, 1),
                "idle_m": round(idle / 60.0, 1),
                "active": active or show_idle,
                "top_app": seg.get("top_app") or "",
                "top_domain": seg.get("top_domain") or "",
            }
        )
    return cols


def _bar_hour_ticks(view_start: float, view_end: float, tz_name: str) -> list[dict]:
    """Sparse, readable hour labels (e.g. 9 AM · 12 PM · 3 PM)."""
    tz = zone(tz_name)
    span = max(1.0, view_end - view_start)
    span_h = span / 3600.0
    # Full-day charts: label every 2 hours so 12 AM … 12 AM stays readable
    if span_h <= 8:
        step_h = 1
    elif span_h <= 14:
        step_h = 2
    else:
        step_h = 2

    ticks: list[dict] = []
    local0 = datetime.fromtimestamp(view_start, tz=tz)
    first = local0.replace(minute=0, second=0, microsecond=0)
    if first.timestamp() < view_start - 1:
        first = first + timedelta(hours=1)
    while first.hour % step_h != 0:
        first = first + timedelta(hours=1)
        if first.timestamp() > view_end:
            break

    t = first.timestamp()
    while t <= view_end + 1:
        left = ((t - view_start) / span) * 100.0
        if 0.0 <= left <= 100.0:
            local = datetime.fromtimestamp(t, tz=tz)
            h = local.hour
            ticks.append(
                {
                    "left": round(left, 3),
                    "label": format_hour_short(float(h)),
                    "ts": t,
                    "major": h % 3 == 0 or h in (0, 9, 12, 15, 18),
                }
            )
        t += step_h * 3600.0

    def _edge(ts: float, prefer_left: bool) -> None:
        left = ((ts - view_start) / span) * 100.0
        left = max(0.0, min(100.0, left))
        # Keep both midnight ends visible on a full-day chart
        min_gap = 3.0 if prefer_left or left >= 97.0 else 5.5
        if any(abs(x["left"] - left) < min_gap for x in ticks):
            return
        local = datetime.fromtimestamp(ts, tz=tz)
        hour_f = local.hour + local.minute / 60.0
        ticks.append(
            {
                "left": round(left, 3),
                "label": format_hour_short(hour_f),
                "ts": ts,
                "major": True,
                "edge": "start" if prefer_left else "end",
            }
        )

    if ticks:
        _edge(view_start, True)
        _edge(view_end, False)
        ticks.sort(key=lambda x: x["left"])
    elif span_h > 0:
        _edge(view_start, True)
        _edge(view_end, False)
    return ticks


def _presence_tracks(segments: list[dict], view_start: float, view_end: float) -> list[dict]:
    span = max(1.0, view_end - view_start)
    tracks: list[dict] = []
    i, n = 0, len(segments)
    while i < n:
        if segments[i].get("kind") == "empty" and not segments[i].get("fillable"):
            i += 1
            continue
        j = i
        while j < n and not (segments[j].get("kind") == "empty" and not segments[j].get("fillable")):
            j += 1
        s = float(segments[i]["start"])
        e = float(segments[j - 1].get("end") or segments[j - 1]["start"])
        if e <= view_start or s >= view_end:
            i = j
            continue
        s, e = max(s, view_start), min(e, view_end)
        left = max(0.0, ((s - view_start) / span) * 100.0)
        width = max(0.15, min(100.0 - left, ((e - s) / span) * 100.0))
        tracks.append({"left": round(left, 3), "width": round(width, 3)})
        i = j
    return tracks


def _bar_away_regions(
    *,
    view_start: float,
    view_end: float,
    arrival_ts: float | None,
    last_seen_ts: float | None,
    is_today: bool,
    is_online: bool,
) -> list[dict]:
    span = max(1.0, view_end - view_start)
    regions: list[dict] = []
    if arrival_ts and arrival_ts > view_start:
        w = min(100.0, ((arrival_ts - view_start) / span) * 100.0)
        if w > 0.4:
            regions.append({"left": 0.0, "width": round(w, 3), "kind": "before"})
    if last_seen_ts and not (is_today and is_online) and last_seen_ts < view_end:
        left = max(0.0, ((last_seen_ts - view_start) / span) * 100.0)
        w = 100.0 - left
        if w > 0.4:
            regions.append({"left": round(left, 3), "width": round(w, 3), "kind": "after"})
    return regions


def _timeline_payload(
    columns: list[dict],
    *,
    hour_ticks: list[dict],
    view_start: float,
    view_end: float,
    presence_tracks: list[dict],
    away_regions: list[dict],
    now_pct: float | None,
    office_left: float,
    office_width: float,
) -> dict:
    bars = []
    for col in columns:
        tip = col.get("label") or ""
        end_lbl = col.get("label_end") or tip
        time_range = f"{tip}–{end_lbl}" if end_lbl != tip else tip
        bars.append(
            {
                "start": float(col.get("start") or 0),
                "end": float(col.get("end") or 0),
                "height": float(col.get("height_pct") or 0),
                "productivity": float(col.get("productivity") or 0),
                "category": col.get("category") or "idle",
                "time": time_range,
                "app": col.get("top_app") or "",
                "domain": col.get("top_domain") or "",
                "productive_m": float(col.get("productive_m") or 0),
                "neutral_m": float(col.get("neutral_m") or 0),
                "unproductive_m": float(col.get("unproductive_m") or 0),
                "idle_m": float(col.get("idle_m") or 0),
                "fillable": bool(col.get("fillable")),
                "active": bool(col.get("active")),
                "gap_start": col.get("gap_start"),
                "gap_end": col.get("gap_end"),
            }
        )
    return {
        "view_start": view_start,
        "view_end": view_end,
        "now_pct": now_pct,
        "office_left": office_left,
        "office_width": office_width,
        "bars": bars,
        "ticks": hour_ticks,
        "presence": presence_tracks,
        "away": away_regions,
    }


def _chart_payload(activities: list[Activity], day: datetime) -> dict:
    start, end = day_bounds(day, tz_name=_tz())
    summary = summarize(activities, start, end)
    buckets = timeline_buckets(activities, start, end, bucket_seconds=3600.0)
    labels = []
    tz = zone(_tz())
    for b in buckets:
        h = datetime.fromtimestamp(b["start"], tz=tz).hour
        suffix = "AM" if h < 12 else "PM"
        labels.append(f"{(h % 12) or 12} {suffix}")
    expected = _expected_seconds()
    prod_pct = []
    presence = []
    bar_colors = []
    for b in buckets:
        prod = float(b.get(PRODUCTIVE, 0))
        unprod = float(b.get(UNPRODUCTIVE, 0))
        neut = float(b.get(NEUTRAL, 0))
        tracked = prod + unprod + neut
        idle = float(b.get("idle", 0))
        presence.append(1 if tracked + idle > 30 else 0)
        if tracked <= 0:
            prod_pct.append(0)
            bar_colors.append("rgba(0,0,0,0)")
        else:
            prod_pct.append(round(100.0 * prod / tracked, 1))
            if unprod > prod and unprod >= neut:
                bar_colors.append("#D9534F")
            elif prod > 0 and prod >= unprod:
                bar_colors.append("#4CAF6A")
            else:
                bar_colors.append("#94A3B8")

    # 5-minute columns (office hours): height = productive/300, color = quality
    settings = get_settings()
    office_start_h = float(
        settings.office_start_hour or settings.expected_arrival_hour or 9.5
    )
    office_end_h = float(settings.office_end_hour or 18.5)
    bucket_sec = 300.0
    fine_start = start + office_start_h * 3600.0
    fine_end = min(end, start + office_end_h * 3600.0)
    if fine_end <= fine_start:
        fine_start, fine_end = start, end
    fine_buckets = timeline_buckets(
        activities, fine_start, fine_end, bucket_seconds=bucket_sec
    )
    fine_labels: list[str] = []
    fine_height: list[float] = []
    fine_quality: list[float] = []
    fine_presence: list[int] = []
    fine_productive: list[float] = []
    fine_unproductive: list[float] = []
    fine_neutral: list[float] = []
    fine_idle: list[float] = []
    for b in fine_buckets:
        prod = float(b.get(PRODUCTIVE, 0))
        unprod = float(b.get(UNPRODUCTIVE, 0))
        neut = float(b.get(NEUTRAL, 0))
        idle = float(b.get("idle", 0))
        tracked = prod + unprod + neut
        present = 1 if tracked + idle > 5 else 0
        # Height: local effectiveness — productive share of the 5-minute window
        height = round(100.0 * prod / bucket_sec, 1) if present else 0.0
        # Color: productivity quality among active (non-idle) time
        if tracked <= 0:
            quality = 0.0
        else:
            quality = round(100.0 * prod / tracked, 1)
        local = datetime.fromtimestamp(b["start"], tz=tz)
        # Tick labels only on the hour; Chart.js still gets an index label
        if local.minute == 0:
            h = local.hour
            suffix = "AM" if h < 12 else "PM"
            fine_labels.append(f"{(h % 12) or 12} {suffix}")
        else:
            fine_labels.append(local.strftime("%H:%M"))
        fine_height.append(height)
        fine_quality.append(quality)
        fine_presence.append(present)
        fine_productive.append(round(prod / 60.0, 2))
        fine_unproductive.append(round(unprod / 60.0, 2))
        fine_neutral.append(round(neut / 60.0, 2))
        fine_idle.append(round(idle / 60.0, 2))

    return {
        "labels": labels,
        "productive": [round(b.get(PRODUCTIVE, 0) / 60.0, 1) for b in buckets],
        "unproductive": [round(b.get(UNPRODUCTIVE, 0) / 60.0, 1) for b in buckets],
        "neutral": [round(b.get(NEUTRAL, 0) / 60.0, 1) for b in buckets],
        "idle": [round(b.get("idle", 0) / 60.0, 1) for b in buckets],
        "prod_pct": prod_pct,
        "presence": presence,
        "bar_colors": bar_colors,
        "fine": {
            "labels": fine_labels,
            "height": fine_height,
            "quality": fine_quality,
            "presence": fine_presence,
            "productive": fine_productive,
            "unproductive": fine_unproductive,
            "neutral": fine_neutral,
            "idle": fine_idle,
            "bucket_seconds": int(bucket_sec),
            "office_start": office_start_h,
            "office_end": office_end_h,
        },
        "category": {
            "productive": round(summary.productive_seconds / 60.0, 1),
            "unproductive": round(summary.unproductive_seconds / 60.0, 1),
            "neutral": round(summary.neutral_seconds / 60.0, 1),
            "idle": round(summary.idle_seconds / 60.0, 1),
        },
        "apps": {
            "labels": [a.app for a in summary.apps[:8]],
            "minutes": [round(a.seconds / 60.0, 1) for a in summary.apps[:8]],
        },
        "desktime_productivity": summary.desktime_productivity_pct,
        "desktime_effectiveness": summary.desktime_effectiveness_pct(expected),
    }


def _project_timeline(
    manual: list[ManualEntry],
    day: datetime,
    *,
    view_start: float | None = None,
    view_end: float | None = None,
) -> list[dict]:
    start, end = day_bounds(day, tz_name=_tz())
    if view_start is not None:
        start = float(view_start)
    if view_end is not None:
        end = float(view_end)
    span = max(end - start, 1.0)
    colors = ("#5B8DEF", "#1F9D6C", "#F59E0B", "#8B5CF6", "#0EA5E9", "#E11D48")
    out = []
    for i, m in enumerate(sorted(manual, key=lambda e: e.start_ts)):
        if m.end_ts <= start or m.start_ts >= end:
            continue
        clipped_start = max(float(m.start_ts), start)
        clipped_end = min(float(m.end_ts), end)
        left = max(0.0, min(100.0, ((clipped_start - start) / span) * 100.0))
        width = max(0.4, min(100.0 - left, ((clipped_end - clipped_start) / span) * 100.0))
        name = m.project.name if m.project else "General"
        out.append(
            {
                "left": round(left, 2),
                "width": round(width, 2),
                "name": name,
                "note": m.note or "",
                "color": colors[i % len(colors)],
                "label": f"{format_clock(m.start_ts, tz_name=_tz())} · {humanize(m.duration)}",
            }
        )
    return out


def _employee_day_context(user: User, day: datetime, *, is_self: bool) -> dict:
    acts = _activities_for(user.id, day)
    start, end = day_bounds(day, tz_name=_tz())
    summary = summarize(acts, start, end)
    expected = _expected_seconds()
    manual = _manual_for(user.id, day)
    manual_seconds = sum(m.duration for m in manual)
    desktime_seconds = summary.desktime_seconds(manual_seconds)
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    settings = get_settings()
    last = _last_seen(user.id)
    now_ts = datetime.now().timestamp()
    is_online = last is not None and (now_ts - last) <= cfg.online_window
    is_today = day.date() == today_tz(_tz())
    off_day = _is_off_day(day)
    left_clock = format_clock(summary.last_seen_ts, tz_name=_tz())
    if is_online and is_today:
        left_display = "ONLINE"
        left_sub = left_clock if left_clock != "—" else ""
    else:
        left_display = left_clock
        left_sub = ""
    week = _week_strip(user.id, day)
    spark = [d["pct"] for d in week]
    spark_line, spark_area = _spark_polyline(spark)
    office_start = float(settings.office_start_hour or settings.expected_arrival_hour or 9.5)
    office_end = float(settings.office_end_hour or 18.5)
    absent = (not off_day) and summary.total_seconds <= 0 and manual_seconds <= 0
    # Never offer fillable gaps in the future (today: clip at now; future days: none)
    today = today_tz(_tz())
    if day.date() > today:
        gap_now_ts = start  # entire day is in the future
    elif day.date() == today:
        gap_now_ts = now_tz(_tz()).timestamp()
    else:
        gap_now_ts = None  # past day — all empty ranges may be filled
    bar_raw = productivity_bar_segments(
        acts,
        start,
        end,
        bucket_seconds=300.0,
        tz_name=_tz(),
        now_ts=gap_now_ts,
    )

    day_start, day_end = start, end
    covered_reqs = list(
        db.session.execute(
            db.select(OfflineRequest)
            .filter(
                OfflineRequest.user_id == user.id,
                OfflineRequest.status.in_(("pending", "approved")),
                OfflineRequest.end_ts > day_start,
                OfflineRequest.start_ts < day_end,
            )
        ).scalars()
    )
    covered_ranges = [(float(r.start_ts), float(r.end_ts)) for r in covered_reqs]
    bar_raw = suppress_covered_gaps(bar_raw, covered_ranges)
    gaps = idle_gap_list(bar_raw, tz_name=_tz())
    bar = merge_bar_segments(bar_raw)

    # Employees default to work-hours window so 5-min columns stay readable;
    # ?bar=day shows the full calendar day.
    bar_mode = (request.args.get("bar") or ("work" if is_self else "day")).strip().lower()
    if bar_mode not in ("work", "day"):
        bar_mode = "work" if is_self else "day"
    if bar_mode == "work":
        pad_h = 0.5
        view_start_h = max(0.0, office_start - pad_h)
        view_end_h = min(24.0, max(office_end + pad_h, view_start_h + 4.0))
    else:
        view_start_h = 0.0
        view_end_h = 24.0
    view_start = start + view_start_h * 3600.0
    view_end = start + view_end_h * 3600.0
    view_span = max(1.0, view_end - view_start)
    bucket_seconds = 300.0
    bar_display = productivity_bar_segments(
        acts,
        view_start,
        view_end,
        bucket_seconds=bucket_seconds,
        tz_name=_tz(),
        now_ts=gap_now_ts,
    )
    bar_display = suppress_covered_gaps(bar_display, covered_ranges)
    bar_columns = _bar_stack_columns(
        bar_display, bucket_seconds=bucket_seconds, view_start=view_start, view_end=view_end
    )
    presence_tracks = _presence_tracks(bar_raw, view_start, view_end)
    bar_hour_ticks = _bar_hour_ticks(view_start, view_end, _tz())
    bar_away_regions = _bar_away_regions(
        view_start=view_start,
        view_end=view_end,
        arrival_ts=summary.arrival_ts,
        last_seen_ts=summary.last_seen_ts,
        is_today=is_today,
        is_online=is_online,
    )
    day_span_h = max(view_end_h - view_start_h, 0.01)
    office_left = max(0.0, min(100.0, ((office_start - view_start_h) / day_span_h) * 100.0))
    office_right = max(0.0, min(100.0, ((office_end - view_start_h) / day_span_h) * 100.0))
    office_width = max(0.0, office_right - office_left)
    now_pct = None
    if is_today:
        local = now_tz(_tz())
        now_ts = local.timestamp()
        if view_start <= now_ts <= view_end:
            now_pct = round(((now_ts - view_start) / view_span) * 100.0, 2)
    timeline_chart = _timeline_payload(
        bar_columns,
        hour_ticks=bar_hour_ticks,
        view_start=view_start,
        view_end=view_end,
        presence_tracks=presence_tracks,
        away_regions=bar_away_regions,
        now_pct=now_pct,
        office_left=office_left,
        office_width=office_width,
    )

    gap_seconds = sum(g["duration"] for g in gaps)
    my_requests = list(
        db.session.execute(
            db.select(OfflineRequest)
            .filter(
                OfflineRequest.user_id == user.id,
                OfflineRequest.start_ts >= day_start,
                OfflineRequest.start_ts < day_end,
            )
            .order_by(OfflineRequest.created_at.desc())
        ).scalars()
    )
    return {
        "subject": user,
        "is_self": is_self,
        **_day_nav(day),
        "day_label": day.strftime("%a, %B %d, %Y").replace(" 0", " "),
        "summary": summary,
        "screenshots": _screenshots_for(user.id, day),
        "charts": _chart_payload(acts, day),
        "apps": apps_with_share(summary),
        "sites": top_sites(acts),
        "timeline": activity_timeline(acts, limit=100),
        "bar": bar,
        "bar_columns": bar_columns,
        "bar_hour_ticks": bar_hour_ticks,
        "bar_away_regions": bar_away_regions,
        "presence_tracks": presence_tracks,
        "timeline_chart": timeline_chart,
        "view_start_ts": view_start,
        "view_end_ts": view_end,
        "bar_view_label": f"{format_hour_short(view_start_h)} – {format_hour_short(view_end_h)}",
        "bar_mode": bar_mode,
        "gaps": gaps,
        "gap_seconds": gap_seconds,
        "my_requests": my_requests,
        "idle_threshold": int(settings.idle_threshold or 180),
        "arrived": format_clock(summary.arrival_ts, tz_name=_tz()),
        "left": left_clock,
        "left_display": left_display,
        "left_sub": left_sub,
        "is_online": is_online,
        "is_today": is_today,
        "late": _is_late(summary.arrival_ts, day),
        "absent": absent,
        "off_day": off_day,
        "tz_name": _tz(),
        "office_hours_label": f"{format_hour_short(office_start)} – {format_hour_short(office_end)} IST"
        if _tz() == "Asia/Kolkata"
        else f"{format_hour_short(office_start)} – {format_hour_short(office_end)}",
        "office_left_pct": office_left,
        "office_width_pct": office_width,
        "now_pct": now_pct,
        "dt_productivity": summary.desktime_productivity_pct,
        "dt_effectiveness": summary.desktime_effectiveness_pct(expected),
        "expected_hours": expected / 3600.0,
        "desktime_seconds": desktime_seconds,
        "humanize": humanize,
        "week": week,
        "spark": spark,
        "spark_line": spark_line,
        "spark_area": spark_area,
        "manual": manual,
        "manual_seconds": manual_seconds,
        "project_timeline": _project_timeline(
            manual, day, view_start=view_start, view_end=view_end
        ),
        "projects": list(
            db.session.execute(
                db.select(Project)
                .filter_by(active=True, organization_id=user.organization_id or 1)
                .order_by(Project.name)
            ).scalars()
        ),
    }


@views_bp.route("/")
def home():
    if not current_user.is_authenticated:
        return render_template("public_home.html")
    if current_user.is_admin:
        return redirect(url_for("views.admin"))
    return redirect(url_for("views.me"))


@views_bp.route("/download")
def download_page():
    from .releases import scan_releases

    return render_template("public_download.html", releases=scan_releases())


@views_bp.route("/download/<platform>/<path:filename>")
def download_file(platform: str, filename: str):
    """Serve release artifacts (linux/windows/mac)."""
    from .releases import releases_dir

    platform = (platform or "").lower().strip()
    if platform not in {"linux", "windows", "mac", "macos"}:
        abort(404)
    name = Path(filename).name
    if not name or name.startswith("."):
        abort(404)
    base = releases_dir()
    if not (base / name).is_file():
        abort(404)
    return send_from_directory(str(base), name, as_attachment=True, download_name=name)


@views_bp.route("/me")
@login_required
def me():
    if current_user.is_admin:
        return redirect(url_for("views.admin", day=_parse_day(request.args.get("day")).strftime("%Y-%m-%d")))
    day = _parse_day(request.args.get("day"))
    ctx = _employee_day_context(current_user, day, is_self=True)
    from .attendance_util import month_summary

    month_start = day.replace(day=1)
    ctx["month_attendance"] = month_summary(
        current_user.id, month_start.year, month_start.month, tz_name=_tz()
    )
    return render_template("employee_status.html", **ctx)


@views_bp.route("/admin")
@admin_required
def admin():
    day = _parse_day(request.args.get("day"))
    team_id = request.args.get("team_id", type=int)
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    now_ts = datetime.now().timestamp()
    expected = _expected_seconds()
    start, end = day_bounds(day, tz_name=_tz())
    off_day = _is_off_day(day)

    from .models import Team
    from .tenancy import current_org_id, teams_in_org_query, users_in_org_query

    teams = list(
        db.session.execute(teams_in_org_query(current_org_id()).order_by(Team.name)).scalars()
    )

    users = list(
        db.session.execute(
            users_in_org_query(current_org_id())
            .filter(User.role != "admin")
            .filter(User.enabled.is_(True))
            .order_by(User.username.asc())
        ).scalars()
    )
    if team_id:
        users = [u for u in users if u.team_id == team_id]

    rows = []
    team = {
        "active": 0.0,
        "productive": 0.0,
        "idle": 0.0,
        "unproductive": 0.0,
        "online": 0,
        "present": 0,
        "late": 0,
        "absent": 0,
    }
    for u in users:
        acts = _activities_for(u.id, day)
        s = summarize(acts, start, end)
        manual = _manual_for(u.id, day)
        manual_seconds = sum(m.duration for m in manual)
        desktime_seconds = s.desktime_seconds(manual_seconds)
        last = _last_seen(u.id)
        online = last is not None and (now_ts - last) <= cfg.online_window
        late = _is_late(s.arrival_ts, day)
        absent = (not off_day) and s.total_seconds <= 0 and manual_seconds <= 0
        live = _live_snapshot(u.id, now_ts=now_ts, online_window=cfg.online_window)
        row = {
            "user": u,
            "summary": s,
            "manual_seconds": manual_seconds,
            "desktime_seconds": desktime_seconds,
            "online": online,
            "last_seen": last,
            "live": live,
            "shots": db.session.execute(
                db.select(db.func.count(Screenshot.id)).filter(
                    Screenshot.user_id == u.id,
                    Screenshot.ts >= start,
                    Screenshot.ts < end,
                )
            ).scalar_one(),
            "arrived": format_clock(s.arrival_ts, tz_name=_tz()),
            "left": format_clock(s.last_seen_ts, tz_name=_tz()),
            "late": late and not absent,
            "absent": absent,
            "dt_productivity": s.desktime_productivity_pct,
            "dt_effectiveness": s.desktime_effectiveness_pct(expected),
        }
        rows.append(row)
        team["active"] += s.active_seconds
        team["productive"] += s.productive_seconds
        team["idle"] += s.idle_seconds
        team["unproductive"] += s.unproductive_seconds
        team["online"] += 1 if online else 0
        if absent:
            team["absent"] += 1
        else:
            team["present"] += 1
            if late:
                team["late"] += 1

    tracked = [r for r in rows if not r["absent"]]
    avg_prod = (
        round(sum(r["dt_productivity"] for r in tracked) / len(tracked), 1)
        if tracked
        else 0.0
    )
    avg_eff = (
        round(sum(r["dt_effectiveness"] for r in tracked) / len(tracked), 1)
        if tracked
        else 0.0
    )
    top_productive = sorted(
        tracked, key=lambda r: r["summary"].productive_seconds, reverse=True
    )[:5]
    top_effective = sorted(
        tracked, key=lambda r: r["dt_effectiveness"], reverse=True
    )[:5]
    top_unproductive = sorted(
        tracked, key=lambda r: r["summary"].unproductive_seconds, reverse=True
    )[:5]
    top_late = sorted(
        [r for r in tracked if r["late"]],
        key=lambda r: r["summary"].arrival_ts or 0.0,
        reverse=True,
    )[:5]

    # Team-wide app usage for the day
    app_totals: dict[str, dict] = {}
    for r in rows:
        for a in r["summary"].apps:
            slot = app_totals.setdefault(
                a.app, {"app": a.app, "category": a.category, "seconds": 0.0}
            )
            slot["seconds"] += a.seconds
            if slot["category"] == "neutral" and a.category != "neutral":
                slot["category"] = a.category
    team_apps = sorted(app_totals.values(), key=lambda x: x["seconds"], reverse=True)[
        :12
    ]
    active_base = team["active"] or 1.0
    for a in team_apps:
        a["pct"] = round(100.0 * a["seconds"] / active_base, 1)

    flagged_shots = list(
        db.session.execute(
            db.select(Screenshot)
            .filter(Screenshot.is_unproductive.is_(True))
            .filter(Screenshot.ts >= start, Screenshot.ts < end)
            .order_by(Screenshot.ts.desc())
            .limit(8)
        ).scalars()
    )
    if team_id:
        member_ids = {u.id for u in users}
        flagged_shots = [s for s in flagged_shots if s.user_id in member_ids]

    total_desktime = team["active"]
    total_idle = team["idle"]
    total_all = max(total_desktime + total_idle, 1.0)

    return render_template(
        "admin.html",
        **_day_nav(day),
        rows=rows,
        team=team,
        avg_prod=avg_prod,
        avg_eff=avg_eff,
        top_productive=top_productive,
        top_effective=top_effective,
        top_unproductive=top_unproductive,
        top_late=top_late,
        team_apps=team_apps,
        flagged_shots=flagged_shots,
        expected_hours=expected / 3600.0,
        humanize=humanize,
        employee_count=len(users),
        off_day=off_day,
        week=_week_strip(None, day),
        teams=teams,
        team_id=team_id,
        split_prod=round(100.0 * team["productive"] / total_all, 1),
        split_unprod=round(100.0 * team["unproductive"] / total_all, 1),
        split_neutral=round(
            100.0
            * max(0.0, team["active"] - team["productive"] - team["unproductive"])
            / total_all,
            1,
        ),
        split_idle=round(100.0 * team["idle"] / total_all, 1),
        total_desktime=team["active"],
        total_productive=team["productive"],
        total_unproductive=team["unproductive"],
        total_idle=team["idle"],
        online_window=int(cfg.online_window),
    )


@views_bp.route("/admin/user/<int:user_id>")
@admin_required
def admin_user(user_id: int):
    user = db.session.get(User, user_id) or abort(404)
    day = _parse_day(request.args.get("day"))
    return render_template(
        "user.html", **_employee_day_context(user, day, is_self=False)
    )


@views_bp.route("/team")
@admin_required
def team_members():
    day = _parse_day(request.args.get("day"))
    return redirect(url_for("views.admin", day=day.strftime("%Y-%m-%d")))


def _rules_path() -> str:
    return rules_path()


def _load_server_rules() -> dict[str, list[str]]:
    return load_server_rules()


def _tracked_for_categories(days: int = 14, limit: int = 60) -> tuple[list[dict], list[dict]]:
    """Aggregate recent apps and domains so admins can assign categories quickly."""
    from ..monitor import extract_domain, extract_url

    cutoff = datetime.now().timestamp() - days * 86400.0
    rows = list(
        db.session.execute(
            db.select(Activity)
            .filter(Activity.start_ts >= cutoff, Activity.idle.is_(False))
            .order_by(Activity.start_ts.desc())
            .limit(8000)
        ).scalars()
    )
    categorizer = RulesConfig(rules=merge_rules(_load_server_rules()))
    apps: dict[str, dict] = {}
    sites: dict[str, dict] = {}
    for a in rows:
        app_name = (a.app or "").strip() or "unknown"
        key = app_name.lower()
        slot = apps.setdefault(
            key,
            {"name": app_name, "seconds": 0.0, "category": NEUTRAL, "kind": "app"},
        )
        slot["seconds"] += float(a.duration or 0)
        url = (getattr(a, "url", "") or "").strip() or extract_url(a.app, a.title)
        domain = extract_domain(url)
        if domain:
            dslot = sites.setdefault(
                domain.lower(),
                {"name": domain.lower(), "seconds": 0.0, "category": NEUTRAL, "kind": "site"},
            )
            dslot["seconds"] += float(a.duration or 0)

    for slot in apps.values():
        slot["category"] = categorizer.categorize(slot["name"], "", "")
        slot["label"] = humanize(slot["seconds"])
    for slot in sites.values():
        slot["category"] = categorizer.categorize("browser", slot["name"], f"https://{slot['name']}")
        slot["label"] = humanize(slot["seconds"])

    ranked_apps = sorted(apps.values(), key=lambda x: x["seconds"], reverse=True)[:limit]
    ranked_sites = sorted(sites.values(), key=lambda x: x["seconds"], reverse=True)[:limit]
    return ranked_apps, ranked_sites


@views_bp.route("/settings/rules", methods=["GET", "POST"])
@admin_required
def settings_rules():
    if request.method == "POST":
        def _lines(name: str) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for x in (request.form.get(name) or "").splitlines():
                item = x.strip()
                if not item:
                    continue
                key = item.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
            return out

        payload = {
            PRODUCTIVE: _lines("productive"),
            UNPRODUCTIVE: _lines("unproductive"),
            "neutral": _lines("neutral"),
            SITES_PRODUCTIVE: _lines("sites_productive"),
            SITES_UNPRODUCTIVE: _lines("sites_unproductive"),
        }
        path = _rules_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        flash(
            "Category rules saved. Agents sync them automatically within ~30 seconds.",
            "info",
        )
        return redirect(url_for("views.settings_rules"))

    stored = _load_server_rules()
    merged = merge_rules(stored)
    # Show effective lists (defaults + custom) so admins see what actually applies.
    display = {
        PRODUCTIVE: stored.get(PRODUCTIVE) or merged.get(PRODUCTIVE, []),
        UNPRODUCTIVE: stored.get(UNPRODUCTIVE) or merged.get(UNPRODUCTIVE, []),
        "neutral": stored.get("neutral") or [],
        SITES_PRODUCTIVE: stored.get(SITES_PRODUCTIVE)
        or merged.get(SITES_PRODUCTIVE, []),
        SITES_UNPRODUCTIVE: stored.get(SITES_UNPRODUCTIVE)
        or merged.get(SITES_UNPRODUCTIVE, []),
    }
    tracked_apps, tracked_sites = _tracked_for_categories()
    counts = {
        "apps": len(tracked_apps),
        "sites": len(tracked_sites),
        "productive": sum(1 for x in tracked_apps + tracked_sites if x["category"] == PRODUCTIVE),
        "unproductive": sum(
            1 for x in tracked_apps + tracked_sites if x["category"] == UNPRODUCTIVE
        ),
        "neutral": sum(1 for x in tracked_apps + tracked_sites if x["category"] == NEUTRAL),
        "keywords": sum(len(v) for v in display.values()),
    }
    return render_template(
        "settings_rules.html",
        rules=display,
        defaults=DEFAULT_RULES,
        site_defaults=DEFAULT_SITE_RULES,
        merged=merged,
        tracked_apps=tracked_apps,
        tracked_sites=tracked_sites,
        counts=counts,
    )


# ----- Employees (admin) -----


@views_bp.route("/employees", methods=["GET", "POST"])
@admin_required
def employees():
    if request.method == "POST":
        from .tenancy import current_org_id

        action = request.form.get("action") or "create"
        if action == "create":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip()
            role = ROLE_ADMIN if request.form.get("admin") else ROLE_EMPLOYEE
            if not username or not password:
                flash("Username and password are required.", "error")
            elif db.session.execute(
                db.select(User).filter_by(username=username)
            ).scalar_one_or_none():
                flash(f"User {username!r} already exists.", "error")
            else:
                user = User(
                    username=username,
                    display_name=name,
                    email=email,
                    role=role,
                    api_token=generate_token(),
                    organization_id=current_org_id(),
                )
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                flash(
                    f"Created {user.role} {user.username}. API token: {user.api_token}",
                    "info",
                )
        elif action == "toggle":
            uid = request.form.get("user_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            if user.id == current_user.id:
                flash("You cannot disable your own account.", "error")
            else:
                user.enabled = not user.enabled
                db.session.commit()
                flash(
                    f"{user.username} is now {'enabled' if user.enabled else 'disabled'}.",
                    "info",
                )
        elif action == "rotate":
            uid = request.form.get("user_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            token = user.rotate_token()
            db.session.commit()
            flash(f"New API token for {user.username}: {token}", "info")
        elif action == "password":
            uid = request.form.get("user_id", type=int)
            password = request.form.get("password") or ""
            user = db.session.get(User, uid) or abort(404)
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
            else:
                user.set_password(password)
                db.session.commit()
                flash(f"Password updated for {user.username}.", "info")
        elif action == "private":
            uid = request.form.get("user_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            user.private_time_allowed = not user.private_time_allowed
            db.session.commit()
            flash(
                f"Private Time {'allowed' if user.private_time_allowed else 'disabled'} for {user.username}.",
                "info",
            )
        elif action == "team":
            from .models import Team

            uid = request.form.get("user_id", type=int)
            tid = request.form.get("team_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            if tid:
                team = db.session.get(Team, tid)
                if team is None:
                    abort(404)
            user.team_id = tid or None
            db.session.commit()
            flash(f"Team updated for {user.username}.", "info")
        elif action == "edit":
            uid = request.form.get("user_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip()
            user.display_name = name
            user.email = email
            db.session.commit()
            flash(f"Profile updated for {user.username}.", "info")
        elif action == "role":
            uid = request.form.get("user_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            if user.id == current_user.id:
                flash("You cannot change your own role.", "error")
            else:
                user.role = (
                    ROLE_EMPLOYEE if user.role == ROLE_ADMIN else ROLE_ADMIN
                )
                db.session.commit()
                flash(f"{user.username} is now {user.role}.", "info")
        elif action == "screenshots":
            uid = request.form.get("user_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            mode = request.form.get("shot_mode") or "company"
            if mode == "company":
                user.screenshots_enabled = None
                user.screenshot_interval = None
            elif mode == "off":
                user.screenshots_enabled = False
                user.screenshot_interval = None
            else:
                user.screenshots_enabled = True
                interval = request.form.get("interval", type=int) or 300
                user.screenshot_interval = max(60, min(interval, 3600))
            db.session.commit()
            flash(f"Screenshot policy updated for {user.username}.", "info")
        elif action == "delete":
            from .tenancy import current_org_id
            from .users_util import delete_user_account

            uid = request.form.get("user_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            if user.id == current_user.id:
                flash("You cannot delete your own account.", "error")
            elif user.organization_id != current_org_id():
                flash("Employee not found in your organization.", "error")
            elif user.role == ROLE_ADMIN:
                admins = (
                    db.session.execute(
                        db.select(db.func.count(User.id)).filter(
                            User.role == ROLE_ADMIN,
                            User.organization_id == current_org_id(),
                            User.enabled.is_(True),
                        )
                    ).scalar_one()
                    or 0
                )
                if admins <= 1:
                    flash("Cannot delete the last active admin.", "error")
                else:
                    delete_user_account(user)
                    flash(f"Deleted user {user.username}.", "info")
            else:
                delete_user_account(user)
                flash(f"Deleted user {user.username}.", "info")
        return redirect(url_for("views.employees"))

    from .models import Team
    from .tenancy import current_org_id, users_in_org_query

    q = request.args.get("q", "").strip().lower()
    users = list(
        db.session.execute(
            users_in_org_query(current_org_id()).order_by(User.role.desc(), User.username)
        ).scalars()
    )
    if q:
        users = [
            u
            for u in users
            if q in u.username.lower()
            or q in (u.display_name or "").lower()
            or q in (u.email or "").lower()
        ]
    teams = list(db.session.execute(db.select(Team).order_by(Team.name)).scalars())
    now_ts = datetime.now().timestamp()
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    rows = []
    for u in users:
        last = _last_seen(u.id)
        rows.append(
            {
                "user": u,
                "online": last is not None and (now_ts - last) <= cfg.online_window,
                "last_seen": last,
            }
        )
    return render_template(
        "employees.html", users=users, teams=teams, rows=rows, q=q, humanize=humanize
    )


# ----- Screenshots browser (DeskTime gallery) -----


@views_bp.route("/screenshots")
@login_required
def screenshots_browser():
    if not current_user.is_admin:
        flash("Screenshots are for admins only.", "info")
        return redirect(url_for("views.me"))
    day = _parse_day(request.args.get("day"))
    start, end = day_bounds(day, tz_name=_tz())
    only_bad = request.args.get("flagged") == "1"
    user_id = request.args.get("user_id", type=int)

    if current_user.is_admin:
        from .tenancy import users_in_org_query

        users = list(
            db.session.execute(
                users_in_org_query(current_user.organization_id or 1)
                .filter(User.role != ROLE_ADMIN)
                .order_by(User.username)
            ).scalars()
        )
        if user_id is None and users:
            # default: all users for the day
            pass
    else:
        users = [current_user]
        user_id = current_user.id

    q = db.select(Screenshot).filter(Screenshot.ts >= start, Screenshot.ts < end)
    if user_id:
        if user_id != current_user.id and not current_user.is_admin:
            abort(403)
        q = q.filter(Screenshot.user_id == user_id)
    elif not current_user.is_admin:
        q = q.filter(Screenshot.user_id == current_user.id)
    if only_bad:
        q = q.filter(Screenshot.is_unproductive.is_(True))
    q = q.order_by(Screenshot.ts.desc()).limit(120)
    shots = list(db.session.execute(q).scalars())

    return render_template(
        "screenshots.html",
        shots=shots,
        users=users,
        selected_user=user_id,
        flagged=only_bad,
        **_day_nav(day),
    )


# ----- Projects + manual timer -----


@views_bp.route("/projects", methods=["GET", "POST"])
@login_required
def projects():
    if not current_user.is_admin:
        flash("Projects are for admins only.", "info")
        return redirect(url_for("views.me"))
    if request.method == "POST":
        action = request.form.get("action") or "add_time"
        if action == "create_project" and current_user.is_admin:
            name = (request.form.get("name") or "").strip()
            description = (request.form.get("description") or "").strip()
            color = (request.form.get("color") or "#0d9488").strip()
            if not name:
                flash("Project name is required.", "error")
            elif db.session.execute(
                db.select(Project)
                .filter_by(name=name, organization_id=current_user.organization_id or 1)
            ).scalar_one_or_none():
                flash("A project with that name already exists.", "error")
            else:
                db.session.add(
                    Project(
                        name=name,
                        description=description,
                        color=color,
                        organization_id=current_user.organization_id or 1,
                    )
                )
                db.session.commit()
                flash(f"Project {name!r} created.", "info")
        elif action == "create_task" and current_user.is_admin:
            from .models import Task

            project_id = request.form.get("project_id", type=int)
            tname = (request.form.get("task_name") or "").strip()
            if not project_id or not tname:
                flash("Project and task name required.", "error")
            else:
                db.session.add(Task(project_id=project_id, name=tname))
                db.session.commit()
                flash(f"Task {tname!r} added.", "info")
        elif action == "add_time":
            project_id = request.form.get("project_id", type=int)
            minutes = request.form.get("minutes", type=float) or 0
            note = (request.form.get("note") or "").strip()
            if minutes <= 0:
                flash("Enter minutes greater than 0.", "error")
            else:
                now = datetime.now().timestamp()
                duration = minutes * 60.0
                entry = ManualEntry(
                    user_id=current_user.id,
                    project_id=project_id or None,
                    note=note,
                    start_ts=now - duration,
                    end_ts=now,
                    duration=duration,
                )
                db.session.add(entry)
                db.session.commit()
                flash(f"Logged {humanize(duration)} to project.", "info")
        elif action == "toggle_project" and current_user.is_admin:
            pid = request.form.get("project_id", type=int)
            project = db.session.get(Project, pid) or abort(404)
            project.active = not project.active
            db.session.commit()
            flash(
                f"Project {project.name!r} is now {'active' if project.active else 'archived'}.",
                "info",
            )
        return redirect(url_for("views.projects"))

    from .tenancy import projects_in_org_query

    projects_list = list(
        db.session.execute(
            projects_in_org_query(current_user.organization_id or 1).order_by(
                Project.active.desc(), Project.name
            )
        ).scalars()
    )
    start, end = day_bounds(datetime.now())
    q = db.select(ManualEntry).filter(
        ManualEntry.start_ts >= start, ManualEntry.start_ts < end
    )
    if not current_user.is_admin:
        q = q.filter(ManualEntry.user_id == current_user.id)
    today_entries = list(
        db.session.execute(q.order_by(ManualEntry.start_ts.desc())).scalars()
    )
    # totals per project today
    totals: dict[int | None, float] = {}
    for e in today_entries:
        totals[e.project_id] = totals.get(e.project_id, 0.0) + e.duration
    return render_template(
        "projects.html",
        projects=projects_list,
        today_entries=today_entries,
        totals=totals,
        humanize=humanize,
    )


@views_bp.route("/reports")
@login_required
def reports():
    if not current_user.is_admin:
        flash("Reports are for admins only.", "info")
        return redirect(url_for("views.me"))
    day = _parse_day(request.args.get("day"))
    scope = request.args.get("scope") or "me"
    selected_user = request.args.get("user_id", type=int)
    expected = _expected_seconds()
    try:
        range_days = int(request.args.get("range") or 7)
    except (TypeError, ValueError):
        range_days = 7
    if range_days not in (7, 14, 30):
        range_days = 7

    days = []
    for i in range(range_days - 1, -1, -1):
        d = day - timedelta(days=i)
        if current_user.is_admin and scope == "team":
            from .tenancy import users_in_org_query

            users_q = list(
                db.session.execute(
                    users_in_org_query(current_user.organization_id or 1)
                    .filter(User.role != ROLE_ADMIN)
                    .filter(User.enabled.is_(True))
                ).scalars()
            )
            s_list = [_summarize_user_day(u.id, d) for u in users_q]
            active = sum(s.active_seconds for s in s_list)
            productive = sum(s.productive_seconds for s in s_list)
            idle = sum(s.idle_seconds for s in s_list)
            unproductive = sum(s.unproductive_seconds for s in s_list)
            present = sum(1 for s in s_list if s.total_seconds > 0)
            late = sum(
                1
                for s in s_list
                if s.total_seconds > 0 and _is_late(s.arrival_ts, d)
            )
            tracked_n = max(1, sum(1 for s in s_list if s.active_seconds))
            pct = (
                round(
                    sum(s.desktime_productivity_pct for s in s_list if s.active_seconds)
                    / tracked_n,
                    1,
                )
                if any(s.active_seconds for s in s_list)
                else 0.0
            )
            eff = (
                round(
                    sum(
                        s.desktime_effectiveness_pct(expected)
                        for s in s_list
                        if s.active_seconds
                    )
                    / tracked_n,
                    1,
                )
                if any(s.active_seconds for s in s_list)
                else 0.0
            )
            row = {
                "day": d.strftime("%Y-%m-%d"),
                "label": d.strftime("%a %d"),
                "active": active,
                "productive": productive,
                "idle": idle,
                "unproductive": unproductive,
                "pct": pct,
                "eff": eff,
                "present": present,
                "late": late,
                "absent": max(0, len(users_q) - present),
            }
        else:
            uid = current_user.id
            if current_user.is_admin and selected_user:
                uid = selected_user
            s = _summarize_user_day(uid, d)
            row = {
                "day": d.strftime("%Y-%m-%d"),
                "label": d.strftime("%a %d"),
                "active": s.active_seconds,
                "productive": s.productive_seconds,
                "idle": s.idle_seconds,
                "unproductive": s.unproductive_seconds,
                "pct": s.desktime_productivity_pct,
                "eff": s.desktime_effectiveness_pct(expected),
                "present": 1 if s.total_seconds > 0 else 0,
                "late": 1 if s.total_seconds > 0 and _is_late(s.arrival_ts, d) else 0,
                "absent": 0 if s.total_seconds > 0 else 1,
            }
        days.append(row)

    max_pct = max((d["pct"] for d in days), default=1.0) or 1.0
    week_productive = sum(d["productive"] for d in days)
    week_active = sum(d["active"] for d in days)
    week_idle = sum(d["idle"] for d in days)
    avg_prod = (
        round(sum(d["pct"] for d in days if d["active"]) / max(1, sum(1 for d in days if d["active"])), 1)
        if any(d["active"] for d in days)
        else 0.0
    )
    avg_eff = (
        round(sum(d["eff"] for d in days if d["active"]) / max(1, sum(1 for d in days if d["active"])), 1)
        if any(d["active"] for d in days)
        else 0.0
    )

    app_totals: dict[str, dict] = {}
    for i in range(range_days - 1, -1, -1):
        d = day - timedelta(days=i)
        if current_user.is_admin and scope == "team":
            from .tenancy import users_in_org_query

            ids = [
                u.id
                for u in db.session.execute(
                    users_in_org_query(current_user.organization_id or 1)
                    .filter(User.role != ROLE_ADMIN)
                    .filter(User.enabled.is_(True))
                ).scalars()
            ]
        else:
            uid = selected_user if (current_user.is_admin and selected_user) else current_user.id
            ids = [uid]
        for uid in ids:
            for a in _summarize_user_day(uid, d).apps:
                slot = app_totals.setdefault(
                    a.app, {"app": a.app, "category": a.category, "seconds": 0.0}
                )
                slot["seconds"] += a.seconds
                if slot["category"] == "neutral" and a.category != "neutral":
                    slot["category"] = a.category
    top_apps = sorted(app_totals.values(), key=lambda x: x["seconds"], reverse=True)[:12]
    base = sum(a["seconds"] for a in top_apps) or 1.0
    for a in top_apps:
        a["pct"] = round(100.0 * a["seconds"] / base, 1)

    users = []
    if current_user.is_admin:
        from .tenancy import users_in_org_query

        users = list(
            db.session.execute(
                users_in_org_query(current_user.organization_id or 1)
                .filter(User.role != ROLE_ADMIN)
                .order_by(User.username)
            ).scalars()
        )

    export_uid = selected_user or current_user.id
    return render_template(
        "reports.html",
        days=days,
        max_pct=max_pct,
        **_day_nav(day),
        humanize=humanize,
        users=users,
        scope=scope,
        selected_user=selected_user,
        week_productive=week_productive,
        week_active=week_active,
        week_idle=week_idle,
        avg_prod=avg_prod,
        avg_eff=avg_eff,
        top_apps=top_apps,
        export_uid=export_uid,
        expected_hours=expected / 3600.0,
        range_days=range_days,
    )


@views_bp.route("/data/charts")
@login_required
def data_charts():
    user_id = request.args.get("user_id", type=int) or current_user.id
    if user_id != current_user.id and not current_user.is_admin:
        abort(403)
    day = _parse_day(request.args.get("day"))
    return jsonify(_chart_payload(_activities_for(user_id, day), day))


def _serve_shot(shot_id: int, thumb: bool):
    shot = db.session.get(Screenshot, shot_id) or abort(404)
    if shot.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    rel = shot.thumb_path if (thumb and shot.thumb_path) else shot.path
    directory = os.path.abspath(cfg.screenshots_dir)
    return send_from_directory(directory, rel)


@views_bp.route("/screenshot/<int:shot_id>")
@login_required
def screenshot(shot_id: int):
    return _serve_shot(shot_id, thumb=False)


@views_bp.route("/thumb/<int:shot_id>")
@login_required
def thumb(shot_id: int):
    return _serve_shot(shot_id, thumb=True)


@views_bp.app_template_filter("clock")
def _clock(ts: float | None) -> str:
    return format_clock(ts, tz_name=_tz())


@views_bp.app_template_filter("ago")
def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    delta = max(0, int(datetime.now().timestamp() - ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


__all__ = ["views_bp"]
