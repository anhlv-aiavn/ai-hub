"""Xử lý 1 GCN (1 PDF): tải PDF từ MinIO → detect_and_extract (VLM .199) →
normalize → ghi extractions/group_key/summary vào Mongo → publish event SSE.

RQ gọi `process_gcn(gcn_id)` (đồng bộ); bên trong chạy asyncio.run cho pipeline async.
Tạo Mongo client MỚI trong mỗi job (tránh chia sẻ event loop giữa các job)."""

import asyncio
import logging
from datetime import datetime, timezone

from app import config
from app.bus import publish_sync
from app.summary import collect_so_phat_hanhs, group_key_of, summarize
from src.extentions.minio_helper import minio_client
from src.extentions.mongo_helper import AsyncMongo
from src.extentions.multimodal.normalize_dang_ky import normalize_extractions
from src.extentions.multimodal.pipeline import detect_and_extract

log = logging.getLogger(__name__)


def process_gcn(gcn_id: str) -> str:
    return asyncio.run(_process(gcn_id))


async def _emit(mongo: AsyncMongo, gcn_id: str, status: str, **extra) -> None:
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "status": status, **extra})


async def _process(gcn_id: str) -> str:
    mongo = AsyncMongo()
    doc = await mongo.find_one(config.COLL_GCN, {"_id": gcn_id})
    if not doc:
        await mongo.close_connection()
        return "not_found"

    batch_id = doc.get("batch_id")
    await mongo.update_one(
        config.COLL_GCN, {"_id": gcn_id},
        {"$set": {"status": "processing", "started_at": datetime.now(timezone.utc)}},
    )
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id, "status": "processing"})

    try:
        pdf_buf = await minio_client.async_get_object(config.AIHUB_BUCKET, doc["s3_key"])
        records = await detect_and_extract(pdf_buf)
    except Exception as e:  # noqa: BLE001
        log.exception("process_gcn %s failed: %s", gcn_id, e)
        await mongo.update_one(
            config.COLL_GCN, {"_id": gcn_id},
            {"$set": {"status": "error", "error": str(e),
                      "finished_at": datetime.now(timezone.utc)}},
        )
        publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                      "status": "error", "error": str(e)})
        await _rollup(mongo, batch_id)
        await mongo.close_connection()
        return "error"

    records = records or []
    normalize_extractions(records)

    first = records[0] if records else {}
    page_count = first.get("page_count", doc.get("page_count", 0))
    skip_reason = first.get("skip_reason")
    has_error = any(r.get("error") for r in records if isinstance(r, dict))

    if skip_reason:
        status = "skip"
        err = first.get("error")
    elif not records or has_error:
        status = "error"
        err = next((r.get("error") for r in records if r.get("error")), None) or "no_gcn_detected"
    else:
        status = "done"
        err = None

    update = {
        "status": status,
        "error": err,
        "extractions": records,
        "page_count": page_count,
        "skip_reason": skip_reason,
        "group_key": group_key_of(records),
        "extracted_so_phat_hanhs": collect_so_phat_hanhs(records),
        "summary": summarize(records),
        "finished_at": datetime.now(timezone.utc),
    }
    await mongo.update_one(config.COLL_GCN, {"_id": gcn_id}, {"$set": update})
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                  "status": status, "group_key": update["group_key"]})
    await _rollup(mongo, batch_id)
    await mongo.close_connection()
    return status


async def _rollup(mongo: AsyncMongo, batch_id) -> None:
    """Cập nhật trạng thái lô theo số GCN còn queued/processing."""
    if not batch_id:
        return
    db = mongo.db
    pending = await db[config.COLL_GCN].count_documents(
        {"batch_id": batch_id, "status": {"$in": ["queued", "processing"]}}
    )
    status = "done" if pending == 0 else "processing"
    await mongo.update_one(config.COLL_BATCH, {"_id": batch_id}, {"$set": {"status": status}})
    publish_sync({"type": "batch", "batch_id": batch_id, "status": status, "pending": pending})
