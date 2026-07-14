"""Phụ thuộc auth: xác thực JWT → user hiện tại; phân quyền admin; khóa phạm vi
chi nhánh. Token qua header Authorization: Bearer <jwt> HOẶC query ?token= (ảnh
trang & SSE không gắn header được). require_key (API key cũ) giữ cho tương thích."""

from fastapi import Depends, Header, HTTPException, Query

from app import auth, config
from app.db import users


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
    # Token tự ký là stateless (không có nơi thu hồi) — so `sid` trong token với
    # `session_id` hiện tại trong DB để có thể vô hiệu hóa: tài khoản bị khóa,
    # bị admin buộc đăng xuất, hoặc phiên đã bị thay bởi một lượt đăng nhập khác.
    u = await users().find_one(
        {"username": payload["sub"]},
        {"session_id": 1, "active": 1, "assigned_batch_ids": 1},
    )
    if not u or not u.get("active", True):
        raise HTTPException(status_code=401, detail="Tài khoản không còn hiệu lực")
    # Cờ AIHUB_SINGLE_SESSION_ENABLED tắt (mặc định) → bỏ qua so sánh sid, cho
    # phép nhiều phiên/token hợp lệ song song thay vì phiên sau âm thầm vô hiệu
    # hóa phiên trước (xem routes/auth.py:login, cùng cờ).
    if config.SINGLE_SESSION_ENABLED and u.get("session_id") != payload.get("sid"):
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã kết thúc (đăng nhập nơi khác hoặc bị buộc đăng xuất)")
    return {
        "username": payload["sub"],
        "role": payload.get("role", "user"),
        "branch": payload.get("branch"),
        # Đọc tươi từ DB mỗi request (không nhúng vào JWT) để admin đổi gán lô
        # cho user có hiệu lực ngay, không cần chờ họ đăng nhập lại.
        "assigned_batch_ids": u.get("assigned_batch_ids") or [],
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
    User thường: LUÔN ép về chi nhánh của họ (bỏ qua giá trị client gửi).

    Không còn dùng trong luồng nghiệp vụ hiện tại (đã chuyển sang phân quyền
    theo lô, xem `scoped_batch_ids`) — giữ lại vì field `branch` cũ vẫn còn
    trong dữ liệu, không migrate/xóa."""
    if is_admin(user):
        return requested or None
    return user.get("branch")


def ensure_branch_access(user: dict, branch: str | None) -> None:
    """Chặn user thường truy cập dữ liệu KHÁC chi nhánh của họ (403). Không còn
    dùng trong luồng nghiệp vụ hiện tại — xem `ensure_batch_access`."""
    if is_admin(user):
        return
    if branch != user.get("branch"):
        raise HTTPException(status_code=403, detail="Không thuộc chi nhánh của bạn")


def scoped_batch_ids(user: dict) -> list[str] | None:
    """Danh sách lô user được phép thấy. None = admin (không giới hạn)."""
    if is_admin(user):
        return None
    return user.get("assigned_batch_ids") or []


def ensure_batch_access(user: dict, batch_id: str | None) -> None:
    """Chặn user thường truy cập lô KHÔNG nằm trong danh sách được gán (403)."""
    if is_admin(user):
        return
    if not batch_id or batch_id not in (user.get("assigned_batch_ids") or []):
        raise HTTPException(status_code=403, detail="Không có quyền truy cập lô này")
