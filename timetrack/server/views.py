"""Web views: home routing, admin dashboard, user dashboard, screenshot serving."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_from_directory,
)
from flask_login import current_user, login_required

from ..analytics import day_bounds, humanize, summarize, timeline_buckets
from .auth import admin_required
from .extensions import db
from .models import Activity, Screenshot, User

views_bp = Blueprint("views", __name__)


def _parse_day(value: str | None) -> datetime:
    if value:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now()


def _activities_for(user_id: int, day: datetime) -> list[Activity]:
    start, end = day_bounds(day)
    return list(
        db.session.execute(
            db.select(Activity)
            .filter(Activity.user_id == user_id)
            .filter(Activity.end_ts >= start, Activity.start_ts < end)
            .order_by(Activity.start_ts.asc())
        ).scalars()
    )


def _screenshots_for(user_id: int, day: datetime) -> list[Screenshot]:
    start, end = day_bounds(day)
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


def _chart_payload(activities: list[Activity], day: datetime) -> dict:
    start, end = day_bounds(day)
    summary = summarize(activities)
    buckets = timeline_buckets(activities, start, end, bucket_seconds=3600.0)
    labels = [
        datetime.fromtimestamp(b["start"]).strftime("%H:%M") for b in buckets
    ]
    return {
        "labels": labels,
        "productive": [round(b["productive"] / 60.0, 1) for b in buckets],
        "unproductive": [round(b["unproductive"] / 60.0, 1) for b in buckets],
        "neutral": [round(b["neutral"] / 60.0, 1) for b in buckets],
        "idle": [round(b["idle"] / 60.0, 1) for b in buckets],
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
    }


@views_bp.route("/")
@login_required
def home():
    from flask import redirect, url_for

    if current_user.is_admin:
        return redirect(url_for("views.admin"))
    return redirect(url_for("views.me"))


@views_bp.route("/me")
@login_required
def me():
    day = _parse_day(request.args.get("day"))
    return _render_user_dashboard(current_user, day, is_self=True)


@views_bp.route("/admin")
@admin_required
def admin():
    day = _parse_day(request.args.get("day"))
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    now_ts = datetime.now().timestamp()

    users = list(
        db.session.execute(
            db.select(User).order_by(User.role.desc(), User.username.asc())
        ).scalars()
    )

    rows = []
    team = {"active": 0.0, "productive": 0.0, "idle": 0.0, "online": 0}
    for u in users:
        acts = _activities_for(u.id, day)
        s = summarize(acts)
        last = _last_seen(u.id)
        online = last is not None and (now_ts - last) <= cfg.online_window
        rows.append(
            {
                "user": u,
                "summary": s,
                "online": online,
                "last_seen": last,
                "shots": db.session.execute(
                    db.select(db.func.count(Screenshot.id)).filter(
                        Screenshot.user_id == u.id
                    )
                ).scalar_one(),
            }
        )
        team["active"] += s.active_seconds
        team["productive"] += s.productive_seconds
        team["idle"] += s.idle_seconds
        team["online"] += 1 if online else 0

    return render_template(
        "admin.html",
        day=day.strftime("%Y-%m-%d"),
        prev_day=(day - timedelta(days=1)).strftime("%Y-%m-%d"),
        next_day=(day + timedelta(days=1)).strftime("%Y-%m-%d"),
        rows=rows,
        team=team,
        humanize=humanize,
    )


@views_bp.route("/admin/user/<int:user_id>")
@admin_required
def admin_user(user_id: int):
    user = db.session.get(User, user_id) or abort(404)
    day = _parse_day(request.args.get("day"))
    return _render_user_dashboard(user, day, is_self=False)


def _render_user_dashboard(user: User, day: datetime, *, is_self: bool):
    acts = _activities_for(user.id, day)
    summary = summarize(acts)
    shots = _screenshots_for(user.id, day)
    charts = _chart_payload(acts, day)
    return render_template(
        "user.html",
        subject=user,
        is_self=is_self,
        day=day.strftime("%Y-%m-%d"),
        prev_day=(day - timedelta(days=1)).strftime("%Y-%m-%d"),
        next_day=(day + timedelta(days=1)).strftime("%Y-%m-%d"),
        summary=summary,
        screenshots=shots,
        charts=charts,
        humanize=humanize,
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
    if not ts:
        return "--"
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


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
