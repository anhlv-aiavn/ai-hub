"""Bench ĐƯỜNG THẬT (real-path) — lấy file thật từ MinIO qua Mongo để tìm chỗ tắc.

Khác `bench_pipeline.py` (đọc file local, chỉ đo model): script này tái hiện
ĐÚNG những gì worker làm cho mỗi doc và ĐO GIỜ TỪNG CHẶNG:

    download (MinIO)  →  render (CPU)  →  detect (VLM)  →  extract (VLM)
    →  cut-encode (PIL, mô phỏng _images_to_pdf)  →  cut-upload (MinIO)

Nhờ đó thấy production chậm ở đâu: tải MinIO? encode PIL chẹn event loop? upload
cut? hay chính VLM (cache nguội vì ảnh khác nhau)? — thứ mà bench model KHÔNG lộ.

KHÔNG ghi gì vào `gcn`/`batch` (chỉ ĐỌC key để lấy mẫu). Cut mặc định KHÔNG
upload (chỉ đo thời gian encode PIL — nghi phạm chẹn loop); bật --upload để đo
luôn mạng ghi (ghi vào prefix tạm `_bench_tmp/`, tự xoá cuối).

Cách chạy (trong container api/worker có Mongo + MinIO):
    docker compose exec api python -m app.scripts.bench_realpath --limit 30

    # so sánh: cut encode chạy đồng bộ (như prod) vs offload executor (bản vá)
    docker compose exec api python -m app.scripts.bench_realpath --limit 30 --cuts-mode sync
    docker compose exec api python -m app.scripts.bench_realpath --limit 30 --cuts-mode executor

    # đo cả upload cut (ghi prefix tạm rồi xoá)
    docker compose exec api python -m app.scripts.bench_realpath --limit 30 --upload

    # giới hạn 1 lô / 1 trạng thái nguồn
    docker compose exec api python -m app.scripts.bench_realpath --batch <batch_id> --status done --limit 50

Đọc kết quả: cột chặng nào chiếm % wall lớn nhất = nút cổ chai thật. Đối chiếu
`vlm util` với bench model: nếu ở đây util THẤP hơn hẳn (dù cùng inflight/vlm) →
GPU đang bị đói vì các chặng I/O/PIL, không phải model chậm.
"""

import argparse
import asyncio
import base64
import functools
import io
import os
import statistics
import time
import uuid
from dataclasses import dataclass, field

from PIL import Image

from app import config, storage
from app.scripts.bench_pipeline import VlmMeter, _groups_from_roles, _pct
from src.extentions.mongo_helper import AsyncMongo
from src.extentions.multimodal.detect_gcn import classify_page
from src.extentions.multimodal.extract_gcn import extract
from src.extentions.multimodal.make import (
    count_pdf_pages_from_bytes,
    pdf_to_corrected_images,
)

DETECT_MIN_PAGES = int(os.getenv("DETECT_MIN_PAGES", "5"))
RENDER_DPI = int(os.getenv("AIHUB_RENDER_DPI", "200"))
RENDER_MAX_SIZE = int(os.getenv("AIHUB_RENDER_MAX_SIZE", "2000"))
MAX_PAGES = int(os.getenv("AIHUB_MAX_PAGES", "250"))

_BENCH_PREFIX = "_bench_tmp/"


@dataclass
class Stage:
    """Kế toán 1 chặng: danh sách thời gian mỗi file để tính p50/p90/avg."""
    times: list[float] = field(default_factory=list)

    def add(self, t: float) -> None:
        self.times.append(t)

    @property
    def avg(self) -> float:
        return statistics.mean(self.times) if self.times else 0.0


@dataclass
class Rec:
    key: str
    total: float = 0.0
    download: float = 0.0
    render: float = 0.0
    detect: float = 0.0
    extract: float = 0.0
    cut_encode: float = 0.0
    cut_upload: float = 0.0
    pages: int = 0
    groups: int = 0
    nbytes: int = 0
    error: str | None = None
    skipped: str | None = None


def _images_to_pdf(b64_list: list[str]) -> bytes | None:
    """Copy nguyên _images_to_pdf của run_job (decode base64 + PIL encode PDF) —
    đây là phần prod chạy ĐỒNG BỘ trong event loop, nghi làm nghẽn."""
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


