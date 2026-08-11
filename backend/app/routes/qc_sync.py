"""QC Sync — CRUD kênh đồng bộ (nguồn + đích + prefix + chu kỳ), kích hoạt quét
ngay, thống kê + theo dõi item. admin-only, cùng khuôn `s3_connections.py`.
Xử lý thật (liệt kê, QC, OCR, crop) chạy trong worker — xem
`app/worker/qc_pipeline.py`. Chi tiết luồng: `docs/algorithm.md §9`."""

import io
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import config, storage
from app.audit import AuditAction, log_action
from app.db import qc_items, qc_stats_daily, qc_sync_configs, qc_sync_jobs, qc_wards, s3_connections
from app.deps import require_admin
from app.land_normalizer.classification.structural import classify_structural
from app.land_normalizer.pipeline import build_payload
from app.land_normalizer.registry import build_default_registry
from app.land_normalizer_adapter import first_entry, raw_record_from_cut
from app.storage import SourceObjectMissing, SourceObjectUnavailable
from app.worker.qc_pipeline import _classify_cuts

_REGISTRY = build_default_registry()

router = APIRouter(prefix="/v1/qc-sync", tags=["qc-sync"], dependencies=[Depends(require_admin)])


class ConfigIn(BaseModel):
    name: str
    source_connection_id: str
    prefix: str = ""
    dest_connection_id: str
    interval_seconds: int | None = None
    enabled: bool = True
    # Tên Phường/Xã hiển thị thay cho `name` — mặc định TỰ ĐỘNG suy từ danh
    # sách xã đã đồng bộ (`qc_wards`, khớp theo `name` == mã xã, xem
    # `POST /wards/sync`); field này chỉ cần điền tay khi muốn GHI ĐÈ (vd
    # `name` không khớp mã xã nào, hoặc muốn hiển thị tên khác).
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


def _public_config(c: dict, ward_map: dict | None = None) -> dict:
    # `ward_name` NHẬP TAY thắng nếu có; không thì tự suy từ danh sách xã đã
    # đồng bộ (khớp mã xã == `name` — quy ước đặt tên kênh hiện tại).
    ward_name = c.get("ward_name") or (ward_map or {}).get(c.get("name"), "")
    return {
        "id": c["_id"], "name": c.get("name"), "ward_name": ward_name,
        "source_connection_id": c.get("source_connection_id"), "prefix": c.get("prefix") or "",
        "dest_connection_id": c.get("dest_connection_id"),
        "interval_seconds": c.get("interval_seconds") or config.QC_SYNC_DEFAULT_INTERVAL_SECONDS,
        "enabled": bool(c.get("enabled")),
        "items_paused": bool(c.get("items_paused")),
        "last_run_at": c.get("last_run_at"), "last_run_status": c.get("last_run_status"),
        "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
        "created_by": c.get("created_by"),
    }


async def _ward_map() -> dict:
    """`{maXa: tenXa}` từ `qc_wards` (đã đồng bộ qua `POST /wards/sync`) —
    fetch 1 lần/request, KHÔNG lặp query cho từng config (tập nhỏ, load hết
    vào RAM là đủ rẻ — cùng tinh thần các map nhỏ khác trong file này)."""
    return {w["_id"]: w.get("ten_xa") async for w in qc_wards().find()}


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
    ward_map = await _ward_map()
    return {"configs": [_public_config(c, ward_map) for c in rows]}


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
    return {"ok": True, "config": _public_config(doc, await _ward_map())}


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
    return {"ok": True, "config": _public_config(after, await _ward_map())}


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


def _range_match(range: str) -> dict:
    """`date` match cho `range` day|week|month|all — dùng chung giữa `/stats`
    và `/stats/by-config` (tránh lặp lại logic quy đổi range→ngưỡng ngày)."""
    today = datetime.now(timezone.utc).date()
    if range == "day":
        return {"date": today.isoformat()}
    if range == "week":
        return {"date": {"$gte": (today - timedelta(days=6)).isoformat()}}
    if range == "month":
        return {"date": {"$gte": (today - timedelta(days=29)).isoformat()}}
    return {}


