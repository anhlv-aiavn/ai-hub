"""Đưa lại vào hàng đợi các hồ sơ có SỐ PHÁT HÀNH THIẾU TIỀN TỐ CHỮ — tức chỉ
còn cụm số trần kiểu "471319", "266529" trong khi phôi thật là "AP 471319".

Vì sao đây là ca đáng chạy lại (khác với "SPH rỗng"): cụm số ĐÚNG, model chỉ bỏ
sót 1-4 chữ in hoa in cạnh nó. Prompt mới đã siết đúng chỗ này ⇒ chạy lại có
kỳ vọng cứu được; không phải đốt GPU mù.

PHÂN BIỆT với SPH bản MỚI (thuần số 8-15 chữ số, vd "0103040010") — dạng đó là
HỢP LỆ, tuyệt đối không đụng vào. Bởi vậy bộ lọc chỉ bắt cụm số DÀI 4-7.

Chỉ đổi `status` → "queued"; worker tự claim và chạy lại pipeline (đúng một nơi
ghi extractions là run_job). Hồ sơ ĐÃ HẬU KIỂM mặc định BỎ QUA — overrides/deleted
đánh theo INDEX của extractions, chạy lại làm lệch (xem requeue_cat_lai).

Chạy TRONG CONTAINER:
    # 1) xem quy mô, KHÔNG đổi gì:
    docker compose exec api python -m app.scripts.requeue_sph_thieu_chu --dry-run
    # 2) chạy thử 200 hồ sơ:
    docker compose exec api python -m app.scripts.requeue_sph_thieu_chu --limit 200
    # 3) gộp cả hồ sơ KHÔNG có SPH nào (rỗng/thiếu hẳn):
    docker compose exec api python -m app.scripts.requeue_sph_thieu_chu --ke-ca-rong --dry-run

Theo dõi tiến độ riêng của đợt này (không lẫn với hàng đợi import):
    db.gcn.countDocuments({sph_requeue_at: {$exists: true}, status: {$ne: "done"}})
"""

import argparse
import asyncio
import re
from collections import Counter
from datetime import datetime, timezone

from pymongo import UpdateOne

from app import config
from app.batch_counters import bump
from app.db import batches, gcns

BULK = 500
SPH_PATH = "extractions.result.Đăng ký.Giấy chứng nhận.Số phát hành"

# Cụm số TRẦN 4-7 chữ số = seri bản cũ bị mất tiền tố chữ. Trên 8 chữ số là SPH
# bản mới (hợp lệ) → không bắt. Cho phép khoảng trắng thừa hai đầu.
_THIEU_CHU_RE = re.compile(r"^\s*\d{4,7}\s*$")
# Regex phía Mongo: dùng để LỌC SƠ ở DB (PCRE của Mongo, cùng ý nghĩa).
_MONGO_RE = r"^\s*[0-9]{4,7}\s*$"


def _sph_list(extractions) -> list:
    """Mọi Số phát hành đang lưu của 1 doc (theo thứ tự record → entry)."""
    out = []
    for r in extractions or []:
        res = r.get("result") if isinstance(r, dict) else None
        if not isinstance(res, dict):
            continue
        for e in res.get("Đăng ký") or []:
            g = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
            if isinstance(g, dict):
                out.append(g.get("Số phát hành"))
    return out


def _thieu_chu(extractions, ke_ca_rong: bool) -> bool:
    sph = _sph_list(extractions)
    if not sph:
        return ke_ca_rong
    if any(isinstance(s, str) and _THIEU_CHU_RE.fullmatch(s) for s in sph):
        return True
    return ke_ca_rong and all(not (isinstance(s, str) and s.strip()) for s in sph)


def _da_hau_kiem(doc: dict) -> bool:
    rv = doc.get("review") or {}
    return bool(rv.get("overrides") or rv.get("deleted")
                or rv.get("status") in ("reviewed", "needs_review"))


