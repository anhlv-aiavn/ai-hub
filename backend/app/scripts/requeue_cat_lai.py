"""Đưa hồ sơ >5 trang về hàng đợi để CẮT LẠI theo luật mới (LUẬT MỘT BÌA).

Chỉ đổi `status` → "queued"; worker tự claim và chạy lại toàn bộ pipeline. KHÔNG
tự xoá/ghi extractions ở đây — để đúng một nơi ghi (run_job) chịu trách nhiệm.

Vì sao chỉ >5 trang: file ≤ DETECT_MIN_PAGES đi nhánh "1 file = 1 giấy", không
gọi classify_page, nên luật mới KHÔNG đổi gì cho chúng — chạy lại là đốt GPU vô ích.

HAI THỨ ĐƯỢC BẢO VỆ:

1. HỒ SƠ ĐÃ HẬU KIỂM — mặc định BỎ QUA. `review.overrides` và `review.deleted`
   đánh theo INDEX của `extractions` (flatten.effective_extractions). Cắt lại làm
   đổi số nhóm ⇒ index lệch ⇒ chữa tay của chuyên viên gắn sang giấy khác. Muốn
   chạy vẫn được bằng --ke-ca-da-doi-soat, nhưng phải biết mình đang đánh đổi gì.

2. ĐẾM CỦA LÔ — mỗi doc rời "done" về "queued" đều bump counts tương ứng, nếu
   không thanh tiến độ trên UI sẽ sai vĩnh viễn.

Chạy TRONG CONTAINER:
    # 1) xem quy mô, KHÔNG đổi gì:
    docker compose exec api python -m app.scripts.requeue_cat_lai --dry-run
    # 2) chỉ hồ sơ CÓ DẤU HIỆU cắt sai (hẹp hơn nhiều, nên làm trước):
    docker compose exec api python -m app.scripts.requeue_cat_lai --chi-nghi --dry-run
    # 3) chạy thật, thử lô nhỏ trước:
    docker compose exec api python -m app.scripts.requeue_cat_lai --chi-nghi --limit 200
"""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone

from pymongo import UpdateOne

from app import config
from app.batch_counters import bump
from app.db import batches, gcns
from app.scripts.audit_cut_vlm import _nghi_ngo

BULK = 500
DETECT_MIN_PAGES = 5  # khớp run_job.DETECT_MIN_PAGES (nhánh ≤5 không dùng classify_page)


def _da_doi_soat(doc: dict) -> bool:
    """Có dữ liệu hậu kiểm gắn theo index không? (không chỉ dựa vào status —
    chuyên viên có thể sửa mà chưa bấm 'đã hậu kiểm')."""
    rv = doc.get("review") or {}
    return bool(rv.get("overrides") or rv.get("deleted")
                or rv.get("status") in ("reviewed", "needs_review"))


