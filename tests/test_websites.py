import time

import pytest

from timetrack.analytics import website_report
from timetrack.monitor import clean_url, extract_domain, extract_url
from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_ADMIN, ROLE_EMPLOYEE, User


@pytest.mark.parametrize(
    "title,expected",
    [
        ("(5) YouTube - Google Chrome", "youtube.com"),
        ("Email ID Data - ESS - Google Sheets - Google Chrome", "docs.google.com"),
        ("ChatGPT - Raghvir - Google Chrome", "chatgpt.com"),
        ("Canva AI - Canva - Brave", "canva.com"),
        ("Vivah songs - YouTube - Mozilla Firefox", "youtube.com"),
        ("app.ridewheelshare.com / rws-db - Google Chrome", "app.ridewheelshare.com"),
        ("Darshini - Rugs and more - Google Chrome", ""),
    ],
)
def test_extract_url_from_browser_titles(title, expected):
    assert extract_domain(extract_url("chrome.exe", title)) == expected


def test_known_sites_only_for_browsers():
    assert extract_url("Figma.exe", "Figma") == ""


def test_extract_domain_keeps_leading_w():
    assert extract_domain("web.whatsapp.com") == "web.whatsapp.com"
    assert extract_domain("https://www.youtube.com/watch?v=1") == "youtube.com"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("youtube.com/watch?v=abc&t=3", "https://youtube.com/watch"),
        ("https://mail.google.com/mail/u/0/#inbox", "https://mail.google.com/mail/u/0/"),
        ("hello world", ""),
        ("how to add email in spam", ""),
        ("javascript:alert(1)", ""),
        ("github.com/ess-dev/astrology/actions", "https://github.com/ess-dev/astrology/actions"),
    ],
)
def test_clean_url(raw, expected):
    assert clean_url(raw) == expected


def _act(app, title, url, start, dur, category="productive", idle=False):
    from types import SimpleNamespace

    return SimpleNamespace(
        app=app, title=title, url=url, category=category, idle=idle,
        start_ts=start, end_ts=start + dur, duration=dur,
    )


def test_website_report_groups_pages_and_visits():
    t = 1_700_000_000.0
    acts = [
        _act("chrome.exe", "Repo - GitHub - Google Chrome", "https://github.com/a/b", t, 60),
        _act("chrome.exe", "Issues - GitHub - Google Chrome", "https://github.com/a/b/issues", t + 60, 30),
        _act("Code.exe", "main.py", "", t + 90, 120),
        _act("chrome.exe", "(2) YouTube - Google Chrome", "", t + 210, 40, "unproductive"),
        _act("chrome.exe", "Repo - GitHub - Google Chrome", "https://github.com/a/b", t + 250, 20),
        _act("chrome.exe", "", "", t + 270, 100, idle=True),
    ]
    r = website_report(acts)
    by = {s["domain"]: s for s in r["sites"]}
    assert set(by) == {"github.com", "youtube.com"}
    assert by["github.com"]["seconds"] == 110
    assert by["github.com"]["visits"] == 2
    assert by["github.com"]["pages"][0]["url"] == "https://github.com/a/b"
    assert by["youtube.com"]["category"] == "unproductive"
    assert by["youtube.com"]["pages"][0]["url"] == ""
    assert r["visit_count"] == 3
    assert [v["domain"] for v in r["visits"]] == ["github.com", "youtube.com", "github.com"]


def test_website_report_does_not_link_unsafe_urls():
    acts = [_act("chrome.exe", "x - Google Chrome", "javascript://x.com/%0aalert(1)", 1.0, 10)]
    for s in website_report(acts)["sites"]:
        assert all(p["url"] == "" for p in s["pages"])


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
        admin.set_password("adminpass")
        emp = User(username="alice", role=ROLE_EMPLOYEE, display_name="Alice")
        emp.set_password("alicepass")
        other = User(username="mallory", role=ROLE_EMPLOYEE, organization_id=2)
        other.set_password("x")
        db.session.add_all([admin, emp, other])
        db.session.commit()
        app.config["_EMP"] = (emp.id, emp.api_token)
        app.config["_OTHER_ID"] = other.id
    return app


def _login(client, username, password):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=True)


def test_admin_sees_employee_urls(app):
    client = app.test_client()
    emp_id, token = app.config["_EMP"]
    now = time.time()
    resp = client.post(
        "/api/v1/activities",
        json={"activities": [
            {"app": "chrome.exe", "title": "Actions · ess-dev/astrology - Google Chrome",
             "url": "https://github.com/ess-dev/astrology/actions", "category": "productive",
             "idle": False, "start_ts": now - 120, "end_ts": now - 60, "duration": 60},
            {"app": "chrome.exe", "title": "(5) YouTube - Google Chrome", "url": "",
             "category": "unproductive", "idle": False,
             "start_ts": now - 60, "end_ts": now, "duration": 60},
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201

    _login(client, "boss", "adminpass")
    page = client.get(f"/admin/user/{emp_id}/websites")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "github.com" in html and "youtube.com" in html
    assert 'href="https://github.com/ess-dev/astrology/actions"' in html

    assert client.get(f"/admin/user/{emp_id}/websites?range=30").status_code == 200
    filtered = client.get(f"/admin/user/{emp_id}/websites?site=youtube.com").get_data(as_text=True)
    assert "Visit log · youtube.com" in filtered

    profile = client.get(f"/admin/user/{emp_id}").get_data(as_text=True)
    assert f"/admin/user/{emp_id}/websites" in profile


def test_websites_page_is_admin_and_org_scoped(app):
    client = app.test_client()
    emp_id, _ = app.config["_EMP"]
    _login(client, "alice", "alicepass")
    assert client.get(f"/admin/user/{emp_id}/websites").status_code in (302, 403)

    client.get("/logout")
    _login(client, "boss", "adminpass")
    assert client.get(f"/admin/user/{app.config['_OTHER_ID']}/websites").status_code == 404
    assert client.get(f"/admin/user/{app.config['_OTHER_ID']}").status_code == 404
