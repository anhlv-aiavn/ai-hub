"""Phase 1 — Backfill mã MĐSD cho kho gcn (600k). KHÔNG GPU, KHÔNG MinIO.

Gắn `Mã MĐSD` / `MĐSD chuẩn` vào TỪNG mục đích trong `extractions`, cộng ba
trường tóm tắt phẳng ở top-level để truy vấn được hàng đợi rà tay.

VÌ SAO GHI THEO PATH CHÍNH XÁC, KHÔNG $set CẢ MẢNG `extractions`:
`extractions` là toàn bộ kết quả VLM của một bộ GCN — ghi lại cả mảng cho mỗi
doc là hàng chục KB × 600k, vừa nặng I/O vừa mở cửa sổ ghi đè lớn nếu có tiến
trình khác đang sửa doc. Ghi đúng vài ô ta tính ra thì nhỏ và hẹp:
    extractions.0.result.Đăng ký.0.Thửa đất.1.Mục đích sử dụng.0.Mã MĐSD

Resumable + idempotent: chỉ xử doc có `mdsdd_version` cũ hơn ALGO_VERSION; chạy
lại cho kết quả y hệt (KILL của smoke stage `mdsdd_real` canh điều này).

KHÔNG đụng `Loại mục đích` (raw VLM) và KHÔNG đụng `review.overrides` — sửa tay
của chuyên viên nằm ở tầng khác, luôn đè lên giá trị ta ghi ở đây.

Chạy TRONG CONTAINER:
    # 1) xem quy mô, KHÔNG ghi gì:
    docker compose exec api python -m app.scripts.backfill_mdsdd --dry-run
    # 2) chạy thử lô nhỏ, in vài doc để mắt người kiểm:
    docker compose exec api python -m app.scripts.backfill_mdsdd --limit 200 --show 5
    # 3) chạy thật toàn kho:
    docker compose exec api python -m app.scripts.backfill_mdsdd
"""

import argparse
import asyncio
import time
from collections import Counter

from pymongo import UpdateOne

from app import config
from app.db import gcns
from src.extentions.multimodal.mdsdd import (
    ALGO_VERSION,
    KEY_MA,
    KEY_TEN,
    duyet_muc_dich,
    gan_mdsdd,
)

BULK = 500
PROGRESS_SEC = 10.0


def _ops_cho_doc(doc: dict) -> tuple[dict, dict]:
    """→ ($set fields, tóm tắt). Sửa bản ghi trong RAM rồi đọc lại đúng các ô đã gắn."""
    records = doc.get("extractions") or []
    tt = gan_mdsdd(records)
    fields: dict = {
        "mdsdd_version": ALGO_VERSION,
        "mdsdd_can_ra_tay": tt["can_ra_tay"],
        "mdsdd_ly_do": tt["ly_do"],
    }
    for ri, ei, ti, mi, md, _ in duyet_muc_dich(records):
        if KEY_MA not in md:      # mục đích rỗng — gan_mdsdd cố ý không gắn ô nào
            continue
        p = (f"extractions.{ri}.result.Đăng ký.{ei}"
             f".Thửa đất.{ti}.Mục đích sử dụng.{mi}")
        fields[f"{p}.{KEY_MA}"] = md[KEY_MA]
        fields[f"{p}.{KEY_TEN}"] = md[KEY_TEN]
    return fields, tt


def _print_doc(doc: dict) -> None:
    print(f"\n  ── {doc.get('filename') or doc.get('_id')}")
    for ri, ei, ti, mi, md, dia_chi in duyet_muc_dich(doc.get("extractions") or []):
        ma = md.get(KEY_MA, "")
        ten = md.get(KEY_TEN, "")
        dau = "✓" if ma != "" else "·"
        print(f"     {dau} [{ri}.{ei}.{ti}.{mi}] {str(md.get('Loại mục đích'))[:34]:<36}"
              f" → {str(ma):<5} {ten[:34]:<36} | {dia_chi[:44]}")


