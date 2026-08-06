import io
import time

import pytest

from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_ADMIN, ROLE_EMPLOYEE, User


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN, display_name="The Boss")
        admin.set_password("adminpass")
        emp = User(username="alice", role=ROLE_EMPLOYEE, display_name="Alice")
        emp.set_password("alicepass")
        db.session.add_all([admin, emp])
        db.session.commit()
        app.config["_ADMIN_TOKEN"] = admin.api_token
        app.config["_EMP_TOKEN"] = emp.api_token
        app.config["_EMP_ID"] = emp.id
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username, password):
    return client.post(
        "/login", data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_login_required_redirect(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert b"Login to Dashboard" in resp.data or b"Login now" in resp.data
    assert b"Product tour" in resp.data or b"product slides" in resp.data


def test_admin_login_sees_team(client):
    resp = _login(client, "boss", "adminpass")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data
    assert b"Employees" in resp.data or b"Live now" in resp.data
    assert b"Total desk time" in resp.data
    assert b"Late arrivals" in resp.data


def test_admin_me_redirects_to_dashboard(client):
    _login(client, "boss", "adminpass")
    resp = client.get("/me", follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin" in resp.headers["Location"]


def test_settings_rules_page(client):
    _login(client, "boss", "adminpass")
    resp = client.get("/settings/rules")
    assert resp.status_code == 200
    assert b"App &amp; website categories" in resp.data or b"Applications" in resp.data


def test_employee_me_page(client):
    _login(client, "alice", "alicepass")
    resp = client.get("/me")
    assert resp.status_code == 200
    assert b"Productivity timeline" in resp.data or b"Productivity bar" in resp.data
    assert b"Arrival" in resp.data
    assert b"Desktime" in resp.data


def test_admin_delete_employee(client, app):
    _login(client, "boss", "adminpass")
    emp_id = app.config["_EMP_ID"]
    resp = client.post(
        "/employees",
        data={"action": "delete", "user_id": emp_id},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Deleted user alice" in resp.data
    with app.app_context():
        assert db.session.get(User, emp_id) is None


def test_employee_absence_calendar(client):
    _login(client, "alice", "alicepass")
    resp = client.get("/me/absence")
    assert resp.status_code == 200
    assert b"Absence calendar" in resp.data


def test_employee_cannot_access_admin(client):
    _login(client, "alice", "alicepass")
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302
    assert "/me" in resp.headers["Location"]


def test_employee_offline_submit_pending(client, app):
    from timetrack.server.models import OfflineRequest

    _login(client, "alice", "alicepass")
    resp = client.get("/offline")
    assert resp.status_code == 200
    assert b"Fill offline" in resp.data
    day = "2026-07-15"
    resp = client.post(
        "/offline",
        data={
            "day": day,
            "start": f"{day}T10:00",
            "end": f"{day}T10:30",
            "category": "productive",
            "note": "Client call",
            "fill_kind": "offline",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Awaiting admin approval" in resp.data or b"Pending" in resp.data
    with app.app_context():
        req = db.session.execute(db.select(OfflineRequest)).scalar_one()
        assert req.status == "pending"
        assert req.duration == 1800
        assert req.note == "Client call"


def test_admin_approves_offline_request(client, app):
    from timetrack.server.models import Activity, OfflineRequest

    _login(client, "alice", "alicepass")
    day = "2026-07-15"
    client.post(
        "/offline",
        data={
            "day": day,
            "start": f"{day}T11:00",
            "end": f"{day}T11:20",
            "category": "neutral",
            "note": "Power cut",
            "fill_kind": "idle",
        },
    )
    with app.app_context():
        rid = db.session.execute(db.select(OfflineRequest.id)).scalar_one()

    client.get("/logout", follow_redirects=True)
    _login(client, "boss", "adminpass")
    resp = client.post(
        "/approvals",
        data={"id": rid, "action": "approve"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"approved" in resp.data.lower()
    with app.app_context():
        req = db.session.get(OfflineRequest, rid)
        assert req.status == "approved"
        acts = list(
            db.session.execute(
                db.select(Activity).filter_by(app="offline", user_id=req.user_id)
            ).scalars()
        )
        assert len(acts) == 1
        assert acts[0].duration == 1200


def test_bad_password(client):
    resp = _login(client, "boss", "wrong")
    assert b"Invalid username or password" in resp.data


def test_agent_login_returns_token(client, app):
    resp = client.post(
        "/api/v1/agent/login",
        json={"username": "alice", "password": "alicepass"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["api_token"] == app.config["_EMP_TOKEN"]
    assert data["user"] == "alice"


def test_agent_login_rejects_bad_password(client):
    resp = client.post(
        "/api/v1/agent/login",
        json={"username": "alice", "password": "nope"},
    )
    assert resp.status_code == 401


def test_api_requires_token(client):
    resp = client.post("/api/v1/activities", json={"activities": []})
    assert resp.status_code == 401


def test_api_ingest_activities(client, app):
    token = app.config["_EMP_TOKEN"]
    now = time.time()
    resp = client.post(
        "/api/v1/activities",
        json={"activities": [
            {"app": "code", "title": "main.py", "category": "productive",
             "idle": False, "start_ts": now - 60, "end_ts": now, "duration": 60},
            {"app": "firefox", "title": "youtube", "category": "unproductive",
             "idle": False, "start_ts": now - 30, "end_ts": now, "duration": 30},
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["accepted"] == 2


def test_api_ingest_screenshot_and_serve(client, app):
    token = app.config["_EMP_TOKEN"]
    # 1x1 red JPEG via Pillow.
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="JPEG")
    buf.seek(0)

    resp = client.post(
        "/api/v1/screenshots",
        data={"ts": str(time.time()), "app": "code", "title": "x",
              "image": (buf, "shot.jpg")},
        headers={"Authorization": f"Bearer {token}"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    shot_id = resp.get_json()["id"]

    # Admin can view it.
    _login(client, "boss", "adminpass")
    img = client.get(f"/screenshot/{shot_id}")
    assert img.status_code == 200
    assert img.data[:2] == b"\xff\xd8"  # JPEG magic


def test_employee_dashboard_shows_own_data(client, app):
    token = app.config["_EMP_TOKEN"]
    now = time.time()
    client.post(
        "/api/v1/activities",
        json={"activities": [
            {"app": "code", "title": "m.py", "category": "productive",
             "idle": False, "start_ts": now - 120, "end_ts": now, "duration": 120},
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    _login(client, "alice", "alicepass")
    resp = client.get("/me")
    assert resp.status_code == 200
    assert b"Productivity" in resp.data


def test_employee_cannot_view_others_screenshot(client, app):
    # Employee alice cannot access a screenshot owned by someone else.
    admin_token = app.config["_ADMIN_TOKEN"]
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="JPEG")
    buf.seek(0)
    resp = client.post(
        "/api/v1/screenshots",
        data={"ts": str(time.time()), "image": (buf, "s.jpg")},
        headers={"Authorization": f"Bearer {admin_token}"},
        content_type="multipart/form-data",
    )
    shot_id = resp.get_json()["id"]

    _login(client, "alice", "alicepass")
    assert client.get(f"/screenshot/{shot_id}").status_code == 403
