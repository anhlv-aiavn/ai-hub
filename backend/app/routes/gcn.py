"""GCN: bảng trích xuất (list/table), chi tiết, ảnh trang đối soát, hậu kiểm, tải bộ."""

import csv
import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pymongo import ReturnDocument

from app import config, doc_types, storage
from app.audit import AuditAction, log_action
from app.batch_counters import bump
from app.bus import publish
from app.db import batches, gcns
from app.deps import (
    current_user, ensure_batch_access, is_admin, require_admin, require_operator, require_viewer, scoped_batch_ids,
)
from app.storage import DestinationNotConfigured, SourceObjectUnavailable
from app.flatten import COLUMNS as FLAT_COLUMNS, effective_extractions, flatten_doc
from app.vn_text import ci_pattern, strip_diacritics

router = APIRouter(prefix="/v1/gcn", tags=["gcn"], dependencies=[Depends(current_user)])

# Giờ VN (UTC+7, không DST) — dùng để quy đổi ngày lịch chọn trên UI (khoảng ngày
# hậu kiểm) sang UTC trước khi so với các trường lưu bằng datetime.now(timezone.utc).
VN_TZ = timezone(timedelta(hours=7))


async def _authz_gcn(gcn_id: str, user: dict, proj: dict | None = None) -> dict:
    """Lấy doc + chặn user thường truy cập GCN thuộc lô họ không được gán (403/404)."""
    p = dict(proj or {})
    if p and "batch_id" not in p:
        p["batch_id"] = 1
    doc = await gcns().find_one({"_id": gcn_id}, p or None)
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    ensure_batch_access(user, doc.get("batch_id"))
    return doc

_TABLE_PROJ = {
    "extractions": 0,  # bảng chỉ cần summary, bỏ raw nặng
}


def _viewer_own_or(user: dict) -> dict | None:
    """Viewer chỉ được xem hồ sơ CHƯA hậu kiểm (việc cần làm) hoặc hồ sơ CHÍNH
    HỌ đã hậu kiểm (lịch sử của mình) — không thấy hồ sơ người khác đã Duyệt/
    Không duyệt, tránh lộ kết quả hậu kiểm của đồng nghiệp. operator/admin
    không bị giới hạn này (chỉ giới hạn theo lô được gán qua `scoped_batch_ids`)."""
    if user.get("role") != "viewer":
        return None
    return {"$or": [
        {"review.status": {"$nin": ["reviewed", "needs_review"]}},
        {"review.reviewer": user.get("username")},
    ]}


@router.get("")
async def list_gcn(
    batch_id: str | None = None,
    status: str | None = None,
    review: str | None = None,
    reviewer: str | None = Query(default=None, description=(
        "Lọc theo tài khoản đã hậu kiểm (review.reviewer) — kết hợp với `review` "
        "để xem các bản ghi tài khoản đó đã Duyệt/Không duyệt")),
    q: str | None = Query(default=None, description=(
        "Tìm theo Số phát hành, Số tờ, Số thửa, Số vào sổ, tên tệp/tên hồ sơ, "
        "tên file GCN (cắt) hoặc Chủ sử dụng")),
    canh_bao: bool | None = Query(default=None, description=(
        "true → CHỈ hồ sơ có cảnh báo 'có chuyển nhượng nhưng chưa rõ chủ' (cần "
        "chuyên viên xác minh); false → chỉ hồ sơ KHÔNG có cảnh báo")),
    doc_type: str | None = Query(default=None, description=(
        "Lọc theo loại giấy: gcn | ddk (đơn đăng ký) | pcctt (phiếu cung cấp "
        "thông tin). Bỏ trống → mọi loại. Doc cũ không có field này được coi là "
        "'gcn' nên lọc 'gcn' vẫn ra đủ dữ liệu cũ")),
    page: int = 1,
    page_size: int = 50,
    user: dict = Depends(current_user),
):
    """Bảng trích xuất — cột tóm tắt, lọc + tìm, sắp theo group_key (Số phát hành).

    Phân trang ở TẦNG FILE (doc), không phải tầng dòng đã expand — 1 trang luôn
    đúng `page_size` file dù file có 1 hay nhiều bản cắt (GCN) bên trong."""
    flt: dict = {}
    if batch_id:
        ensure_batch_access(user, batch_id)
        flt["batch_id"] = batch_id
    else:
        ids = scoped_batch_ids(user)
        if ids is not None:
            flt["batch_id"] = {"$in": ids}
    if status:
        flt["status"] = status
    if review:
        flt["review.status"] = review
    if reviewer:
        flt["review.reviewer"] = reviewer
    if doc_type:
        try:
            dt_loc = doc_types.hop_le(doc_type)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        # Doc tạo TRƯỚC khi có field `doc_type` không có key này — chúng đều là
        # GCN, nên lọc "gcn" phải bắt cả trường hợp thiếu field.
        flt["doc_type"] = ({"$in": [dt_loc, None]} if dt_loc == doc_types.MAC_DINH
                           else dt_loc)
    if canh_bao is not None:
        # Hàng đợi "cần xác minh": có chuyển nhượng nhưng không moi được chủ mới
        # (tên không nằm trong dữ liệu) — xem chu_cuoi.canh_bao.
        flt["chu_cuoi.canh_bao"] = (
            "co_chuyen_nhuong_chua_ro_chu" if canh_bao else {"$ne": "co_chuyen_nhuong_chua_ro_chu"})
    # $or dùng cho 2 mục đích độc lập (giới hạn hiển thị của viewer, và tìm theo
    # `q`) — gộp bằng $and thay vì gán thẳng flt["$or"] 2 lần (dict Python chỉ giữ
    # được 1 khóa "$or", lần gán sau sẽ ghi đè mất lần trước).
    and_clauses: list[dict] = []
    own_or = _viewer_own_or(user)
    if own_or:
        and_clauses.append(own_or)
    if q:
        # Mongo $options:"i" chỉ casefold đúng ASCII — chữ Việt có dấu viết hoa/
        # thường (vd "Ễ"/"ễ") KHÔNG được coi là khớp nhau, nên gõ có dấu (đặc
        # biệt khi dữ liệu trích xuất là chữ IN HOA) sẽ không tìm ra dù gõ không
        # dấu vẫn ra (phần ASCII casefold vẫn đúng). Tự dựng pattern: mỗi ký tự
        # thành character-class [chữ thường + chữ hoa] bằng Python (casefold
        # Unicode đúng cho cả ký tự có dấu) thay vì dựa vào "i" của Mongo.
        qr = ci_pattern(q.strip())
        # "Chủ sử dụng" còn tìm thêm theo bản KHÔNG DẤU (gcn_rows.chu_su_dung_norm,
        # ghi sẵn lúc trích xuất — xem app/summary.py::_entry_summary) để gõ có
        # dấu hay không dấu đều ra kết quả. re.escape thường (không cần
        # ci_pattern) vì chu_su_dung_norm đã lưu sẵn dạng chữ thường.
        qr_norm = re.escape(strip_diacritics(q.strip()))
        # Mongo tự động dò vào từng phần tử của mảng (kể cả mảng lồng trong mảng
        # con của `gcn_rows`), nên không cần $elemMatch cho các field dạng list.
        and_clauses.append({"$or": [
            {"extracted_so_phat_hanhs": {"$regex": qr}},  # Số phát hành
            {"filename": {"$regex": qr}},  # tên tệp gốc
            {"review.display_name": {"$regex": qr}},  # tên hồ sơ đã đổi
            {"cuts.name": {"$regex": qr}},  # tên file GCN đã cắt
            {"gcn_rows.so_phat_hanh": {"$regex": qr}},
            {"gcn_rows.so_vao_so": {"$regex": qr}},
            {"gcn_rows.to_ban_do": {"$regex": qr}},  # Số tờ
            {"gcn_rows.so_thua": {"$regex": qr}},  # Số thửa
            {"gcn_rows.chu_su_dung": {"$regex": qr}},
            {"gcn_rows.chu_su_dung_norm": {"$regex": qr_norm}},
        ]})
    if and_clauses:
        flt["$and"] = and_clauses

    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    pipeline = [
        {"$match": flt},
        {"$facet": {
            "data": [
                {"$addFields": {
                    # Bản ghi CHƯA hậu kiểm xong (unreviewed/needs_review) lên đầu —
                    # để không phải chuyển trang mới tìm ra việc cần làm.
                    "_rev_rank": {"$cond": [{"$eq": ["$review.status", "reviewed"]}, 1, 0]},
                    "_gk_null": {"$eq": ["$group_key", None]},
                    "_gk": {"$ifNull": ["$group_key", ""]},
                }},
                {"$sort": {"_rev_rank": 1, "_gk_null": 1, "_gk": 1, "created_at": 1}},
                {"$skip": (page - 1) * page_size},
                {"$limit": page_size},
                {"$project": {**_TABLE_PROJ, "_rev_rank": 0, "_gk_null": 0, "_gk": 0}},
            ],
            "count": [{"$count": "n"}],
        }},
    ]
    agg = await gcns().aggregate(pipeline).to_list(length=1)
    facet = agg[0] if agg else {"data": [], "count": []}
    total = (facet.get("count") or [{}])[0].get("n", 0) if facet.get("count") else 0

    rows: list[dict] = []
    for d in facet.get("data", []):
        rows.extend(_expand(d))
    await _enrich_dup_candidates(rows)
    return {
        "gcn": rows, "total": total, "page": page, "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
    }


