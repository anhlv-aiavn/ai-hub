"""QC Sync — CRUD kênh đồng bộ (nguồn + đích + prefix + chu kỳ), kích hoạt quét
ngay, thống kê + theo dõi item. admin-only, cùng khuôn `s3_connections.py`.
Xử lý thật (liệt kê, QC, OCR, crop) chạy trong worker — xem
`app/worker/qc_pipeline.py`. Chi tiết luồng: `docs/algorithm.md §9`."""

import io
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import config, storage
from app.audit import AuditAction, log_action
from app.db import qc_items, qc_stats_daily, qc_sync_configs, qc_sync_jobs, s3_connections
from app.deps import require_admin
from app.storage import SourceObjectMissing, SourceObjectUnavailable

router = APIRouter(prefix="/v1/qc-sync", tags=["qc-sync"], dependencies=[Depends(require_admin)])


class ConfigIn(BaseModel):
    name: str
    source_connection_id: str
    prefix: str = ""
    dest_connection_id: str
    interval_seconds: int | None = None
    enabled: bool = True
    # Tên Phường/Xã hiển thị thay cho `name` (thường đặt trùng mã P/X, vd
    # "00004") trên biểu đồ/bộ lọc ở trang Tổng quan — người dùng tự nhập
    # (đã có sẵn bảng mã→tên), KHÔNG có ánh xạ tự động trong hệ thống.
    ward_name: str | None = None


class ConfigPatch(BaseModel):
    name: str | None = None
    source_connection_id: str | None = None
    prefix: str | None = None
    dest_connection_id: str | None = None
    interval_seconds: int | None = None
    enabled: bool | None = None
    # Tạm dừng/tiếp tục XỬ LÝ file đang `queued` (khác `enabled` — cái đó điều
    # khiển có tự quét THÊM file mới hay không). Đọc bởi worker.claim_qc_item
    # (cache có throttle, xem qc_pipeline.py) — file đã claim trước khi bật
    # tạm dừng vẫn chạy nốt, không bị ngắt giữa chừng.
    items_paused: bool | None = None
    ward_name: str | None = None


