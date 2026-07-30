"""Màn hình THEO DÕI TRỰC TIẾP luồng xử lý (terminal, không cần UI). CHỈ ĐỌC —
an toàn chạy trên production. Đọc `batch.counts` (rẻ, không quét gcn) để tính
throughput hồ sơ/phút, số đang xử lý, ETA; sample lỗi + timing từng chặng để
bóc tách nút thắt (download S3 vs pipeline vs ghi cut).

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.watch                 # mọi lô, 5s
    docker compose exec api python -m app.scripts.watch --interval 3
    docker compose exec api python -m app.scripts.watch --batch <id>    # 1 lô
    docker compose exec api python -m app.scripts.watch --timings       # + phân bố thời gian chặng
    docker compose exec api python -m app.scripts.watch --once          # in 1 lần rồi thoát

Cột timing chỉ có dữ liệu khi worker chạy với AIHUB_TRACE_TIMINGS=true (mặc định
tắt). Bật nó (chỉ cần trên service worker) để thấy download/pipeline/cuts.
"""

import argparse
import asyncio
import time

from app import config
from app.db import batches, gcns

_STATES = ("queued", "processing", "done", "error", "no_gcn", "skip", "dead")
_TERMINAL = ("done", "error", "no_gcn", "skip", "dead")  # đã rời hàng đợi vĩnh viễn
CLEAR = "\033[2J\033[H"


async def _counts(batch_id: str | None) -> dict:
    """Tổng batch.counts (mọi lô hoặc 1 lô) — không quét gcn."""
    match = {"_id": batch_id} if batch_id else {}
    total = {s: 0 for s in _STATES}
    async for b in batches().find(match, {"counts": 1}):
        c = b.get("counts") or {}
        for s in _STATES:
            total[s] += int(c.get(s) or 0)
    return total


async def _active_batches(limit: int = 6) -> list[dict]:
    """Lô đang có việc (processing>0 hoặc queued>0), nhiều processing lên trước."""
    rows = []
    async for b in batches().find({}, {"name": 1, "counts": 1}):
        c = b.get("counts") or {}
        proc, q = int(c.get("processing") or 0), int(c.get("queued") or 0)
        if proc or q:
            rows.append((proc, q, b.get("name") or b["_id"]))
    rows.sort(reverse=True)
    return rows[:limit]


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    vals = sorted(vals)
    i = min(len(vals) - 1, int(p / 100 * len(vals)))
    return vals[i]


async def _timings(batch_id: str | None, n: int = 400) -> dict:
    """Sample doc có `timings` → p50/p90 mỗi chặng. $sample: rẻ, không sort."""
    match = {"timings": {"$exists": True}}
    if batch_id:
        match["batch_id"] = batch_id
    buckets: dict[str, list[float]] = {
        "download": [], "render": [], "detect": [], "extract": [], "pipeline": [], "cuts": []}
    pages: list[float] = []
    cur = gcns().aggregate([{"$match": match}, {"$sample": {"size": n}},
                            {"$project": {"timings": 1}}])
    cnt = 0
    async for d in cur:
        t = d.get("timings") or {}
        cnt += 1
        for k in buckets:
            if isinstance(t.get(k), (int, float)):
                buckets[k].append(float(t[k]))
        if isinstance(t.get("page_count"), (int, float)):
            pages.append(float(t["page_count"]))
    return {"n": cnt, "buckets": buckets, "pages": pages}


async def _err_kinds(batch_id: str | None, n: int = 300) -> list[tuple[int, str]]:
    match = {"status": "error"}
    if batch_id:
        match["batch_id"] = batch_id
    agg = {}
    cur = gcns().aggregate([{"$match": match}, {"$sample": {"size": n}},
                            {"$project": {"error_kind": 1}}])
    async for d in cur:
        k = d.get("error_kind") or "(none)"
        agg[k] = agg.get(k, 0) + 1
    return sorted(((v, k) for k, v in agg.items()), reverse=True)


def _fmt_dur(sec: float) -> str:
    sec = int(sec)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


