"""SQLAlchemy models for the TimeTrack server."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .security import validate_password_strength, verify_mfa_code

ROLE_ADMIN = "admin"
ROLE_EMPLOYEE = "employee"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(200), default="")
    display_name = db.Column(db.String(200), default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_EMPLOYEE)
    api_token = db.Column(
        db.String(64), unique=True, nullable=False, index=True, default=generate_token
    )
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=_utcnow)

    # Base32 TOTP secret; NULL/empty means two-factor auth is disabled.
    mfa_secret = db.Column(db.String(64), nullable=True, default=None)

    activities = db.relationship(
        "Activity", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )
    screenshots = db.relationship(
        "Screenshot", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )

    # --- helpers ---
    def set_password(
        self,
        password: str,
        *,
        enforce_policy: bool = True,
        min_length: int | None = None,
    ) -> None:
        """Hash and store ``password``.

        Enforces the minimum password-strength policy by default; pass
        ``enforce_policy=False`` only for trusted, already-validated input
        (e.g. migrating existing hashes).
        """
        if enforce_policy:
            kwargs = {} if min_length is None else {"min_length": min_length}
            validate_password_strength(password, **kwargs)
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def mfa_enabled(self) -> bool:
        return bool(self.mfa_secret)

    def check_mfa_code(self, code: str) -> bool:
        return self.mfa_enabled and verify_mfa_code(self.mfa_secret, code)

    @property
    def name(self) -> str:
        return self.display_name or self.username

    def rotate_token(self) -> str:
        self.api_token = generate_token()
        return self.api_token

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} ({self.role})>"


class Activity(db.Model):
    __tablename__ = "activities"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    app = db.Column(db.String(255), nullable=False, default="unknown")
    title = db.Column(db.Text, nullable=False, default="")
    category = db.Column(db.String(20), nullable=False, default="neutral")
    idle = db.Column(db.Boolean, nullable=False, default=False)
    start_ts = db.Column(db.Float, nullable=False, index=True)
    end_ts = db.Column(db.Float, nullable=False)
    duration = db.Column(db.Float, nullable=False, default=0.0)


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


__all__ = [
    "User",
    "Activity",
    "Screenshot",
    "ROLE_ADMIN",
    "ROLE_EMPLOYEE",
    "generate_token",
]
