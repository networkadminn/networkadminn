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

from ..security import client_ip, ip_in_allowlist
from .extensions import db
from .models import User

auth_bp = Blueprint("auth", __name__)

# Session key holding the id of a user who passed the password step but still
# owes a TOTP code.
_PENDING_MFA = "pending_mfa_user_id"


def _admin_ip_allowed() -> bool:
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    ip = client_ip(request, trust_proxy=cfg.trust_proxy)
    return ip_in_allowlist(ip, cfg.admin_ip_allowlist)


def admin_required(view):
    """Restrict a view to admin users (optionally IP allow-listed)."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        if not _admin_ip_allowed():
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _safe_next() -> str | None:
    """Return the ``next`` target only if it's a local, relative path."""
    nxt = request.args.get("next") or request.form.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("views.home"))

    nxt = _safe_next()
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
            # Password OK — defer the actual login until the TOTP step.
            session[_PENDING_MFA] = user.id
            return redirect(url_for("auth.login_mfa", next=nxt))
        else:
            login_user(user)
            return redirect(nxt or url_for("views.home"))

    return render_template("login.html", next=nxt)


@auth_bp.route("/login/mfa", methods=["GET", "POST"])
def login_mfa():
    if current_user.is_authenticated:
        return redirect(url_for("views.home"))

    user_id = session.get(_PENDING_MFA)
    if not user_id:
        return redirect(url_for("auth.login"))
    user = db.session.get(User, user_id)
    if user is None or not user.enabled or not user.mfa_enabled:
        session.pop(_PENDING_MFA, None)
        return redirect(url_for("auth.login"))

    nxt = _safe_next()
    if request.method == "POST":
        code = request.form.get("code") or ""
        if user.verify_totp(code):
            session.pop(_PENDING_MFA, None)
            login_user(user)
            return redirect(nxt or url_for("views.home"))
        flash("Invalid authentication code.", "error")

    return render_template("mfa.html", next=nxt)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop(_PENDING_MFA, None)
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


__all__ = ["auth_bp", "admin_required"]
