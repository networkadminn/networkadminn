"""The employee agent: samples activity, captures screenshots, syncs to server.

DeskTime-aligned behaviours:
- Private Time skips sampling + screenshots
- Screenshot blur + randomized intervals from server settings (via ping)
- Optional system-tray status menu (name, org, private toggle)
"""

from __future__ import annotations

import os
import random
import signal
import threading
import time
import uuid
from types import FrameType

from .. import platform as pf
from ..monitor import extract_url
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
        self._private = False
        self._private_allowed = True
        self._shot_blur = False
        self._shot_random = True
        self._shot_interval = self.config.screenshot_interval
        self._shots_enabled = self.config.screenshots_enabled
        self._online = False
        self.username = ""
        self.display_name = ""
        self.role = ""
        self.company_name = "Euclidee Software Solutions"
        self._tray = None
        self._loop_thread: threading.Thread | None = None
        self._was_idle = False
        self._idle_started_at: float | None = None
        self._idle_countdown_pending = False
        self._user_offline = False  # tray "Offline" after countdown

    # --- public status helpers for tray ---
    @property
    def private(self) -> bool:
        return self._private

    @private.setter
    def private(self, value: bool) -> None:
        self._private = bool(value)

    @property
    def private_allowed(self) -> bool:
        return self._private_allowed

    @property
    def online(self) -> bool:
        return self._online

    @property
    def shots_enabled(self) -> bool:
        return self._shots_enabled

    def refresh_server_policy(self) -> None:
        ping = self.client.ping()
        if not ping:
            self._online = False
            if self._tray:
                self._tray.refresh_ui()
            return
        self._online = True
        self._private = bool(ping.get("private_active"))
        self._private_allowed = bool(ping.get("private_allowed", True))
        self.username = str(ping.get("user") or self.username)
        self.display_name = str(ping.get("name") or self.display_name or self.username)
        self.role = str(ping.get("role") or self.role)
        if ping.get("company"):
            self.company_name = str(ping["company"])
        shots = ping.get("screenshots") or {}
        if "enabled" in shots:
            self._shots_enabled = bool(shots["enabled"]) and self.config.screenshots_enabled
        if "blur" in shots:
            self._shot_blur = bool(shots["blur"])
        if "random" in shots:
            self._shot_random = bool(shots["random"])
        if shots.get("interval"):
            self._shot_interval = float(shots["interval"])
        if ping.get("idle_threshold"):
            self.config.idle_threshold = float(ping["idle_threshold"])
        # Clock sync check — unix epoch must match within ~2 minutes
        server_ts = ping.get("server_time")
        if isinstance(server_ts, (int, float)):
            skew = time.time() - float(server_ts)
            if abs(skew) >= 120:
                print(
                    f"[esstracker] WARNING: clock skew {skew:+.0f}s vs server. "
                    "Fix system time (NTP) for accurate tracking."
                )
            elif abs(skew) >= 30:
                print(f"[esstracker] clock offset {skew:+.1f}s vs server")
        rules = ping.get("rules")
        if isinstance(rules, dict):
            from ..config import merge_rules

            self.config.rules = merge_rules(rules)
        if self._tray:
            self._tray.refresh_ui()

    @property
    def user_offline(self) -> bool:
        """True after idle countdown completes (tray shows Offline)."""
        return self._user_offline

    def sample_activity(self, elapsed: float, now: float | None = None) -> None:
        if self._private:
            return
        now = time.time() if now is None else now
        idle_seconds = pf.get_idle_seconds()
        is_idle = idle_seconds >= self.config.idle_threshold

        # Idle edge: countdown → tray Offline. Active edge: welcome again.
        if is_idle and not self._was_idle and not self._idle_countdown_pending:
            self._idle_countdown_pending = True
            self._idle_started_at = now - idle_seconds
            from .idle_ui import show_going_offline_countdown

            def _done() -> None:
                self._user_offline = True
                self._idle_countdown_pending = False
                if self._tray:
                    self._tray.refresh_ui()

            show_going_offline_countdown(5, on_done=_done)
        elif not is_idle and (self._was_idle or self._user_offline):
            started = self._idle_started_at or (now - idle_seconds)
            offline_for = max(0.0, now - started)
            self._user_offline = False
            self._idle_countdown_pending = False
            self._idle_started_at = None
            from .idle_ui import welcome_back

            welcome_back(offline_for)
            if self._tray:
                self._tray.refresh_ui()

        self._was_idle = is_idle

        window = pf.get_active_window()
        if window is None:
            app, title = "unknown", ""
        else:
            app, title = window.app, window.title
        self._last_window = (app, title)
        url = "" if is_idle else extract_url(app, title)

        category = "neutral" if is_idle else self.config.categorize(app, title, url=url)
        self.buffer.add_activity(app, title, category, is_idle, elapsed, now, url=url)

    def capture_screenshot(self, now: float | None = None) -> bool:
        if not self._shots_enabled or self._private:
            return False
        now = time.time() if now is None else now
        result = capture_jpeg(
            max_width=self.config.screenshot_max_width,
            quality=82,
            blur=self._shot_blur,
        )
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

    def next_shot_delay(self) -> float:
        base = max(60.0, self._shot_interval)
        if self._shot_random:
            return base * (0.35 + random.random() * 0.65)
        return base

    def flush(self) -> tuple[int, int]:
        acts_sent = 0
        shots_sent = 0

        pending = self.buffer.unsynced_activities(self.config.batch_size)
        while pending:
            payload = [
                {
                    "app": a.app,
                    "title": a.title,
                    "url": a.url,
                    "category": a.category,
                    "idle": a.idle,
                    "start_ts": a.start_ts,
                    "end_ts": a.end_ts,
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
                self.buffer.mark_screenshot_synced(shot.id)
                continue
            meta = {
                "ts": str(shot.ts),
                "app": shot.app,
                "title": shot.title,
                "width": str(shot.width),
                "height": str(shot.height),
                "blurred": "1" if self._shot_blur else "0",
            }
            if not self.client.post_screenshot(data, meta):
                break
            self.buffer.mark_screenshot_synced(shot.id)
            try:
                os.remove(shot.path)
            except OSError:
                pass
            shots_sent += 1

        return acts_sent, shots_sent

    def run(self, *, tray: bool = True) -> None:
        self._running = True
        self._install_signal_handlers()
        self.refresh_server_policy()

        who = self.display_name or self.username or "??"
        print(
            f"[timetrack-agent] backend='{pf.backend_name()}' user={who} "
            f"org={self.company_name} server={self.config.server_url} "
            f"shots={'on' if self._shots_enabled else 'off'} "
            f"blur={'on' if self._shot_blur else 'off'} "
            f"private={'ON' if self._private else 'off'} "
            f"tray={'on' if tray else 'off'}"
        )

        if tray:
            from .tray import AgentTray, tray_available

            if tray_available():
                try:
                    self._tray = AgentTray(self)
                    self._tray.run(on_ready=self._run_loop)
                    return
                except Exception as exc:
                    print(f"[timetrack-agent] tray failed ({exc!r}); running headless")
                    self._tray = None
            else:
                print("[timetrack-agent] tray unavailable (install pystray); running headless")

        self._run_loop()

    def _run_loop(self) -> None:
        cfg = self.config
        interval = max(1.0, cfg.poll_interval)
        last_sample = time.monotonic()
        next_shot = time.monotonic() + self.next_shot_delay()
        next_flush = time.monotonic() + cfg.flush_interval
        next_policy = time.monotonic() + 30.0

        try:
            while self._running:
                time.sleep(interval)
                mono = time.monotonic()
                elapsed = min(mono - last_sample, interval * 3)
                last_sample = mono

                if mono >= next_policy:
                    self._safe(self.refresh_server_policy)
                    next_policy = mono + 30.0

                self._safe(self.sample_activity, elapsed)

                if self._shots_enabled and not self._private and mono >= next_shot:
                    self._safe(self.capture_screenshot)
                    next_shot = mono + self.next_shot_delay()

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
        except Exception as exc:
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
