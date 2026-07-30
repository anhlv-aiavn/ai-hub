"""Chuyển các hồ sơ `status="error"` do FILE KHÔNG TỒN TẠI (NoSuchKey) sang
`status="no_file"` (OPS-1) — để chúng RỜI ô "Lỗi" và KHÔNG bị nút retry quét
(chạy lại vẫn NoSuchKey, vô ích). Từ nay run_job tự phân loại; script này dọn
DỮ LIỆU CŨ đã lỡ nằm ở "error".

Nhận diện: `error` chứa "NoSuchKey" HOẶC `error` khớp mẫu "Không tìm thấy file"/
"Không đọc được file (NoSuchKey". Chỉ đụng status="error" (không đụng no_gcn/skip).
Update TỪNG LÔ để bump(batch.counts) khớp modified_count từng lô.

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.reclass_no_file --dry-run
    docker compose exec api python -m app.scripts.reclass_no_file
"""

import argparse
import asyncio

from app.batch_counters import bump
from app.db import batches, gcns

# NoSuchKey xuất hiện trong message dù bản cũ (SourceObjectUnavailable) hay mới
# (SourceObjectMissing) — bắt theo chuỗi để phủ cả dữ liệu tạo trước khi tách lớp.
_FLT = {"status": "error", "error": {"$regex": "NoSuchKey"}}


async def run(dry_run: bool) -> None:
    tong = await gcns().count_documents(_FLT)
    print(f"  {tong:,} hồ sơ status=error do NoSuchKey\n", flush=True)
    if not tong:
        return
    ids = [b for b in await gcns().distinct("batch_id", _FLT) if b]
    print(f"  nằm ở {len(ids)} lô\n", flush=True)
    if dry_run:
        print("  DRY-RUN — không đổi gì. Bỏ --dry-run để chạy thật.", flush=True)
        return
    total = 0
    for bid in ids:
        res = await gcns().update_many(
            {**_FLT, "batch_id": bid},
            {"$set": {"status": "no_file", "error_kind": "missing_source"}})
        if res.modified_count:
            await bump(batches(), bid, error=-res.modified_count, no_file=res.modified_count)
            total += res.modified_count
            print(f"    {bid}: +{res.modified_count} → no_file", flush=True)
    print(f"\n  XONG — chuyển {total:,} hồ sơ sang 'Không có tệp'.", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
