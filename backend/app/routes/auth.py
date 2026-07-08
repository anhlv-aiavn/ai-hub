"""Đăng nhập + thông tin user hiện tại."""

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, config
from app.db import users
from app.deps import current_user

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginIn):
    username = body.username.strip().lower()
    u = await users().find_one({"username": username})
    now = int(time.time())

    # Đang bị khóa tạm vì sai nhiều lần?
    if u and u.get("locked_until", 0) > now:
        wait = u["locked_until"] - now
        raise HTTPException(status_code=429, detail=f"Tạm khóa, thử lại sau {wait}s")

    # Tài khoản tồn tại nhưng bị admin khóa — báo rõ, không lẫn với sai mật khẩu.
    if u and not u.get("active", True):
        raise HTTPException(status_code=401, detail="Tài khoản này đang bị khóa, vui lòng liên hệ quản trị viên")

    ok = bool(u) and auth.verify_password(body.password, u.get("password", ""))
    if not ok:
        if u:  # đếm số lần sai, khóa khi vượt ngưỡng
            fails = int(u.get("login_fails", 0)) + 1
            upd = {"login_fails": fails}
            if fails >= config.LOGIN_MAX_FAILS:
                upd["locked_until"] = now + config.LOGIN_LOCK_SECONDS
                upd["login_fails"] = 0
            await users().update_one({"username": username}, {"$set": upd})
        raise HTTPException(status_code=401, detail="Sai tài khoản hoặc mật khẩu")

    if u.get("login_fails") or u.get("locked_until"):  # reset khi đăng nhập đúng
        await users().update_one({"username": username},
                                 {"$set": {"login_fails": 0, "locked_until": 0}})
    token = auth.make_token({"sub": u["username"], "role": u.get("role", "user"),
                             "branch": u.get("branch")})
    return {"token": token, "user": _public(u)}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    u = await users().find_one({"username": user["username"]})
    if not u or not u.get("active", True):
        raise HTTPException(status_code=401, detail="Tài khoản không còn hiệu lực")
    return {"user": _public(u)}


def _public(u: dict) -> dict:
    return {"username": u["username"], "role": u.get("role", "user"),
            "branch": u.get("branch"), "active": u.get("active", True)}