async def run_one(doc: dict, meter: VlmMeter, cuts_mode: str, upload: bool,
                  cleanup: list[str]) -> Rec:
    key = doc.get("s3_key") or doc.get("_id")
    r = Rec(key=os.path.basename(str(key)))
    loop = asyncio.get_running_loop()
    t_start = time.perf_counter()

    try:
        # ── download (MinIO) — ĐÚNG đường prod ──
        t0 = time.perf_counter()
        pdf_buf = await storage.get_pdf(doc["s3_key"], doc.get("source_connection_id"))
        r.download = time.perf_counter() - t0
        data = pdf_buf.getvalue()
        r.nbytes = len(data)
        if data[:4] != b"%PDF":
            r.error = "not_pdf"
            return r

        n = await loop.run_in_executor(None, count_pdf_pages_from_bytes, io.BytesIO(data))
        if n == 0:
            r.error = "empty"
            return r
        if n > MAX_PAGES:
            r.skipped = f"too_many_pages:{n}"
            r.pages = n
            return r

        # ── render (CPU / process-pool) ──
        t0 = time.perf_counter()
        render = functools.partial(
            pdf_to_corrected_images, dpi=RENDER_DPI, max_img_size=RENDER_MAX_SIZE)
        images = await loop.run_in_executor(None, render, io.BytesIO(data))
        r.render = time.perf_counter() - t0
        if not images:
            r.error = "render_empty"
            return r
        r.pages = len(images)

        # ── detect (classify từng trang, dưới trần VLM) ──
        t0 = time.perf_counter()
        if len(images) <= DETECT_MIN_PAGES:
            groups = [list(range(len(images)))]
        else:
            async def _cls(i: int) -> str:
                try:
                    return await meter.run(lambda: classify_page(images[i]))
                except Exception:  # noqa: BLE001
                    return "content"
            roles = await asyncio.gather(*(_cls(i) for i in range(len(images))))
            groups = _groups_from_roles(list(roles))
        r.detect = time.perf_counter() - t0
        r.groups = len(groups)
        if not groups:
            r.error = "no_gcn_detected"
            return r

        # ── extract (mỗi nhóm 1 call VLM, dưới trần) ──
        t0 = time.perf_counter()

        async def _ext(group: list[int]) -> dict:
            imgs = [images[i] for i in group if 0 <= i < len(images)]
            return await meter.run(lambda: extract(imgs))

        results = await asyncio.gather(*(_ext(g) for g in groups))
        r.extract = time.perf_counter() - t0

        # ── cut-encode (PIL) — mô phỏng _build_cuts ──
        if cuts_mode != "off":
            t0 = time.perf_counter()
            pdf_blobs: list[bytes] = []
            for group in groups:
                imgs = [images[i] for i in group if 0 <= i < len(images)]
                if cuts_mode == "executor":
                    blob = await loop.run_in_executor(None, _images_to_pdf, imgs)
                else:  # "sync" — chạy thẳng trong event loop, ĐÚNG như prod
                    blob = _images_to_pdf(imgs)
                if blob:
                    pdf_blobs.append(blob)
            r.cut_encode = time.perf_counter() - t0

            # ── cut-upload (MinIO) — tuỳ chọn ──
            if upload and pdf_blobs:
                t0 = time.perf_counter()
                for i, blob in enumerate(pdf_blobs):
                    ckey = f"{_BENCH_PREFIX}{uuid.uuid4()}-{i}.pdf"
                    await storage.put_pdf(ckey, blob)
                    cleanup.append(ckey)
                r.cut_upload = time.perf_counter() - t0

    except Exception as e:  # noqa: BLE001
        r.error = str(e)
    finally:
        r.total = time.perf_counter() - t_start
    return r


async def sample_docs(mongo: AsyncMongo, limit: int, batch: str | None,
                      status: str | None) -> list[dict]:
    match: dict = {"s3_key": {"$ne": None}}
    if batch:
        match["batch_id"] = batch
    if status:
        match["status"] = status
    pipeline = [{"$match": match}, {"$sample": {"size": limit}},
                {"$project": {"s3_key": 1, "source_connection_id": 1, "status": 1}}]
    return await mongo.db[config.COLL_GCN].aggregate(pipeline).to_list(length=limit)


