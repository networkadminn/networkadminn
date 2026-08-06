"""Agent-facing ingest API (token authenticated) — DeskTime-aligned.

Agents authenticate with ``Authorization: Bearer <api_token>``.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request

from .extensions import db
from .models import (
    Activity,
    PrivatePeriod,
    Project,
    Screenshot,
    Task,
    TimerSession,
    User,
)
from .settings_util import company_tz_name, get_settings
from ..tzutil import is_work_day, now_tz, today_tz, zone

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

_VALID_CATEGORIES = {"productive", "unproductive", "neutral"}


def _token_from_request() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-Api-Token")


def token_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        token = _token_from_request()
        if not token:
            return jsonify({"error": "missing token"}), 401
        user = db.session.execute(
            db.select(User).filter_by(api_token=token)
        ).scalar_one_or_none()
        if user is None or not user.enabled:
            return jsonify({"error": "invalid token"}), 401
        g.agent_user = user
        return view(*args, **kwargs)

    return wrapped


@api_bp.route("/agent/login", methods=["POST"])
def agent_login():
    """DeskTime-style desktop login: username + password → API token.

    No prior token required. Used by the employee .deb first-run window.
    """
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    user = db.session.execute(
        db.select(User).filter_by(username=username)
    ).scalar_one_or_none()
    if user is None or not user.enabled or not user.check_password(password):
        return jsonify({"error": "invalid username or password"}), 401

    # Ensure a token always exists
    if not user.api_token:
        user.rotate_token()
        db.session.commit()

    settings = get_settings(user.organization_id or 1)
    return jsonify(
        {
            "ok": True,
            "api_token": user.api_token,
            "user": user.username,
            "name": user.name,
            "role": user.role,
            "organization_id": user.organization_id or 1,
            "company": settings.company_name or "Euclidee Software Solutions",
        }
    )


@api_bp.route("/ping")
@token_required
def ping():
    u: User = g.agent_user
    settings = get_settings(u.organization_id or 1)
    private = db.session.execute(
        db.select(PrivatePeriod)
        .filter_by(user_id=u.id, active=True)
        .order_by(PrivatePeriod.start_ts.desc())
    ).scalar_one_or_none()
    timer = db.session.execute(
        db.select(TimerSession)
        .filter_by(user_id=u.id, running=True)
        .order_by(TimerSession.start_ts.desc())
    ).scalar_one_or_none()
    # Push company category rules so agents stay in sync with the web UI.
    from .settings_util import load_server_rules

    rules = load_server_rules(u.organization_id or 1)
    # Per-user screenshot overrides sit on top of company policy.
    shot_enabled = settings.screenshots_enabled
    if u.screenshots_enabled is not None:
        shot_enabled = bool(u.screenshots_enabled)
    shot_interval = settings.screenshot_interval
    if u.screenshot_interval:
        shot_interval = int(u.screenshot_interval)
    return jsonify(
        {
            "ok": True,
            "user": u.username,
            "role": u.role,
            "name": u.name,
            "company": settings.company_name or "Euclidee Software Solutions",
            "private_active": bool(private),
            "private_allowed": bool(u.private_time_allowed and settings.private_time_enabled),
            "screenshots": {
                "enabled": shot_enabled,
                "blur": settings.screenshot_blur,
                "interval": shot_interval,
                "random": settings.screenshot_random,
            },
            "idle_threshold": int(settings.idle_threshold or 180),
            "timezone": settings.timezone or "Asia/Kolkata",
            "server_time": time.time(),
            "server_time_iso": now_tz(settings.timezone or "Asia/Kolkata").isoformat(
                timespec="seconds"
            ),
            "office_start_hour": float(
                settings.office_start_hour or settings.expected_arrival_hour or 9.5
            ),
            "office_end_hour": float(settings.office_end_hour or 18.5),
            "work_days": settings.work_days or "0,1,2,3,4",
            "track_today": is_work_day(
                today_tz(settings.timezone or "Asia/Kolkata"),
                settings.work_days,
            ),
            "rules": rules,
            "timer": None
            if timer is None
            else {
                "id": timer.id,
                "project_id": timer.project_id,
                "task_id": timer.task_id,
                "start_ts": timer.start_ts,
                "note": timer.note,
            },
        }
    )


@api_bp.route("/private", methods=["GET", "POST"])
@token_required
def private_time():
    u: User = g.agent_user
    settings = get_settings(u.organization_id or 1)
    active = db.session.execute(
        db.select(PrivatePeriod)
        .filter_by(user_id=u.id, active=True)
        .order_by(PrivatePeriod.start_ts.desc())
    ).scalar_one_or_none()

    if request.method == "GET":
        return jsonify(
            {
                "active": bool(active),
                "allowed": bool(u.private_time_allowed and settings.private_time_enabled),
                "start_ts": active.start_ts if active else None,
            }
        )

    if not (u.private_time_allowed and settings.private_time_enabled):
        return jsonify({"error": "private time disabled"}), 403

    payload = request.get_json(silent=True) or {}
    enable = bool(payload.get("active", True))
    now = time.time()

    if enable:
        if active is None:
            db.session.add(
                PrivatePeriod(user_id=u.id, start_ts=now, end_ts=None, active=True)
            )
            db.session.commit()
        return jsonify({"active": True})

    if active is not None:
        active.active = False
        active.end_ts = now
        db.session.commit()
    return jsonify({"active": False})


@api_bp.route("/timer", methods=["GET", "POST"])
@token_required
def timer():
    u: User = g.agent_user
    running = db.session.execute(
        db.select(TimerSession)
        .filter_by(user_id=u.id, running=True)
        .order_by(TimerSession.start_ts.desc())
    ).scalar_one_or_none()

    if request.method == "GET":
        if running is None:
            return jsonify({"running": False})
        return jsonify(
            {
                "running": True,
                "id": running.id,
                "project_id": running.project_id,
                "task_id": running.task_id,
                "start_ts": running.start_ts,
                "note": running.note,
            }
        )

    payload = request.get_json(silent=True) or {}
    action = (payload.get("action") or "start").lower()
    now = time.time()

    if action == "stop":
        if running is not None:
            from .time_util import finalize_timer_session

            finalize_timer_session(running, now)
            db.session.commit()
        return jsonify({"running": False})

    # start (stop any previous)
    if running is not None:
        from .time_util import finalize_timer_session

        finalize_timer_session(running, now)
    project_id = payload.get("project_id")
    task_id = payload.get("task_id")
    note = str(payload.get("note") or "")[:500]
    sess = TimerSession(
        user_id=u.id,
        project_id=int(project_id) if project_id else None,
        task_id=int(task_id) if task_id else None,
        note=note,
        start_ts=now,
        running=True,
    )
    db.session.add(sess)
    db.session.commit()
    return jsonify(
        {
            "running": True,
            "id": sess.id,
            "project_id": sess.project_id,
            "task_id": sess.task_id,
            "start_ts": sess.start_ts,
        }
    )


@api_bp.route("/projects")
@token_required
def list_projects():
    from .tenancy import projects_in_org_query

    u: User = g.agent_user
    projects = db.session.execute(
        projects_in_org_query(u.organization_id or 1)
        .filter(Project.active.is_(True))
        .order_by(Project.name)
    ).scalars()
    out = []
    for p in projects:
        tasks = [
            {"id": t.id, "name": t.name}
            for t in db.session.execute(
                db.select(Task).filter_by(project_id=p.id, active=True).order_by(Task.name)
            ).scalars()
        ]
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "color": p.color,
                "tasks": tasks,
            }
        )
    return jsonify({"projects": out})


@api_bp.route("/activities", methods=["POST"])
@token_required
def ingest_activities():
    u: User = g.agent_user
    # Reject (not 2xx) while private so the agent keeps buffered rows for retry.
    private = db.session.execute(
        db.select(PrivatePeriod).filter_by(user_id=u.id, active=True)
    ).scalar_one_or_none()
    if private is not None:
        return jsonify({"accepted": 0, "private": True, "error": "private_time"}), 409

    payload = request.get_json(silent=True) or {}
    items = payload.get("activities")
    if not isinstance(items, list):
        return jsonify({"error": "expected 'activities' list"}), 400

    timer = db.session.execute(
        db.select(TimerSession).filter_by(user_id=u.id, running=True)
    ).scalar_one_or_none()

    accepted = 0
    from ..config import Config as CoreConfig, merge_rules
    from .settings_util import load_server_rules

    server_rules = merge_rules(load_server_rules(u.organization_id or 1))
    categorizer = CoreConfig(rules=server_rules)

    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            start_ts = float(item["start_ts"])
            end_ts = float(item["end_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        duration = float(item.get("duration", max(0.0, end_ts - start_ts)))
        app = str(item.get("app", "unknown"))[:255]
        title = str(item.get("title", ""))[:2000]
        url = str(item.get("url", "") or "")[:500]
        idle = bool(item.get("idle", False))
        if idle:
            category = "neutral"
        else:
            # Server rules are source of truth (apps + website domains).
            category = categorizer.categorize(app, title, url=url)
        project_id = item.get("project_id")
        task_id = item.get("task_id")
        if project_id is None and timer is not None:
            project_id = timer.project_id
            task_id = timer.task_id
        db.session.add(
            Activity(
                user_id=u.id,
                app=app,
                title=title,
                url=url,
                category=category,
                idle=idle,
                start_ts=start_ts,
                end_ts=end_ts,
                duration=duration,
                project_id=int(project_id) if project_id else None,
                task_id=int(task_id) if task_id else None,
            )
        )
        accepted += 1

    db.session.commit()
    return jsonify({"accepted": accepted}), 201


@api_bp.route("/screenshots", methods=["POST"])
@token_required
def ingest_screenshot():
    u: User = g.agent_user
    settings = get_settings(u.organization_id or 1)
    if not settings.screenshots_enabled:
        return jsonify({"skipped": True, "reason": "disabled"}), 202

    private = db.session.execute(
        db.select(PrivatePeriod).filter_by(user_id=u.id, active=True)
    ).scalar_one_or_none()
    if private is not None:
        return jsonify({"skipped": True, "reason": "private"}), 409

    file = request.files.get("image")
    if file is None:
        return jsonify({"error": "missing 'image' file"}), 400

    ts = float(request.form.get("ts", time.time()))
    app_name = (request.form.get("app", "") or "")[:255]
    title = (request.form.get("title", "") or "")[:2000]
    blurred = request.form.get("blurred", "0") in ("1", "true", "True")
    is_unproductive = request.form.get("is_unproductive", "0") in ("1", "true", "True")
    # Categorize with company rules from the server.
    if not is_unproductive and app_name:
        from ..config import UNPRODUCTIVE, merge_rules, Config as CoreConfig
        from .settings_util import load_server_rules

        cfg_rules = merge_rules(load_server_rules(u.organization_id or 1))
        if CoreConfig(rules=cfg_rules).categorize(app_name, title) == UNPRODUCTIVE:
            is_unproductive = True

    from .settings_util import org_screenshot_dir

    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    tz = zone(company_tz_name())
    day = datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d")
    org_root = org_screenshot_dir(u.organization_id or 1)
    # Forward-slash relative paths so Flask send_from_directory works on Windows.
    rel_dir = f"{u.id}/{day}"
    abs_dir = os.path.join(org_root, str(u.id), day)
    os.makedirs(abs_dir, exist_ok=True)

    name = f"{uuid.uuid4().hex}.jpg"
    # Keep DB path relative to screenshots_dir for existing serve logic
    rel_path = f"org-{u.organization_id or 1}/{rel_dir}/{name}"
    abs_path = os.path.join(cfg.screenshots_dir, *rel_path.split("/"))
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    data = file.read()
    with open(abs_path, "wb") as fh:
        fh.write(data)

    thumb_rel = ""
    try:
        from ..platform.screenshot import make_thumbnail

        thumb = make_thumbnail(data)
        if thumb:
            thumb_name = f"thumb_{name}"
            with open(os.path.join(abs_dir, thumb_name), "wb") as fh:
                fh.write(thumb)
            thumb_rel = f"org-{u.organization_id or 1}/{rel_dir}/{thumb_name}"
    except Exception:
        pass

    width = int(request.form.get("width", 0) or 0)
    height = int(request.form.get("height", 0) or 0)

    shot = Screenshot(
        user_id=u.id,
        ts=ts,
        path=rel_path,
        thumb_path=thumb_rel,
        width=width,
        height=height,
        app=app_name,
        title=title,
        blurred=blurred,
        is_unproductive=is_unproductive,
    )
    db.session.add(shot)
    db.session.commit()
    return jsonify({"id": shot.id}), 201


__all__ = ["api_bp", "token_required"]
