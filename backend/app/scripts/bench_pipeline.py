"""Benchmark THROUGHPUT pipeline GCN theo đúng mô hình inflight của worker thật.

Mục tiêu: đo "1 phút / 15 phút xử lý được bao nhiêu bộ GCN" và bóc tách xem
thời gian đi đâu (render CPU vs detect VLM vs extract VLM) + VLM có bị bão hòa
không → biết cần tối ưu ở đâu.

Vì sao KHÔNG chạy qua worker/Mongo/MinIO: bench này gọi TRỰC TIẾP các hàm leaf
thật của pipeline (`count_pdf_pages_from_bytes`, `pdf_to_corrected_images`,
`classify_page`, `extract`) và tái tạo đúng orchestration của
`app/worker/run_job._pipeline` — cùng logic nhóm trang, cùng 1 semaphore VLM
TOÀN CỤC (config.MAX_VLM_CONCURRENT), cùng trần MAX_IN_FLIGHT file song song.
Nhờ đó số đo phản ánh production nhưng không phụ thuộc hạ tầng queue/S3, chỉ cần
kết nối được vLLM (VLLM_BASE_URL).

Cách chạy (trong container worker đã có env VLLM_*):
    # mặc định: 3 file trong tmp/, 60s, inflight=MAX_IN_FLIGHT, vlm=MAX_VLM_CONCURRENT
    docker compose exec worker python -m app.scripts.bench_pipeline \
        tmp/*.pdf --duration 60

    # bench 15 phút, thử nới trần VLM lên 16 xem GPU còn headroom không
    python -m app.scripts.bench_pipeline tmp/*.pdf --duration 900 --vlm 16

    # quét nhiều mức inflight/vlm để tìm điểm bão hòa, ghi CSV
    python -m app.scripts.bench_pipeline tmp/*.pdf --duration 120 \
        --sweep-vlm 4,8,16 --csv bench.csv

Đọc kết quả:
    - throughput: files/phút, groups(GCN)/phút, pages/phút + suy ra 15'.
    - latency p50/p90/p99 mỗi file.
    - breakdown: render / detect / extract chiếm bao nhiêu wall-time mỗi file.
    - VLM utilization = tổng-giây-call-VLM / (elapsed × MAX_VLM_CONCURRENT):
        ~1.0  → VLM (GPU) là nút cổ chai → cần nới trần/thêm GPU/giảm token.
        thấp  → nghẽn ở nơi khác (render CPU, hoặc MAX_IN_FLIGHT quá thấp).
    - vlm_wait: thời gian file phải CHỜ lấy slot semaphore. Cao mà GPU còn rảnh
      → tăng MAX_VLM_CONCURRENT. Cao mà GPU full → GPU là trần thật.
"""

import argparse
import asyncio
import glob
import io
import os
import statistics
import time
from dataclasses import dataclass, field

from app import config
from src.extentions.multimodal.detect_gcn import classify_page
from src.extentions.multimodal.extract_gcn import extract
from src.extentions.multimodal.make import (
    count_pdf_pages_from_bytes,
    pdf_to_corrected_images,
)

# Mirror hằng số render của run_job (production đọc từ env này).
DETECT_MIN_PAGES = int(os.getenv("DETECT_MIN_PAGES", "5"))
RENDER_DPI = int(os.getenv("AIHUB_RENDER_DPI", "200"))
RENDER_MAX_SIZE = int(os.getenv("AIHUB_RENDER_MAX_SIZE", "2000"))
MAX_PAGES = int(os.getenv("AIHUB_MAX_PAGES", "250"))


# ── Đo VLM: 1 semaphore toàn cục + kế toán busy/wait giống production ─────────

@dataclass
class VlmMeter:
    """Semaphore VLM toàn cục (thay _VLM_SEM của run_job) + kế toán thời gian.

    call_seconds: tổng wall-time NẰM TRONG các call VLM (đã có slot). Chia cho
      (elapsed × limit) ra utilization: đo GPU có bị bơm đầy không.
    wait_seconds: tổng thời gian CHỜ lấy slot. Cao → semaphore đang thắt cổ chai.
    """

    limit: int
    sem: asyncio.Semaphore = field(init=False)
    calls: int = 0
    call_seconds: float = 0.0
    wait_seconds: float = 0.0
    peak_inuse: int = 0
    _inuse: int = 0

    def __post_init__(self) -> None:
        self.sem = asyncio.Semaphore(self.limit)

    async def run(self, coro_factory):
        """Chạy 1 call VLM dưới trần semaphore, đo wait + busy."""
        t_wait = time.perf_counter()
        async with self.sem:
            self.wait_seconds += time.perf_counter() - t_wait
            self._inuse += 1
            self.peak_inuse = max(self.peak_inuse, self._inuse)
            t0 = time.perf_counter()
            try:
                return await coro_factory()
            finally:
                self.call_seconds += time.perf_counter() - t0
                self.calls += 1
                self._inuse -= 1


