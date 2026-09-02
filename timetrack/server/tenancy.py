"""Organization scoping helpers (single-company + optional org folders)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import current_app
from flask_login import current_user

from .extensions import db
from .models import ORG_ACTIVE, PLAN_BUSINESS, Organization, Project, Team, User


def current_org_id() -> int:
    try:
        if current_user.is_authenticated:
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
            tenant_path="tenants/default",
        )
        db.session.add(org)
        db.session.commit()
    else:
        changed = False
        if org.slug == "default" and org.status != ORG_ACTIVE:
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
    return _data_dir() / rel


def _ensure_tenant_folder(org: Organization) -> Path:
    """Create per-org directory for rules / exports metadata."""
    root = tenant_root(org)
    for sub in ("", "exports"):
        (root / sub if sub else root).mkdir(parents=True, exist_ok=True)
    meta = {
        "organization_id": org.id,
        "slug": org.slug,
        "name": org.name,
    }
    meta_path = root / "tenant.json"
    try:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass
    return root
