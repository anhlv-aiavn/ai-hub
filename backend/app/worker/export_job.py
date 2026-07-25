"""Xử lý `export_jobs` — xuất CSV nền, đọc Mongo qua cursor (KHÔNG `.to_list()`
cả tập, không cap 5000 dòng như `/v1/gcn/export.csv` đồng bộ hiện có). Cùng idiom
claim atomic + stale-reclaim với `gcns`/`import_jobs`. Xem PLAN_PHASE2.md §⑦
— ghi 1 lần `put_object` cuối (không multipart streaming lên S3); RAM chỉ giữ
CSV text tích lũy + 1 doc tại một thời điểm, không giữ toàn bộ doc trong RAM."""

import csv
import io
import logging
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

from app import config, storage
from app.flatten import COLUMNS as FLAT_COLUMNS, flatten_doc

log = logging.getLogger(__name__)

_LEAN_PROJ = {"extractions": 1, "cuts": 1, "review": 1, "filename": 1,
             "status": 1, "page_count": 1, "created_at": 1}


async def claim_export_job(mongo, proc_ttl: int) -> dict | None:
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=proc_ttl)
    return await mongo.db[config.COLL_EXPORT_JOB].find_one_and_update(
        {"$or": [
            {"status": "queued"},
            {"status": "processing", "started_at": {"$lt": stale}},
        ]},
        {"$set": {"status": "processing", "started_at": now}},
        return_document=ReturnDocument.AFTER,
    )


def _build_filter(f: dict) -> dict:
    flt: dict = {}
    if f.get("batch_id"):
        flt["batch_id"] = f["batch_id"]
    if f.get("branch"):
        flt["branch"] = f["branch"]
    # Mặc định CHỈ hồ sơ đã xong — như bảng/CSV đồng bộ (routes/gcn.py `_rows_filter`):
    # hồ sơ đang Chờ/Đang xử lý/Lỗi chưa có thửa nào để xuất, nếu không lọc thì CSV
    # lẫn hàng loạt dòng trống. Truyền `status` cụ thể vẫn ghi đè được.
    flt["status"] = f.get("status") or "done"
    if f.get("review"):
        flt["review.status"] = f["review"]
    return flt


async def process_export_job(mongo, job: dict) -> None:
    job_id = job["_id"]
    flt = _build_filter(job.get("filter") or {})

    buf = io.StringIO()
    buf.write("﻿")  # BOM để Excel đọc UTF-8 đúng — nhất quán với export.csv đồng bộ
    writer = csv.DictWriter(buf, fieldnames=FLAT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    row_count = 0
    try:
        cursor = mongo.db[config.COLL_GCN].find(flt, _LEAN_PROJ).sort("created_at", 1)
        async for doc in cursor:
            for row in flatten_doc(doc):
                writer.writerow({c: row.get(c, "") for c in FLAT_COLUMNS})
                row_count += 1
        data = buf.getvalue().encode("utf-8")
        file_key = f"exports/{job_id}.csv"
        await storage.put_object(file_key, data)
    except Exception as e:  # noqa: BLE001
        log.exception("export_job %s lỗi: %s", job_id, e)
        await mongo.db[config.COLL_EXPORT_JOB].update_one(
            {"_id": job_id}, {"$set": {"status": "error", "error": str(e)}})
        return

    await mongo.db[config.COLL_EXPORT_JOB].update_one(
        {"_id": job_id},
        {"$set": {"status": "done", "file_key": file_key, "row_count": row_count,
                  "finished_at": datetime.now(timezone.utc)}},
    )
