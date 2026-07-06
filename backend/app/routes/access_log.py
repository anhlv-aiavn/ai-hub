"""Đọc access log (xem/tải/xuất bản gốc) — admin-only, phân trang cursor."""

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends

from app.db import access_log
from app.deps import require_admin

router = APIRouter(prefix="/v1/access-log", tags=["access-log"], dependencies=[Depends(require_admin)])


def _utc(dt: datetime | None) -> datetime | None:
    """Xem app/routes/audit.py::_utc — pymongo trả naive datetime, gắn lại UTC
    để JSON ISO có offset (không thì JS hiểu nhầm giờ địa phương)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _public(r: dict) -> dict:
    return {
        "id": str(r["_id"]), "at": _utc(r.get("at")), "actor": r.get("actor"),
        "gcn_id": r.get("gcn_id"), "action": r.get("action"), "detail": r.get("detail"),
    }


@router.get("")
async def list_access_log(
    actor: str | None = None,
    gcn_id: str | None = None,
    action: str | None = None,
    limit: int = 50,
    before_id: str | None = None,
):
    flt: dict = {}
    if actor:
        flt["actor"] = actor
    if gcn_id:
        flt["gcn_id"] = gcn_id
    if action:
        flt["action"] = action
    if before_id:
        flt["_id"] = {"$lt": ObjectId(before_id)}

    limit = max(1, min(limit, 200))
    rows = await access_log().find(flt).sort("_id", -1).limit(limit).to_list(length=limit)
    next_cursor = str(rows[-1]["_id"]) if len(rows) == limit else None
    return {"items": [_public(r) for r in rows], "next_cursor": next_cursor}
