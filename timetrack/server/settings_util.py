"""Company settings helpers + lightweight SQLite column migration."""

from __future__ import annotations

import json
import os

from flask import current_app
from sqlalchemy import inspect, text

from ..config import (
    DEFAULT_RULES,
    DEFAULT_SITE_RULES,
    PRODUCTIVE,
    SITES_PRODUCTIVE,
    SITES_UNPRODUCTIVE,
    UNPRODUCTIVE,
)
from .extensions import db
from .models import CompanySettings, Organization


def get_settings(org_id: int | None = None) -> CompanySettings:
    from .tenancy import current_org_id, ensure_default_organization

    ensure_default_organization()
    oid = org_id if org_id is not None else current_org_id()
    row = db.session.execute(
        db.select(CompanySettings).filter_by(organization_id=oid)
    ).scalar_one_or_none()
    if row is None:
        legacy = db.session.get(CompanySettings, 1)
        if legacy is not None and getattr(legacy, "organization_id", None) in (None, oid):
            if not getattr(legacy, "organization_id", None):
                legacy.organization_id = oid
                db.session.commit()
            row = legacy
        else:
            row = CompanySettings(organization_id=oid)
            db.session.add(row)
            db.session.commit()
    # Normalize older DBs after column adds (SQLite may leave NULLs).
    changed = False
    if not (row.timezone or "").strip():
        row.timezone = "Asia/Kolkata"
        changed = True
    if row.office_start_hour is None:
        row.office_start_hour = float(row.expected_arrival_hour or 9.5)
        changed = True
    if row.office_end_hour is None:
        row.office_end_hour = 18.5
        changed = True
    if not (row.work_days or "").strip():
        row.work_days = "0,1,2,3,4"
        changed = True
    if changed:
        db.session.commit()
    return row


def company_tz_name() -> str:
    return (get_settings().timezone or "Asia/Kolkata").strip() or "Asia/Kolkata"


def rules_path(org_id: int | None = None) -> str:
    """Per-organization category rules (tenant folder, then legacy orgs/)."""
    from .tenancy import current_org_id, tenant_root

    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    oid = org_id if org_id is not None else current_org_id()
    org = db.session.get(Organization, oid)
    if org is not None:
        tenant_rules = str(tenant_root(org) / "rules.json")
        if os.path.isfile(tenant_rules):
            return tenant_rules
        # Prefer writing new rules into the tenant folder.
        try:
            os.makedirs(os.path.dirname(tenant_rules), exist_ok=True)
        except OSError:
            pass
        legacy_org = os.path.join(cfg.data_dir, "orgs", str(oid), "rules.json")
        if os.path.isfile(legacy_org):
            return legacy_org
        global_legacy = os.path.join(cfg.data_dir, "rules.json")
        if oid == 1 and os.path.isfile(global_legacy):
            try:
                with open(global_legacy, encoding="utf-8") as src, open(
                    tenant_rules, "w", encoding="utf-8"
                ) as dst:
                    dst.write(src.read())
                return tenant_rules
            except OSError:
                return global_legacy
        return tenant_rules

    org_dir = os.path.join(cfg.data_dir, "orgs", str(oid))
    os.makedirs(org_dir, exist_ok=True)
    return os.path.join(org_dir, "rules.json")


def org_screenshot_dir(org_id: int | None = None) -> str:
    """Screenshot root for an org (under shared screenshots_dir for URL serving)."""
    from .tenancy import current_org_id

    cfg = current_app.config["TIMETRACK_SERVER_CONFIG"]
    oid = org_id if org_id is not None else current_org_id()
    path = os.path.join(cfg.screenshots_dir, f"org-{oid}")
    os.makedirs(path, exist_ok=True)
    return path


def load_server_rules(org_id: int | None = None) -> dict[str, list[str]]:
    path = rules_path(org_id)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {
            PRODUCTIVE: list(data.get(PRODUCTIVE, [])),
            UNPRODUCTIVE: list(data.get(UNPRODUCTIVE, [])),
            "neutral": list(data.get("neutral", [])),
            SITES_PRODUCTIVE: list(data.get(SITES_PRODUCTIVE, [])),
            SITES_UNPRODUCTIVE: list(data.get(SITES_UNPRODUCTIVE, [])),
        }
    return {
        PRODUCTIVE: list(DEFAULT_RULES.get(PRODUCTIVE, [])),
        UNPRODUCTIVE: list(DEFAULT_RULES.get(UNPRODUCTIVE, [])),
        "neutral": [],
        SITES_PRODUCTIVE: list(DEFAULT_SITE_RULES.get(SITES_PRODUCTIVE, [])),
        SITES_UNPRODUCTIVE: list(DEFAULT_SITE_RULES.get(SITES_UNPRODUCTIVE, [])),
    }


