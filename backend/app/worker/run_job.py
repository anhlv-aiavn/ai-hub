"""Xử lý 1 GCN (1 PDF): tải PDF từ MinIO → detect (windowed) + extract (song song,
có trần VLM toàn cục) → normalize → cắt file (tái dùng ảnh đã render) → ghi Mongo + SSE.

Concurrency:
- Worker (app/worker/main.py) chạy NHIỀU file in-flight cùng lúc.
- `_VLM_SEM` (semaphore toàn cục) bao MỌI call detect + extract → bơm nhiều request
  đồng thời cho vLLM dynamic-batch nhưng có trần `MAX_VLM_CONCURRENT`.

File lớn:
- > AIHUB_MAX_PAGES (mặc định 250) → skip (an toàn RAM/thời gian).
- Detect chia cửa sổ DETECT_WINDOW trang, detect SONG SONG từng cửa sổ rồi ghép
  nhóm (tránh nhồi quá nhiều ảnh vào 1 call VLM)."""

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
from src.extentions.minio_helper import minio_client
from src.extentions.mongo_helper import AsyncMongo
from src.extentions.multimodal.detect_gcn import detect, verify_split
from src.extentions.multimodal.extract_gcn import extract
from src.extentions.multimodal.make import (
    count_pdf_pages_from_bytes,
    pdf_to_corrected_images,
)
from src.extentions.multimodal.normalize_dang_ky import normalize_extractions

log = logging.getLogger(__name__)

DETECT_MIN_PAGES = int(os.getenv("DETECT_MIN_PAGES", "5"))
DETECT_WINDOW = int(os.getenv("DETECT_WINDOW", "12"))
DETECT_VERIFY = os.getenv("DETECT_VERIFY", "true").strip().lower() == "true"
RENDER_DPI = int(os.getenv("AIHUB_RENDER_DPI", "200"))
RENDER_MAX_SIZE = int(os.getenv("AIHUB_RENDER_MAX_SIZE", "2000"))
MAX_PAGES = int(os.getenv("AIHUB_MAX_PAGES", "250"))
EXTRACT_TIMEOUT = float(os.getenv("EXTRACT_TIMEOUT_SECONDS", "600"))

# Semaphore TOÀN CỤC cho mọi call VLM (detect+extract). Tạo lazy trong event loop worker.
_VLM_SEM: asyncio.Semaphore | None = None


def _vlm_sem() -> asyncio.Semaphore:
    global _VLM_SEM
    if _VLM_SEM is None:
        _VLM_SEM = asyncio.Semaphore(config.MAX_VLM_CONCURRENT)
    return _VLM_SEM


# ── Pipeline ────────────────────────────────────────────────────────────────

async def _detect_groups(images: list[str]) -> list[list[int]]:
    """Nhóm trang theo từng GCN. File ngắn → 1 call; file dài → chia cửa sổ
    DETECT_WINDOW trang (không chồng lấn), detect song song, offset về toàn cục.
    Sau detect (nếu DETECT_VERIFY) chạy VLM verify soi lại từng nhóm để tách
    đúng GCN (chống detect gom nhầm nhiều giấy vào một nhóm)."""
    n = len(images)
    if n <= DETECT_MIN_PAGES:
        # File ngắn (≤ DETECT_MIN_PAGES trang): coi là MỘT giấy, extract hết —
        # khỏi detect, khỏi verify (đỡ 2 vòng VLM cho file nhỏ).
        return [list(range(n))]

    async def _one(imgs: list[str]) -> dict:
        async with _vlm_sem():
            return await detect(imgs)

    if n <= DETECT_WINDOW:
        try:
            d = await _one(images)
        except Exception as e:  # noqa: BLE001
            log.exception("detect lỗi: %s", e)
            d = {}
        groups = d.get("gcn_pages") or [list(range(n))]
        return await _verify_groups(images, groups)

    wins = [(i, min(i + DETECT_WINDOW, n)) for i in range(0, n, DETECT_WINDOW)]

    async def _win(lo: int, hi: int) -> list[list[int]]:
        try:
            d = await _one(images[lo:hi])
        except Exception as e:  # noqa: BLE001
            log.exception("detect window %d-%d lỗi: %s", lo, hi, e)
            d = {}
        out = []
        for g in d.get("gcn_pages") or []:
            gg = sorted({lo + j for j in g if isinstance(j, int) and 0 <= j < (hi - lo)})
            if gg:
                out.append(gg)
        return out

    parts = await asyncio.gather(*(_win(lo, hi) for lo, hi in wins))
    groups = [g for part in parts for g in part]
    return await _verify_groups(images, groups)