def _public_config(c: dict) -> dict:
    return {
        "id": c["_id"], "name": c.get("name"), "ward_name": c.get("ward_name") or "",
        "source_connection_id": c.get("source_connection_id"), "prefix": c.get("prefix") or "",
        "dest_connection_id": c.get("dest_connection_id"),
        "interval_seconds": c.get("interval_seconds") or config.QC_SYNC_DEFAULT_INTERVAL_SECONDS,
        "enabled": bool(c.get("enabled")),
        "items_paused": bool(c.get("items_paused")),
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
        "enabled": body.enabled, "items_paused": False, "ward_name": body.ward_name or "",
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
                  "interval_seconds", "enabled", "items_paused", "ward_name"):
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
    """Xóa kênh = xóa TOÀN BỘ dữ liệu liên quan (job/item/thống kê) — sau khi
    xóa config, không còn cách nào xem/dọn các bản ghi "mồ côi" này qua UI nữa
    (không có tên kênh để tra). KHÔNG đụng file đã cắt đã ghi ở MinIO đích
    (chỉ dữ liệu Mongo)."""
    doc = await qc_sync_configs().find_one({"_id": config_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh đồng bộ")
    # Hủy MỌI job queued/processing TRƯỚC — worker sớm dừng, giảm khả năng ghi
    # thêm dữ liệu SAU khi đã xóa bên dưới (không loại bỏ hoàn toàn race: worker
    # chỉ nhận cờ cancel ở checkpoint giữa các trang liệt kê — chấp nhận cho
    # thao tác dọn dẹp admin, không phải đường xử lý chính).
    await qc_sync_jobs().update_many(
        {"config_id": config_id, "status": "queued"}, {"$set": {"status": "cancelled"}})
    await qc_sync_jobs().update_many(
        {"config_id": config_id, "status": "processing"}, {"$set": {"cancel_requested": True}})
    n_jobs = (await qc_sync_jobs().delete_many({"config_id": config_id})).deleted_count
    n_items = (await qc_items().delete_many({"config_id": config_id})).deleted_count
    n_stats = (await qc_stats_daily().delete_many({"config_id": config_id})).deleted_count
    await qc_sync_configs().delete_one({"_id": config_id})
    await log_action(admin["username"], AuditAction.QC_SYNC_CONFIG_DELETE, config_id,
                     {"deleted": doc, "deleted_jobs": n_jobs, "deleted_items": n_items,
                      "deleted_stats_days": n_stats})
    return {"ok": True, "deleted_jobs": n_jobs, "deleted_items": n_items, "deleted_stats_days": n_stats}


def _public_job(j: dict) -> dict:
    j = dict(j)
    j["id"] = j.pop("_id")
    return j


@router.post("/configs/{config_id}/run")
async def run_now(config_id: str, admin: dict = Depends(require_admin)):
    """Tạo `qc_sync_jobs` ngay, bỏ qua chờ `interval_seconds` — worker nhặt ở
    vòng poll kế tiếp (xem `claim_qc_sync_job`)."""
    cfg = await qc_sync_configs().find_one({"_id": config_id})
    if not cfg:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh đồng bộ")
    active = await qc_sync_jobs().find_one(
        {"config_id": config_id, "status": {"$in": ["queued", "processing"]}})
    if active:
        # Kèm job đang chạy trong detail — FE hiện tiến độ thay vì chỉ báo lỗi
        # suông (xem GET .../active-job để poll tiếp + POST .../cancel để dừng).
        raise HTTPException(status_code=409, detail={
            "message": "Đã có lượt quét đang chạy cho kênh này", "job": _public_job(active),
        })
    now = datetime.now(timezone.utc)
    job = {
        "_id": str(uuid.uuid4()), "config_id": config_id,
        "source_connection_id": cfg["source_connection_id"], "prefix": cfg.get("prefix") or "",
        "dest_connection_id": cfg.get("dest_connection_id"),
        "status": "queued", "started_at": None, "list_token": None,
        "scanned": 0, "enqueued": 0, "skipped": 0, "error": None,
        "cancel_requested": False, "created_at": now,
    }
    await qc_sync_jobs().insert_one(job)
    await log_action(admin["username"], AuditAction.QC_SYNC_RUN, config_id, {"job_id": job["_id"]})
    return {"ok": True, "job_id": job["_id"]}


@router.get("/configs/{config_id}/active-job")
async def get_active_job(config_id: str):
    """Lượt quét (`qc_sync_job`) đang `queued`/`processing` của 1 kênh, nếu có
    — FE poll endpoint này để hiện tiến độ (scanned/enqueued/skipped) khi đang
    chạy, thay vì chỉ thấy lỗi 409 lúc bấm "Chạy ngay" lần nữa."""
    job = await qc_sync_jobs().find_one(
        {"config_id": config_id, "status": {"$in": ["queued", "processing"]}},
        sort=[("created_at", -1)],
    )
    return {"job": _public_job(job) if job else None}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, admin: dict = Depends(require_admin)):
    """Dừng 1 lượt quét đang chạy. `queued` (chưa worker nào claim) → hủy NGAY
    tại chỗ. `processing` → cắm cờ `cancel_requested`, worker tự dừng ở
    checkpoint kế tiếp giữa các trang liệt kê (xem `process_qc_sync_job` —
    không thể ngắt ngang 1 call S3 đang chạy dở, chỉ dừng được giữa 2 trang)."""
    job = await qc_sync_jobs().find_one({"_id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy lượt quét")
    if job["status"] not in ("queued", "processing"):
        raise HTTPException(status_code=409, detail="Lượt quét này không còn chạy")
    res = await qc_sync_jobs().update_one(
        {"_id": job_id, "status": "queued"}, {"$set": {"status": "cancelled"}})
    if res.modified_count:
        await qc_sync_configs().update_one(
            {"_id": job["config_id"]},
            {"$set": {"last_run_at": datetime.now(timezone.utc), "last_run_status": "cancelled"}},
        )
    else:
        # Đã/đang bị claim (processing) — không tự tay đổi status ở đây (worker
        # đang ghi heartbeat song song, dễ đụng độ), chỉ cắm cờ để NÓ tự dừng.
        await qc_sync_jobs().update_one({"_id": job_id}, {"$set": {"cancel_requested": True}})
    await log_action(admin["username"], AuditAction.QC_SYNC_JOB_CANCEL, job_id,
                     {"config_id": job["config_id"]})
    return {"ok": True}


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


@router.get("/stats/series")
async def get_stats_series(config_id: str | None = Query(default=None),
                           days: int = Query(default=90, ge=1, le=730)):
    """Chuỗi thời gian theo NGÀY từ `qc_stats_daily` (rollup có sẵn, không tính
    lại) — phục vụ 3 biểu đồ (QC/OCR/số file cắt) ở trang Tổng quan
    (`QcSyncStats.jsx`), khác `GET /stats` (chỉ trả 1 tổng gộp cho 1 khoảng).
    Gộp theo ngày bằng vòng lặp Python (không lọc `config_id` → nhiều kênh
    CÙNG 1 ngày phải cộng dồn) — collection nhỏ (bounded theo số ngày × số
    kênh), không cần aggregation pipeline. Resample ngày→tuần/tháng do FE lo."""
    since = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    match: dict = {"date": {"$gte": since}}
    if config_id:
        match["config_id"] = config_id
    by_date: dict[str, dict] = {}
    async for d in qc_stats_daily().find(match):
        date = d["date"]
        bucket = by_date.setdefault(date, {})
        for k, v in (d.get("counts") or {}).items():
            bucket[k] = bucket.get(k, 0) + v
    series = [{"date": d, "counts": by_date[d]} for d in sorted(by_date)]
    return {"config_id": config_id, "days": days, "series": series}


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


@router.post("/items/{item_id}/retry")
async def retry_item(item_id: str, admin: dict = Depends(require_admin)):
    """Đưa 1 item về `queued` để worker xử lý lại. GIỮ NGUYÊN field `qc` nếu
    đã có verdict — `process_qc_item` (worker) tự nhận ra và BỎ QUA gọi lại
    QC scanner, chỉ chạy lại từ bước sau (OCR/crop). Đúng ý: lỗi mạng/QC thì
    chạy lại được, nhưng KHÔNG bắt quét QC lại với file đã có verdict rồi.
    Chỉ xóa `ocr` (sắp chạy lại) + error/timestamps."""
    item = await qc_items().find_one({"_id": item_id})
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy item")
    if item.get("status") == "processing":
        raise HTTPException(status_code=409, detail="Item đang được xử lý, thử lại sau")
    await qc_items().update_one(
        {"_id": item_id},
        {"$set": {"status": "queued", "error": None, "error_kind": None,
                  "ocr": None, "started_at": None, "finished_at": None}},
    )
    await log_action(admin["username"], AuditAction.QC_SYNC_ITEM_RETRY, item_id,
                     {"config_id": item.get("config_id"), "s3_key": item.get("s3_key"),
                      "kept_qc": bool(item.get("qc"))})
    return {"ok": True}


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, admin: dict = Depends(require_admin)):
    """Xóa 1 item khỏi lịch sử quét — file sẽ được coi là "mới" và quét lại ở
    lượt kế tiếp (unique index chỉ chặn theo `(source_connection_id, s3_key)`
    hiện có, xóa doc là gỡ chặn). KHÔNG xóa file đã cắt đã ghi ở MinIO đích,
    KHÔNG lùi số liệu `qc_stats_daily` đã cộng dồn trước đó (thống kê vận
    hành gần đúng, không phải sổ sách tài chính — chấp nhận lệch nhỏ)."""
    item = await qc_items().find_one({"_id": item_id})
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy item")
    await qc_items().delete_one({"_id": item_id})
    await log_action(admin["username"], AuditAction.QC_SYNC_ITEM_DELETE, item_id,
                     {"config_id": item.get("config_id"), "s3_key": item.get("s3_key"),
                      "status": item.get("status")})
    return {"ok": True}


@router.delete("/configs/{config_id}/items")
async def clear_config_items(config_id: str, admin: dict = Depends(require_admin)):
    """Xóa TOÀN BỘ lịch sử quét (mọi file, mọi trạng thái) của 1 kênh — để quét
    lại từ đầu cả thư mục. KHÔNG xóa file đã cắt đã ghi ở MinIO đích, KHÔNG
    xóa lịch sử job (`qc_sync_jobs` — giữ làm audit trail)."""
    cfg = await qc_sync_configs().find_one({"_id": config_id})
    if not cfg:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh đồng bộ")
    n_items = (await qc_items().delete_many({"config_id": config_id})).deleted_count
    n_stats = (await qc_stats_daily().delete_many({"config_id": config_id})).deleted_count
    await log_action(admin["username"], AuditAction.QC_SYNC_ITEMS_CLEAR, config_id,
                     {"deleted_items": n_items, "deleted_stats_days": n_stats})
    return {"ok": True, "deleted_items": n_items, "deleted_stats_days": n_stats}


@router.get("/items/{item_id}/source-pdf")
async def get_source_pdf(item_id: str, admin: dict = Depends(require_admin)):
    """Xem trực tiếp PDF nguồn (kho MinIO nguồn, KHÔNG phải file đã cắt) — mở
    tab mới từ cột "S3 key" trên bảng theo dõi, phục vụ đối chiếu khi debug.
    `inline` (không `attachment`) để trình duyệt hiển thị PDF thay vì tải về."""
    item = await qc_items().find_one({"_id": item_id})
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy item")
    try:
        buf = await storage.get_pdf(item["s3_key"], item.get("source_connection_id"))
    except SourceObjectMissing as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except SourceObjectUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    await log_action(admin["username"], AuditAction.QC_SYNC_ITEM_VIEW_SOURCE, item_id,
                     {"s3_key": item["s3_key"]})
    filename = item["s3_key"].rsplit("/", 1)[-1] or "file.pdf"
    return StreamingResponse(
        io.BytesIO(buf.getvalue()), media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/items/{item_id}/cuts/{cut_index}/pdf")
async def get_cut_pdf(item_id: str, cut_index: int, admin: dict = Depends(require_admin)):
    """Xem trực tiếp 1 file ĐÃ CẮT (khác PDF nguồn) — nằm ở MinIO đích RIÊNG
    của QC Sync (`purpose="qc"`), khác đích của pipeline GCN chính. Mở tab mới
    từ cột "File đã cắt" trên bảng theo dõi."""
    item = await qc_items().find_one({"_id": item_id})
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy item")
    cuts = ((item.get("ocr") or {}).get("cuts")) or []
    cut = next((c for c in cuts if c.get("index") == cut_index), None)
    if not cut:
        raise HTTPException(status_code=404, detail="Không tìm thấy file đã cắt")
    try:
        buf = await storage.get_pdf(cut["s3_key"], dest_purpose="qc")
    except SourceObjectMissing as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except SourceObjectUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    await log_action(admin["username"], AuditAction.QC_SYNC_ITEM_VIEW_CUT, item_id,
                     {"s3_key": cut["s3_key"], "cut_index": cut_index})
    filename = cut.get("name") or cut["s3_key"].rsplit("/", 1)[-1] or "cut.pdf"
    return StreamingResponse(
        io.BytesIO(buf.getvalue()), media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
