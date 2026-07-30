"""Tính LẠI `batch.counts` từ status THẬT của gcn — sửa drift (vd counter
`processing` âm do 'giải phóng job kẹt' giật nhầm doc đang chạy khi ngưỡng
min_stale_seconds < thời gian xử lý thật).

An toàn: chỉ GHI ĐÈ `batch.counts` bằng số đếm thật (aggregate group by status).
Không đụng gcn. Nên chạy lúc THẤP TẢI (aggregate quét theo batch — với lô triệu
doc là một lần quét đắt, nhưng dùng index {batch_id} hoặc {status,batch_id}).

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.reconcile_counts --dry-run
    docker compose exec api python -m app.scripts.reconcile_counts             # mọi lô
    docker compose exec api python -m app.scripts.reconcile_counts --batch <id>
"""

import argparse
import asyncio

from app.batch_counters import zero_counts
from app.db import batches, gcns


async def _true_counts(batch_id: str) -> dict:
    counts = zero_counts()
    cur = gcns().aggregate([
        {"$match": {"batch_id": batch_id}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ])
    async for r in cur:
        s = r.get("_id")
        if s in counts:
            counts[s] = r["n"]
    return counts


async def run(batch_id: str | None, dry_run: bool) -> None:
    if batch_id:
        ids = [batch_id]
    else:
        ids = [b["_id"] async for b in batches().find({}, {"_id": 1})]
    print(f"  {len(ids)} lô cần reconcile\n", flush=True)

    fixed = 0
    for bid in ids:
        b = await batches().find_one({"_id": bid}, {"counts": 1})
        old = (b or {}).get("counts") or {}
        new = await _true_counts(bid)
        if old != new:
            diff = {k: new[k] - int(old.get(k) or 0) for k in new if new[k] != int(old.get(k) or 0)}
            print(f"  {bid}: {diff}", flush=True)
            if not dry_run:
                await batches().update_one({"_id": bid}, {"$set": {"counts": new}})
            fixed += 1
    verb = "sẽ sửa" if dry_run else "đã sửa"
    print(f"\n  {verb} {fixed}/{len(ids)} lô lệch counter."
          + ("  (DRY-RUN — chưa ghi)" if dry_run else ""), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.batch, args.dry_run))


if __name__ == "__main__":
    main()
