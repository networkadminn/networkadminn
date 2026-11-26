"""30-day agent sign-in sessions, one per device."""

from __future__ import annotations

import time
import urllib.error

import pytest

from timetrack.agent.client import ServerClient
from timetrack.server import create_app
from timetrack.server.api import SESSION_SECONDS
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_EMPLOYEE, AgentSession, User


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
        app.config["_EMP_ID"] = emp.id
    return app


def _login(client, device_id: str = "laptop") -> str:
    resp = client.post(
        "/api/v1/agent/login",
        json={"username": "alice", "password": "alicepass", "device_id": device_id},
    )
    assert resp.status_code == 200
    return resp.get_json()["api_token"]


def _ping(client, token):
    return client.get("/api/v1/ping", headers={"Authorization": f"Bearer {token}"})


def _age_session(app, token, seconds):
    with app.app_context():
        s = db.session.execute(db.select(AgentSession).filter_by(token=token)).scalar_one()
        s.issued_at = time.time() - seconds
        db.session.commit()


def test_session_lasts_30_days_then_requires_login(app):
    client = app.test_client()
    token = _login(client)
    _age_session(app, token, SESSION_SECONDS - 3600)
    assert _ping(client, token).status_code == 200

    _age_session(app, token, SESSION_SECONDS + 60)
    resp = _ping(client, token)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "session expired"

    new_token = _login(client)
    assert new_token != token
    assert _ping(client, new_token).status_code == 200
    assert _ping(client, token).status_code == 401


def test_same_device_login_reuses_session(app):
    client = app.test_client()
    first = _login(client, "laptop")
    assert _login(client, "laptop") == first
    assert _ping(client, first).status_code == 200


def test_second_device_has_its_own_session(app):
    client = app.test_client()
    laptop = _login(client, "laptop")
    desktop = _login(client, "desktop")
    assert laptop != desktop

    # The laptop's window ending must not touch the desktop's.
    _age_session(app, laptop, SESSION_SECONDS + 60)
    assert _ping(client, laptop).status_code == 401
    assert _ping(client, desktop).status_code == 200


def test_logout_ends_only_that_device(app):
    client = app.test_client()
    laptop = _login(client, "laptop")
    desktop = _login(client, "desktop")
    resp = client.post(
        "/api/v1/agent/stop",
        json={"reason": "logout"},
        headers={"Authorization": f"Bearer {laptop}"},
    )
    assert resp.status_code == 200
    assert _ping(client, laptop).status_code == 401
    assert _ping(client, desktop).status_code == 200


def test_rotate_token_signs_out_every_device(app):
    client = app.test_client()
    laptop = _login(client, "laptop")
    with app.app_context():
        db.session.get(User, app.config["_EMP_ID"]).rotate_token()
        db.session.commit()
    assert _ping(client, laptop).status_code == 401


def test_legacy_token_starts_session_on_first_use(app):
    client = app.test_client()
    with app.app_context():
        u = db.session.get(User, app.config["_EMP_ID"])
        u.token_issued_at = None
        token = u.api_token
        db.session.commit()
    assert _ping(client, token).status_code == 200
    with app.app_context():
        assert db.session.get(User, app.config["_EMP_ID"]).token_issued_at is not None


def test_client_tells_offline_from_unauthorized(monkeypatch):
    c = ServerClient("https://example.invalid", "tok")

    def offline(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("urllib.request.urlopen", offline)
    assert c.ping() is None and not c.unauthorized

    def refused(req, *a, **k):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", refused)
    assert c.ping() is None and c.unauthorized
