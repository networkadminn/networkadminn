"""Shared helpers for timer sessions counting as DeskTime time."""

from __future__ import annotations

from .extensions import db
from .models import Activity, ManualEntry, Project, TimerSession


def finalize_timer_session(session: TimerSession, end_ts: float) -> float:
    """Stop a timer and record ManualEntry + Activity so metrics include it.

    Returns duration in seconds (0 if too short).
    """
    session.running = False
    session.end_ts = end_ts
    duration = max(0.0, end_ts - session.start_ts)
    if duration < 1:
        return 0.0

    project_name = "Project timer"
    if session.project_id:
        project = db.session.get(Project, session.project_id)
        if project is not None:
            project_name = project.name

    note = (session.note or "Timer").strip() or "Timer"
    db.session.add(
        ManualEntry(
            user_id=session.user_id,
            project_id=session.project_id,
            task_id=session.task_id,
            note=note,
            start_ts=session.start_ts,
            end_ts=end_ts,
            duration=duration,
        )
    )
    # Count toward DeskTime productivity (DeskTime treats project/offline time as work).
    db.session.add(
        Activity(
            user_id=session.user_id,
            app=project_name[:255],
            title=note[:2000],
            category="productive",
            idle=False,
            start_ts=session.start_ts,
            end_ts=end_ts,
            duration=duration,
            project_id=session.project_id,
            task_id=session.task_id,
        )
    )
    return duration
