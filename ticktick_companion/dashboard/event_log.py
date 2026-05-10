"""Append-only SQLite log of dashboard actions.

The TickTick public API doesn't expose completion or change history, so
the dashboard records every mutation it issues into a local SQLite file.
This powers "Completed today" in End of Day and is a foundation for
weekly retro stats.

The schema is intentionally flat: one row per action, with denormalized
local date for fast "what happened today" queries. Mock mode passes
`":memory:"` so demo data doesn't pollute the real DB.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

DEFAULT_DB_PATH = Path.home() / ".ticktick-dashboard.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  ts_local_date TEXT NOT NULL,
  action TEXT NOT NULL,
  task_id TEXT,
  project_id TEXT,
  task_title TEXT,
  project_name TEXT,
  priority_before INTEGER,
  priority_after INTEGER,
  due_before TEXT,
  due_after TEXT,
  details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_local_date ON events(ts_local_date);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
"""


class EventLog:
    """Thread-safe append-only event log backed by SQLite."""

    def __init__(self, db_path: str = ":memory:",
                 tz_name: str = "America/Los_Angeles"):
        self._tz = ZoneInfo(tz_name)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _local_date_str(self, dt: datetime) -> str:
        return dt.astimezone(self._tz).date().isoformat()

    def record(
        self,
        action: str,
        task: Optional[Dict[str, Any]] = None,
        *,
        priority_before: Optional[int] = None,
        priority_after: Optional[int] = None,
        due_before: Optional[str] = None,
        due_after: Optional[str] = None,
        project_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        task = task or {}
        with self._lock:
            self._conn.execute(
                """INSERT INTO events
                   (ts, ts_local_date, action, task_id, project_id, task_title,
                    project_name, priority_before, priority_after, due_before,
                    due_after, details_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now.isoformat(),
                    self._local_date_str(now),
                    action,
                    task.get("id"),
                    task.get("projectId"),
                    task.get("title"),
                    project_name,
                    priority_before,
                    priority_after,
                    due_before,
                    due_after,
                    json.dumps(details) if details else None,
                ),
            )

    @property
    def revision(self) -> int:
        return self.latest_id

    @property
    def latest_id(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM events").fetchone()
        return int(row["id"] if row else 0)

    def today_local_date(self) -> str:
        return self._local_date_str(datetime.now(timezone.utc))

    def completed_on(self, local_date: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM events
                   WHERE action = 'complete' AND ts_local_date = ?
                   ORDER BY ts ASC""",
                (local_date,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def actions_on(self, local_date: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM events
                   WHERE ts_local_date = ?
                   ORDER BY ts ASC""",
                (local_date,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def stats_on(self, local_date: str) -> Dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT action, COUNT(*) AS n FROM events
                   WHERE ts_local_date = ? GROUP BY action""",
                (local_date,),
            ).fetchall()
        return {r["action"]: r["n"] for r in rows}

    def recent(self, n: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def reschedule_count(self, task_id: str) -> int:
        if not task_id:
            return 0
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS n FROM events
                   WHERE task_id = ? AND action = 'reschedule'""",
                (task_id,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def reschedule_counts(self) -> Dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT task_id, COUNT(*) AS n FROM events
                   WHERE task_id IS NOT NULL AND action = 'reschedule'
                   GROUP BY task_id"""
            ).fetchall()
        return {r["task_id"]: int(r["n"]) for r in rows}

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        if d.get("details_json"):
            try:
                d["details"] = json.loads(d["details_json"])
            except (json.JSONDecodeError, TypeError):
                d["details"] = None
        else:
            d["details"] = None
        return d
