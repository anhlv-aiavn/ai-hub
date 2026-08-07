"""Trả các hồ sơ đang kẹt `processing` về `queued` — dùng sau khi GIẾT worker.

Vì sao cần: worker bị kill (rebuild, OOM, Ctrl-C) để lại doc ở "processing" mà
không còn ai chạy. Worker mới CHỈ tự đòi lại sau WORKER_PROC_TTL (mặc định 30
phút), và mỗi lần claim lại `$inc attempts`; quá WORKER_MAX_ATTEMPTS (3) thì doc
bị đánh dấu "dead" (poison) dù nó chẳng độc — chỉ là bị giết oan mấy lần.

Script này trả về hàng chờ NGAY và (mặc định) xoá `attempts` để doc không tiến
gần mốc dead vì lỗi của mình.

CHẠY KHI WORKER ĐÃ DỪNG. Chạy lúc worker còn sống sẽ giật doc khỏi tay nó:
    docker compose stop worker
    docker compose exec api python -m app.scripts.reset_processing --dry-run
    docker compose exec api python -m app.scripts.reset_processing
    docker compose up -d worker

Chỉ đụng doc "cũ" (mặc định started_at quá 60s) để không cướp việc đang chạy
thật; --tat-ca bỏ điều kiện đó.
"""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone

from app import config
from app.batch_counters import bump
from app.db import batches, gcns


async def run(qua_giay: float, dry_run: bool, giu_attempts: bool) -> None:
    q: dict = {"status": "processing"}
    if qua_giay > 0:
        q["started_at"] = {"$lt": datetime.now(timezone.utc) - timedelta(seconds=qua_giay)}

    tong = await gcns().count_documents(q)
    print(f"  {tong:,} hồ sơ đang 'processing'"
          + (f" (bắt đầu quá {qua_giay:g}s trước)" if qua_giay > 0 else "") + "\n", flush=True)
    if not tong:
        return

    theo_lo: Counter = Counter()
    async for d in gcns().find(q, {"batch_id": 1}):
        theo_lo[d.get("batch_id")] += 1
    print(f"  nằm ở {len(theo_lo)} lô", flush=True)

    if dry_run:
        print("\n  DRY-RUN — chưa đổi gì. Bỏ --dry-run để chạy thật.")
        return

    dat = {"status": "queued", "started_at": None}
    unset = {} if giu_attempts else {"attempts": ""}
    tong_ghi = 0
    for bid, n in theo_lo.items():
        flt = {**q, "batch_id": bid}
        res = await gcns().update_many(
            flt, {"$set": dat, **({"$unset": unset} if unset else {})})
        if res.modified_count:
            # Counter lô: doc rời "processing" về "queued" — không bump thì thanh
            # tiến độ trên UI sai vĩnh viễn.
            await bump(batches(), bid, processing=-res.modified_count,
                       queued=res.modified_count)
            tong_ghi += res.modified_count
    print(f"\n  XONG — {tong_ghi:,} hồ sơ về hàng chờ"
          + ("" if giu_attempts else " (đã xoá attempts)"))
    print("  Bật lại worker: docker compose up -d worker")


def main() -> None:
    p = argparse.ArgumentParser(description="Trả doc kẹt 'processing' về 'queued' (chạy khi worker đã dừng).")
    p.add_argument("--qua-giay", type=float, default=60,
                   help="Chỉ đụng doc bắt đầu quá N giây trước (mặc định 60; 0 = mọi doc).")
    p.add_argument("--tat-ca", action="store_true", help="Bỏ điều kiện thời gian (= --qua-giay 0).")
    p.add_argument("--giu-attempts", action="store_true",
                   help="Giữ nguyên attempts (mặc định xoá — doc bị giết oan không nên tiến tới 'dead').")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    asyncio.run(run(0 if args.tat_ca else args.qua_giay, args.dry_run, args.giu_attempts))


if __name__ == "__main__":
    main()
