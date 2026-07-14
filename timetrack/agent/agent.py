"""The employee agent: single-threaded loop that samples activity, captures
screenshots, buffers everything locally, and syncs to the server.

Design notes:
- One event loop keeps SQLite access single-threaded (simple + safe).
- Timers decide when to take screenshots and when to flush to the server.
- Nothing here raises on transient failures; unsynced data stays buffered.
"""

from __future__ import annotations

import os
import signal
import time
import uuid
from types import FrameType

from .. import platform as pf
from ..platform.screenshot import capture_jpeg
from .buffer import AgentBuffer
from .client import ServerClient
from .config import AgentConfig, load_agent_config


class Agent:
    def __init__(self, config: AgentConfig | None = None):
        self.config = config or load_agent_config()
        os.makedirs(self.config.shots_dir, exist_ok=True)
        self.buffer = AgentBuffer(self.config.buffer_path)
        self.client = ServerClient(self.config.server_url, self.config.api_token)
        self._running = False
        self._last_window = ("unknown", "")

    # --- individual steps (unit-testable) ---
    def sample_activity(self, elapsed: float, now: float | None = None) -> None:
        now = time.time() if now is None else now
        idle_seconds = pf.get_idle_seconds()
        is_idle = idle_seconds >= self.config.idle_threshold

        window = pf.get_active_window()
        if window is None:
            app, title = "unknown", ""
        else:
            app, title = window.app, window.title
        self._last_window = (app, title)

        category = "neutral" if is_idle else self.config.categorize(app, title)
        self.buffer.add_activity(app, title, category, is_idle, elapsed, now)

    def capture_screenshot(self, now: float | None = None) -> bool:
        if not self.config.screenshots_enabled:
            return False
        now = time.time() if now is None else now
        result = capture_jpeg(max_width=self.config.screenshot_max_width)
        if result is None:
            return False
        data, width, height = result
        name = f"{uuid.uuid4().hex}.jpg"
        path = os.path.join(self.config.shots_dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        app, title = self._last_window
        self.buffer.add_screenshot(now, path, app, title, width, height)
        return True

    def flush(self) -> tuple[int, int]:
        """Push buffered data to the server. Returns (activities, shots) sent."""
        acts_sent = 0
        shots_sent = 0

        pending = self.buffer.unsynced_activities(self.config.batch_size)
        while pending:
            payload = [
                {
                    "app": a.app, "title": a.title, "category": a.category,
                    "idle": a.idle, "start_ts": a.start_ts, "end_ts": a.end_ts,
                    "duration": a.duration,
                }
                for a in pending
            ]
            if not self.client.post_activities(payload):
                break
            self.buffer.mark_activities_synced([a.id for a in pending])
            acts_sent += len(pending)
            if len(pending) < self.config.batch_size:
                break
            pending = self.buffer.unsynced_activities(self.config.batch_size)

        for shot in self.buffer.unsynced_screenshots():
            try:
                with open(shot.path, "rb") as fh:
                    data = fh.read()
            except OSError:
                # File vanished; drop the reference so we don't retry forever.
                self.buffer.mark_screenshot_synced(shot.id)
                continue
            meta = {
                "ts": shot.ts, "app": shot.app, "title": shot.title,
                "width": shot.width, "height": shot.height,
            }
            if not self.client.post_screenshot(data, meta):
                break
            self.buffer.mark_screenshot_synced(shot.id)
            try:
                os.remove(shot.path)  # server has it now
            except OSError:
                pass
            shots_sent += 1

        return acts_sent, shots_sent

    # --- main loop ---
    def run(self) -> None:
        self._running = True
        self._install_signal_handlers()

        cfg = self.config
        interval = max(1.0, cfg.poll_interval)
        ping = self.client.ping()
        who = ping.get("user") if ping else "?? (server unreachable, buffering)"
        print(
            f"[timetrack-agent] backend='{pf.backend_name()}' user={who} "
            f"server={cfg.server_url} shots={'on' if cfg.screenshots_enabled else 'off'}"
        )

        last_sample = time.monotonic()
        next_shot = time.monotonic() + cfg.screenshot_interval
        next_flush = time.monotonic() + cfg.flush_interval

        try:
            while self._running:
                time.sleep(interval)
                mono = time.monotonic()
                elapsed = min(mono - last_sample, interval * 3)
                last_sample = mono

                self._safe(self.sample_activity, elapsed)

                if cfg.screenshots_enabled and mono >= next_shot:
                    self._safe(self.capture_screenshot)
                    next_shot = mono + cfg.screenshot_interval

                if mono >= next_flush:
                    self._safe(self.flush)
                    next_flush = mono + cfg.flush_interval
        finally:
            self._safe(self.flush)
            self.buffer.close()
            print("[timetrack-agent] stopped.")

    def stop(self, *_: object) -> None:
        self._running = False

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception as exc:  # never let the loop die
            print(f"[timetrack-agent] error in {fn.__name__}: {exc!r}")
            return None

    def _install_signal_handlers(self) -> None:
        def handler(_s: int, _f: FrameType | None) -> None:
            self.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


__all__ = ["Agent"]
