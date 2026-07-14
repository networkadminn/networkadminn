"""Application factory for the TimeTrack server."""

from __future__ import annotations

from flask import Flask

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
    )

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
