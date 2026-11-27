"""First-run setup detection and completion (Docker / self-hosted)."""

from __future__ import annotations

from dataclasses import dataclass

from werkzeug.datastructures import FileStorage

from .extensions import db
from .models import ROLE_ADMIN, ROLE_SUPERADMIN, User
from .settings_util import get_settings
from .tenancy import ensure_default_organization


def needs_setup() -> bool:
    """True when no admin account exists yet."""
    n = db.session.execute(
        db.select(db.func.count())
        .select_from(User)
        .filter(User.role.in_((ROLE_ADMIN, ROLE_SUPERADMIN)))
    ).scalar_one()
    return int(n or 0) == 0


@dataclass
class SetupPayload:
    company_name: str
    company_website: str
    company_email: str
    timezone: str
    admin_name: str
    username: str
    admin_email: str
    password: str
    logo: FileStorage | None = None


def complete_setup(payload: SetupPayload, *, data_dir: str) -> User:
    """Persist branding + create the first admin. Caller must check needs_setup()."""
    from .brand_util import save_company_logo

    company_name = (payload.company_name or "").strip()[:200] or "ESS Tracker"
    website = (payload.company_website or "").strip()[:300]
    company_email = (payload.company_email or "").strip()[:200]
    tz = (payload.timezone or "Asia/Kolkata").strip()[:64] or "Asia/Kolkata"
    admin_name = (payload.admin_name or "").strip()[:200]
    username = (payload.username or "").strip()[:80]
    admin_email = (payload.admin_email or "").strip()[:200] or company_email
    password = payload.password or ""

    if not admin_name:
        raise ValueError("Admin name is required.")
    if not username:
        raise ValueError("Username is required.")
    if " " in username or "/" in username:
        raise ValueError("Username cannot contain spaces.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")

    existing = db.session.execute(
        db.select(User).filter_by(username=username)
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError("That username is already taken.")

    org = ensure_default_organization()
    org.name = company_name
    if company_email:
        org.billing_email = company_email

    settings = get_settings(org.id)
    settings.company_name = company_name
    settings.timezone = tz
    settings.company_website = website

    if payload.logo and payload.logo.filename:
        settings.company_logo = save_company_logo(payload.logo, data_dir)
    elif not (settings.company_logo or "").strip():
        settings.company_logo = ""

    user = User(
        username=username,
        email=admin_email,
        display_name=admin_name,
        role=ROLE_ADMIN,
        organization_id=org.id,
        timezone=tz,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user
