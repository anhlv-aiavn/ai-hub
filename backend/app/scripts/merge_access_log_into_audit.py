"""Gộp collection `access_log` (cũ) vào `audit_log` — chạy 1 LẦN khi deploy bản
gộp audit (PLAN_UI_AUDIT_S3_FIXES.md §2a). Map action cũ → action mới:
"view"→"gcn.view", "download"→"gcn.download", "export"→"export.create".

Sau khi migrate xong (verify tổng số dòng khớp), có thể `drop()` collection
`access_log` — script này KHÔNG tự drop, để admin tự xác nhận trước.

Chạy trong container API:
    docker compose exec api python -m app.scripts.merge_access_log_into_audit
    docker compose exec api python -m app.scripts.merge_access_log_into_audit --drop  # xóa luôn access_log sau khi gộp
"""

import argparse
import asyncio

from app import config
from app.db import audit_log, get_db

_ACTION_MAP = {"view": "gcn.view", "download": "gcn.download", "export": "export.create"}


def access_log_coll():
    return get_db()[config.COLL_ACCESS_LOG]


async def _migrate() -> int:
    src = access_log_coll()
    n = 0
    async for r in src.find({}):
        action = _ACTION_MAP.get(r.get("action"), r.get("action"))
        target = r.get("gcn_id") or (r.get("detail") or {}).get("job_id")
        await audit_log().insert_one({
            "at": r.get("at"), "actor": r.get("actor"), "action": action,
            "target": target, "detail": r.get("detail") or {},
        })
        n += 1
    return n


async def main() -> None:
    p = argparse.ArgumentParser(description="Gộp access_log vào audit_log.")
    p.add_argument("--drop", action="store_true", help="Xóa collection access_log sau khi gộp xong.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    n_before = await access_log_coll().count_documents({})
    n = await _migrate()
    print(f"[merge] đã chép {n}/{n_before} bản ghi access_log → audit_log.")

    if args.drop:
        if n != n_before:
            raise SystemExit("Số dòng chép không khớp — KHÔNG drop, kiểm tra lại trước khi chạy lại với --drop.")
        await access_log_coll().drop()
        print("[merge] đã drop collection access_log.")


if __name__ == "__main__":
    asyncio.run(main())
