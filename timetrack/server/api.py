"""Agent-facing ingest API (token authenticated).

Agents authenticate with ``Authorization: Bearer <api_token>``.

Endpoints (all under ``/api/v1``):
- ``GET  /ping``          -> verify token, return the identified user
- ``POST /activities``    -> batch upload of activity spans (JSON)
- ``POST /screenshots``   -> upload one screenshot (multipart form)
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request

from .extensions import db
from .models import Activity, Screenshot, User

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


@api_bp.route("/ping")
@token_required
def ping():
    u: User = g.agent_user
    return jsonify({"ok": True, "user": u.username, "role": u.role, "name": u.name})


@api_bp.route("/activities", methods=["POST"])
@token_required
def ingest_activities():
    u: User = g.agent_user
    payload = request.get_json(silent=True) or {}
    items = payload.get("activities")
    if not isinstance(items, list):
        return jsonify({"error": "expected 'activities' list"}), 400

    accepted = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            start_ts = float(item["start_ts"])
            end_ts = float(item["end_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        duration = float(item.get("duration", max(0.0, end_ts - start_ts)))
        category = str(item.get("category", "neutral"))
        if category not in _VALID_CATEGORIES:
            category = "neutral"
        db.session.add(
            Activity(
                user_id=u.id,
                app=str(item.get("app", "unknown"))[:255],
                title=str(item.get("title", ""))[:2000],
                category=category,
                idle=bool(item.get("idle", False)),
                start_ts=start_ts,
                end_ts=end_ts,
                duration=duration,
            )
        )
        accepted += 1

    db.session.commit()
    return jsonify({"accepted": accepted}), 201


@api_bp.route("/screenshots", methods=["POST"])
@token_required
def ingest_screenshot():
    u: User = g.agent_user
    file = request.files.get("image")
    if file is None:
        return jsonify({"error": "missing 'image' file"}), 400

    ts = float(request.form.get("ts", time.time()))
    app_name = (request.form.get("app", "") or "")[:255]
    title = (request.form.get("title", "") or "")[:2000]

    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    rel_dir = os.path.join(str(u.id), day)
    abs_dir = os.path.join(cfg.screenshots_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    name = f"{uuid.uuid4().hex}.jpg"
    rel_path = os.path.join(rel_dir, name)
    abs_path = os.path.join(abs_dir, name)
    data = file.read()
    with open(abs_path, "wb") as fh:
        fh.write(data)

    # Best-effort thumbnail.
    thumb_rel = ""
    try:
        from ..platform.screenshot import make_thumbnail

        thumb = make_thumbnail(data)
        if thumb:
            thumb_name = f"thumb_{name}"
            with open(os.path.join(abs_dir, thumb_name), "wb") as fh:
                fh.write(thumb)
            thumb_rel = os.path.join(rel_dir, thumb_name)
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
    )
    db.session.add(shot)
    db.session.commit()
    return jsonify({"id": shot.id}), 201


__all__ = ["api_bp", "token_required"]
