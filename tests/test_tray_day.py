"""Tray day JSON API for desktop flyout."""

import pytest

from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_EMPLOYEE, User


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    app.config.update(TESTING=True)
    with app.app_context():
        emp = User(username="alice", role=ROLE_EMPLOYEE, display_name="Alice")
        emp.set_password("alicepass")
        db.session.add(emp)
        db.session.commit()
        app.config["_EMP_TOKEN"] = emp.api_token
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_tray_day_requires_token(client):
    assert client.get("/api/v1/tray-day").status_code == 401


def test_tray_day_ok(client, app):
    token = app.config["_EMP_TOKEN"]
    resp = client.get("/api/v1/tray-day", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "dt_productivity" in data
    assert "mix_pct" in data
    assert "top_apps" in data
