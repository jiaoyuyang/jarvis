"""Durable lifecycle tracking for maintenance tasks.

This module deliberately reuses the existing ``dingtalk_tasks`` and
``task_events`` tables.  It does not run work or change plugin routing; it
only records enough state for a task interrupted by a safe restart to be
recovered after the service comes back.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any


from core.paths import DATA_DIR


DB_PATH = DATA_DIR / "runtime_tasks.sqlite3"

CREATED = "CREATED"
RUNNING = "RUNNING"
WAITING_RESTART = "WAITING_RESTART"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
TERMINAL_STATES = {COMPLETED, FAILED}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TaskManager:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dingtalk_tasks (
                    id TEXT PRIMARY KEY, task_type TEXT NOT NULL,
                    session_id TEXT NOT NULL, status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    payload_json TEXT NOT NULL, result_text TEXT,
                    error_text TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dingtalk_tasks_status_created ON dingtalk_tasks(status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_created ON task_events(task_id, created_at)")

    def create(self, task_type: str, session_id: str, payload: dict[str, Any] | None = None) -> str:
        task_id, now = uuid.uuid4().hex, _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO dingtalk_tasks
                (id, task_type, session_id, status, priority, payload_json, attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, 100, ?, 0, ?, ?)""",
                (task_id, task_type, session_id or "", CREATED,
                 json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")), now, now),
            )
            self._event(conn, task_id, CREATED, "task created")
        return task_id

    def transition(self, task_id: str, state: str, message: str = "", *, result_text: str = "", error_text: str = "") -> None:
        if state not in {CREATED, RUNNING, WAITING_RESTART, COMPLETED, FAILED}:
            raise ValueError(f"unsupported task state: {state}")
        now = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM dingtalk_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            if row["status"] in TERMINAL_STATES and row["status"] != state:
                raise RuntimeError(f"terminal task cannot transition: {row['status']} -> {state}")
            conn.execute(
                """UPDATE dingtalk_tasks SET status=?, updated_at=?, started_at=COALESCE(started_at, ?),
                finished_at=CASE WHEN ? IN (?, ?) THEN ? ELSE finished_at END,
                result_text=CASE WHEN ? != '' THEN ? ELSE result_text END,
                error_text=CASE WHEN ? != '' THEN ? ELSE error_text END
                WHERE id=?""",
                (state, now, now, state, COMPLETED, FAILED, now,
                 result_text, result_text[-2000:], error_text, error_text[-4000:], task_id),
            )
            self._event(conn, task_id, state, message or f"task state: {state}")

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM dingtalk_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        task = dict(row)
        task["payload"] = json.loads(task.pop("payload_json") or "{}")
        return task

    def waiting_restart(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM dingtalk_tasks WHERE status=? ORDER BY created_at", (WAITING_RESTART,)).fetchall()
        tasks = []
        for row in rows:
            task = dict(row)
            task["payload"] = json.loads(task.pop("payload_json") or "{}")
            tasks.append(task)
        return tasks

    def interrupted_by_restart(self) -> list[dict[str, Any]]:
        """Return non-terminal tasks left active when this process restarted."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM dingtalk_tasks WHERE status IN (?, ?)
                ORDER BY created_at""",
                (RUNNING, WAITING_RESTART),
            ).fetchall()
        tasks = []
        for row in rows:
            task = dict(row)
            task["payload"] = json.loads(task.pop("payload_json") or "{}")
            tasks.append(task)
        return tasks

    def latest_active(self, session_id: str) -> dict[str, Any] | None:
        """Return the newest request that may still be executing for a session."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM dingtalk_tasks
                WHERE session_id=? AND status IN (?, ?)
                ORDER BY created_at DESC LIMIT 1""",
                (session_id or "", RUNNING, WAITING_RESTART),
            ).fetchone()
        if row is None:
            return None
        task = dict(row)
        task["payload"] = json.loads(task.pop("payload_json") or "{}")
        return task

    def record_event(self, task_id: str, event_type: str, message: str = "") -> None:
        """Append a lifecycle event without changing the durable task state."""
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM dingtalk_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            self._event(conn, task_id, event_type, message or event_type)

    def events(self, task_id: str) -> list[dict[str, Any]]:
        """Return task events in write order for diagnostics and regression checks."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_type, message, created_at FROM task_events WHERE task_id=? ORDER BY id",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _event(self, conn: sqlite3.Connection, task_id: str, event_type: str, message: str) -> None:
        conn.execute(
            "INSERT INTO task_events (task_id,event_type,message,metadata_json,created_at) VALUES (?, ?, ?, '{}', ?)",
            (task_id, event_type, message, _now()),
        )
