"""Multi-tenant (SaaS) helpers — organization scoping + tenant folders."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app
from flask_login import current_user

from .extensions import db
from .models import (
    ORG_ACTIVE,
    ORG_EXPIRED,
    ORG_PENDING,
    ORG_SUSPENDED,
    ORG_TRIAL,
    PLAN_BUSINESS,
    PLAN_TRIAL,
    TRIAL_DAYS,
    CompanySettings,
    Organization,
    Project,
    ROLE_ADMIN,
    Team,
    User,
    generate_token,
)


def current_org_id() -> int:
    try:
        if current_user.is_authenticated:
            if getattr(current_user, "is_superadmin", False):
                # Superadmin may impersonate via session later; default org 1
                return int(getattr(current_user, "organization_id", None) or 1)
            return int(getattr(current_user, "organization_id", None) or 1)
    except RuntimeError:
        pass
    except AttributeError:
        pass
    return 1


def ensure_default_organization() -> Organization:
    org = db.session.get(Organization, 1)
    if org is None:
        org = Organization(
            id=1,
            slug="default",
            name="Default organization",
            plan=PLAN_BUSINESS,
            status=ORG_ACTIVE,
            email_confirmed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            trial_ends_at=None,
            paid_until=None,
            tenant_path="tenants/default",
        )
        db.session.add(org)
        db.session.commit()
    else:
        # Legacy default org is always paid/active (platform home company).
        changed = False
        if org.slug == "default" and org.status not in (ORG_ACTIVE,):
            org.status = ORG_ACTIVE
            org.plan = PLAN_BUSINESS
            changed = True
        if not (org.tenant_path or "").strip():
            org.tenant_path = "tenants/default"
            changed = True
        if changed:
            db.session.commit()
    _ensure_tenant_folder(org)
    return org


def users_in_org_query(org_id: int | None = None):
    oid = org_id if org_id is not None else current_org_id()
    return db.select(User).filter(User.organization_id == oid)


def teams_in_org_query(org_id: int | None = None):
    oid = org_id if org_id is not None else current_org_id()
    return db.select(Team).filter(Team.organization_id == oid)


def projects_in_org_query(org_id: int | None = None):
    oid = org_id if org_id is not None else current_org_id()
    return db.select(Project).filter(Project.organization_id == oid)


def assert_same_org(user: User, org_id: int | None = None) -> bool:
    oid = org_id if org_id is not None else current_org_id()
    return int(getattr(user, "organization_id", None) or 1) == int(oid)


def _data_dir() -> Path:
    try:
        cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
        return Path(cfg.data_dir)
    except Exception:
        from ..userdirs import data_dir

        return data_dir("timetrack-server")


def tenant_root(org: Organization) -> Path:
    rel = (org.tenant_path or f"tenants/{org.slug}").strip().lstrip("/")
    root = _data_dir() / rel
    return root


def _ensure_tenant_folder(org: Organization) -> Path:
    """Create per-workspace directory (rules, screenshots, meta)."""
    root = tenant_root(org)
    for sub in ("", "screenshots", "exports"):
        (root / sub if sub else root).mkdir(parents=True, exist_ok=True)
    meta = {
        "organization_id": org.id,
        "slug": org.slug,
        "name": org.name,
        "plan": org.plan,
        "status": org.status,
        "isolation": "shared-db-row-level + tenant-folder",
        "note": (
            "Control-plane users/orgs live in the shared SQLite DB with "
            "organization_id scoping. This folder holds tenant files "
            "(screenshots, rules copy, exports). Future: optional per-tenant DB."
        ),
    }
    meta_path = root / "tenant.json"
    try:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass
    # Lightweight marker DB so each workspace has a dedicated sqlite file
    marker_db = root / "tenant.db"
    if not marker_db.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(str(marker_db))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tenant_meta "
                "(key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO tenant_meta(key,value) VALUES(?,?)",
                ("slug", org.slug),
            )
            conn.execute(
                "INSERT OR REPLACE INTO tenant_meta(key,value) VALUES(?,?)",
                ("organization_id", str(org.id)),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    return root


def create_organization(
    *,
    name: str,
    slug: str,
    admin_username: str,
    admin_password: str,
    admin_email: str = "",
    admin_display_name: str = "",
    require_email_confirm: bool = True,
) -> tuple[Organization, User]:
    """Provision a new tenant + first admin (SaaS signup)."""
    ensure_default_organization()
    slug = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in slug.strip().lower())
    slug = "-".join(p for p in slug.split("-") if p)[:40]
    name = name.strip()
    email = admin_email.strip()
    if not slug or not name:
        raise ValueError("Organization name and slug are required.")
    if require_email_confirm and (not email or "@" not in email):
        raise ValueError("A valid work email is required for confirmation.")
    if db.session.execute(db.select(Organization).filter_by(slug=slug)).scalar_one_or_none():
        raise ValueError(f"Organization slug {slug!r} is already taken.")
    if db.session.execute(db.select(User).filter_by(username=admin_username.strip())).scalar_one_or_none():
        raise ValueError(f"Username {admin_username!r} is already taken.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    trial_end = now + timedelta(days=TRIAL_DAYS)
    confirm_token = generate_token() if require_email_confirm else None

    org = Organization(
        slug=slug,
        name=name,
        plan=PLAN_TRIAL,
        status=ORG_PENDING if require_email_confirm else ORG_TRIAL,
        billing_email=email,
        trial_ends_at=trial_end,
        confirm_token=confirm_token,
        email_confirmed_at=None if require_email_confirm else now,
        tenant_path=f"tenants/{slug}",
        max_seats=25,
    )
    db.session.add(org)
    db.session.flush()

    admin = User(
        username=admin_username.strip(),
        display_name=admin_display_name.strip() or admin_username.strip(),
        email=email,
        role=ROLE_ADMIN,
        api_token=generate_token(),
        organization_id=org.id,
        enabled=True,
    )
    admin.set_password(admin_password)
    db.session.add(admin)

    settings = CompanySettings(organization_id=org.id, company_name=name)
    db.session.add(settings)
    db.session.commit()

    _ensure_tenant_folder(org)
    return org, admin


def confirm_organization(token: str) -> Organization | None:
    org = db.session.execute(
        db.select(Organization).filter_by(confirm_token=token)
    ).scalar_one_or_none()
    if org is None:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    org.email_confirmed_at = now
    org.confirm_token = None
    if org.trial_ends_at is None:
        org.trial_ends_at = now + timedelta(days=TRIAL_DAYS)
    org.status = ORG_TRIAL
    org.plan = PLAN_TRIAL
    db.session.commit()
    _ensure_tenant_folder(org)
    return org


def org_access_ok(org: Organization | None) -> tuple[bool, str]:
    if org is None:
        return False, "Workspace not found."
    status = org.refresh_access_status()
    db.session.commit()
    if status == ORG_PENDING:
        return False, "Confirm your email to activate this workspace."
    if status == ORG_SUSPENDED:
        return False, "This workspace is suspended. Contact support."
    if status == ORG_EXPIRED:
        return (
            False,
            "Your 15-day trial has ended. Contact sales or your platform admin to activate a paid plan.",
        )
    if status in (ORG_TRIAL, ORG_ACTIVE):
        return True, ""
    return False, "Workspace access denied."



def org_user_ids(org_id: int | None = None) -> list[int]:
    oid = org_id if org_id is not None else current_org_id()
    rows = db.session.execute(
        db.select(User.id).filter(User.organization_id == oid)
    ).scalars()
    return [int(x) for x in rows]


def get_org_user(user_id: int, org_id: int | None = None) -> User | None:
    """Load a user only if they belong to the given (or current) org."""
    user = db.session.get(User, user_id)
    if user is None:
        return None
    if not assert_same_org(user, org_id):
        return None
    return user


def seat_count(org_id: int | None = None) -> int:
    oid = org_id if org_id is not None else current_org_id()
    return int(
        db.session.execute(
            db.select(db.func.count(User.id)).filter(User.organization_id == oid)
        ).scalar_one()
        or 0
    )


def can_add_seat(org: Organization | None = None) -> tuple[bool, str]:
    if org is None:
        org = db.session.get(Organization, current_org_id())
    if org is None:
        return False, "Workspace not found."
    used = seat_count(org.id)
    max_seats = int(org.max_seats or 0)
    if max_seats > 0 and used >= max_seats:
        return False, f"Seat limit reached ({used}/{max_seats}). Upgrade your plan."
    return True, ""
