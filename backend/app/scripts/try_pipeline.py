"""CLI thử nghiệm detect / extract trên 1 file PDF — để tune prompt & pipeline.

Chạy (trong container API/worker đã có env VLLM_*):
    docker compose exec worker python -m app.scripts.try_pipeline /path/to.pdf

Ví dụ:
    # detect + extract bình thường (windowed như pipeline)
    python -m app.scripts.try_pipeline a.pdf

    # chỉ detect (xem nhóm trang), cả file trong 1 call (window=0)
    python -m app.scripts.try_pipeline a.pdf --detect-only --window 0

    # bỏ detect, extract đúng các trang chỉ định (test prompt extract)
    python -m app.scripts.try_pipeline a.pdf --extract-pages 0,1

    # bật thinking + lưu ảnh đã xoay để xem model "nhìn" gì + ghi JSON
    python -m app.scripts.try_pipeline a.pdf --thinking --save-images ./imgs --out out.json

Mỗi lần sửa prompt trong src/extentions/multimodal/prompt.py → chạy lại file này.
"""

import argparse
import asyncio
import base64
import functools
import io
import json
import os
import time

from src.extentions.multimodal.detect_gcn import detect
from src.extentions.multimodal.extract_gcn import extract
from src.extentions.multimodal.make import pdf_to_corrected_images
from src.extentions.multimodal.normalize_dang_ky import normalize_dang_ky


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _save_images(images: list[str], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for i, b in enumerate(images):
        with open(os.path.join(out_dir, f"page_{i:03d}.png"), "wb") as f:
            f.write(base64.b64decode(b))
    print(f"[i] Đã lưu {len(images)} ảnh (đã xoay) vào {out_dir}")


async def _windowed_detect(images: list[str], window: int) -> list[list[int]]:
    n = len(images)
    if window <= 0 or n <= window:
        d = await detect(images)
        return d.get("gcn_pages") or [list(range(n))]
    wins = [(i, min(i + window, n)) for i in range(0, n, window)]

    async def _win(lo, hi):
        d = await detect(images[lo:hi])
        out = []
        for g in d.get("gcn_pages") or []:
            gg = sorted({lo + j for j in g if 0 <= j < (hi - lo)})
            if gg:
                out.append(gg)
        return out

    parts = await asyncio.gather(*(_win(lo, hi) for lo, hi in wins))
    return [g for part in parts for g in part]


async def run(args) -> None:
    with open(args.pdf, "rb") as f:
        pdf_buf = io.BytesIO(f.read())

    thinking = True if args.thinking else None

    t0 = time.time()
    render = functools.partial(pdf_to_corrected_images, dpi=args.dpi, max_img_size=args.max_size)
    images = await asyncio.get_running_loop().run_in_executor(None, render, pdf_buf)
    print(f"[i] Render + xoay: {len(images)} trang @ dpi={args.dpi} max={args.max_size} ({time.time() - t0:.1f}s)")
    if args.save_images:
        _save_images(images, args.save_images)

    # Xác định các nhóm trang để extract.
    if args.extract_pages:
        groups = [[int(x) for x in args.extract_pages.split(",") if x.strip() != ""]]
        print(f"[i] Bỏ detect — extract trang chỉ định: {groups}")
    else:
        t1 = time.time()
        groups = await _windowed_detect(images, args.window)
        print(f"[i] DETECT ({time.time() - t1:.1f}s) → {len(groups)} nhóm GCN:")
        print(_dump(groups))

    if args.detect_only:
        return

    results = []
    for gi, group in enumerate(groups):
        imgs = [images[i] for i in group if 0 <= i < len(images)]
        if not imgs:
            continue
        t2 = time.time()
        res = await extract(imgs, enable_thinking=thinking)
        dt = time.time() - t2
        if args.normalize:
            res = normalize_dang_ky(res)
        print(f"\n===== GCN #{gi + 1} · trang {group} · extract {dt:.1f}s =====")
        print(_dump(res))
        results.append({"page_indices": group, "result": res})

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(_dump({"groups": groups, "results": results}))
        print(f"\n[i] Đã ghi kết quả → {args.out}")


def main() -> None:
    p = argparse.ArgumentParser(description="Thử detect/extract GCN trên 1 PDF (tune prompt/pipeline).")
    p.add_argument("pdf", help="Đường dẫn file PDF.")
    p.add_argument("--window", type=int, default=12,
                   help="Kích thước cửa sổ detect (trang). 0 = detect cả file 1 call. Mặc định 12.")
    p.add_argument("--dpi", type=int, default=150, help="DPI render PDF (mặc định 150).")
    p.add_argument("--max-size", type=int, default=1344,
                   help="Cạnh dài tối đa ảnh gửi VLM (mặc định 1344; tăng để đọc Số phát hành mờ).")
    p.add_argument("--detect-only", action="store_true", help="Chỉ chạy detect, in nhóm trang.")
    p.add_argument("--extract-pages", default=None,
                   help="Bỏ detect; extract đúng các trang này (vd '0,1,2').")
    p.add_argument("--thinking", action="store_true", help="Bật chain-of-thought khi extract.")
    p.add_argument("--normalize", action="store_true", help="Áp normalize_dang_ky lên kết quả.")
    p.add_argument("--save-images", default=None, help="Thư mục lưu ảnh trang đã xoay (PNG).")
    p.add_argument("--out", default=None, help="Ghi JSON kết quả ra file.")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
