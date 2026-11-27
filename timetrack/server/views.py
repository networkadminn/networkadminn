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
    g,
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
    compute_day_metrics,
    day_bounds,
    format_clock,
    humanize,
    idle_gap_list,
    merge_bar_segments,
    merge_nearby_gaps,
    productivity_bar_segments,
    bar_view_hours,
    summarize,
    annotate_pending_offline_requests,
    annotate_rejected_offline_requests,
    reconcile_fillable_gap_bounds,
    suppress_covered_gaps,
    timeline_buckets,
    top_sites,
    website_report,
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
    PrivatePeriod,
    Project,
    Screenshot,
    User,
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    generate_token,
)
from .settings_util import (
    active_tz_name,
    get_settings,
    load_server_rules,
    rules_path,
    user_tz,
    user_tz_name,
    valid_tz_name,
)

views_bp = Blueprint("views", __name__)


def _tz() -> str:
    return active_tz_name()


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


def _day_metrics(
    user_id: int,
    day: datetime,
    *,
    acts: list | None = None,
    manual: list | None = None,
):
    start, end = day_bounds(day, tz_name=_tz())
    if acts is None:
        acts = _activities_for(user_id, day)
    if manual is None:
        manual = _manual_for(user_id, day)
    return compute_day_metrics(acts, manual, start, end, _expected_seconds())


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
                dm = _day_metrics(u.id, d)
                if dm.desktime_seconds > 0:
                    prods.append(dm.dt_productivity)
            pct = round(sum(prods) / len(prods), 1) if prods else 0.0
            seconds = sum(_day_metrics(u.id, d).desktime_seconds for u in users)
        else:
            dm = _day_metrics(user_id, d)
            pct = dm.dt_productivity
            seconds = dm.summary.active_seconds
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


def _team_trend_days(users: list[User], day: datetime, range_days: int = 7) -> list[dict]:
    """Daily team aggregates for admin visual dashboard."""
    expected = _expected_seconds()
    days: list[dict] = []
    for i in range(range_days - 1, -1, -1):
        d = day - timedelta(days=i)
        dm_list = [_day_metrics(u.id, d) for u in users]
        active = sum(dm.summary.active_seconds for dm in dm_list)
        productive = sum(dm.summary.productive_seconds for dm in dm_list)
        idle = sum(dm.summary.idle_seconds for dm in dm_list)
        unproductive = sum(dm.summary.unproductive_seconds for dm in dm_list)
        present = sum(1 for dm in dm_list if dm.summary.total_seconds > 0)
        tracked_n = max(1, sum(1 for dm in dm_list if dm.desktime_seconds > 0))
        pct = (
            round(
                sum(dm.dt_productivity for dm in dm_list if dm.desktime_seconds > 0)
                / tracked_n,
                1,
            )
            if any(dm.desktime_seconds > 0 for dm in dm_list)
            else 0.0
        )
        eff = (
            round(
                sum(
                    dm.dt_effectiveness
                    for dm in dm_list
                    if dm.desktime_seconds > 0
                )
                / tracked_n,
                1,
            )
            if any(dm.desktime_seconds > 0 for dm in dm_list)
            else 0.0
        )
        total_tracked = max(active + idle, 1.0)
        neutral = max(0.0, active - productive - unproductive)
        days.append(
            {
                "day": d.strftime("%Y-%m-%d"),
                "label": d.strftime("%a"),
                "label_full": d.strftime("%a %d"),
                "active": active,
                "productive": productive,
                "idle": idle,
                "unproductive": unproductive,
                "present": present,
                "pct": pct,
                "eff": eff,
                "stack_prod": round(100.0 * productive / total_tracked, 1),
                "stack_unprod": round(100.0 * unproductive / total_tracked, 1),
                "stack_neutral": round(100.0 * neutral / total_tracked, 1),
                "stack_idle": round(100.0 * idle / total_tracked, 1),
            }
        )
    return days


def _category_conic_gradient(segments: list[dict]) -> str:
    """CSS conic-gradient stops for time-split donut."""
    colors = {
        "productive": "#10B981",
        "neutral": "#8B9AAB",
        "unproductive": "#F43F5E",
        "idle": "#C4D0DB",
    }
    cum = 0.0
    parts: list[str] = []
    for seg in segments:
        pct = float(seg.get("pct") or 0)
        if pct <= 0:
            continue
        end = min(100.0, cum + pct)
        color = colors.get(seg.get("class") or "", "#94A3B8")
        parts.append(f"{color} {cum}% {end}%")
        cum = end
    if not parts:
        return "conic-gradient(#E2E8F0 0 100%)"
    return f"conic-gradient({', '.join(parts)})"


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


def _active_private_map(user_ids: list[int]) -> dict[int, PrivatePeriod]:
    """Users with Private Time currently ON (active period row)."""
    if not user_ids:
        return {}
    periods = list(
        db.session.execute(
            db.select(PrivatePeriod).filter(
                PrivatePeriod.user_id.in_(user_ids),
                PrivatePeriod.active.is_(True),
            )
        ).scalars()
    )
    return {p.user_id: p for p in periods}


def _private_duration_label(start_ts: float, now_ts: float) -> str:
    """Human duration for admin dashboard (caps silly multi-day stale actives)."""
    ago_s = max(0, int(now_ts - start_ts))
    if ago_s < 60:
        return f"{ago_s}s"
    if ago_s < 3600:
        return f"{ago_s // 60}m"
    if ago_s < 86400:
        return f"{ago_s // 3600}h {(ago_s % 3600) // 60}m"
    days = ago_s // 86400
    if days > 14:
        return f"{days}d+"
    return f"{days}d {(ago_s % 86400) // 3600}h"


def _admin_team_row(
    u: User,
    day: datetime,
    expected: float,
    off_day: bool,
    now_ts: float,
    online_window: float,
    private_period: PrivatePeriod | None = None,
    *,
    acts: list[Activity] | None = None,
    last_seen: float | None = None,
    shot_count: int | None = None,
    live: dict | None = None,
) -> dict:
    """One employee's row on the team board, in the active (employee's) zone."""
    start, end = day_bounds(day, tz_name=_tz())
    if acts is None:
        acts = _activities_for(u.id, day)
    manual = _manual_for(u.id, day)
    dm = compute_day_metrics(acts, manual, start, end, expected)
    s = dm.summary
    manual_seconds = dm.manual_total_seconds
    last = last_seen if last_seen is not None else _last_seen(u.id)
    # Prefer day's last activity end when it is newer (avoids a second full-table scan).
    if acts:
        day_last = float(acts[-1].end_ts)
        last = max(float(last or 0.0), day_last) or None
    online = _is_online(u, last, now_ts, online_window)
    late = _is_late(s.arrival_ts, day)
    absent = (not off_day) and s.total_seconds <= 0 and manual_seconds <= 0
    if live is None:
        live = _live_snapshot(u.id, now_ts=now_ts, online_window=online_window)
    if not online and isinstance(live, dict):
        live["fresh"] = False
    private_active = private_period is not None
    stale_private = False
    if private_period:
        stale_private = (now_ts - private_period.start_ts) > 86400 * 2
    if shot_count is None:
        shot_count = db.session.execute(
            db.select(db.func.count(Screenshot.id)).filter(
                Screenshot.user_id == u.id,
                Screenshot.ts >= start,
                Screenshot.ts < end,
            )
        ).scalar_one()
    return {
        "user": u,
        "private_active": private_active,
        "private_stale": stale_private,
        "private_started": format_clock(private_period.start_ts, tz_name=_tz())
        if private_period
        else "",
        "private_ago": _private_duration_label(private_period.start_ts, now_ts)
        if private_period
        else "",
        "private_start_ts": float(private_period.start_ts) if private_period else 0.0,
        "summary": s,
        "manual_seconds": manual_seconds,
        "desktime_seconds": dm.desktime_seconds,
        "online": online,
        "last_seen": last,
        "live": live,
        "shots": int(shot_count or 0),
        "_acts": acts,
        "arrived": format_clock(s.arrival_ts, tz_name=_tz()),
        "left": format_clock(s.last_seen_ts, tz_name=_tz()),
        "late": late and not absent,
        "early": _is_early(s.arrival_ts, day) and not absent,
        "absent": absent,
        "dt_productivity": dm.dt_productivity,
        "dt_effectiveness": dm.dt_effectiveness,
    }


def _admin_team_day_state(
    users: list[User],
    day: datetime,
    *,
    now_ts: float,
    online_window: float,
) -> tuple[list[dict], dict, list[dict], float, float, bool]:
    """Per-day team rows, aggregates, and top apps (admin dashboard + reports viz)."""
    expected = _expected_seconds()
    off_day = _is_off_day(day)
    rows: list[dict] = []
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
    user_ids = [u.id for u in users]
    priv_map = _active_private_map(user_ids)
    start, end = day_bounds(day, tz_name=_tz())
    shot_counts: dict[int, int] = {}
    last_map: dict[int, float] = {}
    if user_ids:
        shot_counts = {
            int(uid): int(n)
            for uid, n in db.session.execute(
                db.select(Screenshot.user_id, db.func.count(Screenshot.id))
                .filter(
                    Screenshot.user_id.in_(user_ids),
                    Screenshot.ts >= start,
                    Screenshot.ts < end,
                )
                .group_by(Screenshot.user_id)
            ).all()
        }
        last_map = {
            int(uid): float(ts)
            for uid, ts in db.session.execute(
                db.select(Activity.user_id, db.func.max(Activity.end_ts))
                .filter(Activity.user_id.in_(user_ids))
                .group_by(Activity.user_id)
            ).all()
            if ts is not None
        }
    for u in users:
        with user_tz(u):
            acts = _activities_for(u.id, day)
            live = None
            # Only hit latest-activity when this person looks online.
            tentative_last = last_map.get(u.id)
            if acts:
                tentative_last = max(float(tentative_last or 0.0), float(acts[-1].end_ts))
            if _is_online(u, tentative_last, now_ts, online_window):
                live = _live_snapshot(u.id, now_ts=now_ts, online_window=online_window)
            else:
                live = {
                    "app": "—",
                    "title": "",
                    "domain": "",
                    "category": "neutral",
                    "idle": False,
                    "ago": "offline",
                    "end_ts": tentative_last,
                    "fresh": False,
                }
            row = _admin_team_row(
                u,
                day,
                expected,
                off_day,
                now_ts,
                online_window,
                private_period=priv_map.get(u.id),
                acts=acts,
                last_seen=last_map.get(u.id),
                shot_count=shot_counts.get(u.id, 0),
                live=live,
            )
        rows.append(row)
        s = row["summary"]
        team["active"] += s.active_seconds
        team["productive"] += s.productive_seconds
        team["idle"] += s.idle_seconds
        team["unproductive"] += s.unproductive_seconds
        team["online"] += 1 if row["online"] else 0
        if row["absent"]:
            team["absent"] += 1
        else:
            team["present"] += 1
            if row["late"]:
                team["late"] += 1

    app_totals: dict[str, dict] = {}
    for r in rows:
        for a in r["summary"].apps:
            slot = app_totals.setdefault(
                a.app, {"app": a.app, "category": a.category, "seconds": 0.0}
            )
            slot["seconds"] += a.seconds
            if slot["category"] == "neutral" and a.category != "neutral":
                slot["category"] = a.category
    team_apps = sorted(app_totals.values(), key=lambda x: x["seconds"], reverse=True)[:12]
    active_base = team["active"] or 1.0
    for a in team_apps:
        a["pct"] = round(100.0 * a["seconds"] / active_base, 1)

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
    return rows, team, team_apps, avg_prod, avg_eff, off_day


