"""DeskTime-parity web features: timer, private time, offline fill, teams, exports, settings."""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..analytics import day_bounds, humanize, summarize
from .auth import admin_required
from .extensions import db
from .models import (
    Activity,
    OfflineRequest,
    PrivatePeriod,
    Project,
    Task,
    Team,
    TimerSession,
    User,
)
from .settings_util import get_settings
from .views import _activities_for, _parse_day

desk_bp = Blueprint("desk", __name__)


@desk_bp.route("/timer", methods=["GET", "POST"])
@login_required
def timer_page():
    if not current_user.is_admin:
        flash("Project timer is for admins only.", "info")
        return redirect(url_for("views.me"))
    running = db.session.execute(
        db.select(TimerSession)
        .filter_by(user_id=current_user.id, running=True)
        .order_by(TimerSession.start_ts.desc())
    ).scalar_one_or_none()

    if request.method == "POST":
        action = request.form.get("action") or "start"
        now = time.time()
        if action == "stop" and running:
            from .time_util import finalize_timer_session

            duration = finalize_timer_session(running, now)
            db.session.commit()
            flash(f"Timer stopped · {humanize(duration)}", "info")
        elif action == "start":
            if running:
                from .time_util import finalize_timer_session

                finalize_timer_session(running, now)
            project_id = request.form.get("project_id", type=int)
            task_id = request.form.get("task_id", type=int)
            note = (request.form.get("note") or "").strip()
            db.session.add(
                TimerSession(
                    user_id=current_user.id,
                    project_id=project_id,
                    task_id=task_id,
                    note=note,
                    start_ts=now,
                    running=True,
                )
            )
            db.session.commit()
            flash("Project timer started.", "info")
        return redirect(url_for("desk.timer_page"))

    from .tenancy import projects_in_org_query

    projects = list(
        db.session.execute(
            projects_in_org_query(current_user.organization_id or 1)
            .filter(Project.active.is_(True))
            .order_by(Project.name)
        ).scalars()
    )
    tasks_by_project = {
        str(p.id): [{"id": t.id, "name": t.name} for t in db.session.execute(
            db.select(Task).filter_by(project_id=p.id, active=True).order_by(Task.name)
        ).scalars()]
        for p in projects
    }
    elapsed = (time.time() - running.start_ts) if running else 0
    return render_template(
        "timer.html",
        running=running,
        elapsed=elapsed,
        projects=projects,
        tasks_by_project=tasks_by_project,
        humanize=humanize,
    )


@desk_bp.route("/private", methods=["POST"])
@login_required
def toggle_private():
    settings = get_settings()
    if not (settings.private_time_enabled and current_user.private_time_allowed):
        flash("Private Time is disabled for your account.", "error")
        return redirect(request.referrer or url_for("views.me"))

    active = db.session.execute(
        db.select(PrivatePeriod)
        .filter_by(user_id=current_user.id, active=True)
        .order_by(PrivatePeriod.start_ts.desc())
    ).scalar_one_or_none()
    now = time.time()
    want_on = (request.form.get("active") or "1") == "1"
    if want_on and active is None:
        db.session.add(
            PrivatePeriod(user_id=current_user.id, start_ts=now, active=True)
        )
        db.session.commit()
        flash("Private Time ON — tracking & screenshots paused.", "info")
    elif not want_on and active is not None:
        active.active = False
        active.end_ts = now
        db.session.commit()
        flash("Private Time OFF — tracking resumed.", "info")
    return redirect(request.referrer or url_for("views.me"))


