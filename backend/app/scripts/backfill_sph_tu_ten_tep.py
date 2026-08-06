"""Vá SỐ PHÁT HÀNH thiếu tiền tố chữ bằng chính TÊN TỆP — KHÔNG gọi GPU.

Ca xử lý: model đọc đúng cụm số nhưng bỏ mất 1-4 chữ in hoa đứng trước, trong khi
tên tệp đã mang sẵn tiền tố đó:

    AD 597734.pdf  →  SPH đang lưu ['597734']   ⇒  'AD 597734'
    AN 065845.pdf  →  SPH đang lưu ['065845']   ⇒  'AN 065845'

ĐIỀU KIỆN GHÉP (chặt, cố ý): phần SỐ của tên tệp phải TRÙNG với cụm số đang lưu
(bỏ qua số 0 ở đầu). Không trùng ⇒ KHÔNG đụng — vd `AB 243010.pdf → ['1000005']`
là model đọc sai hẳn, phải để VLM chạy lại, vá bừa theo tên tệp là bịa dữ liệu.

Chỉ sửa ĐÚNG entry khớp; các entry khác trong cùng hồ sơ giữ nguyên.

Các field suy ra từ extractions được tính LẠI bằng chính hàm mà run_job dùng
(group_key/extracted_so_phat_hanhs/summary/gcn_rows) — không thì bảng trích xuất
và phép gom nhóm sẽ lệch so với extractions.

KHÔNG đụng: nhóm nghi trùng (dup_group). Chạy `backfill_dup_groups.py` sau nếu cần.
KHÔNG đụng: tên file cắt đã sinh trên S3 (đặt theo SPH cũ) — chỉ là tên, nội dung đúng.

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.backfill_sph_tu_ten_tep --dry-run
    docker compose exec api python -m app.scripts.backfill_sph_tu_ten_tep --limit 500
    docker compose exec api python -m app.scripts.backfill_sph_tu_ten_tep

Làm TRƯỚC rồi mới `rerun_sph_endpoint --mark`: hồ sơ đã vá xong không còn khớp bộ
lọc "cụm số trần" nữa nên tự động rơi khỏi phạm vi chạy VLM.
"""

import argparse
import asyncio
import re
from collections import Counter

from pymongo import UpdateOne

from app import config
from app.scripts.requeue_sph_thieu_chu import _MONGO_RE, _da_hau_kiem

BULK = 500

# Tên tệp mang tiền tố: "AD 597734.pdf", "AĐ381126.pdf", "S 266529.pdf",
# kèm biến thể còn dính chữ "Số"/"So"/"S6" ở đầu ("So AN 065845.pdf").
_TEN_TEP_RE = re.compile(
    r"^(?:S[ỐỒỔỖỘÔỎÕỌÓÒƠỚO0-9]\s*)?([A-ZĐ]{1,4})\s*(\d{4,7})$")
# Cụm số trần trong SPH đang lưu (khớp _MONGO_RE phía DB).
_SO_TRAN_RE = re.compile(r"^\s*(\d{4,7})\s*$")


def _tu_ten_tep(filename: str) -> tuple[str, str] | None:
    """('AD', '597734') từ 'AD 597734.pdf'. None nếu tên tệp không mang tiền tố."""
    if not isinstance(filename, str) or not filename.strip():
        return None
    ten = filename.strip().rsplit("/", 1)[-1]
    ten = re.sub(r"\.pdf$", "", ten, flags=re.IGNORECASE)
    m = _TEN_TEP_RE.fullmatch(re.sub(r"\s+", " ", ten).strip().upper())
    return (m.group(1), m.group(2)) if m else None


def _khop(so_luu: str, so_ten: str) -> bool:
    """Cùng một con số, bỏ qua số 0 ở đầu ('065845' ≡ '65845')."""
    return so_luu.lstrip("0") == so_ten.lstrip("0") and so_luu.lstrip("0") != ""


def _va_extractions(extractions, prefix: str, so_ten: str) -> int:
    """Sửa TẠI CHỖ mọi entry có SPH là cụm số trần trùng `so_ten`. Trả số entry đã sửa."""
    n = 0
    for r in extractions or []:
        res = r.get("result") if isinstance(r, dict) else None
        if not isinstance(res, dict):
            continue
        for e in res.get("Đăng ký") or []:
            g = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
            if not isinstance(g, dict):
                continue
            sph = g.get("Số phát hành")
            if not isinstance(sph, str):
                continue
            m = _SO_TRAN_RE.fullmatch(sph)
            if m and _khop(m.group(1), so_ten):
                # Lấy cụm số của TÊN TỆP, không phải cụm đang lưu: seri in trên
                # phôi có số 0 ở đầu ("AN 065845"), model hay nuốt mất số 0 đó.
                g["Số phát hành"] = f"{prefix} {so_ten}"
                n += 1
    return n