def _rows_filter(batch_id, status, review, user: dict) -> dict:
    """Bộ lọc dùng chung cho preview `/rows` và `export.csv` — cùng phạm vi
    quyền (lô được gán + giới hạn viewer) để hai đường luôn thấy cùng một tập."""
    flt: dict = {}
    if batch_id:
        ensure_batch_access(user, batch_id)
        flt["batch_id"] = batch_id
    else:
        ids = scoped_batch_ids(user)
        if ids is not None:
            flt["batch_id"] = {"$in": ids}
    # Mặc định CHỈ hồ sơ đã xử lý xong. Đây là bảng XUẤT DỮ LIỆU: hồ sơ đang
    # Chờ/Đang xử lý/Lỗi chưa có thửa nào để xuất, nếu không lọc thì sắp-mới-nhất
    # đẩy cả loạt vừa upload (chưa trích xuất) lên đầu → bảng nhìn TRẮNG. Truyền
    # `status` cụ thể vẫn ghi đè được.
    flt["status"] = status or "done"
    if review:
        flt["review.status"] = review
    own_or = _viewer_own_or(user)
    if own_or:
        flt["$and"] = [own_or]
    return flt


# "Tải CSV" đồng bộ cap ở đây (dựng cả CSV trong RAM + trả trong 1 response —
# không stream). Toàn kho không cap = "Xuất nền" (export_job đọc qua cursor).
_CSV_SYNC_CAP = 5000

# group_key rỗng → mỗi hồ sơ tự làm một cụm (khoá "\n#id:<_id>"). Tiền tố có
# xuống dòng để không đụng group_key thật (sinh từ Số phát hành, không có \n).
_GRP_ID = {"$ifNull": ["$group_key", {"$concat": ["\n#id:", {"$toString": "$_id"}]}]}


async def _ranked_doc_ids(flt: dict, page: int, page_size: int) -> tuple[list, int, int]:
    """Xếp hồ sơ MỚI NHẤT TRƯỚC + GOM BẢN TRÙNG, trả về (ids theo đúng thứ tự cần
    hiển thị của 1 trang, tổng số cụm, doc_offset).

    Cụm = các hồ sơ cùng `group_key` (trùng nội dung GCN). Vị trí một cụm do hồ
    sơ MỚI NHẤT trong cụm quyết định — nên bản trùng cũ được kéo lên nằm cạnh
    bản mới, không lạc mất ở cuối kho. Gom PHẢI toàn cục (bản trùng có thể cách
    nhau nhiều tháng, gom trong-trang thì chúng chẳng bao giờ gặp nhau).

    Chỉ gom trên trường NHẸ (`group_key`/`created_at`/`_id`) — KHÔNG kéo
    `extractions` vào $group (chục KB × 600k sẽ nổ RAM). $sort created_at đi theo
    index `created_at_desc`; sau khi biết trang cần id nào mới nạp doc đầy đủ."""
    skip = (page - 1) * page_size
    pipeline = [
        {"$match": flt},
        # created_at giảm dần TRƯỚC $group để $first = bản mới nhất trong cụm.
        {"$sort": {"created_at": -1, "_id": -1}},
        {"$group": {"_id": _GRP_ID, "ids": {"$push": "$_id"},
                    "newest": {"$first": "$created_at"}, "newest_id": {"$first": "$_id"}}},
        {"$sort": {"newest": -1, "newest_id": -1}},
        {"$facet": {
            "page": [{"$skip": skip}, {"$limit": page_size}, {"$project": {"ids": 1}}],
            "count": [{"$count": "n"}],
        }},
    ]
    agg = await gcns().aggregate(pipeline, allowDiskUse=True).to_list(length=1)
    facet = agg[0] if agg else {"page": [], "count": []}
    total = (facet.get("count") or [{}])[0].get("n", 0) if facet.get("count") else 0
    ids = [i for g in (facet.get("page") or []) for i in (g.get("ids") or [])]
    return ids, total, skip


