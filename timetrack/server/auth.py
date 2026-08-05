"""Authentication: login / logout and access-control helpers."""

from __future__ import annotations

from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from .extensions import db
from .models import User
from .security import client_ip_allowed

auth_bp = Blueprint("auth", __name__)

# Session key holding the id of a user who passed the password check but
# still needs to submit a valid TOTP code to complete login.
_MFA_SESSION_KEY = "mfa_pending_user_id"


def admin_required(view):
    """Restrict a view to admin users, honoring the admin IP allow-list."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        cfg = current_app.config.get("TIMETRACK_SERVER_CONFIG")
        allowlist = getattr(cfg, "admin_ip_allowlist", None) or []
        if allowlist and not client_ip_allowed(request.remote_addr, allowlist):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _complete_login(user: User):
    login_user(user)
    session.pop(_MFA_SESSION_KEY, None)
    nxt = request.args.get("next")
    return redirect(nxt or url_for("views.home"))


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
        elif user.mfa_enabled:
            session[_MFA_SESSION_KEY] = user.id
            return redirect(url_for("auth.login_mfa", next=request.args.get("next")))
        else:
            return _complete_login(user)

    return render_template("login.html")


@auth_bp.route("/login/verify", methods=["GET", "POST"])
def login_mfa():
    """Second login step: verify a TOTP code for users with MFA enabled."""
    if current_user.is_authenticated:
        return redirect(url_for("views.home"))

    user_id = session.get(_MFA_SESSION_KEY)
    user = db.session.get(User, user_id) if user_id else None
    if user is None:
        flash("Please sign in again.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        if user.check_mfa_code(code):
            return _complete_login(user)
        flash("Invalid authentication code.", "error")

    return render_template("login_mfa.html", username=user.username)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


__all__ = ["auth_bp", "admin_required", "login_mfa"]
