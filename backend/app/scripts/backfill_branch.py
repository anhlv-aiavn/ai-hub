"""Gán chi nhánh cho dữ liệu cũ (trước khi có tính năng chi nhánh).

- Mặc định: lan `batch.branch` xuống các GCN cùng batch còn thiếu branch.
- Tùy chọn: gán cứng 1 chi nhánh cho 1 lô (lô cũ chưa có branch).

Chạy trong container API:
    docker compose exec api python -m app.scripts.backfill_branch
    docker compose exec api python -m app.scripts.backfill_branch --batch <batch_id> --set "Chi nhánh Hà Đông"
"""

import argparse
import asyncio

from app import config
from app.branches import is_valid_branch
from app.db import batches, gcns


async def _propagate() -> None:
    """batch.branch → gcn.branch (cho gcn thiếu branch)."""
    n_batches = 0
    n_gcn = 0
    cursor = batches().find({"branch": {"$ne": None}}, {"_id": 1, "branch": 1})
    async for b in cursor:
        res = await gcns().update_many(
            {"batch_id": b["_id"], "$or": [{"branch": None}, {"branch": {"$exists": False}}]},
            {"$set": {"branch": b["branch"]}},
        )
        if res.modified_count:
            n_batches += 1
            n_gcn += res.modified_count
    print(f"[propagate] cập nhật {n_gcn} GCN từ {n_batches} lô.")


async def _assign(batch_id: str, branch: str) -> None:
    if not await is_valid_branch(branch):
        raise SystemExit(f"Chi nhánh không hợp lệ: {branch!r}")
    b = await batches().find_one({"_id": batch_id}, {"_id": 1})
    if not b:
        raise SystemExit(f"Không tìm thấy lô {batch_id}")
    await batches().update_one({"_id": batch_id}, {"$set": {"branch": branch}})
    res = await gcns().update_many({"batch_id": batch_id}, {"$set": {"branch": branch}})
    print(f"[assign] lô {batch_id} → {branch!r} ({res.modified_count} GCN).")


async def main() -> None:
    p = argparse.ArgumentParser(description="Backfill chi nhánh cho dữ liệu cũ.")
    p.add_argument("--batch", help="Gán cứng chi nhánh cho 1 lô (kèm --set).")
    p.add_argument("--set", dest="branch", help="Tên chi nhánh để gán cho --batch.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    if args.batch:
        if not args.branch:
            raise SystemExit("--batch cần kèm --set <chi nhánh>")
        await _assign(args.batch, args.branch)
    await _propagate()


if __name__ == "__main__":
    asyncio.run(main())