@router.get("/stats")
async def get_stats(config_id: str | None = Query(default=None),
                    range: str = Query(default="all", pattern="^(day|week|month|all)$")):
    """Tổng đếm từ `qc_stats_daily` (rollup ngày, atomic $inc) — KHÔNG
    `count_documents` trên `qc_items` (nguyên tắc bất biến #3 của dự án)."""
    match = _range_match(range)
    if config_id:
        match["config_id"] = config_id
    totals: dict = {}
    async for d in qc_stats_daily().find(match):
        for k, v in (d.get("counts") or {}).items():
            totals[k] = totals.get(k, 0) + v
    return {"range": range, "config_id": config_id, "counts": totals}


@router.get("/stats/by-config")
async def get_stats_by_config(range: str = Query(default="all", pattern="^(day|week|month|all)$")):
    """Đếm theo TỪNG kênh (= từng Phường/Xã, xem `ward_name`) cho `range` —
    phục vụ bảng "Theo Phường/Xã" ở Tổng quan (`QcSyncStats.jsx`), 1 lần gọi
    thay vì N lần gọi `/stats?config_id=` phía FE. Liệt kê ĐỦ mọi kênh (kể cả
    kênh chưa có hoạt động nào trong khoảng — vẫn hiện dòng, counts rỗng) để
    bảng phản ánh đúng danh sách Phường/Xã đang quản lý, không chỉ kênh có
    dữ liệu."""
    match = _range_match(range)
    by_config: dict[str, dict] = {}
    async for d in qc_stats_daily().find(match):
        bucket = by_config.setdefault(d["config_id"], {})
        for k, v in (d.get("counts") or {}).items():
            bucket[k] = bucket.get(k, 0) + v
    configs = await qc_sync_configs().find().sort("name", 1).to_list(length=500)
    ward_map = await _ward_map()
    rows = [
        {"config_id": c["_id"], "name": c.get("name"),
         "ward_name": c.get("ward_name") or ward_map.get(c.get("name"), ""),
         "counts": by_config.get(c["_id"], {})}
        for c in configs
    ]
    return {"range": range, "rows": rows}


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
                     # "Đã xử lý" = không còn nằm ở hàng chờ/đang chạy — dùng
                     # cho panel CHỈ-XEM ở Tổng quan (WardItemsPanel), khác
                     # bảng admin "File gần đây" (có dropdown status riêng,
                     # mặc định vẫn thấy cả hàng chờ để debug tiến độ).
                     processed_only: bool = Query(default=False),
                     sort_by: str = Query(default="created_at", pattern="^(created_at|finished_at)$"),
                     page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=50, ge=1, le=200)):
    flt: dict = {}
    if config_id:
        flt["config_id"] = config_id
    if status:
        flt["status"] = status
    elif processed_only:
        flt["status"] = {"$nin": ["queued", "processing"]}
    if verdict:
        flt["qc.verdict"] = verdict
    skip = (page - 1) * page_size
    rows = await qc_items().find(flt).sort(sort_by, -1).skip(skip).limit(page_size).to_list(length=page_size)
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


# ── Phân loại hồ sơ + "làm mịn dữ liệu" (phase 2, port từ vpdd-don-ai) ──────
# Xem docs/algorithm.md §10 — build_payload/classify_structural tính TỰ ĐỘNG
# trong worker ngay sau OCR (app/worker/qc_pipeline.py::_classify_cuts); các
# endpoint dưới đây chỉ phục vụ xem lại (bảng "Phân loại") + chạy lại thủ công.

