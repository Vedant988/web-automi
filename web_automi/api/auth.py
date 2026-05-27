"""
web_automi/api/auth.py
----------------------
FastAPI Router for authentication endpoints.
"""

from fastapi import APIRouter, HTTPException, Response, Request, Depends, status
from pydantic import BaseModel

from web_automi.core.database import db

router = APIRouter(prefix="/api", tags=["authentication"])


class AuthRequest(BaseModel):
    username: str
    password: str


def get_current_user(request: Request):
    token = request.cookies.get("session_id")
    user = db.get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    return user


@router.post("/register")
def api_register(req: AuthRequest, response: Response):
    user = db.register_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=400, detail="Username already exists")
    token = db.login_user(req.username, req.password)
    response.set_cookie(
        key="session_id",
        value=token,
        httponly=True,
        max_age=86400 * 30,
        path="/"
    )
    return {"ok": True, "user": user}


@router.post("/login")
def api_login(req: AuthRequest, response: Response):
    token = db.login_user(req.username, req.password)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    response.set_cookie(
        key="session_id",
        value=token,
        httponly=True,
        max_age=86400 * 30,
        path="/"
    )
    user = db.get_user_by_token(token)
    return {"ok": True, "user": user}


@router.post("/logout")
def api_logout(request: Request, response: Response):
    token = request.cookies.get("session_id")
    if token:
        db.logout_user(token)
    response.delete_cookie("session_id", path="/")
    return {"ok": True}


@router.get("/me")
def api_me(user: dict = Depends(get_current_user)):
    return {"ok": True, "user": user}
