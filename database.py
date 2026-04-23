"""
database.py
-----------
SQLite-backed persistence for tasks and their execution steps.
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager

# Determine appropriate path based on environment permissions
if os.path.exists("/app/data"):
    # Render persistent disk
    DB_PATH = "/app/data/chats.db"
elif os.access(".", os.W_OK):
    # Local development
    DB_PATH = "chats.db"
else:
    # Serverless (Vercel/AWS Lambda) read-only filesystem fallback
    DB_PATH = "/tmp/chats.db"


def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'idle',
                result TEXT,
                model TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT DEFAULT '',
                browser_url TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_steps_task_id ON task_steps(task_id)
        """)


def create_task(prompt: str, model: str = "openai/gpt-oss-120b") -> dict:
    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (id, prompt, status, model, created_at, updated_at) VALUES (?, ?, 'running', ?, ?, ?)",
            (task_id, prompt, model, now, now),
        )
    return {"id": task_id, "prompt": prompt, "status": "running", "model": model, "result": None, "created_at": now, "updated_at": now}


def list_tasks() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, prompt, status, result, model, created_at, updated_at FROM tasks ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_task(task_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, prompt, status, result, model, created_at, updated_at FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    return dict(row) if row else None


def update_task(task_id: str, status: str, result: str | None = None):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        if result is not None:
            conn.execute(
                "UPDATE tasks SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                (status, result, now, task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, task_id),
            )


def delete_task(task_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return cursor.rowcount > 0


def add_step(task_id: str, step_number: int, step_type: str, status: str, title: str, detail: str = "", browser_url: str = ""):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO task_steps (task_id, step_number, type, status, title, detail, browser_url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, step_number, step_type, status, title, detail, browser_url, now),
        )


def get_steps(task_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT step_number, type, status, title, detail, browser_url, created_at FROM task_steps WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
    return [dict(r) for r in rows]
