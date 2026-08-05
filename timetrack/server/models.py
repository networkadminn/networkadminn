"""SQLAlchemy models for the TimeTrack server."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db

ROLE_ADMIN = "admin"
ROLE_EMPLOYEE = "employee"
_PASSWORD_SYMBOLS = set("!@#$%^&*()-_=+[]{};:,.?/")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def validate_password_strength(password: str, *, username: str = "") -> list[str]:
    """Return validation errors for passwords used by interactive users."""

    errors = []
    if len(password) < 12:
        errors.append("use at least 12 characters")
    if username and username.lower() in password.lower():
        errors.append("do not include the username")
    if not any(ch.islower() for ch in password):
        errors.append("include a lowercase letter")
    if not any(ch.isupper() for ch in password):
        errors.append("include an uppercase letter")
    if not any(ch.isdigit() for ch in password):
        errors.append("include a number")
    if not any(ch in _PASSWORD_SYMBOLS for ch in password):
        errors.append("include a symbol")
    return errors


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

    activities = db.relationship(
        "Activity", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )
    screenshots = db.relationship(
        "Screenshot", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )

    # --- helpers ---
    def set_password(self, password: str) -> None:
        errors = validate_password_strength(password, username=self.username)
        if errors:
            raise ValueError(
                "password does not meet strength requirements: " + "; ".join(errors)
            )
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

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
    "validate_password_strength",
]
