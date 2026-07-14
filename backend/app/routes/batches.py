"""Lô (batch): tạo lô từ nhiều PDF, liệt kê, rollup trạng thái."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import config, storage
from app.audit import AuditAction, log_action
from app.batch_counters import bump, init_counts
from app.db import batches, gcns, users
from app.deps import (
    current_user, ensure_batch_access, is_admin, require_admin, require_operator,
    scoped_batch_ids,
)

router = APIRouter(prefix="/v1/batches", tags=["batches"], dependencies=[Depends(current_user)])


@router.post("")
async def create_batch(
    files: list[UploadFile] = File(...),
    name: str | None = Form(default=None),
    batch_id: str | None = Form(default=None),
    user: dict = Depends(require_operator),
):
    """Tạo lô MỚI hoặc THÊM file vào lô có sẵn (truyền batch_id, hoặc trùng tên).

    Frontend upload TỪNG file một (mỗi file một request) để né giới hạn body nginx
    khi lô nặng và để file lỗi không kéo đổ cả lô. Request đầu không có batch_id →
    tạo lô; các request sau truyền batch_id → nối thêm. Không có batch_id nhưng
    `name` trùng với 1 lô đã có (trong phạm vi user được truy cập) → cũng coi là
    nối thêm vào lô đó thay vì tạo lô mới trùng tên (ưu tiên batch_id trước, name
    sau — batch_id luôn thắng nếu có).
    """
    if not files:
        raise HTTPException(status_code=400, detail="Không có tệp nào")
    try:
        await storage.ensure_destination_configured()
    except storage.DestinationNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    now = datetime.now(timezone.utc)
    appending = bool(batch_id)
    if appending:
        existing = await batches().find_one({"_id": batch_id}, {"_id": 1})
        if not existing:
            raise HTTPException(status_code=404, detail="Không tìm thấy lô để nối")
        ensure_batch_access(user, batch_id)
    else:
        match_name = (name or "").strip()
        existing_by_name = None
        if match_name:
            ids = scoped_batch_ids(user)
            flt: dict = {"name": match_name}
            if ids is not None:
                flt["_id"] = {"$in": ids}
            existing_by_name = await batches().find_one(flt, {"_id": 1})
        if existing_by_name:
            batch_id = existing_by_name["_id"]
            appending = True
        else:
            batch_id = str(uuid.uuid4())
    created: list[str] = []
    filenames: list[str] = []

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
                       "status": "unreviewed", "reviewer": None, "at": None,
                       "lock": None, "version": 0},
            "created_at": now,
        })
        # Không cần enqueue: worker tự poll & claim doc status=queued từ Mongo.
        created.append(gcn_id)
        filenames.append(f.filename or f"{gcn_id}.pdf")

    if not created:
        raise HTTPException(status_code=400, detail="Tệp rỗng/không hợp lệ")

    if appending:
        await batches().update_one(
            {"_id": batch_id},
            {"$inc": {"file_count": len(created)}, "$set": {"status": "processing"}},
        )
        await bump(batches(), batch_id, queued=len(created))
        b = await batches().find_one({"_id": batch_id}, {"file_count": 1})
        total = (b or {}).get("file_count", len(created))
    else:
        await batches().insert_one({
            "_id": batch_id,
            "name": name or now.strftime("Lô %d/%m %H:%M"),
            "created_at": now,
            "file_count": len(created),
            "status": "processing",
        })
        await init_counts(batches(), batch_id, queued=len(created))
        total = len(created)
        if not is_admin(user):
            # Tự gán người tạo vào lô vừa tạo, để họ thấy/thao tác được ngay
            # (thay cho việc trước đây lô tự thuộc chi nhánh của họ).
            await users().update_one(
                {"username": user["username"]},
                {"$addToSet": {"assigned_batch_ids": batch_id}},
            )

    await log_action(user["username"], AuditAction.GCN_UPLOAD, batch_id, {
        "filenames": filenames, "file_count": len(created), "gcn_ids": created,
    })
    return {"batch_id": batch_id, "file_count": total, "gcn_ids": created}


@router.get("")
async def list_batches(limit: int = 50, user: dict = Depends(current_user)):
    ids = scoped_batch_ids(user)
    flt = {} if ids is None else {"_id": {"$in": ids}}
    rows = await batches().find(flt).sort("created_at", -1).limit(limit).to_list(length=limit)
    out = []
    for b in rows:
        counts = await _status_counts(b)
        out.append({
            "batch_id": b["_id"], "name": b.get("name"), "branch": b.get("branch"),
            "status": b.get("status"), "file_count": b.get("file_count", 0),
            "created_at": b.get("created_at"), "counts": counts,
        })
    return {"batches": out}


@router.get("/{batch_id}")
async def get_batch(batch_id: str, user: dict = Depends(current_user)):
    b = await batches().find_one({"_id": batch_id})
    if not b:
        raise HTTPException(status_code=404, detail="Không tìm thấy lô")
    ensure_batch_access(user, batch_id)
    return {
        "batch_id": b["_id"], "name": b.get("name"), "status": b.get("status"),
        "file_count": b.get("file_count", 0), "created_at": b.get("created_at"),
        "counts": await _status_counts(b),
    }


@router.delete("/{batch_id}")
async def delete_batch(batch_id: str, admin: dict = Depends(require_admin)):
    """Xóa cứng 1 lô: toàn bộ GCN thuộc lô + object trên S3 đích (prefix
    `{batch_id}/`) + doc lô. Không phục hồi được. Không đụng `import_jobs`/
    `audit_log` cũ liên quan (giữ lịch sử, giống `scripts/delete_branch.py`).
    """
    b = await batches().find_one({"_id": batch_id})
    if not b:
        raise HTTPException(status_code=404, detail="Không tìm thấy lô")
    if b.get("status") in ("processing", "importing"):
        raise HTTPException(status_code=409, detail="Lô đang xử lý, chờ xong rồi xóa")

    n_gcn = await gcns().count_documents({"batch_id": batch_id})
    try:
        n_obj = await storage.delete_prefix(f"{batch_id}/")
    except storage.DestinationNotConfigured:
        # Lô toàn file import-theo-tham-chiếu (browse.py, không copy vào đích)
        # thì không có gì để xóa ở đích — không chặn xóa Mongo vì lý do này.
        n_obj = 0
    await gcns().delete_many({"batch_id": batch_id})
    await batches().delete_one({"_id": batch_id})

    await log_action(admin["username"], AuditAction.BATCH_DELETE, batch_id, {
        "name": b.get("name"),
        "file_count": b.get("file_count", 0), "deleted_gcn": n_gcn, "deleted_objects": n_obj,
    })
    return {"ok": True, "deleted_gcn": n_gcn, "deleted_objects": n_obj}


# ── Quản lý gán user ↔ lô (admin-only) — thay thế cho khóa cứng theo chi nhánh.
# Đối xứng với Users.jsx (gán lô cho user): cùng ghi vào `user.assigned_batch_ids`. ─

@router.get("/{batch_id}/users")
async def list_batch_users(batch_id: str, admin: dict = Depends(require_admin)):
    rows = await users().find(
        {"assigned_batch_ids": batch_id}, {"username": 1, "role": 1},
    ).sort("username", 1).to_list(length=1000)
    return {"users": [{"username": r["username"], "role": r.get("role", "viewer")} for r in rows]}


class BatchUserIn(BaseModel):
    username: str


@router.post("/{batch_id}/users")
async def assign_batch_user(batch_id: str, body: BatchUserIn, admin: dict = Depends(require_admin)):
    if not await batches().find_one({"_id": batch_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Không tìm thấy lô")
    username = body.username.strip().lower()
    u = await users().find_one({"username": username}, {"role": 1})
    if not u:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    if u.get("role") == "admin":
        raise HTTPException(status_code=400, detail="Admin không cần gán lô (đã có toàn quyền)")
    await users().update_one({"username": username}, {"$addToSet": {"assigned_batch_ids": batch_id}})
    return {"ok": True}


@router.delete("/{batch_id}/users/{username}")
async def unassign_batch_user(batch_id: str, username: str, admin: dict = Depends(require_admin)):
    username = username.strip().lower()
    await users().update_one({"username": username}, {"$pull": {"assigned_batch_ids": batch_id}})
    return {"ok": True}


async def _status_counts(b: dict) -> dict:
    """Đọc `batch.counts` đã duy trì (không aggregate mỗi lần gọi — N+1 đắt ở
    `list_batches`). Batch cũ trước khi có counter → tính 1 lần rồi lưu lại
    (tự chữa lành, không cần script backfill riêng)."""
    counts = b.get("counts")
    if counts is not None:
        return counts
    pipeline = [
        {"$match": {"batch_id": b["_id"]}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]
    rows = await gcns().aggregate(pipeline).to_list(length=None)
    counts = {r["_id"]: r["n"] for r in rows}
    await batches().update_one({"_id": b["_id"]}, {"$set": {"counts": counts}})
    return counts
