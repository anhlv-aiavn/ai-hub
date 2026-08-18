"""Duyệt + import PDF trực tiếp từ kho S3 nguồn (không copy, chỉ tham chiếu
path gốc). operator trở lên, khóa theo lô được gán — xem PLAN_.md §Phân quyền."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from app import config, doc_types
from app.audit import AuditAction, log_action
from app.batch_counters import bump, init_counts
from app.db import batches, browse_progress_cache, gcns, import_jobs, s3_connections, users
from app.deps import ensure_batch_access, is_admin, require_operator, scoped_batch_ids
from app.s3_util import async_list_folder, build_client, list_folder_page, open_s3_client

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
    # Đánh dấu file đã từng import (BẤT KỂ lô nào — chỉ để hiển thị badge, không
    # đụng tới logic chống trùng lúc import vốn đang khoanh theo batch_id). Kèm
    # lô/ngày/trạng thái lần import gần nhất để người dùng biết đang trùng với
    # hồ sơ nào trước khi chọn lại, thay vì chỉ thấy dấu tích chung chung.
    if files:
        cursor = gcns().find(
            {"source_connection_id": source_id, "s3_key": {"$in": [f["key"] for f in files]}},
            {"s3_key": 1, "batch_id": 1, "created_at": 1, "status": 1},
        )
        imported_info = {d["s3_key"]: {
            "batch_id": d.get("batch_id"), "created_at": d.get("created_at"), "status": d.get("status"),
        } async for d in cursor}
        for f in files:
            f["imported"] = f["key"] in imported_info
            f["imported_info"] = imported_info.get(f["key"])
    return {"prefix": prefix, "folders": folders, "files": files, "next_token": next_token}


async def _list_recursive_capped(client, bucket: str, prefix: str) -> tuple[list[str], bool]:
    """Duyệt đệ quy TOÀN BỘ file dưới 1 prefix, dừng khi chạm `BROWSE_PROGRESS_CAP`
    (không chỉ giới hạn số file mà còn tránh cây quá RỘNG khiến tốn hàng trăm lần
    gọi S3 liệt kê tuần tự -- dùng 1 s3 client DÙNG CHUNG cho cả vòng lặp, xem
    open_s3_client(), thay vì mở session mới mỗi lần gọi)."""
    keys: list[str] = []
    capped = False
    async with open_s3_client(client) as s3:
        stack = [prefix]
        while stack and not capped:
            p = stack.pop()
            token = None
            while True:
                folders, files, token = await list_folder_page(s3, bucket, p, token)
                stack.extend(folders)
                keys.extend(f["key"] for f in files)
                if len(keys) >= config.BROWSE_PROGRESS_CAP:
                    capped = True
                    break
                if not token:
                    break
    return keys, capped


@router.get("/{source_id}/progress")
async def folder_progress(source_id: str, prefix: str = ""):
    """Tiến độ import (x/y) của 1 thư mục.

    Phần ĐẮT (liệt kê đệ quy MinIO ra danh sách key) được cache theo
    source+prefix, DÙNG CHUNG cho mọi người/phiên xem (kể cả tài khoản khác) —
    xem lại không phải đếm từ đầu. Phần "đã import bao nhiêu" KHÔNG cache, luôn
    đếm lại (rẻ — 1 `count_documents`) nên số hóa xong là thấy đúng ngay lập
    tức, khỏi cần cơ chế invalidate riêng: nội dung kho nguồn bất biến trong
    lúc dùng (§ bất biến 1, không copy/ghi/xóa ở nguồn), chỉ có SỐ ĐÃ IMPORT đổi
    theo thời gian — nên cache đúng phần không đổi, luôn tính tươi phần hay đổi.
    Cache hết hạn theo TTL (Mongo tự dọn, xem `BROWSE_PROGRESS_CACHE_TTL`) —
    phòng khi nội dung kho nguồn đổi ngoài luồng hệ thống.
    """
    conn = await s3_connections().find_one({"_id": source_id, "role": "source"})
    if not conn:
        raise HTTPException(status_code=404, detail="Không tìm thấy nguồn")

    cache_id = f"{source_id}::{prefix}"
    cached = await browse_progress_cache().find_one({"_id": cache_id})
    if cached:
        keys, capped = cached["keys"], cached["capped"]
    else:
        client = build_client(conn)
        keys, capped = await _list_recursive_capped(client, conn["bucket"], prefix)
        await browse_progress_cache().update_one(
            {"_id": cache_id},
            {"$set": {
                "source_connection_id": source_id, "prefix": prefix,
                "keys": keys, "capped": capped, "computed_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )

    total = len(keys)
    imported = 0
    if keys:
        imported = await gcns().count_documents({
            "source_connection_id": source_id, "s3_key": {"$in": keys},
        })
    return {"total": total, "imported": imported, "capped": capped}


class ImportIn(BaseModel):
    prefix: str | None = None
    recursive: bool = False
    keys: list[str] | None = None
    batch_id: str | None = None
    name: str | None = None
    # Loại giấy cho toàn bộ file import lần này: "gcn" (mặc định) | "ddk" | "pcctt".
    doc_type: str | None = None


def _gcn_doc(batch_id: str, source_id: str, key: str, meta: dict, now,
             doc_type: str = "gcn") -> dict:
    return {
        "_id": str(uuid.uuid4()), "batch_id": batch_id,
        "filename": key.rsplit("/", 1)[-1], "s3_key": key,
        "doc_type": doc_type,
        "status": "queued", "page_count": 0, "extractions": [],
        "extracted_so_phat_hanhs": [], "group_key": None, "summary": {},
        "review": {"display_name": None, "overrides": {}, "status": "unreviewed",
                   "reviewer": None, "at": None, "lock": None, "version": 0},
        "source_connection_id": source_id, "source_etag": meta.get("etag"),
        "source_mtime": meta.get("last_modified"), "attempts": 0,
        "created_at": now,
    }


async def _sync_import_files(
    batch_id: str, source_id: str, items: list[tuple[str, dict]],
    doc_type: str = "gcn",
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
        doc = _gcn_doc(batch_id, source_id, key, meta, now, doc_type)
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
    """Liệt kê ĐẦY ĐỦ 1 cấp (mọi trang) — dùng cho planner sharding (§5). Prefix
    phẳng (không sub-folder) nhưng nhiều nghìn file vẫn cần nhiều trang; dùng 1
    s3 client DÙNG CHUNG cho cả vòng lặp (xem open_s3_client()) thay vì mở
    session mới mỗi trang, nếu không request có thể timeout trước khi liệt kê
    xong (đã xảy ra thực tế với prefix ~vài chục nghìn file)."""
    folders: list[str] = []
    files: list[dict] = []
    async with open_s3_client(client) as s3:
        token = None
        while True:
            f, fl, token = await list_folder_page(s3, bucket, prefix, token)
            folders.extend(f)
            files.extend(fl)
            if not token:
                break
    return folders, files


async def _ensure_batch(batch_id: str | None, name: str | None, status: str, user: dict) -> str:
    """batch_id → nối vào lô đó. Không có batch_id nhưng `name` trùng 1 lô đã có
    (trong phạm vi user được truy cập) → cũng nối vào lô đó (ưu tiên batch_id
    trước, name sau — khớp hành vi `POST /v1/batches`, xem routes/batches.py)."""
    now = datetime.now(timezone.utc)
    if batch_id:
        existing = await batches().find_one({"_id": batch_id}, {"_id": 1})
        if not existing:
            raise HTTPException(status_code=404, detail="Không tìm thấy lô để nối")
        ensure_batch_access(user, batch_id)
        return batch_id
    match_name = (name or "").strip()
    if match_name:
        ids = scoped_batch_ids(user)
        flt: dict = {"name": match_name}
        if ids is not None:
            flt["_id"] = {"$in": ids}
        existing_by_name = await batches().find_one(flt, {"_id": 1})
        if existing_by_name:
            return existing_by_name["_id"]
    batch_id = str(uuid.uuid4())
    await batches().insert_one({
        "_id": batch_id, "name": name or now.strftime("Lô %d/%m %H:%M"),
        "created_at": now, "file_count": 0, "status": status,
    })
    await init_counts(batches(), batch_id)
    if not is_admin(user):
        await users().update_one(
            {"username": user["username"]},
            {"$addToSet": {"assigned_batch_ids": batch_id}},
        )
    return batch_id


@router.post("/{source_id}/import")
async def import_from_minio(source_id: str, body: ImportIn, user: dict = Depends(require_operator)):
    conn = await s3_connections().find_one({"_id": source_id, "role": "source"})
    if not conn:
        raise HTTPException(status_code=404, detail="Không tìm thấy nguồn")
    try:
        dt = doc_types.hop_le(body.doc_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if body.keys:
        batch_id = await _ensure_batch(body.batch_id, body.name, "processing", user)
        # Khong HEAD tung key nua (nguon S3 cham/khong on dinh voi list/head o
        # quy mo lon se lam ca request timeout) -- luu key ngay voi status=
        # "queued", KHONG co source_etag (giong het nhanh prefix -- xem
        # process_import_job trong worker/import_job.py). Loi file thieu/mat
        # quyen se lo ra luc worker OCR thuc su tai file, khong chan o day.
        # HEAD that bai truoc day cung chi tra ve {} (rong) nen khong mat kha
        # nang phat hien loi nao ca, chi bot 1 vong goi S3 khong can thiet.
        items = [(key, {}) for key in body.keys]
        created, skipped, requeued = await _sync_import_files(
            batch_id, source_id, items, dt)
        await log_action(user["username"], AuditAction.GCN_IMPORT_MINIO, batch_id, {
            "source_connection_id": source_id, "keys": body.keys,
            "created": created, "skipped": skipped, "requeued": requeued,
        })
        return {"batch_id": batch_id, "created": created, "skipped": skipped, "requeued": requeued}

    if not body.prefix and not body.recursive:
        raise HTTPException(status_code=400, detail="Cần 'keys' hoặc 'prefix'+recursive")

    batch_id = await _ensure_batch(body.batch_id, body.name, "importing", user)
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
            "prefix": sub_prefix, "doc_type": dt, "status": "queued",
            "started_at": None, "list_token": None, "inserted": 0, "skipped": 0,
            "error": None, "created_at": now,
        })
        job_ids.append(job_id)

    if sub_folders and loose_files:
        await _sync_import_files(
            batch_id, source_id,
            [(f["key"], f) for f in loose_files], dt,
        )

    await log_action(user["username"], AuditAction.GCN_IMPORT_MINIO, batch_id, {
        "source_connection_id": source_id, "prefix": body.prefix,
        "recursive": body.recursive, "job_ids": job_ids,
    })
    return {"batch_id": batch_id, "status": "importing", "job_ids": job_ids}
