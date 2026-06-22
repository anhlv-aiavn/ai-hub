import asyncio
import io
import logging
import os
import re

from tqdm.asyncio import tqdm as tqdm_asyncio

from src.extentions.multimodal.detect_gcn import detect
from src.extentions.multimodal.extract_gcn import extract_gcn_only
from src.extentions.multimodal.make import (
    count_pdf_pages_from_bytes,
    pdf_to_corrected_images,
)

log = logging.getLogger(__name__)

DETECT_MIN_PAGES = int(os.getenv("DETECT_MIN_PAGES", "5"))
EXTRACT_TIMEOUT_SECONDS = float(os.getenv("EXTRACT_TIMEOUT_SECONDS", "300"))
# Thinking sinh chain-of-thought → tốn ~6x thời gian, cần nhân timeout.
THINKING_TIMEOUT_MULTIPLIER = float(os.getenv("THINKING_TIMEOUT_MULTIPLIER", "6"))
# PDF nhiều hơn ngưỡng này sẽ bị skip để xử lý sau (tránh timeout/quá tải VLM).
MAX_PAGES_PER_PDF = int(os.getenv("MAX_PAGES_PER_PDF", "30"))



def _norm_for_match(s: str) -> str:
    """Strip whitespace + uppercase + bỏ dấu Đ để so khớp Số phát hành tolerant.

    - Bỏ space, upper case
    - Đ/đ → D  (vd: 'DĐ 154222' khớp 'DD 154222')
    """
    if not isinstance(s, str):
        return ""
    s = re.sub(r"\s+", "", s).upper()
    s = s.replace("Đ", "D").replace("đ", "D")
    return s


def _is_match(extracted: str, expected: str) -> bool:
    a, b = _norm_for_match(extracted), _norm_for_match(expected)
    if not a or not b:
        return False
    return a == b or a in b or b in a


async def detect_and_check(
    pdf_bytesio: io.BytesIO,
    gcn_id: str,
    force_thinking: bool = False,
) -> dict:
    """Pipeline kiểm khớp 'Số phát hành' với gcn_id.

    force_thinking: True → ép bật chain-of-thought (dùng khi retry các case không khớp).

    Returns:
        {
            "extractions": [...],
            "match": "khớp" | "không khớp",
            "matched_so_phat_hanh": str|None,
            "expected_gcn_id": gcn_id,
            "thinking_used": bool,
            "error": None|str,
        }
    """
    loop = asyncio.get_running_loop()

    # FAST PATH: đếm pages trước, skip sớm để tránh render PDF lớn vô ích.
    try:
        page_count = await loop.run_in_executor(
            None, count_pdf_pages_from_bytes, pdf_bytesio
        )
    except Exception as e:
        log.exception("count pdf pages failed")
        return {
            "extractions": [],
            "match": "không khớp",
            "matched_so_phat_hanh": None,
            "expected_gcn_id": gcn_id,
            "thinking_used": bool(force_thinking),
            "error": f"count_failed: {e}",
            "page_count": 0,
            "groups_detected": 0,
            "skip_reason": None,
        }

    if page_count == 0:
        return {
            "extractions": [],
            "match": "không khớp",
            "matched_so_phat_hanh": None,
            "expected_gcn_id": gcn_id,
            "thinking_used": bool(force_thinking),
            "error": "empty_pdf",
            "page_count": 0,
            "groups_detected": 0,
            "skip_reason": None,
        }

    if page_count > MAX_PAGES_PER_PDF:
        # log.info(
        #     "PDF có %d trang > %d → skip (đánh dấu để check lại sau)",
        #     page_count, MAX_PAGES_PER_PDF,
        # )
        return {
            "extractions": [],
            "match": "skip",
            "matched_so_phat_hanh": None,
            "expected_gcn_id": gcn_id,
            "thinking_used": False,
            "error": f"too_many_pages:{page_count}",
            "page_count": page_count,
            "groups_detected": 0,
            "skip_reason": "too_many_pages",
            "skip_threshold": MAX_PAGES_PER_PDF,
        }

    # Đã chắc cần xử lý → render thật.
    try:
        images = await loop.run_in_executor(None, pdf_to_corrected_images, pdf_bytesio)
    except Exception as e:
        log.exception("render pdf failed")
        return {
            "extractions": [],
            "match": "không khớp",
            "matched_so_phat_hanh": None,
            "expected_gcn_id": gcn_id,
            "thinking_used": bool(force_thinking),
            "error": f"render_failed: {e}",
            "page_count": page_count,
            "groups_detected": 0,
            "skip_reason": None,
        }

    if not images:
        return {
            "extractions": [],
            "match": "không khớp",
            "matched_so_phat_hanh": None,
            "expected_gcn_id": gcn_id,
            "thinking_used": bool(force_thinking),
            "error": "empty_after_render",
            "page_count": page_count,
            "groups_detected": 0,
            "skip_reason": None,
        }

    page_count = len(images)  # đồng bộ lại

    if len(images) < DETECT_MIN_PAGES:
        groups: list[list[int]] = [list(range(len(images)))]
    else:
        try:
            detected = await detect(images)
        except Exception as e:
            log.exception("detect failed")
            detected = {}
        groups = detected.get("gcn_pages") or []
        if not groups:
            groups = [list(range(len(images)))]

    thinking_param: bool | None = True if force_thinking else None
    effective_timeout = (
        EXTRACT_TIMEOUT_SECONDS * THINKING_TIMEOUT_MULTIPLIER
        if force_thinking else EXTRACT_TIMEOUT_SECONDS
    )

    async def _run(group: list[int]) -> dict:
        page_imgs = [images[i] for i in group if 0 <= i < len(images)]
        try:
            result = await asyncio.wait_for(
                extract_gcn_only(page_imgs, enable_thinking=thinking_param),
                timeout=effective_timeout,
            )
            return {
                "page_indices": group,
                "Giấy chứng nhận": result.get("Giấy chứng nhận", []) or [],
                "error": None,
            }
        except asyncio.TimeoutError:
            log.warning("extract_gcn_only timeout (>%ss) pages %s", effective_timeout, group)
            return {
                "page_indices": group,
                "Giấy chứng nhận": [],
                "error": f"timeout>{effective_timeout}s",
            }
        except Exception as e:
            log.exception("extract_gcn_only failed pages %s", group)
            return {"page_indices": group, "Giấy chứng nhận": [], "error": str(e)}

    def _find_match(exs: list[dict]) -> str | None:
        for ex in exs:
            for item in ex.get("Giấy chứng nhận", []):
                sph = item.get("Số phát hành", "") if isinstance(item, dict) else ""
                if _is_match(sph, gcn_id):
                    return sph
        return None

    extractions = await asyncio.gather(*(_run(g) for g in groups))
    matched_value = _find_match(extractions)

    return {
        "extractions": extractions,
        "match": "khớp" if matched_value else "không khớp",
        "matched_so_phat_hanh": matched_value,
        "expected_gcn_id": gcn_id,
        "thinking_used": bool(force_thinking),
        "error": None,
        "page_count": page_count,
        "groups_detected": len(groups),
        "skip_reason": None,
    }