async def _rows_for_ids(ids: list) -> list[dict]:
    """Nạp doc đầy đủ cho đúng các id (1 truy vấn $in) rồi phẳng THEO ĐÚNG THỨ TỰ
    `ids` đã xếp ($in trả về không theo thứ tự nên phải map lại)."""
    if not ids:
        return []
    by_id = {d["_id"]: d async for d in gcns().find({"_id": {"$in": ids}})}
    rows: list[dict] = []
    for i in ids:
        d = by_id.get(i)
        if d is not None:
            rows.extend(flatten_doc(d))
    return rows


async def _collect_rows(batch_id, status, review, user: dict) -> list[dict]:
    # "Tải CSV" dùng CHUNG cách xếp với bảng preview (mới nhất trước + gom trùng)
    # để cái người ta xem và cái tải về khớp nhau — chỉ khác là cap 5000 cụm đầu.
    flt = _rows_filter(batch_id, status, review, user)
    ids, _total, _off = await _ranked_doc_ids(flt, page=1, page_size=_CSV_SYNC_CAP)
    return await _rows_for_ids(ids)


@router.get("/rows")
async def gcn_rows(batch_id: str | None = None, status: str | None = None,
                   review: str | None = None,
                   page: int = 1, page_size: int = 50,
                   user: dict = Depends(current_user)):
    """Khung nhìn dạng HÀNG phẳng (đã áp hậu kiểm) — preview trên UI.

    Phân trang ở TẦNG HỒ SƠ NGAY TRONG MONGO, MỚI NHẤT LÊN ĐẦU và GOM BẢN TRÙNG
    (xem `_ranked_doc_ids`): xem được TOÀN KHO (không còn cap 5000 + sort-trong-
    RAM như trước). Một hồ sơ nhiều thửa → nhiều hàng liền nhau; `total` đếm CỤM
    (≈ số hồ sơ vì bản trùng hiếm) và `doc_offset` để FE đánh STT theo hồ sơ."""
    flt = _rows_filter(batch_id, status, review, user)
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    ids, total, doc_offset = await _ranked_doc_ids(flt, page, page_size)
    rows = await _rows_for_ids(ids)
    return {
        "columns": FLAT_COLUMNS, "rows": rows, "total": total,
        "doc_offset": doc_offset,
        "page": page, "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
    }


@router.get("/export.csv")
async def export_csv(batch_id: str | None = None, status: str | None = None,
                     review: str | None = None,
                     user: dict = Depends(current_user)):
    rows = await _collect_rows(batch_id, status, review, user)
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
async def stats(batch_id: str | None = None,
                reviewer_days: int | None = Query(default=None, description=(
                    "Chỉ tính by_reviewer trong N ngày gần nhất (theo review.reviewed_at); "
                    "bỏ trống/0 = toàn thời gian")),
                reviewer_from: str | None = Query(default=None, description=(
                    "Khoảng ngày tùy chọn (YYYY-MM-DD, đầu ngày) — ưu tiên hơn reviewer_days")),
                reviewer_to: str | None = Query(default=None, description=(
                    "Khoảng ngày tùy chọn (YYYY-MM-DD, cuối ngày) — ưu tiên hơn reviewer_days")),
                user: dict = Depends(current_user)):
    """Tổng hợp cho bảng Thống kê: tổng tệp/GCN/trang, breakdown trạng thái & hậu
    kiểm, và các chỉ số cảnh báo. Một lần aggregate ($facet)."""
    match: dict = {}
    if batch_id:
        ensure_batch_access(user, batch_id)
        match["batch_id"] = batch_id
    else:
        ids = scoped_batch_ids(user)
        if ids is not None:
            match["batch_id"] = {"$in": ids}

    by_reviewer_stage: list[dict] = [{"$match": {"review.reviewer": {"$ne": None}}}]
    if reviewer_from or reviewer_to:
        try:
            rng: dict = {}
            # `reviewer_from/to` là ngày lịch theo giờ VN (UTC+7, không DST) từ input
            # date trên UI — phải quy đổi sang UTC trước khi so với review.reviewed_at
            # (luôn lưu bằng datetime.now(timezone.utc)), nếu không mốc ngày sẽ lệch
            # 7 tiếng: đầu ngày VN bị loại, và đầu ngày hôm sau VN bị tính nhầm vào.
            if reviewer_from:
                rng["$gte"] = datetime.strptime(reviewer_from, "%Y-%m-%d").replace(tzinfo=VN_TZ)
            if reviewer_to:
                rng["$lt"] = (datetime.strptime(reviewer_to, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=VN_TZ)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Ngày không hợp lệ (định dạng YYYY-MM-DD)") from e
        by_reviewer_stage.append({"$match": {"review.reviewed_at": rng}})
    elif reviewer_days and reviewer_days > 0:
        # Neo theo ranh giới ngày lịch giờ VN (00:00) thay vì cửa sổ trượt 24h theo
        # UTC kể từ lúc gọi API, để reviewer_days=1 ("Hôm nay") tương đương chính
        # xác với reviewer_from=reviewer_to=hôm nay ở nhánh trên.
        today_start_vn = datetime.now(VN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        since = today_start_vn - timedelta(days=reviewer_days - 1)
        by_reviewer_stage.append({"$match": {"review.reviewed_at": {"$gte": since}}})
    by_reviewer_stage += [
        {"$group": {
            "_id": "$review.reviewer",
            "reviewed": {"$sum": {"$cond": [{"$eq": ["$review.status", "reviewed"]}, 1, 0]}},
            "rejected": {"$sum": {"$cond": [{"$eq": ["$review.status", "needs_review"]}, 1, 0]}},
        }},
        {"$sort": {"reviewed": -1}},
    ]

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
            # s3_key_mapping (xem app/scripts/mapping_du_lieu_cu.py): "smap_docs" đếm
            # theo TỪNG hồ sơ ("chưa tính trùng"); "smap_groups" gộp theo matched_name
            # ("đã tính trùng") — cùng 2 cách đếm với báo cáo của script mapping.
            "smap_docs": [
                {"$match": {"s3_key_mapping": {"$exists": True, "$ne": []}}},
                {"$count": "n"},
            ],
            "smap_groups": [
                {"$match": {"s3_key_mapping": {"$exists": True, "$ne": []}}},
                {"$group": {"_id": {"$arrayElemAt": ["$s3_key_mapping.matched_name", 0]}}},
                {"$count": "n"},
            ],
            # Theo người hậu kiểm (§ thống kê cho quản lý) — reviewer chỉ được server
            # gán khi có hành động Duyệt/Không duyệt (xem put_review), nên số liệu ở
            # đây phản ánh trạng thái HIỆN TẠI của từng hồ sơ là do ai đặt gần nhất.
            # Lọc theo `reviewer_days` (nếu có) dựa trên `review.reviewed_at`.
            "by_reviewer": by_reviewer_stage,
        }},
    ]
    agg = await gcns().aggregate(pipeline).to_list(length=1)
    f = agg[0] if agg else {}

    def _kv(rows):
        return {r["_id"]: r["n"] for r in (rows or []) if r.get("_id") is not None}

    def _one(rows):
        return (rows[0]["n"] if rows else 0)

    totals = (f.get("totals") or [{}])[0]
    by_reviewer = [
        {"reviewer": r.get("_id"), "reviewed": r.get("reviewed", 0), "rejected": r.get("rejected", 0)}
        for r in (f.get("by_reviewer") or [])
    ]
    return {
        "files": totals.get("files", 0),
        "gcns": totals.get("gcns", 0),
        "pages": totals.get("pages", 0),
        "by_status": _kv(f.get("by_status")),
        "by_review": _kv(f.get("by_review")),
        "by_reviewer": by_reviewer,
        "missing_sph": _one(f.get("missing_sph")),
        "unreviewed_done": _one(f.get("unreviewed_done")),
        "smap_docs": _one(f.get("smap_docs")),
        "smap_groups": _one(f.get("smap_groups")),
    }


