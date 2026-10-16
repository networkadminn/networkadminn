"""Authentication: login / logout, forgot/reset password, access-control helpers."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, or_

from .email_templates import render_email
from .login_page import login_page_context
from .extensions import db
from .mail import send_email
from .models import PasswordResetToken, User

auth_bp = Blueprint("auth", __name__)

_RESET_TTL_HOURS = 1


def admin_required(view):
    """Restrict a view to admin users."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            flash("Admin access only. Sign in with an admin account.", "error")
            return redirect(url_for("views.me"))
        return view(*args, **kwargs)

    return wrapped


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("views.home"))

    login_username = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        login_username = username
        user = db.session.execute(
            db.select(User).filter_by(username=username)
        ).scalar_one_or_none()

        if user is None or not user.check_password(password):
            flash("Invalid username or password.", "error")
        elif not user.enabled:
            flash("This account is disabled.", "error")
        else:
            login_user(user, remember=True)
            nxt = request.args.get("next") or ""
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = ""
            if nxt.startswith("/admin") and not user.is_admin:
                nxt = ""
            if nxt in ("/logout", "/login"):
                nxt = ""
            return redirect(nxt or url_for("views.home"))

    from .login_page import login_page_context

    ctx = login_page_context()
    ctx["login_username"] = login_username
    return render_template("login.html", **ctx)


@auth_bp.route("/logout")
@login_required
def logout():
    from .models import ROLE_EMPLOYEE, User

    user = None
    if current_user.is_authenticated and current_user.role == ROLE_EMPLOYEE:
        user = db.session.get(User, current_user.id)
    logout_user()
    if user is not None:
        from .ai_report import maybe_send_ai_report_on_logout

        maybe_send_ai_report_on_logout(user)
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("views.home"))

    if request.method == "POST":
        identity = (request.form.get("identity") or "").strip()
        # Always show the same message (do not leak whether the account exists).
        generic = (
            "If that account has an email on file, we sent a reset link. "
            "Check your inbox (and spam)."
        )
        if not identity:
            flash("Enter your username or email.", "error")
            return render_template("forgot_password.html", **login_page_context())

        user = db.session.execute(
            db.select(User).filter(
                or_(
                    User.username == identity,
                    func.lower(User.email) == identity.lower(),
                )
            )
        ).scalar_one_or_none()

        if user and user.enabled and (user.email or "").strip():
            token = secrets.token_urlsafe(32)
            row = PasswordResetToken(
                user_id=user.id,
                token=token,
                expires_at=_utcnow().replace(tzinfo=None)
                + timedelta(hours=_RESET_TTL_HOURS),
            )
            db.session.add(row)
            db.session.commit()

            reset_url = url_for("auth.reset_password", token=token, _external=True)
            name = user.name or user.username
            text, html = render_email(
                "password_reset",
                name=name,
                username=user.username,
                reset_url=reset_url,
                ttl_hours=_RESET_TTL_HOURS,
            )
            cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
            ok, err = send_email(
                to=user.email.strip(),
                subject="Reset your esstracker password",
                text_body=text,
                html_body=html,
                data_dir=cfg.data_dir,
            )
            if not ok:
                current_app.logger.error("password reset email failed: %s", err)
                flash(
                    "We could not send email right now. Contact your admin, "
                    "or try again later.",
                    "error",
                )
                return render_template("forgot_password.html", **login_page_context())

        flash(generic, "info")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html", **login_page_context())


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    if current_user.is_authenticated:
        return redirect(url_for("views.home"))

    row = db.session.execute(
        db.select(PasswordResetToken).filter_by(token=token)
    ).scalar_one_or_none()
    now = _utcnow()
    valid = (
        row is not None
        and row.used_at is None
        and (row.expires_at.replace(tzinfo=None) if row.expires_at.tzinfo else row.expires_at)
        > now.replace(tzinfo=None)
    )
    if not valid:
        flash("This reset link is invalid or has expired. Request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    user = db.session.get(User, row.user_id)
    if user is None or not user.enabled:
        flash("This account cannot be reset.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("reset_password.html", token=token)
        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)
        user.set_password(password)
        row.used_at = now
        db.session.commit()
        flash("Password updated. You can sign in now.", "info")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)


__all__ = ["auth_bp", "admin_required"]
