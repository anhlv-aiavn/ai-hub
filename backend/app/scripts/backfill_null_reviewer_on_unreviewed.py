"""Backfill: null hóa review.reviewer/review.reviewed_at cho hồ sơ đang "Chưa
kiểm" (review.status == "unreviewed") nhưng còn sót giá trị reviewer/reviewed_at
cũ từ TRƯỚC khi put_review được sửa để tự null hóa 2 field này khi hủy duyệt
(xem app/routes/gcn.py::put_review).

Không chạy backfill này, các hồ sơ đã hủy duyệt TRƯỚC thời điểm vá vẫn còn
reviewer cũ → lọc theo tài khoản (GET /v1/gcn?reviewer=...) và thống kê
by_reviewer (GET /v1/gcn/stats) tiếp tục hiện nhầm cho tới khi hồ sơ đó bị động
vào lại (chỉ tự sửa khi có thao tác put_review mới trên đúng hồ sơ đó).

Chạy trong container API:
    docker compose exec api python -m app.scripts.backfill_null_reviewer_on_unreviewed --dry-run
    docker compose exec api python -m app.scripts.backfill_null_reviewer_on_unreviewed
"""

import argparse
import asyncio

from app import config
from app.db import gcns


async def _run(dry_run: bool) -> None:
    flt = {"review.status": "unreviewed", "review.reviewer": {"$ne": None}}
    n = await gcns().count_documents(flt)
    print(f"Tìm thấy {n} hồ sơ 'Chưa kiểm' còn sót review.reviewer/reviewed_at cũ.")
    if n == 0:
        print("Không có gì để backfill.")
        return
    if dry_run:
        print("(--dry-run: chưa ghi gì vào DB)")
        return
    res = await gcns().update_many(flt, {"$set": {"review.reviewer": None, "review.reviewed_at": None}})
    print(f"Xong. Đã null hóa reviewer/reviewed_at cho {res.modified_count} hồ sơ.")


async def main() -> None:
    p = argparse.ArgumentParser(
        description="Backfill null hóa review.reviewer/reviewed_at cho hồ sơ đã hủy duyệt (về unreviewed).")
    p.add_argument("--dry-run", action="store_true", help="Chỉ đếm, không ghi DB.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    await _run(args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
