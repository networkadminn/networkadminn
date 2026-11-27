"""Upload dedupe, whole-word categories, update grace period, per-employee time zone."""

from __future__ import annotations

import io
import time
from datetime import datetime

import pytest

from timetrack.config import NEUTRAL, PRODUCTIVE, UNPRODUCTIVE, Config, _domain_matches
from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import (
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    Activity,
    PrivatePeriod,
    Screenshot,
    User,
)


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN, display_name="Boss")
        admin.set_password("adminpass")
        emp = User(username="alice", role=ROLE_EMPLOYEE, display_name="Alice")
        emp.set_password("alicepass")
        db.session.add_all([admin, emp])
        db.session.commit()
        app.config["_EMP_TOKEN"] = emp.api_token
        app.config["_EMP_ID"] = emp.id
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _auth(app):
    return {"Authorization": f"Bearer {app.config['_EMP_TOKEN']}"}


def test_retried_activity_upload_is_not_stored_twice(client, app):
    now = time.time()
    batch = {"activities": [
        {"app": "code", "title": "a.py", "start_ts": now - 60, "end_ts": now - 30},
        {"app": "code", "title": "b.py", "start_ts": now - 30, "end_ts": now},
    ]}
    first = client.post("/api/v1/activities", json=batch, headers=_auth(app))
    assert first.get_json() == {"accepted": 2, "duplicates": 0}
    again = client.post("/api/v1/activities", json=batch, headers=_auth(app))
    assert again.status_code == 201
    assert again.get_json() == {"accepted": 0, "duplicates": 2}
    with app.app_context():
        assert db.session.query(Activity).count() == 2


def test_duplicate_rows_inside_one_batch(client, app):
    now = time.time()
    row = {"app": "code", "title": "a.py", "start_ts": now - 60, "end_ts": now}
    resp = client.post("/api/v1/activities", json={"activities": [row, row]}, headers=_auth(app))
    assert resp.get_json() == {"accepted": 1, "duplicates": 1}


def test_retried_screenshot_upload_is_not_stored_twice(client, app):
    from PIL import Image

    ts = str(time.time())

    def upload():
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="JPEG")
        buf.seek(0)
        return client.post(
            "/api/v1/screenshots",
            data={"ts": ts, "app": "code", "title": "x", "image": (buf, "s.jpg")},
            headers=_auth(app),
        )

    first = upload()
    assert first.status_code == 201
    second = upload()
    assert second.status_code == 200
    assert second.get_json() == {"id": first.get_json()["id"], "duplicate": True}
    with app.app_context():
        assert db.session.query(Screenshot).count() == 1


def test_title_rules_match_whole_words_only():
    cfg = Config(rules={PRODUCTIVE: ["word"], UNPRODUCTIVE: ["x.com"]})
    assert cfg.categorize("chrome", "Reset password - Bank") == NEUTRAL
    assert cfg.categorize("chrome", "Report.docx - Word") == PRODUCTIVE
    assert cfg.categorize("chrome", "max.com pricing") == NEUTRAL
    assert cfg.categorize("chrome", "Home / x.com") == UNPRODUCTIVE


def test_domain_rules_do_not_match_substrings():
    assert _domain_matches("x.com", ["x.com"])
    assert _domain_matches("mobile.x.com", ["x.com"])
    assert not _domain_matches("max.com", ["x.com"])
    assert _domain_matches("docs.google.com", ["google"])
    assert not _domain_matches("googleusercontent.net", ["google"])


def test_update_stop_gets_grace_period_before_stale_alert(app, monkeypatch):
    from timetrack.server import tracker_alerts

    sent = []
    monkeypatch.setattr(
        tracker_alerts, "send_tracker_stop_alert", lambda u, reason="stopped": sent.append(reason)
    )
    with app.app_context():
        u = db.session.get(User, app.config["_EMP_ID"])
        now = time.time()
        u.last_agent_ping = now - 300
        u.agent_stopped_at = now - 290
        u.agent_stop_reason = "update"
        db.session.commit()
        tracker_alerts.check_stale_trackers(u.organization_id or 1)
        assert sent == []

        u.agent_stopped_at = now - tracker_alerts.UPDATE_GRACE_SECONDS - 10
        db.session.commit()
        tracker_alerts.check_stale_trackers(u.organization_id or 1)
        assert sent == ["stopped"]


