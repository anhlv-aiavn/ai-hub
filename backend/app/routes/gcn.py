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
from app.flatten import COLUMNS as FLAT_COLUMNS, effective_extractions, flatten_doc
from app.summary import collect_so_phat_hanhs, group_key_of, per_gcn, summarize

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

    docs = await gcns().find(flt, _TABLE_PROJ).limit(limit).to_list(length=limit)
    rows: list[dict] = []
    for d in docs:
        rows.extend(_expand(d))
    # Gom theo group_key (None xuống cuối), trong nhóm giữ thứ tự tạo.
    rows.sort(key=lambda r: (r.get("group_key") is None, r.get("group_key") or "",
                             str(r.get("created_at") or "")))
    return {"gcn": rows, "total": len(rows)}


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


@router.get("/stats")
async def stats(batch_id: str | None = None):
    """Tổng hợp cho bảng Thống kê: tổng tệp/GCN/trang, breakdown trạng thái & hậu
    kiểm, và các chỉ số cảnh báo (lỗi, bỏ qua, thiếu Số phát hành, xong-chưa-kiểm).
    Một lần aggregate ($facet) cho rẻ."""
    match: dict = {"batch_id": batch_id} if batch_id else {}
    pipeline = [
        {"$match": match},
        {"$facet": {
            "by_status": [{"$group": {"_id": "$status", "n": {"$sum": 1}}}],
            "by_review": [{"$group": {
                "_id": {"$ifNull": ["$review.status", "unreviewed"]}, "n": {"$sum": 1}}}],
            "totals": [{"$group": {
                "_id": None,
                "files": {"$sum": 1},
                "pages": {"$sum": {"$ifNull": ["$page_count", 0]}},
                "gcns": {"$sum": {"$size": {"$ifNull": ["$gcn_rows", []]}}},
            }}],
            "missing_sph": [{"$match": {"status": "done", "group_key": None}}, {"$count": "n"}],
            "unreviewed_done": [
                {"$match": {"status": "done", "review.status": "unreviewed"}}, {"$count": "n"}],
        }},
    ]
    agg = await gcns().aggregate(pipeline).to_list(length=1)
    f = agg[0] if agg else {}

    def _kv(rows):
        return {r["_id"]: r["n"] for r in (rows or []) if r.get("_id") is not None}

    def _one(rows):
        return (rows[0]["n"] if rows else 0)

    totals = (f.get("totals") or [{}])[0]
    return {
        "files": totals.get("files", 0),
        "gcns": totals.get("gcns", 0),
        "pages": totals.get("pages", 0),
        "by_status": _kv(f.get("by_status")),
        "by_review": _kv(f.get("by_review")),
        "missing_sph": _one(f.get("missing_sph")),
        "unreviewed_done": _one(f.get("unreviewed_done")),
    }


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
    deleted: list[int] | None = None  # chỉ số bản ghi GCN bị xoá khi hậu kiểm


@router.put("/{gcn_id}")
async def put_review(gcn_id: str, body: ReviewIn):
    proj = {"review": 1}
    recompute = body.overrides is not None or body.deleted is not None
    if recompute:  # cần raw để tính lại cột dẫn xuất
        proj.update({"extractions": 1, "cuts": 1})
    doc = await gcns().find_one({"_id": gcn_id}, proj)
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    review = doc.get("review") or {}
    if body.display_name is not None:
        review["display_name"] = body.display_name
    if body.overrides is not None:
        review["overrides"] = body.overrides
    if body.deleted is not None:
        review["deleted"] = sorted({int(i) for i in body.deleted})
    if body.status is not None:
        review["status"] = body.status
    if body.reviewer is not None:
        review["reviewer"] = body.reviewer
    review["at"] = datetime.now(timezone.utc)

    update: dict = {"review": review}
    # Sửa tay (overrides) / xoá GCN phải phản chiếu vào bảng list, nếu không bảng
    # vẫn hiện giá trị raw cũ. Tính lại từ extractions đã áp override + bỏ bản xoá.
    if recompute:
        deleted = review.get("deleted") or []
        ext = effective_extractions(doc.get("extractions"), review.get("overrides"), deleted)
        cuts = [c for c in (doc.get("cuts") or [])
                if not (isinstance(c, dict) and c.get("index") in deleted)]
        update.update({
            "group_key": group_key_of(ext),
            "extracted_so_phat_hanhs": collect_so_phat_hanhs(ext),
            "summary": summarize(ext),
            "gcn_rows": per_gcn(ext, cuts),
            "cuts": cuts,
        })

    await gcns().update_one({"_id": gcn_id}, {"$set": update})
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


def _expand(doc: dict) -> list[dict]:
    """1 doc (1 file) → NHIỀU dòng nếu file chứa nhiều GCN (theo gcn_rows worker lưu).
    Chưa xong / không có GCN → 1 dòng cấp file."""
    rev = doc.get("review") or {}
    base = {
        "gcn_id": doc["_id"],
        "batch_id": doc.get("batch_id"),
        "filename": doc.get("filename"),
        "display_name": rev.get("display_name"),
        "status": doc.get("status"),
        "review_status": rev.get("status", "unreviewed"),
        "error": doc.get("error"),
        "created_at": doc.get("created_at"),
    }
    gcn_rows = doc.get("gcn_rows") or []
    if not gcn_rows:
        s = doc.get("summary") or {}
        return [{**base, "row_id": doc["_id"], "cut_index": None,
                 "page_count": doc.get("page_count", 0),
                 "group_key": doc.get("group_key"), "summary": s}]

    total = len(gcn_rows)
    out = []
    for i, g in enumerate(gcn_rows):
        sph = g.get("so_phat_hanh") or ""
        out.append({
            **base,
            "row_id": f"{doc['_id']}#{i}",
            "cut_index": g.get("cut_index"),
            "page_count": g.get("page_count", 0),
            "group_key": sph or None,
            "summary": {**g, "gcn_count": total, "gcn_pos": i + 1},
        })
    return out


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
