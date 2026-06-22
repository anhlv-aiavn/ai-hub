import asyncio
import base64
import io
import logging
import multiprocessing
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

import pypdfium2 as pdfium
from PIL import Image
from src.extentions.multimodal.models.dat import OrientationDetector

CPU_COUNT = multiprocessing.cpu_count()
PDF_RENDER_EXECUTOR = ProcessPoolExecutor(max_workers=min(20, CPU_COUNT // 2 + 1))
DETECTOR = None


def get_detector():
    global DETECTOR
    if DETECTOR is None:
        DETECTOR = OrientationDetector()
    return DETECTOR


def get_pdf_page_count(pdf_path: str) -> int:
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def _count_pages_from_data(data: bytes) -> int:
    """Worker chạy trong subprocess — pdfium không thread-safe nên BUỘC subprocess."""
    if not data:
        return 0
    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def count_pdf_pages_from_bytes(pdf_bytesio: io.BytesIO) -> int:
    """Đếm pages từ bytes. Submit qua PDF_RENDER_EXECUTOR (ProcessPoolExecutor) để
    đảm bảo thread-safe — nhiều coroutine gọi đồng thời từ các thread khác nhau
    sẽ không gây race trong pdfium state.
    """
    pdf_bytesio.seek(0)
    data = pdf_bytesio.read()
    pdf_bytesio.seek(0)
    if not data:
        return 0
    return PDF_RENDER_EXECUTOR.submit(_count_pages_from_data, data).result()


def render_pages_chunk(args):
    pdf_path, page_numbers, dpi, max_img_size = args
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        results = []
        scale = dpi / 72.0

        for page_num in page_numbers:
            page = pdf[page_num]
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
            finally:
                page.close()

            if max_img_size and max(image.size) > max_img_size:
                ratio = max_img_size / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            image.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            results.append((page_num, img_b64))

        return results
    finally:
        pdf.close()


def rotate_b64_image(img_b64: str, angle: int) -> str:
    if angle not in (90, 270):
        return img_b64
    image = Image.open(io.BytesIO(base64.b64decode(img_b64)))
    rotated = image.rotate(angle, expand=True, fillcolor="white")
    buf = io.BytesIO()
    rotated.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def pdf_to_corrected_images(
    pdf_bytesio: io.BytesIO,
    dpi: int = 150,
    chunk_size: int = 10,
    max_img_size: Optional[int] = 1344,
) -> list[str]:
    """
    Render PDF pages → detect orientation → rotate if needed.
    Returns list of base64 PNG strings, one per page, ready for LLM.
    """
    pdf_bytesio.seek(0)
    pdf_data = pdf_bytesio.read()

    if not pdf_data:
        raise ValueError("PDF input is empty")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name

    try:
        # PDFium is not thread-safe; do all PDFium calls in worker subprocesses
        # (a single thread per process) to avoid corruption when this function
        # is called concurrently from many threads.
        num_pages = PDF_RENDER_EXECUTOR.submit(get_pdf_page_count, tmp_path).result()

        chunks = [
            list(range(i, min(i + chunk_size, num_pages)))
            for i in range(0, num_pages, chunk_size)
        ]
        tasks = [(tmp_path, chunk, dpi, max_img_size) for chunk in chunks]

        raw: list[tuple[int, str]] = []
        for chunk_result in PDF_RENDER_EXECUTOR.map(render_pages_chunk, tasks):
            raw.extend(chunk_result)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    raw.sort(key=lambda x: x[0])
    images_b64 = [img for _, img in raw]

    # Orientation detection
    try:
        detector = get_detector()
        images_bytesio = [io.BytesIO(base64.b64decode(img)) for img in images_b64]
        orientations = detector.get_orientations_from_images_batch(images_bytesio)
    except Exception as e:
        logging.warning(f"Orientation detection failed: {e}. Returning original images.")
        return images_b64

    return [
        rotate_b64_image(img, orientations[i] if i < len(orientations) else 0)
        for i, img in enumerate(images_b64)
    ]


async def pdf_to_corrected_images_async(
    pdf_bytesio: io.BytesIO,
    dpi: int = 150,
    max_img_size: Optional[int] = 1344,
) -> list[str]:
    """Async wrapper — runs the CPU-heavy work in a thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, pdf_to_corrected_images, pdf_bytesio, dpi, 10, max_img_size
    )

if __name__ == "__main__":
    file_path = "0_00004-GCN-1078-1997.pdf"
    with open(file_path, "rb") as f:
        pdf_bytesio = io.BytesIO(f.read())

    # Chọn 1 trong 2 cách:

    # Cách 1 - sync (đơn giản hơn)
    images = pdf_to_corrected_images(pdf_bytesio)

    # Cách 2 - async
    images = asyncio.run(pdf_to_corrected_images_async(pdf_bytesio))

    print(f"Total pages: {len(images)}")