async def run(limit, batch_id, dry_run, chi_nghi, ke_ca_da_doi_soat) -> None:
    q: dict = {"page_count": {"$gt": DETECT_MIN_PAGES},
               "status": {"$in": ["done", "error"]}}
    if batch_id:
        q["batch_id"] = batch_id

    print("  đang đếm…", flush=True)
    tong = await gcns().count_documents(q)
    print(f"  {tong:,} hồ sơ >{DETECT_MIN_PAGES} trang, trạng thái done/error\n", flush=True)

    cur = gcns().find(
        q, {"page_count": 1, "batch_id": 1, "status": 1, "review": 1,
            "extractions.page_indices": 1},
    ).batch_size(BULK)
    if limit:
        cur = cur.limit(limit)

    st = Counter()
    ops: list[UpdateOne] = []
    # đếm theo (batch_id, status cũ) để bump chính xác — mỗi lô một con số
    delta: Counter = Counter()

    async def _flush() -> None:
        if not ops or dry_run:
            ops.clear()
            return
        res = await gcns().bulk_write(ops, ordered=False)
        st["da_ghi"] += res.modified_count
        ops.clear()

    async for doc in cur:
        st["xet"] += 1
        if _da_doi_soat(doc) and not ke_ca_da_doi_soat:
            st["bo_qua_da_doi_soat"] += 1
            continue
        if chi_nghi and not _nghi_ngo(doc):
            st["bo_qua_khong_nghi"] += 1
            continue
        st["chon"] += 1
        cu = doc.get("status") or "done"
        delta[(doc.get("batch_id"), cu)] += 1
        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            # Xoá cờ chu_cuoi_llm_done: nó đánh dấu "đã thử LLM cho ca cảnh báo,
            # khỏi gọi lại". Cắt lại sinh extractions MỚI (chính những hồ sơ trước
            # đây mất trang biến động) → phải cho LLM thử lại trên nội dung mới,
            # không thì hồ sơ vừa lấy lại được trang biến động vẫn kẹt cảnh báo.
            # cat_lai_at: dấu mốc để ĐO ĐƯỢC tiến độ. Hàng đợi thường có sẵn hàng
            # chục nghìn doc từ import, doc cắt lại lẫn vào đó và fairness chia
            # lượt theo lô → không có dấu này thì không tách nổi tiến độ cắt lại
            # khỏi tiến độ import. Cũng là cách truy lại "hồ sơ nào đã cắt lại".
            {"$set": {"status": "queued", "error": None, "error_kind": None,
                      "cat_lai_at": datetime.now(timezone.utc)},
             "$unset": {"chu_cuoi_llm_done": ""}},
        ))
        if len(ops) >= BULK:
            await _flush()
    await _flush()

    if not dry_run:
        for (bid, cu), n in delta.items():
            await bump(batches(), bid, **{cu: -n, "queued": n})

    print("=" * 66)
    print(f" XÉT {st['xet']:,} hồ sơ  ·  CHỌN {st['chon']:,}"
          + ("   (DRY-RUN, chưa đổi gì)" if dry_run else f"  ·  đã đưa về hàng đợi {st['da_ghi']:,}"))
    print("=" * 66)
    print(f"  bỏ qua vì ĐÃ HẬU KIỂM : {st['bo_qua_da_doi_soat']:,}"
          "   ← chạy lại sẽ làm lệch overrides theo index")
    if chi_nghi:
        print(f"  bỏ qua vì không nghi  : {st['bo_qua_khong_nghi']:,}")
    if dry_run:
        print("\n  DRY-RUN: chưa đổi 1 doc nào. Bỏ --dry-run để chạy thật.")
    elif st["chon"]:
        print("\n  Worker sẽ tự nhặt dần. Theo dõi: docker compose logs -f worker")


async def main() -> None:
    p = argparse.ArgumentParser(description="Đưa hồ sơ >5 trang về hàng đợi để cắt lại.")
    p.add_argument("--limit", type=int, default=None, help="Chỉ xử N hồ sơ (chạy thử).")
    p.add_argument("--batch-id", default=None, help="Giới hạn 1 lô.")
    p.add_argument("--dry-run", action="store_true", help="Chỉ đếm, KHÔNG đổi status.")
    p.add_argument("--chi-nghi", action="store_true",
                   help="Chỉ hồ sơ CÓ DẤU HIỆU cắt sai (thiếu trang đầu/hở giữa/nhóm 1 trang).")
    p.add_argument("--ke-ca-da-doi-soat", action="store_true",
                   help="NGUY HIỂM: chạy lại cả hồ sơ đã hậu kiểm — overrides sẽ lệch index.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}"
          f"  ·  {'CHỈ HỒ SƠ NGHI' if args.chi_nghi else 'MỌI hồ sơ >5 trang'}"
          f"  ·  hồ sơ đã hậu kiểm: {'CHẠY LẠI (nguy hiểm)' if args.ke_ca_da_doi_soat else 'bỏ qua'}")
    await run(args.limit, args.batch_id, args.dry_run, args.chi_nghi, args.ke_ca_da_doi_soat)


if __name__ == "__main__":
    asyncio.run(main())
