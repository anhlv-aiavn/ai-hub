"""Xử lý 1 GCN (1 PDF): tải PDF từ MinIO → detect (windowed) + extract (song song,
có trần VLM toàn cục) → normalize → cắt file (tái dùng ảnh đã render) → ghi Mongo + SSE.

Concurrency:
- Worker (app/worker/main.py) chạy NHIỀU file in-flight cùng lúc.
- `_VLM_SEM` (semaphore toàn cục) bao MỌI call detect + extract → bơm nhiều request
  đồng thời cho vLLM dynamic-batch nhưng có trần `MAX_VLM_CONCURRENT`.

File lớn:
- > AIHUB_MAX_PAGES (mặc định 250) → skip (an toàn RAM/thời gian).
- Detect = phân loại biên TỪNG TRANG (cover/content/other) song song rồi suy nhóm
  tuyến tính (quyết định cục bộ → không lỗi mốc cửa sổ, không rớt trang)."""

import asyncio
import base64
import functools
import io
import logging
import os
from datetime import datetime, timezone

from PIL import Image

from app import config, storage
from app.bus import publish_sync
from app.summary import collect_so_phat_hanhs, group_key_of, per_gcn, summarize
from src.extentions.mongo_helper import AsyncMongo
from src.extentions.multimodal.detect_gcn import classify_page
from src.extentions.multimodal.extract_gcn import extract
from src.extentions.multimodal.make import (
    count_pdf_pages_from_bytes,
    pdf_to_corrected_images,
)
from src.extentions.multimodal.normalize_dang_ky import normalize_extractions

log = logging.getLogger(__name__)

DETECT_MIN_PAGES = int(os.getenv("DETECT_MIN_PAGES", "5"))
RENDER_DPI = int(os.getenv("AIHUB_RENDER_DPI", "200"))
RENDER_MAX_SIZE = int(os.getenv("AIHUB_RENDER_MAX_SIZE", "2000"))
MAX_PAGES = int(os.getenv("AIHUB_MAX_PAGES", "250"))
EXTRACT_TIMEOUT = float(os.getenv("EXTRACT_TIMEOUT_SECONDS", "3000"))

# Semaphore TOÀN CỤC cho mọi call VLM (detect+extract). Tạo lazy trong event loop worker.
_VLM_SEM: asyncio.Semaphore | None = None


def _vlm_sem() -> asyncio.Semaphore:
    global _VLM_SEM
    if _VLM_SEM is None:
        _VLM_SEM = asyncio.Semaphore(config.MAX_VLM_CONCURRENT)
    return _VLM_SEM


# ── Pipeline ────────────────────────────────────────────────────────────────

def _groups_from_roles(roles: list[str]) -> list[list[int]]:
    """Suy nhóm GCN tuyến tính từ nhãn vai trò từng trang (cover/content/other).

    - "cover"  → mở một nhóm MỚI (GCN được ĐỊNH DANH bởi bìa: Số phát hành nằm
      trên bìa, không bìa thì không phải GCN dùng được).
    - "content"→ nối vào nhóm đang mở; nếu CHƯA có bìa nào mở thì BỎ (content lạc
      không bìa = không phải GCN → tránh chế ra giấy giả từ trang phụ trợ).
    - "other"  → loại + đóng nhóm hiện tại.

    Đảm bảo: mỗi nhóm luôn bắt đầu bằng một bìa và liên tiếp tới trang phụ trợ.
    """
    groups: list[list[int]] = []
    cur: list[int] | None = None
    for i, role in enumerate(roles):
        if role == "cover":
            cur = [i]
            groups.append(cur)
        elif role == "content" and cur is not None:
            cur.append(i)
        else:  # "other", hoặc content lạc không có bìa mở → bỏ + đóng nhóm
            cur = None
    return [g for g in groups if g]


async def _detect_groups(images: list[str]) -> list[list[int]]:
    """Detect = phân loại biên TỪNG TRANG rồi suy nhóm tuyến tính.

    File rất ngắn (≤ DETECT_MIN_PAGES) → coi là MỘT giấy, khỏi gọi VLM. Còn lại:
    phân loại song song mỗi trang (cover/content/other) — mỗi call 1 ảnh nên chính
    xác cao + batch tốt, KHÔNG còn lỗi mốc cửa sổ / nhồi nhiều ảnh / rớt trang."""
    n = len(images)
    if n == 0:
        return []
    if n <= DETECT_MIN_PAGES:
        return [list(range(n))]

    async def _cls(i: int) -> str:
        async with _vlm_sem():
            try:
                return await classify_page(images[i])
            except Exception as e:  # noqa: BLE001
                log.warning("classify_page trang %d lỗi: %s", i, e)
                return "content"  # fail-safe: giữ trang, không cắt nhầm

    roles = await asyncio.gather(*(_cls(i) for i in range(n)))
    return _groups_from_roles(list(roles))


