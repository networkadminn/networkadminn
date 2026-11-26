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
import sys
import threading
import time
import uuid
from types import FrameType

from .. import platform as pf
from ..monitor import clean_url, extract_url, is_browser
from ..platform.screenshot import capture_jpeg, ensure_macos_screen_recording_access
from .buffer import AgentBuffer
from .client import ServerClient
from .config import AgentConfig, load_agent_config

# Unsynced screenshots kept on disk while offline (oldest are dropped beyond this).
MAX_PENDING_SHOTS = 300
# Uploads hold back recent activity this long (capped) so time that turns out to
# be idle can still be re-labelled before it reaches the server.
MAX_IDLE_HOLDBACK = 900.0
# Synced rows are pruned from the local buffer after this many seconds.
SYNCED_RETENTION = 3 * 24 * 3600.0


class Agent:
    def __init__(self, config: AgentConfig | None = None):
        self.config = config or load_agent_config()
        os.makedirs(self.config.shots_dir, exist_ok=True)
        self.buffer = AgentBuffer(self.config.buffer_path)
        self.client = ServerClient(self.config.server_url, self.config.api_token)
        self._running = False
        self._stop_reason = "quit"
        self._last_window = ("unknown", "")
        self._private = False
        self._private_allowed = True
        self._tray_logout_allowed, self._tray_quit_allowed = self._load_tray_policy()
        self._shot_blur = False
        self._shot_random = True
        self._shot_interval = self.config.screenshot_interval
        self._shots_enabled = self.config.screenshots_enabled
        self._online = False
        self.username = ""
        self.display_name = ""
        self.role = ""
        self.company_name = "ESS Tracker"
        self._tray = None
        self._loop_thread: threading.Thread | None = None
        self._was_idle = False
        self._idle_started_at: float | None = None
        self._idle_countdown_pending = False
        self._user_offline = False  # tray "Offline" after countdown
        self._flush_lock = threading.Lock()
        self._relogin_lock = threading.Lock()

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
    def is_admin(self) -> bool:
        return (self.role or "").lower() in ("admin", "superadmin")

    @property
    def tray_logout_allowed(self) -> bool:
        return self.is_admin or self._tray_logout_allowed

    @property
    def tray_quit_allowed(self) -> bool:
        return self.is_admin or self._tray_quit_allowed

    def _tray_policy_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.config.buffer_path)), "tray_policy.json")

    def _load_tray_policy(self) -> tuple[bool, bool]:
        """Last policy from the server, so it still applies when starting offline."""
        import json

        try:
            with open(self._tray_policy_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            return bool(data.get("logout", True)), bool(data.get("quit", True))
        except (OSError, ValueError, AttributeError):
            return True, True

    def _save_tray_policy(self) -> None:
        import json

        try:
            with open(self._tray_policy_path(), "w", encoding="utf-8") as fh:
                json.dump({"logout": self._tray_logout_allowed, "quit": self._tray_quit_allowed}, fh)
        except OSError:
            pass

    @property
    def online(self) -> bool:
        return self._online

    @property
    def shots_enabled(self) -> bool:
        return self._shots_enabled

    def refresh_server_policy(self) -> None:
        ping = self.client.ping()
        if not ping and self.client.unauthorized and self._running:
            if self._relogin():
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
        policy = (
            bool(ping.get("tray_logout_allowed", True)),
            bool(ping.get("tray_quit_allowed", True)),
        )
        if policy != (self._tray_logout_allowed, self._tray_quit_allowed):
            self._tray_logout_allowed, self._tray_quit_allowed = policy
            self._save_tray_policy()
        if ping.get("company"):
            self.company_name = str(ping["company"])
        if ping.get("client_update"):
            from .update_ui import maybe_prompt_update

            maybe_prompt_update(
                ping["client_update"],
                server_url=self.config.server_url,
                state_dir=os.path.dirname(os.path.abspath(self.config.buffer_path)),
            )
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

            self.config.rules = merge_rules(rules, org_only=True)
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
            # The threshold minutes before detection were logged as active: fix them.
            self.buffer.mark_idle_since(self._idle_started_at)
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
        url = ""
        if not is_idle:
            if is_browser(app):
                url = clean_url(pf.get_browser_url(window))
            url = url or extract_url(app, title)

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
        self.buffer.add_screenshot(now, path, app, title, width, height, blurred=self._shot_blur)
        for old in self.buffer.trim_screenshots(MAX_PENDING_SHOTS):
            try:
                os.remove(old)
            except OSError:
                pass
        return True

    def next_shot_delay(self) -> float:
        base = max(60.0, self._shot_interval)
        if self._shot_random:
            return base * (0.35 + random.random() * 0.65)
        return base

    def upload_holdback(self) -> float:
        """Seconds of recent activity kept local so it can still become idle."""
        return min(max(0.0, float(self.config.idle_threshold)), MAX_IDLE_HOLDBACK) + 2 * max(
            1.0, float(self.config.poll_interval)
        )

    def flush(self, holdback: float = 0.0) -> tuple[int, int]:
        """Upload buffered activity and screenshots; returns ``(activities, shots)``.

        Serialised with a lock: the tray's "Sync now" and the tracking loop must
        not upload the same unsynced rows twice. ``holdback`` keeps the most recent
        seconds local (see :meth:`upload_holdback`); the final flush on exit sends all.
        """
        with self._flush_lock:
            return self._flush_locked(holdback)

    def _flush_locked(self, holdback: float) -> tuple[int, int]:
        acts_sent = 0
        shots_sent = 0

        cutoff = None
        if holdback > 0:
            cutoff = time.time() - holdback
            self.buffer.seal_before(cutoff)

        limit = max(1, int(self.config.batch_size))
        while True:
            pending = self.buffer.unsynced_activities(limit, ended_before=cutoff)
            if not pending:
                break
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
            if self.client.post_activities(payload):
                acts_sent += len(pending)
            else:
                if not self.client.last_post_rejected:
                    break  # offline, signed out, Private Time or server error: retry later
                if len(pending) > 1:
                    limit = max(1, len(pending) // 2)  # e.g. 413: retry in smaller batches
                    continue
                print(
                    f"[esstracker] server rejected an activity "
                    f"(HTTP {self.client.last_post_status}); dropping it"
                )
            self.buffer.mark_uploaded(pending)

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
                "blurred": "1" if shot.blurred else "0",
            }
            if not self.client.post_screenshot(data, meta):
                if not self.client.last_post_rejected:
                    break
                print(
                    f"[esstracker] server rejected a screenshot "
                    f"(HTTP {self.client.last_post_status}); dropping it"
                )
            else:
                shots_sent += 1
            self.buffer.mark_screenshot_synced(shot.id)
            try:
                os.remove(shot.path)
            except OSError:
                pass

        return acts_sent, shots_sent

    def run(self, *, tray: bool = True) -> None:
        self._running = True
        self._install_signal_handlers()
        from .stop_signal import watch_for_stop

        watch_for_stop(self._stop_requested_externally)
        if sys.platform == "darwin":
            from .autostart import enable_once_on_macos

            enable_once_on_macos(os.path.dirname(os.path.abspath(self.config.buffer_path)))
            if self._shots_enabled:
                ensure_macos_screen_recording_access()
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
        if pf.backend_name() == "linux" and not pf.idle_detection_available():
            print(
                "[timetrack-agent] WARNING: idle detection unavailable — "
                "install xprintidle (sudo apt install xprintidle) or use an X11 session. "
                "Away time may be logged as active until fixed."
            )
        if pf.backend_name() == "linux" and (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or os.environ.get("WAYLAND_DISPLAY")
        ):
            print(
                "[timetrack-agent] WARNING: Wayland session — window titles and app names "
                "are only visible for X11 apps. Choose 'Ubuntu on Xorg' at the login "
                "screen for full tracking."
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
        next_prune = time.monotonic() + 60.0

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
                    self._safe(self.flush, self.upload_holdback())
                    next_flush = mono + cfg.flush_interval

                if mono >= next_prune:
                    self._safe(self.buffer.prune_synced, time.time() - SYNCED_RETENTION)
                    next_prune = mono + 3600.0
        finally:
            self._safe(self.flush)
            if self.client.token:
                self._safe(self.client.notify_stop, self._stop_reason)
            self.buffer.close()
            print("[timetrack-agent] stopped.")

    def _relogin(self) -> bool:
        """Session ended (30 days or revoked): ask to sign in, or quit if cancelled.

        Runs from the tracking thread, so the sign-in window is opened in a child
        process (a Tk window off the main thread crashes the macOS app).
        """
        from .ui_process import run_ui

        if not self._relogin_lock.acquire(blocking=False):
            # Another thread is already showing the sign-in window: wait for it.
            with self._relogin_lock:
                return bool(self.client.token) and self._running
        try:
            return self._relogin_locked(run_ui)
        finally:
            self._relogin_lock.release()

    def _relogin_locked(self, run_ui) -> bool:
        print("[esstracker] session expired — please sign in again")
        if run_ui("login") == 0:
            fresh = load_agent_config()
            if fresh.api_token:
                self.config.api_token = fresh.api_token
                self.config.server_url = fresh.server_url
                self.client.base_url = fresh.server_url.rstrip("/")
                self.client.token = fresh.api_token
                return True
        print("[esstracker] sign-in cancelled — stopping")
        self.request_stop("logout")
        if self._tray is not None:
            self._tray.stop_icon()
        return False

    def _stop_requested_externally(self) -> None:
        """``esstracker-agent stop`` (the installer passes ``--reason update/uninstall``)."""
        from .stop_signal import read_stop_reason

        self._stop_reason = read_stop_reason()
        self.stop()

    def stop(self, *_: object) -> None:
        """Stop tracking (signal handler / installer request) and close the tray."""
        self._running = False
        if self._tray is not None:
            self._tray.stop_icon()

    def request_stop(self, reason: str = "quit") -> None:
        self._stop_reason = reason
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
            # SIGTERM from ``esstracker-agent stop`` leaves its reason in a file.
            from .stop_signal import read_stop_reason

            self._stop_reason = read_stop_reason(self._stop_reason)
            self.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


__all__ = ["Agent"]
