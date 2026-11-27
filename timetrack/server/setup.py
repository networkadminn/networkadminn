"""First-time setup wizard (company brand + first admin)."""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from .setup_util import SetupPayload, complete_setup, needs_setup

setup_bp = Blueprint("setup", __name__)

_SETUP_TIMEZONES = [
    ("Asia/Kolkata", "India (IST) — Asia/Kolkata"),
    ("Asia/Dubai", "Dubai (GST)"),
    ("Asia/Singapore", "Singapore"),
    ("Europe/London", "London (UK)"),
    ("America/New_York", "New York (US Eastern)"),
    ("America/Los_Angeles", "Los Angeles (US Pacific)"),
    ("UTC", "UTC"),
]


@setup_bp.route("/setup", methods=["GET", "POST"])
def setup():
    # Local design preview (Flask debug only) — does not mutate data.
    design_preview = bool(
        current_app.debug and request.args.get("design") == "1" and request.method == "GET"
    )
    if not design_preview:
        if not needs_setup():
            return redirect(url_for("auth.login"))
        if current_user.is_authenticated:
            return redirect(url_for("views.home"))

    errors: list[str] = []
    form = {
        "company_name": "Euclidee Software Solutions",
        "company_website": "https://euclideesolutions.com",
        "company_email": "admin@euclideesolutions.com",
        "timezone": "Asia/Kolkata",
        "admin_name": "Local Admin",
        "username": "admin",
        "admin_email": "admin@euclideesolutions.com",
    }

    if request.method == "POST":
        form = {
            "company_name": (request.form.get("company_name") or "").strip(),
            "company_website": (request.form.get("company_website") or "").strip(),
            "company_email": (request.form.get("company_email") or "").strip(),
            "timezone": (request.form.get("timezone") or "Asia/Kolkata").strip(),
            "admin_name": (request.form.get("admin_name") or "").strip(),
            "username": (request.form.get("username") or "").strip(),
            "admin_email": (request.form.get("admin_email") or "").strip(),
        }
        password = request.form.get("password") or ""
        confirm = request.form.get("password_confirm") or ""
        if password != confirm:
            errors.append("Passwords do not match.")
        if not form["company_name"]:
            errors.append("Company / brand name is required.")

        if not errors:
            cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
            try:
                complete_setup(
                    SetupPayload(
                        company_name=form["company_name"],
                        company_website=form["company_website"],
                        company_email=form["company_email"],
                        timezone=form["timezone"],
                        admin_name=form["admin_name"],
                        username=form["username"],
                        admin_email=form["admin_email"],
                        password=password,
                        logo=request.files.get("logo"),
                    ),
                    data_dir=cfg.data_dir,
                )
            except ValueError as exc:
                errors.append(str(exc))
            else:
                flash("Setup complete. Sign in with your admin account.", "info")
                return redirect(url_for("auth.login"))

        for msg in errors:
            flash(msg, "error")

    return render_template(
        "setup.html",
        form=form,
        timezones=_SETUP_TIMEZONES,
        errors=errors,
        design_preview=design_preview,
        force_auth_shell=True,
    )