async def _pipeline(pdf_buf: io.BytesIO) -> tuple[list[dict], list[str]]:
    """Trả (records, images). images = ảnh đã xoay thẳng (tái dùng để cắt file)."""
    loop = asyncio.get_running_loop()
    n = await loop.run_in_executor(None, count_pdf_pages_from_bytes, pdf_buf)
    if n == 0:
        return [], []
    if n > MAX_PAGES:
        return [{"page_indices": [], "result": None, "error": f"too_many_pages:{n}",
                 "page_count": n, "skip_reason": "too_many_pages"}], []

    render = functools.partial(
        pdf_to_corrected_images, dpi=RENDER_DPI, max_img_size=RENDER_MAX_SIZE
    )
    images = await loop.run_in_executor(None, render, pdf_buf)
    if not images:
        return [], []
    page_count = len(images)
    groups = await _detect_groups(images)
    if not groups:
        return [], images

    async def _run(group: list[int]) -> dict:
        base = {"page_indices": group, "result": None, "error": None,
                "page_count": page_count, "skip_reason": None}
        imgs = [images[i] for i in group if 0 <= i < len(images)]
        try:
            async with _vlm_sem():
                base["result"] = await asyncio.wait_for(extract(imgs), timeout=EXTRACT_TIMEOUT)
        except asyncio.TimeoutError:
            base["error"] = f"timeout>{EXTRACT_TIMEOUT}s"
        except Exception as e:  # noqa: BLE001
            log.exception("extract lỗi pages %s: %s", group, e)
            base["error"] = str(e)
        return base

    records = await asyncio.gather(*(_run(g) for g in groups))
    return records, images


# ── Cắt file (tái dùng ảnh đã render) ───────────────────────────────────────

def _images_to_pdf(b64_list: list[str]) -> bytes | None:
    pages = []
    for b in b64_list:
        try:
            pages.append(Image.open(io.BytesIO(base64.b64decode(b))).convert("RGB"))
        except Exception:  # noqa: BLE001
            continue
    if not pages:
        return None
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()


def _entry_sph(rec: dict) -> str | None:
    res = rec.get("result") if isinstance(rec, dict) else None
    if isinstance(res, dict):
        for e in res.get("Đăng ký", []) or []:
            gcn = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
            if isinstance(gcn, dict) and gcn.get("Số phát hành"):
                return str(gcn["Số phát hành"])
    return None


async def _build_cuts(gcn_id: str, batch_id, images: list[str], records: list) -> list[dict]:
    cuts: list[dict] = []
    for ri, rec in enumerate(records):
        pages = rec.get("page_indices") if isinstance(rec, dict) else None
        if not pages:
            continue
        group = [images[j] for j in pages if isinstance(j, int) and 0 <= j < len(images)]
        pdf_bytes = _images_to_pdf(group)
        if not pdf_bytes:
            continue
        ckey = f"{batch_id}/{gcn_id}/cut-{ri}.pdf"
        await storage.put_pdf(ckey, pdf_bytes)
        sph = _entry_sph(rec)
        # Tên tệp cắt chuẩn: "<Số phát hành>-GCN.pdf". Thiếu Số phát hành → kèm
        # index để khỏi trùng giữa các bản cắt cùng file.
        stem = sph if sph else f"{gcn_id}-{ri + 1}"
        cuts.append({
            "index": ri, "s3_key": ckey, "page_indices": pages,
            "page_count": len(group), "so_phat_hanh": sph,
            "name": f"{stem}-GCN.pdf",
        })
    return cuts


# ── Orchestration cho 1 doc đã được worker claim ────────────────────────────

