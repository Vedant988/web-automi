"""
web_automi/services/db_service.py
---------------------------------
SQLite database concrete implementation of the IDatabase interface.
"""

import sqlite3
import uuid
import hashlib
import secrets
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from web_automi.core.interfaces import IDatabase
from web_automi.core.exceptions import DatabaseError


class SQLiteDatabaseService(IDatabase):
    """OOP SQLite Database Service providing safe persistence with WAL and busy timeouts."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        except sqlite3.Error as err:
            raise DatabaseError(f"Failed to open database connection: {err}")

    @contextmanager
    def get_db(self):
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as err:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise DatabaseError(f"Database transaction failure: {err}")
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    result TEXT,
                    model TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            
            # Migration check: user_id column
            cursor = conn.execute("PRAGMA table_info(tasks)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "user_id" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
                
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_steps_task_id ON task_steps(task_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)")

    def _hash_password(self, password: str) -> str:
        salt = "web_automi_salt_"
        return hashlib.sha256((salt + password).encode('utf-8')).hexdigest()

    def register_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        username = username.strip().lower()
        now = datetime.now(timezone.utc).isoformat()
        pwd_hash = self._hash_password(password)
        try:
            with self.get_db() as conn:
                cursor = conn.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, pwd_hash, now)
                )
                user_id = cursor.lastrowid
            return {"id": user_id, "username": username}
        except DatabaseError as dberr:
            if "UNIQUE constraint failed" in str(dberr):
                return None
            raise
        except sqlite3.IntegrityError:
            return None

    def login_user(self, username: str, password: str) -> Optional[str]:
        username = username.strip().lower()
        pwd_hash = self._hash_password(password)
        with self.get_db() as conn:
            user = conn.execute(
                "SELECT id FROM users WHERE username = ? AND password_hash = ?",
                (username, pwd_hash)
            ).fetchone()
            
            if user:
                token = secrets.token_urlsafe(32)
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                    (token, user["id"], now)
                )
                return token
        return None

    def get_user_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        with self.get_db() as conn:
            row = conn.execute("""
                SELECT u.id, u.username 
                FROM users u
                JOIN sessions s ON u.id = s.user_id
                WHERE s.token = ?
            """, (token,)).fetchone()
        return dict(row) if row else None

    def logout_user(self, token: str) -> None:
        if token:
            with self.get_db() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def create_task(self, prompt: str, user_id: Optional[int] = None, model: str = "openai/gpt-oss-120b") -> Dict[str, Any]:
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.get_db() as conn:
            conn.execute(
                "INSERT INTO tasks (id, user_id, prompt, status, model, created_at, updated_at) VALUES (?, ?, ?, 'running', ?, ?, ?)",
                (task_id, user_id, prompt, model, now, now),
            )
        return {"id": task_id, "user_id": user_id, "prompt": prompt, "status": "running", "model": model, "result": None, "created_at": now, "updated_at": now}

    def list_tasks(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.get_db() as conn:
            if user_id is not None:
                rows = conn.execute(
                    "SELECT id, prompt, status, result, model, created_at, updated_at FROM tasks WHERE user_id = ? ORDER BY updated_at DESC",
                    (user_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, prompt, status, result, model, created_at, updated_at FROM tasks ORDER BY updated_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def get_task(self, task_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self.get_db() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT id, prompt, status, result, model, created_at, updated_at FROM tasks WHERE id = ? AND user_id = ?",
                    (task_id, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, prompt, status, result, model, created_at, updated_at FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
        return dict(row) if row else None

    def update_task(self, task_id: str, status: str, result: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.get_db() as conn:
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

    def delete_task(self, task_id: str, user_id: Optional[int] = None) -> bool:
        with self.get_db() as conn:
            if user_id is not None:
                cursor = conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
            else:
                cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0

    def add_step(self, task_id: str, step_number: int, step_type: str, status: str, title: str, detail: str = "", browser_url: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.get_db() as conn:
            conn.execute(
                "INSERT INTO task_steps (task_id, step_number, type, status, title, detail, browser_url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, step_number, step_type, status, title, detail, browser_url, now),
            )

    def get_steps(self, task_id: str) -> List[Dict[str, Any]]:
        with self.get_db() as conn:
            rows = conn.execute(
                "SELECT step_number, type, status, title, detail, browser_url, created_at FROM task_steps WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]
