"""SaaS gates: trial, confirmation, invites, billing, superadmin."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user

from .auth import admin_required
from .extensions import db
from .mail import send_email
from .models import (
    ORG_ACTIVE,
    ORG_PENDING,
    ORG_SUSPENDED,
    ORG_TRIAL,
    PLAN_BUSINESS,
    PLAN_STARTER,
    PLAN_TRIAL,
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_SUPERADMIN,
    TRIAL_DAYS,
    Invitation,
    Organization,
    User,
    generate_token,
)
from .tenancy import (
    can_add_seat,
    confirm_organization,
    current_org_id,
    org_access_ok,
    seat_count,
)

saas_bp = Blueprint("saas", __name__)


def superadmin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_superadmin", False):
            flash("Platform super-admin access only.", "error")
            return redirect(url_for("views.home"))
        return view(*args, **kwargs)

    return wrapped


def send_workspace_confirmation(org: Organization, admin: User) -> tuple[bool, str]:
    if not org.confirm_token or not (admin.email or "").strip():
        return False, "missing token or email"
    confirm_url = url_for("saas.confirm_email", token=org.confirm_token, _external=True)
    name = admin.name or admin.username
    text = (
        f"Hi {name},\n\n"
        f"Confirm your Timeforge workspace «{org.name}» ({org.slug}).\n\n"
        f"Click this link to activate your {TRIAL_DAYS}-day free trial:\n"
        f"{confirm_url}\n\n"
        "If you did not create this workspace, ignore this email.\n\n"
        "— Timeforge\n"
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#0F291C">
      <h2 style="color:#0B7A4B">Confirm your Timeforge workspace</h2>
      <p>Hi {name},</p>
      <p>You created workspace <strong>{org.name}</strong>
      (<code>{org.slug}</code>).</p>
      <p>Confirm your email to start a <strong>{TRIAL_DAYS}-day free trial</strong>.</p>
      <p style="margin:28px 0">
        <a href="{confirm_url}"
           style="background:#0B7A4B;color:#fff;padding:12px 20px;border-radius:8px;
                  text-decoration:none;font-weight:700">Confirm &amp; start trial</a>
      </p>
      <p style="font-size:13px;color:#5F7A6A">Or paste:<br/>
        <a href="{confirm_url}">{confirm_url}</a></p>
    </div>
    """
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    return send_email(
        to=admin.email.strip(),
        subject=f"Confirm your Timeforge workspace ({org.slug})",
        text_body=text,
        html_body=html,
        data_dir=cfg.data_dir,
    )