def _team_hourly_bars(
    users: list[User],
    day: datetime,
    *,
    preloaded_acts: list | None = None,
) -> tuple[list[dict], bool]:
    """Hourly team productivity columns for the admin dashboard bar."""
    settings = get_settings()
    start, end = day_bounds(day, tz_name=_tz())
    office_start_h = float(
        settings.office_start_hour or settings.expected_arrival_hour or 9.0
    )
    office_end_h = float(settings.office_end_hour or 18.0)
    view_start = start + office_start_h * 3600.0
    view_end = min(end, start + office_end_h * 3600.0)
    if view_end <= view_start:
        view_start, view_end = start, end

    if preloaded_acts is not None:
        acts = list(preloaded_acts)
    else:
        acts = []
        for u in users:
            acts.extend(_activities_for(u.id, day))

    h0 = max(0, int(office_start_h))
    h1 = max(h0, int(math.ceil(office_end_h)) - 1)

    if not acts:
        out = []
        for h in range(h0, h1 + 1):
            short = format_hour_short(float(h))
            ampm = "AM" if h < 12 else "PM"
            out.append(
                {
                    "label": f"{h:02d}:00",
                    "short": short,
                    "ampm": ampm,
                    "pct": 0.0,
                    "active": False,
                }
            )
        return out, False

    buckets = timeline_buckets(acts, view_start, view_end, bucket_seconds=3600.0)
    tz = zone(_tz())
    out: list[dict] = []
    any_active = False
    for b in buckets:
        prod = float(b.get(PRODUCTIVE, 0))
        unprod = float(b.get(UNPRODUCTIVE, 0))
        neut = float(b.get(NEUTRAL, 0))
        tracked = prod + unprod + neut
        idle = float(b.get("idle", 0))
        active = tracked + idle > 30
        any_active = any_active or active
        pct = round(100.0 * prod / tracked, 1) if tracked > 0 else 0.0
        dt = datetime.fromtimestamp(b["start"], tz=tz)
        h = dt.hour
        out.append(
            {
                "label": dt.strftime("%H:%M"),
                "short": format_hour_short(float(h) + dt.minute / 60.0),
                "ampm": "AM" if h < 12 else "PM",
                "pct": pct,
                "active": active,
            }
        )
    if not out:
        for h in range(h0, h1 + 1):
            short = format_hour_short(float(h))
            ampm = "AM" if h < 12 else "PM"
            out.append(
                {
                    "label": f"{h:02d}:00",
                    "short": short,
                    "ampm": ampm,
                    "pct": 0.0,
                    "active": False,
                }
            )
        return out, False
    return out, any_active


def _team_visual_viz_context(
    *,
    users: list[User],
    rows: list[dict],
    team: dict,
    team_apps: list[dict],
    day: datetime,
    avg_prod: float,
    avg_eff: float,
    off_day: bool,
    expected_hours: float,
    trend_range: int = 7,
    team_id: int | None = None,
    viz_day_query: str = "",
    viz_apps_anchor: str = "#team-apps",
    viz_emp_anchor: str = "#team-table",
) -> dict:
    """Template context for _admin_visual_grid.html (team analytics charts)."""
    day_str = day.strftime("%Y-%m-%d")
    trend_days = _team_trend_days(users, day, trend_range)
    trend_max_pct = max((d["pct"] for d in trend_days), default=1.0) or 1.0
    trend_max_active = max((d["active"] for d in trend_days), default=1.0) or 1.0
    prod_spark_line, prod_spark_area = _spark_polyline(
        [d["pct"] for d in trend_days], width=240.0, height=56.0
    )
    active_norm = [
        round(100.0 * d["active"] / trend_max_active, 1) for d in trend_days
    ]
    active_spark_line, active_spark_area = _spark_polyline(
        active_norm, width=240.0, height=56.0
    )
    eff_spark_line, eff_spark_area = _spark_polyline(
        [d["eff"] for d in trend_days], width=240.0, height=56.0
    )
    present_max = max((d["present"] for d in trend_days), default=1) or 1
    present_norm = [
        round(100.0 * d["present"] / present_max, 1) for d in trend_days
    ]
    present_spark_line, present_spark_area = _spark_polyline(
        present_norm, width=240.0, height=56.0
    )
    trend_stack_max = max(
        (d["active"] + d["idle"] for d in trend_days), default=1.0
    ) or 1.0
    emp_n = max(len(users), 1)
    attendance_viz = [
        {
            "key": "online",
            "label": "Online",
            "n": team["online"],
            "pct": round(100.0 * team["online"] / emp_n, 1),
        },
        {
            "key": "present",
            "label": "Present",
            "n": team["present"],
            "pct": round(100.0 * team["present"] / emp_n, 1),
        },
        {
            "key": "late",
            "label": "Late",
            "n": team["late"],
            "pct": round(100.0 * team["late"] / emp_n, 1),
        },
        {
            "key": "absent",
            "label": "Absent",
            "n": team["absent"],
            "pct": round(100.0 * team["absent"] / emp_n, 1),
        },
    ]
    neutral_seconds = max(
        0.0, team["active"] - team["productive"] - team["unproductive"]
    )
    cat_total = max(team["active"] + team["idle"], 1.0)
    category_viz = [
        {
            "label": "Productive",
            "class": "productive",
            "seconds": team["productive"],
            "pct": round(100.0 * team["productive"] / cat_total, 1),
        },
        {
            "label": "Neutral",
            "class": "neutral",
            "seconds": neutral_seconds,
            "pct": round(100.0 * neutral_seconds / cat_total, 1),
        },
        {
            "label": "Unproductive",
            "class": "unproductive",
            "seconds": team["unproductive"],
            "pct": round(100.0 * team["unproductive"] / cat_total, 1),
        },
        {
            "label": "Idle",
            "class": "idle",
            "seconds": team["idle"],
            "pct": round(100.0 * team["idle"] / cat_total, 1),
        },
    ]
    treemap_apps = team_apps[:8]
    category_conic = _category_conic_gradient(category_viz)
    employee_viz = sorted(
        [
            {
                "id": r["user"].id,
                "name": r["user"].name,
                "pct": r["dt_productivity"],
                "eff": r["dt_effectiveness"],
            }
            for r in rows
            if not r["absent"]
        ],
        key=lambda x: x["pct"],
        reverse=True,
    )[:10]
    emp_max_pct = max((e["pct"] for e in employee_viz), default=1.0) or 1.0
    app_category_totals: dict[str, float] = {
        "productive": 0.0,
        "neutral": 0.0,
        "unproductive": 0.0,
    }
    for a in team_apps:
        cat = a.get("category") or "neutral"
        if cat in app_category_totals:
            app_category_totals[cat] += a["seconds"]
    app_cat_sum = sum(app_category_totals.values()) or 1.0
    app_category_viz = [
        {
            "label": k.capitalize(),
            "class": k,
            "seconds": v,
            "pct": round(100.0 * v / app_cat_sum, 1),
        }
        for k, v in app_category_totals.items()
        if v > 0
    ]
    app_category_conic = _category_conic_gradient(app_category_viz)

    total_desktime = team["active"]
    total_idle = team["idle"]
    total_all = max(total_desktime + total_idle, 1.0)

    return {
        "day": day_str,
        "avg_prod": avg_prod,
        "avg_eff": avg_eff,
        "humanize": humanize,
        "employee_count": len(users),
        "off_day": off_day,
        "trend_days": trend_days,
        "trend_max_pct": trend_max_pct,
        "trend_max_active": trend_max_active,
        "trend_range": trend_range,
        "prod_spark_line": prod_spark_line,
        "prod_spark_area": prod_spark_area,
        "active_spark_line": active_spark_line,
        "active_spark_area": active_spark_area,
        "eff_spark_line": eff_spark_line,
        "eff_spark_area": eff_spark_area,
        "present_spark_line": present_spark_line,
        "present_spark_area": present_spark_area,
        "trend_stack_max": trend_stack_max,
        "attendance_viz": attendance_viz,
        "category_viz": category_viz,
        "category_conic": category_conic,
        "app_category_viz": app_category_viz,
        "app_category_conic": app_category_conic,
        "employee_viz": employee_viz,
        "emp_max_pct": emp_max_pct,
        "treemap_apps": treemap_apps,
        "team_apps": team_apps,
        "team": team,
        "team_id": team_id,
        "viz_day_query": viz_day_query,
        "viz_apps_anchor": viz_apps_anchor,
        "viz_emp_anchor": viz_emp_anchor,
        "expected_hours": expected_hours,
        "split_prod": round(100.0 * team["productive"] / total_all, 1),
        "split_unprod": round(100.0 * team["unproductive"] / total_all, 1),
        "split_neutral": round(
            100.0
            * max(0.0, team["active"] - team["productive"] - team["unproductive"])
            / total_all,
            1,
        ),
        "split_idle": round(100.0 * team["idle"] / total_all, 1),
        "total_desktime": team["active"],
        "total_productive": team["productive"],
        "total_unproductive": team["unproductive"],
        "total_idle": team["idle"],
    }


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