@desk_bp.route("/offline", methods=["GET", "POST"])
@login_required
def offline_fill():
    from ..tzutil import format_datetime_local, zone as tz_zone
    from .settings_util import company_tz_name

    settings = get_settings()
    tz_name = company_tz_name()
    day = _parse_day(request.args.get("day") or request.form.get("day"))
    # Prefill from gap click (?start=&end= unix timestamps) or datetime-local strings.
    prefill_start = ""
    prefill_end = ""
    raw_start = request.args.get("start") or ""
    raw_end = request.args.get("end") or ""
    if raw_start.replace(".", "", 1).isdigit() and raw_end.replace(".", "", 1).isdigit():
        prefill_start = format_datetime_local(float(raw_start), tz_name)
        prefill_end = format_datetime_local(float(raw_end), tz_name)
    elif "T" in raw_start and "T" in raw_end:
        prefill_start, prefill_end = raw_start, raw_end

    if request.method == "POST":
        try:
            start = datetime.strptime(request.form.get("start") or "", "%Y-%m-%dT%H:%M")
            end = datetime.strptime(request.form.get("end") or "", "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Invalid start/end time.", "error")
            return redirect(url_for("desk.offline_fill", day=day.strftime("%Y-%m-%d")))
        # Interpret form times in company timezone.
        tz = tz_zone(tz_name)
        start = start.replace(tzinfo=tz)
        end = end.replace(tzinfo=tz)
        start_ts, end_ts = start.timestamp(), end.timestamp()
        if end_ts <= start_ts:
            flash("End must be after start.", "error")
            return redirect(url_for("desk.offline_fill", day=day.strftime("%Y-%m-%d")))
        now_ts = time.time()
        if start_ts > now_ts + 60:
            flash("Cannot fill a gap that starts in the future.", "error")
            return redirect(url_for("desk.offline_fill", day=day.strftime("%Y-%m-%d")))
        if end_ts > now_ts + 60:
            end_ts = now_ts
            end = datetime.fromtimestamp(end_ts, tz=tz)
            if end_ts <= start_ts:
                flash("Cannot fill a gap that is entirely in the future.", "error")
                return redirect(url_for("desk.offline_fill", day=day.strftime("%Y-%m-%d")))
        category = request.form.get("category") or "neutral"
        if category not in ("productive", "unproductive", "neutral"):
            category = "neutral"
        note = (request.form.get("note") or "").strip()
        fill_kind = request.form.get("fill_kind") or "offline"
        if fill_kind not in ("offline", "idle"):
            fill_kind = "offline"
        if note and fill_kind == "idle" and "idle" not in note.lower():
            note = f"[Idle] {note}"
        elif not note:
            note = "Idle time" if fill_kind == "idle" else "Offline / away from desk"
        # Employees always need approval; admins respect company setting.
        needs_approval = (not current_user.is_admin) or settings.offline_requires_approval
        status = "pending" if needs_approval else "approved"
        duration = end_ts - start_ts
        req = OfflineRequest(
            user_id=current_user.id,
            start_ts=start_ts,
            end_ts=end_ts,
            duration=duration,
            category=category,
            note=note,
            status=status,
        )
        db.session.add(req)
        if status == "approved":
            db.session.add(
                Activity(
                    user_id=current_user.id,
                    app="offline",
                    title=note or "Manual / offline time",
                    category=category,
                    idle=False,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    duration=duration,
                )
            )
        db.session.commit()
        flash(
            "Time submitted."
            + (" Awaiting admin approval." if status == "pending" else " Added to your day."),
            "info",
        )
        return redirect(url_for("views.me", day=day.strftime("%Y-%m-%d")))

    my_recent = list(
        db.session.execute(
            db.select(OfflineRequest)
            .filter_by(user_id=current_user.id)
            .order_by(OfflineRequest.created_at.desc())
            .limit(12)
        ).scalars()
    )
    return render_template(
        "offline.html",
        day=day.strftime("%Y-%m-%d"),
        requires_approval=(not current_user.is_admin) or settings.offline_requires_approval,
        prefill_start=prefill_start,
        prefill_end=prefill_end,
        my_recent=my_recent,
        humanize=humanize,
        tz_name=tz_name,
    )


