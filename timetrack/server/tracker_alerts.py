"""Email alerts when an employee tracker stops."""

from __future__ import annotations

import time

from flask import current_app

from .extensions import db
from .mail import send_email
from .models import ROLE_ADMIN, ROLE_EMPLOYEE, ROLE_SUPERADMIN, User
from .settings_util import get_settings


def touch_agent_ping(user: User) -> None:
    """Record latest agent heartbeat (ping or activity sync)."""
    user.last_agent_ping = time.time()


def _alert_recipients(org_id: int, settings) -> list[str]:
    raw = (getattr(settings, "tracker_alert_emails", None) or "").strip()
    if raw:
        return [e.strip() for e in raw.replace(";", ",").split(",") if "@" in e.strip()]
    admins = db.session.execute(
        db.select(User).filter(
            User.organization_id == org_id,
            User.enabled.is_(True),
            User.role.in_((ROLE_ADMIN, ROLE_SUPERADMIN)),
        )
    ).scalars()
    return [u.email.strip() for u in admins if (u.email or "").strip()]


def send_tracker_stop_alert(user: User, *, reason: str = "stopped") -> None:
    """Notify admins that an employee tracker went offline."""
    settings = get_settings(user.organization_id or 1)
    if not getattr(settings, "alert_on_tracker_stop", True):
        return
    now = time.time()
    if user.tracker_stop_alerted_at and user.last_agent_ping:
        if user.tracker_stop_alerted_at >= user.last_agent_ping:
            return

    recipients = _alert_recipients(user.organization_id or 1, settings)
    if not recipients:
        current_app.logger.warning(
            "tracker stop alert: no admin email for user %s", user.username
        )
        return

    cfg = current_app.config.get("TIMETRACK_SERVER_CONFIG")
    data_dir = getattr(cfg, "data_dir", None) if cfg else None
    name = user.display_name or user.username
    reason_label = {
        "quit": "Quit from tray",
        "logout": "Signed out from tray",
        "signal": "Agent closed",
        "stopped": "Tracker stopped",
    }.get(reason, reason)

    subject = f"[esstracker] Tracker stopped — {name}"
    text = (
        f"Employee: {name} (@{user.username})\n"
        f"Reason: {reason_label}\n"
        f"Time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
        "Open the admin dashboard to review live status."
    )
    html = f"""
    <p><strong>{name}</strong> (@{user.username}) tracker is no longer running.</p>
    <p>Reason: {reason_label}</p>
    <p style="color:#5F7A6A;font-size:13px">Sent by esstracker</p>
    """

    for addr in recipients:
        ok, err = send_email(
            to=addr,
            subject=subject,
            text_body=text,
            html_body=html,
            data_dir=data_dir,
        )
        if not ok:
            current_app.logger.error("tracker stop email to %s failed: %s", addr, err)

    user.tracker_stop_alerted_at = now
    db.session.commit()


def check_stale_trackers(org_id: int, *, stale_seconds: float = 90.0) -> None:
    """Email when an employee was pinging recently but went silent (kill/crash)."""
    settings = get_settings(org_id)
    if not getattr(settings, "alert_on_tracker_stop", True):
        return
    now = time.time()
    users = db.session.execute(
        db.select(User).filter(
            User.organization_id == org_id,
            User.enabled.is_(True),
            User.role == ROLE_EMPLOYEE,
        )
    ).scalars()
    for u in users:
        last = u.last_agent_ping
        if not last:
            continue
        gap = now - float(last)
        if gap < stale_seconds or gap > 86400:
            continue
        if u.tracker_stop_alerted_at and u.tracker_stop_alerted_at >= last:
            continue
        send_tracker_stop_alert(u, reason="stopped")


__all__ = ["touch_agent_ping", "send_tracker_stop_alert", "check_stale_trackers"]
