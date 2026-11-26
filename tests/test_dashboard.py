import time

from timetrack.config import Config
from timetrack.dashboard import create_app
from timetrack.storage import Storage


def _app(tmp_path):
    db = str(tmp_path / "t.db")
    now = time.time()
    with Storage(db) as s:
        # Back-to-back spans (120 s then 60 s); both ending "now" would overlap.
        s.record("code", "main.py", "productive", False, 120.0, now=now - 60)
        s.record("firefox", "youtube", "unproductive", False, 60.0, now=now)
    cfg = Config(db_path=db)
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app


def test_healthz(tmp_path):
    client = _app(tmp_path).test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_index_renders(tmp_path):
    client = _app(tmp_path).test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"TimeTrack" in resp.data


def test_api_summary(tmp_path):
    client = _app(tmp_path).test_client()
    data = client.get("/api/summary").get_json()
    assert "productivity_pct" in data
    assert data["active_seconds"] >= 180.0


def test_api_timeline(tmp_path):
    client = _app(tmp_path).test_client()
    data = client.get("/api/timeline").get_json()
    assert "buckets" in data
    assert isinstance(data["buckets"], list)