@desk_bp.route("/approvals", methods=["GET", "POST"])
@admin_required
def approvals():
    from .tenancy import get_org_user, org_user_ids

    if request.method == "POST":
        rid = request.form.get("id", type=int)
        action = request.form.get("action")
        row = db.session.get(OfflineRequest, rid) or abort(404)
        if get_org_user(row.user_id) is None:
            abort(404)
        if action == "approve" and row.status == "pending":
            row.status = "approved"
            db.session.add(
                Activity(
                    user_id=row.user_id,
                    app="offline",
                    title=row.note or "Manual / offline time",
                    category=row.category,
                    idle=False,
                    start_ts=row.start_ts,
                    end_ts=row.end_ts,
                    duration=row.duration,
                )
            )
            flash("Offline request approved.", "info")
        elif action == "reject" and row.status == "pending":
            row.status = "rejected"
            flash("Offline request rejected.", "info")
        db.session.commit()
        return redirect(url_for("desk.approvals"))

    uids = org_user_ids()
    pending_q = db.select(OfflineRequest).filter_by(status="pending")
    recent_q = db.select(OfflineRequest).filter(
        OfflineRequest.status.in_(("approved", "rejected"))
    )
    if uids:
        pending_q = pending_q.filter(OfflineRequest.user_id.in_(uids))
        recent_q = recent_q.filter(OfflineRequest.user_id.in_(uids))
    else:
        pending_q = pending_q.filter(OfflineRequest.user_id == -1)
        recent_q = recent_q.filter(OfflineRequest.user_id == -1)
    pending = list(
        db.session.execute(
            pending_q.order_by(OfflineRequest.created_at.desc())
        ).scalars()
    )
    recent = list(
        db.session.execute(
            recent_q.order_by(OfflineRequest.created_at.desc()).limit(30)
        ).scalars()
    )
    return render_template(
        "approvals.html",
        pending=pending,
        recent=recent,
        humanize=humanize,
    )


@desk_bp.route("/teams", methods=["GET", "POST"])
@admin_required
def teams():
    from .tenancy import current_org_id, teams_in_org_query, users_in_org_query

    org_id = current_org_id()
    if request.method == "POST":
        action = request.form.get("action") or "create"
        if action == "create":
            name = (request.form.get("name") or "").strip()
            if name:
                exists = db.session.execute(
                    teams_in_org_query(org_id).filter(Team.name == name)
                ).scalar_one_or_none()
                if exists:
                    flash("Team already exists.", "error")
                else:
                    db.session.add(Team(name=name, organization_id=org_id))
                    db.session.commit()
                    flash(f"Team {name!r} created.", "info")
        elif action == "assign":
            uid = request.form.get("user_id", type=int)
            tid = request.form.get("team_id", type=int)
            user = db.session.get(User, uid) or abort(404)
            if (user.organization_id or 1) != org_id:
                abort(404)
            if tid:
                team = db.session.get(Team, tid)
                if team is None or (team.organization_id or 1) != org_id:
                    abort(404)
            user.team_id = tid or None
            db.session.commit()
            flash("Team assignment updated.", "info")
        return redirect(url_for("desk.teams"))

    teams_list = list(
        db.session.execute(teams_in_org_query(org_id).order_by(Team.name)).scalars()
    )
    users = list(
        db.session.execute(users_in_org_query(org_id).order_by(User.username)).scalars()
    )
    return render_template("teams.html", teams=teams_list, users=users)


