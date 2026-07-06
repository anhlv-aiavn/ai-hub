"""Xuất dữ liệu NỀN (job) — bổ sung `GET /v1/gcn/export.csv` đồng bộ hiện có
(cap 5000 dòng, chặn request). Dùng cho lô lớn: không cap, không chặn request,
worker đọc Mongo qua cursor (xem `worker/export_job.py`)."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.access_log import log_access
from app.db import export_jobs
from app.deps import is_admin, require_operator, scoped_branch
from app.storage import SourceObjectUnavailable, get_pdf

# Toàn bộ export (tạo/xem trạng thái/tải) đều operator trở lên — xuất hàng loạt
# nhạy hơn xem lẻ 1 doc (PLAN_.md §Phân quyền), không mở cho viewer.
router = APIRouter(prefix="/v1/gcn/export-jobs", tags=["export"], dependencies=[Depends(require_operator)])


class ExportIn(BaseModel):
    batch_id: str | None = None
    branch: str | None = None
    status: str | None = None
    review: str | None = None


def _public(j: dict) -> dict:
    return {
        "job_id": j["_id"], "status": j.get("status"), "row_count": j.get("row_count"),
        "error": j.get("error"), "created_at": j.get("created_at"),
        "finished_at": j.get("finished_at"),
    }


async def _authz_job(job_id: str, user: dict) -> dict:
    job = await export_jobs().find_one({"_id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy export job")
    if not is_admin(user) and (job.get("filter") or {}).get("branch") != user.get("branch"):
        raise HTTPException(status_code=403, detail="Không thuộc chi nhánh của bạn")
    return job


@router.post("")
async def create_export_job(body: ExportIn, user: dict = Depends(require_operator)):
    branch = scoped_branch(user, body.branch)
    job_id = str(uuid.uuid4())
    await export_jobs().insert_one({
        "_id": job_id,
        "filter": {"batch_id": body.batch_id, "branch": branch, "status": body.status, "review": body.review},
        "format": "csv", "status": "queued", "requested_by": user["username"],
        "row_count": None, "error": None, "created_at": datetime.now(timezone.utc),
    })
    return {"job_id": job_id}


@router.get("/{job_id}")
async def get_export_job(job_id: str, user: dict = Depends(require_operator)):
    job = await _authz_job(job_id, user)
    return _public(job)


@router.get("/{job_id}/download")
async def download_export_job(job_id: str, user: dict = Depends(require_operator)):
    job = await _authz_job(job_id, user)
    if job.get("status") != "done" or not job.get("file_key"):
        raise HTTPException(status_code=409, detail="Export chưa xong")
    try:
        buf = await get_pdf(job["file_key"])
    except SourceObjectUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    await log_access(user["username"], None, "export", {"job_id": job_id, "filter": job.get("filter")})
    fname = f"ai-hub-export-{job_id[:8]}.csv"
    return StreamingResponse(
        buf, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
