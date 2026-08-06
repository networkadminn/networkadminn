"""User lifecycle helpers."""

from __future__ import annotations

from sqlalchemy import delete

from .extensions import db
from .models import (
    Activity,
    ManualEntry,
    OfflineRequest,
    PasswordResetToken,
    PrivatePeriod,
    Screenshot,
    TimerSession,
    User,
)


def delete_user_account(user: User) -> None:
    """Remove a user and all dependent rows (activity, screenshots, requests, etc.)."""
    uid = user.id
    for model in (
        OfflineRequest,
        PasswordResetToken,
        TimerSession,
        ManualEntry,
        PrivatePeriod,
        Activity,
        Screenshot,
    ):
        db.session.execute(delete(model).where(model.user_id == uid))
    db.session.delete(user)
    db.session.commit()
