"""Flask web dashboard for viewing tracked activity."""

from __future__ import annotations

from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request

from ..analytics import (
    day_bounds,
    humanize,
    summarize,
    summarize_day,
    timeline_buckets,
)
from ..config import Config, load_config
from ..security import DASHBOARD_CSP, apply_security_headers
from ..storage import Storage


def _parse_day(value: str | None) -> datetime:
    if not value:
        return datetime.now()
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return datetime.now()


def create_app(config: Config | None = None) -> Flask:
    cfg = config or load_config()
    app = Flask(__name__)
    app.config["TIMETRACK_CONFIG"] = cfg
    apply_security_headers(app, csp=DASHBOARD_CSP)

    def get_storage() -> Storage:
        return Storage(cfg.db_path)

    @app.route("/")
    def index() -> str:
        day = _parse_day(request.args.get("day"))
        with get_storage() as storage:
            summary = summarize_day(storage, day)

        prev_day = (day - timedelta(days=1)).strftime("%Y-%m-%d")
        next_day = (day + timedelta(days=1)).strftime("%Y-%m-%d")
        return render_template(
            "dashboard.html",
            day=day.strftime("%Y-%m-%d"),
            prev_day=prev_day,
            next_day=next_day,
            summary=summary,
            humanize=humanize,
            backend=cfg.db_path,
        )

    @app.route("/api/summary")
    def api_summary():
        day = _parse_day(request.args.get("day"))
        with get_storage() as storage:
            summary = summarize_day(storage, day)
        return jsonify(
            {
                "day": day.strftime("%Y-%m-%d"),
                "total_seconds": summary.total_seconds,
                "active_seconds": summary.active_seconds,
                "idle_seconds": summary.idle_seconds,
                "productive_seconds": summary.productive_seconds,
                "unproductive_seconds": summary.unproductive_seconds,
                "neutral_seconds": summary.neutral_seconds,
                "productivity_pct": summary.productivity_pct,
                "effectiveness_pct": summary.effectiveness_pct,
                "span_seconds": summary.span_seconds,
                "apps": [
                    {"app": a.app, "category": a.category, "seconds": a.seconds}
                    for a in summary.apps
                ],
            }
        )

    @app.route("/api/timeline")
    def api_timeline():
        day = _parse_day(request.args.get("day"))
        start, end = day_bounds(day)
        with get_storage() as storage:
            activities = storage.query(start, end)
        buckets = timeline_buckets(activities, start, end)
        return jsonify({"day": day.strftime("%Y-%m-%d"), "buckets": buckets})

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    return app


__all__ = ["create_app"]