@desk_bp.route("/attendance")
@admin_required
def attendance():
    """DeskTime-style attendance board — week grid or month calendar."""
    from datetime import datetime, timedelta

    from .attendance_util import attendance_cell, month_weeks, week_matrix
    from .tenancy import current_org_id, users_in_org_query
    from .views import _parse_day, _tz

    view = (request.args.get("view") or "week").lower()
    day = _parse_day(request.args.get("day"))
    tz = _tz()
    org_id = current_org_id()

    employees = list(
        db.session.execute(
            users_in_org_query(org_id)
            .filter(User.role != "admin")
            .filter(User.enabled.is_(True))
            .order_by(User.username)
        ).scalars()
    )

    if view == "month":
        month_start = day.replace(day=1)
        year, month = month_start.year, month_start.month
        weeks = month_weeks(year, month)
        user_id = request.args.get("user_id", type=int)
        if user_id:
            subject = db.session.get(User, user_id)
            if subject is None or subject.organization_id != org_id:
                abort(404)
            month_cells: dict[str, dict] = {}
            for w in weeks:
                for d in w:
                    if d is not None:
                        month_cells[d.strftime("%Y-%m-%d")] = attendance_cell(
                            subject.id, d, tz_name=tz
                        )
            prev_month = (month_start - timedelta(days=1)).replace(day=1)
            next_month = (month_start + timedelta(days=32)).replace(day=1)
            return render_template(
                "attendance_month.html",
                view="month",
                subject=subject,
                weeks=weeks,
                month_cells=month_cells,
                year=year,
                month=month,
                month_label=month_start.strftime("%B %Y"),
                day=month_start.strftime("%Y-%m-%d"),
                prev_month=prev_month.strftime("%Y-%m-%d"),
                next_month=next_month.strftime("%Y-%m-%d"),
                employees=employees,
                humanize=humanize,
                tz_name=tz,
            )

        matrix = []
        totals = {"present": 0, "late": 0, "absent": 0, "off": 0}
        _, days_in = __import__("calendar").monthrange(year, month)
        month_days = [
            month_start.replace(day=d) for d in range(1, days_in + 1)
        ]
        for u in employees:
            cells = []
            for d in month_days:
                c = attendance_cell(u.id, d, tz_name=tz)
                totals[c["status"]] = totals.get(c["status"], 0) + 1
                cells.append(c)
            matrix.append({"user": u, "cells": cells})
        prev_month = (month_start - timedelta(days=1)).replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        return render_template(
            "attendance_month.html",
            view="month_team",
            matrix=matrix,
            month_days=month_days,
            totals=totals,
            year=year,
            month=month,
            month_label=month_start.strftime("%B %Y"),
            day=month_start.strftime("%Y-%m-%d"),
            prev_month=prev_month.strftime("%Y-%m-%d"),
            next_month=next_month.strftime("%Y-%m-%d"),
            employees=employees,
            humanize=humanize,
            tz_name=tz,
        )

    monday = day - timedelta(days=day.weekday())
    week_days = [monday + timedelta(days=i) for i in range(7)]
    matrix, totals = week_matrix(employees, week_days, tz_name=tz)
    prev_week = (monday - timedelta(days=7)).strftime("%Y-%m-%d")
    next_week = (monday + timedelta(days=7)).strftime("%Y-%m-%d")
    return render_template(
        "attendance.html",
        view="week",
        week_days=week_days,
        matrix=matrix,
        totals=totals,
        day=monday.strftime("%Y-%m-%d"),
        prev_week=prev_week,
        next_week=next_week,
        humanize=humanize,
        tz_name=tz,
    )


@desk_bp.route("/me/absence")
@login_required
def my_absence():
    """Employee absence calendar (month view)."""
    from datetime import timedelta

    from .attendance_util import attendance_cell, month_summary, month_weeks
    from .views import _parse_day, _tz

    day = _parse_day(request.args.get("day"))
    month_start = day.replace(day=1)
    year, month = month_start.year, month_start.month
    tz = _tz()
    weeks = month_weeks(year, month)
    month_cells: dict[str, dict] = {}
    for w in weeks:
        for d in w:
            if d is not None:
                month_cells[d.strftime("%Y-%m-%d")] = attendance_cell(
                    current_user.id, d, tz_name=tz
                )
    counts = month_summary(current_user.id, year, month, tz_name=tz)
    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return render_template(
        "my_absence.html",
        weeks=weeks,
        month_cells=month_cells,
        counts=counts,
        year=year,
        month=month,
        month_label=month_start.strftime("%B %Y"),
        day=month_start.strftime("%Y-%m-%d"),
        prev_month=prev_month.strftime("%Y-%m-%d"),
        next_month=next_month.strftime("%Y-%m-%d"),
        tz_name=tz,
        humanize=humanize,
    )