def ensure_schema() -> None:
    """Add new columns/tables for upgrades on existing SQLite databases."""
    db.create_all()
    engine = db.engine
    insp = inspect(engine)
    tables = set(insp.get_table_names())

    def cols(table: str) -> set[str]:
        if table not in tables:
            return set()
        # Re-inspect each time — SQLite ALTER is visible only after refresh.
        return {c["name"] for c in inspect(engine).get_columns(table)}

    alters: list[tuple[str, str, str]] = [
        ("users", "team_id", "INTEGER"),
        ("users", "private_time_allowed", "BOOLEAN DEFAULT 1"),
        ("users", "screenshots_enabled", "BOOLEAN"),
        ("users", "screenshot_interval", "INTEGER"),
        ("activities", "project_id", "INTEGER"),
        ("activities", "task_id", "INTEGER"),
        ("activities", "url", "VARCHAR(500) DEFAULT ''"),
        ("screenshots", "blurred", "BOOLEAN DEFAULT 0"),
        ("screenshots", "is_unproductive", "BOOLEAN DEFAULT 0"),
        ("manual_entries", "task_id", "INTEGER"),
        ("company_settings", "expected_hours", "FLOAT DEFAULT 8.0"),
        ("company_settings", "expected_arrival_hour", "FLOAT DEFAULT 9.5"),
        ("company_settings", "screenshots_enabled", "BOOLEAN DEFAULT 1"),
        ("company_settings", "screenshot_blur", "BOOLEAN DEFAULT 0"),
        ("company_settings", "screenshot_interval", "INTEGER DEFAULT 300"),
        ("company_settings", "screenshot_random", "BOOLEAN DEFAULT 1"),
        ("company_settings", "private_time_enabled", "BOOLEAN DEFAULT 1"),
        ("company_settings", "offline_requires_approval", "BOOLEAN DEFAULT 0"),
        ("company_settings", "company_name", "VARCHAR(200) DEFAULT 'Euclidee Software Solutions Private Limited'"),
        ("company_settings", "idle_threshold", "INTEGER DEFAULT 180"),
        ("company_settings", "office_start_hour", "FLOAT DEFAULT 9.5"),
        ("company_settings", "office_end_hour", "FLOAT DEFAULT 18.5"),
        ("company_settings", "work_days", "VARCHAR(32) DEFAULT '0,1,2,3,4'"),
        ("company_settings", "timezone", "VARCHAR(64) DEFAULT 'Asia/Kolkata'"),
        ("company_settings", "organization_id", "INTEGER DEFAULT 1"),
        ("users", "organization_id", "INTEGER DEFAULT 1"),
        ("teams", "organization_id", "INTEGER DEFAULT 1"),
        ("projects", "organization_id", "INTEGER DEFAULT 1"),
        ("organizations", "trial_ends_at", "DATETIME"),
        ("organizations", "paid_until", "DATETIME"),
        ("organizations", "email_confirmed_at", "DATETIME"),
        ("organizations", "confirm_token", "VARCHAR(64)"),
        ("organizations", "billing_email", "VARCHAR(200) DEFAULT ''"),
        ("organizations", "max_seats", "INTEGER DEFAULT 25"),
        ("organizations", "tenant_path", "VARCHAR(300) DEFAULT ''"),
        ("organizations", "notes", "TEXT DEFAULT ''"),
    ]
    with engine.begin() as conn:
        for table, col, typ in alters:
            if table in tables and col not in cols(table):
                try:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
                except Exception as exc:
                    # Race / cached inspector: ignore duplicate column.
                    if "duplicate column" not in str(exc).lower():
                        raise

    # Refresh default org + tenant folders after column migrations.
    from .tenancy import ensure_default_organization as _ensure_org

    _ensure_org()
