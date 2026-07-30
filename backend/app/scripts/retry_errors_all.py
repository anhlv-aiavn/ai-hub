"""Đưa lại vào hàng chờ các hồ sơ `status="error"` — MỌI LÔ, tùy chọn lọc theo
KHOẢNG THỜI GIAN LỖI (`finished_at`). Dùng khi một sự cố hạ tầng (vd VLM/litellm
khởi động lại) làm hỏng hàng loạt trong một quãng thời gian.

Làm đúng như route `/v1/gcn/retry-errors`: đổi `error → queued`, xoá `error`/
`error_kind`, và bump `batch.counts` TỪNG LÔ (mỗi bump khớp `modified_count` của
lô đó, không thể một `update_many` toàn cục vì counter theo lô). Worker tự claim
doc `queued` chạy lại toàn bộ pipeline.

Doc `status="dead"` (poison) KHÔNG nằm trong phạm vi — cần soi thủ công.

Chạy TRONG CONTAINER (máy serve):
    # 1) xem quy mô, KHÔNG đổi gì:
    docker compose exec api python -m app.scripts.retry_errors_all --dry-run
    # 2) chỉ lỗi tạm thời (transient — lỗi mạng/VLM tự khỏi khi chạy lại):
    docker compose exec api python -m app.scripts.retry_errors_all --error-kind transient --dry-run
    # 3) chỉ lỗi trong khoảng giờ xảy ra sự cố (UTC, ISO 8601):
    docker compose exec api python -m app.scripts.retry_errors_all \
        --since 2026-07-30T02:00:00 --until 2026-07-30T04:00:00 --dry-run
    # 4) chạy thật (bỏ --dry-run):
    docker compose exec api python -m app.scripts.retry_errors_all --error-kind transient
"""

import argparse
import asyncio
from datetime import datetime, timezone

from app.batch_counters import bump
from app.db import batches, gcns


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    # Không có tzinfo ⇒ coi là UTC (finished_at ghi bằng UTC, xem run_job).
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def run(error_kind, since, until, dry_run) -> None:
    base: dict = {"status": "error"}
    if error_kind:
        base["error_kind"] = error_kind
    time_flt: dict = {}
    if since:
        time_flt["$gte"] = since
    if until:
        time_flt["$lte"] = until
    if time_flt:
        base["finished_at"] = time_flt

    print(f"  bộ lọc: {base}", flush=True)
    tong = await gcns().count_documents(base)
    print(f"  {tong:,} hồ sơ status=error khớp bộ lọc\n", flush=True)
    if not tong:
        return

    batch_ids = [b for b in await gcns().distinct("batch_id", base) if b]
    print(f"  nằm ở {len(batch_ids)} lô\n", flush=True)

    if dry_run:
        print("  DRY-RUN — không đổi gì. Bỏ --dry-run để chạy thật.", flush=True)
        return

    total = 0
    for bid in batch_ids:
        res = await gcns().update_many(
            {**base, "batch_id": bid},
            {"$set": {"status": "queued", "error": None, "error_kind": None}})
        if res.modified_count:
            await bump(batches(), bid, error=-res.modified_count, queued=res.modified_count)
            total += res.modified_count
            print(f"    {bid}: +{res.modified_count} → queued", flush=True)
    print(f"\n  XONG — đã đưa lại {total:,} hồ sơ vào hàng chờ.", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--error-kind", default=None,
                    help='lọc theo error_kind (vd "transient", "permanent"); bỏ = mọi loại')
    ap.add_argument("--since", default=None, help="finished_at ≥ (ISO 8601, UTC)")
    ap.add_argument("--until", default=None, help="finished_at ≤ (ISO 8601, UTC)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.error_kind, _parse_dt(args.since), _parse_dt(args.until), args.dry_run))


if __name__ == "__main__":
    main()
