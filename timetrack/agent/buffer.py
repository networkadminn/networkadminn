"""Local, offline-safe buffer for the agent.

Activity spans and screenshot references are written here first, then a
syncer flushes them to the server and marks them synced. This means the
agent keeps working (and loses no data) when the network or server is down.

The buffer is shared by the tracking loop and the tray ("Sync now"), so every
operation runs under one lock.
"""

from __future__ import annotations

import functools
import os
import sqlite3
import threading
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
    blurred INTEGER NOT NULL DEFAULT 0,
    synced INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_act_synced ON activities(synced);
CREATE INDEX IF NOT EXISTS idx_shot_synced ON screenshots(synced);
"""

NEUTRAL = "neutral"


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
    blurred: bool = False


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class AgentBuffer:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
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
        shot_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(screenshots)")}
        if "blurred" not in shot_cols:
            self._conn.execute(
                "ALTER TABLE screenshots ADD COLUMN blurred INTEGER NOT NULL DEFAULT 0"
            )

    @_locked
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AgentBuffer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @_locked
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

    @_locked
    def mark_idle_since(self, since_ts: float) -> int:
        """Re-label unsynced *active* time after ``since_ts`` as idle.

        Idle is only detected once the idle threshold has passed, so the minutes
        before that were recorded as active. Rows fully after ``since_ts`` become
        idle; a row that straddles it is split. Returns the number of rows changed.
        """
        rows = self._conn.execute(
            "SELECT * FROM activities WHERE synced=0 AND idle=0 AND end_ts > ? ORDER BY id",
            (since_ts,),
        ).fetchall()
        changed = 0
        for r in rows:
            start, end = float(r["start_ts"]), float(r["end_ts"])
            span = max(end - start, 1e-9)
            if start >= since_ts:
                self._conn.execute(
                    "UPDATE activities SET idle=1, category=? WHERE id=?",
                    (NEUTRAL, r["id"]),
                )
            else:
                active_part = float(r["duration"]) * (since_ts - start) / span
                idle_part = max(0.0, float(r["duration"]) - active_part)
                self._conn.execute(
                    "UPDATE activities SET end_ts=?, duration=? WHERE id=?",
                    (since_ts, active_part, r["id"]),
                )
                self._conn.execute(
                    "INSERT INTO activities "
                    "(app,title,url,category,idle,start_ts,end_ts,duration,synced) "
                    "VALUES (?,?,?,?,1,?,?,?,0)",
                    (r["app"], r["title"], r["url"], NEUTRAL, since_ts, end, idle_part),
                )
            changed += 1
        self._conn.commit()
        return changed

    @_locked
    def seal_before(self, cutoff: float) -> None:
        """Split unsynced rows that straddle ``cutoff`` so the older part can sync.

        Uploads hold back the most recent minutes (they may still turn out to be
        idle). Without the split, one long span in a single window would keep its
        ``end_ts`` moving and never reach the server until the window changed.
        """
        rows = self._conn.execute(
            "SELECT * FROM activities WHERE synced=0 AND start_ts < ? AND end_ts > ?",
            (cutoff, cutoff),
        ).fetchall()
        for r in rows:
            start, end = float(r["start_ts"]), float(r["end_ts"])
            span = max(end - start, 1e-9)
            older = float(r["duration"]) * (cutoff - start) / span
            newer = max(0.0, float(r["duration"]) - older)
            # Re-insert both parts so the newer one has the highest id and later
            # samples keep merging into it.
            self._conn.execute(
                "INSERT INTO activities "
                "(app,title,url,category,idle,start_ts,end_ts,duration,synced) "
                "VALUES (?,?,?,?,?,?,?,?,0)",
                (r["app"], r["title"], r["url"], r["category"], r["idle"], start, cutoff, older),
            )
            self._conn.execute(
                "INSERT INTO activities "
                "(app,title,url,category,idle,start_ts,end_ts,duration,synced) "
                "VALUES (?,?,?,?,?,?,?,?,0)",
                (r["app"], r["title"], r["url"], r["category"], r["idle"], cutoff, end, newer),
            )
            self._conn.execute("DELETE FROM activities WHERE id=?", (r["id"],))
        if rows:
            self._conn.commit()

    @_locked
    def add_screenshot(
        self,
        ts: float,
        path: str,
        app: str,
        title: str,
        width: int,
        height: int,
        blurred: bool = False,
    ) -> None:
        self._conn.execute(
            "INSERT INTO screenshots (ts,path,app,title,width,height,blurred,synced) "
            "VALUES (?,?,?,?,?,?,?,0)",
            (ts, path, app, title, width, height, int(blurred)),
        )
        self._conn.commit()

    @_locked
    def unsynced_activities(
        self, limit: int = 200, *, ended_before: float | None = None
    ) -> list[BufferedActivity]:
        """Oldest unsynced rows; ``ended_before`` holds back still-open recent spans."""
        if ended_before is None:
            rows = self._conn.execute(
                "SELECT * FROM activities WHERE synced=0 ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM activities WHERE synced=0 AND end_ts <= ? "
                "ORDER BY id ASC LIMIT ?",
                (ended_before, limit),
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

    @_locked
    def unsynced_screenshots(self, limit: int = 50) -> list[BufferedShot]:
        rows = self._conn.execute(
            "SELECT * FROM screenshots WHERE synced=0 ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            BufferedShot(
                id=r["id"], ts=r["ts"], path=r["path"], app=r["app"],
                title=r["title"], width=r["width"], height=r["height"],
                blurred=bool(r["blurred"]),
            )
            for r in rows
        ]

    @_locked
    def mark_activities_synced(self, ids: list[int]) -> None:
        if not ids:
            return
        qmarks = ",".join("?" * len(ids))
        self._conn.execute(
            f"UPDATE activities SET synced=1 WHERE id IN ({qmarks})", ids
        )
        self._conn.commit()

    @_locked
    def mark_uploaded(self, uploaded: list[BufferedActivity]) -> None:
        """Mark rows synced, keeping any time merged into them during the upload.

        The tracking loop can extend the open row (``add_activity`` merge) while a
        "Sync now" upload of it is in flight. Such a row keeps only the part that was
        not sent, as a fresh unsynced span starting where the upload ended.
        """
        for a in uploaded:
            row = self._conn.execute(
                "SELECT end_ts, duration, synced FROM activities WHERE id=?", (a.id,)
            ).fetchone()
            if row is None or row["synced"]:
                continue
            if float(row["end_ts"]) <= float(a.end_ts) + 1e-6:
                self._conn.execute("UPDATE activities SET synced=1 WHERE id=?", (a.id,))
            else:
                self._conn.execute(
                    "UPDATE activities SET start_ts=?, duration=? WHERE id=?",
                    (a.end_ts, max(0.0, float(row["duration"]) - float(a.duration)), a.id),
                )
        self._conn.commit()

    @_locked
    def mark_screenshot_synced(self, shot_id: int) -> None:
        self._conn.execute(
            "UPDATE screenshots SET synced=1 WHERE id=?", (shot_id,)
        )
        self._conn.commit()

    @_locked
    def trim_screenshots(self, keep: int) -> list[str]:
        """Drop the oldest unsynced screenshots beyond ``keep``; return their file paths.

        Long offline periods would otherwise fill the disk with JPEGs.
        """
        rows = self._conn.execute(
            "SELECT id, path FROM screenshots WHERE synced=0 ORDER BY id DESC LIMIT -1 OFFSET ?",
            (max(0, keep),),
        ).fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        qmarks = ",".join("?" * len(ids))
        self._conn.execute(f"UPDATE screenshots SET synced=1 WHERE id IN ({qmarks})", ids)
        self._conn.commit()
        return [r["path"] for r in rows]

    @_locked
    def prune_synced(self, older_than: float) -> None:
        """Delete synced rows older than ``older_than`` (keeps the file small)."""
        self._conn.execute(
            "DELETE FROM activities WHERE synced=1 AND end_ts < ?",
            (older_than,),
        )
        self._conn.execute("DELETE FROM screenshots WHERE synced=1 AND ts < ?", (older_than,))
        self._conn.commit()

    @_locked
    def pending_counts(self) -> tuple[int, int]:
        acts = self._conn.execute(
            "SELECT COUNT(*) FROM activities WHERE synced=0"
        ).fetchone()[0]
        shots = self._conn.execute(
            "SELECT COUNT(*) FROM screenshots WHERE synced=0"
        ).fetchone()[0]
        return int(acts), int(shots)


__all__ = ["AgentBuffer", "BufferedActivity", "BufferedShot"]
