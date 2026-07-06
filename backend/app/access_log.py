"""Audit truy cập dữ liệu nhạy cảm (xem/tải/xuất bản gốc) — tách khỏi `audit_log`
(đó là log CẤU HÌNH) để không loãng. Đất đai + chủ sử dụng = dữ liệu cá nhân."""

from datetime import datetime, timezone

from app.db import access_log


async def log_access(actor: str, gcn_id: str | None, action: str, detail: dict | None = None) -> None:
    """action ∈ {"view", "download", "export"}."""
    await access_log().insert_one({
        "at": datetime.now(timezone.utc),
        "actor": actor,
        "gcn_id": gcn_id,
        "action": action,
        "detail": detail or {},
    })
