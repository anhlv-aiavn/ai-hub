"""Lưu/đọc PDF trên MinIO (qua minio_client vendor) + render 1 trang PNG cho đối soát."""

import asyncio
import io
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pypdfium2 as pdfium
from botocore.exceptions import ClientError
from PIL import Image

from app import config
from app.db import s3_connections
from app.s3_util import build_client

log = logging.getLogger(__name__)


class SourceObjectUnavailable(Exception):
    """File gốc không đọc được từ kho nguồn (mất/di chuyển/quyền/mạng) — người
    gọi phải xử lý riêng (409/502 rõ ràng), không để lỗi trần 500."""


class DestinationNotConfigured(Exception):
    """Chưa cấu hình S3 đích (role=destination) — KHÔNG còn fallback ENV ngầm
    (xem PLAN_PHASE3_menu_logo_s3dest.md §Quyết định 5). Người gọi phải dịch
    thành lỗi rõ ràng cho người dùng (503 + hướng dẫn vào Cấu hình hệ thống),
    không để lộ lỗi kết nối boto3 trần."""


# Cache client theo nguồn/đích — TTL ~30s BẮT BUỘC (nhiều API/worker process,
# invalidate_s3_cache() chỉ xóa cache của process nhận request; xem PLAN_.md
# §Đồng thời). source: dict theo id; destination: 1 slot (role=destination —
# BẮT BUỘC phải có, không còn fallback minio_client/AIHUB_BUCKET).
_TTL = 30.0
_source_cache: dict[str, tuple[float, object, str]] = {}
_dest_cache: tuple[float, object, str] | None = None


def invalidate_s3_cache() -> None:
    global _dest_cache
    _source_cache.clear()
    _dest_cache = None


async def _get_source_client(source_connection_id: str):
    now = time.monotonic()
    cached = _source_cache.get(source_connection_id)
    if cached and (now - cached[0]) < _TTL:
        return cached[1], cached[2]
    doc = await s3_connections().find_one({"_id": source_connection_id})
    if not doc:
        raise SourceObjectUnavailable(f"Không tìm thấy cấu hình nguồn (id={source_connection_id})")
    client, bucket = build_client(doc), doc["bucket"]
    _source_cache[source_connection_id] = (now, client, bucket)
    return client, bucket


async def _get_dest_client():
    global _dest_cache
    now = time.monotonic()
    if _dest_cache and (now - _dest_cache[0]) < _TTL:
        return _dest_cache[1], _dest_cache[2]
    doc = await s3_connections().find_one({"role": "destination"})
    if not doc:
        raise DestinationNotConfigured(
            "Chưa cấu hình S3 đích — vào Cấu hình hệ thống → S3 đích để thiết lập")
    client, bucket = build_client(doc), doc["bucket"]
    _dest_cache = (now, client, bucket)
    return client, bucket

# pdfium KHÔNG thread-safe: nhiều render song song (FE nạp loạt thumbnail) trong
# thread pool mặc định → crash → 500 hàng loạt. Tuần tự hóa qua executor 1-thread
# riêng (giữ ONNX detector ấm trong thread đó, không phình RAM như process pool).
_RENDER_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-render")

# Xoay orientation cho trang đối soát ĐÚNG như pipeline (pdf_to_corrected_images) đã
# áp khi extract — nếu không preview lệch (nghiêng/ngược) so với dữ liệu bóc ra.
RENDER_ORIENT = os.getenv("AIHUB_RENDER_ORIENT", "true").strip().lower() == "true"


def _orient(image: Image.Image) -> Image.Image:
    """Phát hiện hướng (ONNX, cùng detector pipeline) → xoay 90/270 nếu cần. Lỗi → giữ nguyên."""
    if not RENDER_ORIENT:
        return image
    try:
        from src.extentions.multimodal.make import get_detector

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        angles = get_detector().get_orientations_from_images_batch([buf])
        angle = angles[0] if angles else 0
        if angle in (90, 270):
            return image.rotate(angle, expand=True, fillcolor="white")
    except Exception as e:  # noqa: BLE001
        log.warning("orientation render lỗi: %s", e)
    return image


async def ensure_destination_configured() -> None:
    """Kiểm nhanh trước khi làm việc (vd upload nhiều file) — raise
    `DestinationNotConfigured` NGAY, thay vì để lỗi rơi ở file đầu tiên giữa
    vòng lặp sau khi đã đọc/xử lý dở phần trước."""
    await _get_dest_client()


async def put_object(key: str, data: bytes) -> None:
    """Ghi ĐÍCH luôn — dùng chung cho PDF (upload gốc/cuts) và tệp khác (export
    CSV, logo). `DestinationNotConfigured` (từ `_get_dest_client`) truyền
    nguyên xuống người gọi — KHÔNG bắt ở đây."""
    client, bucket = await _get_dest_client()
    await client.async_put_object(bucket, key, io.BytesIO(data))


async def get_object(key: str) -> bytes:
    """Đọc ĐÍCH (đối xứng `put_object`) — dùng cho logo. `DestinationNotConfigured`
    truyền nguyên xuống người gọi."""
    client, bucket = await _get_dest_client()
    buf = await client.async_get_object(bucket, key)
    return buf.getvalue()


async def put_pdf(key: str, data: bytes) -> None:
    """Ghi ĐÍCH luôn (upload gốc + cuts) — không bao giờ ghi kho nguồn."""
    await put_object(key, data)


async def get_pdf(key: str, source_connection_id: str | None = None) -> io.BytesIO:
    """`source_connection_id=None` → đọc kho ĐÍCH (file nội bộ/cuts — cùng nơi
    `put_pdf` ghi; trước đây đọc thẳng `minio_client`/`AIHUB_BUCKET` bất kể đích
    đã cấu hình gì, lệch với đường ghi — đã hợp nhất qua `_get_dest_client`).
    Có id → đọc kho nguồn (read-only), lỗi phân loại rõ.
    `DestinationNotConfigured` truyền nguyên xuống (không bắt thành
    `SourceObjectUnavailable` — người gọi cần phân biệt 2 tình huống)."""
    if source_connection_id is None:
        client, bucket = await _get_dest_client()
    else:
        client, bucket = await _get_source_client(source_connection_id)
    try:
        return await client.async_get_object(bucket, key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        raise SourceObjectUnavailable(f"Không đọc được file ({code or e}): {key}") from e
    except Exception as e:  # noqa: BLE001 — lỗi mạng/kết nối (không phải ClientError)
        raise SourceObjectUnavailable(f"Không đọc được file ({e}): {key}") from e


def _render_page_png(pdf_bytes: bytes, page_index: int, width: int) -> bytes:
    """Render 1 trang PDF → PNG bytes, scale theo bề rộng mong muốn (cap PAGE_RENDER_MAX_W)."""
    width = min(max(200, width), config.PAGE_RENDER_MAX_W)
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        n = len(pdf)
        if n == 0:
            raise ValueError("PDF rỗng")
        page_index = max(0, min(page_index, n - 1))
        page = pdf[page_index]
        try:
            base_w = page.get_size()[0] or 612.0
            scale = max(0.2, min(4.0, width / base_w))
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
        finally:
            page.close()
        image = _orient(image)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        pdf.close()


async def render_page(key: str, page_index: int, width: int,
                      source_connection_id: str | None = None) -> bytes:
    buf = await get_pdf(key, source_connection_id)
    data = buf.getvalue()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_RENDER_POOL, _render_page_png, data, page_index, width)


def page_count(pdf_bytes: bytes) -> int:
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(pdf)
    finally:
        pdf.close()
