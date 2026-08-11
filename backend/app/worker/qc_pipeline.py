"""Pipeline QC Sync — MỚI, song song với pipeline GCN chính (run_job.py):
đồng bộ liên tục 1 kho MinIO nguồn do người dùng cấu hình, chấm chất lượng
scan qua `qc-scanner-server` NGOÀI (app/qc_client.py), ghi thống kê vào Mongo,
và nếu QC đạt (pass/warn) thì chạy OCR (TÁI DÙNG `run_job._pipeline`/
`_build_cuts` có sẵn — không viết lại) để tìm + cắt trang GCN, lưu vào MinIO
đích RIÊNG của pipeline này.

2 loại job Mongo-as-queue, cùng idiom với import_job.py/worker/main.py:
- `qc_sync_job`  — DISCOVERY: liệt kê STREAM 1 kênh đồng bộ (`qc_sync_configs`),
  insert `qc_items` mới (dedup nhờ unique index, không đọc trước khi ghi).
- `qc_item`      — XỬ LÝ 1 file: tải PDF nguồn → QC → (nếu đạt) OCR + crop.

`maybe_schedule_qc_sync_jobs()` tự tạo `qc_sync_job` mới khi 1 config tới hạn
quét lại (không có job nào đang chạy cho config đó) — chạy cạnh `_sweep_dead`
trong worker/main.py, tự throttle để không query mỗi vòng poll.

LƯU Ý VẬN HÀNH: bước OCR dùng CHUNG pool vLLM (_VLM_SEM) với pipeline GCN sản
xuất — xem config.WORKER_QC_ITEM_MAX_CONCURRENT."""

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError

from app import config, qc_client, storage
from app.s3_util import build_client
from app.storage import DestinationNotConfigured, SourceObjectMissing, SourceObjectUnavailable
from app.worker import run_job
from src.extentions.mongo_helper import AsyncMongo

log = logging.getLogger(__name__)

CHUNK_SIZE = 500  # cùng cỡ trang liệt kê với import_job.py

SCHEDULE_INTERVAL = float(os.getenv("WORKER_QC_SCHEDULE_INTERVAL_SECONDS", "15"))
_last_schedule = 0.0


# ── qc_sync_job: discovery (mirror import_job.py) ───────────────────────────