async def run(limit, batch_id, dry_run, ke_ca_rong, ke_ca_da_hau_kiem) -> None:
    q: dict = {"status": "done"}
    if batch_id:
        q["batch_id"] = batch_id
    if ke_ca_rong:
        # Lọc sơ rộng hơn: cụm số trần HOẶC không có SPH nào là chuỗi hợp lệ.
        q["$or"] = [{SPH_PATH: {"$regex": _MONGO_RE}},
                    {SPH_PATH: {"$in": [None, ""]}},
                    {SPH_PATH: {"$exists": False}}]
    else:
        q[SPH_PATH] = {"$regex": _MONGO_RE}

    print("  đang đếm (quét cả kho, chờ chút)…", flush=True)
    tong = await gcns().count_documents(q)
    print(f"  {tong:,} hồ sơ khớp lọc sơ ở DB\n", flush=True)
    if not tong:
        return

    cur = gcns().find(
        q, {"batch_id": 1, "status": 1, "review": 1, "filename": 1,
            "extractions.result.Đăng ký.Giấy chứng nhận.Số phát hành": 1},
    ).batch_size(BULK)
    if limit:
        cur = cur.limit(limit)

    st = Counter()
    ops: list[UpdateOne] = []
    delta: Counter = Counter()
    mau: list[str] = []

    async def _flush() -> None:
        if not ops or dry_run:
            ops.clear()
            return
        res = await gcns().bulk_write(ops, ordered=False)
        st["da_ghi"] += res.modified_count
        ops.clear()

    async for doc in cur:
        st["xet"] += 1
        if not _thieu_chu(doc.get("extractions"), ke_ca_rong):
            st["bo_qua_khong_khop"] += 1
            continue
        if _da_hau_kiem(doc) and not ke_ca_da_hau_kiem:
            st["bo_qua_da_hau_kiem"] += 1
            continue
        st["chon"] += 1
        if len(mau) < 10:
            mau.append(f"{str(doc.get('filename'))[:34]:<36} {_sph_list(doc.get('extractions'))}")
        delta[(doc.get("batch_id"), doc.get("status") or "done")] += 1
        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            # sph_requeue_at: dấu mốc để ĐO tiến độ riêng của đợt này giữa hàng
            # đợi chung. chu_cuoi_llm_done bị xoá vì extractions sẽ được ghi mới.
            {"$set": {"status": "queued", "error": None, "error_kind": None,
                      "sph_requeue_at": datetime.now(timezone.utc)},
             "$unset": {"chu_cuoi_llm_done": ""}},
        ))
        if len(ops) >= BULK:
            await _flush()
    await _flush()

    if not dry_run:
        for (bid, cu), n in delta.items():
            await bump(batches(), bid, **{cu: -n, "queued": n})

    print("=" * 70)
    print(f" XÉT {st['xet']:,}  ·  CHỌN {st['chon']:,}"
          + ("   (DRY-RUN, chưa đổi gì)" if dry_run else f"  ·  đã về hàng đợi {st['da_ghi']:,}"))
    print("=" * 70)
    print(f"  bỏ qua vì đã hậu kiểm : {st['bo_qua_da_hau_kiem']:,}   ← chạy lại sẽ lệch overrides")
    print(f"  bỏ qua vì soi lại không khớp : {st['bo_qua_khong_khop']:,}")
    if mau:
        print("\n  MẪU (tên tệp · SPH đang lưu):")
        for m in mau:
            print(f"    {m}")
    if dry_run:
        print("\n  DRY-RUN: chưa đổi doc nào. Bỏ --dry-run để chạy thật.")
    elif st["chon"]:
        print("\n  Worker sẽ nhặt dần. Theo dõi: docker compose logs -f worker")


async def main() -> None:
    p = argparse.ArgumentParser(description="Requeue hồ sơ có SPH thiếu tiền tố chữ.")
    p.add_argument("--limit", type=int, default=None, help="Chỉ xử N hồ sơ (chạy thử).")
    p.add_argument("--batch-id", default=None, help="Giới hạn 1 lô.")
    p.add_argument("--dry-run", action="store_true", help="Chỉ đếm + in mẫu, KHÔNG đổi status.")
    p.add_argument("--ke-ca-rong", action="store_true",
                   help="Gộp cả hồ sơ KHÔNG có SPH nào (rỗng/thiếu hẳn) — quy mô lớn hơn nhiều.")
    p.add_argument("--ke-ca-da-hau-kiem", action="store_true",
                   help="NGUY HIỂM: chạy lại cả hồ sơ đã hậu kiểm — overrides lệch index.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}"
          f"  ·  lọc: SPH là cụm số trần 4-7 chữ số"
          + ("  + SPH rỗng" if args.ke_ca_rong else "")
          + f"  ·  đã hậu kiểm: {'CHẠY LẠI (nguy hiểm)' if args.ke_ca_da_hau_kiem else 'bỏ qua'}")
    await run(args.limit, args.batch_id, args.dry_run, args.ke_ca_rong, args.ke_ca_da_hau_kiem)


if __name__ == "__main__":
    asyncio.run(main())
