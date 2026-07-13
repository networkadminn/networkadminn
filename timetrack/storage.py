"""SQLite storage for activity samples.

The schema is intentionally simple: one row per contiguous "activity" span
(app + title + category + productivity), with a start time, end time and a
computed duration in seconds. The tracker extends the most recent span when
the active window is unchanged, keeping the database compact.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app         TEXT    NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    category    TEXT    NOT NULL DEFAULT 'neutral',
    idle        INTEGER NOT NULL DEFAULT 0,
    start_ts    REAL    NOT NULL,
    end_ts      REAL    NOT NULL,
    duration    REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_ts);
"""


@dataclass
class Activity:
    app: str
    title: str
    category: str
    idle: bool
    start_ts: float
    end_ts: float
    duration: float
    id: int | None = None


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


class Storage:
    """Thin wrapper around a SQLite connection."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def record(
        self,
        app: str,
        title: str,
        category: str,
        idle: bool,
        duration: float,
        *,
        now: float | None = None,
    ) -> None:
        """Record ``duration`` seconds of activity ending at ``now``.

        If the previous row has the same app/title/category/idle and ends at
        approximately ``now - duration``, the two are merged into one span.
        """
        now = _now() if now is None else now
        start = now - duration

        with self._tx() as conn:
            last = conn.execute(
                "SELECT * FROM activities ORDER BY id DESC LIMIT 1"
            ).fetchone()

            mergeable = (
                last is not None
                and last["app"] == app
                and last["title"] == title
                and last["category"] == category
                and bool(last["idle"]) == idle
                and abs(last["end_ts"] - start) <= max(1.0, duration)
            )

            if mergeable:
                conn.execute(
                    "UPDATE activities SET end_ts = ?, duration = duration + ? "
                    "WHERE id = ?",
                    (now, duration, last["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO activities "
                    "(app, title, category, idle, start_ts, end_ts, duration) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (app, title, category, int(idle), start, now, duration),
                )

    def query(self, start_ts: float, end_ts: float) -> list[Activity]:
        """Return activities overlapping the ``[start_ts, end_ts)`` window."""
        rows = self._conn.execute(
            "SELECT * FROM activities WHERE end_ts >= ? AND start_ts < ? "
            "ORDER BY start_ts ASC",
            (start_ts, end_ts),
        ).fetchall()
        return [
            Activity(
                id=r["id"],
                app=r["app"],
                title=r["title"],
                category=r["category"],
                idle=bool(r["idle"]),
                start_ts=r["start_ts"],
                end_ts=r["end_ts"],
                duration=r["duration"],
            )
            for r in rows
        ]

    def all(self) -> list[Activity]:
        return self.query(0.0, _now() + 1.0)


__all__ = ["Storage", "Activity"]
