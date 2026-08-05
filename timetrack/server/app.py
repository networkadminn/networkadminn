"""Application factory for the TimeTrack server."""

from __future__ import annotations

from flask import Flask

from ..security import install_security
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
        # Harden the session cookie.
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.security.hsts_force,
        TIMETRACK_SERVER_CONFIG=cfg,
    )

    db.init_app(app)
    login_manager.init_app(app)
    install_security(app, cfg.security)

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
        _ensure_schema()

    return app


def _ensure_schema() -> None:
    """Best-effort, migration-free addition of new columns to existing DBs.

    The project uses ``db.create_all()`` rather than Alembic, so new columns on
    existing databases are added here via ``ALTER TABLE ... ADD COLUMN`` (a safe,
    idempotent operation on SQLite and Postgres).
    """
    from sqlalchemy import inspect, text

    from .models import User

    inspector = inspect(db.engine)
    try:
        existing = {col["name"] for col in inspector.get_columns(User.__tablename__)}
    except Exception:
        return

    additions = {
        "mfa_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "totp_secret": "VARCHAR(64) DEFAULT ''",
    }
    for column, ddl in additions.items():
        if column not in existing:
            try:
                db.session.execute(
                    text(f"ALTER TABLE {User.__tablename__} ADD COLUMN {column} {ddl}")
                )
                db.session.commit()
            except Exception:
                db.session.rollback()


__all__ = ["create_app"]
