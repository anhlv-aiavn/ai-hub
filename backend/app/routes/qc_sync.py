"""QC Sync — CRUD kênh đồng bộ (nguồn + đích + prefix + chu kỳ), kích hoạt quét
ngay, thống kê + theo dõi item. admin-only, cùng khuôn `s3_connections.py`.
Xử lý thật (liệt kê, QC, OCR, crop) chạy trong worker — xem
`app/worker/qc_pipeline.py`. Chi tiết luồng: `docs/algorithm.md §9`."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import config
from app.audit import AuditAction, log_action
from app.db import qc_items, qc_stats_daily, qc_sync_configs, qc_sync_jobs, s3_connections
from app.deps import require_admin

router = APIRouter(prefix="/v1/qc-sync", tags=["qc-sync"], dependencies=[Depends(require_admin)])


class ConfigIn(BaseModel):
    name: str
    source_connection_id: str
    prefix: str = ""
    dest_connection_id: str
    interval_seconds: int | None = None
    enabled: bool = True


class ConfigPatch(BaseModel):
    name: str | None = None
    source_connection_id: str | None = None
    prefix: str | None = None
    dest_connection_id: str | None = None
    interval_seconds: int | None = None
    enabled: bool | None = None


def _public_config(c: dict) -> dict:
    return {
        "id": c["_id"], "name": c.get("name"),
        "source_connection_id": c.get("source_connection_id"), "prefix": c.get("prefix") or "",
        "dest_connection_id": c.get("dest_connection_id"),
        "interval_seconds": c.get("interval_seconds") or config.QC_SYNC_DEFAULT_INTERVAL_SECONDS,
        "enabled": bool(c.get("enabled")),
        "last_run_at": c.get("last_run_at"), "last_run_status": c.get("last_run_status"),
        "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
        "created_by": c.get("created_by"),
    }


async def _validate_refs(source_id: str | None, dest_id: str | None) -> None:
    if source_id is not None:
        src = await s3_connections().find_one({"_id": source_id, "role": "source"})
        if not src:
            raise HTTPException(status_code=400, detail="source_connection_id không hợp lệ (không tìm thấy S3 nguồn)")
    if dest_id is not None:
        dest = await s3_connections().find_one({"_id": dest_id, "role": "destination"})
        if not dest:
            raise HTTPException(status_code=400, detail="dest_connection_id không hợp lệ (không tìm thấy S3 đích)")


@router.get("/configs")
async def list_configs():
    rows = await qc_sync_configs().find().sort("name", 1).to_list(length=500)
    return {"configs": [_public_config(c) for c in rows]}


@router.post("/configs")
async def create_config(body: ConfigIn, admin: dict = Depends(require_admin)):
    await _validate_refs(body.source_connection_id, body.dest_connection_id)
    now = datetime.now(timezone.utc)
    doc = {
        "_id": str(uuid.uuid4()), "name": body.name,
        "source_connection_id": body.source_connection_id, "prefix": body.prefix or "",
        "dest_connection_id": body.dest_connection_id,
        "interval_seconds": body.interval_seconds or config.QC_SYNC_DEFAULT_INTERVAL_SECONDS,
        "enabled": body.enabled,
        "last_run_at": None, "last_run_status": None,
        "created_at": now, "updated_at": now, "created_by": admin["username"],
    }
    await qc_sync_configs().insert_one(doc)
    await log_action(admin["username"], AuditAction.QC_SYNC_CONFIG_CREATE, doc["_id"],
                     {"after": {k: v for k, v in doc.items() if k != "_id"}})
    return {"ok": True, "config": _public_config(doc)}


@router.patch("/configs/{config_id}")
async def update_config(config_id: str, body: ConfigPatch, admin: dict = Depends(require_admin)):
    before = await qc_sync_configs().find_one({"_id": config_id})
    if not before:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh đồng bộ")
    await _validate_refs(body.source_connection_id, body.dest_connection_id)
    upd: dict = {}
    for field in ("name", "source_connection_id", "prefix", "dest_connection_id",
                  "interval_seconds", "enabled"):
        v = getattr(body, field)
        if v is not None:
            upd[field] = v
    if upd:
        upd["updated_at"] = datetime.now(timezone.utc)
        await qc_sync_configs().update_one({"_id": config_id}, {"$set": upd})
    after = await qc_sync_configs().find_one({"_id": config_id})
    await log_action(admin["username"], AuditAction.QC_SYNC_CONFIG_UPDATE, config_id,
                     {"before": before, "after": after})
    return {"ok": True, "config": _public_config(after)}


@router.delete("/configs/{config_id}")
async def delete_config(config_id: str, admin: dict = Depends(require_admin)):
    doc = await qc_sync_configs().find_one({"_id": config_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh đồng bộ")
    await qc_sync_configs().delete_one({"_id": config_id})
    await log_action(admin["username"], AuditAction.QC_SYNC_CONFIG_DELETE, config_id, {"deleted": doc})
    return {"ok": True}


@router.post("/configs/{config_id}/run")
async def run_now(config_id: str, admin: dict = Depends(require_admin)):
    """Tạo `qc_sync_jobs` ngay, bỏ qua chờ `interval_seconds` — worker nhặt ở
    vòng poll kế tiếp (xem `claim_qc_sync_job`)."""
    cfg = await qc_sync_configs().find_one({"_id": config_id})
    if not cfg:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh đồng bộ")
    active = await qc_sync_jobs().find_one(
        {"config_id": config_id, "status": {"$in": ["queued", "processing"]}}, {"_id": 1})
    if active:
        raise HTTPException(status_code=409, detail="Đã có lượt quét đang chạy cho kênh này")
    now = datetime.now(timezone.utc)
    job = {
        "_id": str(uuid.uuid4()), "config_id": config_id,
        "source_connection_id": cfg["source_connection_id"], "prefix": cfg.get("prefix") or "",
        "dest_connection_id": cfg.get("dest_connection_id"),
        "status": "queued", "started_at": None, "list_token": None,
        "scanned": 0, "enqueued": 0, "skipped": 0, "error": None, "created_at": now,
    }
    await qc_sync_jobs().insert_one(job)
    await log_action(admin["username"], AuditAction.QC_SYNC_RUN, config_id, {"job_id": job["_id"]})
    return {"ok": True, "job_id": job["_id"]}


@router.get("/stats")
async def get_stats(config_id: str | None = Query(default=None),
                    range: str = Query(default="all", pattern="^(day|week|month|all)$")):
    """Tổng đếm từ `qc_stats_daily` (rollup ngày, atomic $inc) — KHÔNG
    `count_documents` trên `qc_items` (nguyên tắc bất biến #3 của dự án)."""
    match: dict = {}
    if config_id:
        match["config_id"] = config_id
    today = datetime.now(timezone.utc).date()
    if range == "day":
        match["date"] = today.isoformat()
    elif range == "week":
        match["date"] = {"$gte": (today - timedelta(days=6)).isoformat()}
    elif range == "month":
        match["date"] = {"$gte": (today - timedelta(days=29)).isoformat()}
    totals: dict = {}
    async for d in qc_stats_daily().find(match):
        for k, v in (d.get("counts") or {}).items():
            totals[k] = totals.get(k, 0) + v
    return {"range": range, "config_id": config_id, "counts": totals}


@router.get("/items")
async def list_items(config_id: str | None = Query(default=None),
                     status: str | None = Query(default=None),
                     verdict: str | None = Query(default=None),
                     page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=50, ge=1, le=200)):
    flt: dict = {}
    if config_id:
        flt["config_id"] = config_id
    if status:
        flt["status"] = status
    if verdict:
        flt["qc.verdict"] = verdict
    skip = (page - 1) * page_size
    rows = await qc_items().find(flt).sort("created_at", -1).skip(skip).limit(page_size).to_list(length=page_size)
    for r in rows:
        r["id"] = r.pop("_id")
    return {"items": rows, "page": page, "page_size": page_size}
