"""Render branded HTML + plain-text emails from Jinja templates."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app, url_for


def _tracker_base_url() -> str:
    try:
        return url_for("views.home", _external=True).rstrip("/")
    except RuntimeError:
        return "https://tracker.euclideesolutions.com"


def _brand_logo_url() -> str:
    try:
        return url_for("static", filename="brand/ess-mark.png", _external=True)
    except RuntimeError:
        return ""


def render_email(template: str, **ctx) -> tuple[str, str]:
    """Return (plain_text, html) for ``emails/{template}.txt`` and ``emails/{template}.html``."""
    ctx.setdefault("tracker_url", _tracker_base_url())
    ctx.setdefault("logo_url", _brand_logo_url())
    ctx.setdefault("year", datetime.now(timezone.utc).year)
    env = current_app.jinja_env
    text = env.get_template(f"emails/{template}.txt").render(ctx).strip()
    html = env.get_template(f"emails/{template}.html").render(ctx)
    return text, html
