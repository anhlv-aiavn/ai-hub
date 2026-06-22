import asyncio
import io
import logging
import os

from tqdm.asyncio import tqdm as tqdm_asyncio

from src.extentions.multimodal.detect_gcn import detect
from src.extentions.multimodal.extract_gcn import extract
from src.extentions.multimodal.make import (
    count_pdf_pages_from_bytes,
    pdf_to_corrected_images,
)

log = logging.getLogger(__name__)

# Số trang tối thiểu để chạy detect. PDF ít hơn ngưỡng này coi như 1 GCN duy nhất,
# bỏ qua detect để tiết kiệm 1 lượt VLM call.
DETECT_MIN_PAGES = int(os.getenv("DETECT_MIN_PAGES", "5"))

# Timeout (giây) cho mỗi lượt VLM extract của 1 GCN. Quá hạn → ghi nhận lỗi.
EXTRACT_TIMEOUT_SECONDS = float(os.getenv("EXTRACT_TIMEOUT_SECONDS", "300"))

# Khi force_thinking, model sinh thêm chain-of-thought tokens → tốn ~6x thời gian.
# Multiplier dùng để nhân timeout cho VLM call lúc retry-thinking.
THINKING_TIMEOUT_MULTIPLIER = float(os.getenv("THINKING_TIMEOUT_MULTIPLIER", "6"))

# PDF nhiều hơn ngưỡng này sẽ skip để xử lý sau. Giúp đồng bộ thời gian xử lý
# trong batch (các PDF nhỏ chạy nhanh, ít straggler).
EXTRACT_MAX_PAGES_PER_PDF = int(os.getenv("EXTRACT_MAX_PAGES_PER_PDF", "10"))


async def detect_and_extract(
    pdf_bytesio: io.BytesIO,
    force_thinking: bool = False,
) -> list[dict]:
    """Pipeline cho 1 PDF.

    1. Render PDF -> images (1 lần)
    2. detect: nhóm các trang thuộc cùng GCN
    3. extract: chạy song song 1 lần/GCN

    force_thinking: True → ép bật chain-of-thought (dùng cho retry).

    Returns: list các record GCN, mỗi record:
        {"page_indices": [..], "result": <dict GCN>, "error": <str|None>,
         "page_count": int, "skip_reason": str|None}

    Trường hợp skip (PDF > EXTRACT_MAX_PAGES_PER_PDF): trả về 1 record duy nhất
    với skip_reason='too_many_pages', không gọi VLM.
    """
    loop = asyncio.get_running_loop()

    # ── FAST PATH: đếm pages trước khi render ──
    # Render full PDF tốn rất nhiều CPU (rasterize + orientation detection).
    # PDF lớn sẽ bị skip, nên chỉ render khi chắc chắn xử lý.
    page_count = await loop.run_in_executor(None, count_pdf_pages_from_bytes, pdf_bytesio)

    if page_count == 0:
        return []

    if page_count > EXTRACT_MAX_PAGES_PER_PDF:
        # log.info(
        #     "PDF có %d trang > %d → skip extract (đánh dấu để chạy sau)",
        #     page_count, EXTRACT_MAX_PAGES_PER_PDF,
        # )
        return [{
            "page_indices": [],
            "result": None,
            "error": f"too_many_pages:{page_count}",
            "page_count": page_count,
            "skip_reason": "too_many_pages",
            "skip_threshold": EXTRACT_MAX_PAGES_PER_PDF,
        }]

    # Đã chắc cần xử lý → render thật.
    images = await loop.run_in_executor(None, pdf_to_corrected_images, pdf_bytesio)
    if not images:
        return []
    page_count = len(images)  # đồng bộ lại nếu render khác với count (hi hữu)

    if page_count < DETECT_MIN_PAGES:
        # PDF ngắn → bỏ qua detect, coi cả PDF là 1 GCN.
        groups: list[list[int]] = [list(range(page_count))]
    else:
        try:
            detected = await detect(images)
        except Exception as e:
            log.exception("detect failed: %s", e)
            detected = {}

        groups = detected.get("gcn_pages") or []
        if not groups:
            # Fallback: detect không trả về group nào → coi cả PDF là 1 GCN.
            groups = [list(range(len(images)))]

    thinking_param: bool | None = True if force_thinking else None
    effective_timeout = (
        EXTRACT_TIMEOUT_SECONDS * THINKING_TIMEOUT_MULTIPLIER
        if force_thinking else EXTRACT_TIMEOUT_SECONDS
    )

    async def _run(group: list[int]) -> dict:
        page_imgs = [images[i] for i in group if 0 <= i < len(images)]
        base = {
            "page_indices": group,
            "result": None,
            "error": None,
            "page_count": page_count,
            "skip_reason": None,
        }
        try:
            result = await asyncio.wait_for(
                extract(page_imgs, enable_thinking=thinking_param),
                timeout=effective_timeout,
            )
            base["result"] = result
            return base
        except asyncio.TimeoutError:
            log.warning("extract timeout (>%ss) for pages %s", effective_timeout, group)
            base["error"] = f"timeout>{effective_timeout}s"
            return base
        except Exception as e:
            log.exception("extract failed for pages %s: %s", group, e)
            base["error"] = str(e)
            return base

    return await asyncio.gather(*(_run(g) for g in groups))


async def _run_file(path: str, sem: asyncio.Semaphore) -> tuple[str, list[dict] | Exception]:
    async with sem:
        try:
            with open(path, "rb") as f:
                pdf = io.BytesIO(f.read())
            return path, await detect_and_extract(pdf)
        except Exception as e:
            log.exception("pipeline failed for %s", path)
            return path, e


async def main():
    file_paths = [
        "tests/output/0_10363-GCN-BO 175400.pdf",
        "tests/output/0_10363-GCN-BO 175403.pdf",
        "tests/output/0_BO 175399-GCN.pdf",
        "tests/output/0_BN 953446-GCN.pdf",
        "tests/output/0_A 934013-GCN.pdf",
        "tests/output/0_AA 00827763-GCN.pdf",
    ]
    max_concurrent = int(os.getenv("PIPELINE_MAX_CONCURRENT", "6"))
    sem = asyncio.Semaphore(max_concurrent)

    tasks = [asyncio.create_task(_run_file(p, sem)) for p in file_paths]

    results: list[tuple[str, list[dict] | Exception]] = []
    pbar = tqdm_asyncio(total=len(tasks), desc="PDF Pipeline", unit="pdf")
    try:
        for fut in asyncio.as_completed(tasks):
            path, result = await fut
            results.append((path, result))
            n_gcn = "ERR" if isinstance(result, Exception) else len(result)
            pbar.set_postfix_str(f"last={os.path.basename(path)} gcn={n_gcn}")
            pbar.update(1)
    finally:
        pbar.close()

    print("\n=== KẾT QUẢ ===")
    for path, result in results:
        print(f"\n{path}")
        if isinstance(result, Exception):
            print(f"  ERROR: {result}")
            continue
        for r in result:
            print(f"  pages: {r['page_indices']}  error: {r['error']}")
            print(f"  result: {r['result']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(main())
