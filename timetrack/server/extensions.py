"""Shared Flask extension singletons."""

from __future__ import annotations

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."

__all__ = ["db", "login_manager"]
