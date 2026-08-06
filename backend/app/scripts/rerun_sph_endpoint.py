"""Chạy LẠI trích xuất cho nhóm SPH thiếu tiền tố chữ — TIẾN TRÌNH RIÊNG, ghim
vào MỘT máy vLLM (vd máy phụ .10:30001), KHÔNG đi qua hàng đợi chung.

Vì sao không dùng requeue_sph_thieu_chu ở đây: requeue đẩy doc về `status=queued`
⇒ worker chính (đang ghim .9:30000) sẽ giành mất. Script này đánh dấu bằng field
RIÊNG `sph_rerun` và tự claim `done → processing`, nên worker chính không thấy
doc nào ở "queued" để nhặt — hai luồng chạy song song trên hai GPU khác nhau.

Ghim endpoint bằng ENV lúc khởi động tiến trình (vlm_client đọc env khi import),
KHÔNG sửa .env của compose:

    # 1) đánh dấu phạm vi (chỉ ghi cờ, KHÔNG đổi status, KHÔNG gọi GPU):
    docker compose exec api python -m app.scripts.rerun_sph_endpoint --mark --dry-run
    docker compose exec api python -m app.scripts.rerun_sph_endpoint --mark

    # 2) chạy ngầm trong screen, ghim máy phụ:
    screen -S sph
    docker compose exec \
        -e VLLM_ENDPOINTS=http://192.168.120.10:30001/v1 \
        -e MAX_VLM_CONCURRENT=8 \
        api python -m app.scripts.rerun_sph_endpoint 2>&1 | tee -a /tmp/sph_rerun.log
    # Ctrl-A D để rời screen · screen -r sph để quay lại

    # 3) theo dõi / dừng:
    docker compose exec api python -m app.scripts.rerun_sph_endpoint --trang-thai
    # Ctrl-C: dừng êm, doc đang chạy dở sẽ thành "running" → chạy lại với --reset-running

Nồi cơm chung với worker chính: `process_doc` (cùng một nơi ghi extractions) và
`bump` counter của lô ⇒ thanh tiến độ UI vẫn đúng.

MỘT RỦI RO CẦN BIẾT: worker chính có nhánh reclaim "processing treo > 30 phút"
(WORKER_PROC_TTL) và _sweep_dead. Doc của script này nằm ở "processing" thật, nên
nếu 1 hồ sơ chạy quá 30 phút thì worker chính có thể giành lại — hiếm (mỗi hồ sơ
thường 1-3 phút), và cùng lắm là nó chạy lại trên máy .9, không hỏng dữ liệu.
"""

import argparse
import asyncio
import os
import signal
import time
from collections import Counter
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app import config
from app.batch_counters import bump
from app.scripts.requeue_sph_thieu_chu import (
    _MONGO_RE,
    _da_hau_kiem,
    _sph_list,
    _thieu_chu,
    SPH_PATH,
)

_stop = False


def _xin_dung(*_a) -> None:
    global _stop
    if _stop:
        raise KeyboardInterrupt
    _stop = True
    print("\n  … nhận tín hiệu dừng, đợi các hồ sơ đang chạy xong (Ctrl-C lần nữa để cắt ngay)",
          flush=True)


# ── Pha 1: đánh dấu phạm vi ──────────────────────────────────────────────────

async def mark(mongo, limit, batch_id, dry_run, ke_ca_rong, ke_ca_da_hau_kiem) -> None:
    gcns = mongo.db[config.COLL_GCN]
    q: dict = {"status": "done", "sph_rerun": {"$exists": False}}
    if batch_id:
        q["batch_id"] = batch_id
    if ke_ca_rong:
        q["$or"] = [{SPH_PATH: {"$regex": _MONGO_RE}},
                    {SPH_PATH: {"$in": [None, ""]}},
                    {SPH_PATH: {"$exists": False}}]
    else:
        q[SPH_PATH] = {"$regex": _MONGO_RE}

    print("  đang quét kho…", flush=True)
    cur = gcns.find(q, {"batch_id": 1, "review": 1, "filename": 1,
                        "extractions.result.Đăng ký.Giấy chứng nhận.Số phát hành": 1})
    if limit:
        cur = cur.limit(limit)

    st, mau, ids = Counter(), [], []
    async for doc in cur:
        st["xet"] += 1
        if not _thieu_chu(doc.get("extractions"), ke_ca_rong):
            st["khong_khop"] += 1
            continue
        if _da_hau_kiem(doc) and not ke_ca_da_hau_kiem:
            st["da_hau_kiem"] += 1
            continue
        st["chon"] += 1
        if len(mau) < 10:
            mau.append(f"{str(doc.get('filename'))[:34]:<36} {_sph_list(doc.get('extractions'))}")
        ids.append(doc["_id"])
        if len(ids) >= 1000 and not dry_run:
            await gcns.update_many({"_id": {"$in": ids}}, {"$set": {"sph_rerun": "pending"}})
            ids.clear()
    if ids and not dry_run:
        await gcns.update_many({"_id": {"$in": ids}}, {"$set": {"sph_rerun": "pending"}})

    print("=" * 70)
    print(f" XÉT {st['xet']:,}  ·  ĐÁNH DẤU {st['chon']:,}" + ("   (DRY-RUN)" if dry_run else ""))
    print("=" * 70)
    print(f"  bỏ qua vì đã hậu kiểm : {st['da_hau_kiem']:,}")
    print(f"  bỏ qua vì soi lại không khớp : {st['khong_khop']:,}")
    for m in mau:
        print(f"    {m}")
    if dry_run:
        print("\n  DRY-RUN: chưa ghi cờ nào. Bỏ --dry-run để đánh dấu thật.")