def send_employee_invite(
    invite: Invitation, org: Organization, inviter: User | None = None
) -> tuple[bool, str]:
    accept_url = url_for("saas.invite_accept", token=invite.token, _external=True)
    name = invite.display_name or invite.email
    who = (inviter.name if inviter else "Your admin")
    text = (
        f"Hi {name},\n\n"
        f"{who} invited you to join «{org.name}» on Timeforge.\n\n"
        f"Accept your invite and set a password:\n{accept_url}\n\n"
        "This link expires in 7 days.\n\n— Timeforge\n"
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#0F291C">
      <h2 style="color:#0B7A4B">You're invited to {org.name}</h2>
      <p>Hi {name},</p>
      <p><strong>{who}</strong> invited you to the Timeforge workspace
      <strong>{org.name}</strong>.</p>
      <p style="margin:28px 0">
        <a href="{accept_url}"
           style="background:#0B7A4B;color:#fff;padding:12px 20px;border-radius:8px;
                  text-decoration:none;font-weight:700">Accept invite</a>
      </p>
      <p style="font-size:13px;color:#5F7A6A">Or paste:<br/>
        <a href="{accept_url}">{accept_url}</a></p>
    </div>
    """
    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    return send_email(
        to=invite.email.strip(),
        subject=f"Join {org.name} on Timeforge",
        text_body=text,
        html_body=html,
        data_dir=cfg.data_dir,
    )


def ensure_platform_superadmin() -> None:
    username = (
        os.environ.get("TIMEFORGE_SUPERADMIN_USER")
        or os.environ.get("ESSTRACKER_SUPERADMIN_USER")
        or "boss"
    ).strip()
    if not username:
        return
    user = db.session.execute(
        db.select(User).filter_by(username=username)
    ).scalar_one_or_none()
    if user is None:
        return
    if user.role != ROLE_SUPERADMIN:
        user.role = ROLE_SUPERADMIN
        db.session.commit()


def check_request_org_access():
    if not current_user.is_authenticated:
        return None
    if getattr(current_user, "is_superadmin", False):
        return None
    ep = request.endpoint or ""
    if (
        ep.startswith("auth.")
        or ep.startswith("saas.confirm")
        or ep.startswith("saas.invite")
        or ep
        in (
            "saas.awaiting_confirm",
            "saas.billing_blocked",
            "saas.check_slug",
            "saas.resend_confirm",
            "static",
        )
    ):
        return None
    org = db.session.get(Organization, current_user.organization_id or 1)
    ok, msg = org_access_ok(org)
    if ok:
        return None
    if ep.startswith("api."):
        return jsonify({"error": msg, "code": "org_access_denied"}), 402
    flash(msg, "error")
    if org and org.status == ORG_PENDING:
        return redirect(url_for("saas.awaiting_confirm"))
    return redirect(url_for("saas.billing_blocked"))


@saas_bp.route("/confirm/<token>")
def confirm_email(token: str):
    org = confirm_organization(token)
    if org is None:
        flash("This confirmation link is invalid or already used.", "error")
        return redirect(url_for("auth.login"))
    flash(
        f"Workspace «{org.name}» confirmed. Your {TRIAL_DAYS}-day trial is active.",
        "info",
    )
    admin = db.session.execute(
        db.select(User)
        .filter_by(organization_id=org.id, role=ROLE_ADMIN)
        .order_by(User.id.asc())
    ).scalar_one_or_none()
    if admin and admin.enabled:
        login_user(admin)
        return redirect(url_for("views.admin"))
    return redirect(url_for("auth.login"))


@saas_bp.route("/awaiting-confirm")
@login_required
def awaiting_confirm():
    org = db.session.get(Organization, current_user.organization_id or 1)
    return render_template("awaiting_confirm.html", org=org, trial_days=TRIAL_DAYS)


@saas_bp.route("/resend-confirm", methods=["POST"])
@login_required
def resend_confirm():
    org = db.session.get(Organization, current_user.organization_id or 1)
    if org is None or org.status != ORG_PENDING:
        flash("Nothing to confirm.", "info")
        return redirect(url_for("saas.awaiting_confirm"))
    if not org.confirm_token:
        org.confirm_token = generate_token()
        db.session.commit()
    ok, detail = send_workspace_confirmation(org, current_user)
    if ok:
        flash("Confirmation email resent. Check your inbox.", "info")
    else:
        flash(f"Could not send email ({detail}). Ask the platform admin.", "error")
    return redirect(url_for("saas.awaiting_confirm"))


@saas_bp.route("/billing-blocked")
@login_required
def billing_blocked():
    org = db.session.get(Organization, current_user.organization_id or 1)
    if org:
        org.refresh_access_status()
        db.session.commit()
    return render_template("billing_blocked.html", org=org, trial_days=TRIAL_DAYS)


@saas_bp.route("/billing")
@login_required
@admin_required
def billing():
    org = db.session.get(Organization, current_org_id()) or abort(404)
    org.refresh_access_status()
    db.session.commit()
    return render_template(
        "billing.html",
        org=org,
        seats_used=seat_count(org.id),
        trial_days=TRIAL_DAYS,
        days_left=org.days_left,
    )


@saas_bp.route("/api/check-slug")
def check_slug():
    slug = (request.args.get("slug") or "").strip().lower()
    slug = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in slug)
    slug = "-".join(p for p in slug.split("-") if p)[:40]
    if len(slug) < 2:
        return jsonify({"ok": False, "slug": slug, "reason": "too short"})
    taken = (
        db.session.execute(
            db.select(Organization).filter_by(slug=slug)
        ).scalar_one_or_none()
        is not None
    )
    return jsonify(
        {"ok": not taken, "slug": slug, "reason": "taken" if taken else ""}
    )


@saas_bp.route("/invite", methods=["POST"])
@login_required
@admin_required
def invite_employee():
    org = db.session.get(Organization, current_org_id()) or abort(404)
    ok_seat, seat_msg = can_add_seat(org)
    if not ok_seat:
        flash(seat_msg, "error")
        return redirect(url_for("views.employees"))

    email = (request.form.get("email") or "").strip().lower()
    display_name = (request.form.get("display_name") or "").strip()
    role = ROLE_ADMIN if request.form.get("admin") else ROLE_EMPLOYEE
    if not email or "@" not in email:
        flash("A valid email is required to invite.", "error")
        return redirect(url_for("views.employees"))

    existing = db.session.execute(
        db.select(User).filter(
            db.func.lower(User.email) == email,
            User.organization_id == org.id,
        )
    ).scalar_one_or_none()
    if existing:
        flash("That email already belongs to someone in this workspace.", "error")
        return redirect(url_for("views.employees"))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    invite = Invitation(
        organization_id=org.id,
        email=email,
        display_name=display_name,
        role=role,
        token=generate_token(),
        invited_by_id=current_user.id,
        expires_at=now + timedelta(days=7),
    )
    db.session.add(invite)
    db.session.commit()
    ok, detail = send_employee_invite(invite, org, current_user)
    if ok:
        flash(f"Invite sent to {email}.", "info")
    else:
        link = url_for("saas.invite_accept", token=invite.token, _external=True)
        flash(
            f"Invite created, but email failed ({detail}). Share this link: {link}",
            "error",
        )
    return redirect(url_for("views.employees"))


@saas_bp.route("/invite/<token>", methods=["GET", "POST"])
def invite_accept(token: str):
    invite = db.session.execute(
        db.select(Invitation).filter_by(token=token)
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    exp = invite.expires_at if invite else None
    if exp is not None and getattr(exp, "tzinfo", None):
        exp = exp.replace(tzinfo=None)
    if invite is None or invite.accepted_at is not None or (exp is not None and exp < now):
        flash("This invite is invalid or has expired.", "error")
        return redirect(url_for("auth.login"))

    org = db.session.get(Organization, invite.organization_id)
    if org is None:
        flash("Workspace no longer exists.", "error")
        return redirect(url_for("auth.login"))
    ok, msg = org_access_ok(org)
    if not ok:
        flash(msg, "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        display_name = (
            (request.form.get("display_name") or "").strip()
            or invite.display_name
            or username
        )
        ok_seat, seat_msg = can_add_seat(org)
        if not ok_seat:
            flash(seat_msg, "error")
        elif len(username) < 2:
            flash("Choose a username.", "error")
        elif db.session.execute(
            db.select(User).filter_by(username=username)
        ).scalar_one_or_none():
            flash("That username is taken.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            user = User(
                username=username,
                display_name=display_name,
                email=invite.email,
                role=(
                    invite.role
                    if invite.role in (ROLE_ADMIN, ROLE_EMPLOYEE)
                    else ROLE_EMPLOYEE
                ),
                organization_id=org.id,
                api_token=generate_token(),
                enabled=True,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            invite.accepted_at = now
            invite.created_user_id = user.id
            db.session.commit()
            login_user(user)
            flash(f"Welcome to «{org.name}»!", "info")
            return redirect(url_for("views.home"))

    return render_template(
        "invite_accept.html",
        invitation=invite,
        org=org,
        email=invite.email,
        display_name=invite.display_name or "",
    )


@saas_bp.route("/superadmin", methods=["GET", "POST"])
@superadmin_required
def superadmin_console():
    if request.method == "POST":
        action = request.form.get("action") or ""
        oid = request.form.get("org_id", type=int)
        org = db.session.get(Organization, oid) if oid else None
        if org is None:
            flash("Workspace not found.", "error")
            return redirect(url_for("saas.superadmin_console"))

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if action == "activate_paid":
            months = request.form.get("months", type=int) or 12
            plan = request.form.get("plan") or PLAN_STARTER
            if plan not in (PLAN_STARTER, PLAN_BUSINESS):
                plan = PLAN_STARTER
            org.plan = plan
            org.status = ORG_ACTIVE
            org.paid_until = now + timedelta(days=30 * max(1, months))
            if org.email_confirmed_at is None:
                org.email_confirmed_at = now
                org.confirm_token = None
            seats = request.form.get("max_seats", type=int)
            if seats and seats > 0:
                org.max_seats = seats
            flash(f"Marked {org.slug} as paid ({plan}) for {months} month(s).", "info")
        elif action == "extend_trial":
            days = request.form.get("days", type=int) or TRIAL_DAYS
            base = org.trial_ends_at or now
            if base.tzinfo:
                base = base.replace(tzinfo=None)
            if base < now:
                base = now
            org.trial_ends_at = base + timedelta(days=max(1, days))
            org.status = ORG_TRIAL
            org.plan = PLAN_TRIAL
            flash(f"Extended trial for {org.slug} by {days} days.", "info")
        elif action == "suspend":
            org.status = ORG_SUSPENDED
            flash(f"Suspended {org.slug}.", "info")
        elif action == "unsuspend":
            org.status = ORG_ACTIVE if org.paid_until else ORG_TRIAL
            org.refresh_access_status()
            flash(f"Unsuspended {org.slug} → {org.status}.", "info")
        elif action == "confirm_email":
            org.email_confirmed_at = now
            org.confirm_token = None
            if org.status == ORG_PENDING:
                org.status = ORG_TRIAL
            flash(f"Email marked confirmed for {org.slug}.", "info")
        db.session.commit()
        return redirect(url_for("saas.superadmin_console"))

    orgs = list(
        db.session.execute(
            db.select(Organization).order_by(Organization.id.asc())
        ).scalars()
    )
    rows = []
    for org in orgs:
        org.refresh_access_status()
        seats = (
            db.session.execute(
                db.select(db.func.count(User.id)).filter(
                    User.organization_id == org.id
                )
            ).scalar_one()
            or 0
        )
        rows.append({"org": org, "seats": int(seats), "days_left": org.days_left})
    db.session.commit()
    return render_template(
        "superadmin.html",
        rows=rows,
        trial_days=TRIAL_DAYS,
        plans=(PLAN_TRIAL, PLAN_STARTER, PLAN_BUSINESS),
    )


__all__ = [
    "saas_bp",
    "superadmin_required",
    "send_workspace_confirmation",
    "send_employee_invite",
    "ensure_platform_superadmin",
    "check_request_org_access",
]
