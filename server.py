"""
server.py — FastAPI backend with WebSocket for real-time agent streaming.
"""
import os, sys, json, asyncio
import queue as queue_module
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from database import init_db, create_task, list_tasks, get_task, update_task, delete_task, add_step, get_steps
from agent_runner import AgentRunner

runner = AgentRunner()

@asynccontextmanager
async def lifespan(app):
    init_db()
    print("[server] Database initialized — http://localhost:8000")
    yield

app = FastAPI(title="Web-Automi", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── REST endpoints ──────────────────────────────────────────────
class RunTaskRequest(BaseModel):
    prompt: str
    model: str = "openai/gpt-oss-120b"

@app.get("/api/tasks")
def api_list_tasks():
    return list_tasks()

@app.get("/api/tasks/{task_id}")
def api_get_task(task_id: str):
    t = get_task(task_id)
    if not t: raise HTTPException(404, "Not found")
    return {**t, "steps": get_steps(task_id)}

@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: str):
    if not delete_task(task_id): raise HTTPException(404, "Not found")
    return {"ok": True}

@app.post("/api/stop")
def api_stop():
    if not runner.is_running: raise HTTPException(409, "Not running")
    runner.stop()
    return {"ok": True}

@app.get("/api/status")
def api_status():
    return {"status": runner.status, "steps": len(runner.steps), "result": runner.result}

# ── WebSocket endpoint ──────────────────────────────────────────
@app.websocket("/ws/run")
async def ws_run(websocket: WebSocket):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        prompt = (data.get("prompt") or "").strip()
        model = data.get("model", "openai/gpt-oss-120b")

        if not prompt:
            await websocket.send_json({"type": "error", "data": {"error": "Empty prompt"}})
            return
        if runner.is_running:
            await websocket.send_json({"type": "error", "data": {"error": "Agent is already running a task. Please wait."}})
            return

        task = create_task(prompt, model)
        task_id = task["id"]
        runner.start(prompt, model=model)
        await websocket.send_json({"type": "started", "data": {"task_id": task_id}})

        while True:
            try:
                step = runner.event_queue.get_nowait()
                try:
                    add_step(task_id, step.step_number, step.type, step.status, step.title, step.detail, step.browser_url)
                except Exception:
                    pass

                await websocket.send_json({"type": "step", "data": step.to_dict()})

                if step.type == "done":
                    update_task(task_id, "completed", runner.result)
                    await websocket.send_json({"type": "result", "data": {"task_id": task_id, "result": runner.result}})
                    break
                elif step.type == "error" and step.status == "failed":
                    update_task(task_id, "failed", runner.result)
                    await websocket.send_json({"type": "result", "data": {"task_id": task_id, "result": runner.result, "error": True}})
                    break
            except queue_module.Empty:
                if not runner.is_running:
                    # Agent finished but we might have missed the final event
                    if runner.result:
                        update_task(task_id, runner.status, runner.result)
                        await websocket.send_json({"type": "result", "data": {"task_id": task_id, "result": runner.result}})
                    break
                await asyncio.sleep(0.25)

        await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "data": {"error": str(exc)}})
        except Exception:
            pass

# ── Static files ────────────────────────────────────────────────
@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
