import time

import pytest

from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_EMPLOYEE, User
from timetrack.server.views import _is_online, _last_seen


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
        app.config["_EMP_ID"] = emp.id
    return app


def _online(app) -> bool:
    with app.app_context():
        u = db.session.get(User, app.config["_EMP_ID"])
        window = app.config["TIMETRACK_SERVER_CONFIG"].online_window
        return _is_online(u, _last_seen(u.id), time.time(), window)


def test_quit_marks_offline_immediately_and_ping_restores(app):
    client = app.test_client()
    auth = {"Authorization": f"Bearer {app.config['_EMP_TOKEN']}"}
    now = time.time()
    resp = client.post(
        "/api/v1/activities",
        json={"activities": [
            {"app": "code", "title": "main.py", "category": "productive",
             "idle": False, "start_ts": now - 60, "end_ts": now, "duration": 60},
        ]},
        headers=auth,
    )
    assert resp.status_code == 201
    assert _online(app)

    assert client.post("/api/v1/agent/stop", json={"reason": "quit"}, headers=auth).status_code == 200
    assert not _online(app)

    assert client.get("/api/v1/ping", headers=auth).status_code == 200
    assert _online(app)
