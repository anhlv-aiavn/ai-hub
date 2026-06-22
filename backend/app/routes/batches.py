"""Lô (batch): tạo lô từ nhiều PDF, liệt kê, rollup trạng thái."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app import config, storage
from app.db import batches, gcns
from app.deps import require_key

router = APIRouter(prefix="/v1/batches", tags=["batches"], dependencies=[Depends(require_key)])


@router.post("")
async def create_batch(
    files: list[UploadFile] = File(...),
    name: str | None = Form(default=None),
):
    if not files:
        raise HTTPException(status_code=400, detail="Không có tệp nào")

    batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    created: list[str] = []

    for f in files:
        data = await f.read()
        if not data:
            continue
        gcn_id = str(uuid.uuid4())
        s3_key = f"{batch_id}/{gcn_id}.pdf"
        await storage.put_pdf(s3_key, data)
        try:
            pages = storage.page_count(data)
        except Exception:
            pages = 0
        await gcns().insert_one({
            "_id": gcn_id,
            "batch_id": batch_id,
            "filename": f.filename or f"{gcn_id}.pdf",
            "s3_key": s3_key,
            "status": "queued",
            "page_count": pages,
            "extractions": [],
            "extracted_so_phat_hanhs": [],
            "group_key": None,
            "summary": {},
            "review": {"display_name": None, "overrides": {},
                       "status": "unreviewed", "reviewer": None, "at": None},
            "created_at": now,
        })
        # Không cần enqueue: worker tự poll & claim doc status=queued từ Mongo.
        created.append(gcn_id)

    if not created:
        raise HTTPException(status_code=400, detail="Tệp rỗng/không hợp lệ")

    await batches().insert_one({
        "_id": batch_id,
        "name": name or now.strftime("Lô %d/%m %H:%M"),
        "created_at": now,
        "file_count": len(created),
        "status": "processing",
    })
    return {"batch_id": batch_id, "file_count": len(created), "gcn_ids": created}


@router.get("")
async def list_batches(limit: int = 50):
    rows = await batches().find().sort("created_at", -1).limit(limit).to_list(length=limit)
    out = []
    for b in rows:
        counts = await _status_counts(b["_id"])
        out.append({
            "batch_id": b["_id"], "name": b.get("name"), "status": b.get("status"),
            "file_count": b.get("file_count", 0), "created_at": b.get("created_at"),
            "counts": counts,
        })
    return {"batches": out}


@router.get("/{batch_id}")
async def get_batch(batch_id: str):
    b = await batches().find_one({"_id": batch_id})
    if not b:
        raise HTTPException(status_code=404, detail="Không tìm thấy lô")
    return {
        "batch_id": b["_id"], "name": b.get("name"), "status": b.get("status"),
        "file_count": b.get("file_count", 0), "created_at": b.get("created_at"),
        "counts": await _status_counts(batch_id),
    }


async def _status_counts(batch_id: str) -> dict:
    pipeline = [
        {"$match": {"batch_id": batch_id}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]
    rows = await gcns().aggregate(pipeline).to_list(length=None)
    return {r["_id"]: r["n"] for r in rows}
