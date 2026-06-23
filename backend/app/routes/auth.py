"""Đăng nhập + thông tin user hiện tại."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth
from app.db import users
from app.deps import current_user

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginIn):
    u = await users().find_one({"username": body.username.strip().lower()})
    if not u or not u.get("active", True) or not auth.verify_password(body.password, u.get("password", "")):
        raise HTTPException(status_code=401, detail="Sai tài khoản hoặc mật khẩu")
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