async def render(args, prev: dict | None) -> dict:
    now = time.monotonic()
    counts = await _counts(args.batch)
    done_total = sum(counts[s] for s in _TERMINAL)
    total = sum(counts.values())

    lines = []
    scope = f"lô {args.batch}" if args.batch else "mọi lô"
    lines.append(f"AI-HUB watch · {time.strftime('%H:%M:%S')} · {scope} · interval {args.interval}s")
    lines.append("─" * 60)
    pct = (done_total / total * 100) if total else 0
    lines.append(f"Tiến độ: {done_total:,}/{total:,} ({pct:.1f}%)")
    for s in _STATES:
        bar = "  ← đang chạy" if s == "processing" and counts[s] else ""
        lines.append(f"  {s:<11} {counts[s]:>12,}{bar}")

    # Throughput từ delta terminal-count giữa 2 tick.
    lines.append("─" * 60)
    if prev:
        dt = now - prev["t"]
        d_done = done_total - prev["done_total"]
        rate_min = (d_done / dt * 60) if dt > 0 else 0
        # rate trung bình từ lúc bắt đầu watch
        dt0 = now - prev["t0"]
        mean_min = ((done_total - prev["done_total0"]) / dt0 * 60) if dt0 > 0 else 0
        lines.append(f"Throughput: {d_done:+,} trong {dt:.1f}s  →  {rate_min:,.0f} hồ sơ/phút")
        lines.append(f"Trung bình (từ lúc watch): {mean_min:,.0f} hồ sơ/phút")
        remain = counts["queued"] + counts["processing"]
        if mean_min > 0 and remain:
            lines.append(f"ETA còn {_fmt_dur(remain / mean_min * 60)} cho {remain:,} hồ sơ")
    else:
        lines.append("Throughput: (chờ tick sau để đo)")

    # Lô đang chạy
    act = await _active_batches()
    if act and not args.batch:
        lines.append("─" * 60)
        lines.append("Lô đang chạy (processing · queued · tên):")
        for proc, q, name in act:
            lines.append(f"  {proc:>5} · {q:>10,} · {name}")

    # Timing từng chặng (nếu bật trace)
    if args.timings:
        lines.append("─" * 60)
        tm = await _timings(args.batch)
        if tm["n"] == 0:
            lines.append("Timing: chưa có doc nào có `timings`.")
            lines.append("  → chạy worker với AIHUB_TRACE_TIMINGS=true rồi đợi vài hồ sơ xong.")
        else:
            lines.append(f"Timing/chặng (giây · sample {tm['n']} doc · p50 / p90):")
            # render/detect/extract là 3 chặng CON của pipeline — thụt vào cho rõ.
            sub = {"render", "detect", "extract"}
            for k in ("download", "pipeline", "render", "detect", "extract", "cuts"):
                v = tm["buckets"][k]
                if v:
                    label = ("  ↳ " + k) if k in sub else k
                    lines.append(f"  {label:<12} {_pct(v,50):>7.2f} / {_pct(v,90):>7.2f}"
                                 f"   (mẫu {len(v)})")
            if tm["pages"]:
                lines.append(f"  trang/hồ sơ p50={_pct(tm['pages'],50):.0f}"
                             f" p90={_pct(tm['pages'],90):.0f}")
            lines.append("  → download cao = nghẽn MinIO; render cao = CPU; detect/extract cao = VLM;"
                         " cuts cao = ghi S3 đích.")

    # Lỗi
    if counts["error"]:
        ek = await _err_kinds(args.batch)
        if ek:
            lines.append("─" * 60)
            lines.append("Lỗi theo error_kind (sample):")
            for v, k in ek[:6]:
                lines.append(f"  {v:>5}  {k}")

    print(CLEAR + "\n".join(lines), flush=True)
    return {"t": now, "done_total": done_total,
            "t0": prev["t0"] if prev else now,
            "done_total0": prev["done_total0"] if prev else done_total}


async def run(args) -> None:
    prev = None
    if args.once:
        await render(args, None)
        return
    try:
        while True:
            prev = await render(args, prev)
            await asyncio.sleep(args.interval)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nDừng watch.", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--batch", default=None, help="chỉ theo dõi 1 batch_id")
    ap.add_argument("--timings", action="store_true", help="+ phân bố thời gian từng chặng")
    ap.add_argument("--once", action="store_true", help="in 1 lần rồi thoát")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
