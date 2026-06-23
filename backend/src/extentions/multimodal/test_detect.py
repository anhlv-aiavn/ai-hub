"""Test nhanh DETECT (phân loại biên từng trang) trên 1 hoặc nhiều PDF — không
cần Mongo/MinIO/worker, chỉ cần model VLM đang serve.

Render PDF (200 DPI, max 2000 như pipeline) → phân loại MỖI trang song song
(cover/content/other) → suy nhóm GCN tuyến tính, in ra để soi bằng mắt.

Chạy (env VLLM_BASE_URL / VLLM_MODEL_NAME trỏ máy serve; .env cũng được load):
    cd backend
    PYTHONPATH=. python -m src.extentions.multimodal.test_detect /path/a.pdf [/path/b.pdf ...]
"""

import asyncio
import io
import sys
import time

from src.extentions.multimodal.detect_gcn import classify_page
from src.extentions.multimodal.make import pdf_to_corrected_images

DPI = 200
MAX_SIZE = 2000


def groups_from_roles(roles: list[str]) -> list[list[int]]:
    """Bản sao logic suy nhóm của worker (run_job._groups_from_roles)."""
    groups: list[list[int]] = []
    cur: list[int] | None = None
    for i, r in enumerate(roles):
        if r == "cover":
            cur = [i]
            groups.append(cur)
        elif r == "content" and cur is not None:
            cur.append(i)
        else:  # "other" / content lạc không bìa → bỏ + đóng nhóm
            cur = None
    return [g for g in groups if g]


async def run_one(path: str) -> None:
    with open(path, "rb") as f:
        buf = io.BytesIO(f.read())
    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(
        None, lambda: pdf_to_corrected_images(buf, dpi=DPI, max_img_size=MAX_SIZE)
    )
    t0 = time.perf_counter()
    roles = list(await asyncio.gather(*(classify_page(im) for im in images)))
    dt = time.perf_counter() - t0
    groups = groups_from_roles(roles)

    print(f"\n{'=' * 64}\n{path}  · {len(images)} trang · {dt:.1f}s")
    print("  trang→vai trò: " + "  ".join(f"{i}:{r}" for i, r in enumerate(roles)))
    print(f"  → {len(groups)} GCN: {groups}")


async def main(paths: list[str]) -> None:
    for p in paths:
        try:
            await run_one(p)
        except Exception as e:  # noqa: BLE001
            print(f"\n[LỖI] {p}: {e}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Dùng: python -m src.extentions.multimodal.test_detect <file.pdf> [file2.pdf ...]")
        raise SystemExit(1)
    asyncio.run(main(args))
