"""
web_automi/api/websocket.py
---------------------------
FastAPI WebSocket Router for real-time agent progression execution.
"""

import asyncio
import queue as queue_module
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web_automi.core.database import db
from web_automi.core.runner import runner

router = APIRouter(prefix="/ws", tags=["websocket"])


@router.websocket("/run")
async def ws_run(websocket: WebSocket):
    await websocket.accept()
    try:
        # Check authentication cookie
        token = websocket.cookies.get("session_id")
        user = db.get_user_by_token(token) if token else None
        if not user:
            await websocket.send_json({
                "type": "error",
                "data": {"error": "Not authenticated. Please log in."}
            })
            await websocket.close()
            return

        # Receive task specifications
        data = await websocket.receive_json()
        prompt = (data.get("prompt") or "").strip()
        model = data.get("model", "openai/gpt-oss-20b")

        if not prompt:
            await websocket.send_json({
                "type": "error",
                "data": {"error": "Empty prompt"}
            })
            return
        if runner.is_running:
            await websocket.send_json({
                "type": "error",
                "data": {"error": "Agent is already running a task. Please wait."}
            })
            return

        # Create task and launch thread
        task = db.create_task(prompt, user_id=user["id"], model=model)
        task_id = task["id"]
        runner.start(prompt, model=model)
        await websocket.send_json({"type": "started", "data": {"task_id": task_id}})

        while True:
            try:
                step = runner.event_queue.get_nowait()
                try:
                    db.add_step(
                        task_id,
                        step.step_number,
                        step.type,
                        step.status,
                        step.title,
                        step.detail,
                        step.browser_url
                    )
                except Exception:
                    pass

                await websocket.send_json({"type": "step", "data": step.to_dict()})

                if step.type == "done":
                    db.update_task(task_id, "completed", runner.result)
                    await websocket.send_json({
                        "type": "result",
                        "data": {"task_id": task_id, "result": runner.result}
                    })
                    break
                elif step.type == "error" and step.status == "failed":
                    db.update_task(task_id, "failed", runner.result)
                    await websocket.send_json({
                        "type": "result",
                        "data": {"task_id": task_id, "result": runner.result, "error": True}
                    })
                    break
            except queue_module.Empty:
                if not runner.is_running:
                    if runner.result:
                        db.update_task(task_id, runner.status, runner.result)
                        await websocket.send_json({
                            "type": "result",
                            "data": {"task_id": task_id, "result": runner.result}
                        })
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
