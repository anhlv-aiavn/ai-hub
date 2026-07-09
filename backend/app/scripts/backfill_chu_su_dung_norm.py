"""Backfill `gcn_rows[].chu_su_dung_norm` (bản Chủ sử dụng bỏ dấu + chữ thường)
cho dữ liệu cũ, ghi trước khi có tìm kiếm không phân biệt dấu (xem
app/summary.py::_entry_summary + app/routes/gcn.py, search theo `q`).

Chỉ xử lý các doc có gcn_rows chứa chu_su_dung nhưng còn thiếu/lệch
chu_su_dung_norm — ghi lại nguyên mảng gcn_rows (Mongo không có cách $set
field lệch nhau theo từng phần tử mảng bằng 1 update duy nhất).

Chạy trong container API:
    docker compose exec api python -m app.scripts.backfill_chu_su_dung_norm
    docker compose exec api python -m app.scripts.backfill_chu_su_dung_norm --dry-run
"""

import argparse
import asyncio

from app import config
from app.db import gcns
from app.vn_text import strip_diacritics


async def _run(dry_run: bool) -> None:
    docs = await gcns().find(
        {"gcn_rows.chu_su_dung": {"$exists": True}},
        {"_id": 1, "gcn_rows": 1},
    ).to_list(length=None)
    print(f"Đang xét {len(docs)} hồ sơ có gcn_rows...")

    changed = 0
    for d in docs:
        rows = d.get("gcn_rows") or []
        new_rows = []
        row_changed = False
        for r in rows:
            if not isinstance(r, dict):
                new_rows.append(r)
                continue
            chu = r.get("chu_su_dung") or []
            new_norm = [strip_diacritics(c) for c in chu]
            if r.get("chu_su_dung_norm") != new_norm:
                row_changed = True
                r = {**r, "chu_su_dung_norm": new_norm}
            new_rows.append(r)

        if not row_changed:
            continue
        changed += 1
        print(f"  {d['_id']}: cập nhật chu_su_dung_norm cho {len(new_rows)} dòng")
        if not dry_run:
            await gcns().update_one({"_id": d["_id"]}, {"$set": {"gcn_rows": new_rows}})

    suffix = " (--dry-run: chưa ghi gì vào DB)" if dry_run else ""
    print(f"Xong. {changed}/{len(docs)} hồ sơ được cập nhật.{suffix}")


async def main() -> None:
    p = argparse.ArgumentParser(
        description="Backfill gcn_rows[].chu_su_dung_norm cho dữ liệu cũ.")
    p.add_argument("--dry-run", action="store_true", help="Chỉ in thay đổi, không ghi DB.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    await _run(args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