# ── 1 lần chạy pipeline cho 1 file (mirror run_job._pipeline, có instrument) ──

@dataclass
class FileRun:
    file: str
    total_s: float = 0.0
    render_s: float = 0.0
    detect_s: float = 0.0      # wall-time chặng detect (classify song song)
    extract_s: float = 0.0     # wall-time chặng extract (các nhóm song song)
    pages: int = 0
    groups: int = 0
    vlm_calls: int = 0
    error: str | None = None
    skipped: str | None = None


def _groups_from_roles(roles: list[str]) -> list[list[int]]:
    """Copy nguyên logic run_job._groups_from_roles (giữ bench khỏi phụ thuộc
    module worker/storage). cover mở nhóm mới; content nối nhóm đang mở; other
    (hoặc content lạc không bìa) đóng nhóm."""
    groups: list[list[int]] = []
    cur: list[int] | None = None
    for i, role in enumerate(roles):
        if role == "cover":
            cur = [i]
            groups.append(cur)
        elif role == "content" and cur is not None:
            cur.append(i)
        else:
            cur = None
    return [g for g in groups if g]


async def run_one(path: str, pdf_bytes: bytes, meter: VlmMeter) -> FileRun:
    r = FileRun(file=os.path.basename(path))
    t_start = time.perf_counter()
    loop = asyncio.get_running_loop()
    buf = io.BytesIO(pdf_bytes)

    try:
        n = await loop.run_in_executor(None, count_pdf_pages_from_bytes, buf)
        if n == 0:
            r.error = "empty"
            return r
        if n > MAX_PAGES:
            r.skipped = f"too_many_pages:{n}"
            r.pages = n
            return r

        # ── render (CPU / process-pool) ──
        t0 = time.perf_counter()

        def _render(b: io.BytesIO):
            return pdf_to_corrected_images(b, dpi=RENDER_DPI, max_img_size=RENDER_MAX_SIZE)

        images = await loop.run_in_executor(None, _render, buf)
        r.render_s = time.perf_counter() - t0
        if not images:
            r.error = "render_empty"
            return r
        r.pages = len(images)

        # ── detect: classify từng trang (song song, dưới trần VLM) ──
        t0 = time.perf_counter()
        if len(images) <= DETECT_MIN_PAGES:
            groups = [list(range(len(images)))]
        else:
            async def _cls(i: int) -> str:
                try:
                    return await meter.run(lambda: classify_page(images[i]))
                except Exception:  # noqa: BLE001 - fail-safe giống production
                    return "content"

            roles = await asyncio.gather(*(_cls(i) for i in range(len(images))))
            groups = _groups_from_roles(list(roles))
        r.detect_s = time.perf_counter() - t0
        r.groups = len(groups)
        if not groups:
            r.error = "no_gcn_detected"
            return r

        # ── extract: mỗi nhóm 1 call VLM, song song, dưới trần ──
        t0 = time.perf_counter()

        async def _ext(group: list[int]) -> None:
            imgs = [images[i] for i in group if 0 <= i < len(images)]
            await meter.run(lambda: extract(imgs))

        await asyncio.gather(*(_ext(g) for g in groups))
        r.extract_s = time.perf_counter() - t0

    except Exception as e:  # noqa: BLE001
        r.error = str(e)
    finally:
        r.total_s = time.perf_counter() - t_start
    return r


# ── Driver: giữ MAX_IN_FLIGHT file chạy liên tục trong `duration` giây ────────

async def bench(
    files: list[tuple[str, bytes]],
    duration: float,
    in_flight: int,
    vlm_limit: int,
) -> dict:
    meter = VlmMeter(limit=vlm_limit)
    results: list[FileRun] = []
    deadline = time.monotonic() + duration
    idx = 0  # round-robin con trỏ file
    idx_lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal idx
        while time.monotonic() < deadline:
            async with idx_lock:
                path, data = files[idx % len(files)]
                idx += 1
            res = await run_one(path, data, meter)
            results.append(res)

    t_start = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(in_flight)))
    elapsed = time.perf_counter() - t_start

    return {
        "elapsed": elapsed,
        "results": results,
        "meter": meter,
        "in_flight": in_flight,
        "vlm_limit": vlm_limit,
    }


