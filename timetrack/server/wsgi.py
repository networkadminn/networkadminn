"""WSGI entry for gunicorn / Docker.

    gunicorn -b 0.0.0.0:8080 timetrack.server.wsgi:app
"""

from __future__ import annotations

from .app import create_app

app = create_app()
