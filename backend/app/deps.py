"""Phụ thuộc auth: xác thực JWT → user hiện tại; phân quyền admin; khóa phạm vi
chi nhánh. Token qua header Authorization: Bearer <jwt> HOẶC query ?token= (ảnh
trang & SSE không gắn header được). require_key (API key cũ) giữ cho tương thích."""

from fastapi import Depends, Header, HTTPException, Query

from app import auth, config


def require_key(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
) -> None:
    if not config.API_KEY:
        return
    if (x_api_key or api_key) != config.API_KEY:
        raise HTTPException(status_code=401, detail="API key không hợp lệ")


def _token_from(authorization: str | None, token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return token


async def current_user(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> dict:
    raw = _token_from(authorization, token)
    payload = auth.decode_token(raw) if raw else None
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Cần đăng nhập")
    return {
        "username": payload["sub"],
        "role": payload.get("role", "user"),
        "branch": payload.get("branch"),
    }


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ admin")
    return user


def is_admin(user: dict) -> bool:
    return user.get("role") == "admin"


# RBAC 3-role: admin (config/secret/users) > operator (số hóa: upload/import
# MinIO, retry lỗi, khóa chi nhánh, không thấy secret) > viewer (tra cứu/xem +
# HẬU KIỂM — khóa/sửa/duyệt GCN, khóa chi nhánh). Cấp bậc để require_operator/
# require_viewer chấp nhận cả role cao hơn.
ROLES = ("viewer", "operator", "admin")


def _rank(role: str | None) -> int:
    try:
        return ROLES.index(role or "viewer")
    except ValueError:
        return -1


def require_operator(user: dict = Depends(current_user)) -> dict:
    if _rank(user.get("role")) < ROLES.index("operator"):
        raise HTTPException(status_code=403, detail="Cần quyền operator trở lên")
    return user


def require_viewer(user: dict = Depends(current_user)) -> dict:
    if _rank(user.get("role")) < ROLES.index("viewer"):
        raise HTTPException(status_code=403, detail="Cần đăng nhập")
    return user


def scoped_branch(user: dict, requested: str | None) -> str | None:
    """Branch dùng để lọc dữ liệu. Admin: theo `requested` (None = tất cả).
    User thường: LUÔN ép về chi nhánh của họ (bỏ qua giá trị client gửi)."""
    if is_admin(user):
        return requested or None
    return user.get("branch")


def ensure_branch_access(user: dict, branch: str | None) -> None:
    """Chặn user thường truy cập dữ liệu KHÁC chi nhánh của họ (403)."""
    if is_admin(user):
        return
    if branch != user.get("branch"):
        raise HTTPException(status_code=403, detail="Không thuộc chi nhánh của bạn")
