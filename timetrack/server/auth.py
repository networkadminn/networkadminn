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
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from ..security import ip_allowed, is_safe_redirect_target
from .extensions import db
from .models import User

auth_bp = Blueprint("auth", __name__)


def admin_required(view):
    """Restrict a view to admin users (optionally from allowlisted IPs only)."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        allowlist = current_app.config.get("TIMETRACK_ADMIN_IP_NETS")
        if not ip_allowed(request.remote_addr, allowlist):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


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
            nxt = request.args.get("next")
            if not is_safe_redirect_target(nxt):
                nxt = None
            return redirect(nxt or url_for("views.home"))

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


__all__ = ["auth_bp", "admin_required"]
