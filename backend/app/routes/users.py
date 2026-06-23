"""Quản trị tài khoản — chỉ admin. Tạo/sửa/khóa/đổi mật khẩu user, gán chi nhánh."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth
from app.branches import is_valid_branch
from app.db import users
from app.deps import require_admin

router = APIRouter(prefix="/v1/users", tags=["users"], dependencies=[Depends(require_admin)])


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "user"            # user | admin
    branch: str | None = None     # bắt buộc nếu role=user


class UserPatch(BaseModel):
    password: str | None = None
    role: str | None = None
    branch: str | None = None
    active: bool | None = None


def _public(u: dict) -> dict:
    return {"username": u["username"], "role": u.get("role", "user"),
            "branch": u.get("branch"), "active": u.get("active", True),
            "created_at": u.get("created_at")}


def _validate(role: str, branch: str | None) -> None:
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Vai trò không hợp lệ")
    if role == "user" and not is_valid_branch(branch):
        raise HTTPException(status_code=400, detail="User thường phải gán chi nhánh hợp lệ")


@router.get("")
async def list_users():
    rows = await users().find({}, {"password": 0}).sort("username", 1).to_list(length=1000)
    return {"users": [_public(u) for u in rows]}


@router.post("")
async def create_user(body: UserIn):
    username = body.username.strip().lower()
    if not username or len(body.password) < 4:
        raise HTTPException(status_code=400, detail="Tên đăng nhập/mật khẩu không hợp lệ")
    _validate(body.role, body.branch)
    if await users().find_one({"username": username}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="Tài khoản đã tồn tại")
    doc = {
        "username": username,
        "password": auth.hash_password(body.password),
        "role": body.role,
        "branch": body.branch if body.role == "user" else None,
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
    role = body.role if body.role is not None else u.get("role", "user")
    branch = body.branch if body.branch is not None else u.get("branch")
    if body.role is not None or body.branch is not None:
        _validate(role, branch)
    upd: dict = {}
    if body.password is not None:
        if len(body.password) < 4:
            raise HTTPException(status_code=400, detail="Mật khẩu quá ngắn")
        upd["password"] = auth.hash_password(body.password)
    if body.role is not None:
        upd["role"] = role
        upd["branch"] = branch if role == "user" else None
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
    res = await users().delete_one({"username": username})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    return {"ok": True}
