"""
database.py
-----------
Backwards compatibility wrapper delegating to the new modular database service.
"""

from web_automi.core.database import db

def init_db():
    return db.init_db()

def register_user(username: str, password: str):
    return db.register_user(username, password)

def login_user(username: str, password: str):
    return db.login_user(username, password)

def get_user_by_token(token: str):
    return db.get_user_by_token(token)

def logout_user(token: str):
    return db.logout_user(token)

def create_task(prompt: str, user_id = None, model = "openai/gpt-oss-120b"):
    return db.create_task(prompt, user_id, model)

def list_tasks(user_id = None):
    return db.list_tasks(user_id)

def get_task(task_id: str, user_id = None):
    return db.get_task(task_id, user_id)

def update_task(task_id: str, status: str, result = None):
    return db.update_task(task_id, status, result)

def delete_task(task_id: str, user_id = None):
    return db.delete_task(task_id, user_id)

def add_step(task_id: str, step_number: int, step_type: str, status: str, title: str, detail = "", browser_url = ""):
    return db.add_step(task_id, step_number, step_type, status, title, detail, browser_url)

def get_steps(task_id: str):
    return db.get_steps(task_id)