@desk_bp.route("/alerts")
@admin_required
def alerts():
    """DeskTime-style attention list: offline agents, late/absent, pending approvals."""
    from datetime import datetime

    from flask import current_app

    from .views import _activities_for, _is_late, _last_seen, _parse_day
    from ..analytics import day_bounds, format_clock, summarize

    day = _parse_day(request.args.get("day"))
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    now_ts = datetime.now().timestamp()
    start, end = day_bounds(day)

    from .tenancy import org_user_ids, users_in_org_query

    employees = list(
        db.session.execute(
            users_in_org_query()
            .filter(User.role != "admin")
            .filter(User.role != "superadmin")
            .filter(User.enabled.is_(True))
            .order_by(User.username)
        ).scalars()
    )

    offline_agents = []
    late_list = []
    absent_list = []
    for u in employees:
        last = _last_seen(u.id)
        online = last is not None and (now_ts - last) <= cfg.online_window
        acts = _activities_for(u.id, day)
        s = summarize(acts, *day_bounds(day))
        absent = s.total_seconds <= 0
        late = _is_late(s.arrival_ts, day) and not absent
        if not online:
            offline_agents.append(
                {
                    "user": u,
                    "last_seen": format_clock(last) if last else "Never",
                    "ago": humanize(now_ts - last) if last else "—",
                }
            )
        if late:
            late_list.append({"user": u, "arrived": format_clock(s.arrival_ts)})
        if absent:
            absent_list.append(u)

    uids = org_user_ids()
    pending_q = db.select(OfflineRequest).filter_by(status="pending")
    if uids:
        pending_q = pending_q.filter(OfflineRequest.user_id.in_(uids))
    else:
        pending_q = pending_q.filter(OfflineRequest.user_id == -1)
    pending = list(
        db.session.execute(
            pending_q.order_by(OfflineRequest.created_at.desc())
        ).scalars()
    )
    from .models import Screenshot as Shot

    flagged_q = (
        db.select(Shot)
        .filter(Shot.is_unproductive.is_(True))
        .filter(Shot.ts >= start, Shot.ts < end)
    )
    if uids:
        flagged_q = flagged_q.filter(Shot.user_id.in_(uids))
    else:
        flagged_q = flagged_q.filter(Shot.user_id == -1)
    flagged_shots = list(
        db.session.execute(
            flagged_q.order_by(Shot.ts.desc()).limit(24)
        ).scalars()
    )

    return render_template(
        "alerts.html",
        day=day.strftime("%Y-%m-%d"),
        offline_agents=offline_agents,
        late_list=late_list,
        absent_list=absent_list,
        pending=pending,
        flagged_shots=flagged_shots,
        humanize=humanize,
        online_window=int(cfg.online_window),
    )


