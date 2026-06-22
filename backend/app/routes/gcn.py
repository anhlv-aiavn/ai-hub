"""GCN: bảng trích xuất (list/table), chi tiết, ảnh trang đối soát, hậu kiểm, tải bộ."""

import csv
import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import storage
from app.db import gcns
from app.deps import require_key
from app.flatten import COLUMNS as FLAT_COLUMNS, flatten_doc

router = APIRouter(prefix="/v1/gcn", tags=["gcn"], dependencies=[Depends(require_key)])

_TABLE_PROJ = {
    "extractions": 0,  # bảng chỉ cần summary, bỏ raw nặng
}


@router.get("")
async def list_gcn(
    batch_id: str | None = None,
    status: str | None = None,
    review: str | None = None,
    q: str | None = Query(default=None, description="Tìm theo Số phát hành / tên tệp"),
    limit: int = 500,
):
    """Bảng trích xuất — cột tóm tắt, lọc + tìm, sắp theo group_key (Số phát hành)."""
    flt: dict = {}
    if batch_id:
        flt["batch_id"] = batch_id
    if status:
        flt["status"] = status
    if review:
        flt["review.status"] = review
    if q:
        flt["$or"] = [
            {"extracted_so_phat_hanhs": {"$regex": q, "$options": "i"}},
            {"group_key": {"$regex": q, "$options": "i"}},
            {"filename": {"$regex": q, "$options": "i"}},
        ]

    rows = await gcns().find(flt, _TABLE_PROJ).limit(limit).to_list(length=limit)
    # Gom theo group_key (None xuống cuối), trong nhóm giữ thứ tự tạo.
    rows.sort(key=lambda r: (r.get("group_key") is None, r.get("group_key") or "",
                             str(r.get("created_at") or "")))
    return {"gcn": [_row(r) for r in rows], "total": len(rows)}


async def _collect_rows(batch_id, status, review) -> list[dict]:
    flt: dict = {}
    if batch_id:
        flt["batch_id"] = batch_id
    if status:
        flt["status"] = status
    if review:
        flt["review.status"] = review
    docs = await gcns().find(flt).to_list(length=5000)
    docs.sort(key=lambda r: (r.get("group_key") is None, r.get("group_key") or "",
                             str(r.get("created_at") or "")))
    rows: list[dict] = []
    for d in docs:
        rows.extend(flatten_doc(d))
    return rows


@router.get("/rows")
async def gcn_rows(batch_id: str | None = None, status: str | None = None,
                   review: str | None = None):
    """Khung nhìn dạng HÀNG phẳng (đã áp hậu kiểm) — phục vụ xem/xuất/FME."""
    rows = await _collect_rows(batch_id, status, review)
    return {"columns": FLAT_COLUMNS, "rows": rows}


@router.get("/export.csv")
async def export_csv(batch_id: str | None = None, status: str | None = None,
                     review: str | None = None, _=Depends(require_key)):
    rows = await _collect_rows(batch_id, status, review)
    buf = io.StringIO()
    buf.write("﻿")  # BOM để Excel đọc UTF-8 đúng
    writer = csv.DictWriter(buf, fieldnames=FLAT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c, "") for c in FLAT_COLUMNS})
    data = buf.getvalue().encode("utf-8")
    fname = f"ai-hub-export-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.csv"
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/{gcn_id}")
async def get_gcn(gcn_id: str):
    doc = await gcns().find_one({"_id": gcn_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    return _detail(doc)


@router.get("/{gcn_id}/page/{n}")
async def get_page(gcn_id: str, n: int, w: int = 1100, _=Depends(require_key)):
    doc = await gcns().find_one({"_id": gcn_id}, {"s3_key": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    png = await storage.render_page(doc["s3_key"], n, w)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/{gcn_id}/pageinfo")
async def page_info(gcn_id: str):
    doc = await gcns().find_one({"_id": gcn_id}, {"page_count": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    return {"pages": doc.get("page_count", 0)}


def _find_cut(doc: dict, ci: int) -> dict:
    for c in doc.get("cuts") or []:
        if isinstance(c, dict) and c.get("index") == ci:
            return c
    raise HTTPException(status_code=404, detail="Không tìm thấy file cắt")


@router.get("/{gcn_id}/cut/{ci}/pageinfo")
async def cut_pageinfo(gcn_id: str, ci: int):
    doc = await gcns().find_one({"_id": gcn_id}, {"cuts": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    cut = _find_cut(doc, ci)
    return {"pages": cut.get("page_count", 0)}


@router.get("/{gcn_id}/cut/{ci}/page/{n}")
async def cut_page(gcn_id: str, ci: int, n: int, w: int = 1100, _=Depends(require_key)):
    doc = await gcns().find_one({"_id": gcn_id}, {"cuts": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    cut = _find_cut(doc, ci)
    png = await storage.render_page(cut["s3_key"], n, w)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


class ReviewIn(BaseModel):
    display_name: str | None = None
    overrides: dict | None = None
    status: str | None = None  # unreviewed | needs_review | reviewed
    reviewer: str | None = None


@router.put("/{gcn_id}")
async def put_review(gcn_id: str, body: ReviewIn):
    doc = await gcns().find_one({"_id": gcn_id}, {"review": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    review = doc.get("review") or {}
    if body.display_name is not None:
        review["display_name"] = body.display_name
    if body.overrides is not None:
        review["overrides"] = body.overrides
    if body.status is not None:
        review["status"] = body.status
    if body.reviewer is not None:
        review["reviewer"] = body.reviewer
    review["at"] = datetime.now(timezone.utc)
    await gcns().update_one({"_id": gcn_id}, {"$set": {"review": review}})
    return {"ok": True, "review": review}


@router.get("/{gcn_id}/download")
async def download(gcn_id: str, _=Depends(require_key)):
    doc = await gcns().find_one({"_id": gcn_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    pdf = (await storage.get_pdf(doc["s3_key"])).getvalue()
    name = (doc.get("review") or {}).get("display_name") or doc.get("group_key") \
        or doc.get("filename", gcn_id)
    name = _safe(name)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}.pdf", pdf)
        z.writestr(f"{name}.json", json.dumps(_detail(doc), ensure_ascii=False,
                                              indent=2, default=str))
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


def _safe(s: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_." else "_" for c in str(s)).strip()
    return keep or "gcn"


def _row(r: dict) -> dict:
    s = r.get("summary") or {}
    rev = r.get("review") or {}
    return {
        "gcn_id": r["_id"],
        "batch_id": r.get("batch_id"),
        "filename": r.get("filename"),
        "display_name": rev.get("display_name"),
        "status": r.get("status"),
        "review_status": rev.get("status", "unreviewed"),
        "page_count": r.get("page_count", 0),
        "group_key": r.get("group_key"),
        "error": r.get("error"),
        "summary": s,
    }


def _detail(doc: dict) -> dict:
    return {
        "gcn_id": doc["_id"],
        "batch_id": doc.get("batch_id"),
        "filename": doc.get("filename"),
        "status": doc.get("status"),
        "error": doc.get("error"),
        "page_count": doc.get("page_count", 0),
        "group_key": doc.get("group_key"),
        "extracted_so_phat_hanhs": doc.get("extracted_so_phat_hanhs", []),
        "extractions": doc.get("extractions", []),
        "summary": doc.get("summary", {}),
        "review": doc.get("review", {}),
        "cuts": doc.get("cuts", []),
        "created_at": doc.get("created_at"),
    }
