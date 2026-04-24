"""
server.py — FastAPI backend with WebSocket for real-time agent streaming.
"""
import os, sys, json, asyncio
import queue as queue_module
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Request, Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from database import (
    init_db, create_task, list_tasks, get_task, update_task, delete_task, add_step, get_steps,
    register_user, login_user, logout_user, get_user_by_token
)
from agent_runner import AgentRunner

runner = AgentRunner()

global_playwright = None
global_browser = None

@asynccontextmanager
async def lifespan(app):
    init_db()
    print("[server] Database initialized — http://localhost:8000")
    
    # Launch persistent browser for context reuse
    try:
        from playwright.async_api import async_playwright
        global global_playwright, global_browser
        global_playwright = await async_playwright().start()
        global_browser = await global_playwright.chromium.launch(
            headless=True,
            args=[
                "--remote-debugging-port=9222",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        print("[server] Global Chromium launched on port 9222 for context reuse")
    except Exception as e:
        print(f"[server] Warning: Could not launch global Chromium: {e}")
        
    yield

    if global_browser:
        await global_browser.close()
    if global_playwright:
        await global_playwright.stop()

app = FastAPI(title="Web-Automi", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Auth Dependency ─────────────────────────────────────────────
def get_current_user(request: Request):
    token = request.cookies.get("session_id")
    user = get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


# ── Auth Endpoints ──────────────────────────────────────────────
class AuthRequest(BaseModel):
    username: str
    password: str

@app.post("/api/register")
def api_register(req: AuthRequest, response: Response):
    user = register_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=400, detail="Username already exists")
    token = login_user(req.username, req.password)
    response.set_cookie(key="session_id", value=token, httponly=True, max_age=86400 * 30, path="/") # 30 days
    return {"ok": True, "user": user}

@app.post("/api/login")
def api_login(req: AuthRequest, response: Response):
    token = login_user(req.username, req.password)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    response.set_cookie(key="session_id", value=token, httponly=True, max_age=86400 * 30, path="/")
    user = get_user_by_token(token)
    return {"ok": True, "user": user}

@app.post("/api/logout")
def api_logout(request: Request, response: Response):
    token = request.cookies.get("session_id")
    if token:
        logout_user(token)
    response.delete_cookie("session_id", path="/")
    return {"ok": True}

@app.get("/api/me")
def api_me(user: dict = Depends(get_current_user)):
    return {"ok": True, "user": user}


# ── REST endpoints ──────────────────────────────────────────────
class RunTaskRequest(BaseModel):
    prompt: str
    model: str = "openai/gpt-oss-120b"

@app.get("/api/tasks")
def api_list_tasks(user: dict = Depends(get_current_user)):
    return list_tasks(user_id=user["id"])

@app.get("/api/tasks/{task_id}")
def api_get_task(task_id: str, user: dict = Depends(get_current_user)):
    t = get_task(task_id, user_id=user["id"])
    if not t: raise HTTPException(404, "Not found")
    return {**t, "steps": get_steps(task_id)}

@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: str, user: dict = Depends(get_current_user)):
    if not delete_task(task_id, user_id=user["id"]): raise HTTPException(404, "Not found")
    return {"ok": True}

@app.post("/api/stop")
def api_stop(user: dict = Depends(get_current_user)):
    if not runner.is_running: raise HTTPException(409, "Not running")
    runner.stop()
    return {"ok": True}

@app.get("/api/status")
def api_status(user: dict = Depends(get_current_user)):
    return {"status": runner.status, "steps": len(runner.steps), "result": runner.result}

# ── WebSocket endpoint ──────────────────────────────────────────
@app.websocket("/ws/run")
async def ws_run(websocket: WebSocket):
    await websocket.accept()
    try:
        # Auth check
        token = websocket.cookies.get("session_id")
        user = get_user_by_token(token) if token else None
        if not user:
            await websocket.send_json({"type": "error", "data": {"error": "Not authenticated. Please log in."}})
            await websocket.close()
            return

        data = await websocket.receive_json()
        prompt = (data.get("prompt") or "").strip()
        model = data.get("model", "openai/gpt-oss-120b")

        if not prompt:
            await websocket.send_json({"type": "error", "data": {"error": "Empty prompt"}})
            return
        if runner.is_running:
            await websocket.send_json({"type": "error", "data": {"error": "Agent is already running a task. Please wait."}})
            return

        task = create_task(prompt, user_id=user["id"], model=model)
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

@app.get("/auth")
async def serve_auth():
    return FileResponse("static/auth.html")

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
