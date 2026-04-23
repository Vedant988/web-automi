"""
database.py
-----------
SQLite-backed persistence for users, tasks and their execution steps.
"""

import os
import sqlite3
import uuid
import hashlib
import secrets
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
    """Create tables and apply migrations if they don't exist."""
    with get_db() as conn:
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
        
        # Migration: Add user_id to tasks if it doesn't exist
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


# --- Auth & Users ---

def _hash_password(password: str) -> str:
    # Simple SHA-256 with salt for demonstration. In production, use bcrypt or argon2.
    salt = "web_automi_salt_"
    return hashlib.sha256((salt + password).encode('utf-8')).hexdigest()

def register_user(username: str, password: str) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    pwd_hash = _hash_password(password)
    try:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, pwd_hash, now)
            )
            user_id = cursor.lastrowid
        return {"id": user_id, "username": username}
    except sqlite3.IntegrityError:
        return None # Username exists

def login_user(username: str, password: str) -> str | None:
    pwd_hash = _hash_password(password)
    with get_db() as conn:
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

def get_user_by_token(token: str) -> dict | None:
    if not token:
        return None
    with get_db() as conn:
        row = conn.execute("""
            SELECT u.id, u.username 
            FROM users u
            JOIN sessions s ON u.id = s.user_id
            WHERE s.token = ?
        """, (token,)).fetchone()
    return dict(row) if row else None

def logout_user(token: str):
    if token:
        with get_db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


# --- Tasks ---

def create_task(prompt: str, user_id: int | None = None, model: str = "openai/gpt-oss-120b") -> dict:
    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (id, user_id, prompt, status, model, created_at, updated_at) VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (task_id, user_id, prompt, model, now, now),
        )
    return {"id": task_id, "user_id": user_id, "prompt": prompt, "status": "running", "model": model, "result": None, "created_at": now, "updated_at": now}


def list_tasks(user_id: int | None = None) -> list[dict]:
    with get_db() as conn:
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


def get_task(task_id: str, user_id: int | None = None) -> dict | None:
    with get_db() as conn:
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


def delete_task(task_id: str, user_id: int | None = None) -> bool:
    with get_db() as conn:
        if user_id is not None:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
        else:
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
