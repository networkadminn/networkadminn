"""Application factory for the TimeTrack server."""

from __future__ import annotations

from flask import Flask

from ..security import SERVER_CSP, apply_security_headers, parse_ip_allowlist
from .config import ServerConfig
from .extensions import db, login_manager


def create_app(config: ServerConfig | None = None, **overrides) -> Flask:
    cfg = (config or ServerConfig())
    for key, value in overrides.items():
        setattr(cfg, key, value)
    cfg.finalize()

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=cfg.secret_key,
        SQLALCHEMY_DATABASE_URI=cfg.database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,  # 25 MB screenshot cap
        TIMETRACK_SERVER_CONFIG=cfg,
        # Session-cookie hardening.
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.cookie_secure,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SECURE=cfg.cookie_secure,
        # Parsed once at startup; misconfigured entries raise immediately.
        TIMETRACK_ADMIN_IP_NETS=parse_ip_allowlist(cfg.admin_ip_allowlist),
    )

    apply_security_headers(app, csp=SERVER_CSP, hsts=cfg.hsts_enabled)

    db.init_app(app)
    login_manager.init_app(app)

    from .models import User

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    from .api import api_bp
    from .auth import auth_bp
    from .views import views_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)

    with app.app_context():
        db.create_all()

    return app


__all__ = ["create_app"]