class RetryErrorsIn(BaseModel):
    batch_id: str | None = None  # None = MỌI LÔ (§tất cả đợt); giá trị = 1 lô cụ thể
    error_kind: str | None = None  # None = mọi error_kind; "dead" (poison) không nằm trong phạm vi
    since: datetime | None = None  # lọc theo thời điểm lỗi (finished_at) ≥ since
    until: datetime | None = None  # finished_at ≤ until


@router.post("/retry-errors")
async def retry_errors(body: RetryErrorsIn, user: dict = Depends(require_operator)):
    """Retry hàng loạt (§Quy mô cực lớn 6) — đặt lại `queued` cho doc `status=error`.
    `batch_id=None` ⇒ MỌI LÔ (cần quyền admin, không giới hạn theo lô); có giá trị ⇒
    1 lô cụ thể. `since`/`until` lọc theo `finished_at` (thời điểm lỗi) — hữu ích khi
    một sự cố (vd VLM khởi động lại) làm hỏng hàng loạt trong một khoảng thời gian.

    Update chạy TỪNG LÔ để mỗi `bump(batch.counts)` khớp đúng `modified_count` của lô
    đó (không thể một lệnh `update_many` toàn cục vì counter theo từng lô). Doc
    `status="dead"` (poison, §Backend worker) KHÔNG nằm trong phạm vi — cần soi thủ công."""
    time_flt: dict = {}
    if body.since:
        time_flt["$gte"] = body.since
    if body.until:
        time_flt["$lte"] = body.until

    base: dict = {"status": "error"}
    if body.error_kind:
        base["error_kind"] = body.error_kind
    if time_flt:
        base["finished_at"] = time_flt

    if body.batch_id:
        batch = await batches().find_one({"_id": body.batch_id}, {"_id": 1})
        if not batch:
            raise HTTPException(status_code=404, detail="Không tìm thấy lô")
        ensure_batch_access(user, body.batch_id)
        batch_ids = [body.batch_id]
    else:
        # Mọi lô — chỉ admin (không truyền lô ⇒ không giới hạn theo quyền lô).
        require_admin(user)
        batch_ids = [b for b in await gcns().distinct("batch_id", base) if b]

    total = 0
    for bid in batch_ids:
        res = await gcns().update_many(
            {**base, "batch_id": bid},
            {"$set": {"status": "queued", "error": None, "error_kind": None}})
        if res.modified_count:
            await bump(batches(), bid, error=-res.modified_count, queued=res.modified_count)
            total += res.modified_count
    return {"requeued": total, "batches": len(batch_ids)}


class ReleaseStuckIn(BaseModel):
    batch_id: str | None = None      # None = MỌI LÔ (cần admin)
    # Chỉ giải phóng doc processing "cũ" hơn ngưỡng này. PHẢI lớn hơn thời gian xử
    # lý thật của 1 hồ sơ (extract p90 quan sát ~170s) — nếu không sẽ GIẬT doc
    # đang chạy dở → xử lý 2 lần + counter processing bị trừ 2 lần (âm). Mặc định
    # 600s an toàn; worker dù sao cũng tự reclaim doc CHẾT sau PROC_TTL(1800s).
    min_stale_seconds: int = 600


