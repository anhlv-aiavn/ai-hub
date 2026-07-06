"""Đọc audit log cấu hình — admin-only, phân trang cursor (không skip/limit sâu)."""

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, Query

from app.db import audit_log
from app.deps import require_admin

router = APIRouter(prefix="/v1/audit-log", tags=["audit-log"], dependencies=[Depends(require_admin)])


def _utc(dt: datetime | None) -> datetime | None:
    """pymongo trả datetime NAIVE khi đọc lại từ Mongo (BSON không giữ tzinfo) dù
    lúc ghi là datetime.now(timezone.utc) → gắn lại UTC để JSON ISO có offset,
    không thì JS `new Date()` hiểu nhầm thành giờ địa phương (lệch UTC+7 ở VN)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _public(r: dict) -> dict:
    return {
        "id": str(r["_id"]), "at": _utc(r.get("at")), "actor": r.get("actor"),
        "action": r.get("action"), "target": r.get("target"), "detail": r.get("detail"),
    }


@router.get("")
async def list_audit_log(
    action: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    limit: int = 50,
    before_id: str | None = None,
):
    flt: dict = {}
    if action:
        flt["action"] = action
    if actor:
        flt["actor"] = actor
    if target:
        flt["target"] = target
    if date_from or date_to:
        at: dict = {}
        if date_from:
            at["$gte"] = date_from
        if date_to:
            at["$lte"] = date_to
        flt["at"] = at
    if before_id:
        flt["_id"] = {"$lt": ObjectId(before_id)}

    limit = max(1, min(limit, 200))
    rows = await audit_log().find(flt).sort("_id", -1).limit(limit).to_list(length=limit)
    next_cursor = str(rows[-1]["_id"]) if len(rows) == limit else None
    return {"items": [_public(r) for r in rows], "next_cursor": next_cursor}