def _lunch_range(day: datetime) -> tuple[float, float] | None:
    """Company lunch window for ``day`` as (start_ts, end_ts), or None if disabled."""
    settings = get_settings()
    ls = settings.lunch_start_hour
    le = settings.lunch_end_hour
    if ls is None or le is None or float(le) <= float(ls):
        return None
    tz = _tz()
    return (
        office_datetime(day, float(ls), tz).timestamp(),
        office_datetime(day, float(le), tz).timestamp(),
    )


def _without_lunch(acts: list[Activity], lunch: tuple[float, float] | None) -> list[Activity]:
    """Drop the lunch window from activities.

    Returns detached copies for clipped rows so the stored activity is never modified.
    """
    if lunch is None:
        return acts
    ls, le = lunch
    cols = [c.name for c in Activity.__table__.columns]
    out: list[Activity] = []
    for a in acts:
        s, e = float(a.start_ts), float(a.end_ts)
        if e <= ls or s >= le:
            out.append(a)
            continue
        for ps, pe in ((s, min(e, ls)), (max(s, le), e)):
            if pe - ps <= 0:
                continue
            piece = Activity(**{c: getattr(a, c) for c in cols if c != "id"})
            piece.start_ts, piece.end_ts, piece.duration = ps, pe, pe - ps
            out.append(piece)
    return out


def _activities_for(user_id: int, day: datetime) -> list[Activity]:
    start, end = day_bounds(day, tz_name=_tz())
    acts = list(
        db.session.execute(
            db.select(Activity)
            .filter(Activity.user_id == user_id)
            .filter(Activity.end_ts >= start, Activity.start_ts < end)
            .order_by(Activity.start_ts.asc())
        ).scalars()
    )
    return _without_lunch(acts, _lunch_range(day))


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


def _agent_connected(user: User, now_ts: float, window: float) -> bool:
    """Desktop app recently pinged the server (matches profile menu)."""
    ping = getattr(user, "last_agent_ping", None)
    if ping is None or (now_ts - float(ping)) > window:
        return False
    stopped = getattr(user, "agent_stopped_at", None)
    return not (stopped and float(stopped) >= float(ping))


def _is_online(user: User, last: float | None, now_ts: float, window: float) -> bool:
    """Recent activity or agent ping, and the agent has not reported Quit / Sign out since."""
    stopped = getattr(user, "agent_stopped_at", None)
    if last is not None and (now_ts - last) <= window:
        if not (stopped and stopped >= last):
            return True
    return _agent_connected(user, now_ts, window)


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


def _office_start_ts(day: datetime) -> float:
    settings = get_settings()
    start_h = float(settings.office_start_hour or settings.expected_arrival_hour or 9.5)
    return office_datetime(day, start_h, _tz()).timestamp()


def _is_late(arrival_ts: float | None, day: datetime) -> bool:
    if arrival_ts is None or _is_off_day(day):
        return False
    return arrival_ts > _office_start_ts(day)


def _is_early(arrival_ts: float | None, day: datetime) -> bool:
    if arrival_ts is None or _is_off_day(day):
        return False
    return arrival_ts < _office_start_ts(day)


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


def _bar_stack_slice_pct(seconds: float, bucket_seconds: float, *, min_pct: float = 5.0) -> float:
    """Stack segment size for the bar chart; omit tiny slices (tooltip still has exact times)."""
    if seconds <= 0 or bucket_seconds <= 0:
        return 0.0
    pct = 100.0 * seconds / bucket_seconds
    return round(pct, 2) if pct >= min_pct else 0.0


def _overlaps_range(s: float, e: float, ranges: list[tuple[float, float]]) -> bool:
    for rs, re in ranges:
        if s < re and e > rs:
            return True
    return False


