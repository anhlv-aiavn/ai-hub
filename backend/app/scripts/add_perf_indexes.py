"""Tạo compound index cho hot path claim/fairness (PERF-7 — xem
docs/features_issues.md#perf-index). CHẠY TAY lúc THẤP TẢI: build index trên
collection triệu+ doc tốn I/O, KHÔNG nhét vào ensure_indexes (startup) để tránh
spike mỗi lần API khởi động lại trên production đang chạy.

Index thêm:
  - {status:1, batch_id:1}  → phủ distinct(batch_id,{status:queued}) (fairness)
                              + nhánh claim queued có batch_id.
  - {status:1, started_at:1} → nhánh reclaim processing-treo + _sweep_dead.

An toàn chạy lại: createIndex idempotent (index đã có → bỏ qua). Mongo 4.2+ build
theo cơ chế tối ưu, không giữ khóa ghi độc quyền suốt quá trình.

Chạy TRONG CONTAINER (chọn lúc ít hồ sơ đang xử lý):
    docker compose exec api python -m app.scripts.add_perf_indexes
"""

import asyncio
import time

from app.db import gcns


async def run() -> None:
    specs = [
        ([("status", 1), ("batch_id", 1)], "status_batch_id"),
        ([("status", 1), ("started_at", 1)], "status_started_at"),
    ]
    for keys, name in specs:
        print(f"  tạo index {name} {keys} …", flush=True)
        t0 = time.monotonic()
        await gcns().create_index(keys, name=name, background=True)
        print(f"    xong sau {time.monotonic() - t0:.1f}s", flush=True)
    print("\n  Đã tạo xong index PERF-7.", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
