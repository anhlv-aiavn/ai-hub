"""Lô (batch): tạo lô từ nhiều PDF, liệt kê, rollup trạng thái."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app import config, storage
from app.audit import AuditAction, log_action
from app.batch_counters import bump, init_counts
from app.branches import get_branches, is_valid_branch
from app.db import batches, gcns
from app.deps import current_user, ensure_branch_access, is_admin, require_operator

router = APIRouter(prefix="/v1/batches", tags=["batches"], dependencies=[Depends(current_user)])


@router.post("")
async def create_batch(
    files: list[UploadFile] = File(...),
    name: str | None = Form(default=None),
    branch: str | None = Form(default=None),
    batch_id: str | None = Form(default=None),
    user: dict = Depends(require_operator),
):
    """Tạo lô MỚI hoặc THÊM file vào lô có sẵn (truyền batch_id).

    Frontend upload TỪNG file một (mỗi file một request) để né giới hạn body nginx
    khi lô nặng và để file lỗi không kéo đổ cả lô. Request đầu không có batch_id →
    tạo lô; các request sau truyền batch_id → nối thêm.

    `branch` (chi nhánh/đơn vị) = chiều gộp để thống kê; denormalize xuống mỗi GCN
    để aggregate rẻ. Khi nối, branch lấy theo lô có sẵn.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Không có tệp nào")

    now = datetime.now(timezone.utc)
    # User thường: LUÔN ép chi nhánh của họ. Admin: theo chi nhánh được chọn.
    branch = (branch or "").strip() or None
    if not is_admin(user):
        branch = user.get("branch")
    appending = bool(batch_id)
    if appending:
        existing = await batches().find_one({"_id": batch_id}, {"branch": 1})
        if not existing:
            raise HTTPException(status_code=404, detail="Không tìm thấy lô để nối")
        ensure_branch_access(user, existing.get("branch"))
        branch = existing.get("branch")  # nối thì giữ chi nhánh của lô
    else:
        if not await is_valid_branch(branch):
            raise HTTPException(status_code=400, detail="Chi nhánh không hợp lệ")
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
            "branch": branch,
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
            "name": name or branch or now.strftime("Lô %d/%m %H:%M"),
            "branch": branch,
            "created_at": now,
            "file_count": len(created),
            "status": "processing",
        })
        await init_counts(batches(), batch_id, queued=len(created))
        total = len(created)

    await log_action(user["username"], AuditAction.GCN_UPLOAD, batch_id, {
        "filenames": filenames, "file_count": len(created), "branch": branch, "gcn_ids": created,
    })
    return {"batch_id": batch_id, "file_count": total, "branch": branch, "gcn_ids": created}


@router.get("")
async def list_batches(limit: int = 50, user: dict = Depends(current_user)):
    flt = {} if is_admin(user) else {"branch": user.get("branch")}
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


@router.get("/branches")
async def list_branches():
    """Danh sách chi nhánh (chọn khi tạo việc + lọc thống kê) — đọc từ site_config."""
    return {"branches": await get_branches()}


@router.get("/{batch_id}")
async def get_batch(batch_id: str, user: dict = Depends(current_user)):
    b = await batches().find_one({"_id": batch_id})
    if not b:
        raise HTTPException(status_code=404, detail="Không tìm thấy lô")
    ensure_branch_access(user, b.get("branch"))
    return {
        "batch_id": b["_id"], "name": b.get("name"), "status": b.get("status"),
        "file_count": b.get("file_count", 0), "created_at": b.get("created_at"),
        "counts": await _status_counts(b),
    }


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
