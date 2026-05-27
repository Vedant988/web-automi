"""
server.py
---------
Backwards compatibility wrapper delegating to the new modular FastAPI app.
"""

from web_automi.app import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
