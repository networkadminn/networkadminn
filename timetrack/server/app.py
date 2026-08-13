"""Application factory for the TimeTrack server."""

from __future__ import annotations

import os

from flask import Flask

from ..paths import package_dir
from .config import ServerConfig
from .extensions import db, login_manager


def create_app(config: ServerConfig | None = None, **overrides) -> Flask:
    cfg = (config or ServerConfig())
    for key, value in overrides.items():
        setattr(cfg, key, value)
    cfg.finalize()

    root = package_dir("server")
    app = Flask(
        __name__,
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
    )
    app.config.update(
        SECRET_KEY=cfg.secret_key,
        SQLALCHEMY_DATABASE_URI=cfg.database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,  # 25 MB screenshot cap
        TIMETRACK_SERVER_CONFIG=cfg,
    )

    db.init_app(app)
    login_manager.init_app(app)

    from .models import User

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    from .api import api_bp
    from .auth import auth_bp
    from .desk import desk_bp
    from .saas import check_request_org_access, saas_bp
    from .views import views_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(desk_bp)
    app.register_blueprint(saas_bp)

    @app.before_request
    def _saas_org_gate():
        return check_request_org_access()

    @app.context_processor
    def inject_desk_flags():
        from flask_login import current_user

        flags = {
            "private_active": False,
            "timer_running": False,
            "alert_count": 0,
            "pending_approvals": 0,
            "workspace_org": None,
            "workspace_name": "Timeforge",
            "trial_banner": None,
        }
        if current_user.is_authenticated:
            from .models import (
                ORG_TRIAL,
                OfflineRequest,
                Organization,
                PrivatePeriod,
                TimerSession,
            )
            from .settings_util import get_settings
            from .tenancy import current_org_id, org_user_ids, seat_count

            flags["private_active"] = (
                db.session.execute(
                    db.select(PrivatePeriod).filter_by(
                        user_id=current_user.id, active=True
                    )
                ).scalar_one_or_none()
                is not None
            )
            flags["timer_running"] = (
                db.session.execute(
                    db.select(TimerSession).filter_by(
                        user_id=current_user.id, running=True
                    )
                ).scalar_one_or_none()
                is not None
            )
            org = db.session.get(Organization, current_org_id())
            if org:
                org.refresh_access_status()
                flags["workspace_org"] = org
                settings = get_settings(org.id)
                flags["workspace_name"] = (
                    (settings.company_name or "").strip() or org.name or "Timeforge"
                )
                if org.status == ORG_TRIAL and not current_user.is_superadmin:
                    days = org.days_left
                    flags["trial_banner"] = {
                        "days_left": days,
                        "plan": org.plan,
                        "seats_used": seat_count(org.id),
                        "max_seats": org.max_seats,
                    }
            if current_user.is_admin:
                uids = org_user_ids(current_org_id())
                pending = 0
                if uids:
                    pending = (
                        db.session.execute(
                            db.select(db.func.count(OfflineRequest.id)).filter(
                                OfflineRequest.status == "pending",
                                OfflineRequest.user_id.in_(uids),
                            )
                        ).scalar_one()
                        or 0
                    )
                flags["pending_approvals"] = int(pending)
                flags["alert_count"] = int(pending)
        return flags

    # Harden sessions behind HTTPS proxies (Cloudflare / aaPanel).
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("PREFERRED_URL_SCHEME", "https")
    if os.environ.get("TIMETRACK_SECURE_COOKIES", "").lower() in ("1", "true", "yes"):
        app.config["SESSION_COOKIE_SECURE"] = True

    # Correct external URLs (password-reset links) behind reverse proxy.
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    except Exception:
        pass

    with app.app_context():
        from .saas import ensure_platform_superadmin
        from .settings_util import ensure_schema, get_settings

        ensure_schema()
        ensure_platform_superadmin()
        get_settings()

    return app


__all__ = ["create_app"]