@router.post("/items/{item_id}/cuts/{cut_index}/reclassify")
async def reclassify_cut(item_id: str, cut_index: int, admin: dict = Depends(require_admin)):
    """Chạy lại "làm mịn dữ liệu" (build Payload) + phân loại cấu trúc cho 1
    cut — KHÔNG chạy lại QC/OCR (đã có sẵn `item.ocr.records`), chỉ tính lại
    bước thuần Python phía sau. Dùng khi thuật toán chuẩn hoá đổi, hoặc lần
    tính tự động trước đó lỗi (`refined`/`classification` = None)."""
    item = await qc_items().find_one({"_id": item_id})
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy item")
    cuts = ((item.get("ocr") or {}).get("cuts")) or []
    cut = next((c for c in cuts if c.get("index") == cut_index), None)
    if not cut:
        raise HTTPException(status_code=404, detail="Không tìm thấy file đã cắt")
    records = ((item.get("ocr") or {}).get("records")) or []
    record = records[cut_index] if 0 <= cut_index < len(records) else {}
    entry = first_entry(record)
    cfg = await qc_sync_configs().find_one(
        {"_id": item.get("config_id")}, {"name": 1, "dest_connection_id": 1})
    ward_code = (cfg or {}).get("name") or ""
    dest_bucket = ""
    dest_id = (cfg or {}).get("dest_connection_id")
    if dest_id:
        conn = await s3_connections().find_one({"_id": dest_id}, {"bucket": 1})
        dest_bucket = (conn or {}).get("bucket") or ""
    try:
        raw = raw_record_from_cut(entry, cut, ward_code=ward_code, item_id=item_id,
                                  dest_bucket=dest_bucket)
        build = build_payload([raw], _REGISTRY)
        refined = build.payload.model_dump(mode="json")
        classification = classify_structural(build.payload).model_dump(mode="json")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Lỗi làm mịn/phân loại: {e}") from e
    await qc_items().update_one(
        {"_id": item_id, "ocr.cuts.index": cut_index},
        {"$set": {"ocr.cuts.$.refined": refined, "ocr.cuts.$.classification": classification}},
    )
    await log_action(admin["username"], AuditAction.QC_SYNC_ITEM_RECLASSIFY, item_id,
                     {"cut_index": cut_index})
    return {"ok": True, "refined": refined, "classification": classification}


@router.get("/classifications")
async def list_classifications(config_id: str | None = Query(default=None),
                                q: str | None = Query(default=None),
                                structural_label: str | None = Query(default=None),
                                loai_giay: str | None = Query(default=None),
                                page: int = Query(default=1, ge=1),
                                page_size: int = Query(default=50, ge=1, le=200)):
    """Bảng "Phân loại" (1 dòng/1 cut đã làm mịn) — aggregation nhỏ trên
    `qc_items` (`$unwind` cuts), collection không lớn tới mức phải tránh
    aggregate (khác `gcns()` 600k dòng, xem nguyên tắc bất biến #3)."""
    pipeline: list[dict] = [
        {"$match": {"config_id": config_id} if config_id else {}},
        {"$unwind": "$ocr.cuts"},
        {"$match": {"ocr.cuts.classification": {"$ne": None}}},
    ]
    if structural_label:
        pipeline.append({"$match": {"ocr.cuts.classification.NhanCauTruc": structural_label}})
    if loai_giay:
        # Dot-path qua mảng GiayChungNhans khớp nếu BẤT KỲ phần tử nào có đúng
        # tên loại này (thực tế luôn 1 phần tử/cut) — xem resolvers/giay_chung_nhan.py.
        pipeline.append({"$match": {
            "ocr.cuts.refined.GiayChungNhans.GiayChungNhan.TenLoaiGiayChungNhan": loai_giay}})
    if q:
        pipeline.append({"$match": {"ocr.cuts.so_phat_hanh": {"$regex": re.escape(q.strip()), "$options": "i"}}})
    skip = (page - 1) * page_size
    # $facet dùng LẠI đúng $match/$unwind ở trên cho cả 2 nhánh (trang dữ liệu +
    # đếm tổng) — không phải quét thêm 1 lần riêng như `count_documents` trên
    # toàn bộ collection (nguyên tắc bất biến #3 chỉ cấm đếm KHÔNG lọc gì).
    pipeline.append({"$facet": {
        "data": [
            {"$sort": {"finished_at": -1}},
            {"$skip": skip}, {"$limit": page_size},
            {"$project": {
                "_id": 0, "item_id": "$_id", "config_id": "$config_id",
                "cut_index": "$ocr.cuts.index", "so_phat_hanh": "$ocr.cuts.so_phat_hanh",
                "name": "$ocr.cuts.name", "classification": "$ocr.cuts.classification",
                # "Loại giấy" (Loại GCN, suy từ SoHieuGiayChungNhan + ngày cấp —
                # xem resolvers/giay_chung_nhan.py) đã có sẵn trong `refined`, chỉ
                # lấy đúng field cần hiện ở bảng, tránh kéo cả payload lớn về FE.
                "loai_giay": {"$arrayElemAt": [
                    "$ocr.cuts.refined.GiayChungNhans.GiayChungNhan.TenLoaiGiayChungNhan", 0]},
            }},
        ],
        "count": [{"$count": "n"}],
    }})
    res = await qc_items().aggregate(pipeline).to_list(length=1)
    facet = res[0] if res else {"data": [], "count": []}
    rows = facet["data"]
    total = (facet["count"][0]["n"] if facet["count"] else 0)

    configs = await qc_sync_configs().find().to_list(length=500)
    ward_map = await _ward_map()
    ward_names = {c["_id"]: (c.get("ward_name") or ward_map.get(c.get("name"), "")) for c in configs}
    for r in rows:
        r["ward_name"] = ward_names.get(r["config_id"], "")
    return {"rows": rows, "total": total, "page": page, "page_size": page_size}