async def claim_qc_sync_job(mongo: AsyncMongo, proc_ttl: int) -> dict | None:
    """Atomic claim 1 job: queued, hoặc processing đã treo quá proc_ttl."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=proc_ttl)
    return await mongo.db[config.COLL_QC_SYNC_JOB].find_one_and_update(
        {"$or": [
            {"status": "queued"},
            {"status": "processing", "started_at": {"$lt": stale}},
        ]},
        {"$set": {"status": "processing", "started_at": now}},
        return_document=ReturnDocument.AFTER,
    )


def _qc_item_doc(config_id: str, source_id: str, dest_id: str | None, key: str, now) -> dict:
    return {
        "_id": str(uuid.uuid4()), "config_id": config_id,
        "source_connection_id": source_id, "s3_key": key, "dest_connection_id": dest_id,
        "status": "queued", "attempts": 0,
        "error": None, "error_kind": None,
        "qc": None, "ocr": None,
        "classification": None, "refined": None,  # phase 2 — để chỗ sẵn, chưa code logic
        "created_at": now, "started_at": None, "finished_at": None,
    }


async def _fail_sync_job(mongo: AsyncMongo, job_id: str, config_id: str, message: str) -> None:
    log.warning("qc_sync_job %s lỗi: %s", job_id, message)
    await mongo.db[config.COLL_QC_SYNC_JOB].update_one(
        {"_id": job_id}, {"$set": {"status": "error", "error": message}})
    await mongo.db[config.COLL_QC_SYNC_CONFIG].update_one(
        {"_id": config_id},
        {"$set": {"last_run_at": datetime.now(timezone.utc), "last_run_status": "error"}},
    )


async def process_qc_sync_job(mongo: AsyncMongo, job: dict) -> None:
    """Liệt kê STREAM prefix nguồn (không nạp cả kho vào RAM), insert_many mỗi
    trang — trùng key bị unique index từ chối êm (đây là toàn bộ cơ chế
    "không chạy lại file đã QC", không cần đọc trước). Heartbeat mỗi trang:
    job kẹt được stale-reclaim, list_token cho lần chạy lại (bị worker restart
    giữa chừng) tiếp gần chỗ dừng — CÙNG idiom import_job.py."""
    job_id, config_id = job["_id"], job["config_id"]
    source_id, prefix = job["source_connection_id"], job.get("prefix") or ""
    dest_id = job.get("dest_connection_id")

    conn = await mongo.db[config.COLL_S3_CONN].find_one({"_id": source_id})
    if not conn:
        await _fail_sync_job(mongo, job_id, config_id, "Không tìm thấy cấu hình nguồn")
        return

    client = build_client(conn)
    bucket = conn["bucket"]
    token = job.get("list_token")
    scanned = job.get("scanned", 0)
    enqueued = job.get("enqueued", 0)
    skipped = job.get("skipped", 0)

    try:
        while True:
            keys, next_token = await client.async_list_files_paginated(
                bucket, prefix=prefix, suffix_filter=".pdf",
                continuation_token=token, max_keys=CHUNK_SIZE,
            )
            if keys:
                now = datetime.now(timezone.utc)
                docs = [_qc_item_doc(config_id, source_id, dest_id, k, now) for k in keys]
                try:
                    res = await mongo.db[config.COLL_QC_ITEM].insert_many(docs, ordered=False)
                    n_ins = len(res.inserted_ids)
                except BulkWriteError as bwe:
                    n_ins = bwe.details.get("nInserted", 0)
                scanned += len(docs)
                enqueued += n_ins
                skipped += len(docs) - n_ins
            token = next_token
            # Đọc lại `cancel_requested` NGAY TRONG heartbeat (1 round-trip, không
            # thêm query riêng) — cho phép "Dừng" từ UI (routes/qc_sync.py) ngắt
            # vòng liệt kê ở checkpoint kế tiếp, không cần chờ quét hết prefix.
            updated = await mongo.db[config.COLL_QC_SYNC_JOB].find_one_and_update(
                {"_id": job_id},
                {"$set": {"started_at": datetime.now(timezone.utc), "list_token": token,
                          "scanned": scanned, "enqueued": enqueued, "skipped": skipped}},
                return_document=ReturnDocument.AFTER,
            )
            if updated and updated.get("cancel_requested"):
                await mongo.db[config.COLL_QC_SYNC_JOB].update_one(
                    {"_id": job_id}, {"$set": {"status": "cancelled"}})
                await mongo.db[config.COLL_QC_SYNC_CONFIG].update_one(
                    {"_id": config_id},
                    {"$set": {"last_run_at": datetime.now(timezone.utc), "last_run_status": "cancelled"}},
                )
                return
            if not token:
                break
    except Exception as e:  # noqa: BLE001
        await _fail_sync_job(mongo, job_id, config_id, str(e))
        return

    await mongo.db[config.COLL_QC_SYNC_JOB].update_one({"_id": job_id}, {"$set": {"status": "done"}})
    await mongo.db[config.COLL_QC_SYNC_CONFIG].update_one(
        {"_id": config_id},
        {"$set": {"last_run_at": datetime.now(timezone.utc), "last_run_status": "done"}},
    )


async def maybe_schedule_qc_sync_jobs(mongo: AsyncMongo) -> None:
    """Tự tạo `qc_sync_job` mới cho mỗi config `enabled` đã tới hạn quét lại
    (không có job queued/processing nào của config đó). Mỗi lượt là FULL
    re-scan prefix (S3 liệt kê theo thứ tự key, không theo mtime — không thể
    "chỉ lấy file mới" bằng resume token giữa các chu kỳ); dedup rẻ ở tầng
    insert (`qc_items` unique index) làm việc "không chạy lại file đã QC".
    Tự THROTTLE — không query mỗi vòng poll (cùng lý do `_sweep_dead`)."""
    global _last_schedule
    now_m = time.monotonic()
    if now_m - _last_schedule < SCHEDULE_INTERVAL:
        return
    _last_schedule = now_m

    now = datetime.now(timezone.utc)
    async for cfg in mongo.db[config.COLL_QC_SYNC_CONFIG].find({"enabled": True}):
        interval = cfg.get("interval_seconds") or config.QC_SYNC_DEFAULT_INTERVAL_SECONDS
        last_run = cfg.get("last_run_at")
        # pymongo trả datetime NAIVE khi đọc lại từ Mongo (BSON không giữ tzinfo)
        # dù lúc ghi là datetime.now(timezone.utc) (xem process_qc_sync_job) —
        # gắn lại UTC trước khi trừ, không thì `now - last_run` ném
        # "can't subtract offset-naive and offset-aware datetimes" NGAY VÒNG
        # LẶP ĐẦU TIÊN có config đã chạy 1 lần → worker CRASH-LOOP, kéo sập
        # LUÔN pipeline GCN chính (cùng 1 vòng lặp run(), không try/except
        # riêng từng claim — xem worker/main.py). Cùng pattern đã có ở
        # routes/audit.py._utc / routes/gcn.py.
        if last_run is not None and last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        due = not last_run or (now - last_run).total_seconds() >= interval
        if not due:
            continue
        active = await mongo.db[config.COLL_QC_SYNC_JOB].find_one(
            {"config_id": cfg["_id"], "status": {"$in": ["queued", "processing"]}}, {"_id": 1})
        if active:
            continue
        await mongo.db[config.COLL_QC_SYNC_JOB].insert_one({
            "_id": str(uuid.uuid4()), "config_id": cfg["_id"],
            "source_connection_id": cfg["source_connection_id"], "prefix": cfg.get("prefix") or "",
            "dest_connection_id": cfg.get("dest_connection_id"),
            "status": "queued", "started_at": None, "list_token": None,
            "scanned": 0, "enqueued": 0, "skipped": 0, "error": None,
            "cancel_requested": False, "created_at": now,
        })


# ── qc_item: QC + OCR + crop 1 file ─────────────────────────────────────────

async def claim_qc_item(mongo: AsyncMongo, proc_ttl: int) -> dict | None:
    """Atomic claim (mirror `_claim` gcn ở worker/main.py, không cần fairness
    round-robin — quy mô nhỏ hơn nhiều, 1 config = 1 hàng đợi riêng đã tách
    theo `qc_sync_job`)."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=proc_ttl)
    return await mongo.db[config.COLL_QC_ITEM].find_one_and_update(
        {"$or": [
            {"status": "queued"},
            {"status": "processing", "started_at": {"$lt": stale}},
        ]},
        {"$set": {"status": "processing", "started_at": now}, "$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )


async def _bump_daily(mongo: AsyncMongo, config_id: str | None, **deltas: int) -> None:
    """`$inc` nguyên tử `qc_stats_daily.counts.<key>`, upsert theo (config_id,
    date UTC hôm nay) — cùng idiom `batch_counters.bump`, chỉ thêm upsert vì
    doc ngày chưa chắc đã tồn tại. Tuần/tổng = aggregate cộng dồn các doc ngày
    ở tầng route (`routes/qc_sync.py`), KHÔNG count_documents trên `qc_items`."""
    if not config_id or not deltas:
        return
    inc = {f"counts.{k}": v for k, v in deltas.items() if v}
    if not inc:
        return
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await mongo.db[config.COLL_QC_STATS_DAILY].update_one(
        {"config_id": config_id, "date": date},
        {"$inc": inc, "$setOnInsert": {"config_id": config_id, "date": date}},
        upsert=True,
    )


def _safe_key_part(s: str) -> str:
    """Tên/khoá S3 phẳng (KHÔNG thư mục con) — "/" trong Số phát hành hay tên
    file gốc phải bị loại, không thì vô tình tạo "thư mục" ngoài ý muốn."""
    return "".join(c if c not in "/\\" else "-" for c in s).strip() or "gcn"


def _qc_cut_naming(item: dict):
    """Quy ước đặt tên RIÊNG cho QC Sync (khác pipeline GCN chính, xem
    `_build_cuts.naming_fn`): KHÔNG tạo thư mục con — ghi PHẲNG ngay tại bucket
    đích; tên file = "<tên GCN>_<tên file gốc>_cropped.pdf". "tên GCN" = Số
    phát hành (nếu đọc được) — thiếu thì dùng `stem` (`_build_cuts` đã tự
    fallback về `{item_id}-{ri+1}`)."""
    src_name = item["s3_key"].rsplit("/", 1)[-1]
    src_stem = src_name[:-4] if src_name.lower().endswith(".pdf") else src_name
    src_stem = _safe_key_part(src_stem)

    def _fn(ri: int, sph: str | None, stem: str) -> tuple[str, str]:
        gcn_name = _safe_key_part(sph or stem)
        base = f"{gcn_name}_{src_stem}_cropped"
        if ri:  # >=2 GCN trong cùng 1 file gốc — hậu tố index để khỏi đè nhau
            base = f"{base}_{ri + 1}"
        fname = f"{base}.pdf"
        return fname, fname

    return _fn


async def _finish_item(mongo: AsyncMongo, item_id: str, status: str, *, qc: dict | None = None,
                       ocr: dict | None = None, error: str | None = None,
                       error_kind: str | None = None) -> None:
    upd: dict = {"status": status, "error": error, "error_kind": error_kind,
                "finished_at": datetime.now(timezone.utc)}
    if qc is not None:
        upd["qc"] = qc
    if ocr is not None:
        upd["ocr"] = ocr
    await mongo.update_one(config.COLL_QC_ITEM, {"_id": item_id}, {"$set": upd})


async def process_qc_item(mongo: AsyncMongo, item: dict) -> str:
    """Tải PDF nguồn → QC (qc_client) → nếu đạt (pass/warn): OCR + crop, TÁI
    DÙNG NGUYÊN `run_job._pipeline`/`_build_cuts` (hàm thuần, không phụ thuộc
    doc `gcn`) — đích cắt là `dest_purpose="qc"` (S3 đích RIÊNG của kênh này).
    QC "fail" KHÔNG phải lỗi hệ thống — item vẫn `status="done"`, chỉ là
    không đạt (dữ liệu QC đã lưu đủ để thống kê)."""
    item_id = item["_id"]
    config_id = item.get("config_id")

    try:
        pdf_buf = await storage.get_pdf(item["s3_key"], item.get("source_connection_id"))
    except SourceObjectMissing as e:
        await _finish_item(mongo, item_id, "no_file", error=str(e), error_kind="missing_source")
        return "no_file"
    except Exception as e:  # noqa: BLE001 — SourceObjectUnavailable (tạm) + lỗi khác
        log.warning("qc_item %s tải file lỗi: %s", item_id, e)
        await _finish_item(mongo, item_id, "error", error=str(e), error_kind="transient")
        await _bump_daily(mongo, config_id, error=1)
        return "error"

    try:
        qc_res = await qc_client.check_pdf(pdf_buf.getvalue(), filename=item["s3_key"].rsplit("/", 1)[-1])
    except qc_client.QCError as e:
        log.warning("qc_item %s gọi QC scanner lỗi: %s", item_id, e)
        error_kind = "permanent" if isinstance(e, (qc_client.QCAuthError, qc_client.QCInputError)) else "transient"
        await _finish_item(mongo, item_id, "error", error=str(e), error_kind=error_kind)
        await _bump_daily(mongo, config_id, error=1)
        return "error"

    qc_doc = {
        "verdict": qc_res.verdict, "reasons": qc_res.reasons, "metrics": qc_res.metrics,
        "page_count": qc_res.page_count, "checked_at": datetime.now(timezone.utc),
    }
    await _bump_daily(mongo, config_id, scanned=1, **{qc_res.verdict: 1})

    if qc_res.verdict == "fail":
        await _finish_item(mongo, item_id, "done", qc=qc_doc)
        return "done"

    # QC đạt (pass/warn) → OCR trên PDF GỐC (không dùng ảnh đã nắn QC trả về —
    # quyết định v1, xem docs/features_issues.md: rủi ro thấp nhất, tái dùng
    # nguyên vẹn pipeline đã kiểm chứng).
    try:
        records, images = await run_job._pipeline(pdf_buf)
    except Exception as e:  # noqa: BLE001
        log.exception("qc_item %s OCR lỗi: %s", item_id, e)
        await _finish_item(mongo, item_id, "error", qc=qc_doc, error=str(e), error_kind="transient")
        await _bump_daily(mongo, config_id, error=1)
        return "error"

    records = records or []
    if not records:
        await _finish_item(mongo, item_id, "no_gcn", qc=qc_doc)
        await _bump_daily(mongo, config_id, no_gcn=1)
        return "no_gcn"

    ocr_doc = {"records_count": len(records), "cuts": []}
    try:
        cuts = await run_job._build_cuts(
            gcn_id=item_id, batch_id=config_id, images=images, records=records,
            dest_purpose="qc", naming_fn=_qc_cut_naming(item),
        )
        ocr_doc["cuts"] = cuts
    except DestinationNotConfigured as e:
        # Thiếu S3 đích QC là lỗi HỆ THỐNG (ảnh hưởng mọi cut của item này) —
        # đánh dấu "error" rõ ràng, khác lỗi cắt-1-trang cục bộ ở nhánh dưới.
        await _finish_item(mongo, item_id, "error", qc=qc_doc, ocr=ocr_doc,
                           error=str(e), error_kind="permanent")
        await _bump_daily(mongo, config_id, error=1)
        return "error"
    except Exception as e:  # noqa: BLE001
        log.warning("qc_item %s build_cuts lỗi: %s", item_id, e)

    await _finish_item(mongo, item_id, "done", qc=qc_doc, ocr=ocr_doc)
    await _bump_daily(mongo, config_id, ocr_done=1)
    return "done"
