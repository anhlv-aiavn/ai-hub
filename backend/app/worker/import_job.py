"""Xử lý `import_jobs` — worker-queue cho import THƯ MỤC (prefix+recursive) từ
kho MinIO nguồn. Cùng idiom Mongo-as-queue với `gcns` ([worker/main.py:24-37]):
claim atomic + stale-reclaim → job kẹt vì worker/API restart tự chạy tiếp, không
cần code resume riêng. Liệt kê STREAM theo trang (không nạp hết vào RAM),
insert_many(ordered=False) mỗi chunk, heartbeat mỗi chunk (bump started_at)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError

from app import config
from app.s3_util import build_client
from src.extentions.mongo_helper import AsyncMongo

log = logging.getLogger(__name__)

CHUNK_SIZE = 500


async def claim_import_job(mongo: AsyncMongo, proc_ttl: int) -> dict | None:
    """Atomic claim 1 job: queued, hoặc processing đã treo quá proc_ttl."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=proc_ttl)
    return await mongo.db[config.COLL_IMPORT_JOB].find_one_and_update(
        {"$or": [
            {"status": "queued"},
            {"status": "processing", "started_at": {"$lt": stale}},
        ]},
        {"$set": {"status": "processing", "started_at": now}},
        return_document=ReturnDocument.AFTER,
    )


def _gcn_doc(batch_id: str, branch: str | None, source_id: str, key: str, now) -> dict:
    return {
        "_id": str(uuid.uuid4()), "batch_id": batch_id,
        "filename": key.rsplit("/", 1)[-1], "s3_key": key, "branch": branch,
        "status": "queued", "page_count": 0, "extractions": [],
        "extracted_so_phat_hanhs": [], "group_key": None, "summary": {},
        "review": {"display_name": None, "overrides": {}, "status": "unreviewed",
                   "reviewer": None, "at": None, "lock": None, "version": 0},
        "source_connection_id": source_id, "source_etag": None, "source_mtime": None,
        "attempts": 0, "created_at": now,
    }


async def _fail(mongo: AsyncMongo, job_id: str, batch_id: str, message: str) -> None:
    log.warning("import_job %s lỗi: %s", job_id, message)
    await mongo.db[config.COLL_IMPORT_JOB].update_one(
        {"_id": job_id}, {"$set": {"status": "error", "error": message}})
    await mongo.db[config.COLL_BATCH].update_one(
        {"_id": batch_id}, {"$set": {"import_status": "failed"}})


async def process_import_job(mongo: AsyncMongo, job: dict) -> None:
    job_id, batch_id = job["_id"], job["batch_id"]
    source_id, prefix, branch = job["source_connection_id"], job.get("prefix") or "", job.get("branch")

    conn = await mongo.db[config.COLL_S3_CONN].find_one({"_id": source_id})
    if not conn:
        await _fail(mongo, job_id, batch_id, "Không tìm thấy cấu hình nguồn")
        return

    client = build_client(conn)
    bucket = conn["bucket"]
    token = job.get("list_token")
    inserted, skipped = job.get("inserted", 0), job.get("skipped", 0)

    try:
        while True:
            keys, next_token = await client.async_list_files_paginated(
                bucket, prefix=prefix, suffix_filter=".pdf",
                continuation_token=token, max_keys=CHUNK_SIZE,
            )
            if keys:
                now = datetime.now(timezone.utc)
                docs = [_gcn_doc(batch_id, branch, source_id, k, now) for k in keys]
                try:
                    res = await mongo.db[config.COLL_GCN].insert_many(docs, ordered=False)
                    n_ins = len(res.inserted_ids)
                except BulkWriteError as bwe:
                    n_ins = bwe.details.get("nInserted", 0)
                inserted += n_ins
                skipped += len(docs) - n_ins
                if n_ins:
                    await mongo.db[config.COLL_BATCH].update_one(
                        {"_id": batch_id}, {"$inc": {"file_count": n_ins}})
            token = next_token
            # Heartbeat mỗi chunk: job đang khỏe không bị stale-reclaim nhặt nhầm
            # giữa lúc liệt kê lâu; list_token cho lần chạy lại tiếp gần chỗ dừng.
            await mongo.db[config.COLL_IMPORT_JOB].update_one(
                {"_id": job_id},
                {"$set": {"started_at": datetime.now(timezone.utc), "list_token": token,
                          "inserted": inserted, "skipped": skipped}},
            )
            if not token:
                break
    except Exception as e:  # noqa: BLE001
        await _fail(mongo, job_id, batch_id, str(e))
        return

    await mongo.db[config.COLL_IMPORT_JOB].update_one({"_id": job_id}, {"$set": {"status": "done"}})
    await mongo.db[config.COLL_BATCH].update_one({"_id": batch_id}, {"$set": {"status": "processing"}})
