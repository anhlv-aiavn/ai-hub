"""Tính lại dup_suspect/dup_candidates cho TOÀN BỘ hồ sơ status=done theo cơ chế
đối xứng mới (xem app.worker.run_job._refresh_dup_group) — sửa ngay dữ liệu cũ
bị lệch do cơ chế trước đây (chiều tiến cap 5 kết quả, chiều ngược $addToSet
cộng dồn không cap → 2 hồ sơ thực sự trùng nhau có thể ra số lượng khác nhau
tùy thời điểm/thứ tự xử lý).

Chạy trong container API:
    docker compose exec api python -m app.scripts.backfill_dup_groups
    docker compose exec api python -m app.scripts.backfill_dup_groups --dry-run
"""

import argparse
import asyncio
from collections import defaultdict

from app import config
from app.db import gcns


async def _run(dry_run: bool) -> None:
    docs = await gcns().find(
        {"status": "done"},
        {"_id": 1, "extracted_so_phat_hanhs": 1, "dup_suspect": 1, "dup_candidates": 1},
    ).to_list(length=None)
    print(f"Đang xét {len(docs)} hồ sơ status=done...")

    # Chỉ mục Số phát hành → tập gcn_id sở hữu nó, dựng 1 lần trong bộ nhớ để
    # tính giao nhau cho MỌI hồ sơ mà khỏi cần query riêng từng cái (nhanh hơn
    # nhiều so với gọi lại _refresh_dup_group tuần tự cho từng doc). Cùng đúng
    # 1 định nghĩa quan hệ: "chung ít nhất 1 Số phát hành" — tự đối xứng.
    sph_index: dict[str, set[str]] = defaultdict(set)
    for d in docs:
        for sph in d.get("extracted_so_phat_hanhs") or []:
            sph_index[sph].add(d["_id"])

    changed = 0
    for d in docs:
        gcn_id = d["_id"]
        others: set[str] = set()
        for sph in d.get("extracted_so_phat_hanhs") or []:
            others |= sph_index[sph]
        others.discard(gcn_id)
        new_candidates = sorted(others)
        new_suspect = bool(new_candidates)

        old_candidates = sorted(d.get("dup_candidates") or [])
        old_suspect = bool(d.get("dup_suspect"))
        if new_candidates == old_candidates and new_suspect == old_suspect:
            continue

        changed += 1
        print(f"  {gcn_id}: {len(old_candidates)} -> {len(new_candidates)} hồ sơ nghi trùng")
        if not dry_run:
            await gcns().update_one(
                {"_id": gcn_id},
                {"$set": {"dup_suspect": new_suspect, "dup_candidates": new_candidates}},
            )

    suffix = " (--dry-run: chưa ghi gì vào DB)" if dry_run else ""
    print(f"Xong. {changed}/{len(docs)} hồ sơ được cập nhật lại.{suffix}")


async def main() -> None:
    p = argparse.ArgumentParser(
        description="Backfill dup_suspect/dup_candidates đối xứng cho dữ liệu cũ.")
    p.add_argument("--dry-run", action="store_true", help="Chỉ in thay đổi, không ghi DB.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    await _run(args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
