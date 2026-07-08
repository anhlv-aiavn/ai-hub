"""Quản trị tài khoản — chỉ admin. Tạo/sửa/khóa/đổi mật khẩu user, gán chi nhánh."""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, config
from app.branches import is_valid_branch
from app.db import users
from app.deps import ROLES, require_admin

router = APIRouter(prefix="/v1/users", tags=["users"], dependencies=[Depends(require_admin)])


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "viewer"           # admin | operator | viewer
    branch: str | None = None      # bắt buộc nếu role=operator|viewer


class UserPatch(BaseModel):
    password: str | None = None
    role: str | None = None
    branch: str | None = None
    active: bool | None = None


def _is_online(u: dict, now: int | None = None) -> bool:
    now = now if now is not None else int(time.time())
    last_seen = u.get("last_seen_at") or 0
    return bool(u.get("session_id")) and (now - last_seen) < config.SESSION_ACTIVE_TTL


def _public(u: dict) -> dict:
    return {"username": u["username"], "role": u.get("role", "viewer"),
            "branch": u.get("branch"), "active": u.get("active", True),
            "created_at": u.get("created_at"), "online": _is_online(u)}


async def _validate(role: str, branch: str | None) -> None:
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="Vai trò không hợp lệ")
    if role in ("operator", "viewer") and not await is_valid_branch(branch):
        raise HTTPException(status_code=400, detail="Operator/viewer phải gán chi nhánh hợp lệ")


@router.get("")
async def list_users():
    rows = await users().find({}, {"password": 0}).sort("username", 1).to_list(length=1000)
    return {"users": [_public(u) for u in rows]}


@router.post("")
async def create_user(body: UserIn):
    username = body.username.strip().lower()
    if not username or len(body.password) < 4:
        raise HTTPException(status_code=400, detail="Tên đăng nhập/mật khẩu không hợp lệ")
    await _validate(body.role, body.branch)
    if await users().find_one({"username": username}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="Tài khoản đã tồn tại")
    doc = {
        "username": username,
        "password": auth.hash_password(body.password),
        "role": body.role,
        "branch": body.branch if body.role != "admin" else None,
        "active": True,
        "created_at": datetime.now(timezone.utc),
    }
    await users().insert_one(doc)
    return {"ok": True, "user": _public(doc)}


@router.patch("/{username}")
async def update_user(username: str, body: UserPatch, admin: dict = Depends(require_admin)):
    username = username.strip().lower()
    u = await users().find_one({"username": username})
    if not u:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    role = body.role if body.role is not None else u.get("role", "viewer")
    branch = body.branch if body.branch is not None else u.get("branch")
    if body.role is not None or body.branch is not None:
        await _validate(role, branch)
    # Đang có phiên hoạt động → chặn đổi mật khẩu/khóa NGƯỜI KHÁC, tránh xung đột
    # với người đang thao tác; admin phải "Buộc đăng xuất" trước
    # (POST .../force-logout). KHÔNG áp dụng cho chính admin đang gọi API này —
    # họ luôn "đang hoạt động" khi dùng bảng này nên sẽ không bao giờ tự đổi
    # được mật khẩu của mình nếu áp luật này lên cả bản thân.
    is_self = username == admin["username"]
    if not is_self and (body.password is not None or body.active is False) and _is_online(u):
        raise HTTPException(status_code=409, detail="Tài khoản đang hoạt động — hãy buộc đăng xuất trước")
    upd: dict = {}
    if body.password is not None:
        if len(body.password) < 4:
            raise HTTPException(status_code=400, detail="Mật khẩu quá ngắn")
        upd["password"] = auth.hash_password(body.password)
    if body.role is not None:
        upd["role"] = role
        upd["branch"] = branch if role != "admin" else None
    elif body.branch is not None:
        upd["branch"] = branch
    if body.active is not None:
        # Không cho tự khóa chính mình (tránh mất quyền)
        if username == admin["username"] and not body.active:
            raise HTTPException(status_code=400, detail="Không thể tự khóa tài khoản của bạn")
        upd["active"] = body.active
    if upd:
        await users().update_one({"username": username}, {"$set": upd})
    u = await users().find_one({"username": username})
    return {"ok": True, "user": _public(u)}


@router.delete("/{username}")
async def delete_user(username: str, admin: dict = Depends(require_admin)):
    username = username.strip().lower()
    if username == admin["username"]:
        raise HTTPException(status_code=400, detail="Không thể xóa tài khoản của bạn")
    u = await users().find_one({"username": username})
    if not u:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    if _is_online(u):
        raise HTTPException(status_code=409, detail="Tài khoản đang hoạt động — hãy buộc đăng xuất trước")
    await users().delete_one({"username": username})
    return {"ok": True}


@router.post("/{username}/force-logout")
async def force_logout(username: str, admin: dict = Depends(require_admin)):
    """Vô hiệu hóa phiên hiện tại ngay lập tức (token cũ mất giá trị ở request kế
    tiếp, xem app.deps.current_user) — dùng khi user đã thoát web nhưng chưa
    đăng xuất, để admin có thể khóa/xóa tài khoản mà không cần chờ hết TTL."""
    username = username.strip().lower()
    res = await users().update_one({"username": username},
                                   {"$set": {"session_id": None, "last_seen_at": 0}})
    if not res.matched_count:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    return {"ok": True}