# ── Pha 2: chạy — claim done→processing, gọi process_doc ─────────────────────

async def _claim(mongo):
    """Claim 1 doc đã đánh dấu. done → processing + bump counter lô cho khớp
    (process_doc cuối hàm bump processing=-1, <status>=+1)."""
    now = datetime.now(timezone.utc)
    doc = await mongo.db[config.COLL_GCN].find_one_and_update(
        {"sph_rerun": "pending", "status": "done"},
        {"$set": {"status": "processing", "started_at": now, "sph_rerun": "running"},
         "$inc": {"attempts": 1}},
        return_document=ReturnDocument.BEFORE,
    )
    if not doc:
        return None
    await bump(mongo.db[config.COLL_BATCH], doc.get("batch_id"), done=-1, processing=1)
    doc["status"] = "processing"
    return doc


async def _chay_mot(mongo, doc, process_doc, st: Counter) -> None:
    truoc = _sph_list(doc.get("extractions"))
    try:
        status = await process_doc(mongo, doc)
    except Exception as e:  # noqa: BLE001
        st["loi"] += 1
        print(f"    LỖI {doc.get('filename')}: {e}", flush=True)
        await mongo.db[config.COLL_GCN].update_one(
            {"_id": doc["_id"]}, {"$set": {"sph_rerun": "error"}})
        return
    st[status] += 1
    moi_doc = await mongo.db[config.COLL_GCN].find_one(
        {"_id": doc["_id"]},
        {"extractions.result.Đăng ký.Giấy chứng nhận.Số phát hành": 1})
    sau = _sph_list((moi_doc or {}).get("extractions"))
    da_sua = sau != truoc
    st["sua_duoc" if da_sua else "y_nguyen"] += 1
    await mongo.db[config.COLL_GCN].update_one(
        {"_id": doc["_id"]},
        {"$set": {"sph_rerun": "done", "sph_rerun_at": datetime.now(timezone.utc)}})
    if da_sua:
        print(f"    SỬA {str(doc.get('filename'))[:32]:<34} {truoc} → {sau}", flush=True)


