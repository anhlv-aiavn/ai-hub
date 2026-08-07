"""Soi LỖI GẦN ĐÂY: gom theo nội dung, kèm mẫu mới nhất. CHỈ ĐỌC.

`watch.py` chỉ đếm theo `error_kind` (transient/permanent/no_file) — không đủ để
biết đang hỏng vì cái gì. Script này gom theo NỘI DUNG lỗi và in mẫu mới nhất,
đủ để phân biệt "timeout VLM" với "endpoint chết" hay "PDF hỏng".

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.soi_loi                # 24h gần nhất
    docker compose exec api python -m app.scripts.soi_loi --gio 2        # 2h gần nhất
    docker compose exec api python -m app.scripts.soi_loi --gio 0        # KHÔNG lọc thời gian
    docker compose exec api python -m app.scripts.soi_loi --mau 20       # in 20 mẫu mỗi nhóm
    docker compose exec api python -m app.scripts.soi_loi --dead         # xem cả doc "dead"
"""

import argparse
import asyncio
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from app import config
from app.db import gcns

# Gộp các lỗi chỉ khác nhau vài con số về một nhóm (đường dẫn, id, số giây...)
_SO_RE = re.compile(r"\d+")


def _nhom(msg: str) -> str:
    return _SO_RE.sub("#", (msg or "(rỗng)").strip())[:120]


async def run(gio: float, batch_id: str | None, mau: int, ke_ca_dead: bool) -> None:
    trang_thai = ["error", "dead"] if ke_ca_dead else ["error"]
    q: dict = {"status": {"$in": trang_thai}}
    if batch_id:
        q["batch_id"] = batch_id
    if gio > 0:
        q["finished_at"] = {"$gte": datetime.now(timezone.utc) - timedelta(hours=gio)}

    tong = await gcns().count_documents(q)
    pham_vi = f"{gio:g}h gần nhất" if gio > 0 else "MỌI thời điểm"
    print(f"  {tong:,} hồ sơ {'/'.join(trang_thai)} · {pham_vi}"
          + (f" · lô {batch_id}" if batch_id else "") + "\n", flush=True)
    if not tong:
        return

    dem_nhom: Counter = Counter()
    dem_kind: Counter = Counter()
    vi_du: dict[str, list] = {}
    cur = gcns().find(q, {"error": 1, "error_kind": 1, "filename": 1, "status": 1,
                          "finished_at": 1, "attempts": 1, "page_count": 1,
                          "timings": 1}).sort("finished_at", -1).limit(5000)
    async for d in cur:
        k = _nhom(d.get("error") or "")
        dem_nhom[k] += 1
        dem_kind[d.get("error_kind") or "(none)"] += 1
        vi_du.setdefault(k, []).append(d)

    print("  THEO error_kind:")
    for k, v in dem_kind.most_common():
        print(f"    {v:>6,}  {k}")

    print("\n  THEO NỘI DUNG (mẫu tối đa 5.000 doc mới nhất):")
    for k, v in dem_nhom.most_common(10):
        print("─" * 78)
        print(f"  {v:>6,}  {k}")
        for d in vi_du[k][:mau]:
            t = d.get("finished_at")
            tm = t.strftime("%d/%m %H:%M") if isinstance(t, datetime) else "?"
            tim = d.get("timings") or {}
            chi_tiet = " ".join(f"{c}={tim[c]}s" for c in ("download", "render", "detect", "extract")
                                if c in tim)
            print(f"      {tm}  {str(d.get('filename'))[:34]:<36} "
                  f"trang={d.get('page_count') or '?':<4} lần={d.get('attempts') or 0}"
                  + (f"  [{chi_tiet}]" if chi_tiet else ""))

    print("\n  Đưa lại vào hàng chờ: app.scripts.retry_errors_all --error-kind transient --dry-run")


def main() -> None:
    p = argparse.ArgumentParser(description="Soi lỗi gần đây, gom theo nội dung (chỉ đọc).")
    p.add_argument("--gio", type=float, default=24, help="Cửa sổ giờ theo finished_at (0 = mọi lúc).")
    p.add_argument("--batch", default=None, help="Giới hạn 1 lô.")
    p.add_argument("--mau", type=int, default=5, help="Số hồ sơ mẫu in cho mỗi nhóm lỗi.")
    p.add_argument("--dead", action="store_true", help="Xem cả doc status=dead (poison).")
    args = p.parse_args()
    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    asyncio.run(run(args.gio, args.batch, args.mau, args.dead))


if __name__ == "__main__":
    main()