def test_update_stop_reason_cleared_on_next_ping(client, app):
    client.post("/api/v1/agent/stop", json={"reason": "update"}, headers=_auth(app))
    with app.app_context():
        assert db.session.get(User, app.config["_EMP_ID"]).agent_stop_reason == "update"
    client.get("/api/v1/ping", headers=_auth(app))
    with app.app_context():
        assert db.session.get(User, app.config["_EMP_ID"]).agent_stop_reason is None


def test_employee_day_uses_their_time_zone(app):
    from timetrack.server.settings_util import user_tz
    from timetrack.server.views import _activities_for
    from timetrack.tzutil import zone

    with app.test_request_context():
        u = db.session.get(User, app.config["_EMP_ID"])
        u.timezone = "America/New_York"
        # 23:00 on Nov 10 in New York is already Nov 11 in India (company zone).
        ts = datetime(2026, 11, 10, 23, 0, tzinfo=zone("America/New_York")).timestamp()
        db.session.add(Activity(user_id=u.id, app="code", start_ts=ts, end_ts=ts + 600, duration=600))
        db.session.commit()
        nov10 = datetime(2026, 11, 10)
        assert _activities_for(u.id, nov10) == []
        with user_tz(u):
            assert len(_activities_for(u.id, nov10)) == 1


def test_invalid_time_zone_is_rejected(client, app):
    client.post("/login", data={"username": "alice", "password": "alicepass"})
    resp = client.post(
        "/me/account",
        data={"action": "profile", "name": "Alice", "email": "", "timezone": "Mars/Base"},
        follow_redirects=True,
    )
    assert "Unknown time zone".encode() in resp.data
    client.post(
        "/me/account",
        data={"action": "profile", "name": "Alice", "email": "", "timezone": "Europe/London"},
    )
    with app.app_context():
        assert db.session.get(User, app.config["_EMP_ID"]).timezone == "Europe/London"


def test_admin_dashboard_summary_layout(client, app):
    client.post("/login", data={"username": "boss", "password": "adminpass"})
    resp = client.get("/admin")
    assert resp.status_code == 200
    html = resp.data
    for needle in (
        b"dash-att-strip",
        b"dash-att-bars",
        b"dash-prod-panel",
        b"dash-kpi-hero",
        b"dash-kpi-meters",
        b"dash-meter-ring",
        b"dash-mid-grid",
        b"Productivity by hour",
        b"tone-private",
        b"server.css?v=270",
        b"dash-snapshot",
    ):
        assert needle in html, needle.decode("utf-8", errors="replace")
    assert b"dash-top-grid" not in html
    assert b"dash-insight" not in html
    assert b"dash-jump" not in html
    assert b"week-strip" not in html


def test_admin_dashboard_shows_private_time_on(client, app):
    with app.app_context():
        emp_id = app.config["_EMP_ID"]
        db.session.add(
            PrivatePeriod(user_id=emp_id, start_ts=time.time() - 120, active=True)
        )
        db.session.commit()
    client.post("/login", data={"username": "boss", "password": "adminpass"})
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert b"Private time" in resp.data
    assert b"Alice" in resp.data


def test_admin_user_settings_save(client, app):
    client.post("/login", data={"username": "boss", "password": "adminpass"})
    emp_id = app.config["_EMP_ID"]
    resp = client.post(
        f"/admin/user/{emp_id}/settings",
        data={
            "day": "2026-11-27",
            "private_time_allowed": "1",
            "view_own_screenshots": "1",
            "shot_mode": "off",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        u = db.session.get(User, emp_id)
        assert u.view_own_screenshots is True
        assert u.screenshots_enabled is False