@router.post("/release-stuck")
async def release_stuck(body: ReleaseStuckIn, user: dict = Depends(require_operator)):
    """Giải phóng hồ sơ KẸT ở `processing` → `queued` NGAY, không chờ worker tự
    reclaim sau PROC_TTL (30′). Dùng sau khi tắt/bật hay build lại worker: worker
    cũ chết giữa chừng bỏ lại doc `processing` không ai chạy.

    An toàn: CHỈ đụng doc `started_at` cũ hơn `min_stale_seconds` — để không giật
    doc mà worker vừa khởi động lại đang claim & chạy thật (double-processing).
    Không tăng `attempts` (đây là thao tác vận hành, không phải dấu hiệu poison).
    Update TỪNG LÔ để `bump(batch.counts)` khớp đúng modified_count từng lô."""
    stale = datetime.now(timezone.utc) - timedelta(seconds=max(0, body.min_stale_seconds))
    base: dict = {"status": "processing", "started_at": {"$lt": stale}}

    if body.batch_id:
        batch = await batches().find_one({"_id": body.batch_id}, {"_id": 1})
        if not batch:
            raise HTTPException(status_code=404, detail="Không tìm thấy lô")
        ensure_batch_access(user, body.batch_id)
        batch_ids = [body.batch_id]
    else:
        require_admin(user)
        batch_ids = [b for b in await gcns().distinct("batch_id", base) if b]

    total = 0
    for bid in batch_ids:
        res = await gcns().update_many(
            {**base, "batch_id": bid}, {"$set": {"status": "queued"}})
        if res.modified_count:
            await bump(batches(), bid, processing=-res.modified_count, queued=res.modified_count)
            total += res.modified_count
    return {"released": total, "batches": len(batch_ids)}


