"""Duyệt + import PDF trực tiếp từ kho MinIO nguồn (không copy, chỉ tham chiếu
path gốc). operator trở lên, khóa theo chi nhánh — xem PLAN_.md §Phân quyền."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from app.audit import AuditAction, log_action
from app.batch_counters import bump, init_counts
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


async def _sync_import_files(
    batch_id: str, branch: str, source_id: str, items: list[tuple[str, dict]],
) -> tuple[int, int, int]:
    """Chèn/đối chiếu ĐỒNG BỘ 1 danh sách (key, meta). Dùng chung cho `keys` (chọn
    lẻ, meta lấy qua head_object) và file lẻ ở cấp gốc khi sharding (§5 — meta đã
    có sẵn từ `async_list_folder`, không head_object lại)."""
    created, skipped, requeued = 0, 0, 0
    now = datetime.now(timezone.utc)
    for key, meta in items:
        existing = await gcns().find_one(
            {"source_connection_id": source_id, "s3_key": key, "batch_id": batch_id},
            {"source_etag": 1, "status": 1},
        )
        if existing:
            # Nguồn đổi nội dung (etag khác, key giữ nguyên) → xử lý lại thay vì
            # bỏ qua vĩnh viễn (PLAN_PHASE2.md §①). Etag giống hoặc thiếu → skip.
            if meta.get("etag") and existing.get("source_etag") and meta["etag"] != existing["source_etag"]:
                await gcns().update_one({"_id": existing["_id"]}, {"$set": {
                    "status": "queued", "error": None, "error_kind": None,
                    "source_etag": meta.get("etag"), "source_mtime": meta.get("last_modified"),
                }})
                prev_status = existing.get("status") or "done"
                await bump(batches(), batch_id, **{prev_status: -1, "queued": 1})
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
        await bump(batches(), batch_id, queued=created)
    return created, skipped, requeued


async def _list_level_all(client, bucket: str, prefix: str) -> tuple[list[str], list[dict]]:
    """Liệt kê ĐẦY ĐỦ 1 cấp (mọi trang) — dùng cho planner sharding (§5), không
    phải hot path nên phân trang trọn vẹn ở đây chấp nhận được."""
    folders: list[str] = []
    files: list[dict] = []
    token = None
    while True:
        f, fl, token = await async_list_folder(client, bucket, prefix, token)
        folders.extend(f)
        files.extend(fl)
        if not token:
            break
    return folders, files


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
    await init_counts(batches(), batch_id)
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
        items = []
        for key in body.keys:
            try:
                meta = await async_head_object(client, conn["bucket"], key)
            except Exception:  # noqa: BLE001
                meta = {}
            items.append((key, meta))
        created, skipped, requeued = await _sync_import_files(batch_id, branch, source_id, items)
        await log_action(user["username"], AuditAction.GCN_IMPORT_MINIO, batch_id, {
            "source_connection_id": source_id, "keys": body.keys,
            "created": created, "skipped": skipped, "requeued": requeued,
        })
        return {"batch_id": batch_id, "created": created, "skipped": skipped, "requeued": requeued}

    if not body.prefix and not body.recursive:
        raise HTTPException(status_code=400, detail="Cần 'keys' hoặc 'prefix'+recursive")

    batch_id, branch = await _ensure_batch(body.batch_id, branch, None, "importing", user)
    if body.batch_id:  # nối vào lô đang chạy: đánh dấu importing lại
        await batches().update_one({"_id": batch_id}, {"$set": {"status": "importing"}})

    # Sharding (planner 1 cấp, §5): liệt kê sub-prefix cấp 1 — có sub-folder thì
    # tạo 1 import_jobs/sub-folder (chạy song song, N worker) thay vì 1 job serial
    # liệt kê đệ quy toàn bộ. File nằm ngay cấp này (không thuộc sub-folder nào)
    # được chèn ĐỒNG BỘ luôn (danh sách đã có sẵn từ lần liệt kê planner, khỏi tạo
    # thêm 1 job cho vài file lẻ).
    client = build_client(conn)
    sub_folders, loose_files = await _list_level_all(client, conn["bucket"], body.prefix or "")

    job_ids: list[str] = []
    now = datetime.now(timezone.utc)
    prefixes = sub_folders if sub_folders else [body.prefix or ""]
    for sub_prefix in prefixes:
        job_id = str(uuid.uuid4())
        await import_jobs().insert_one({
            "_id": job_id, "batch_id": batch_id, "source_connection_id": source_id,
            "prefix": sub_prefix, "branch": branch, "status": "queued",
            "started_at": None, "list_token": None, "inserted": 0, "skipped": 0,
            "error": None, "created_at": now,
        })
        job_ids.append(job_id)

    if sub_folders and loose_files:
        await _sync_import_files(
            batch_id, branch, source_id,
            [(f["key"], f) for f in loose_files],
        )

    await log_action(user["username"], AuditAction.GCN_IMPORT_MINIO, batch_id, {
        "source_connection_id": source_id, "prefix": body.prefix,
        "recursive": body.recursive, "job_ids": job_ids,
    })
    return {"batch_id": batch_id, "status": "importing", "job_ids": job_ids}
