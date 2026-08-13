"""SQLAlchemy models for the TimeTrack server (DeskTime-aligned)."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db

ROLE_SUPERADMIN = "superadmin"
ROLE_ADMIN = "admin"
ROLE_EMPLOYEE = "employee"

PLAN_TRIAL = "trial"
PLAN_STARTER = "starter"
PLAN_BUSINESS = "business"

# pending_confirm → trial → active (paid) | expired | suspended
ORG_PENDING = "pending_confirm"
ORG_TRIAL = "trial"
ORG_ACTIVE = "active"
ORG_EXPIRED = "expired"
ORG_SUSPENDED = "suspended"

TRIAL_DAYS = 15


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


class Organization(db.Model):
    """SaaS tenant — one company workspace (row-level + tenant folder)."""

    __tablename__ = "organizations"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    plan = db.Column(db.String(32), nullable=False, default=PLAN_TRIAL)
    status = db.Column(db.String(20), nullable=False, default=ORG_PENDING)
    created_at = db.Column(db.DateTime, default=_utcnow)
    trial_ends_at = db.Column(db.DateTime, nullable=True)
    paid_until = db.Column(db.DateTime, nullable=True)
    email_confirmed_at = db.Column(db.DateTime, nullable=True)
    confirm_token = db.Column(db.String(64), unique=True, nullable=True, index=True)
    billing_email = db.Column(db.String(200), default="")
    max_seats = db.Column(db.Integer, nullable=False, default=25)
    tenant_path = db.Column(db.String(300), default="")
    notes = db.Column(db.Text, default="")

    users = db.relationship("User", backref="organization", lazy="dynamic")
    teams = db.relationship("Team", backref="organization", lazy="dynamic")

    def refresh_access_status(self) -> str:
        """Recompute status from trial/paid dates (does not commit)."""
        if self.status == ORG_SUSPENDED:
            return self.status
        if self.status == ORG_PENDING:
            return self.status
        now = _utcnow().replace(tzinfo=None)
        paid = self.paid_until
        if paid is not None:
            paid_naive = paid.replace(tzinfo=None) if getattr(paid, "tzinfo", None) else paid
            if paid_naive >= now:
                self.status = ORG_ACTIVE
                if self.plan == PLAN_TRIAL:
                    self.plan = PLAN_STARTER
                return self.status
        trial = self.trial_ends_at
        if trial is not None:
            trial_naive = (
                trial.replace(tzinfo=None) if getattr(trial, "tzinfo", None) else trial
            )
            if trial_naive >= now:
                self.status = ORG_TRIAL
                self.plan = PLAN_TRIAL
                return self.status
            self.status = ORG_EXPIRED
            return self.status
        if self.plan in (PLAN_STARTER, PLAN_BUSINESS) and self.status == ORG_ACTIVE:
            return self.status
        # Existing default/platform org without trial dates stays active.
        if self.id == 1 or self.slug == "default":
            self.status = ORG_ACTIVE
            if self.plan == PLAN_TRIAL:
                self.plan = PLAN_BUSINESS
            return self.status
        self.status = ORG_EXPIRED
        return self.status

    @property
    def has_access(self) -> bool:
        self.refresh_access_status()
        return self.status in (ORG_TRIAL, ORG_ACTIVE)

    @property
    def days_left(self) -> int | None:
        now = _utcnow().replace(tzinfo=None)
        end = None
        if self.paid_until:
            end = (
                self.paid_until.replace(tzinfo=None)
                if self.paid_until.tzinfo
                else self.paid_until
            )
        elif self.trial_ends_at:
            end = (
                self.trial_ends_at.replace(tzinfo=None)
                if self.trial_ends_at.tzinfo
                else self.trial_ends_at
            )
        if end is None:
            return None
        return max(0, (end.date() - now.date()).days)


class Team(db.Model):
    __tablename__ = "teams"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, default=1, index=True
    )
    name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow)

    users = db.relationship("User", backref="team", lazy="dynamic")

    __table_args__ = (db.UniqueConstraint("organization_id", "name", name="uq_team_org_name"),)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, default=1, index=True
    )
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(200), default="")
    display_name = db.Column(db.String(200), default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_EMPLOYEE)
    api_token = db.Column(
        db.String(64), unique=True, nullable=False, index=True, default=generate_token
    )
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)
    private_time_allowed = db.Column(db.Boolean, nullable=False, default=True)
    # Per-user screenshot overrides (NULL = use company settings)
    screenshots_enabled = db.Column(db.Boolean, nullable=True)
    screenshot_interval = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow)

    activities = db.relationship(
        "Activity", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )
    screenshots = db.relationship(
        "Screenshot", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_superadmin(self) -> bool:
        return self.role == ROLE_SUPERADMIN

    @property
    def is_admin(self) -> bool:
        return self.role in (ROLE_ADMIN, ROLE_SUPERADMIN)

    @property
    def name(self) -> str:
        return self.display_name or self.username

    def rotate_token(self) -> str:
        self.api_token = generate_token()
        return self.api_token


class Activity(db.Model):
    __tablename__ = "activities"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    app = db.Column(db.String(255), nullable=False, default="unknown")
    title = db.Column(db.Text, nullable=False, default="")
    url = db.Column(db.String(500), nullable=False, default="")
    category = db.Column(db.String(20), nullable=False, default="neutral")
    idle = db.Column(db.Boolean, nullable=False, default=False)
    start_ts = db.Column(db.Float, nullable=False, index=True)
    end_ts = db.Column(db.Float, nullable=False)
    duration = db.Column(db.Float, nullable=False, default=0.0)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=True)


class Screenshot(db.Model):
    __tablename__ = "screenshots"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    ts = db.Column(db.Float, nullable=False, index=True)
    path = db.Column(db.String(500), nullable=False)
    thumb_path = db.Column(db.String(500), default="")
    width = db.Column(db.Integer, default=0)
    height = db.Column(db.Integer, default=0)
    app = db.Column(db.String(255), default="")
    title = db.Column(db.Text, default="")
    blurred = db.Column(db.Boolean, nullable=False, default=False)
    is_unproductive = db.Column(db.Boolean, nullable=False, default=False)


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, default=1, index=True
    )
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    color = db.Column(db.String(20), default="#0d9488")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=_utcnow)

    tasks = db.relationship(
        "Task", backref="project", lazy="dynamic", cascade="all, delete-orphan"
    )
    entries = db.relationship(
        "ManualEntry", backref="project", lazy="dynamic", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.UniqueConstraint("organization_id", "name", name="uq_project_org_name"),
    )


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True
    )
    name = db.Column(db.String(200), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=_utcnow)


class ManualEntry(db.Model):
    __tablename__ = "manual_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    project_id = db.Column(
        db.Integer, db.ForeignKey("projects.id"), nullable=True, index=True
    )
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=True)
    note = db.Column(db.String(500), default="")
    start_ts = db.Column(db.Float, nullable=False, index=True)
    end_ts = db.Column(db.Float, nullable=False)
    duration = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=_utcnow)

    user = db.relationship("User", backref=db.backref("manual_entries", lazy="dynamic"))
    task = db.relationship("Task")


class TimerSession(db.Model):
    """DeskTime-style live project timer (start/stop)."""

    __tablename__ = "timer_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=True)
    note = db.Column(db.String(500), default="")
    start_ts = db.Column(db.Float, nullable=False, index=True)
    end_ts = db.Column(db.Float, nullable=True)
    running = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User", backref=db.backref("timer_sessions", lazy="dynamic"))
    project = db.relationship("Project")
    task = db.relationship("Task")


class PrivatePeriod(db.Model):
    """DeskTime Private Time — tracking paused span."""

    __tablename__ = "private_periods"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    start_ts = db.Column(db.Float, nullable=False, index=True)
    end_ts = db.Column(db.Float, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User", backref=db.backref("private_periods", lazy="dynamic"))


class OfflineRequest(db.Model):
    """Manual fill of idle/offline gap (optionally admin-approved)."""

    __tablename__ = "offline_requests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    start_ts = db.Column(db.Float, nullable=False)
    end_ts = db.Column(db.Float, nullable=False)
    duration = db.Column(db.Float, nullable=False, default=0.0)
    category = db.Column(db.String(20), nullable=False, default="neutral")
    note = db.Column(db.String(500), default="")
    status = db.Column(db.String(20), nullable=False, default="approved")
    # pending | approved | rejected
    created_at = db.Column(db.DateTime, default=_utcnow)

    user = db.relationship("User", backref=db.backref("offline_requests", lazy="dynamic"))


class CompanySettings(db.Model):
    """Singleton-ish company settings (id=1)."""

    __tablename__ = "company_settings"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, unique=True, default=1
    )
    screenshots_enabled = db.Column(db.Boolean, nullable=False, default=True)
    screenshot_blur = db.Column(db.Boolean, nullable=False, default=False)
    screenshot_interval = db.Column(db.Integer, nullable=False, default=300)
    # seconds base interval; agent randomizes within
    screenshot_random = db.Column(db.Boolean, nullable=False, default=True)
    private_time_enabled = db.Column(db.Boolean, nullable=False, default=True)
    offline_requires_approval = db.Column(db.Boolean, nullable=False, default=False)
    expected_hours = db.Column(db.Float, nullable=False, default=8.0)
    expected_arrival_hour = db.Column(db.Float, nullable=False, default=9.5)
    # Office window (IST by default). expected_arrival_hour stays in sync with start.
    office_start_hour = db.Column(db.Float, nullable=False, default=9.5)
    office_end_hour = db.Column(db.Float, nullable=False, default=18.5)
    # Comma-separated Python weekdays: 0=Mon … 6=Sun. Default Mon–Fri.
    work_days = db.Column(db.String(32), nullable=False, default="0,1,2,3,4")
    timezone = db.Column(db.String(64), nullable=False, default="Asia/Kolkata")
    company_name = db.Column(db.String(200), nullable=False, default="Timeforge")
    # Seconds without keyboard/mouse before agent marks idle (DeskTime default ~180).
    idle_threshold = db.Column(db.Integer, nullable=False, default=180)


class PasswordResetToken(db.Model):
    """One-time password reset links emailed to the user."""

    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=_utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", backref=db.backref("reset_tokens", lazy="dynamic"))


class Invitation(db.Model):
    """Admin invite for a new employee seat (email link → set password)."""

    __tablename__ = "invitations"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True
    )
    email = db.Column(db.String(200), nullable=False, index=True)
    display_name = db.Column(db.String(200), default="")
    role = db.Column(db.String(20), nullable=False, default=ROLE_EMPLOYEE)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    invited_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    accepted_at = db.Column(db.DateTime, nullable=True)
    created_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    organization = db.relationship("Organization", backref=db.backref("invitations", lazy="dynamic"))


__all__ = [
    "Organization",
    "Team",
    "User",
    "Activity",
    "Screenshot",
    "Project",
    "Task",
    "ManualEntry",
    "TimerSession",
    "PrivatePeriod",
    "OfflineRequest",
    "CompanySettings",
    "PasswordResetToken",
    "Invitation",
    "ROLE_SUPERADMIN",
    "ROLE_ADMIN",
    "ROLE_EMPLOYEE",
    "PLAN_TRIAL",
    "PLAN_STARTER",
    "PLAN_BUSINESS",
    "ORG_PENDING",
    "ORG_TRIAL",
    "ORG_ACTIVE",
    "ORG_EXPIRED",
    "ORG_SUSPENDED",
    "TRIAL_DAYS",
    "generate_token",
]
