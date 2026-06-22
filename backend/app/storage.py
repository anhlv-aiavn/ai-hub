"""Lưu/đọc PDF trên MinIO (qua minio_client vendor) + render 1 trang PNG cho đối soát."""

import asyncio
import io
import logging
import os

import pypdfium2 as pdfium
from PIL import Image

from app import config
from src.extentions.minio_helper import minio_client

log = logging.getLogger(__name__)

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


async def put_pdf(key: str, data: bytes) -> None:
    await minio_client.async_put_object(config.AIHUB_BUCKET, key, io.BytesIO(data))


async def get_pdf(key: str) -> io.BytesIO:
    return await minio_client.async_get_object(config.AIHUB_BUCKET, key)


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


async def render_page(key: str, page_index: int, width: int) -> bytes:
    buf = await get_pdf(key)
    data = buf.getvalue()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _render_page_png, data, page_index, width)


def page_count(pdf_bytes: bytes) -> int:
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(pdf)
    finally:
        pdf.close()
