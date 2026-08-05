import io
import threading
import time

import pytest
from werkzeug.serving import make_server

from timetrack.agent.agent import Agent
from timetrack.agent.buffer import AgentBuffer
from timetrack.agent.config import AgentConfig
from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import Activity, Screenshot, User


def test_buffer_merges_contiguous():
    with AgentBuffer(":memory:") as buf:
        buf.add_activity("code", "a.py", "productive", False, 5.0, now=105.0)
        buf.add_activity("code", "a.py", "productive", False, 5.0, now=110.0)
        pending = buf.unsynced_activities()
        assert len(pending) == 1
        assert pending[0].duration == 10.0


def test_buffer_marks_synced():
    with AgentBuffer(":memory:") as buf:
        buf.add_activity("code", "a.py", "productive", False, 5.0, now=105.0)
        buf.add_activity("firefox", "yt", "unproductive", False, 5.0, now=110.0)
        pending = buf.unsynced_activities()
        assert len(pending) == 2
        buf.mark_activities_synced([pending[0].id])
        assert len(buf.unsynced_activities()) == 1


def test_buffer_does_not_merge_into_synced():
    with AgentBuffer(":memory:") as buf:
        buf.add_activity("code", "a.py", "productive", False, 5.0, now=105.0)
        p = buf.unsynced_activities()
        buf.mark_activities_synced([p[0].id])
        buf.add_activity("code", "a.py", "productive", False, 5.0, now=110.0)
        # Should create a new row, not merge into the synced one.
        assert len(buf.unsynced_activities()) == 1


class _ServerThread:
    def __init__(self, app):
        self.srv = make_server("127.0.0.1", 0, app)
        self.port = self.srv.server_port
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        time.sleep(0.2)
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.thread.join(timeout=3)


@pytest.fixture()
def live_server(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "srv"),
        database_uri="sqlite:///" + str(tmp_path / "srv.db"),
        secret_key="k",
    )
    with app.app_context():
        u = User(username="alice", role="employee")
        u.set_password("Agent-Passphrase-123")
        db.session.add(u)
        db.session.commit()
        token = u.api_token
        uid = u.id
    with _ServerThread(app) as srv:
        yield app, srv.port, token, uid


def test_agent_flush_end_to_end(tmp_path, live_server):
    app, port, token, uid = live_server

    # Create a screenshot file on disk to be uploaded.
    from PIL import Image

    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    shot_path = shots_dir / "s.jpg"
    Image.new("RGB", (16, 16), (10, 120, 200)).save(shot_path, format="JPEG")

    cfg = AgentConfig(
        server_url=f"http://127.0.0.1:{port}",
        api_token=token,
        buffer_path=str(tmp_path / "buf.db"),
        shots_dir=str(shots_dir),
    )
    agent = Agent(cfg)
    now = time.time()
    agent.buffer.add_activity("code", "m.py", "productive", False, 60.0, now=now)
    agent.buffer.add_screenshot(now, str(shot_path), "code", "m.py", 16, 16)

    acts, shots = agent.flush()
    agent.buffer.close()

    assert acts == 1
    assert shots == 1
    with app.app_context():
        assert db.session.query(Activity).filter_by(user_id=uid).count() == 1
        assert db.session.query(Screenshot).filter_by(user_id=uid).count() == 1


def test_agent_ping(live_server):
    app, port, token, uid = live_server
    from timetrack.agent.client import ServerClient

    client = ServerClient(f"http://127.0.0.1:{port}", token)
    result = client.ping()
    assert result is not None
    assert result["user"] == "alice"


def test_client_encode_multipart_roundtrip():
    from timetrack.agent.client import _encode_multipart

    body = _encode_multipart("BOUND", {"ts": "1.0", "app": "code"}, b"\xff\xd8xx")
    assert b'name="ts"' in body
    assert b'name="image"; filename="shot.jpg"' in body
    assert b"\xff\xd8xx" in body
    assert body.endswith(b"--BOUND--\r\n")