# ── Báo cáo ──────────────────────────────────────────────────────────────────

def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))
    return s[k]


def summarize(run: dict) -> dict:
    results: list[FileRun] = run["results"]
    meter: VlmMeter = run["meter"]
    elapsed = run["elapsed"]

    ok = [r for r in results if r.error is None and r.skipped is None]
    errored = [r for r in results if r.error is not None]
    skipped = [r for r in results if r.skipped is not None]

    n_files = len(results)
    n_ok = len(ok)
    n_groups = sum(r.groups for r in ok)
    n_pages = sum(r.pages for r in ok)

    totals = [r.total_s for r in ok]
    files_per_min = n_ok / elapsed * 60 if elapsed else 0
    groups_per_min = n_groups / elapsed * 60 if elapsed else 0
    pages_per_min = n_pages / elapsed * 60 if elapsed else 0

    def _avg(attr: str) -> float:
        vals = [getattr(r, attr) for r in ok]
        return statistics.mean(vals) if vals else 0.0

    vlm_capacity = elapsed * meter.limit
    vlm_util = meter.call_seconds / vlm_capacity if vlm_capacity else 0.0

    return {
        "in_flight": run["in_flight"],
        "vlm_limit": run["vlm_limit"],
        "elapsed": elapsed,
        "n_files": n_files,
        "n_ok": n_ok,
        "n_err": len(errored),
        "n_skip": len(skipped),
        "n_groups": n_groups,
        "n_pages": n_pages,
        "files_per_min": files_per_min,
        "groups_per_min": groups_per_min,
        "pages_per_min": pages_per_min,
        "files_15min": files_per_min * 15,
        "groups_15min": groups_per_min * 15,
        "p50": _pct(totals, 50),
        "p90": _pct(totals, 90),
        "p99": _pct(totals, 99),
        "avg_total": statistics.mean(totals) if totals else 0.0,
        "avg_render": _avg("render_s"),
        "avg_detect": _avg("detect_s"),
        "avg_extract": _avg("extract_s"),
        "vlm_calls": meter.calls,
        "vlm_call_seconds": meter.call_seconds,
        "vlm_wait_seconds": meter.wait_seconds,
        "vlm_util": vlm_util,
        "vlm_peak_inuse": meter.peak_inuse,
        "errors": [(r.file, r.error) for r in errored][:10],
    }


def print_report(s: dict) -> None:
    print("\n" + "=" * 68)
    print(f" KẾT QUẢ BENCH  ·  inflight={s['in_flight']}  vlm={s['vlm_limit']}  "
          f"·  elapsed={s['elapsed']:.1f}s")
    print("=" * 68)
    print(f" Hoàn tất : {s['n_ok']} file OK  |  {s['n_err']} lỗi  |  {s['n_skip']} skip")
    print(f"            {s['n_groups']} bộ GCN (group)  ·  {s['n_pages']} trang")
    print("-" * 68)
    print(" THROUGHPUT")
    print(f"   files/phút   : {s['files_per_min']:8.1f}   → 15 phút ≈ {s['files_15min']:.0f} file")
    print(f"   GCN/phút     : {s['groups_per_min']:8.1f}   → 15 phút ≈ {s['groups_15min']:.0f} bộ")
    print(f"   trang/phút   : {s['pages_per_min']:8.1f}")
    print("-" * 68)
    print(" LATENCY/FILE (giây)")
    print(f"   p50={s['p50']:.1f}   p90={s['p90']:.1f}   p99={s['p99']:.1f}   avg={s['avg_total']:.1f}")
    print("-" * 68)
    print(" BREAKDOWN wall-time TRUNG BÌNH mỗi file (giây)")
    tot = max(s['avg_total'], 1e-9)
    for name, v in (("render (CPU)", s['avg_render']),
                    ("detect (VLM)", s['avg_detect']),
                    ("extract(VLM)", s['avg_extract'])):
        print(f"   {name:14s}: {v:6.1f}s  ({v / tot * 100:4.1f}% wall/file)")
    print("   (tổng chặng < total vì các file chờ nhau ở semaphore VLM)")
    print("-" * 68)
    print(" VLM (nút cổ chai?)")
    print(f"   calls        : {s['vlm_calls']}")
    print(f"   utilization  : {s['vlm_util'] * 100:5.1f}%  "
          f"(giây-call / (elapsed × {s['vlm_limit']}))")
    print(f"   peak in-use  : {s['vlm_peak_inuse']} / {s['vlm_limit']}")
    print(f"   tổng chờ slot: {s['vlm_wait_seconds']:.1f}s")
    print("   → util ~100% ⇒ VLM/GPU là trần: nới MAX_VLM_CONCURRENT hoặc thêm GPU.")
    print("   → util thấp mà chờ-slot cao ⇒ tăng trần VLM. Cả hai thấp ⇒ nghẽn")
    print("     render CPU hoặc MAX_IN_FLIGHT quá nhỏ.")
    if s["errors"]:
        print("-" * 68)
        print(" LỖI (tối đa 10):")
        for f, e in s["errors"]:
            print(f"   {f}: {e}")
    print("=" * 68 + "\n")


