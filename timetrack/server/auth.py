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

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = db.session.execute(
            db.select(User).filter_by(username=username)
        ).scalar_one_or_none()

        if user is None or not user.check_password(password):
            flash("Invalid username or password.", "error")
        elif not user.enabled:
            flash("This account is disabled.", "error")
        else:
            login_user(user)
            nxt = request.args.get("next") or ""
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = ""
            if nxt.startswith("/admin") and not user.is_admin:
                nxt = ""
            if nxt in ("/logout", "/login"):
                nxt = ""
            return redirect(nxt or url_for("views.home"))

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
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
            return render_template("forgot_password.html")

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
            text = (
                f"Hi {name},\n\n"
                f"Reset your esstracker password using this link "
                f"(valid {_RESET_TTL_HOURS} hour):\n\n{reset_url}\n\n"
                "If you did not request this, you can ignore this email.\n\n"
                "— ESS Tracker\n"
            )
            html = f"""
            <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;color:#0F291C">
              <h2 style="color:#0B7A4B">Reset your esstracker password</h2>
              <p>Hi {name},</p>
              <p>We received a request to reset your password. This link expires in
              {_RESET_TTL_HOURS} hour.</p>
              <p style="margin:28px 0">
                <a href="{reset_url}"
                   style="background:#0B7A4B;color:#fff;padding:12px 20px;border-radius:8px;
                          text-decoration:none;font-weight:700">
                  Choose a new password
                </a>
              </p>
              <p style="font-size:13px;color:#5F7A6A">Or paste this URL:<br/>
                <a href="{reset_url}">{reset_url}</a></p>
              <p style="font-size:12px;color:#5F7A6A">If you did not request this, ignore this email.</p>
            </div>
            """
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
                return render_template("forgot_password.html")

        flash(generic, "info")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html")


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