async def _amain(args) -> None:
    mongo = AsyncMongo()
    if not await mongo.ping():
        raise SystemExit("Không kết nối được Mongo.")

    docs = await sample_docs(mongo, args.limit, args.batch, args.status)
    if not docs:
        raise SystemExit("Không lấy được doc mẫu nào (kiểm tra --batch/--status).")

    in_flight = args.in_flight or config.MAX_IN_FLIGHT
    vlm = args.vlm or config.MAX_VLM_CONCURRENT
    print(f"[i] {len(docs)} file thật · inflight={in_flight} vlm={vlm} · "
          f"cuts={args.cuts_mode} upload={args.upload} · dpi={RENDER_DPI} max={RENDER_MAX_SIZE}")

    meter = VlmMeter(limit=vlm)
    cleanup: list[str] = []
    recs: list[Rec] = []
    sem = asyncio.Semaphore(in_flight)  # trần file song song (giống MAX_IN_FLIGHT)

    async def _guarded(doc: dict) -> None:
        async with sem:
            recs.append(await run_one(doc, meter, args.cuts_mode, args.upload, cleanup))

    t0 = time.perf_counter()
    await asyncio.gather(*(_guarded(d) for d in docs))
    elapsed = time.perf_counter() - t0

    if cleanup:
        print(f"[i] Dọn {len(cleanup)} cut tạm…")
        for k in cleanup:
            try:
                await storage.delete_prefix(_BENCH_PREFIX)
                break  # delete_prefix xoá cả prefix 1 lần
            except Exception:  # noqa: BLE001
                pass

    _report(recs, meter, elapsed, in_flight, vlm)


def _report(recs: list[Rec], meter: VlmMeter, elapsed: float, in_flight: int, vlm: int) -> None:
    ok = [r for r in recs if r.error is None and r.skipped is None]
    n_ok = len(ok)
    files_per_min = n_ok / elapsed * 60 if elapsed else 0
    groups = sum(r.groups for r in ok)

    def avg(attr: str) -> float:
        v = [getattr(r, a) for r in ok for a in [attr]]
        return statistics.mean(v) if v else 0.0

    def p(attr: str, q: float) -> float:
        return _pct([getattr(r, attr) for r in ok], q)

    vlm_cap = elapsed * meter.limit
    util = meter.call_seconds / vlm_cap if vlm_cap else 0.0
    avg_bytes = statistics.mean([r.nbytes for r in ok]) / 1024 if ok else 0

    print("\n" + "=" * 72)
    print(f" REAL-PATH BENCH · inflight={in_flight} vlm={vlm} · elapsed={elapsed:.1f}s")
    print("=" * 72)
    print(f" {n_ok} OK | {len([r for r in recs if r.error])} lỗi | "
          f"{len([r for r in recs if r.skipped])} skip · {groups} bộ GCN · "
          f"avg {avg_bytes:.0f} KB/file")
    print("-" * 72)
    print(f" THROUGHPUT: {files_per_min:.1f} file/phút  → 15' ≈ {files_per_min * 15:.0f}")
    print("-" * 72)
    print(f" {'CHẶNG':<16}{'avg(s)':>9}{'p50':>8}{'p90':>8}{'% wall':>9}")
    tot = max(avg("total"), 1e-9)
    for name, attr in (("download", "download"), ("render", "render"),
                       ("detect(VLM)", "detect"), ("extract(VLM)", "extract"),
                       ("cut-encode", "cut_encode"), ("cut-upload", "cut_upload")):
        a = avg(attr)
        print(f" {name:<16}{a:>9.2f}{p(attr, 50):>8.2f}{p(attr, 90):>8.2f}{a / tot * 100:>8.1f}%")
    print(f" {'TOTAL/file':<16}{avg('total'):>9.2f}{p('total', 50):>8.2f}{p('total', 90):>8.2f}")
    print("-" * 72)
    print(" VLM")
    print(f"   utilization : {util * 100:5.1f}%   peak {meter.peak_inuse}/{meter.limit}   "
          f"chờ-slot {meter.wait_seconds:.0f}s")
    print("   → so với bench model: util ở đây THẤP hơn nhiều ⇒ GPU bị đói vì I/O")
    print("     (download/PIL/upload) hoặc encode PIL đồng bộ chẹn event loop.")
    errs = [(r.key, r.error) for r in recs if r.error][:8]
    if errs:
        print("-" * 72)
        for k, e in errs:
            print(f"   LỖI {k}: {e}")
    print("=" * 72 + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Bench real-path (MinIO→pipeline) đo từng chặng.")
    p.add_argument("--limit", type=int, default=30, help="Số file thật lấy mẫu (mặc định 30).")
    p.add_argument("--batch", default=None, help="Chỉ lấy trong 1 batch_id.")
    p.add_argument("--status", default=None, help="Lọc theo status doc (vd done/queued).")
    p.add_argument("--in-flight", type=int, default=None, help="Trần file song song.")
    p.add_argument("--vlm", type=int, default=None, help="Trần call VLM đồng thời.")
    p.add_argument("--cuts-mode", choices=["sync", "executor", "off"], default="sync",
                   help="Cut-encode: sync=như prod (chẹn loop), executor=offload, off=bỏ.")
    p.add_argument("--upload", action="store_true", help="Đo luôn upload cut lên MinIO (prefix tạm).")
    asyncio.run(_amain(p.parse_args()))


if __name__ == "__main__":
    main()