async def _run_file(path: str, gcn_id: str, sem: asyncio.Semaphore):
    async with sem:
        try:
            with open(path, "rb") as f:
                pdf = io.BytesIO(f.read())
            return path, await detect_and_check(pdf, gcn_id)
        except Exception as e:
            log.exception("pipeline_check failed for %s", path)
            return path, e


async def main():
    # (path, expected gcn_id) — chỉnh theo data thật của bạn
    samples = [
        ("tests/output/0_10363-GCN-BO 175400.pdf", "BO 175400"),
        ("tests/output/0_00019-GCN-DM 493045.pdf", "DM 493045"),
        ("tests/output/0_00277-GCN-10107250187.pdf", "10107250187"),
        ("tests/output/0_00019-GCN-DA 935939.pdf", "DA 935939"),
        ("tests/output/0_BO 175399-GCN.pdf",       "BO 175399"),
        ("tests/output/0_BN 953446-GCN.pdf",       "BN 953446"),
        ("tests/output/0_A 934013-GCN.pdf",        "A 934013"),
        ("tests/output/0_AA 00827763-GCN.pdf",     "AA 00827763"),
    ]
    max_concurrent = int(os.getenv("PIPELINE_MAX_CONCURRENT", "6"))
    sem = asyncio.Semaphore(max_concurrent)

    tasks = [asyncio.create_task(_run_file(p, gid, sem)) for p, gid in samples]

    results: list = []
    pbar = tqdm_asyncio(total=len(tasks), desc="GCN Check", unit="pdf")
    try:
        for fut in asyncio.as_completed(tasks):
            path, out = await fut
            results.append((path, out))
            if isinstance(out, Exception):
                tag = "ERR"
            else:
                tag = out["match"]
            pbar.set_postfix_str(f"last={os.path.basename(path)} {tag}")
            pbar.update(1)
    finally:
        pbar.close()

    print("\n=== KẾT QUẢ ===")
    for path, out in results:
        print(f"\n{path}")
        if isinstance(out, Exception):
            print(f"  ERROR: {out}")
            continue
        print(f"  expected: {out['expected_gcn_id']}")
        print(f"  match: {out['match']}  (matched={out['matched_so_phat_hanh']})")
        for ex in out["extractions"]:
            print(f"  pages {ex['page_indices']} err={ex['error']} → {ex['Giấy chứng nhận']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(main())