async def process_doc(mongo: AsyncMongo, doc: dict) -> str:
    """Xử lý 1 gcn doc (đã set processing bởi worker). Dùng chung mongo client."""
    gcn_id = doc["_id"]
    batch_id = doc.get("batch_id")
    branch = doc.get("branch")  # nhúng vào event để SSE lọc theo chi nhánh
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                  "branch": branch, "status": "processing"})

    try:
        # source_connection_id=None → hành vi cũ (kho nội bộ). Có id → đọc kho
        # nguồn read-only; lỗi (mất/di chuyển/quyền) raise SourceObjectUnavailable,
        # bắt chung bên dưới → doc "error" message rõ, không kẹt processing.
        pdf_buf = await storage.get_pdf(doc["s3_key"], doc.get("source_connection_id"))
        if pdf_buf.getvalue()[:4] != b"%PDF":
            raise ValueError("File không phải PDF (magic-byte không khớp)")
        records, images = await _pipeline(pdf_buf)
    except Exception as e:  # noqa: BLE001
        log.exception("process %s lỗi: %s", gcn_id, e)
        await mongo.update_one(
            config.COLL_GCN, {"_id": gcn_id},
            {"$set": {"status": "error", "error": str(e),
                      "finished_at": datetime.now(timezone.utc)}},
        )
        publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                      "branch": branch, "status": "error", "error": str(e)})
        await _rollup(mongo, batch_id, branch)
        return "error"

    records = records or []
    normalize_extractions(records)

    first = records[0] if records else {}
    # records rỗng KHÔNG có nghĩa là không đọc được file — có thể do không phát
    # hiện được bìa (không nhóm nào) dù ảnh đã render thành công (`images`).
    # Ưu tiên số trang từ ảnh đã render thật để vẫn xem được PDF dù trích xuất lỗi.
    page_count = first.get("page_count") or len(images) or doc.get("page_count", 0)
    skip_reason = first.get("skip_reason")
    has_error = any(r.get("error") for r in records if isinstance(r, dict))

    if skip_reason:
        status, err = "skip", first.get("error")
    elif not records or has_error:
        status = "error"
        err = next((r.get("error") for r in records if r.get("error")), None) or "no_gcn_detected"
    else:
        status, err = "done", None

    cuts: list[dict] = []
    if status == "done":
        try:
            cuts = await _build_cuts(gcn_id, batch_id, images, records)
        except Exception as e:  # noqa: BLE001
            log.warning("build_cuts %s lỗi: %s", gcn_id, e)

    sph_list = collect_so_phat_hanhs(records)
    dup_suspect, dup_candidates = False, []
    if status == "done" and sph_list:
        # Đánh dấu nghi trùng nội dung (PLAN_PHASE2.md §⑧): unique index chống
        # trùng theo KEY, không theo NỘI DUNG — 2 lần scan cùng GCN dưới 2 tên
        # khác nhau vẫn ra 2 doc. Không chặn cứng (2 bản scan có thể khác chất
        # lượng), chỉ cảnh báo để hậu kiểm biết mà xử lý.
        dupes = await mongo.db[config.COLL_GCN].find(
            {"extracted_so_phat_hanhs": {"$in": sph_list}, "_id": {"$ne": gcn_id}},
            {"_id": 1},
        ).to_list(length=5)
        dup_candidates = [d["_id"] for d in dupes]
        dup_suspect = bool(dup_candidates)

    update = {
        "status": status, "error": err, "extractions": records,
        "page_count": page_count, "skip_reason": skip_reason,
        "group_key": group_key_of(records),
        "extracted_so_phat_hanhs": sph_list,
        "summary": summarize(records),
        "gcn_rows": per_gcn(records, cuts), "cuts": cuts,
        "dup_suspect": dup_suspect, "dup_candidates": dup_candidates,
        "finished_at": datetime.now(timezone.utc),
    }
    await mongo.update_one(config.COLL_GCN, {"_id": gcn_id}, {"$set": update})
    if dup_suspect:  # 2 chiều: doc trước cũng cần biết doc mới trùng với nó
        await mongo.db[config.COLL_GCN].update_many(
            {"_id": {"$in": dup_candidates}},
            {"$set": {"dup_suspect": True}, "$addToSet": {"dup_candidates": gcn_id}},
        )
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                  "branch": branch, "status": status, "group_key": update["group_key"]})
    await _rollup(mongo, batch_id, branch)
    return status


async def _rollup(mongo: AsyncMongo, batch_id, branch=None) -> None:
    if not batch_id:
        return
    pending = await mongo.db[config.COLL_GCN].count_documents(
        {"batch_id": batch_id, "status": {"$in": ["queued", "processing"]}}
    )
    status = "done" if pending == 0 else "processing"
    await mongo.update_one(config.COLL_BATCH, {"_id": batch_id}, {"$set": {"status": status}})
    publish_sync({"type": "batch", "batch_id": batch_id, "branch": branch,
                  "status": status, "pending": pending})