@router.delete("/{gcn_id}")
async def delete_gcn(gcn_id: str, user: dict = Depends(require_operator)):
    """Xóa cứng 1 hồ sơ (khác `review.deleted` — chỉ ẩn 1 giấy chứng nhận con
    khỏi hiển thị). Xóa cả file gốc lẫn mọi bản cắt trên S3 đích (cùng prefix
    `{batch_id}/{gcn_id}`, xem `_gcn_doc`/`_build_cuts`) + doc Mongo, giảm
    `batch.file_count`/`counts` tương ứng. Không phục hồi được."""
    doc = await gcns().find_one({"_id": gcn_id}, {"batch_id": 1, "status": 1, "filename": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    ensure_batch_access(user, doc.get("batch_id"))
    if doc.get("status") == "processing":
        raise HTTPException(status_code=409, detail="Hồ sơ đang xử lý, chờ xong rồi xóa")

    batch_id = doc.get("batch_id")
    try:
        n_obj = await storage.delete_prefix(f"{batch_id}/{gcn_id}")
    except DestinationNotConfigured:
        # Hồ sơ import-theo-tham-chiếu (browse.py, không copy vào đích) — không
        # có gì để xóa ở đích, không chặn xóa Mongo vì lý do này (giống delete_batch).
        n_obj = 0
    await gcns().delete_one({"_id": gcn_id})
    if batch_id:
        await batches().update_one({"_id": batch_id}, {"$inc": {"file_count": -1}})
        await bump(batches(), batch_id, **{doc.get("status", "queued"): -1})

    await log_action(user["username"], AuditAction.GCN_DELETE, gcn_id, {
        "batch_id": batch_id, "filename": doc.get("filename"), "deleted_objects": n_obj,
    })
    return {"ok": True, "deleted_objects": n_obj}


@router.get("/{gcn_id}")
async def get_gcn(gcn_id: str, user: dict = Depends(current_user)):
    doc = await gcns().find_one({"_id": gcn_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    ensure_batch_access(user, doc.get("batch_id"))
    await log_action(user["username"], AuditAction.GCN_VIEW, gcn_id)
    out = _detail(doc)
    await _enrich_dup_candidates([out])
    return out


@router.get("/{gcn_id}/page/{n}")
async def get_page(gcn_id: str, n: int, w: int = 1100, user: dict = Depends(current_user)):
    doc = await _authz_gcn(gcn_id, user, {"s3_key": 1, "source_connection_id": 1})
    try:
        png = await storage.render_page(doc["s3_key"], n, w, doc.get("source_connection_id"))
    except DestinationNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SourceObjectUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{gcn_id}/pageinfo")
async def page_info(gcn_id: str, user: dict = Depends(current_user)):
    doc = await _authz_gcn(gcn_id, user, {"page_count": 1, "status": 1, "error": 1})
    pages = doc.get("page_count", 0)
    # File nguồn (nhất là import từ MinIO ngoài) không đọc được khi xử lý → lỗi
    # đã lưu trên doc nhưng page_count vẫn = 0 (chưa từng đếm được trang) → nếu
    # trả {pages:0} trần, FE hiểu nhầm là "còn đang tải" và kẹt loading vĩnh viễn.
    # Báo lỗi rõ ràng thay vì im lặng.
    if not pages and doc.get("status") == "error":
        raise HTTPException(status_code=502, detail=doc.get("error") or "Không đọc được file gốc")
    return {"pages": pages}


def _find_cut(doc: dict, ci: int) -> dict:
    for c in doc.get("cuts") or []:
        if isinstance(c, dict) and c.get("index") == ci:
            return c
    raise HTTPException(status_code=404, detail="Không tìm thấy file cắt")


@router.get("/{gcn_id}/cut/{ci}/pageinfo")
async def cut_pageinfo(gcn_id: str, ci: int, user: dict = Depends(current_user)):
    doc = await _authz_gcn(gcn_id, user, {"cuts": 1})
    cut = _find_cut(doc, ci)
    return {"pages": cut.get("page_count", 0)}


@router.get("/{gcn_id}/cut/{ci}/page/{n}")
async def cut_page(gcn_id: str, ci: int, n: int, w: int = 1100, user: dict = Depends(current_user)):
    doc = await _authz_gcn(gcn_id, user, {"cuts": 1})
    cut = _find_cut(doc, ci)
    try:
        png = await storage.render_page(cut["s3_key"], n, w)
    except DestinationNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SourceObjectUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


def _find_smap(doc: dict, si: int) -> dict:
    """`si` = vị trí trong mảng `s3_key_mapping` (không phải field 'index' như
    cuts — s3_key_mapping là mảng phẳng, xem app/scripts/mapping_du_lieu_cu.py)."""
    smap = doc.get("s3_key_mapping") or []
    if not (0 <= si < len(smap)):
        raise HTTPException(status_code=404, detail="Không tìm thấy file khớp")
    return smap[si]


@router.get("/{gcn_id}/smap/{si}/pageinfo")
async def smap_pageinfo(gcn_id: str, si: int, user: dict = Depends(current_user)):
    doc = await _authz_gcn(gcn_id, user, {"s3_key_mapping": 1})
    m = _find_smap(doc, si)
    try:
        pages = await storage.get_pdf_page_count(m["filepath"], m.get("source_connection_id"))
    except DestinationNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SourceObjectUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"pages": pages}


@router.get("/{gcn_id}/smap/{si}/page/{n}")
async def smap_page(gcn_id: str, si: int, n: int, w: int = 1100, user: dict = Depends(current_user)):
    doc = await _authz_gcn(gcn_id, user, {"s3_key_mapping": 1})
    m = _find_smap(doc, si)
    try:
        png = await storage.render_page(m["filepath"], n, w, m.get("source_connection_id"))
    except DestinationNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SourceObjectUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


def _sph_at(ext: list, ri: int) -> str | None:
    """Số phát hành của bản ghi thứ ri trong extractions (đã áp override)."""
    if not (0 <= ri < len(ext)):
        return None
    res = ext[ri].get("result") if isinstance(ext[ri], dict) else None
    if isinstance(res, dict):
        for e in res.get("Đăng ký", []) or []:
            g = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
            if isinstance(g, dict) and g.get("Số phát hành"):
                return str(g["Số phát hành"])
    return None


class ReviewIn(BaseModel):
    display_name: str | None = None
    overrides: dict | None = None
    status: str | None = None  # unreviewed | needs_review | reviewed
    reviewer: str | None = None
    deleted: list[int] | None = None  # chỉ số bản ghi GCN bị xoá khi hậu kiểm
    version: int | None = None  # optimistic concurrency — xem review.version


# ── Hậu kiểm đồng thời: soft-lock (mở thẳng qua URL/search, không qua hàng chờ)
# + optimistic concurrency (chốt chặn cuối khi lưu). Xem PLAN_PHASE2.md §③. ────

def _lock_public(review: dict) -> dict:
    lock = review.get("lock")
    return {"lock": lock, "version": review.get("version", 0)}


@router.post("/{gcn_id}/lock")
async def claim_lock(gcn_id: str, user: dict = Depends(require_viewer)):
    doc0 = await _authz_gcn(gcn_id, user, {"review": 1})
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=config.REVIEW_LOCK_TTL)
    updated = await gcns().find_one_and_update(
        {"_id": gcn_id, "$or": [
            {"review.lock": None},
            {"review.lock": {"$exists": False}},
            {"review.lock.expires_at": {"$lt": now}},
            {"review.lock.by": user["username"]},
        ]},
        {"$set": {"review.lock": {"by": user["username"], "at": now, "expires_at": expires}}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        holder = (doc0.get("review") or {}).get("lock") or {}
        exp = holder.get("expires_at")
        # HTTPException.detail đi qua JSONResponse thô của Starlette (KHÔNG qua
        # jsonable_encoder như response 200 bình thường) — để nguyên datetime ở
        # đây làm json.dumps ném TypeError, FastAPI trả 500 thay vì 409 định làm
        # (người thứ 2 tưởng lỗi mạng, không thấy thông báo "đang được X giữ").
        raise HTTPException(status_code=409, detail={
            "message": "Hồ sơ đang được người khác hậu kiểm",
            "locked_by": holder.get("by"),
            "expires_at": exp.isoformat() if exp else None,
        })
    # Đẩy realtime cho MỌI phiên đang mở bảng danh sách (kể cả tab khác của chính
    # mình) — không thì badge "đang hậu kiểm" chỉ hiện sau khi ai đó bấm Làm mới.
    # Bắt buộc kèm "batch_id": /v1/events lọc bỏ event ngoài lô được gán cho user thường.
    await publish({"type": "review_lock", "gcn_id": gcn_id, "batch_id": doc0.get("batch_id")})
    return _lock_public(updated.get("review") or {})


@router.post("/{gcn_id}/lock/heartbeat")
async def heartbeat_lock(gcn_id: str, user: dict = Depends(require_viewer)):
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=config.REVIEW_LOCK_TTL)
    updated = await gcns().find_one_and_update(
        {"_id": gcn_id, "review.lock.by": user["username"]},
        {"$set": {"review.lock.expires_at": expires}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Bạn không giữ khóa hồ sơ này (có thể đã hết hạn)")
    return _lock_public(updated.get("review") or {})


@router.delete("/{gcn_id}/lock")
async def release_lock(gcn_id: str, user: dict = Depends(require_viewer)):
    """Best-effort — không lỗi nếu khóa đã hết hạn hoặc đã bị người khác chiếm."""
    updated = await gcns().find_one_and_update(
        {"_id": gcn_id, "review.lock.by": user["username"]},
        {"$set": {"review.lock": None}},
        projection={"batch_id": 1},
    )
    # Chỉ đẩy event khi THỰC SỰ vừa mở khóa (match được nghĩa là lock đang là của
    # mình — tránh refresh thừa khi khóa đã hết hạn/bị người khác chiếm từ trước).
    if updated:
        await publish({"type": "review_lock", "gcn_id": gcn_id, "batch_id": updated.get("batch_id")})
    return {"ok": True}


@router.put("/{gcn_id}")
async def put_review(gcn_id: str, body: ReviewIn, user: dict = Depends(require_viewer)):
    # filename: 1 — BẮT BUỘC để lấy đúng đuôi file gốc khi tự thêm đuôi cho tên
    # mới bên dưới (thiếu dòng này thì doc.get("filename") luôn None dù tên tệp
    # gốc thực tế có đuôi, khiến hệ thống tưởng nhầm là "file gốc không có đuôi").
    proj = {"review": 1, "batch_id": 1, "filename": 1}
    recompute = body.overrides is not None or body.deleted is not None
    if recompute:  # cần raw để tính lại cột dẫn xuất
        proj.update({"extractions": 1, "cuts": 1})
    doc = await gcns().find_one({"_id": gcn_id}, proj)
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    ensure_batch_access(user, doc.get("batch_id"))
    review = doc.get("review") or {}
    before_review = dict(review)  # snapshot trước khi áp thay đổi — dùng để ghi audit sau
    current_version = review.get("version", 0)
    if body.version is not None and body.version != current_version:
        raise HTTPException(status_code=409, detail={
            "message": "Người khác vừa sửa hồ sơ này, hãy tải lại", "current_version": current_version,
        })
    # `is not None` không phân biệt được "không gửi display_name" với "gửi
    # display_name=null" (xóa tên) — cả 2 đều None trong Python, nên xóa tên
    # bị BỎ QUA im lặng (tên cũ không mất dù ô nhập trên UI đã trống). Dùng
    # model_fields_set để biết field có THỰC SỰ nằm trong request hay không.
    if "display_name" in body.model_fields_set:
        dn = body.display_name
        if dn:
            # Người dùng đặt tên thường quên đuôi file (gõ "Hợp đồng ABC" thay vì
            # "Hợp đồng ABC.pdf") — tự thêm đúng đuôi của file GỐC (doc.filename)
            # nếu tên mới chưa có sẵn đuôi đó. Tên file gốc đôi khi TỰ NÓ cũng
            # không có đuôi (vd tên tệp lúc tải lên/import chỉ là "34567" không
            # ".pdf") — mặc định ".pdf" trong trường hợp đó vì hệ thống CHỈ xử lý
            # PDF (kiểm tra magic-byte %PDF, không nhận định dạng khác).
            orig_ext = os.path.splitext(doc.get("filename") or "")[1] or ".pdf"
            if not dn.lower().endswith(orig_ext.lower()):
                dn = f"{dn}{orig_ext}"
        review["display_name"] = dn
    if body.overrides is not None:
        review["overrides"] = body.overrides
    if body.deleted is not None:
        review["deleted"] = sorted({int(i) for i in body.deleted})
    if body.status is not None:
        review["status"] = body.status
        if body.status == "unreviewed":
            # Hủy duyệt = không còn ai "sở hữu" quyết định hậu kiểm này nữa — null
            # hóa thay vì ghi đè bằng người vừa hủy, để lọc theo tài khoản
            # (GET /v1/gcn?reviewer=...) và thống kê by_reviewer (GET /v1/gcn/stats)
            # tự động đúng mà không cần sửa thêm nơi nào. Lịch sử đầy đủ (ai đã hủy
            # duyệt, lúc nào) vẫn tra được qua audit_log (action=gcn.edit).
            review["reviewer"] = None
            review["reviewed_at"] = None
        else:
            # Người BẤM Duyệt/Không duyệt mới là reviewer — lấy từ user đã xác thực
            # (server-trusted), không tin `body.reviewer` cho hành động này (tránh giả mạo).
            review["reviewer"] = user["username"]
            # Mốc thời gian RIÊNG cho lần Duyệt/Không duyệt gần nhất — khác `at` (mọi
            # lần lưu, kể cả autosave/sửa tay không đổi status) để thống kê theo thời
            # gian ở dưới phản ánh đúng lúc ra quyết định, không bị autosave làm lệch.
            review["reviewed_at"] = datetime.now(timezone.utc)
    elif body.reviewer is not None:
        review["reviewer"] = body.reviewer
    review["at"] = datetime.now(timezone.utc)
    review["version"] = current_version + 1

    update: dict = {"review": review}
    # Sửa tay (overrides) / xoá GCN phải phản chiếu vào bảng list, nếu không bảng
    # vẫn hiện giá trị raw cũ. Tính lại từ extractions đã áp override + bỏ bản xoá.
    if recompute:
        deleted = review.get("deleted") or []
        ext = effective_extractions(doc.get("extractions"), review.get("overrides"), deleted)
        cuts = [c for c in (doc.get("cuts") or [])
                if not (isinstance(c, dict) and c.get("index") in deleted)]
        # Tính lại phải đi qua ĐÚNG loại giấy của doc, nếu không hậu kiểm một
        # lá đơn/phiếu sẽ ghi đè summary bằng bộ suy diễn của GCN → mất sạch cột.
        dt = doc_types.get(doc.get("doc_type"))
        khoa = "" if dt.hau_xu_ly_gcn else doc_types.khoa_tu_nguon(doc)
        # Tên tệp cắt bám theo khóa ĐÃ override: "<khóa>-<LOẠI>.pdf".
        for c in cuts:
            ri = c.get("index")
            if not isinstance(ri, int):
                sph = None
            elif dt.hau_xu_ly_gcn:
                sph = _sph_at(ext, ri)
            else:
                sph = dt.cut_stem(ext[ri]) if 0 <= ri < len(ext) else None
            stem = sph if sph else f"{gcn_id}-{(ri or 0) + 1}"
            c["so_phat_hanh"] = sph
            c["name"] = f"{stem}-{dt.ma.upper()}.pdf"
        keys = dt.collect_keys(ext, khoa)
        update.update({
            "group_key": dt.group_key(ext, khoa),
            "extracted_so_phat_hanhs": keys if dt.hau_xu_ly_gcn else [],
            "extracted_keys": keys,
            "summary": dt.summarize(ext, khoa),
            "gcn_rows": dt.rows(ext, cuts, khoa),
            "cuts": cuts,
        })

    # Điều kiện version chặn ghi đè mù: nếu client gửi version, update PHẢI khớp
    # đúng version đã đọc — dù đã pass check ở trên, race hiếm (2 request cùng
    # lúc) vẫn được chặn ở tầng Mongo (atomic), không chỉ ở tầng Python.
    filt: dict = {"_id": gcn_id}
    if body.version is not None:
        filt["review.version"] = body.version
    res = await gcns().update_one(filt, {"$set": update})
    if res.matched_count == 0:
        fresh = await gcns().find_one({"_id": gcn_id}, {"review.version": 1})
        raise HTTPException(status_code=409, detail={
            "message": "Người khác vừa sửa hồ sơ này, hãy tải lại",
            "current_version": (fresh or {}).get("review", {}).get("version", 0),
        })

    # Audit: tách riêng "sửa" và "xóa dòng" (2 action) để lọc/tra dễ hơn — 1 lần
    # lưu có thể vừa sửa vừa xóa, ghi cả hai khi cả hai cùng xảy ra.
    if body.overrides is not None or body.display_name is not None or body.status is not None:
        await log_action(user["username"], AuditAction.GCN_EDIT, gcn_id, {
            "before": {k: before_review.get(k) for k in ("display_name", "overrides", "status")},
            "after": {k: review.get(k) for k in ("display_name", "overrides", "status")},
        })
    new_deleted = sorted(set(review.get("deleted") or []) - set(before_review.get("deleted") or []))
    if new_deleted:
        await log_action(user["username"], AuditAction.GCN_ROWS_DELETE, gcn_id, {"deleted_indices": new_deleted})

    return {"ok": True, "review": review}


@router.get("/{gcn_id}/download")
async def download(gcn_id: str, user: dict = Depends(current_user)):
    doc = await gcns().find_one({"_id": gcn_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy GCN")
    ensure_batch_access(user, doc.get("batch_id"))
    try:
        pdf = (await storage.get_pdf(doc["s3_key"], doc.get("source_connection_id"))).getvalue()
    except DestinationNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except SourceObjectUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    await log_action(user["username"], AuditAction.GCN_DOWNLOAD, gcn_id)
    name = (doc.get("review") or {}).get("display_name") or doc.get("group_key") \
        or doc.get("filename", gcn_id)
    # display_name giờ có thể đã kèm sẵn đuôi file gốc (xem put_review) — bỏ đuôi
    # đó trước khi tự thêm ".pdf" dưới đây, tránh nhân đôi ("ABC.pdf.pdf").
    if name.lower().endswith(".pdf"):
        name = name[: -len(".pdf")]
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
    lock = rev.get("lock") or {}
    lock_exp = lock.get("expires_at")
    # pymongo trả datetime NAIVE khi đọc lại (BSON không giữ tzinfo) dù lúc ghi là
    # datetime.now(timezone.utc) — phải gắn lại tzinfo trước khi so, nếu không
    # TypeError (naive vs aware) sẽ sập CẢ request list_gcn hễ có 1 bản ghi đang
    # khóa (bug thực tế: người khác không mở nổi danh sách khi ai đó đang hậu kiểm).
    if lock_exp is not None and lock_exp.tzinfo is None:
        lock_exp = lock_exp.replace(tzinfo=timezone.utc)
    # Khóa đã hết hạn (TTL) coi như không còn ai giữ — không hiện "đang được X hậu
    # kiểm" nhầm cho bản ghi thực ra đã rảnh (tránh chặn nhầm ở bảng danh sách).
    locked_by = lock.get("by") if lock_exp and lock_exp > datetime.now(timezone.utc) else None
    base = {
        "gcn_id": doc["_id"],
        "batch_id": doc.get("batch_id"),
        "branch": doc.get("branch"),
        "filename": doc.get("filename"),
        "display_name": rev.get("display_name"),
        "status": doc.get("status"),
        "review_status": rev.get("status", "unreviewed"),
        "reviewer": rev.get("reviewer"),
        "locked_by": locked_by,
        "error": doc.get("error"),
        "created_at": doc.get("created_at"),
        "dup_suspect": bool(doc.get("dup_suspect")),
        "dup_candidates": doc.get("dup_candidates") or [],
        "s3_key_mapping": doc.get("s3_key_mapping") or [],
    }
    gcn_rows = doc.get("gcn_rows") or []
    if not gcn_rows:
        s = doc.get("summary") or {}
        return [{**base, "row_id": doc["_id"], "cut_index": None, "cut_name": None,
                 "page_count": doc.get("page_count", 0),
                 "group_key": doc.get("group_key"), "summary": s}]

    cuts = {c.get("index"): c for c in (doc.get("cuts") or []) if isinstance(c, dict)}
    total = len(gcn_rows)
    # Chủ cuối gắn theo VỊ TRÍ (gcn_rows và chu_cuoi cùng sinh từ 1 lần duyệt entry
    # nên đồng thứ tự/độ dài). Lệch độ dài (hiếm, sau khi xoá 1 GCN) → bỏ, không
    # hiện dữ liệu SAI.
    ccs = doc.get("chu_cuoi") or []
    aligned = len(ccs) == total
    out = []
    for i, g in enumerate(gcn_rows):
        sph = g.get("so_phat_hanh") or ""
        cut_name = (cuts.get(g.get("cut_index")) or {}).get("name")
        summary = {**g, "gcn_count": total, "gcn_pos": i + 1}
        if aligned and isinstance(ccs[i], dict):
            summary["chu_cuoi"] = _cc_view(ccs[i])
        out.append({
            **base,
            "row_id": f"{doc['_id']}#{i}",
            "cut_index": g.get("cut_index"),
            "cut_name": cut_name,  # "<Số phát hành>-GCN.pdf" (bám SPH đã hậu kiểm)
            "page_count": g.get("page_count", 0),
            "group_key": sph or None,
            "summary": summary,
        })
    return out


def _cc_view(cc: dict) -> dict:
    """Gọn chu_cuoi cho UI (bảng + overview): tên chủ + nguồn + cờ cảnh báo."""
    return {
        "chu": [c.get("Tên chủ", "") for c in (cc.get("chu") or []) if isinstance(c, dict)],
        "nguon": cc.get("nguon"),
        "confidence": cc.get("confidence"),
        "canh_bao": cc.get("canh_bao") or "",
        "thoi_gian": cc.get("thoi_gian") or "",
    }


async def _enrich_dup_candidates(items: list[dict]) -> None:
    """Thay danh sách id thô trong `dup_candidates` bằng thông tin hiển thị được
    (tên tệp/lô/ngày/trạng thái) — để người hậu kiểm biết đang nghi trùng với hồ
    sơ CỤ THỂ nào thay vì chỉ biết "có N hồ sơ khác trùng"."""
    ids = {cid for it in items for cid in (it.get("dup_candidates") or [])}
    if not ids:
        return
    cursor = gcns().find(
        {"_id": {"$in": list(ids)}},
        {"filename": 1, "batch_id": 1, "created_at": 1, "status": 1, "review.display_name": 1},
    )
    info = {d["_id"]: {
        "gcn_id": d["_id"], "filename": d.get("filename"), "batch_id": d.get("batch_id"),
        # Hồ sơ đã đặt lại tên (review.display_name) → hiện tên đó, không hiện
        # tên tệp gốc nữa — khớp quy ước hiển thị đang dùng ở bảng danh sách.
        "display_name": (d.get("review") or {}).get("display_name"),
        "created_at": d.get("created_at"), "status": d.get("status"),
    } async for d in cursor}
    for it in items:
        cids = it.get("dup_candidates") or []
        it["dup_candidates"] = [info[c] for c in cids if c in info]


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
        "chu_cuoi": doc.get("chu_cuoi", []),  # [{rec_index, entry_index, chu, nguon, canh_bao...}]
        "review": doc.get("review", {}),
        "cuts": doc.get("cuts", []),
        "created_at": doc.get("created_at"),
        "dup_suspect": bool(doc.get("dup_suspect")),
        "dup_candidates": doc.get("dup_candidates") or [],
    }
