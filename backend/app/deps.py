"""Phụ thuộc dùng chung: kiểm tra API key (tùy chọn). Nếu AIHUB_API_KEY rỗng → mở.
Ảnh trang & SSE không gửi header được → chấp nhận api_key qua query."""

from fastapi import Header, HTTPException, Query

from app import config


def require_key(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
) -> None:
    if not config.API_KEY:
        return
    if (x_api_key or api_key) != config.API_KEY:
        raise HTTPException(status_code=401, detail="API key không hợp lệ")