@router.post("/classifications/backfill")
async def backfill_classifications(config_id: str | None = Query(default=None),
                                    limit: int = Query(default=300, ge=1, le=2000),
                                    admin: dict = Depends(require_admin)):
    """Phân loại/làm mịn dữ liệu cho các item ĐÃ XONG (`status=done`) từ TRƯỚC
    khi tính năng này ra đời, hoặc lần tính tự động (worker) trước đó lỗi —
    `process_qc_item` chỉ tự động tính cho item xử lý SAU khi thêm tính năng
    (xem docs/algorithm.md §10). Xử lý tối đa `limit` item/lần gọi (đồng bộ,
    thuần Python — không gọi API ngoài nào nên đủ rẻ để chạy theo lô lớn); FE
    gọi lặp lại (nút "Phân loại các bản ghi cũ") tới khi `has_more=false`.
    KHÔNG dùng `count_documents` để báo "còn lại bao nhiêu" (nguyên tắc bất
    biến #3) — chỉ báo còn hay hết dựa vào có lấy đủ `limit` item hay không."""
    match: dict = {
        "status": "done",
        "ocr.cuts": {"$elemMatch": {"$or": [{"refined": {"$exists": False}}, {"refined": None}]}},
    }
    if config_id:
        match["config_id"] = config_id
    items = await qc_items().find(
        match, {"config_id": 1, "ocr.cuts": 1, "ocr.records": 1},
    ).limit(limit).to_list(length=limit)

    configs = await qc_sync_configs().find().to_list(length=500)
    dest_ids = [c["dest_connection_id"] for c in configs if c.get("dest_connection_id")]
    dests = (await s3_connections().find({"_id": {"$in": dest_ids}}, {"bucket": 1}).to_list(length=500)
             if dest_ids else [])
    dest_bucket_by_id = {d["_id"]: d.get("bucket") or "" for d in dests}
    meta_by_config = {
        c["_id"]: (c.get("name") or "", dest_bucket_by_id.get(c.get("dest_connection_id"), ""))
        for c in configs
    }

    n_items = 0
    for item in items:
        item_id = item["_id"]
        cuts = ((item.get("ocr") or {}).get("cuts")) or []
        records = ((item.get("ocr") or {}).get("records")) or []
        ward_code, dest_bucket = meta_by_config.get(item.get("config_id"), ("", ""))
        _classify_cuts(records, cuts, ward_code=ward_code, dest_bucket=dest_bucket, item_id=item_id)
        await qc_items().update_one({"_id": item_id}, {"$set": {"ocr.cuts": cuts}})
        n_items += 1

    await log_action(admin["username"], AuditAction.QC_SYNC_ITEM_RECLASSIFY, config_id or "(all)",
                     {"backfill": True, "processed_items": n_items})
    return {"processed": n_items, "has_more": len(items) == limit}


