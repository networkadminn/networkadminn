"""Local, offline-safe buffer for the agent.

Activity spans and screenshot references are written here first, then a
syncer flushes them to the server and marks them synced. This means the
agent keeps working (and loses no data) when the network or server is down.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    app      TEXT NOT NULL,
    title    TEXT NOT NULL DEFAULT '',
    url      TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'neutral',
    idle     INTEGER NOT NULL DEFAULT 0,
    start_ts REAL NOT NULL,
    end_ts   REAL NOT NULL,
    duration REAL NOT NULL DEFAULT 0,
    synced   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS screenshots (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    path   TEXT NOT NULL,
    app    TEXT NOT NULL DEFAULT '',
    title  TEXT NOT NULL DEFAULT '',
    width  INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    synced INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_act_synced ON activities(synced);
CREATE INDEX IF NOT EXISTS idx_shot_synced ON screenshots(synced);
"""


@dataclass
class BufferedActivity:
    id: int
    app: str
    title: str
    url: str
    category: str
    idle: bool
    start_ts: float
    end_ts: float
    duration: float


@dataclass
class BufferedShot:
    id: int
    ts: float
    path: str
    app: str
    title: str
    width: int
    height: int


class AgentBuffer:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._ensure_columns()
        self._conn.commit()

    def _ensure_columns(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(activities)")}
        if "url" not in cols:
            self._conn.execute(
                "ALTER TABLE activities ADD COLUMN url TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AgentBuffer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def add_activity(
        self,
        app: str,
        title: str,
        category: str,
        idle: bool,
        duration: float,
        now: float,
        url: str = "",
    ) -> None:
        """Append an activity span, merging into the last *unsynced* row when
        it matches (keeps the buffer small without risking re-send gaps)."""
        start = now - duration
        url = url or ""
        cur = self._conn.execute(
            "SELECT * FROM activities ORDER BY id DESC LIMIT 1"
        ).fetchone()
        mergeable = (
            cur is not None
            and cur["synced"] == 0
            and cur["app"] == app
            and cur["title"] == title
            and (cur["url"] if "url" in cur.keys() else "") == url
            and cur["category"] == category
            and bool(cur["idle"]) == idle
            and abs(cur["end_ts"] - start) <= max(1.0, duration)
        )
        if mergeable:
            self._conn.execute(
                "UPDATE activities SET end_ts=?, duration=duration+? WHERE id=?",
                (now, duration, cur["id"]),
            )
        else:
            self._conn.execute(
                "INSERT INTO activities "
                "(app,title,url,category,idle,start_ts,end_ts,duration,synced) "
                "VALUES (?,?,?,?,?,?,?,?,0)",
                (app, title, url, category, int(idle), start, now, duration),
            )
        self._conn.commit()

    def add_screenshot(
        self, ts: float, path: str, app: str, title: str, width: int, height: int
    ) -> None:
        self._conn.execute(
            "INSERT INTO screenshots (ts,path,app,title,width,height,synced) "
            "VALUES (?,?,?,?,?,?,0)",
            (ts, path, app, title, width, height),
        )
        self._conn.commit()

    def unsynced_activities(self, limit: int = 200) -> list[BufferedActivity]:
        rows = self._conn.execute(
            "SELECT * FROM activities WHERE synced=0 ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            BufferedActivity(
                id=r["id"],
                app=r["app"],
                title=r["title"],
                url=r["url"] if "url" in r.keys() else "",
                category=r["category"],
                idle=bool(r["idle"]),
                start_ts=r["start_ts"],
                end_ts=r["end_ts"],
                duration=r["duration"],
            )
            for r in rows
        ]

    def unsynced_screenshots(self, limit: int = 50) -> list[BufferedShot]:
        rows = self._conn.execute(
            "SELECT * FROM screenshots WHERE synced=0 ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            BufferedShot(
                id=r["id"], ts=r["ts"], path=r["path"], app=r["app"],
                title=r["title"], width=r["width"], height=r["height"],
            )
            for r in rows
        ]

    def mark_activities_synced(self, ids: list[int]) -> None:
        if not ids:
            return
        qmarks = ",".join("?" * len(ids))
        self._conn.execute(
            f"UPDATE activities SET synced=1 WHERE id IN ({qmarks})", ids
        )
        self._conn.commit()

    def mark_screenshot_synced(self, shot_id: int) -> None:
        self._conn.execute(
            "UPDATE screenshots SET synced=1 WHERE id=?", (shot_id,)
        )
        self._conn.commit()

    def pending_counts(self) -> tuple[int, int]:
        acts = self._conn.execute(
            "SELECT COUNT(*) FROM activities WHERE synced=0"
        ).fetchone()[0]
        shots = self._conn.execute(
            "SELECT COUNT(*) FROM screenshots WHERE synced=0"
        ).fetchone()[0]
        return int(acts), int(shots)


__all__ = ["AgentBuffer", "BufferedActivity", "BufferedShot"]
