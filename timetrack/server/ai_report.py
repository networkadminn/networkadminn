"""AI day reports via Google Gemini (activity + optional screenshots)."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import current_app

from ..analytics import (
    activity_timeline,
    apps_with_share,
    day_bounds,
    format_clock,
    humanize,
    summarize,
    top_sites,
)
from .extensions import db
from .mail import send_email
from .models import Activity, DayAiReport, ROLE_ADMIN, ROLE_EMPLOYEE, ROLE_SUPERADMIN, Screenshot, User
from .settings_util import get_settings

GEMINI_MODEL = "gemini-flash-latest"
MAX_SCREENSHOTS = 0  # text-only — vision payloads often timeout from server
MAX_TIMELINE_ROWS = 50
GEMINI_TIMEOUT = 90
GEMINI_RETRIES = 4
STALE_GENERATING_SECONDS = 600


def load_gemini_api_key(data_dir: str | None = None) -> str:
    """Load Gemini API key from env or ``data_dir/gemini.toml``."""
    key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("ESSTRACKER_GEMINI_API_KEY") or "").strip()
    if key:
        return key
    toml_path = None
    if data_dir:
        toml_path = Path(data_dir) / "gemini.toml"
    elif os.environ.get("ESSTRACKER_GEMINI_TOML"):
        toml_path = Path(os.environ["ESSTRACKER_GEMINI_TOML"])
    if toml_path and toml_path.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover
            return ""
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
        section = data.get("gemini", data)
        return str(section.get("api_key") or "").strip()
    return ""


def _data_dir() -> str | None:
    cfg = current_app.config.get("TIMETRACK_SERVER_CONFIG")
    return getattr(cfg, "data_dir", None) if cfg else None


def get_day_report(user_id: int, day_str: str) -> DayAiReport | None:
    return db.session.execute(
        db.select(DayAiReport).filter_by(user_id=user_id, day=day_str)
    ).scalar_one_or_none()


def report_to_dict(report: DayAiReport | None) -> dict:
    if report is None:
        return {
            "status": "none",
            "summary": "",
            "error_message": "",
            "created_at": None,
            "emailed_at": None,
        }
    return {
        "status": report.status or "none",
        "summary": report.summary or "",
        "error_message": report.error_message or "",
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "emailed_at": report.emailed_at.isoformat() if report.emailed_at else None,
    }


def _alert_recipients(org_id: int, settings) -> list[str]:
    raw = (getattr(settings, "ai_report_emails", None) or "").strip()
    if not raw:
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


def _build_prompt(
    user: User,
    day: datetime,
    *,
    tz_name: str,
    summary,
    apps: list[dict],
    sites: list[dict],
    timeline: list[dict],
    screenshot_count: int,
) -> str:
    day_label = day.strftime("%A, %B %d, %Y")
    name = user.display_name or user.username
    lines = [
        f"You are an assistant for an employee time-tracking system.",
        f"Write a clear, manager-friendly daily work summary for {name} (@{user.username}).",
        f"Date: {day_label} ({tz_name})",
        "",
        "Stats:",
        f"- Desk time (active): {humanize(summary.active_seconds)}",
        f"- Productive: {humanize(summary.productive_seconds)} ({summary.desktime_productivity_pct}%)",
        f"- Neutral: {humanize(summary.neutral_seconds)}",
        f"- Low productive: {humanize(summary.unproductive_seconds)}",
        f"- Idle: {humanize(summary.idle_seconds)}",
        f"- Arrival: {format_clock(summary.arrival_ts, tz_name=tz_name)}",
        f"- Last activity: {format_clock(summary.last_seen_ts, tz_name=tz_name)}",
        "",
        "Top applications:",
    ]
    for a in apps[:10]:
        lines.append(f"- {a['app']}: {humanize(a['seconds'])} ({a['category']}, {a['pct']}%)")
    if sites:
        lines.append("")
        lines.append("Top websites:")
        for s in sites[:10]:
            lines.append(f"- {s['domain']}: {humanize(s['seconds'])} ({s['category']})")
    lines.append("")
    lines.append("Activity timeline (most recent segments):")
    for t in timeline[:MAX_TIMELINE_ROWS]:
        title = (t.get("title") or "").replace("\n", " ")[:120]
        domain = t.get("domain") or ""
        site = f" · {domain}" if domain else ""
        lines.append(
            f"- {t['start_ts']}|{t['end_ts']} | {t['app']} | {title}{site} | "
            f"{humanize(t['seconds'])} | {t['category']}"
        )
    if screenshot_count:
        lines.append("")
        lines.append(
            f"{screenshot_count} screenshot thumbnail(s) are attached (chronological). "
            "Use them to infer what the employee worked on; do not invent details you cannot see."
        )
    lines.extend(
        [
            "",
            "Respond in plain English with:",
            "1) One short overview paragraph (3-4 sentences)",
            "2) Bullet list of main work themes / projects inferred",
            "3) Bullet list of tools and sites used",
            "4) Any notable idle gaps or low-productivity periods (if visible)",
            "5) Overall assessment: focused / mixed / light day",
            "Keep it factual; say 'unclear' when data is insufficient.",
        ]
    )
    return "\n".join(lines)


def _sample_screenshot_bytes(
    screenshots: list,
    *,
    screenshots_dir: str,
    max_count: int = MAX_SCREENSHOTS,
) -> list[tuple[bytes, str]]:
    if not screenshots:
        return []
    ordered = sorted(screenshots, key=lambda s: float(s.ts))
    if len(ordered) <= max_count:
        picked = ordered
    else:
        step = (len(ordered) - 1) / (max_count - 1)
        picked = [ordered[int(round(i * step))] for i in range(max_count)]
    out: list[tuple[bytes, str]] = []
    root = Path(screenshots_dir)
    for shot in picked:
        rel = (shot.thumb_path or shot.path or "").strip()
        if not rel:
            continue
        path = root / rel
        if not path.is_file():
            path = root / (shot.path or "")
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not data:
            continue
        mime = "image/jpeg"
        if path.suffix.lower() == ".png":
            mime = "image/png"
        out.append((data, mime))
    return out


def call_gemini(
    prompt: str,
    images: list[tuple[bytes, str]] | None = None,
    *,
    api_key: str,
) -> tuple[str, str]:
    """Return (summary_text, error_message)."""
    if not api_key:
        return "", "Gemini API key not configured (set GEMINI_API_KEY or data/gemini.toml)"

    parts: list[dict] = [{"text": prompt}]
    for data, mime in images or []:
        parts.append(
            {
                "inline_data": {
                    "mime_type": mime,
                    "data": base64.b64encode(data).decode("ascii"),
                }
            }
        )
    body = json.dumps({"contents": [{"parts": parts}]}).encode("utf-8")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    last_err = ""
    for attempt in range(GEMINI_RETRIES):
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": api_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001
                detail = str(exc)
            last_err = f"Gemini HTTP {exc.code}: {detail}"
            if exc.code in (429, 503) and attempt + 1 < GEMINI_RETRIES:
                time.sleep(2.0 * (attempt + 1))
                continue
            return "", last_err
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_err = str(exc)
            if attempt + 1 < GEMINI_RETRIES:
                time.sleep(2.0)
                continue
            return "", last_err
        except (ValueError, json.JSONDecodeError) as exc:
            return "", str(exc)

        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return "", "Unexpected Gemini response format"
        text = (text or "").strip()
        if not text:
            return "", "Empty response from Gemini"
        return text, ""

    return "", last_err or "Gemini request failed"


def _activities_for_user_day(user_id: int, day: datetime, tz_name: str) -> list[Activity]:
    start, end = day_bounds(day, tz_name=tz_name)
    return list(
        db.session.execute(
            db.select(Activity)
            .filter(Activity.user_id == user_id)
            .filter(Activity.end_ts >= start, Activity.start_ts < end)
            .order_by(Activity.start_ts.asc())
        ).scalars()
    )


def _screenshots_for_user_day(user_id: int, day: datetime, tz_name: str) -> list[Screenshot]:
    start, end = day_bounds(day, tz_name=tz_name)
    return list(
        db.session.execute(
            db.select(Screenshot)
            .filter(Screenshot.user_id == user_id)
            .filter(Screenshot.ts >= start, Screenshot.ts < end)
            .order_by(Screenshot.ts.asc())
        ).scalars()
    )


def _mark_report_failed(report: DayAiReport, message: str) -> None:
    report.status = "failed"
    report.error_message = (message or "Unknown error")[:2000]
    report.summary = ""
    db.session.commit()


def _mark_report_ready(report: DayAiReport, summary: str) -> None:
    report.status = "ready"
    report.error_message = ""
    report.summary = summary
    report.created_at = datetime.utcnow()
    db.session.commit()


def reset_stale_generating(user_id: int, day_str: str) -> None:
    """Mark stuck ``generating`` rows as failed so the user can retry."""
    report = get_day_report(user_id, day_str)
    if report is None or report.status != "generating":
        return
    age = (datetime.utcnow() - (report.created_at or datetime.utcnow())).total_seconds()
    if age >= STALE_GENERATING_SECONDS:
        _mark_report_failed(report, "Generation timed out. Please try again.")


def generate_day_ai_report(
    user: User,
    day: datetime,
    *,
    acts=None,
    shots=None,
) -> tuple[DayAiReport | None, str]:
    """Generate or refresh the AI report for one user/day. Returns (report, error)."""
    settings = get_settings(user.organization_id or 1)
    tz_name = settings.timezone or "Asia/Kolkata"
    day_str = day.strftime("%Y-%m-%d")
    api_key = load_gemini_api_key(_data_dir())
    if not api_key:
        return None, "Gemini API key not configured (set GEMINI_API_KEY or data/gemini.toml)"

    report = get_day_report(user.id, day_str)
    if report is None:
        report = DayAiReport(user_id=user.id, day=day_str, status="generating")
        db.session.add(report)
        db.session.commit()

    if acts is None:
        acts = _activities_for_user_day(user.id, day, tz_name)
    if shots is None:
        shots = _screenshots_for_user_day(user.id, day, tz_name)

    start, end = day_bounds(day, tz_name=tz_name)
    summary = summarize(acts, start, end)
    apps = apps_with_share(summary)
    sites = top_sites(acts)
    timeline = activity_timeline(acts, limit=MAX_TIMELINE_ROWS)
    for t in timeline:
        t["start_ts"] = format_clock(t["start_ts"], tz_name=tz_name)
        t["end_ts"] = format_clock(t["end_ts"], tz_name=tz_name)

    cfg = current_app.config.get("TIMETRACK_SERVER_CONFIG")
    screenshots_dir = getattr(cfg, "screenshots_dir", "") if cfg else ""
    images: list[tuple[bytes, str]] = []
    if MAX_SCREENSHOTS > 0:
        images = _sample_screenshot_bytes(
            shots, screenshots_dir=screenshots_dir, max_count=MAX_SCREENSHOTS
        )

    prompt = _build_prompt(
        user,
        day,
        tz_name=tz_name,
        summary=summary,
        apps=apps,
        sites=sites,
        timeline=timeline,
        screenshot_count=len(images),
    )
    text, err = call_gemini(prompt, images or None, api_key=api_key)
    if err and images:
        # Fallback: text-only if vision request failed
        prompt_text = _build_prompt(
            user,
            day,
            tz_name=tz_name,
            summary=summary,
            apps=apps,
            sites=sites,
            timeline=timeline,
            screenshot_count=0,
        )
        text, err = call_gemini(prompt_text, None, api_key=api_key)
    if err:
        _mark_report_failed(report, err)
        return None, err

    _mark_report_ready(report, text)
    return report, ""


def queue_ai_report_generation(user: User, day: datetime) -> tuple[bool, str]:
    """Start AI report generation in a background thread (avoids gunicorn timeout)."""
    day_str = day.strftime("%Y-%m-%d")
    if not load_gemini_api_key(_data_dir()):
        return False, "Gemini API key not configured (set GEMINI_API_KEY or data/gemini.toml)"

    report = get_day_report(user.id, day_str)
    if report is not None and report.status == "generating":
        return True, "AI report is already generating. Refresh this page in a moment."

    if report is None:
        report = DayAiReport(user_id=user.id, day=day_str, status="generating")
        db.session.add(report)
    else:
        report.status = "generating"
        report.error_message = ""
        report.summary = ""
        report.created_at = datetime.utcnow()
    db.session.commit()

    app = current_app._get_current_object()
    uid = user.id

    def _run() -> None:
        with app.app_context():
            u = db.session.get(User, uid)
            if not u:
                return
            day_dt = datetime.strptime(day_str, "%Y-%m-%d")
            generate_day_ai_report(u, day_dt)

    threading.Thread(target=_run, daemon=True, name=f"ai-report-{uid}-{day_str}").start()
    return True, "AI report is generating. This page will refresh automatically."


def email_ai_report(
    report: DayAiReport,
    user: User,
    *,
    force: bool = False,
) -> tuple[bool, str]:
    """Email report to configured admin addresses."""
    settings = get_settings(user.organization_id or 1)
    recipients = _alert_recipients(user.organization_id or 1, settings)
    if not recipients:
        return False, "No admin email addresses configured"

    if report.emailed_at and not force:
        return True, ""

    name = user.display_name or user.username
    subject = f"[esstracker] AI day report — {name} · {report.day}"
    text = (
        f"Employee: {name} (@{user.username})\n"
        f"Date: {report.day}\n\n"
        f"{report.summary}\n\n"
        "— Generated by esstracker AI"
    )
    html = f"""
    <p><strong>{name}</strong> (@{user.username}) · {report.day}</p>
    <div style="white-space:pre-wrap;font-family:system-ui,sans-serif;line-height:1.5">
    {report.summary.replace(chr(10), "<br>")}
    </div>
    <p style="color:#5F7A6A;font-size:13px">Generated by esstracker AI</p>
    """
    data_dir = _data_dir()
    last_err = ""
    sent_any = False
    for addr in recipients:
        ok, err = send_email(
            to=addr,
            subject=subject,
            text_body=text,
            html_body=html,
            data_dir=data_dir,
        )
        if ok:
            sent_any = True
        else:
            last_err = err
            current_app.logger.error("AI report email to %s failed: %s", addr, err)

    if sent_any:
        report.emailed_at = datetime.utcnow()
        db.session.commit()
        return True, ""
    return False, last_err or "Email send failed"


def _logout_time_ok(settings) -> bool:
    """True if auto-email should run now (after office end or any-logout mode)."""
    from ..tzutil import now_tz

    tz_name = settings.timezone or "Asia/Kolkata"
    now = now_tz(tz_name)
    if not getattr(settings, "ai_report_after_office_only", True):
        return True
    office_end = float(settings.office_end_hour or 18.5)
    now_hour = now.hour + now.minute / 60.0
    return now_hour >= office_end


def _send_ai_report_on_logout_impl(user: User) -> None:
    settings = get_settings(user.organization_id or 1)
    if not getattr(settings, "email_ai_report_on_logout", False):
        return
    if user.role not in (ROLE_EMPLOYEE,):
        return
    if not _logout_time_ok(settings):
        return

    from ..tzutil import today_tz

    tz_name = settings.timezone or "Asia/Kolkata"
    day = datetime.combine(today_tz(tz_name), datetime.min.time())
    day_str = day.strftime("%Y-%m-%d")

    report = get_day_report(user.id, day_str)
    if report is None or not (report.summary or "").strip():
        report, err = generate_day_ai_report(user, day)
        if err or report is None:
            current_app.logger.warning(
                "AI report on logout for %s failed: %s", user.username, err
            )
            return

    if report.emailed_at:
        return
    ok, err = email_ai_report(report, user, force=True)
    if not ok:
        current_app.logger.warning(
            "AI report email on logout for %s failed: %s", user.username, err
        )


def maybe_send_ai_report_on_logout(user: User) -> None:
    """Fire-and-forget AI report generation + email when an employee logs out."""
    if user.role != ROLE_EMPLOYEE:
        return
    settings = get_settings(user.organization_id or 1)
    if not getattr(settings, "email_ai_report_on_logout", False):
        return
    app = current_app._get_current_object()
    uid = user.id

    def _run() -> None:
        with app.app_context():
            u = db.session.get(User, uid)
            if u:
                _send_ai_report_on_logout_impl(u)

    threading.Thread(target=_run, daemon=True, name="ai-report-logout").start()


__all__ = [
    "load_gemini_api_key",
    "get_day_report",
    "report_to_dict",
    "reset_stale_generating",
    "generate_day_ai_report",
    "queue_ai_report_generation",
    "email_ai_report",
    "maybe_send_ai_report_on_logout",
    "call_gemini",
]