@desk_bp.route("/settings/company", methods=["GET", "POST"])
@admin_required
def company_settings():
    from ..tzutil import (
        WEEKDAY_LABELS,
        format_hour_label,
        hour_parts,
        hour_to_time,
        parse_work_days,
        work_days_csv,
    )

    settings = get_settings()
    if request.method == "POST":
        settings.screenshots_enabled = bool(request.form.get("screenshots_enabled"))
        settings.screenshot_blur = bool(request.form.get("screenshot_blur"))
        settings.screenshot_random = bool(request.form.get("screenshot_random"))
        settings.screenshot_interval = int(request.form.get("screenshot_interval") or 300)
        settings.private_time_enabled = bool(request.form.get("private_time_enabled"))
        settings.offline_requires_approval = bool(
            request.form.get("offline_requires_approval")
        )
        settings.expected_hours = float(request.form.get("expected_hours") or 8)
        settings.company_name = (request.form.get("company_name") or "TimeTrack").strip()[
            :200
        ] or "TimeTrack"
        idle = int(request.form.get("idle_threshold") or 180)
        settings.idle_threshold = max(60, min(idle, 1800))

        tz = (request.form.get("timezone") or "Asia/Kolkata").strip()[:64]
        settings.timezone = tz or "Asia/Kolkata"

        def _parse_time(name: str, default_h: float) -> float:
            raw = (request.form.get(name) or "").strip()
            if raw and ":" in raw:
                hh, mm = raw.split(":", 1)
                return int(hh) + int(mm) / 60.0
            return default_h

        start_h = _parse_time("office_start", 9.5)
        end_h = _parse_time("office_end", 18.5)
        if end_h <= start_h:
            end_h = min(23.75, start_h + 8.0)
        settings.office_start_hour = start_h
        settings.office_end_hour = end_h
        settings.expected_arrival_hour = start_h  # keep late logic in sync

        selected = {int(v) for v in request.form.getlist("work_days") if str(v).isdigit()}
        settings.work_days = work_days_csv(selected) if selected else "0,1,2,3,4"

        db.session.commit()
        flash("Company settings saved. Agents pick them up within ~30s.", "info")
        return redirect(url_for("desk.company_settings"))

    selected = {str(d) for d in parse_work_days(settings.work_days)}
    sh, sm = hour_parts(float(settings.office_start_hour or settings.expected_arrival_hour or 9.5))
    eh, em = hour_parts(float(settings.office_end_hour or 18.5))
    return render_template(
        "company_settings.html",
        settings=settings,
        weekdays=WEEKDAY_LABELS,
        selected_days=selected,
        office_start_value=f"{sh:02d}:{sm:02d}",
        office_end_value=f"{eh:02d}:{em:02d}",
        timezones=[
            ("Asia/Kolkata", "India (IST) — Asia/Kolkata"),
            ("Asia/Dubai", "Dubai (GST)"),
            ("UTC", "UTC"),
        ],
        office_label=f"{format_hour_label(settings.office_start_hour or 9.5)} – {format_hour_label(settings.office_end_hour or 18.5)}",
    )


@desk_bp.route("/export/day.csv")
@login_required
def export_day_csv():
    day = _parse_day(request.args.get("day"))
    user_id = request.args.get("user_id", type=int) or current_user.id
    if user_id != current_user.id and not current_user.is_admin:
        abort(403)
    acts = _activities_for(user_id, day)
    from ..tzutil import format_iso_tz
    from .settings_util import company_tz_name

    tz = company_tz_name()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["start", "end", "duration_sec", "app", "title", "category", "idle"])
    for a in acts:
        w.writerow(
            [
                format_iso_tz(a.start_ts, tz_name=tz),
                format_iso_tz(a.end_ts, tz_name=tz),
                int(a.duration),
                a.app,
                a.title,
                a.category,
                int(a.idle),
            ]
        )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=timetrack-{day.strftime('%Y-%m-%d')}.csv"
        },
    )


@desk_bp.route("/export/team.csv")
@admin_required
def export_team_csv():
    from .tenancy import current_org_id, users_in_org_query

    day = _parse_day(request.args.get("day"))
    team_id = request.args.get("team_id", type=int)
    q = users_in_org_query(current_org_id()).order_by(User.username)
    if team_id:
        q = q.filter(User.team_id == team_id)
    users = list(db.session.execute(q).scalars())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "user",
            "team",
            "active_sec",
            "productive_sec",
            "unproductive_sec",
            "idle_sec",
            "productivity_pct",
        ]
    )
    for u in users:
        s = summarize(_activities_for(u.id, day), *day_bounds(day))
        w.writerow(
            [
                u.username,
                u.team.name if u.team else "",
                int(s.active_seconds),
                int(s.productive_seconds),
                int(s.unproductive_seconds),
                int(s.idle_seconds),
                s.desktime_productivity_pct,
            ]
        )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=team-{day.strftime('%Y-%m-%d')}.csv"
        },
    )


__all__ = ["desk_bp"]
