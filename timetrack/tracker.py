"""The tracking loop: sample active window + idle state and persist it."""

from __future__ import annotations

import signal
import time
from types import FrameType

from . import platform as pf
from .config import Config, load_config
from .storage import Storage


class Tracker:
    def __init__(self, config: Config | None = None, storage: Storage | None = None):
        self.config = config or load_config()
        self._own_storage = storage is None
        self.storage = storage or Storage(self.config.db_path)
        self._running = False

    def sample_once(self, elapsed: float) -> None:
        """Take a single sample covering the last ``elapsed`` seconds."""
        idle_seconds = pf.get_idle_seconds()
        is_idle = idle_seconds >= self.config.idle_threshold

        window = pf.get_active_window()
        if window is None:
            app, title = "unknown", ""
        else:
            app, title = window.app, window.title

        if is_idle:
            category = "neutral"
        else:
            category = self.config.categorize(app, title)

        self.storage.record(
            app=app,
            title=title,
            category=category,
            idle=is_idle,
            duration=elapsed,
        )

    def run(self) -> None:
        """Run the blocking tracking loop until interrupted."""
        self._running = True
        self._install_signal_handlers()

        interval = max(1.0, self.config.poll_interval)
        print(
            f"[timetrack] tracking with backend='{pf.backend_name()}' "
            f"every {interval:.0f}s -> {self.config.db_path}"
        )

        last = time.monotonic()
        try:
            while self._running:
                time.sleep(interval)
                now = time.monotonic()
                elapsed = now - last
                last = now
                # Guard against long sleeps (e.g. laptop suspend): cap the
                # recorded span at 3x the interval so suspends don't inflate.
                elapsed = min(elapsed, interval * 3)
                try:
                    self.sample_once(elapsed)
                except Exception as exc:  # never let one bad sample kill us
                    print(f"[timetrack] sample error: {exc!r}")
        finally:
            if self._own_storage:
                self.storage.close()
            print("[timetrack] stopped.")

    def stop(self, *_: object) -> None:
        self._running = False

    def _install_signal_handlers(self) -> None:
        def handler(_signum: int, _frame: FrameType | None) -> None:
            self.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                # Not in the main thread; caller handles shutdown.
                pass


__all__ = ["Tracker"]
