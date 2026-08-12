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
from app.land_normalizer.classification.structural import classify_structural
from app.land_normalizer.pipeline import build_payload
from app.land_normalizer.registry import build_default_registry
from app.land_normalizer_adapter import first_entry, raw_record_from_cut
from app.s3_util import build_client
from app.storage import DestinationNotConfigured, SourceObjectMissing, SourceObjectUnavailable
from app.worker import run_job
from src.extentions.mongo_helper import AsyncMongo
from src.extentions.multimodal.normalize_dang_ky import normalize_extractions

# Đăng ký resolver 1 lần ở module scope (thuần, không state) — dùng lại cho mọi
# lần "làm mịn dữ liệu" (build Payload) + phân loại cấu trúc, xem
# _classify_cuts() và docs/algorithm.md §10.
_REGISTRY = build_default_registry()

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

# Cache config_id đang "tạm dừng xử lý" (items_paused=true) — refresh có
# throttle, KHÔNG query qc_sync_configs mỗi lần claim (claim_qc_item gọi
# nhiều lần/giây khi có backlog). Cùng idiom cache-throttle với
# worker/main.py._next_batch_id (RR_REFRESH_INTERVAL).
PAUSED_REFRESH_INTERVAL = float(os.getenv("WORKER_QC_PAUSED_REFRESH_SECONDS", "5"))
_paused_config_ids: set = set()
_paused_refreshed_at = 0.0


async def _get_paused_config_ids(mongo: AsyncMongo) -> set:
    global _paused_config_ids, _paused_refreshed_at
    now_m = time.monotonic()
    if now_m - _paused_refreshed_at >= PAUSED_REFRESH_INTERVAL:
        ids = await mongo.db[config.COLL_QC_SYNC_CONFIG].distinct("_id", {"items_paused": True})
        _paused_config_ids = set(ids)
        _paused_refreshed_at = now_m
    return _paused_config_ids