async def run(mongo, limit, batch_id, dry_run, ke_ca_da_hau_kiem) -> None:
    from app.summary import collect_so_phat_hanhs, group_key_of, per_gcn, summarize

    gcns = mongo.db[config.COLL_GCN]
    q: dict = {
        "status": "done",
        "extractions.result.Đăng ký.Giấy chứng nhận.Số phát hành": {"$regex": _MONGO_RE},
        # Lọc sơ ở DB: chỉ hồ sơ có tên tệp mang tiền tố chữ (khối 70% rẻ tiền).
        "filename": {"$regex": r"^\s*(?:S[ỐÔO0-9]\s*)?[A-ZĐ]{1,4} ?[0-9]{4,7}\.pdf$",
                     "$options": "i"},
    }
    if batch_id:
        q["batch_id"] = batch_id

    print("  đang quét kho…", flush=True)
    tong = await gcns.count_documents(q)
    print(f"  {tong:,} hồ sơ khớp lọc sơ\n", flush=True)
    if not tong:
        return

    cur = gcns.find(q, {"filename": 1, "review": 1, "extractions": 1, "cuts": 1}).batch_size(BULK)
    if limit:
        cur = cur.limit(limit)

    st, mau, ops = Counter(), [], []

    async def _flush() -> None:
        if not ops or dry_run:
            ops.clear()
            return
        res = await gcns.bulk_write(ops, ordered=False)
        st["da_ghi"] += res.modified_count
        ops.clear()

    async for doc in cur:
        st["xet"] += 1
        cap = _tu_ten_tep(doc.get("filename"))
        if not cap:
            st["ten_tep_khong_dung_dang"] += 1
            continue
        if _da_hau_kiem(doc) and not ke_ca_da_hau_kiem:
            st["bo_qua_da_hau_kiem"] += 1
            continue
        prefix, so_ten = cap
        extractions = doc.get("extractions") or []
        truoc = collect_so_phat_hanhs(extractions)
        if not _va_extractions(extractions, prefix, so_ten):
            # Tên tệp có tiền tố nhưng SỐ KHÔNG TRÙNG → model đọc sai hẳn, để VLM lo.
            st["so_khong_trung"] += 1
            continue
        st["va_duoc"] += 1
        sau = collect_so_phat_hanhs(extractions)
        if len(mau) < 12:
            mau.append(f"{str(doc.get('filename'))[:30]:<32} {truoc} → {sau}")
        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {
                "extractions": extractions,
                "group_key": group_key_of(extractions),
                "extracted_so_phat_hanhs": sau,
                "summary": summarize(extractions),
                "gcn_rows": per_gcn(extractions, doc.get("cuts") or []),
            }},
        ))
        if len(ops) >= BULK:
            await _flush()
    await _flush()

    print("=" * 74)
    print(f" XÉT {st['xet']:,}  ·  VÁ ĐƯỢC {st['va_duoc']:,}"
          + ("   (DRY-RUN, chưa ghi gì)" if dry_run else f"  ·  đã ghi {st['da_ghi']:,}"))
    print("=" * 74)
    print(f"  số không trùng tên tệp → để VLM chạy lại : {st['so_khong_trung']:,}")
    print(f"  tên tệp không đúng dạng                  : {st['ten_tep_khong_dung_dang']:,}")
    print(f"  bỏ qua vì đã hậu kiểm                    : {st['bo_qua_da_hau_kiem']:,}")
    if mau:
        print("\n  MẪU:")
        for m in mau:
            print(f"    {m}")
    if dry_run:
        print("\n  DRY-RUN: chưa sửa doc nào. Bỏ --dry-run để ghi thật.")
    else:
        print("\n  Xong. Nhóm nghi trùng: chạy backfill_dup_groups.py nếu cần.")
        print("  Phần còn lại (số không trùng) → rerun_sph_endpoint --mark rồi chạy VLM.")


async def main() -> None:
    p = argparse.ArgumentParser(description="Vá SPH thiếu tiền tố bằng tên tệp (không dùng GPU).")
    p.add_argument("--limit", type=int, default=None, help="Chỉ xử N hồ sơ (chạy thử).")
    p.add_argument("--batch-id", default=None, help="Giới hạn 1 lô.")
    p.add_argument("--dry-run", action="store_true", help="Chỉ đếm + in mẫu, KHÔNG ghi.")
    p.add_argument("--ke-ca-da-hau-kiem", action="store_true",
                   help="Vá cả hồ sơ đã hậu kiểm (giá trị override của chuyên viên vẫn thắng).")
    args = p.parse_args()

    from src.extentions.mongo_helper import AsyncMongo
    mongo = AsyncMongo()
    while not await mongo.ping():
        print("  chưa kết nối được Mongo, thử lại…", flush=True)
        await asyncio.sleep(2)
    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    await run(mongo, args.limit, args.batch_id, args.dry_run, args.ke_ca_da_hau_kiem)


if __name__ == "__main__":
    asyncio.run(main())
