"""TimeTrack multi-user server (admin + employee dashboards, agent ingest API)."""

from .app import create_app

__all__ = ["create_app"]
