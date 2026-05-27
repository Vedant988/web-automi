"""
web_automi/api/tasks.py
-----------------------
FastAPI Router for task management and status endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from web_automi.core.database import db
from web_automi.core.runner import runner
from web_automi.api.auth import get_current_user

router = APIRouter(prefix="/api", tags=["tasks"])


class RunTaskRequest(BaseModel):
    prompt: str
    model: str = "openai/gpt-oss-20b"


@router.get("/tasks")
def api_list_tasks(user: dict = Depends(get_current_user)):
    return db.list_tasks(user_id=user["id"])


@router.get("/tasks/{task_id}")
def api_get_task(task_id: str, user: dict = Depends(get_current_user)):
    t = db.get_task(task_id, user_id=user["id"])
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return {**t, "steps": db.get_steps(task_id)}


@router.delete("/tasks/{task_id}")
def api_delete_task(task_id: str, user: dict = Depends(get_current_user)):
    if not db.delete_task(task_id, user_id=user["id"]):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


@router.post("/stop")
def api_stop(user: dict = Depends(get_current_user)):
    if not runner.is_running:
        raise HTTPException(status_code=409, detail="Agent is not running")
    runner.stop()
    return {"ok": True}


@router.get("/status")
def api_status(user: dict = Depends(get_current_user)):
    return {
        "status": runner.status,
        "steps": len(runner.steps),
        "result": runner.result
    }
