"""Cross-platform agent fixes: idle math, buffer, uploads, capture checks, CLI plumbing."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from timetrack.agent.agent import Agent
from timetrack.agent.buffer import AgentBuffer
from timetrack.agent.config import AgentConfig


# --- Windows idle tick math -------------------------------------------------


def test_idle_millis_after_signed_overflow():
    from timetrack.platform.windows import idle_millis

    # Uptime past 2**31 ms (~24.9 days): both ticks are large unsigned values.
    assert idle_millis(2_200_000_000, 2_199_990_000) == 10_000


def test_idle_millis_across_49_day_wrap():
    from timetrack.platform.windows import idle_millis

    # Tick counter wrapped to a small value after the last input was recorded.
    assert idle_millis(5_000, 0xFFFFFFFF - 4_999) == 10_000


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API")
def test_windows_idle_seconds_is_sane():
    from timetrack.platform.windows import get_idle_seconds

    idle = get_idle_seconds()
    assert 0.0 <= idle < 50 * 24 * 3600


# --- Buffer: idle back-fill, sealing, disk cap ------------------------------


def test_mark_idle_since_relabels_and_splits():
    with AgentBuffer(":memory:") as buf:
        buf.add_activity("code", "a.py", "productive", False, 100.0, now=1100.0)  # 1000..1100
        buf.add_activity("chrome", "docs", "productive", False, 100.0, now=1200.0)  # 1100..1200
        changed = buf.mark_idle_since(1050.0)
        assert changed == 2
        rows = sorted(buf.unsynced_activities(), key=lambda a: a.start_ts)
        active = [r for r in rows if not r.idle]
        idle = [r for r in rows if r.idle]
        assert sum(r.duration for r in active) == pytest.approx(50.0)
        assert sum(r.duration for r in idle) == pytest.approx(150.0)
        assert all(r.category == "neutral" for r in idle)


def test_mark_idle_since_leaves_synced_rows_alone():
    with AgentBuffer(":memory:") as buf:
        buf.add_activity("code", "a.py", "productive", False, 100.0, now=1100.0)
        buf.mark_activities_synced([buf.unsynced_activities()[0].id])
        assert buf.mark_idle_since(1000.0) == 0


def test_seal_before_lets_long_span_sync_and_keeps_merging():
    with AgentBuffer(":memory:") as buf:
        buf.add_activity("code", "a.py", "productive", False, 600.0, now=1600.0)  # 1000..1600
        buf.seal_before(1300.0)
        ready = buf.unsynced_activities(ended_before=1300.0)
        assert len(ready) == 1 and ready[0].duration == pytest.approx(300.0)
        # The next sample merges into the newer (still held back) part.
        buf.add_activity("code", "a.py", "productive", False, 5.0, now=1605.0)
        assert len(buf.unsynced_activities()) == 2
        total = sum(a.duration for a in buf.unsynced_activities())
        assert total == pytest.approx(605.0)


def test_trim_screenshots_keeps_newest():
    with AgentBuffer(":memory:") as buf:
        for i in range(5):
            buf.add_screenshot(float(i), f"/tmp/{i}.jpg", "a", "t", 1, 1)
        dropped = buf.trim_screenshots(2)
        assert sorted(dropped) == ["/tmp/0.jpg", "/tmp/1.jpg", "/tmp/2.jpg"]
        assert [s.path for s in buf.unsynced_screenshots()] == ["/tmp/3.jpg", "/tmp/4.jpg"]


def test_screenshot_blur_flag_is_stored_per_shot():
    with AgentBuffer(":memory:") as buf:
        buf.add_screenshot(1.0, "x.jpg", "a", "t", 1, 1, blurred=True)
        assert buf.unsynced_screenshots()[0].blurred is True


# --- Flush: rejection handling, holdback, single flight ---------------------


class _FakeClient:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.batches: list[int] = []
        self.last_post_status = None
        self.token = "t"

    @property
    def last_post_rejected(self):
        c = self.last_post_status
        return c is not None and 400 <= c < 500 and c not in (401, 403, 408, 409, 429)

    def post_activities(self, payload):
        self.batches.append(len(payload))
        self.last_post_status = self.statuses.pop(0) if self.statuses else 201
        return 200 <= (self.last_post_status or 0) < 300

    def post_screenshot(self, data, meta):
        self.last_post_status = self.statuses.pop(0) if self.statuses else 201
        return 200 <= (self.last_post_status or 0) < 300


def _agent(tmp_path, **kw) -> Agent:
    cfg = AgentConfig(
        server_url="http://127.0.0.1:9",
        api_token="t",
        buffer_path=str(tmp_path / "buf.db"),
        shots_dir=str(tmp_path / "shots"),
        **kw,
    )
    return Agent(cfg)


def test_flush_keeps_rows_when_offline(tmp_path):
    agent = _agent(tmp_path)
    agent.client = _FakeClient([None])
    agent.buffer.add_activity("code", "a", "productive", False, 5.0, now=100.0)
    assert agent.flush() == (0, 0)
    assert agent.buffer.pending_counts()[0] == 1
    agent.buffer.close()


def test_flush_413_halves_batches_then_succeeds(tmp_path):
    agent = _agent(tmp_path, batch_size=4)
    agent.client = _FakeClient([413, 201, 201])
    for i in range(4):
        agent.buffer.add_activity(f"app{i}", "t", "neutral", False, 5.0, now=100.0 + i * 10)
    sent, _ = agent.flush()
    assert sent == 4
    assert agent.client.batches == [4, 2, 2]
    assert agent.buffer.pending_counts()[0] == 0
    agent.buffer.close()


def test_flush_drops_single_rejected_row_instead_of_blocking(tmp_path):
    agent = _agent(tmp_path, batch_size=1)
    agent.client = _FakeClient([400, 201])
    agent.buffer.add_activity("bad", "t", "neutral", False, 5.0, now=100.0)
    agent.buffer.add_activity("good", "t", "neutral", False, 5.0, now=200.0)
    sent, _ = agent.flush()
    assert sent == 1
    assert agent.buffer.pending_counts()[0] == 0
    agent.buffer.close()


def test_flush_private_time_409_keeps_rows(tmp_path):
    agent = _agent(tmp_path)
    agent.client = _FakeClient([409])
    agent.buffer.add_activity("code", "a", "productive", False, 5.0, now=100.0)
    agent.flush()
    assert agent.buffer.pending_counts()[0] == 1
    agent.buffer.close()


def test_flush_holdback_keeps_recent_activity_local(tmp_path):
    agent = _agent(tmp_path)
    agent.client = _FakeClient([])
    now = time.time()
    agent.buffer.add_activity("old", "t", "neutral", False, 5.0, now=now - 1000)
    agent.buffer.add_activity("recent", "t", "neutral", False, 5.0, now=now)
    sent, _ = agent.flush(holdback=300.0)
    assert sent == 1
    assert agent.buffer.pending_counts()[0] == 1
    agent.buffer.close()


def test_concurrent_flushes_do_not_double_send(tmp_path):
    agent = _agent(tmp_path)
    sent_rows: list[int] = []

    class SlowClient(_FakeClient):
        def post_activities(self, payload):
            time.sleep(0.2)
            sent_rows.extend([1] * len(payload))
            return super().post_activities(payload)

    agent.client = SlowClient([])
    for i in range(3):
        agent.buffer.add_activity(f"a{i}", "t", "neutral", False, 5.0, now=100.0 + i * 10)
    threads = [threading.Thread(target=agent.flush) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sent_rows) == 3
    agent.buffer.close()


def test_rejected_screenshot_is_dropped(tmp_path):
    agent = _agent(tmp_path)
    agent.client = _FakeClient([413])
    shot = tmp_path / "s.jpg"
    shot.write_bytes(b"\xff\xd8")
    agent.buffer.add_screenshot(1.0, str(shot), "a", "t", 1, 1)
    agent.flush()
    assert agent.buffer.pending_counts()[1] == 0
    assert not shot.exists()
    agent.buffer.close()


def test_idle_edge_backfills_threshold_minutes(tmp_path, monkeypatch):
    from timetrack import platform as pf
    from timetrack.agent import idle_ui

    agent = _agent(tmp_path, idle_threshold=180.0)
    monkeypatch.setattr(idle_ui, "show_going_offline_countdown", lambda *a, **k: None)
    monkeypatch.setattr(pf, "get_active_window", lambda: None)
    idle = {"s": 0.0}
    monkeypatch.setattr(pf, "get_idle_seconds", lambda: idle["s"])
    t0 = 10_000.0
    for i in range(1, 41):  # 200 s of samples, 5 s apart
        idle["s"] = max(0.0, i * 5.0 - 10.0)  # last input 10 s after start
        agent.sample_activity(5.0, now=t0 + i * 5.0)
    rows = agent.buffer.unsynced_activities()
    active = sum(r.duration for r in rows if not r.idle)
    assert active == pytest.approx(10.0, abs=5.0)
    agent.buffer.close()


# --- Screenshot checks ------------------------------------------------------


def test_dark_top_panel_is_not_treated_as_black_frame():
    from PIL import Image

    from timetrack.platform.linux_screenshot import _image_mostly_black

    img = Image.new("RGB", (1920, 1080), (220, 220, 220))
    img.paste((0, 0, 0), (0, 0, 1920, 32))  # Ubuntu's black top bar
    assert not _image_mostly_black(img)


def test_effective_max_width_allows_two_screens():
    from timetrack.platform.screenshot import effective_max_width

    assert effective_max_width(1920, 1080, 1920) == 1920
    assert effective_max_width(3840, 1080, 1920) == 3840
    assert effective_max_width(5760, 1080, 1920) == 3840


# --- CLI / child-process plumbing --------------------------------------------


def test_agent_command_puts_config_before_subcommand():
    from timetrack.agent.ui_process import agent_command

    cmd = agent_command("login", config_path="/x/agent.toml")
    assert cmd[-3:] == ["-c", "/x/agent.toml", "login"]


def test_main_does_not_treat_config_flag_as_run(monkeypatch):
    from timetrack.agent import __main__ as cli

    seen = {}
    monkeypatch.setattr(cli, "_cmd_login", lambda args: seen.setdefault("cfg", args.config) and 0)
    with pytest.raises(SystemExit):
        cli.main(["-c", "/x/agent.toml", "login"])
    assert seen["cfg"] == "/x/agent.toml"


def test_normalize_server_url():
    pytest.importorskip("tkinter")
    from timetrack.agent.login_ui import normalize_server_url

    assert normalize_server_url("tracker.example.com/") == "https://tracker.example.com"
    assert normalize_server_url("http://10.0.0.5:8080") == "http://10.0.0.5:8080"
    assert normalize_server_url("ftp://x") == ""
    assert normalize_server_url("  ") == ""


def test_save_falls_back_to_profile_when_config_not_writable(tmp_path, monkeypatch):
    from timetrack.agent import config as agent_config

    profile = tmp_path / "profile" / "agent.toml"
    monkeypatch.setattr(agent_config, "USER_CONFIG_PATH", profile)
    system_file = tmp_path / "etc" / "agent.toml"
    cfg = AgentConfig(api_token="", config_path=str(system_file))

    real_write = type(system_file).write_text

    def deny(self, *a, **k):
        if self == system_file:
            raise PermissionError("read-only")
        return real_write(self, *a, **k)

    monkeypatch.setattr(type(system_file), "write_text", deny)
    dest = agent_config.save_agent_config(cfg)
    assert dest == profile
    text = profile.read_text(encoding="utf-8")
    assert "batch_size" in text and "screenshot_max_width" in text


def test_notify_uses_registered_tray_notifier(monkeypatch):
    from timetrack.agent import idle_ui

    got = []
    idle_ui.set_notifier(lambda title, body: got.append((title, body)) or True)
    try:
        idle_ui.notify("T", "B")
    finally:
        idle_ui.set_notifier(None)
    assert got == [("T", "B")]


def test_client_marks_permanent_rejections():
    from timetrack.agent.client import ServerClient

    c = ServerClient("http://x", "t")
    for code, rejected in ((413, True), (400, True), (401, False), (409, False), (429, False), (503, False), (None, False)):
        c.last_post_status = code
        assert c.last_post_rejected is rejected, code


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API")
def test_windows_stop_request_when_no_agent_is_listening():
    from timetrack.agent import stop_signal

    # With no agent holding the test mutex name, stop() reports "not running".
    orig = stop_signal.MUTEX_NAME
    stop_signal.MUTEX_NAME = "Local\\esstracker-test-mutex-that-does-not-exist"
    try:
        assert stop_signal.request_running_agent_stop(timeout=1) is True
    finally:
        stop_signal.MUTEX_NAME = orig


def test_mark_uploaded_keeps_time_merged_during_upload():
    with AgentBuffer(":memory:") as buf:
        buf.add_activity("code", "a.py", "productive", False, 10.0, now=110.0)  # 100..110
        sent = buf.unsynced_activities()
        # Tracking loop extends the same row while the upload is in flight.
        buf.add_activity("code", "a.py", "productive", False, 5.0, now=115.0)
        buf.mark_uploaded(sent)
        left = buf.unsynced_activities()
        assert len(left) == 1
        assert left[0].start_ts == pytest.approx(110.0)
        assert left[0].duration == pytest.approx(5.0)


def test_stop_reason_round_trip(tmp_path, monkeypatch):
    from timetrack.agent import stop_signal

    monkeypatch.setattr(stop_signal, "REASON_PATH", tmp_path / "stop-reason")
    stop_signal._write_reason("update")
    assert stop_signal.read_stop_reason() == "update"
    assert stop_signal.read_stop_reason() == "quit"  # consumed
    stop_signal._write_reason("bogus")
    assert stop_signal.read_stop_reason() == "quit"


def test_macos_temporary_locations():
    from timetrack.agent.autostart import _mac_temporary_location

    assert _mac_temporary_location("/Volumes/esstracker/esstracker.app/Contents/MacOS/esstracker")
    assert _mac_temporary_location("/private/var/folders/x/AppTranslocation/ABC/d/esstracker.app/x")
    assert not _mac_temporary_location("/Applications/esstracker.app/Contents/MacOS/esstracker")