async def claim_qc_item(mongo: AsyncMongo, proc_ttl: int) -> dict | None:
    """Atomic claim (mirror `_claim` gcn ở worker/main.py, không cần fairness
    round-robin — quy mô nhỏ hơn nhiều, 1 config = 1 hàng đợi riêng đã tách
    theo `qc_sync_job`). Loại trừ item thuộc kênh đang "tạm dừng xử lý"
    (`items_paused=true`, xem routes/qc_sync.py PATCH .../configs/{id}) — file
    VẪN nằm nguyên `queued`, chỉ là chưa ai claim; tắt tạm dừng thì worker tự
    nhặt lại ở lượt claim kế tiếp (độ trễ tối đa `PAUSED_REFRESH_INTERVAL`).
    Item ĐÃ claim trước khi tạm dừng chạy nốt, không bị ngắt giữa chừng."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=proc_ttl)
    base = {"$or": [
        {"status": "queued"},
        {"status": "processing", "started_at": {"$lt": stale}},
    ]}
    paused = await _get_paused_config_ids(mongo)
    query = {"$and": [base, {"config_id": {"$nin": list(paused)}}]} if paused else base
    return await mongo.db[config.COLL_QC_ITEM].find_one_and_update(
        query,
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
    date = datetime.now(config.QC_VN_TZ).strftime("%Y-%m-%d")  # ngày lịch VN, không phải UTC
    await mongo.db[config.COLL_QC_STATS_DAILY].update_one(
        {"config_id": config_id, "date": date},
        {"$inc": inc, "$setOnInsert": {"config_id": config_id, "date": date}},
        upsert=True,
    )


def _safe_key_part(s: str) -> str:
    """Tên/khoá S3 phẳng (KHÔNG thư mục con) — "/" trong Số phát hành hay tên
    file gốc phải bị loại, không thì vô tình tạo "thư mục" ngoài ý muốn.
    Khoảng trắng (dấu cách) đổi thành "-" cho tên file gọn, dễ copy/paste,
    tránh lỗi ở tool/URL không quote khoảng trắng."""
    out = "".join(c if c not in "/\\" else "-" for c in s).strip()
    out = "-".join(out.split())  # gộp mọi chuỗi khoảng trắng (space/tab...) liên tiếp thành 1 "-"
    return out or "gcn"


def _qc_cut_naming(item: dict, channel_folder: str):
    """Quy ước đặt tên RIÊNG cho QC Sync (khác pipeline GCN chính, xem
    `_build_cuts.naming_fn`): ghi vào THƯ MỤC RIÊNG theo KÊNH đồng bộ
    (`channel_folder/`, xem lời gọi ở `process_qc_item` — lấy TRƯỚC khi cắt để
    có sẵn folder ngay từ `s3_key` đầu tiên, không phải rename sau); trong thư
    mục đó tên file PHẲNG = "<Số GCN đã OCR>_<tên thư mục gốc>_<tên file
    gốc>.pdf". "Số GCN" = Số phát hành (nếu đọc được) — thiếu thì dùng `stem`
    (`_build_cuts` đã tự fallback về `{item_id}-{ri+1}`). "tên thư mục gốc" =
    thư mục CHA trực tiếp của file trên kho nguồn — giữ lại làm 1 phần tên để
    còn phân biệt được nguồn gốc + giảm khả năng đè khi 2 thư mục khác nhau
    tình cờ có file trùng tên; tách theo `channel_folder` GIẢM HẲN rủi ro đè
    giữa 2 KÊNH khác nhau so với trước (xem
    features_issues.md#qc-flat-naming-collision — vẫn còn rủi ro đè trong
    CÙNG 1 kênh nếu 2 lần quét ra trùng cả Số GCN/thư mục gốc/tên file)."""
    parts = item["s3_key"].split("/")
    src_name = parts[-1]
    src_stem = src_name[:-4] if src_name.lower().endswith(".pdf") else src_name
    src_stem = _safe_key_part(src_stem)
    folder_name = _safe_key_part(parts[-2]) if len(parts) >= 2 else ""
    channel = _safe_key_part(channel_folder) if channel_folder else "khac"

    def _fn(ri: int, sph: str | None, stem: str) -> tuple[str, str]:
        gcn_name = _safe_key_part(sph or stem)
        base_parts = [gcn_name] + ([folder_name] if folder_name else []) + [src_stem]
        base = "_".join(base_parts)
        if ri:  # >=2 GCN trong cùng 1 file gốc — hậu tố index để khỏi đè nhau
            base = f"{base}_{ri + 1}"
        fname = f"{base}.pdf"
        return f"{channel}/{fname}", fname

    return _fn


def _extract_corrected_images(raw: dict) -> list[str]:
    """Lấy ảnh đã nắn phối cảnh/deskew (base64 PNG, cùng định dạng `images` mà
    `run_job._images_to_pdf` nhận) từ response `qc-scanner-server`: PDF nhiều
    trang trả `pages[].image` theo THỨ TỰ trang gốc; 1 trang/ảnh đơn trả thẳng
    `image` top-level. Trang nào thiếu `image` (server không nắn được trang đó)
    bị BỎ QUA — `_qc2_correct` coi cả cut là chưa nắn được nếu số ảnh thu về
    không khớp số trang gửi đi (an toàn hơn ghép thiếu trang)."""
    if "pages" in raw:
        return [p.get("image") for p in (raw.get("pages") or []) if p.get("image")]
    img = raw.get("image")
    return [img] if img else []


async def _qc2_correct(pdf_bytes: bytes, ri: int) -> tuple[bytes, dict | None]:
    """QC LẦN 2 — chấm lại + lấy ảnh đã nắn phối cảnh/deskew CHO CHÍNH bản cắt
    (khác QC lần 1 chấm PDF GỐC để quyết định có OCR hay không, xem
    docs/features_issues.md#qc-decide-raw-ocr — quyết định đó KHÔNG đổi, đây
    là bước RIÊNG áp dụng sau khi đã biết page_indices của từng GCN, để file
    XUẤT RA đẹp hơn bản render thô). Lỗi/không nắn được ở BẤT KỲ bước nào →
    FALLBACK về `pdf_bytes` gốc (không chặn pipeline, chỉ là bản cắt không có
    hiệu ứng nắn) — ghi lại verdict/lỗi vào `extra["qc2"]` để biết cut nào
    chưa nắn được. HÀM NÀY KHÔNG ĐƯỢC RAISE — `_build_cuts` gọi trong vòng lặp
    NHIỀU cut của 1 item, 1 exception lọt ra sẽ làm hỏng CẢ item (toàn bộ
    `cuts[]` còn lại bị bỏ qua, `process_qc_item` nuốt lỗi ở tầng ngoài và
    đánh dấu `status="done"` nhưng KHÔNG có file cắt nào — bug đã gặp thực
    tế khi chỉ bắt `qc_client.QCError`, bỏ sót lỗi bất ngờ như JSON hỏng/
    response sai hợp đồng). Vì vậy bọc try/except Exception RỘNG ở CẢ 2 nửa
    hàm (gọi QC-2, và xử lý ảnh trả về)."""
    try:
        qc2 = await qc_client.check_pdf(pdf_bytes, filename=f"cut-{ri}.pdf")
    except Exception as e:  # noqa: BLE001 — bao gồm cả QCError lẫn lỗi bất ngờ, xem docstring
        log.warning("qc_item cut %s: QC-2 lỗi, giữ bản cắt gốc: %s", ri, e)
        return pdf_bytes, {"qc2": {"verdict": None, "error": str(e)}}

    try:
        qc2_doc = {"verdict": qc2.verdict, "reasons": qc2.reasons}
        images_b64 = _extract_corrected_images(qc2.raw)
        expected = qc2.page_count or 1
        if not images_b64 or len(images_b64) != expected:
            qc2_doc["error"] = "missing_corrected_image"
            return pdf_bytes, {"qc2": qc2_doc}

        corrected_pdf = run_job._images_to_pdf(images_b64)
        if not corrected_pdf:
            qc2_doc["error"] = "rebuild_pdf_failed"
            return pdf_bytes, {"qc2": qc2_doc}

        return corrected_pdf, {"qc2": qc2_doc}
    except Exception as e:  # noqa: BLE001 — response sai hợp đồng dự kiến (thiếu field, sai kiểu...)
        log.warning("qc_item cut %s: QC-2 xử lý ảnh lỗi, giữ bản cắt gốc: %s", ri, e)
        return pdf_bytes, {"qc2": {"verdict": qc2.verdict, "error": str(e)}}


async def _classify_meta_of(mongo: AsyncMongo, config_id: str | None) -> tuple[str, str]:
    """`(ward_code, dest_bucket)` cần cho `raw_record_from_cut` khi "làm mịn dữ
    liệu": `ward_code` = `qc_sync_configs.name` (kênh QC Sync đặt tên trùng mã
    Phường/Xã, quy ước đã có, xem WardSyncPanel/`_ward_map` trong
    routes/qc_sync.py) dùng làm `DonDangKy.XaId` (không có nguồn thu thập thực
    địa riêng như dự án gốc `vpdd-don-ai`, xem docs/algorithm.md §10);
    `dest_bucket` = bucket MinIO ĐÍCH thật của kênh (`s3_connections.bucket` của
    `dest_connection_id`) dùng cho `HoSoQuet.BucketName`."""
    if not config_id:
        return "", ""
    cfg = await mongo.db[config.COLL_QC_SYNC_CONFIG].find_one(
        {"_id": config_id}, {"name": 1, "dest_connection_id": 1})
    if not cfg:
        return "", ""
    ward_code = cfg.get("name") or ""
    dest_bucket = ""
    dest_id = cfg.get("dest_connection_id")
    if dest_id:
        conn = await mongo.db[config.COLL_S3_CONN].find_one({"_id": dest_id}, {"bucket": 1})
        dest_bucket = (conn or {}).get("bucket") or ""
    return ward_code, dest_bucket


def _classify_cuts(records: list, cuts: list[dict], ward_code: str, dest_bucket: str, item_id: str) -> None:
    """"Làm mịn dữ liệu" (build Payload) + phân loại cấu trúc cho MỖI cut, port
    từ `vpdd-don-ai` (docs/algorithm.md §10) — MUTATE `cuts` tại chỗ, ghi
    `cut["refined"]`/`cut["classification"]`. 1 cut lỗi KHÔNG chặn cut khác
    (try/except riêng từng cut, giữ tinh thần "1 GCN lỗi không chặn cả batch")."""
    for cut in cuts:
        try:
            ri = cut.get("index")
            record = records[ri] if isinstance(ri, int) and 0 <= ri < len(records) else {}
            entry = first_entry(record)
            raw = raw_record_from_cut(entry, cut, ward_code=ward_code, item_id=item_id,
                                      dest_bucket=dest_bucket)
            build = build_payload([raw], _REGISTRY)
            cut["refined"] = build.payload.model_dump(mode="json")
            cut["classification"] = classify_structural(build.payload).model_dump(mode="json")
        except Exception as e:  # noqa: BLE001
            log.warning("qc_item %s cut %s: lỗi làm mịn/phân loại: %s", item_id, cut.get("index"), e)
            cut["refined"] = None
            cut["classification"] = None


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
    """Tải PDF nguồn → QC LẦN 1 (qc_client, trên PDF GỐC) → nếu đạt (pass/warn):
    OCR + crop, TÁI DÙNG NGUYÊN `run_job._pipeline`/`_build_cuts` (hàm thuần,
    không phụ thuộc doc `gcn`) — đích cắt là `dest_purpose="qc"` (S3 đích RIÊNG
    của kênh này). MỖI bản cắt còn qua QC LẦN 2 (`_qc2_correct`, truyền vào
    `_build_cuts` qua `correct_fn`) để lấy ảnh đã nắn phối cảnh/deskew — file
    lưu S3 đích là bản ĐÃ NẮN (fallback về bản thô nếu QC-2 lỗi/không nắn
    được, xem `_qc2_correct`). QC "fail" (lần 1) KHÔNG phải lỗi hệ thống — item
    vẫn `status="done"`, chỉ là không đạt (dữ liệu QC đã lưu đủ để thống kê).

    RESUMABLE theo yêu cầu thực tế (lỗi mạng/QC không nên bắt quét QC lại từ
    đầu cho file ĐÃ CÓ verdict): nếu `item["qc"]` đã có sẵn (do 1 lần chạy
    trước đã gọi QC thành công nhưng hỏng ở bước SAU đó — OCR/build_cuts —
    hoặc do `/items/{id}/retry` giữ nguyên field này), BỎ QUA gọi lại
    `qc_client.check_pdf` và KHÔNG bump lại `qc_stats_daily` (đã cộng ở lần
    chạy QC thành công trước đó, cộng lại sẽ đếm trùng) — chỉ chạy tiếp từ
    OCR. Chỉ gọi lại QC khi thật sự CHƯA có verdict (lần đầu, hoặc lần trước
    lỗi ngay ở bước gọi QC — network/query lỗi)."""
    item_id = item["_id"]
    config_id = item.get("config_id")

    try:
        pdf_buf = await storage.get_pdf(item["s3_key"], item.get("source_connection_id"))
    except SourceObjectMissing as e:
        await _finish_item(mongo, item_id, "no_file", error=str(e), error_kind="missing_source")
        await _bump_daily(mongo, config_id, no_file=1)
        return "no_file"
    except Exception as e:  # noqa: BLE001 — SourceObjectUnavailable (tạm) + lỗi khác
        log.warning("qc_item %s tải file lỗi: %s", item_id, e)
        await _finish_item(mongo, item_id, "error", error=str(e), error_kind="transient")
        await _bump_daily(mongo, config_id, error=1)
        return "error"

    qc_doc = item.get("qc")
    if qc_doc and qc_doc.get("verdict"):
        log.info("qc_item %s: tái dùng verdict QC đã có (%s), không gọi lại QC scanner",
                 item_id, qc_doc["verdict"])
    else:
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
            "page_count": qc_res.page_count, "pages": qc_res.pages,
            "checked_at": datetime.now(timezone.utc),
        }
        await _bump_daily(mongo, config_id, scanned=1, **{qc_res.verdict: 1})

    if qc_doc["verdict"] == "fail":
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

    # Chuẩn hoá NGAY (định dạng ngày tháng...) — cùng bước đầu tiên pipeline
    # GCN chính áp dụng trước khi lưu (`run_job.process_doc`), để dữ liệu OCR
    # lưu ở đây nhất quán với `gcn.extractions`.
    normalize_extractions(records)

    # Lưu NGUYÊN VẸN kết quả OCR (không chỉ đếm số lượng) — người dùng cần
    # tra cứu lại toàn bộ thông tin đã trích xuất (chủ sử dụng, thửa đất...),
    # không riêng Số phát hành đã tách ra field `cuts[].so_phat_hanh`.
    ocr_doc = {"records_count": len(records), "records": records, "cuts": []}
    try:
        # Lấy ward_code TRƯỚC khi cắt — vừa dùng làm THƯ MỤC ĐÍCH theo kênh
        # (_qc_cut_naming), vừa dùng cho "làm mịn dữ liệu" bên dưới (1 query,
        # không tách rời như trước — trước đây chỉ gọi SAU _build_cuts vì lúc
        # đó chưa cần cho naming_fn).
        ward_code, dest_bucket = await _classify_meta_of(mongo, config_id)
        cuts = await run_job._build_cuts(
            gcn_id=item_id, batch_id=config_id, images=images, records=records,
            dest_purpose="qc", naming_fn=_qc_cut_naming(item, ward_code or config_id),
            correct_fn=_qc2_correct,
        )
        # "Làm mịn dữ liệu" (build Payload) + phân loại cấu trúc — TỰ ĐỘNG, miễn
        # phí (thuần Python, không gọi API nào) — port từ vpdd-don-ai, xem
        # docs/algorithm.md §10. Người dùng có thể chạy lại thủ công sau qua
        # POST /items/{id}/cuts/{i}/reclassify (routes/qc_sync.py).
        _classify_cuts(records, cuts, ward_code=ward_code, dest_bucket=dest_bucket, item_id=item_id)
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
    # ocr_done = số ITEM cắt được; cuts_created = số FILE cắt thật (1 item có
    # thể ra >1 file nếu nhiều GCN trong cùng 1 file gốc) — 2 số liệu khác
    # nhau, biểu đồ "số file đã cắt" ở Tổng quan cần số THỨ HAI.
    # no_sph = trong số file ĐÃ CẮT ĐƯỢC, bao nhiêu file KHÔNG đọc được Số
    # phát hành (`cut["so_phat_hanh"]` rỗng — khác `no_gcn`: đây vẫn tìm thấy
    # trang GCN và cắt được, chỉ là không đọc ra được số trên đó). KHÔNG cộng
    # dồn vào biểu đồ stacked OCR_SERIES vì không loại trừ lẫn nhau với
    # `ocr_done` (1 cut vừa tính vào ocr_done vừa có thể thiếu SPH) — hiện
    # riêng ở KPI (`OcrKpiStrip`), xem từng file cụ thể qua field `cuts[]`
    # (`qc_items.ocr.cuts[].so_phat_hanh` null) ở bảng theo dõi.
    n_no_sph = sum(1 for c in ocr_doc["cuts"] if not c.get("so_phat_hanh"))
    await _bump_daily(mongo, config_id, ocr_done=1, cuts_created=len(ocr_doc["cuts"]), no_sph=n_no_sph)
    return "done"