async def run(limit, batch_id, dry_run, show, lam_lai) -> None:
    q: dict = {"extractions": {"$exists": True, "$ne": []}}
    if not lam_lai:
        q["$or"] = [{"mdsdd_version": {"$exists": False}},
                    {"mdsdd_version": {"$lt": ALGO_VERSION}}]
    if batch_id:
        q["batch_id"] = batch_id

    print("  đang đếm…", flush=True)
    total = await gcns().count_documents(q)
    if limit:
        total = min(total, limit)
    print(f"  {total:,} hồ sơ cần xử lý\n", flush=True)
    if not total:
        return

    st = Counter()
    ops: list[UpdateOne] = []
    n_doc = n_written = 0
    t0 = last = time.perf_counter()

    cur = gcns().find(q, {"extractions": 1, "filename": 1, "finished_at": 1}).batch_size(BULK)
    if limit:
        cur = cur.limit(limit)

    async for doc in cur:
        n_doc += 1
        fields, tt = _ops_cho_doc(doc)
        st["md"] += tt["n"]
        st["ra_ma"] += tt["n_ra_ma"]
        if tt["can_ra_tay"]:
            st["doc_ra_tay"] += 1
        for ld in tt["ly_do"]:
            st[f"ly_do_{ld}"] += 1
        if not tt["n"]:
            st["doc_khong_co_muc_dich"] += 1
        if show and n_doc <= show:
            _print_doc(doc)

        if not dry_run:
            # CHỐNG ĐUA VỚI WORKER: path ghi bám theo CHỈ SỐ thửa/mục đích đọc
            # được lúc nãy. Nếu giữa lúc đọc và lúc ghi, worker cắt lại hồ sơ và
            # thay `extractions` bằng cây có số thửa khác, thì $set theo chỉ số
            # cũ sẽ đặt mã vào ô của THỬA KHÁC — sai âm thầm.
            # `finished_at` đổi mỗi lần run_job ghi xong, nên đưa vào bộ lọc là
            # có khoá lạc quan: doc đã bị worker sửa thì update KHÔNG khớp, bỏ
            # qua, lần backfill sau nhặt lại (version vẫn cũ).
            ops.append(UpdateOne(
                {"_id": doc["_id"], "finished_at": doc.get("finished_at")},
                {"$set": fields},
            ))
            if len(ops) >= BULK:
                res = await gcns().bulk_write(ops, ordered=False)
                n_written += res.modified_count
                st["bo_qua_dang_chay"] += len(ops) - res.matched_count
                ops.clear()

        now = time.perf_counter()
        if now - last >= PROGRESS_SEC:
            _progress(n_doc, total, t0)
            last = now

    if ops and not dry_run:
        res = await gcns().bulk_write(ops, ordered=False)
        n_written += res.modified_count
        st["bo_qua_dang_chay"] += len(ops) - res.matched_count

    _tong_ket(n_doc, n_written, st, dry_run, t0)


def _progress(n: int, total: int, t0: float) -> None:
    el = time.perf_counter() - t0
    toc = n / el if el else 0
    con = (total - n) / toc if toc else 0
    print(f"  … {n:,}/{total:,} ({n / total * 100:5.1f}%)  ·  {toc:,.0f} doc/s"
          f"  ·  còn ~{con / 60:.1f} phút", flush=True)


def _tong_ket(n_doc, n_written, st, dry_run, t0) -> None:
    md = st["md"] or 1
    print("\n" + "=" * 72)
    print(f" BACKFILL MĐSD  ·  {n_doc:,} hồ sơ  ·  {st['md']:,} bản ghi mục đích"
          + ("   (DRY-RUN, chưa ghi gì)" if dry_run else f"  ·  đã ghi {n_written:,}"))
    print("=" * 72)
    print(f"   ra mã            : {st['ra_ma']:,} ({st['ra_ma'] / md * 100:.1f}%)")
    print(f"   để trống, rà tay : {st['md'] - st['ra_ma']:,} "
          f"({(st['md'] - st['ra_ma']) / md * 100:.1f}%)")
    print(f"   hồ sơ cần rà tay : {st['doc_ra_tay']:,} / {n_doc:,}"
          "   ← lọc bằng mdsdd_can_ra_tay: true")
    for ld in ("nhap_nhang", "khong_phai_muc_dich", "chua_map"):
        if st.get(f"ly_do_{ld}"):
            print(f"     · {ld:<22}: {st[f'ly_do_{ld}']:,} hồ sơ")
    if st["doc_khong_co_muc_dich"]:
        print(f"   hồ sơ không có mục đích nào: {st['doc_khong_co_muc_dich']:,}")
    if st["bo_qua_dang_chay"]:
        print(f"   bỏ qua vì worker vừa sửa : {st['bo_qua_dang_chay']:,}"
              "   ← chạy lại lệnh này là nhặt nốt")
    print(f"   thời gian: {(time.perf_counter() - t0) / 60:.1f} phút")
    if dry_run:
        print("\n  DRY-RUN: chưa đổi 1 doc nào. Bỏ --dry-run để chạy thật.")


async def main() -> None:
    p = argparse.ArgumentParser(
        description="Phase 1 — backfill Mã MĐSD vào extractions (không GPU).")
    p.add_argument("--limit", type=int, default=None, help="Chỉ xử N hồ sơ (chạy thử).")
    p.add_argument("--batch-id", default=None, help="Giới hạn 1 lô.")
    p.add_argument("--dry-run", action="store_true", help="Chỉ tính + đếm, KHÔNG ghi.")
    p.add_argument("--show", type=int, default=0, help="In chi tiết N hồ sơ đầu để kiểm mắt.")
    p.add_argument("--lam-lai", action="store_true",
                   help="Bỏ qua lọc theo version — tính lại TẤT CẢ (dùng khi sửa luật).")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}  ·  ALGO_VERSION={ALGO_VERSION}"
          + ("  ·  LÀM LẠI TẤT CẢ" if args.lam_lai else ""))
    await run(args.limit, args.batch_id, args.dry_run, args.show, args.lam_lai)


if __name__ == "__main__":
    asyncio.run(main())