async def _verify_groups(images: list[str], groups: list[list[int]]) -> list[list[int]]:
    """Soi lại MỖI nhóm bằng VLM verify, tách thành các GCN đúng. Index cục bộ
    (0..len(group)-1) trả về được ánh xạ ngược về index toàn cục. Verify song song,
    có trần VLM toàn cục. Lỗi 1 nhóm → giữ nguyên nhóm đó."""
    if not DETECT_VERIFY or not groups:
        return groups

    async def _one(group: list[int]) -> list[list[int]]:
        if len(group) <= 1:
            return [group]
        try:
            async with _vlm_sem():
                subs = await verify_split([images[i] for i in group])
        except Exception as e:  # noqa: BLE001
            log.warning("verify_split nhóm %s lỗi: %s", group, e)
            return [group]
        mapped = [sorted(group[j] for j in s if 0 <= j < len(group)) for s in subs]
        mapped = [m for m in mapped if m]
        return mapped or [group]

    parts = await asyncio.gather(*(_one(g) for g in groups))
    return [g for part in parts for g in part]


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
        cuts.append({
            "index": ri, "s3_key": ckey, "page_indices": pages,
            "page_count": len(group), "so_phat_hanh": sph,
            "name": f"{sph or gcn_id}-cat{ri + 1}",
        })
    return cuts


# ── Orchestration cho 1 doc đã được worker claim ────────────────────────────

async def process_doc(mongo: AsyncMongo, doc: dict) -> str:
    """Xử lý 1 gcn doc (đã set processing bởi worker). Dùng chung mongo client."""
    gcn_id = doc["_id"]
    batch_id = doc.get("batch_id")
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id, "status": "processing"})

    try:
        pdf_buf = await minio_client.async_get_object(config.AIHUB_BUCKET, doc["s3_key"])
        records, images = await _pipeline(pdf_buf)
    except Exception as e:  # noqa: BLE001
        log.exception("process %s lỗi: %s", gcn_id, e)
        await mongo.update_one(
            config.COLL_GCN, {"_id": gcn_id},
            {"$set": {"status": "error", "error": str(e),
                      "finished_at": datetime.now(timezone.utc)}},
        )
        publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                      "status": "error", "error": str(e)})
        await _rollup(mongo, batch_id)
        return "error"

    records = records or []
    normalize_extractions(records)

    first = records[0] if records else {}
    page_count = first.get("page_count", doc.get("page_count", 0))
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

    update = {
        "status": status, "error": err, "extractions": records,
        "page_count": page_count, "skip_reason": skip_reason,
        "group_key": group_key_of(records),
        "extracted_so_phat_hanhs": collect_so_phat_hanhs(records),
        "summary": summarize(records),
        "gcn_rows": per_gcn(records, cuts), "cuts": cuts,
        "finished_at": datetime.now(timezone.utc),
    }
    await mongo.update_one(config.COLL_GCN, {"_id": gcn_id}, {"$set": update})
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                  "status": status, "group_key": update["group_key"]})
    await _rollup(mongo, batch_id)
    return status


async def _rollup(mongo: AsyncMongo, batch_id) -> None:
    if not batch_id:
        return
    pending = await mongo.db[config.COLL_GCN].count_documents(
        {"batch_id": batch_id, "status": {"$in": ["queued", "processing"]}}
    )
    status = "done" if pending == 0 else "processing"
    await mongo.update_one(config.COLL_BATCH, {"_id": batch_id}, {"$set": {"status": status}})
    publish_sync({"type": "batch", "batch_id": batch_id, "status": status, "pending": pending})