@router.get("/items/{item_id}/cuts/{cut_index}/refined-payload")
async def get_refined_payload(item_id: str, cut_index: int, download: bool = Query(default=False),
                              admin: dict = Depends(require_admin)):
    """Trả JSON `Payload` đã "làm mịn" (chuẩn hoá) cho 1 cut — đây là dữ liệu
    SẼ LÀ input cho API "kiểm tra đơn" của HSQ nếu người dùng tự đem đi dùng;
    ai-hub KHÔNG gọi API đó (không có item_id/token HSQ) — chỉ hiển thị JSON
    để copy/tải về thủ công (`download=true` → tải file thay vì xem inline)."""
    item = await qc_items().find_one({"_id": item_id})
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy item")
    cuts = ((item.get("ocr") or {}).get("cuts")) or []
    cut = next((c for c in cuts if c.get("index") == cut_index), None)
    if not cut:
        raise HTTPException(status_code=404, detail="Không tìm thấy file đã cắt")
    refined = cut.get("refined")
    if refined is None:
        raise HTTPException(status_code=404, detail="Chưa có dữ liệu đã làm mịn cho file này")
    await log_action(admin["username"], AuditAction.QC_SYNC_ITEM_VIEW_REFINED, item_id,
                     {"cut_index": cut_index, "download": download})
    headers = {}
    if download:
        so_gcn = (cut.get("so_phat_hanh") or f"cut{cut_index}").replace("/", "-")
        headers["Content-Disposition"] = f'attachment; filename="{so_gcn}-payload.json"'
    return JSONResponse(content=refined, headers=headers)


# ── Danh sách Phường/Xã (để hiện tên thay mã kênh) ──────────────────────────

class WardSyncIn(BaseModel):
    url: str
    # Header tuỳ chỉnh (vd "Cookie" cho API đăng nhập qua session — thực tế
    # gặp: hub-vpdk.hanoi.gov.vn yêu cầu Cookie next-auth, không phải Bearer
    # token đơn giản). Admin tự nhập mỗi lần đồng bộ (không lưu lại — cookie/
    # session token hết hạn, lưu vào DB chỉ tạo ra secret chết mà không ai
    # dọn). KHÔNG ghi vào audit log (secret trong header).
    headers: dict[str, str] | None = None


@router.get("/wards")
async def list_wards():
    rows = await qc_wards().find().sort("ten_xa", 1).to_list(length=5000)
    return {"wards": [
        {"ma_xa": r["_id"], "ten_xa": r.get("ten_xa"), "id": r.get("id"), "synced_at": r.get("synced_at")}
        for r in rows
    ]}


@router.post("/wards/sync")
async def sync_wards(body: WardSyncIn, admin: dict = Depends(require_admin)):
    """Đồng bộ danh sách Phường/Xã từ 1 API bên ngoài — URL do admin tự nhập
    trên UI mỗi lần đồng bộ (không hardcode). GHI ĐÈ TOÀN BỘ danh sách cũ (FE
    đã `window.confirm()` cảnh báo trước nếu đang có dữ liệu — cùng pattern
    các thao tác ghi đè/xoá khác trong trang này, xem `QcSync.jsx`).

    Gọi HTTP GET bằng `httpx` — KHÔNG shell ra lệnh `curl` với chuỗi người
    dùng gõ (rủi ro command injection nếu URL/tham số chứa ký tự đặc biệt);
    kết quả tương đương "chạy curl" nhưng an toàn, cùng idiom `qc_client.py`.

    Kỳ vọng response `{"data": [{"id", "tenXa", "maXa"}, ...], "success": bool}`."""
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Cần nhập URL")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=body.headers or None)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Không gọi được API: {e}") from e
    if resp.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"API trả về lỗi (mã {resp.status_code}): {resp.text[:300]}")
    try:
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"API không trả về JSON hợp lệ: {e}") from e
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise HTTPException(status_code=502, detail=f"API báo lỗi: {payload}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="API trả về thiếu field 'data' (danh sách xã)")

    now = datetime.now(timezone.utc)
    docs, skipped = [], 0
    for item in data:
        if not isinstance(item, dict):
            skipped += 1
            continue
        ma_xa = str(item.get("maXa") or "").strip()
        ten_xa = str(item.get("tenXa") or "").strip()
        if not ma_xa or not ten_xa:
            skipped += 1
            continue
        docs.append({"_id": ma_xa, "id": item.get("id"), "ten_xa": ten_xa, "synced_at": now})
    if not docs:
        raise HTTPException(status_code=400, detail="Không có xã hợp lệ nào trong dữ liệu trả về")

    await qc_wards().delete_many({})
    await qc_wards().insert_many(docs, ordered=False)
    await log_action(admin["username"], AuditAction.QC_SYNC_WARDS_SYNC, "wards",
                     {"url": url, "imported": len(docs), "skipped": skipped})
    return {"ok": True, "imported": len(docs), "skipped": skipped, "url": url}