def _bar_stack_columns(
    segments: list[dict],
    *,
    bucket_seconds: float = 300.0,
    view_start: float | None = None,
    view_end: float | None = None,
    display_gaps: list[dict] | None = None,
) -> list[dict]:
    """Per-bucket columns: height = productivity %, color = dominant category."""
    cols: list[dict] = []
    total = max(1.0, float(bucket_seconds))
    span = (
        max(1.0, (view_end or 0) - (view_start or 0))
        if view_start is not None and view_end is not None
        else 0.0
    )
    gap_ranges: list[tuple[float, float]] = []
    if display_gaps:
        for g in display_gaps:
            gap_ranges.append((float(g["start"]), float(g["end"])))

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
        show_idle = False
        is_fill = bool(seg.get("fillable"))
        req_status = (seg.get("request_status") or "").strip().lower()
        is_offline_req = bool(seg.get("requested")) and req_status in ("pending", "rejected")
        is_pending = req_status == "pending"
        is_rejected = req_status == "rejected"
        orig_start = float(seg.get("start") or 0)
        orig_end = float(seg.get("end") or (orig_start + bucket_seconds))
        if gap_ranges and not is_offline_req and _overlaps_range(orig_start, orig_end, gap_ranges):
            continue
        if not is_offline_req:
            if is_fill or seg.get("kind") in ("idle", "empty"):
                continue
        if not active and not is_offline_req:
            continue
        if tracked <= 0 and not is_offline_req:
            continue
        left = width = 0.0
        if span > 0 and view_start is not None:
            slot = max(0.0, min(100.0, (total / span) * 100.0))
            # Narrower than the bucket + centered so adjacent columns don't bleed together
            # on sub-pixel layout (office/lunch/presence bands still use full timestamps).
            width = max(0.15, slot * 0.66)
            bucket_left = max(
                0.0, min(100.0, ((orig_start - view_start) / span) * 100.0)
            )
            inset = max(0.0, (slot - width) / 2.0)
            left = max(0.0, min(100.0 - width, bucket_left + inset))

        active_sec = prod + unprod + neut
        qual = round(100.0 * prod / active_sec, 1) if active_sec > 0 else 0.0
        category = _dominant_category(prod, neut, unprod, idle, show_idle=show_idle)
        request_category = (seg.get("request_category") or "neutral").strip().lower()
        if is_pending:
            category = "pending"
        elif is_rejected:
            category = "rejected"

        active_sec_only = prod + unprod + neut
        fill = (
            round(min(100.0, 100.0 * active_sec_only / total), 1)
            if active_sec_only > 0
            else 0.0
        )
        if is_offline_req:
            height = 100.0
        elif fill > 0:
            height = round(max(fill, 8.0), 1)
        else:
            height = 0.0

        stack_p = _bar_stack_slice_pct(prod, total)
        stack_n = _bar_stack_slice_pct(neut, total)
        stack_u = _bar_stack_slice_pct(unprod, total)
        if is_offline_req:
            stack_p = stack_n = stack_u = 0.0
        elif active and (stack_p + stack_n + stack_u) <= 0 and active_sec_only > 0:
            dominant_pct = round(100.0 * active_sec_only / total, 2)
            if category == "productive":
                stack_p = dominant_pct
            elif category == "neutral":
                stack_n = dominant_pct
            elif category == "unproductive":
                stack_u = dominant_pct

        cols.append(
            {
                "start": s,
                "end": e,
                "left": round(left, 4),
                "width": round(width, 4),
                "quality": qual,
                "productivity": qual,
                "height_pct": height if (active or is_offline_req) else 0.0,
                "stack_p": stack_p,
                "stack_n": stack_n,
                "stack_u": stack_u,
                "stack_i": 0.0,
                "category": category,
                "label": seg.get("label") or "",
                "label_end": seg.get("label_end") or seg.get("label") or "",
                "fillable": False,
                "requested": is_offline_req,
                "request_status": req_status,
                "request_category": request_category if is_offline_req else "",
                "request_id": seg.get("request_id"),
                "gap_start": seg.get("gap_start"),
                "gap_end": seg.get("gap_end"),
                "productive_m": round(prod / 60.0, 1),
                "neutral_m": round(neut / 60.0, 1),
                "unproductive_m": round(unprod / 60.0, 1),
                "idle_m": round(idle / 60.0, 1),
                "active": active and not is_offline_req,
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
    if span_h <= 11:
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


def _intervals_minus_gaps(
    start: float,
    end: float,
    gap_ranges: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Pieces of [start, end) that do not overlap idle/offline gap ranges."""
    if end <= start:
        return []
    if not gap_ranges:
        return [(start, end)]
    pieces: list[tuple[float, float]] = [(start, end)]
    for gs, ge in sorted(gap_ranges):
        next_pieces: list[tuple[float, float]] = []
        for ps, pe in pieces:
            if pe <= gs or ps >= ge:
                next_pieces.append((ps, pe))
                continue
            if ps < gs:
                next_pieces.append((ps, gs))
            if pe > ge:
                next_pieces.append((ge, pe))
        pieces = next_pieces
    return [(a, b) for a, b in pieces if b - a > 0.01]


def _presence_offline_tracks(
    offline_requests: list,
    view_start: float,
    view_end: float,
    *,
    tz_name: str,
) -> list[dict]:
    """Dashed presence rail for approved manual / offline fills."""
    from ..tzutil import format_datetime_local

    span = max(1.0, view_end - view_start)
    tracks: list[dict] = []
    for req in offline_requests:
        if getattr(req, "status", None) != "approved":
            continue
        s = max(float(req.start_ts), view_start)
        e = min(float(req.end_ts), view_end)
        if e <= s:
            continue
        left = max(0.0, ((s - view_start) / span) * 100.0)
        width = max(0.15, min(100.0 - left, ((e - s) / span) * 100.0))
        tracks.append(
            {
                "left": round(left, 3),
                "width": round(width, 3),
                "id": int(req.id),
                "range_label": (
                    f"{format_clock(req.start_ts, tz_name=tz_name)} – "
                    f"{format_clock(req.end_ts, tz_name=tz_name)}"
                ),
                "note": (req.note or "").strip(),
                "category": (req.category or "neutral").strip().lower(),
                "duration_label": humanize(float(req.duration or (e - s))),
                "prefill_start": format_datetime_local(float(req.start_ts), tz_name),
                "prefill_end": format_datetime_local(float(req.end_ts), tz_name),
            }
        )
    return tracks


def _presence_pending_tracks(
    pending_requests: list,
    view_start: float,
    view_end: float,
    *,
    tz_name: str,
) -> list[dict]:
    """Grey presence rail for offline fills awaiting approval (clickable)."""
    from ..tzutil import format_datetime_local

    span = max(1.0, view_end - view_start)
    tracks: list[dict] = []
    for req in pending_requests:
        s = max(float(req.start_ts), view_start)
        e = min(float(req.end_ts), view_end)
        if e <= s:
            continue
        left = max(0.0, ((s - view_start) / span) * 100.0)
        width = max(0.15, min(100.0 - left, ((e - s) / span) * 100.0))
        tracks.append(
            {
                "left": round(left, 3),
                "width": round(width, 3),
                "id": int(req.id),
                "range_label": (
                    f"{format_clock(req.start_ts, tz_name=tz_name)} – "
                    f"{format_clock(req.end_ts, tz_name=tz_name)}"
                ),
                "note": (req.note or "").strip(),
                "category": (req.category or "neutral").strip().lower(),
                "duration_label": humanize(float(req.duration or (e - s))),
                "prefill_start": format_datetime_local(float(req.start_ts), tz_name),
                "prefill_end": format_datetime_local(float(req.end_ts), tz_name),
                "status": "pending",
            }
        )
    return tracks


def _presence_tracks(
    segments: list[dict],
    view_start: float,
    view_end: float,
    *,
    tz_name: str,
    display_gaps: list[dict] | None = None,
    pending_ranges: list[tuple[float, float]] | None = None,
    offline_ranges: list[tuple[float, float]] | None = None,
) -> list[dict]:
    from ..tzutil import format_datetime_local
    span = max(1.0, view_end - view_start)
    tracks: list[dict] = []
    gap_ranges = [(float(g["start"]), float(g["end"])) for g in (display_gaps or [])]
    mask_ranges = list(gap_ranges)
    if pending_ranges:
        mask_ranges.extend(pending_ranges)
    if offline_ranges:
        mask_ranges.extend(offline_ranges)
    i, n = 0, len(segments)

    def _tracked(seg: dict) -> bool:
        if seg.get("fillable") or seg.get("kind") in ("idle", "empty"):
            return False
        return seg.get("kind") in ("productive", "neutral", "unproductive")

    while i < n:
        if not _tracked(segments[i]):
            i += 1
            continue
        j = i
        while j < n and _tracked(segments[j]):
            j += 1
        s = float(segments[i]["start"])
        e = float(segments[j - 1].get("end") or segments[j - 1]["start"])
        if e <= view_start or s >= view_end:
            i = j
            continue
        s, e = max(s, view_start), min(e, view_end)
        for ps, pe in _intervals_minus_gaps(s, e, mask_ranges):
            left = max(0.0, ((ps - view_start) / span) * 100.0)
            width = max(0.15, min(100.0 - left, ((pe - ps) / span) * 100.0))
            tracks.append(
                {
                    "left": round(left, 3),
                    "width": round(width, 3),
                    "start": ps,
                    "end": pe,
                    "range_label": (
                        f"{format_clock(ps, tz_name=tz_name)} – "
                        f"{format_clock(pe, tz_name=tz_name)}"
                    ),
                    "prefill_start": format_datetime_local(ps, tz_name),
                    "prefill_end": format_datetime_local(pe, tz_name),
                }
            )
        i = j
    return tracks


def _enrich_bar_column_tracked_prefill(cols: list[dict], tz_name: str) -> None:
    from ..tzutil import format_datetime_local

    for col in cols:
        if not col.get("active"):
            continue
        s, e = float(col["start"]), float(col["end"])
        col["prefill_start"] = format_datetime_local(s, tz_name)
        col["prefill_end"] = format_datetime_local(e, tz_name)


def _enrich_tracked_presence_prefill(tracks: list[dict], acts: list[Activity]) -> None:
    import re

    for t in tracks:
        ps, pe = float(t["start"]), float(t["end"])
        overlapping = [
            a
            for a in acts
            if not a.idle
            and a.app != "offline"
            and float(a.start_ts) < pe
            and float(a.end_ts) > ps
        ]
        if not overlapping:
            t["project_name"] = ""
            t["task_name"] = ""
            continue
        main = max(overlapping, key=lambda a: float(a.duration))
        text = main.title or ""
        project = task = ""
        m = re.search(r"Project:\s*([^,)]+)", text, re.I)
        if m:
            project = m.group(1).strip()
        m = re.search(r"Task:\s*([^,)]+)", text, re.I)
        if m:
            task = m.group(1).strip()
        t["project_name"] = project
        t["task_name"] = task


def _enrich_gap_prefills(gaps: list[dict], tz_name: str) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ..tzutil import format_datetime_local

    tz = ZoneInfo(tz_name)
    for g in gaps:
        s, e = float(g["start"]), float(g["end"])
        g["prefill_start"] = format_datetime_local(s, tz_name)
        g["prefill_end"] = format_datetime_local(e, tz_name)
        ls = datetime.fromtimestamp(s, tz=tz)
        le = datetime.fromtimestamp(e, tz=tz)
        g["gap_date"] = ls.strftime("%Y-%m-%d")
        g["start_min"] = int(ls.hour * 60 + ls.minute)
        g["end_min"] = int(le.hour * 60 + le.minute)


def _presence_fill_gaps(
    gaps: list[dict], view_start: float, view_end: float
) -> list[dict]:
    """Position fillable offline gaps on the presence strip (same times as chart gaps)."""
    span = max(1.0, view_end - view_start)
    out: list[dict] = []
    for g in gaps:
        s = float(g["start"])
        e = float(g["end"])
        if e <= view_start or s >= view_end:
            continue
        s_cl = max(s, view_start)
        e_cl = min(e, view_end)
        left = max(0.0, ((s_cl - view_start) / span) * 100.0)
        width = max(0.15, min(100.0 - left, ((e_cl - s_cl) / span) * 100.0))
        dur = float(g.get("duration") or (e_cl - s_cl))
        out.append(
            {
                "left": round(left, 3),
                "width": round(width, 3),
                "start": s,
                "end": e,
                "label": f"{g.get('label_start', '')}–{g.get('label_end', '')}",
                "duration_label": humanize(dur),
                "prefill_start": g.get("prefill_start") or "",
                "prefill_end": g.get("prefill_end") or "",
                "gap_date": g.get("gap_date") or "",
                "start_min": int(g.get("start_min") or 0),
                "end_min": int(g.get("end_min") or 0),
            }
        )
    return out


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


def _chart_payload(
    activities: list[Activity],
    day: datetime,
    manual_entries: list | None = None,
) -> dict:
    start, end = day_bounds(day, tz_name=_tz())
    manual = manual_entries or []
    dm = compute_day_metrics(activities, manual, start, end, _expected_seconds())
    summary = dm.summary
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
        "desktime_productivity": dm.dt_productivity,
        "desktime_effectiveness": dm.dt_effectiveness,
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
    expected = _expected_seconds()
    manual = _manual_for(user.id, day)
    dm = compute_day_metrics(acts, manual, start, end, expected)
    summary = dm.summary
    manual_seconds = dm.manual_total_seconds
    desktime_seconds = dm.desktime_seconds
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    settings = get_settings()
    last = _last_seen(user.id)
    now_ts = datetime.now().timestamp()
    is_online = _is_online(user, last, now_ts, cfg.online_window)
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
    # Gaps are fillable from office start (or arrival if earlier); entries before
    # arrival go through approval like any other offline request.
    fill_from_ts = _office_start_ts(day)
    if summary.arrival_ts is not None:
        fill_from_ts = min(fill_from_ts, summary.arrival_ts)
    lunch = _lunch_range(day)
    lunch_excluded = [lunch] if lunch is not None else []
    idle_gap_min = float(settings.idle_threshold or 180)
    bar_raw = productivity_bar_segments(
        acts,
        start,
        end,
        bucket_seconds=300.0,
        tz_name=_tz(),
        now_ts=gap_now_ts,
        earliest_fill_ts=fill_from_ts,
        excluded_ranges=lunch_excluded,
        min_gap_seconds=idle_gap_min,
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
    covered_ranges = [
        (float(r.start_ts), float(r.end_ts))
        for r in covered_reqs
        if r.status == "approved"
    ]
    pending_offline = [r for r in covered_reqs if r.status == "pending"]
    approved_offline = [r for r in covered_reqs if r.status == "approved"]
    rejected_offline = list(
        db.session.execute(
            db.select(OfflineRequest)
            .filter(
                OfflineRequest.user_id == user.id,
                OfflineRequest.status == "rejected",
                OfflineRequest.end_ts > day_start,
                OfflineRequest.start_ts < day_end,
            )
        ).scalars()
    )
    pending_ranges = [(float(r.start_ts), float(r.end_ts)) for r in pending_offline]
    offline_ranges = [(float(r.start_ts), float(r.end_ts)) for r in approved_offline]
    bar_raw = suppress_covered_gaps(bar_raw, covered_ranges)
    bar_raw = annotate_pending_offline_requests(bar_raw, pending_offline)
    bar_raw = annotate_rejected_offline_requests(bar_raw, rejected_offline)
    bar_raw = reconcile_fillable_gap_bounds(bar_raw)
    bar = merge_bar_segments(bar_raw)

    # Auto-fit bar: office band (~8 AM–8 PM for 9:30–6:30 settings) plus user activity.
    # ?bar=day shows midnight to midnight.
    bar_mode = (request.args.get("bar") or "work").strip().lower()
    if bar_mode not in ("work", "day"):
        bar_mode = "work"
    manual_ranges = [(float(m.start_ts), float(m.end_ts)) for m in manual]
    view_start_h, view_end_h = bar_view_hours(
        office_start_h=office_start,
        office_end_h=office_end,
        day_start_ts=start,
        arrival_ts=summary.arrival_ts,
        last_seen_ts=summary.last_seen_ts,
        manual_ranges=manual_ranges,
        is_today=is_today,
        is_online=is_online,
        now_ts=now_tz(_tz()).timestamp() if is_today else None,
        bar_mode=bar_mode,
    )
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
        earliest_fill_ts=fill_from_ts,
        excluded_ranges=lunch_excluded,
        min_gap_seconds=idle_gap_min,
    )
    bar_display = suppress_covered_gaps(bar_display, covered_ranges)
    bar_display = annotate_pending_offline_requests(bar_display, pending_offline)
    bar_display = annotate_rejected_offline_requests(bar_display, rejected_offline)
    bar_display = reconcile_fillable_gap_bounds(bar_display)
    gaps_raw = idle_gap_list(bar_display, tz_name=_tz())
    gaps = merge_nearby_gaps(
        gaps_raw,
        bridge_seconds=max(600.0, float(idle_gap_min)),
        tz_name=_tz(),
    )
    _enrich_gap_prefills(gaps, _tz())
    gap_seconds = sum(g["duration"] for g in gaps)
    pending_offline_seconds = sum(
        float(r.duration or max(0.0, float(r.end_ts) - float(r.start_ts)))
        for r in pending_offline
    )
    # Idle KPI: agent idle + unfilled gaps + offline awaiting approval.
    idle_display_seconds = (
        float(summary.idle_seconds) + gap_seconds + pending_offline_seconds
    )
    bar_columns = _bar_stack_columns(
        bar_display,
        bucket_seconds=bucket_seconds,
        view_start=view_start,
        view_end=view_end,
        display_gaps=gaps,
    )
    _enrich_bar_column_tracked_prefill(bar_columns, _tz())
    presence_tracks = _presence_tracks(
        bar_display,
        view_start,
        view_end,
        tz_name=_tz(),
        display_gaps=gaps,
        pending_ranges=pending_ranges,
        offline_ranges=offline_ranges,
    )
    _enrich_tracked_presence_prefill(presence_tracks, acts)
    presence_pending_tracks = _presence_pending_tracks(
        pending_offline, view_start, view_end, tz_name=_tz()
    )
    presence_rejected_tracks = _presence_pending_tracks(
        rejected_offline, view_start, view_end, tz_name=_tz()
    )
    for t in presence_rejected_tracks:
        t["status"] = "rejected"
    presence_offline_tracks = _presence_offline_tracks(
        approved_offline, view_start, view_end, tz_name=_tz()
    )
    presence_fill_gaps = _presence_fill_gaps(gaps, view_start, view_end)
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
    lunch_left = lunch_width = 0.0
    lunch_label = ""
    if lunch is not None:
        ls, le = max(lunch[0], view_start), min(lunch[1], view_end)
        if le > ls:
            lunch_left = round(((ls - view_start) / view_span) * 100.0, 3)
            lunch_width = round(((le - ls) / view_span) * 100.0, 3)
        lunch_label = (
            f"{format_clock(lunch[0], tz_name=_tz())} – {format_clock(lunch[1], tz_name=_tz())}"
        )
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
    ai_report = None
    gemini_configured = False
    private_period = db.session.execute(
        db.select(PrivatePeriod)
        .filter_by(user_id=user.id, active=True)
        .order_by(PrivatePeriod.start_ts.desc())
    ).scalar_one_or_none()
    if not is_self:
        from .ai_report import get_day_report, load_gemini_api_key

        cfg = current_app.config.get("TIMETRACK_SERVER_CONFIG")
        data_dir = getattr(cfg, "data_dir", None) if cfg else None
        gemini_configured = bool(load_gemini_api_key(data_dir))
        ai_report = get_day_report(user.id, day.strftime("%Y-%m-%d"))
    bar_interactive = is_self or (
        current_user.is_authenticated and current_user.is_admin
    )
    return {
        "subject": user,
        "is_self": is_self,
        "clickable_gaps": bar_interactive,
        **_day_nav(day),
        "day_label": day.strftime("%a, %B %d, %Y").replace(" 0", " "),
        "summary": summary,
        "screenshots": (
            _screenshots_for(user.id, day)
            if (not is_self or user.view_own_screenshots)
            else []
        ),
        "show_own_screenshots": is_self and bool(user.view_own_screenshots),
        "charts": _chart_payload(acts, day, manual),
        "apps": apps_with_share(summary),
        "sites": top_sites(acts),
        "url_visits": list(reversed(website_report(acts, start, end)["visits"]))[:15],
        "timeline": activity_timeline(acts, limit=100),
        "bar": bar,
        "bar_columns": bar_columns,
        "bar_hour_ticks": bar_hour_ticks,
        "bar_away_regions": bar_away_regions,
        "presence_tracks": presence_tracks,
        "presence_pending_tracks": presence_pending_tracks,
        "presence_rejected_tracks": presence_rejected_tracks,
        "presence_offline_tracks": presence_offline_tracks,
        "presence_fill_gaps": presence_fill_gaps,
        "timeline_chart": timeline_chart,
        "view_start_ts": view_start,
        "view_end_ts": view_end,
        "bar_view_label": f"{format_hour_short(view_start_h)} – {format_hour_short(view_end_h)}",
        "bar_mode": bar_mode,
        "gaps": gaps,
        "gap_seconds": gap_seconds,
        "idle_display_seconds": idle_display_seconds,
        "my_requests": my_requests,
        "idle_threshold": int(settings.idle_threshold or 180),
        "arrived": format_clock(summary.arrival_ts, tz_name=_tz()),
        "left": left_clock,
        "left_display": left_display,
        "left_sub": left_sub,
        "is_online": is_online,
        "is_today": is_today,
        "late": _is_late(summary.arrival_ts, day),
        "early": _is_early(summary.arrival_ts, day),
        "absent": absent,
        "off_day": off_day,
        "lunch_left_pct": lunch_left,
        "lunch_width_pct": lunch_width,
        "lunch_label": lunch_label,
        "tz_name": _tz(),
        "office_hours_label": f"{format_hour_short(office_start)} – {format_hour_short(office_end)} IST"
        if _tz() == "Asia/Kolkata"
        else f"{format_hour_short(office_start)} – {format_hour_short(office_end)}",
        "office_left_pct": office_left,
        "office_width_pct": office_width,
        "now_pct": now_pct,
        "dt_productivity": dm.dt_productivity,
        "dt_effectiveness": dm.dt_effectiveness,
        "expected_hours": expected / 3600.0,
        "desktime_seconds": desktime_seconds,
        "humanize": humanize,
        "fill_requires_approval": (not user.is_admin) or bool(
            settings.offline_requires_approval
        ),
        "week": week,
        "spark": spark,
        "spark_line": spark_line,
        "spark_area": spark_area,
        "manual": manual,
        "manual_seconds": manual_seconds,
        "manual_extra_seconds": dm.manual_extra_seconds,
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
        "ai_report": ai_report,
        "gemini_configured": gemini_configured,
        "private_active": private_period is not None,
        "private_started": format_clock(private_period.start_ts, tz_name=_tz())
        if private_period
        else "",
        "private_ago": _private_duration_label(private_period.start_ts, now_ts)
        if private_period
        else "",
        "company_settings": settings,
    }


def tray_day_json_for_user(user: User) -> dict:
    """Compact today snapshot for the desktop tray flyout (agent API)."""
    day = _parse_day(None)
    start, end = day_bounds(day, tz_name=_tz())
    acts = _activities_for(user.id, day)
    manual = _manual_for(user.id, day)
    expected = _expected_seconds()
    dm = compute_day_metrics(acts, manual, start, end, expected)
    summary = dm.summary
    desktime_seconds = dm.desktime_seconds
    settings = get_settings(user.organization_id or 1)
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    now_ts = datetime.now().timestamp()
    last = _last_seen(user.id)
    is_online = _is_online(user, last, now_ts, cfg.online_window)
    is_today = day.date() == today_tz(_tz())
    office_start = float(settings.office_start_hour or settings.expected_arrival_hour or 9.5)
    office_end = float(settings.office_end_hour or 18.5)

    private_seconds = 0.0
    for period in db.session.execute(
        db.select(PrivatePeriod).filter(
            PrivatePeriod.user_id == user.id,
            PrivatePeriod.start_ts < end,
        )
    ).scalars():
        pend = period.end_ts
        if pend is None:
            pend = now_ts if period.active and is_today else period.start_ts
        clip_start = max(float(period.start_ts), start)
        clip_end = min(float(pend), end)
        if clip_end > clip_start:
            private_seconds += clip_end - clip_start

    cat_total = (
        summary.productive_seconds
        + summary.neutral_seconds
        + summary.unproductive_seconds
        + summary.idle_seconds
    ) or 1.0
    mix_pct = {
        "productive": round(100.0 * summary.productive_seconds / cat_total, 1),
        "neutral": round(100.0 * summary.neutral_seconds / cat_total, 1),
        "unproductive": round(100.0 * summary.unproductive_seconds / cat_total, 1),
        "idle": round(100.0 * summary.idle_seconds / cat_total, 1),
    }

    now_pct = None
    view_start_h, view_end_h = bar_view_hours(
        office_start_h=office_start,
        office_end_h=office_end,
        day_start_ts=start,
        arrival_ts=summary.arrival_ts,
        last_seen_ts=summary.last_seen_ts,
        manual_ranges=[(float(m.start_ts), float(m.end_ts)) for m in manual],
        is_today=is_today,
        is_online=is_online,
        now_ts=now_tz(_tz()).timestamp() if is_today else None,
        bar_mode="work",
    )
    view_start = start + view_start_h * 3600.0
    view_end = start + view_end_h * 3600.0
    view_span = max(1.0, view_end - view_start)
    if is_today:
        now_local = now_tz(_tz()).timestamp()
        if view_start <= now_local <= view_end:
            now_pct = round(((now_local - view_start) / view_span) * 100.0, 2)

    top_apps = []
    ranked = sorted(apps_with_share(summary), key=lambda r: float(r.get("seconds") or 0), reverse=True)
    for row in ranked[:3]:
        top_apps.append(
            {
                "app": row.get("app") or "",
                "seconds": float(row.get("seconds") or 0),
                "label": row.get("app") or "—",
            }
        )

    office_label = f"{format_hour_short(office_start)} – {format_hour_short(office_end)}"
    if _tz() == "Asia/Kolkata":
        office_label += " IST"

    return {
        "day": day.strftime("%Y-%m-%d"),
        "arrived": format_clock(summary.arrival_ts, tz_name=_tz()),
        "arrival_ts": summary.arrival_ts,
        "desktime_seconds": desktime_seconds,
        "productive_seconds": summary.productive_seconds,
        "span_seconds": summary.span_seconds,
        "idle_seconds": summary.idle_seconds,
        "private_seconds": private_seconds,
        "dt_productivity": dm.dt_productivity,
        "dt_effectiveness": dm.dt_effectiveness,
        "expected_seconds": expected,
        "office_hours_label": office_label,
        "is_online": is_online,
        "now_pct": now_pct,
        "mix_pct": mix_pct,
        "top_apps": top_apps,
    }


@views_bp.route("/")
def home():
    if not current_user.is_authenticated:
        return render_template("public_home.html")
    if current_user.is_admin:
        return redirect(url_for("views.admin"))
    return redirect(url_for("views.me"))


@views_bp.route("/blog")
def blog_index():
    from .blog_posts import all_posts, blog_categories

    posts = all_posts()
    return render_template(
        "blog_index.html",
        posts=posts,
        categories=blog_categories(posts),
        nav_active="blog",
    )


@views_bp.route("/blog/<slug>")
def blog_post(slug: str):
    from .blog_posts import all_posts, get_post

    post = get_post(slug)
    if post is None:
        abort(404)
    all_p = all_posts()
    related = [p for p in all_p if p.slug != slug][:3]
    same_cat = [p for p in all_p if p.category == post.category and p.slug != slug][:2]
    return render_template(
        "blog_post.html",
        post=post,
        related=related,
        same_category=same_cat,
        nav_active="blog",
    )


@views_bp.route("/download")
@login_required
def download_page():
    from .releases import scan_releases

    return render_template("download.html", releases=scan_releases())


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
    from .daily_quotes import daily_motivation_quote

    ctx["daily_quote"] = daily_motivation_quote(day, current_user.id)
    return render_template("employee_status.html", **ctx)


def _avatar_peer_or_404(user_id: int) -> User:
    """Avatar may be shown for self or anyone in the same organization."""
    from .tenancy import assert_same_org

    user = db.session.get(User, user_id) or abort(404)
    if user.id != current_user.id and not assert_same_org(user):
        abort(404)
    return user


@views_bp.route("/avatar/<int:user_id>")
@login_required
def user_avatar(user_id: int):
    from .avatar_util import avatar_disk_path, user_has_avatar

    user = _avatar_peer_or_404(user_id)
    if not user_has_avatar(user):
        abort(404)
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    path = avatar_disk_path(cfg.data_dir, user.id)
    if not path.is_file():
        abort(404)
    return send_from_directory(
        str(path.parent),
        path.name,
        mimetype="image/jpeg",
        max_age=86400,
    )


def _apply_timezone_field(user: User) -> bool:
    """Set ``user.timezone`` from the form (empty = company default). False if invalid."""
    if "timezone" not in request.form:
        return True
    raw = (request.form.get("timezone") or "").strip()
    if not raw:
        user.timezone = None
        return True
    tz = valid_tz_name(raw)
    if tz is None:
        db.session.rollback()
        flash(f"Unknown time zone “{raw}”. Pick one from the list, e.g. Europe/London.", "error")
        return False
    user.timezone = tz
    return True


@views_bp.route("/me/account", methods=["GET", "POST"])
@login_required
def my_account():
    from .avatar_util import remove_avatar_file, save_avatar_upload, user_has_avatar
    from .models import User

    user = db.session.get(User, current_user.id) or abort(404)
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "photo":
            try:
                save_avatar_upload(user, request.files.get("photo"), cfg.data_dir)
                db.session.commit()
                flash("Profile photo updated.", "info")
            except ValueError as exc:
                flash(str(exc), "error")
        elif action == "remove_photo":
            if user_has_avatar(user):
                remove_avatar_file(user, cfg.data_dir)
                db.session.commit()
                flash("Profile photo removed.", "info")
        elif action == "profile":
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip()
            user.display_name = name
            user.email = email
            if _apply_timezone_field(user):
                db.session.commit()
                flash("Profile saved.", "info")
        elif action == "password":
            current_pw = request.form.get("current_password") or ""
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm") or ""
            if not user.check_password(current_pw):
                flash("Current password is incorrect.", "error")
            elif len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
            elif password != confirm:
                flash("New passwords do not match.", "error")
            else:
                user.set_password(password)
                db.session.commit()
                flash("Password updated.", "info")
        else:
            flash("Unknown action.", "error")
        return redirect(url_for("views.my_account"))
    return render_template("my_account.html")


@views_bp.route("/admin")
@admin_required
def admin():
    day = _parse_day(request.args.get("day"))
    team_id = request.args.get("team_id", type=int)
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    now_ts = datetime.now().timestamp()
    expected = _expected_seconds()
    start, end = day_bounds(day, tz_name=_tz())

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

    rows, team, team_apps, avg_prod, avg_eff, off_day = _admin_team_day_state(
        users,
        day,
        now_ts=now_ts,
        online_window=cfg.online_window,
    )
    preloaded_acts: list = []
    for r in rows:
        preloaded_acts.extend(r.pop("_acts", []) or [])

    tracked = [r for r in rows if not r["absent"]]
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
    top_absent = sorted(
        [r for r in rows if r["absent"]],
        key=lambda r: (r["user"].name or r["user"].username).lower(),
    )[:5]
    top_private = sorted(
        [r for r in rows if r.get("private_active")],
        key=lambda r: r.get("private_start_ts") or 0.0,
    )[:8]
    private_count = len(top_private)

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

    from .releases import is_outdated as client_outdated

    if request.args.get("partial") == "live":
        for r in rows:
            r.pop("_acts", None)
        return render_template(
            "_admin_live_partial.html",
            rows=rows,
            team=team,
            day=day,
            off_day=off_day,
            online_window=int(cfg.online_window),
            humanize=humanize,
            client_outdated=client_outdated,
            top_private=top_private,
            private_count=private_count,
        )

    total_desktime = float(team.get("active") or 0.0)
    total_idle = float(team.get("idle") or 0.0)
    total_productive = float(team.get("productive") or 0.0)
    total_unproductive = float(team.get("unproductive") or 0.0)
    total_all = max(total_desktime + total_idle, 1.0)
    split_prod = round(100.0 * total_productive / total_all, 1)
    split_unprod = round(100.0 * total_unproductive / total_all, 1)
    split_neutral = round(
        100.0
        * max(0.0, total_desktime - total_productive - total_unproductive)
        / total_all,
        1,
    )
    split_idle = round(100.0 * total_idle / total_all, 1)
    week = _week_strip(None, day)
    day_label = day.strftime("%a, %B %d, %Y")
    tracked_today = float(team.get("active") or 0.0) + float(team.get("idle") or 0.0)
    team_hour_bars, team_has_hour_data = _team_hourly_bars(
        users, day, preloaded_acts=preloaded_acts
    )

    return render_template(
        "admin.html",
        **_day_nav(day),
        day_label=day_label,
        week=week,
        tracked_today=tracked_today,
        team_hour_bars=team_hour_bars,
        team_has_hour_data=team_has_hour_data,
        client_outdated=client_outdated,
        rows=rows,
        team=team,
        avg_prod=avg_prod,
        avg_eff=avg_eff,
        top_productive=top_productive,
        top_effective=top_effective,
        top_unproductive=top_unproductive,
        top_late=top_late,
        top_absent=top_absent,
        team_apps=team_apps,
        flagged_shots=flagged_shots,
        expected_hours=expected / 3600.0,
        humanize=humanize,
        employee_count=len(users),
        off_day=off_day,
        teams=teams,
        team_id=team_id,
        online_window=int(cfg.online_window),
        total_desktime=total_desktime,
        total_productive=total_productive,
        total_unproductive=total_unproductive,
        total_idle=total_idle,
        split_prod=split_prod,
        split_unprod=split_unprod,
        split_neutral=split_neutral,
        split_idle=split_idle,
        top_private=top_private,
        private_count=private_count,
    )


def _org_user_or_404(user_id: int) -> User:
    """Employee lookup restricted to the signed-in admin's organization."""
    from .models import ROLE_SUPERADMIN
    from .tenancy import assert_same_org

    user = db.session.get(User, user_id) or abort(404)
    if current_user.role != ROLE_SUPERADMIN and not assert_same_org(user):
        abort(404)
    # Pages about one employee split days in that employee's time zone.
    g.tz_name = user_tz_name(user)
    return user


@views_bp.route("/admin/user/<int:user_id>/websites")
@admin_required
def admin_user_websites(user_id: int):
    """Websites & URLs one employee used over 1 / 7 / 30 days."""
    user = _org_user_or_404(user_id)
    day = _parse_day(request.args.get("day"))
    range_days = request.args.get("range", type=int) or 1
    if range_days not in (1, 7, 30):
        range_days = 1
    tz = _tz()
    first_day = day - timedelta(days=range_days - 1)
    start, _ = day_bounds(first_day, tz_name=tz)
    _, end = day_bounds(day, tz_name=tz)
    acts = list(
        db.session.execute(
            db.select(Activity)
            .filter(Activity.user_id == user.id)
            .filter(Activity.end_ts >= start, Activity.start_ts < end)
            .order_by(Activity.start_ts.asc())
        ).scalars()
    )
    report = website_report(acts, start, end)
    site_filter = (request.args.get("site") or "").strip().lower()
    visits = report["visits"]
    if site_filter:
        visits = [v for v in visits if v["domain"] == site_filter]
    if range_days == 1:
        range_label = day.strftime("%a, %B %d, %Y").replace(" 0", " ")
    else:
        range_label = (
            f"{first_day.strftime('%b %d').replace(' 0', ' ')} – "
            f"{day.strftime('%b %d, %Y').replace(' 0', ' ')}"
        )
    return render_template(
        "user_websites.html",
        subject=user,
        report=report,
        visits=visits,
        site_filter=site_filter,
        range_days=range_days,
        range_label=range_label,
        multi_day=range_days > 1,
        humanize=humanize,
        **_day_nav(day),
    )


_SCREENSHOTS_PER_PAGE = 21


def _shot_page_numbers(page: int, pages: int, *, max_buttons: int = 9) -> list[int | None]:
    """Page numbers for screenshot pager; None = ellipsis gap."""
    if pages <= max_buttons:
        return list(range(1, pages + 1))
    nums: list[int | None] = [1]
    lo = max(2, page - 2)
    hi = min(pages - 1, page + 2)
    if lo > 2:
        nums.append(None)
    for p in range(lo, hi + 1):
        nums.append(p)
    if hi < pages - 1:
        nums.append(None)
    nums.append(pages)
    return nums


@views_bp.route("/admin/user/<int:user_id>")
@admin_required
def admin_user(user_id: int):
    user = _org_user_or_404(user_id)
    day = _parse_day(request.args.get("day"))
    ctx = _employee_day_context(user, day, is_self=False)
    all_shots = ctx["screenshots"]
    total = len(all_shots)
    pages = max(1, (total + _SCREENSHOTS_PER_PAGE - 1) // _SCREENSHOTS_PER_PAGE) if total else 1
    page = request.args.get("shot_page", type=int) or 1
    page = max(1, min(page, pages))
    start = (page - 1) * _SCREENSHOTS_PER_PAGE
    ctx["screenshots"] = all_shots[start : start + _SCREENSHOTS_PER_PAGE]
    ctx["shot_page"] = page
    ctx["shot_pages"] = pages
    ctx["shot_total"] = total
    ctx["shot_page_numbers"] = _shot_page_numbers(page, pages)
    if request.args.get("partial") == "screenshots":
        return render_template("_admin_user_screenshots.html", **ctx)
    return render_template("user.html", **ctx)


@views_bp.route("/admin/user/<int:user_id>/settings", methods=["POST"])
@admin_required
def admin_user_settings(user_id: int):
    """Legacy POST endpoint — settings UI lives on /employees/<id>/settings."""
    user = _org_user_or_404(user_id)
    user.private_time_allowed = bool(request.form.get("private_time_allowed"))
    user.view_own_screenshots = bool(request.form.get("view_own_screenshots"))
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
    if not _apply_timezone_field(user):
        flash("Invalid time zone.", "error")
        return redirect(url_for("views.employee_settings", user_id=user.id, tab="general"))
    db.session.commit()
    flash(f"Settings saved for {user.username}.", "info")
    return redirect(url_for("views.employee_settings", user_id=user.id, tab="tracking"))


@views_bp.route("/admin/user/<int:user_id>/categories", methods=["POST"])
@admin_required
def admin_user_categories(user_id: int):
    from .settings_util import persist_category_rules
    from .tenancy import current_org_id

    _org_user_or_404(user_id)
    day = _parse_day(request.form.get("day") or request.args.get("day"))
    day_s = day.strftime("%Y-%m-%d")
    app_rows = list(
        zip(request.form.getlist("app_name"), request.form.getlist("app_category"))
    )
    site_rows = list(
        zip(request.form.getlist("site_name"), request.form.getlist("site_category"))
    )
    if not app_rows and not site_rows:
        flash("No apps or sites to update.", "error")
        return redirect(url_for("views.admin_user", user_id=user_id, day=day_s))

    changed = persist_category_rules(current_org_id(), app_rows, site_rows)
    flash(
        f"Categories saved for the whole team. Updated {changed} activities (last 90 days).",
        "info",
    )
    return redirect(url_for("views.admin_user", user_id=user_id, day=day_s))


@views_bp.route("/admin/user/<int:user_id>/ai-report/status")
@admin_required
def admin_user_ai_report_status(user_id: int):
    from .ai_report import get_day_report, report_to_dict, reset_stale_generating

    user = db.session.get(User, user_id) or abort(404)
    day_s = _parse_day(request.args.get("day")).strftime("%Y-%m-%d")
    reset_stale_generating(user.id, day_s)
    return jsonify(report_to_dict(get_day_report(user.id, day_s)))


@views_bp.route("/admin/user/<int:user_id>/ai-report", methods=["POST"])
@admin_required
def admin_user_ai_report(user_id: int):
    from .ai_report import (
        email_ai_report,
        get_day_report,
        load_gemini_api_key,
        queue_ai_report_generation,
        report_to_dict,
    )

    user = db.session.get(User, user_id) or abort(404)
    day = _parse_day(request.form.get("day") or request.args.get("day"))
    day_s = day.strftime("%Y-%m-%d")
    wants_json = (
        request.headers.get("X-Requested-With") == "fetch"
        or request.accept_mimetypes.best_match(["application/json", "text/html"]) == "application/json"
    )
    payload = request.get_json(silent=True) or {}
    action = (
        request.form.get("action") or payload.get("action") or request.args.get("action") or "generate"
    ).strip().lower()
    redirect_url = url_for("views.admin_user", user_id=user_id, day=day_s)

    if not load_gemini_api_key(
        getattr(current_app.config.get("TIMETRACK_SERVER_CONFIG"), "data_dir", None)
    ):
        msg = "Gemini API key not set. Add GEMINI_API_KEY or data/gemini.toml on the server."
        if wants_json:
            return jsonify({"ok": False, "error": msg}), 400
        flash(msg, "error")
        return redirect(redirect_url)

    if action == "email":
        report = get_day_report(user.id, day_s)
        if report is None or not (report.summary or "").strip():
            if wants_json:
                return jsonify({"ok": False, "error": "Generate an AI report first."}), 400
            flash("Generate an AI report first.", "error")
            return redirect(redirect_url)
        ok, err = email_ai_report(report, user, force=True)
        if wants_json:
            if ok:
                return jsonify({"ok": True, **report_to_dict(get_day_report(user.id, day_s))})
            return jsonify({"ok": False, "error": err}), 500
        if ok:
            flash("AI report emailed to admins.", "info")
        else:
            flash(f"Could not email AI report: {err}", "error")
        return redirect(redirect_url)

    ok, msg = queue_ai_report_generation(user, day)
    if wants_json:
        status = 200 if ok else 400
        return jsonify(
            {
                "ok": ok,
                "message": msg,
                **report_to_dict(get_day_report(user.id, day_s)),
            }
        ), status
    flash(msg, "info" if ok else "error")
    return redirect(redirect_url)


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
    categorizer = RulesConfig(rules=merge_rules(_load_server_rules(), org_only=True))
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
        from .settings_util import persist_category_rules
        from .tenancy import current_org_id

        org_id = current_org_id()
        app_rows = list(
            zip(request.form.getlist("app_name"), request.form.getlist("app_category"))
        )
        site_rows = list(
            zip(request.form.getlist("site_name"), request.form.getlist("site_category"))
        )
        changed = persist_category_rules(org_id, app_rows, site_rows)
        flash(
            f"Categories saved. Updated {changed} activities (last 90 days) — "
            "user bars and scores now match your choices.",
            "info",
        )
        return redirect(url_for("views.settings_rules"))

    tracked_apps, tracked_sites = _tracked_for_categories()
    counts = {
        "apps": len(tracked_apps),
        "sites": len(tracked_sites),
        "productive": sum(1 for x in tracked_apps + tracked_sites if x["category"] == PRODUCTIVE),
        "unproductive": sum(
            1 for x in tracked_apps + tracked_sites if x["category"] == UNPRODUCTIVE
        ),
        "neutral": sum(1 for x in tracked_apps + tracked_sites if x["category"] == NEUTRAL),
    }
    return render_template(
        "settings_rules.html",
        tracked_apps=tracked_apps,
        tracked_sites=tracked_sites,
        counts=counts,
    )


# ----- Employees (admin) -----


def _employees_return_url() -> str:
    args: dict[str, str | int] = {}
    for key in ("day", "q"):
        val = (request.form.get(key) or request.args.get(key) or "").strip()
        if val:
            args[key] = val
    tid = request.form.get("team_id", type=int) or request.args.get("team_id", type=int)
    if tid:
        args["team_id"] = tid
    return url_for("views.employees", **args)


@views_bp.route("/employees/<int:user_id>/settings", methods=["GET", "POST"])
@admin_required
def employee_settings(user_id: int):
    """DeskTime-style per-employee settings (General / Tracking / Screenshots / …)."""
    from .models import Team
    from .tenancy import current_org_id, teams_in_org_query

    subject = _org_user_or_404(user_id)
    tab = (request.values.get("tab") or "general").strip().lower()
    allowed_tabs = {"general", "tracking", "screenshots", "security", "other"}
    if tab not in allowed_tabs:
        tab = "general"

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        tab = (request.form.get("tab") or tab).strip().lower()
        if tab not in allowed_tabs:
            tab = "general"

        if action == "general":
            subject.display_name = (request.form.get("name") or "").strip()
            subject.email = (request.form.get("email") or "").strip()
            tid = request.form.get("team_id", type=int)
            if tid:
                team = db.session.get(Team, tid)
                if team is None or team.organization_id != current_org_id():
                    abort(404)
                subject.team_id = tid
            else:
                subject.team_id = None
            role = (request.form.get("role") or "").strip()
            if role in (ROLE_ADMIN, ROLE_EMPLOYEE):
                if subject.id == current_user.id and role != subject.role:
                    flash("You cannot change your own role.", "error")
                else:
                    subject.role = role
            if not _apply_timezone_field(subject):
                return redirect(
                    url_for("views.employee_settings", user_id=subject.id, tab="general")
                )
            db.session.commit()
            flash(f"Profile updated for {subject.username}.", "info")
        elif action == "tracking":
            subject.private_time_allowed = bool(request.form.get("private_time_allowed"))
            db.session.commit()
            flash(f"Tracking settings saved for {subject.username}.", "info")
        elif action == "screenshots":
            subject.view_own_screenshots = bool(request.form.get("view_own_screenshots"))
            mode = request.form.get("shot_mode") or "company"
            if mode == "company":
                subject.screenshots_enabled = None
                subject.screenshot_interval = None
            elif mode == "off":
                subject.screenshots_enabled = False
                subject.screenshot_interval = None
            else:
                subject.screenshots_enabled = True
                interval = request.form.get("interval", type=int) or 300
                subject.screenshot_interval = max(60, min(interval, 3600))
            db.session.commit()
            flash(f"Screenshot settings saved for {subject.username}.", "info")
        elif action == "password":
            password = request.form.get("password") or ""
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
            else:
                subject.set_password(password)
                db.session.commit()
                flash(f"Password updated for {subject.username}.", "info")
        elif action == "rotate":
            token = subject.rotate_token()
            db.session.commit()
            flash(f"New API token for {subject.username}: {token}", "info")
        elif action == "toggle":
            if subject.id == current_user.id:
                flash("You cannot disable your own account.", "error")
            else:
                subject.enabled = not subject.enabled
                db.session.commit()
                flash(
                    f"{subject.username} is now {'enabled' if subject.enabled else 'disabled'}.",
                    "info",
                )
        elif action == "delete":
            from .users_util import delete_user_account

            if subject.id == current_user.id:
                flash("You cannot delete your own account.", "error")
            elif subject.organization_id != current_org_id():
                flash("Employee not found in your organization.", "error")
            elif subject.role == ROLE_ADMIN:
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
                    delete_user_account(subject)
                    flash("Deleted team member.", "info")
                    return redirect(url_for("views.employees"))
            else:
                delete_user_account(subject)
                flash("Deleted team member.", "info")
                return redirect(url_for("views.employees"))
        return redirect(url_for("views.employee_settings", user_id=user_id, tab=tab))

    teams = list(
        db.session.execute(
            teams_in_org_query(current_org_id()).order_by(Team.name)
        ).scalars()
    )
    return render_template(
        "employee_settings.html",
        subject=subject,
        teams=teams,
        tab=tab,
        company_settings=get_settings(),
    )


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
        elif action == "view_own_shots":
            uid = request.form.get("user_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            user.view_own_screenshots = not user.view_own_screenshots
            db.session.commit()
            flash(
                f"My screenshots {'on' if user.view_own_screenshots else 'off'} for {user.username}.",
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
            if _apply_timezone_field(user):
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
        return redirect(_employees_return_url())

    from .models import Team
    from .tenancy import current_org_id, teams_in_org_query, users_in_org_query

    day = _parse_day(request.args.get("day"))
    team_id = request.args.get("team_id", type=int)
    start, end = day_bounds(day, tz_name=_tz())
    off_day = _is_off_day(day)
    expected = _expected_seconds()

    q = request.args.get("q", "").strip().lower()
    users = list(
        db.session.execute(
            users_in_org_query(current_org_id()).order_by(User.role.desc(), User.username)
        ).scalars()
    )
    if team_id:
        users = [u for u in users if u.team_id == team_id]
    if q:
        users = [
            u
            for u in users
            if q in u.username.lower()
            or q in (u.display_name or "").lower()
            or q in (u.email or "").lower()
        ]
    teams = list(
        db.session.execute(
            teams_in_org_query(current_org_id()).order_by(Team.name)
        ).scalars()
    )
    now_ts = datetime.now().timestamp()
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    priv_map = _active_private_map([u.id for u in users])
    rows = []
    metrics_by_id: dict[int, dict] = {}
    for u in users:
        last = _last_seen(u.id)
        online = _is_online(u, last, now_ts, cfg.online_window)
        pp = priv_map.get(u.id)
        rows.append(
            {
                "user": u,
                "online": online,
                "last_seen": last,
                "private_active": pp is not None,
                "private_ago": _private_duration_label(pp.start_ts, now_ts) if pp else "",
            }
        )
        acts = _activities_for(u.id, day)
        manual = _manual_for(u.id, day)
        dm = compute_day_metrics(acts, manual, start, end, expected)
        s = dm.summary
        manual_seconds = dm.manual_total_seconds
        desktime_seconds = dm.desktime_seconds
        late = _is_late(s.arrival_ts, day)
        absent = (not off_day) and s.total_seconds <= 0 and manual_seconds <= 0
        live = _live_snapshot(u.id, now_ts=now_ts, online_window=cfg.online_window)
        if not online and isinstance(live, dict):
            live["fresh"] = False
        metrics_by_id[u.id] = {
            "summary": s,
            "desktime_seconds": desktime_seconds,
            "online": online,
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
            "early": _is_early(s.arrival_ts, day) and not absent,
            "absent": absent,
            "dt_productivity": dm.dt_productivity,
            "dt_effectiveness": dm.dt_effectiveness,
        }
    return render_template(
        "employees.html",
        users=users,
        teams=teams,
        rows=rows,
        q=q,
        humanize=humanize,
        team_id=team_id,
        off_day=off_day,
        metrics_by_id=metrics_by_id,
        **_day_nav(day),
    )


# ----- Screenshots browser (DeskTime gallery) -----


# Browsers cached an old nginx 301 to /screenshots/ for a day; serve both so it can't loop.
@views_bp.route("/screenshots/")
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
    scope = request.args.get("scope") or "team"
    selected_user = request.args.get("user_id", type=int)
    team_id = request.args.get("team_id", type=int)
    expected = _expected_seconds()
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    now_ts = datetime.now().timestamp()
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
            if team_id:
                users_q = [u for u in users_q if u.team_id == team_id]
            dm_list = [_day_metrics(u.id, d) for u in users_q]
            active = sum(dm.summary.active_seconds for dm in dm_list)
            productive = sum(dm.summary.productive_seconds for dm in dm_list)
            idle = sum(dm.summary.idle_seconds for dm in dm_list)
            unproductive = sum(dm.summary.unproductive_seconds for dm in dm_list)
            present = sum(1 for dm in dm_list if dm.summary.total_seconds > 0)
            late = sum(
                1
                for dm in dm_list
                if dm.summary.total_seconds > 0 and _is_late(dm.summary.arrival_ts, d)
            )
            tracked_n = max(1, sum(1 for dm in dm_list if dm.desktime_seconds > 0))
            pct = (
                round(
                    sum(dm.dt_productivity for dm in dm_list if dm.desktime_seconds > 0)
                    / tracked_n,
                    1,
                )
                if any(dm.desktime_seconds > 0 for dm in dm_list)
                else 0.0
            )
            eff = (
                round(
                    sum(
                        dm.dt_effectiveness
                        for dm in dm_list
                        if dm.desktime_seconds > 0
                    )
                    / tracked_n,
                    1,
                )
                if any(dm.desktime_seconds > 0 for dm in dm_list)
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
            dm = _day_metrics(uid, d)
            s = dm.summary
            row = {
                "day": d.strftime("%Y-%m-%d"),
                "label": d.strftime("%a %d"),
                "active": s.active_seconds,
                "productive": s.productive_seconds,
                "idle": s.idle_seconds,
                "unproductive": s.unproductive_seconds,
                "pct": dm.dt_productivity,
                "eff": dm.dt_effectiveness,
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

            team_users_for_apps = list(
                db.session.execute(
                    users_in_org_query(current_user.organization_id or 1)
                    .filter(User.role != ROLE_ADMIN)
                    .filter(User.enabled.is_(True))
                ).scalars()
            )
            if team_id:
                team_users_for_apps = [
                    u for u in team_users_for_apps if u.team_id == team_id
                ]
            ids = [u.id for u in team_users_for_apps]
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
    teams: list = []
    team_viz: dict | None = None
    if current_user.is_admin:
        from .models import Team
        from .tenancy import current_org_id, teams_in_org_query, users_in_org_query

        teams = list(
            db.session.execute(
                teams_in_org_query(current_org_id()).order_by(Team.name)
            ).scalars()
        )
        users = list(
            db.session.execute(
                users_in_org_query(current_org_id())
                .filter(User.role != ROLE_ADMIN)
                .order_by(User.username)
            ).scalars()
        )

    if scope == "team":
        from .tenancy import current_org_id, users_in_org_query

        team_users = list(
            db.session.execute(
                users_in_org_query(current_org_id())
                .filter(User.role != ROLE_ADMIN)
                .filter(User.enabled.is_(True))
                .order_by(User.username.asc())
            ).scalars()
        )
        if team_id:
            team_users = [u for u in team_users if u.team_id == team_id]
        rows, team_agg, team_apps_day, day_avg_prod, day_avg_eff, off_day = (
            _admin_team_day_state(
                team_users,
                day,
                now_ts=now_ts,
                online_window=cfg.online_window,
            )
        )
        for r in rows:
            r.pop("_acts", None)
        viz_q = f"&scope=team&range={range_days}"
        if team_id:
            viz_q += f"&team_id={team_id}"
        team_viz = _team_visual_viz_context(
            users=team_users,
            rows=rows,
            team=team_agg,
            team_apps=team_apps_day,
            day=day,
            avg_prod=day_avg_prod,
            avg_eff=day_avg_eff,
            off_day=off_day,
            expected_hours=expected / 3600.0,
            trend_range=range_days,
            team_id=team_id,
            viz_day_query=viz_q,
            viz_apps_anchor="#report-top-apps",
            viz_emp_anchor="#report-daily-breakdown",
        )
        if request.args.get("partial") == "viz":
            return render_template("_admin_visual_grid.html", **team_viz)

    export_uid = selected_user or current_user.id
    report_ctx: dict = {
        "days": days,
        "max_pct": max_pct,
        **_day_nav(day),
        "humanize": humanize,
        "users": users,
        "teams": teams,
        "team_id": team_id,
        "scope": scope,
        "selected_user": selected_user,
        "week_productive": week_productive,
        "week_active": week_active,
        "week_idle": week_idle,
        "avg_prod": avg_prod,
        "avg_eff": avg_eff,
        "top_apps": top_apps,
        "export_uid": export_uid,
        "expected_hours": expected / 3600.0,
        "range_days": range_days,
        "team_viz": team_viz,
    }
    if team_viz:
        report_ctx.update(team_viz)
    return render_template("reports.html", **report_ctx)


@views_bp.route("/data/charts")
@login_required
def data_charts():
    user_id = request.args.get("user_id", type=int) or current_user.id
    if user_id != current_user.id and not current_user.is_admin:
        abort(403)
    day = _parse_day(request.args.get("day"))
    return jsonify(
        _chart_payload(
            _activities_for(user_id, day),
            day,
            _manual_for(user_id, day),
        )
    )


def _serve_shot(shot_id: int, thumb: bool):
    shot = db.session.get(Screenshot, shot_id) or abort(404)
    if shot.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    rel = shot.thumb_path if (thumb and shot.thumb_path) else shot.path
    # Normalize legacy Windows backslash paths stored in the DB.
    rel = (rel or "").replace("\\", "/")
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


@views_bp.app_template_filter("dayclock")
def _dayclock(ts: float | None) -> str:
    if not ts:
        return "—"
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(_tz())
    except Exception:
        tz = None
    day = datetime.fromtimestamp(ts, tz).strftime("%b %d").replace(" 0", " ")
    return f"{day}, {format_clock(ts, tz_name=_tz())}"


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
