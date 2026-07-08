"""Đăng nhập + thông tin user hiện tại."""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, config
from app.db import users
from app.deps import current_user

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


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

    # 1 tài khoản chỉ 1 phiên đang hoạt động: phiên cũ còn "sống" (heartbeat gần
    # đây, xem /heartbeat + /session-end) → từ chối, không đá phiên cũ.
    last_seen = u.get("last_seen_at") or 0
    if u.get("session_id") and (now - last_seen) < config.SESSION_ACTIVE_TTL:
        raise HTTPException(status_code=409, detail="Tài khoản đang được sử dụng ở nơi khác")

    sid = uuid.uuid4().hex
    token = auth.make_token({"sub": u["username"], "role": u.get("role", "user"),
                             "branch": u.get("branch"), "sid": sid})
    await users().update_one({"username": username},
                             {"$set": {"session_id": sid, "last_seen_at": now}})
    return {"token": token, "user": _public(u)}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    u = await users().find_one({"username": user["username"]})
    if not u or not u.get("active", True):
        raise HTTPException(status_code=401, detail="Tài khoản không còn hiệu lực")
    return {"user": _public(u)}


@router.post("/logout")
async def logout(user: dict = Depends(current_user)):
    """Đăng xuất chủ động (bấm nút, không phải đóng tab) — giải phóng session_id
    NGAY để tài khoản đăng nhập lại được lập tức, không phải chờ hết
    SESSION_ACTIVE_TTL như khi chỉ đóng tab (xem /session-end)."""
    await users().update_one({"username": user["username"]},
                             {"$set": {"session_id": None, "last_seen_at": 0}})
    return {"ok": True}


@router.post("/change-password")
async def change_password(body: ChangePasswordIn, user: dict = Depends(current_user)):
    """Tự đổi mật khẩu của chính mình — dành cho MỌI vai trò (không chỉ admin,
    khác với PATCH /v1/users/{username} vốn chỉ admin gọi được để đổi cho
    NGƯỜI KHÁC). Yêu cầu đúng mật khẩu hiện tại để tránh bị lợi dụng nếu phiên
    đăng nhập bị chiếm (XSS/token rò rỉ) đổi mật khẩu âm thầm chiếm tài khoản."""
    u = await users().find_one({"username": user["username"]})
    if not u or not auth.verify_password(body.old_password, u.get("password", "")):
        raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng")
    if len(body.new_password) < 4:
        raise HTTPException(status_code=400, detail="Mật khẩu mới quá ngắn (tối thiểu 4 ký tự)")
    await users().update_one({"username": user["username"]},
                             {"$set": {"password": auth.hash_password(body.new_password)}})
    return {"ok": True}


@router.post("/heartbeat")
async def heartbeat(user: dict = Depends(current_user)):
    """FE gọi định kỳ khi tab đang mở — nuôi 'đang hoạt động' cho admin thấy và
    để giữ phiên (chống bị coi là hết hạn sau SESSION_ACTIVE_TTL)."""
    await users().update_one({"username": user["username"]},
                             {"$set": {"last_seen_at": int(time.time())}})
    return {"ok": True}


@router.post("/session-end")
async def session_end(user: dict = Depends(current_user)):
    """FE gọi bằng navigator.sendBeacon khi đóng/rời tab — đánh dấu ngay 'không
    hoạt động' cho admin, KHÔNG hủy session_id (mở lại tab vẫn dùng token cũ
    được, tránh bắt đăng nhập lại chỉ vì lỡ đóng tab)."""
    await users().update_one({"username": user["username"]}, {"$set": {"last_seen_at": 0}})
    return {"ok": True}


def _public(u: dict) -> dict:
    return {"username": u["username"], "role": u.get("role", "user"),
            "branch": u.get("branch"), "active": u.get("active", True)}