async def chay(mongo, in_flight_max: int) -> None:
    # import nặng (pdfium/onnx) để TRONG hàm — pha --mark/--trang-thai khỏi gánh.
    from app.worker.run_job import process_doc

    from src.extentions.multimodal.vlm_client import endpoint_count, total_vlm_concurrency

    print(f"  endpoint: {os.getenv('VLLM_ENDPOINTS') or os.getenv('VLLM_BASE_URL')}"
          f"  ·  {endpoint_count()} máy  ·  trần tổng {total_vlm_concurrency()}"
          f"  ·  in-flight {in_flight_max}", flush=True)

    con_lai = await mongo.db[config.COLL_GCN].count_documents({"sph_rerun": "pending"})
    print(f"  {con_lai:,} hồ sơ chờ chạy lại\n", flush=True)
    if not con_lai:
        print("  Chưa đánh dấu gì. Chạy --mark trước.", flush=True)
        return

    st: Counter = Counter()
    tasks: set[asyncio.Task] = set()
    t0 = time.monotonic()
    while True:
        while not _stop and len(tasks) < in_flight_max:
            doc = await _claim(mongo)
            if not doc:
                break
            tasks.add(asyncio.create_task(_chay_mot(mongo, doc, process_doc, st)))
        if not tasks:
            break
        done, tasks = await asyncio.wait(tasks, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t.exception():
                print(f"    task lỗi: {t.exception()}", flush=True)
        xong = st["done"] + st["error"] + st["loi"] + st["no_file"]
        if xong:
            phut = (time.monotonic() - t0) / 60 or 1e-9
            print(f"  … {xong:,} hồ sơ  ·  sửa được {st['sua_duoc']:,}  ·  "
                  f"{xong / phut:.1f} hồ sơ/phút", flush=True)

    print("=" * 70)
    print(f" XONG {st['done']:,} thành công · {st['error'] + st['loi']:,} lỗi  ·  "
          f"SỬA ĐƯỢC SPH {st['sua_duoc']:,} · y nguyên {st['y_nguyen']:,}")
    print("=" * 70)
    if _stop:
        print("  Dừng theo yêu cầu. Doc đang chạy dở (nếu có): --reset-running rồi chạy tiếp.")


# ── Tiện ích ─────────────────────────────────────────────────────────────────

async def trang_thai(mongo) -> None:
    gcns = mongo.db[config.COLL_GCN]
    for v in ("pending", "running", "done", "error"):
        print(f"  sph_rerun={v:<8} {await gcns.count_documents({'sph_rerun': v}):,}")


async def reset_running(mongo) -> None:
    """Doc kẹt "running" (script bị giết giữa chừng) → về pending + trả status
    processing về done cho khớp counter."""
    gcns = mongo.db[config.COLL_GCN]
    n = 0
    async for doc in gcns.find({"sph_rerun": "running"}, {"batch_id": 1, "status": 1}):
        res = await gcns.update_one(
            {"_id": doc["_id"], "sph_rerun": "running"},
            {"$set": {"sph_rerun": "pending", "status": "done"}})
        if res.modified_count:
            if doc.get("status") == "processing":
                await bump(mongo.db[config.COLL_BATCH], doc.get("batch_id"),
                           processing=-1, done=1)
            n += 1
    print(f"  đã trả {n:,} hồ sơ về pending")


async def go_co(mongo) -> None:
    res = await mongo.db[config.COLL_GCN].update_many(
        {"sph_rerun": {"$exists": True}}, {"$unset": {"sph_rerun": "", "sph_rerun_at": ""}})
    print(f"  đã gỡ cờ trên {res.modified_count:,} hồ sơ")


async def main() -> None:
    p = argparse.ArgumentParser(description="Chạy lại SPH thiếu chữ trên MỘT endpoint riêng.")
    p.add_argument("--mark", action="store_true", help="Pha 1: đánh dấu phạm vi (không gọi GPU).")
    p.add_argument("--trang-thai", action="store_true", help="Đếm theo cờ sph_rerun rồi thoát.")
    p.add_argument("--reset-running", action="store_true", help="Trả doc kẹt 'running' về pending.")
    p.add_argument("--go-co", action="store_true", help="Gỡ sạch cờ sph_rerun (dọn sau khi xong).")
    p.add_argument("--in-flight", type=int, default=config.MAX_IN_FLIGHT,
                   help=f"Số hồ sơ chạy song song (mặc định {config.MAX_IN_FLIGHT}).")
    p.add_argument("--limit", type=int, default=None, help="--mark: chỉ đánh dấu N hồ sơ.")
    p.add_argument("--batch-id", default=None, help="--mark: giới hạn 1 lô.")
    p.add_argument("--dry-run", action="store_true", help="--mark: chỉ đếm + in mẫu.")
    p.add_argument("--ke-ca-rong", action="store_true", help="--mark: gộp cả hồ sơ SPH rỗng.")
    p.add_argument("--ke-ca-da-hau-kiem", action="store_true",
                   help="--mark: NGUY HIỂM — gộp cả hồ sơ đã hậu kiểm (overrides lệch index).")
    args = p.parse_args()

    from src.extentions.mongo_helper import AsyncMongo
    mongo = AsyncMongo()
    while not await mongo.ping():
        print("  chưa kết nối được Mongo, thử lại…", flush=True)
        await asyncio.sleep(2)

    if args.trang_thai:
        await trang_thai(mongo)
    elif args.reset_running:
        await reset_running(mongo)
    elif args.go_co:
        await go_co(mongo)
    elif args.mark:
        await mark(mongo, args.limit, args.batch_id, args.dry_run,
                   args.ke_ca_rong, args.ke_ca_da_hau_kiem)
    else:
        signal.signal(signal.SIGINT, _xin_dung)
        signal.signal(signal.SIGTERM, _xin_dung)
        await chay(mongo, args.in_flight)


if __name__ == "__main__":
    asyncio.run(main())
