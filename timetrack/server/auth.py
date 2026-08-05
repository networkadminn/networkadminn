"""Authentication: login / logout and access-control helpers."""

from __future__ import annotations

import ipaddress
from functools import wraps
from urllib.parse import urlsplit

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

from .extensions import db
from .models import User

auth_bp = Blueprint("auth", __name__)


def _is_safe_redirect(target: str | None) -> bool:
    if not target:
        return False
    ref = urlsplit(request.host_url)
    test = urlsplit(target)
    return (
        (not test.scheme or test.scheme in {"http", "https"})
        and (not test.netloc or test.netloc == ref.netloc)
    )


def _admin_ip_allowed(remote_addr: str | None) -> bool:
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    allowlist = cfg.admin_allowed_ips
    if not allowlist:
        return True
    if not remote_addr:
        return False
    try:
        client_ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False

    for entry in allowlist:
        try:
            if "/" in entry:
                if client_ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif client_ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def admin_required(view):
    """Restrict a view to admin users."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        if not _admin_ip_allowed(request.remote_addr):
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
            return redirect(nxt if _is_safe_redirect(nxt) else url_for("views.home"))

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


__all__ = ["auth_bp", "admin_required"]
