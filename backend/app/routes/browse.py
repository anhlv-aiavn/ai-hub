"""Duyệt + import PDF trực tiếp từ kho MinIO nguồn (không copy, chỉ tham chiếu
path gốc). operator trở lên, khóa theo chi nhánh — xem PLAN_.md §Phân quyền."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from app.branches import is_valid_branch
from app.db import batches, gcns, import_jobs, s3_connections
from app.deps import is_admin, require_operator
from app.s3_util import async_head_object, async_list_folder, build_client

router = APIRouter(prefix="/v1/browse", tags=["browse"], dependencies=[Depends(require_operator)])


@router.get("/sources")
async def list_sources():
    rows = await s3_connections().find(
        {"role": "source"}, {"_id": 1, "name": 1},
    ).sort("name", 1).to_list(length=200)
    return {"sources": [{"id": r["_id"], "name": r.get("name")} for r in rows]}


@router.get("/{source_id}")
async def browse_folder(source_id: str, prefix: str = "", token: str | None = None):
    conn = await s3_connections().find_one({"_id": source_id, "role": "source"})
    if not conn:
        raise HTTPException(status_code=404, detail="Không tìm thấy nguồn")
    client = build_client(conn)
    folders, files, next_token = await async_list_folder(client, conn["bucket"], prefix, token)
    return {"prefix": prefix, "folders": folders, "files": files, "next_token": next_token}


class ImportIn(BaseModel):
    prefix: str | None = None
    recursive: bool = False
    keys: list[str] | None = None
    branch: str
    batch_id: str | None = None


def _gcn_doc(batch_id: str, branch: str, source_id: str, key: str, meta: dict, now) -> dict:
    return {
        "_id": str(uuid.uuid4()), "batch_id": batch_id,
        "filename": key.rsplit("/", 1)[-1], "s3_key": key, "branch": branch,
        "status": "queued", "page_count": 0, "extractions": [],
        "extracted_so_phat_hanhs": [], "group_key": None, "summary": {},
        "review": {"display_name": None, "overrides": {}, "status": "unreviewed",
                   "reviewer": None, "at": None, "lock": None, "version": 0},
        "source_connection_id": source_id, "source_etag": meta.get("etag"),
        "source_mtime": meta.get("last_modified"), "attempts": 0,
        "created_at": now,
    }


async def _ensure_batch(batch_id: str | None, branch: str, name: str | None, status: str, user: dict):
    now = datetime.now(timezone.utc)
    if batch_id:
        existing = await batches().find_one({"_id": batch_id}, {"branch": 1})
        if not existing:
            raise HTTPException(status_code=404, detail="Không tìm thấy lô để nối")
        if not is_admin(user) and existing.get("branch") != user.get("branch"):
            raise HTTPException(status_code=403, detail="Không thuộc chi nhánh của bạn")
        return batch_id, existing.get("branch")
    batch_id = str(uuid.uuid4())
    await batches().insert_one({
        "_id": batch_id, "name": name or branch or now.strftime("Lô %d/%m %H:%M"),
        "branch": branch, "created_at": now, "file_count": 0, "status": status,
    })
    return batch_id, branch


@router.post("/{source_id}/import")
async def import_from_minio(source_id: str, body: ImportIn, user: dict = Depends(require_operator)):
    conn = await s3_connections().find_one({"_id": source_id, "role": "source"})
    if not conn:
        raise HTTPException(status_code=404, detail="Không tìm thấy nguồn")

    # Operator: LUÔN ép chi nhánh của họ (không tin giá trị client gửi). Admin: theo chọn.
    branch = body.branch
    if not is_admin(user):
        branch = user.get("branch")
    if not await is_valid_branch(branch):
        raise HTTPException(status_code=400, detail="Chi nhánh không hợp lệ")

    if body.keys:
        batch_id, branch = await _ensure_batch(body.batch_id, branch, None, "processing", user)
        client = build_client(conn)
        created, skipped, requeued = 0, 0, 0
        now = datetime.now(timezone.utc)
        for key in body.keys:
            try:
                meta = await async_head_object(client, conn["bucket"], key)
            except Exception:  # noqa: BLE001
                meta = {}
            existing = await gcns().find_one(
                {"source_connection_id": source_id, "s3_key": key, "batch_id": batch_id},
                {"source_etag": 1},
            )
            if existing:
                # Nguồn đổi nội dung (etag khác, key giữ nguyên) → xử lý lại thay vì
                # bỏ qua vĩnh viễn (PLAN_PHASE2.md §①). Etag giống hoặc thiếu → skip.
                if meta.get("etag") and existing.get("source_etag") and meta["etag"] != existing["source_etag"]:
                    await gcns().update_one({"_id": existing["_id"]}, {"$set": {
                        "status": "queued", "error": None,
                        "source_etag": meta.get("etag"), "source_mtime": meta.get("last_modified"),
                    }})
                    requeued += 1
                else:
                    skipped += 1
                continue
            doc = _gcn_doc(batch_id, branch, source_id, key, meta, now)
            try:
                await gcns().insert_one(doc)
                created += 1
            except DuplicateKeyError:  # race hiếm: 2 request cùng lúc chèn cùng key
                skipped += 1
        if created:
            await batches().update_one(
                {"_id": batch_id},
                {"$inc": {"file_count": created}, "$set": {"status": "processing"}},
            )
        return {"batch_id": batch_id, "created": created, "skipped": skipped, "requeued": requeued}

    if not body.prefix and not body.recursive:
        raise HTTPException(status_code=400, detail="Cần 'keys' hoặc 'prefix'+recursive")

    batch_id, branch = await _ensure_batch(body.batch_id, branch, None, "importing", user)
    if body.batch_id:  # nối vào lô đang chạy: đánh dấu importing lại
        await batches().update_one({"_id": batch_id}, {"$set": {"status": "importing"}})
    job_id = str(uuid.uuid4())
    await import_jobs().insert_one({
        "_id": job_id, "batch_id": batch_id, "source_connection_id": source_id,
        "prefix": body.prefix or "", "branch": branch, "status": "queued",
        "started_at": None, "list_token": None, "inserted": 0, "skipped": 0,
        "error": None, "created_at": datetime.now(timezone.utc),
    })
    return {"batch_id": batch_id, "status": "importing", "job_id": job_id}
