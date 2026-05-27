"""
web_automi/app.py
-----------------
Main modular FastAPI app initialization connecting auth, task and websocket routers.
"""

import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

import web_automi.core.config as config
from web_automi.core.database import db
from web_automi.api.auth import router as auth_router
from web_automi.api.tasks import router as tasks_router
from web_automi.api.websocket import router as ws_router

def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

global_playwright = None
global_browser = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB persistence
    db.init_db()
    print(f"[server] Database initialized — WAL mode active on DB path: {config.DB_PATH}")

    reuse_browser = _env_flag("REUSE_BROWSER", default=True)
    headless = _env_flag("HEADLESS", default=False)

    if reuse_browser:
        try:
            from playwright.async_api import async_playwright
            global global_playwright, global_browser
            global_playwright = await async_playwright().start()
            global_browser = await global_playwright.chromium.launch(
                headless=headless,
                args=[
                    "--remote-debugging-port=9222",
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    "--disable-popup-blocking",
                    "--window-size=1280,720",
                    "--disable-extensions",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-gpu-sandbox",
                    "--disable-setuid-sandbox",
                    "--js-flags=--max-old-space-size=256",
                    "--disable-background-networking",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-breakpad",
                    "--disable-client-side-phishing-detection",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-domain-reliability",
                    "--disable-features=AudioServiceOutOfProcess,IsolateOrigins,site-per-process",
                    "--disable-ipc-flooding-protection",
                    "--disable-print-preview",
                    "--disable-prompt-on-repost",
                    "--disable-renderer-backgrounding",
                    "--disable-sync",
                    "--mute-audio",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--metrics-recording-only"
                ]
            )
            print(f"[server] Global Chromium launched on port 9222 (headless={headless})")
        except Exception as e:
            print(f"[server] Warning: Could not launch global Chromium: {e}")
    else:
        print("[server] Global browser reuse disabled; Chromium will launch on demand")

    yield

    if global_browser:
        await global_browser.close()
    if global_playwright:
        await global_playwright.stop()


app = FastAPI(title="Web-Automi", version="2.0.0", lifespan=lifespan)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# Connect modular routers
app.include_router(auth_router)
app.include_router(tasks_router)
app.include_router(ws_router)


# --- Serve Static UI Files ---
@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")

@app.get("/auth")
async def serve_auth():
    return FileResponse("static/auth.html")

# Mount static folder
app.mount("/static", StaticFiles(directory="static"), name="static")


def start_server():
    import uvicorn
    uvicorn.run("web_automi.app:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    start_server()
