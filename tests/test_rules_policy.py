"""Company category rules override defaults; tray Quit/Sign out policy."""

from __future__ import annotations

import time
import types

import pytest

from timetrack.config import Config, merge_rules
from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_ADMIN, ROLE_EMPLOYEE, Activity, User


def _cat(rules, app, title="", url=""):
    return Config(rules=merge_rules(rules, org_only=True)).categorize(app, title, url=url)


def test_exact_app_name_applies_org_wide():
    rules = {"productive": ["Slack"], "unproductive": [], "neutral": [],
             "sites_productive": [], "sites_unproductive": []}
    assert _cat(rules, "Slack", "general") == "productive"
    assert _cat(rules, "slack", "dm") == "productive"
    assert _cat(rules, "SlackHelper", "x") == "neutral"


def test_moving_default_item_sticks():
    base = merge_rules({})
    unprod = [x for x in base["unproductive"] if "youtube" not in x.lower()]
    sites_unprod = [x for x in base["sites_unproductive"] if "youtube" not in x.lower()]
    rules = {
        "productive": base["productive"],
        "unproductive": unprod,
        "neutral": [],
        "sites_productive": base["sites_productive"] + ["youtube.com"],
        "sites_unproductive": sites_unprod,
    }
    assert _cat(rules, "chrome", "Video - YouTube", "https://www.youtube.com/watch") == "productive"


def test_removed_item_becomes_neutral_and_neutral_wins():
    rules = {"productive": ["code"], "unproductive": [], "neutral": ["vscodium"],
             "sites_productive": [], "sites_unproductive": []}
    assert _cat(rules, "slack", "general") == "neutral"
    assert _cat(rules, "vscodium", "main.py - code") == "neutral"
    assert _cat(rules, "code", "main.py") == "productive"


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    app.config.update(TESTING=True)
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN, display_name="Boss")
        admin.set_password("bosspass")
        emp = User(username="alice", role=ROLE_EMPLOYEE, display_name="Alice")
        emp.set_password("alicepass")
        db.session.add_all([admin, emp])
        db.session.commit()
        now = time.time()
        db.session.add(Activity(user_id=emp.id, app="slack", title="general", category="productive",
                                start_ts=now - 60, end_ts=now, duration=60))
        db.session.commit()
        app.config["_EMP_ID"] = emp.id
    return app


def test_saving_rules_recategorizes_past_activity(app):
    client = app.test_client()
    client.post("/login", data={"username": "boss", "password": "bosspass"})
    resp = client.post("/settings/rules", data={
        "app_name": "slack",
        "app_category": "unproductive",
    })
    assert resp.status_code in (200, 302)
    with app.app_context():
        act = db.session.execute(db.select(Activity)).scalar_one()
        assert act.category == "unproductive"


def _agent(tmp_path, role):
    from timetrack.agent.agent import Agent

    a = Agent.__new__(Agent)
    a.config = types.SimpleNamespace(buffer_path=str(tmp_path / "b.db"))
    a.role = role
    a._tray_logout_allowed, a._tray_quit_allowed = a._load_tray_policy()
    return a


def test_tray_policy_persists_and_spares_admins(tmp_path):
    a = _agent(tmp_path, "employee")
    assert a.tray_quit_allowed and a.tray_logout_allowed
    a._tray_logout_allowed, a._tray_quit_allowed = False, False
    a._save_tray_policy()

    b = _agent(tmp_path, "employee")
    assert not b.tray_quit_allowed and not b.tray_logout_allowed
    assert _agent(tmp_path, "admin").tray_quit_allowed
