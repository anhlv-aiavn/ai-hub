"""Xử lý 1 GCN (1 PDF): tải PDF từ MinIO → detect_and_extract (VLM .199) →
normalize → ghi extractions/group_key/summary vào Mongo → publish event SSE.

RQ gọi `process_gcn(gcn_id)` (đồng bộ); bên trong chạy asyncio.run cho pipeline async.
Tạo Mongo client MỚI trong mỗi job (tránh chia sẻ event loop giữa các job)."""

import asyncio
import base64
import io
import logging
from datetime import datetime, timezone

from PIL import Image

from app import config, storage
from app.bus import publish_sync
from app.summary import collect_so_phat_hanhs, group_key_of, summarize
from src.extentions.minio_helper import minio_client
from src.extentions.mongo_helper import AsyncMongo
from src.extentions.multimodal.make import pdf_to_corrected_images
from src.extentions.multimodal.normalize_dang_ky import normalize_extractions
from src.extentions.multimodal.pipeline import detect_and_extract

log = logging.getLogger(__name__)


def _images_to_pdf(b64_list: list[str]) -> bytes | None:
    """Ghép list ảnh base64 PNG (đã xoay thẳng) thành 1 PDF nhiều trang."""
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


async def _build_cuts(gcn_id: str, batch_id, pdf_buf: io.BytesIO, records: list) -> list[dict]:
    """Mỗi GCN nhóm (page_indices) → 1 PDF cắt đã xoay thẳng, upload MinIO.
    Render lại ảnh corrected (pdfium+onnx, KHÔNG gọi VLM) → cắt theo page_indices."""
    loop = asyncio.get_running_loop()
    pdf_buf.seek(0)
    imgs = await loop.run_in_executor(None, pdf_to_corrected_images, pdf_buf)
    if not imgs:
        return []
    cuts: list[dict] = []
    for ri, rec in enumerate(records):
        pages = rec.get("page_indices") if isinstance(rec, dict) else None
        if not pages:
            continue
        group = [imgs[j] for j in pages if isinstance(j, int) and 0 <= j < len(imgs)]
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


def process_gcn(gcn_id: str) -> str:
    return asyncio.run(_process(gcn_id))


async def _emit(mongo: AsyncMongo, gcn_id: str, status: str, **extra) -> None:
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "status": status, **extra})


async def _process(gcn_id: str) -> str:
    mongo = AsyncMongo()
    doc = await mongo.find_one(config.COLL_GCN, {"_id": gcn_id})
    if not doc:
        await mongo.close_connection()
        return "not_found"

    batch_id = doc.get("batch_id")
    await mongo.update_one(
        config.COLL_GCN, {"_id": gcn_id},
        {"$set": {"status": "processing", "started_at": datetime.now(timezone.utc)}},
    )
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id, "status": "processing"})

    try:
        pdf_buf = await minio_client.async_get_object(config.AIHUB_BUCKET, doc["s3_key"])
        records = await detect_and_extract(pdf_buf)
    except Exception as e:  # noqa: BLE001
        log.exception("process_gcn %s failed: %s", gcn_id, e)
        await mongo.update_one(
            config.COLL_GCN, {"_id": gcn_id},
            {"$set": {"status": "error", "error": str(e),
                      "finished_at": datetime.now(timezone.utc)}},
        )
        publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                      "status": "error", "error": str(e)})
        await _rollup(mongo, batch_id)
        await mongo.close_connection()
        return "error"

    records = records or []
    normalize_extractions(records)

    first = records[0] if records else {}
    page_count = first.get("page_count", doc.get("page_count", 0))
    skip_reason = first.get("skip_reason")
    has_error = any(r.get("error") for r in records if isinstance(r, dict))

    if skip_reason:
        status = "skip"
        err = first.get("error")
    elif not records or has_error:
        status = "error"
        err = next((r.get("error") for r in records if r.get("error")), None) or "no_gcn_detected"
    else:
        status = "done"
        err = None

    # File cắt: chỉ dựng khi extract thành công (skip/lỗi → không có file cắt).
    cuts: list[dict] = []
    if status == "done":
        try:
            cuts = await _build_cuts(gcn_id, batch_id, pdf_buf, records)
        except Exception as e:  # noqa: BLE001
            log.warning("build_cuts %s lỗi: %s", gcn_id, e)

    update = {
        "status": status,
        "error": err,
        "extractions": records,
        "page_count": page_count,
        "skip_reason": skip_reason,
        "group_key": group_key_of(records),
        "extracted_so_phat_hanhs": collect_so_phat_hanhs(records),
        "summary": summarize(records),
        "cuts": cuts,
        "finished_at": datetime.now(timezone.utc),
    }
    await mongo.update_one(config.COLL_GCN, {"_id": gcn_id}, {"$set": update})
    publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                  "status": status, "group_key": update["group_key"]})
    await _rollup(mongo, batch_id)
    await mongo.close_connection()
    return status


async def _rollup(mongo: AsyncMongo, batch_id) -> None:
    """Cập nhật trạng thái lô theo số GCN còn queued/processing."""
    if not batch_id:
        return
    db = mongo.db
    pending = await db[config.COLL_GCN].count_documents(
        {"batch_id": batch_id, "status": {"$in": ["queued", "processing"]}}
    )
    status = "done" if pending == 0 else "processing"
    await mongo.update_one(config.COLL_BATCH, {"_id": batch_id}, {"$set": {"status": status}})
    publish_sync({"type": "batch", "batch_id": batch_id, "status": status, "pending": pending})
