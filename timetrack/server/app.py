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
    from .views import views_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(desk_bp)

    @app.context_processor
    def inject_desk_flags():
        from flask_login import current_user

        flags = {
            "private_active": False,
            "timer_running": False,
            "alert_count": 0,
            "pending_approvals": 0,
        }
        if current_user.is_authenticated:
            from .models import OfflineRequest, PrivatePeriod, TimerSession

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
            if current_user.is_admin:
                pending = (
                    db.session.execute(
                        db.select(db.func.count(OfflineRequest.id)).filter_by(
                            status="pending"
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
        from .settings_util import ensure_schema, get_settings

        ensure_schema()
        get_settings()

    return app


__all__ = ["create_app"]