def _csv_row(s: dict) -> str:
    return ",".join(str(x) for x in [
        s["in_flight"], s["vlm_limit"], f"{s['elapsed']:.1f}", s["n_ok"], s["n_err"],
        f"{s['files_per_min']:.2f}", f"{s['groups_per_min']:.2f}", f"{s['pages_per_min']:.2f}",
        f"{s['p50']:.1f}", f"{s['p90']:.1f}", f"{s['p99']:.1f}",
        f"{s['avg_render']:.1f}", f"{s['avg_detect']:.1f}", f"{s['avg_extract']:.1f}",
        f"{s['vlm_util']:.3f}", f"{s['vlm_wait_seconds']:.1f}", s["vlm_peak_inuse"],
    ])


_CSV_HEADER = ("in_flight,vlm_limit,elapsed,n_ok,n_err,files_per_min,groups_per_min,"
               "pages_per_min,p50,p90,p99,avg_render,avg_detect,avg_extract,"
               "vlm_util,vlm_wait_s,vlm_peak")


async def _amain(args) -> None:
    paths: list[str] = []
    for pat in args.files:
        paths.extend(sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat])
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        raise SystemExit("Không tìm thấy file PDF nào khớp.")

    # Đọc file vào RAM 1 lần (loại I/O đĩa khỏi phép đo — đúng bản chất production
    # nơi PDF đã nằm trong bộ nhớ sau khi tải từ MinIO).
    files = [(p, open(p, "rb").read()) for p in paths]
    print(f"[i] {len(files)} file · duration={args.duration}s · "
          f"render dpi={RENDER_DPI} max={RENDER_MAX_SIZE}")
    for p, b in files:
        print(f"    - {os.path.basename(p)} ({len(b) / 1024:.0f} KB)")

    in_flight = args.in_flight or config.MAX_IN_FLIGHT
    vlm_levels = ([int(x) for x in args.sweep_vlm.split(",")]
                  if args.sweep_vlm else [args.vlm or config.MAX_VLM_CONCURRENT])

    if args.warmup > 0:
        print(f"[i] Warmup {args.warmup}s (JIT/kết nối/cache orientation)…")
        await bench(files, args.warmup, in_flight, vlm_levels[0])

    csv_lines = [_CSV_HEADER]
    for vlm in vlm_levels:
        print(f"\n[i] ▶ Chạy: inflight={in_flight}, vlm={vlm}, {args.duration}s …")
        run = await bench(files, args.duration, in_flight, vlm)
        s = summarize(run)
        print_report(s)
        csv_lines.append(_csv_row(s))

    if args.csv:
        with open(args.csv, "w") as f:
            f.write("\n".join(csv_lines) + "\n")
        print(f"[i] Đã ghi CSV → {args.csv}")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Bench throughput pipeline GCN theo mô hình inflight worker.")
    p.add_argument("files", nargs="+", help="Đường dẫn PDF (hỗ trợ glob).")
    p.add_argument("--duration", type=float, default=60,
                   help="Số giây chạy tải bền (mặc định 60; dùng 900 cho 15 phút).")
    p.add_argument("--in-flight", type=int, default=None,
                   help="Số file song song (mặc định config.MAX_IN_FLIGHT).")
    p.add_argument("--vlm", type=int, default=None,
                   help="Trần call VLM đồng thời (mặc định config.MAX_VLM_CONCURRENT).")
    p.add_argument("--sweep-vlm", default=None,
                   help="Quét nhiều mức trần VLM, vd '4,8,16' (ghi đè --vlm).")
    p.add_argument("--warmup", type=float, default=0,
                   help="Giây warmup trước khi đo (không tính vào kết quả).")
    p.add_argument("--csv", default=None, help="Ghi bảng tổng hợp ra CSV.")
    asyncio.run(_amain(p.parse_args()))


if __name__ == "__main__":
    main()
