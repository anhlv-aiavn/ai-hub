"""Phase 2 — Backfill trường `chu_cuoi` cho kho gcn (600k).

Ghi doc['chu_cuoi'] = danh sách (1 phần tử / 1 entry Đăng ký) + doc['chu_cuoi_version'].
KHÔNG đụng `extractions` raw (giữ bất biến như review.overrides).

Luồng mỗi doc:
  regex (chu_cuoi_for_entry) TRƯỚC → ca `canh_bao` (có chuyển nhượng nhưng chưa rõ
  chủ) mới gọi LLM text-only (refine_chu_cuoi). LLM có trần đồng thời (semaphore)
  để không đập vỡ vLLM đang phục vụ pipeline chính.

An toàn quy mô: cursor streaming `.batch_size(500)` + `bulk_write` theo lô — KHÔNG
`to_list` (pattern cũ backfill_chu_su_dung_norm.py nạp cả collection sẽ OOM ở 600k).

Resumable + idempotent: chỉ xử doc có `chu_cuoi_version` cũ hơn hiện tại; chạy lại
cho cùng kết quả. Regex tất định; LLM temperature=0.

Chạy TRONG CONTAINER:
    # 1) soi thử, KHÔNG ghi gì:
    docker compose exec api python -m app.scripts.backfill_chu_cuoi --limit 200 --dry-run
    # 2) chạy thật 1 batch nhỏ để kiểm trong Mongo:
    docker compose exec api python -m app.scripts.backfill_chu_cuoi --limit 500
    # 3) ưng thì chạy toàn bộ (resume phần chưa làm):
    docker compose exec api python -m app.scripts.backfill_chu_cuoi
    # regex-only (nhanh, bỏ LLM):
    docker compose exec api python -m app.scripts.backfill_chu_cuoi --limit 500 --no-llm
"""

import argparse
import asyncio
import re
import time
from collections import Counter

from pymongo import UpdateOne

from app import config
from app.db import gcns
from src.extentions.multimodal.chu_cuoi import ALGO_VERSION, chu_cuoi_for_entry
from src.extentions.multimodal.chu_cuoi_llm import refine_chu_cuoi

BULK = 500


def _iter_entries(doc: dict):
    """Yield (rec_index, entry_index, entry) theo đúng thứ tự flatten duyệt."""
    for ri, rec in enumerate(doc.get("extractions") or []):
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        if not isinstance(res, dict):
            continue
        for ei, e in enumerate(res.get("Đăng ký") or []):
            if isinstance(e, dict):
                yield ri, ei, e


def _sph_of(entry: dict) -> str:
    gcn = entry.get("Giấy chứng nhận") if isinstance(entry, dict) else None
    return str(gcn.get("Số phát hành", "")) if isinstance(gcn, dict) else ""


async def _compute_doc(doc: dict, sem, use_llm: bool) -> list[dict]:
    """Tính danh sách chu_cuoi cho 1 doc (regex + LLM cho ca canh_bao)."""
    out: list[dict] = []
    for ri, ei, entry in _iter_entries(doc):
        cc = chu_cuoi_for_entry(entry)
        if use_llm and cc.get("canh_bao"):
            async with sem:
                cc = await refine_chu_cuoi(cc, entry)
        # so_phat_hanh = khóa nghiệp vụ để map chu_cuoi ↔ GCN (Phase 4) và để soi
        # trên UI (ô tìm kiếm khớp Số phát hành).
        out.append({"rec_index": ri, "entry_index": ei, "so_phat_hanh": _sph_of(entry), **cc})
    return out


def _clip(s, n=150):
    return re.sub(r"\s+", " ", str(s)).strip()[:n]


PROGRESS_SEC = 10.0  # nhịp in tiến trình (theo THỜI GIAN, hợp cả pha regex nhanh
                     # lẫn pha LLM chậm — không phụ thuộc số doc như trước)


def _hms(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600:d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _progress(n_doc: int, total: int, t0: float, st: Counter) -> None:
    el = time.perf_counter() - t0
    rate = n_doc / el if el > 0 else 0.0
    pct = (n_doc / total * 100) if total else 0.0
    eta = ((total - n_doc) / rate) if (rate > 0 and total) else 0
    tail = f" · LLM cứu {st['llm_recover']:,}" if st["llm_recover"] else ""
    print(f"  [{pct:5.1f}%] {n_doc:,}/{total:,} · {rate:.1f} doc/s{tail}"
          f" · đã {_hms(el)} · còn ~{_hms(eta)}", flush=True)


def _tally(ccs: list[dict], st: Counter) -> None:
    for cc in ccs:
        st["entry"] += 1
        st[f"nguon_{cc['nguon']}"] += 1
        st[f"conf_{cc['confidence']}"] += 1
        if cc.get("canh_bao"):
            st["canh_bao"] += 1
        if cc.get("method") == "llm":
            st["llm_recover"] += 1


async def run(limit, batch_id, dry_run, use_llm, vlm, show, only_canh_bao) -> None:
    if only_canh_bao:
        # PHA LLM riêng: chỉ đụng ca 'có chuyển nhượng chưa rõ chủ' mà CHƯA thử LLM
        # (đánh dấu chu_cuoi_llm_done để chạy lại không gọi LLM lặp cho ca vẫn rỗng).
        q = {"chu_cuoi.canh_bao": "co_chuyen_nhuong_chua_ro_chu",
             "chu_cuoi_llm_done": {"$ne": True}}
        use_llm = True
    else:
        q = {"extractions": {"$exists": True, "$ne": []},
             "$or": [{"chu_cuoi_version": {"$exists": False}},
                     {"chu_cuoi_version": {"$lt": ALGO_VERSION}}]}
    if batch_id:
        q["batch_id"] = batch_id

    sem = asyncio.Semaphore(vlm)
    st = Counter()
    ops: list[UpdateOne] = []
    n_doc = n_written = 0

    # Đếm trước để có % + ETA (cần cho pha LLM chạy hàng giờ — không thì như treo).
    print("  đang đếm tổng số hồ sơ cần xử lý…", flush=True)
    total = await gcns().count_documents(q)
    if limit:
        total = min(total, limit)
    print(f"  tổng: {total:,} hồ sơ\n", flush=True)
    t0 = last = time.perf_counter()

    cur = gcns().find(q, {"extractions": 1, "filename": 1}).batch_size(BULK)
    if limit:
        cur = cur.limit(limit)

    # Xử lý theo LÔ SONG SONG: nếu await từng doc một thì semaphore --vlm vô nghĩa
    # (chỉ 1 call LLM bay cùng lúc → nâng --vlm không nhanh hơn). Giữ `chunk` doc
    # cùng chạy để lấp đủ trần đồng thời.
    chunk = max(1, vlm) * 2
    buf: list[dict] = []

    async def _flush() -> None:
        nonlocal n_doc, n_written, last
        if not buf:
            return
        results = await asyncio.gather(*(_compute_doc(d, sem, use_llm) for d in buf))
        for d, ccs in zip(buf, results):
            n_doc += 1
            _tally(ccs, st)
            if show and n_doc <= show:
                _print_doc(d, ccs)
            if not dry_run:
                fields = {"chu_cuoi": ccs, "chu_cuoi_version": ALGO_VERSION}
                if only_canh_bao:
                    fields["chu_cuoi_llm_done"] = True  # đã thử LLM, khỏi gọi lại
                ops.append(UpdateOne({"_id": d["_id"]}, {"$set": fields}))
        buf.clear()
        if ops and not dry_run and len(ops) >= BULK:
            res = await gcns().bulk_write(ops, ordered=False)
            n_written += res.modified_count
            ops.clear()
        now = time.perf_counter()
        if now - last >= PROGRESS_SEC:
            _progress(n_doc, total, t0, st)
            last = now

    async for doc in cur:
        buf.append(doc)
        if len(buf) >= chunk:
            await _flush()
    await _flush()

    if ops and not dry_run:
        res = await gcns().bulk_write(ops, ordered=False)
        n_written += res.modified_count

    _progress(n_doc, total, t0, st)
    _report(n_doc, n_written, st, dry_run)


def _print_doc(doc, ccs) -> None:
    # In tên tệp để tra nhanh; Số phát hành để DÁN VÀO Ô TÌM KIẾM trên UI đối soát.
    print(f"\n  ── {doc.get('filename') or doc.get('_id')} ──")
    for cc in ccs:
        names = " + ".join(c["Tên chủ"] for c in cc["chu"]) or "(chưa rõ)"
        flag = "  ⚠ CẦN XÁC MINH" if cc.get("canh_bao") else ""
        via = f" [{cc.get('method', 'regex')}]"
        sph = cc.get("so_phat_hanh") or "—"
        print(f"     SPH {sph:<14} {cc['nguon']:9} {cc['confidence']:4}{via}  {names}{flag}")


def _report(n_doc, n_written, st, dry_run) -> None:
    e = max(st["entry"], 1)
    print("\n" + "=" * 68)
    print(f" BACKFILL chu_cuoi  ·  {n_doc:,} doc  ·  {st['entry']:,} entry"
          + ("  (DRY-RUN, chưa ghi)" if dry_run else f"  ·  đã ghi {n_written:,} doc"))
    print("=" * 68)
    print(f"  nguồn     : biến động {st['nguon_bien_dong']:,} ({st['nguon_bien_dong']/e*100:.1f}%)"
          f"  ·  giấy gốc {st['nguon_giay_goc']:,}")
    print(f"  độ tin    : cao {st['conf_cao']:,} ({st['conf_cao']/e*100:.1f}%)"
          f"  ·  thấp {st['conf_thap']:,}")
    print(f"  LLM cứu   : {st['llm_recover']:,} entry")
    print(f"  ⚠ cảnh báo: {st['canh_bao']:,} ({st['canh_bao']/e*100:.1f}%) — có chuyển nhượng"
          f" nhưng CHƯA RÕ CHỦ → hậu kiểm/xác minh")
    if dry_run:
        print("\n  DRY-RUN: chưa ghi 1 byte nào. Bỏ --dry-run để ghi thật.")


async def main() -> None:
    p = argparse.ArgumentParser(description="Phase 2 — backfill chu_cuoi (regex + LLM).")
    p.add_argument("--limit", type=int, default=None, help="Chỉ xử N doc (chạy thử).")
    p.add_argument("--batch-id", default=None, help="Giới hạn 1 lô.")
    p.add_argument("--dry-run", action="store_true", help="Tính + thống kê, KHÔNG ghi DB.")
    p.add_argument("--no-llm", action="store_true", help="Bỏ LLM (regex-only, nhanh).")
    p.add_argument("--vlm", type=int, default=6, help="Trần call LLM đồng thời (mặc định 6).")
    p.add_argument("--show", type=int, default=0, help="In chi tiết N doc đầu để soi.")
    p.add_argument("--only-canh-bao", action="store_true",
                   help="PHA LLM riêng: chỉ chạy ca canh_bao chưa thử LLM (ép LLM bật).")
    args = p.parse_args()

    use_llm = (not args.no_llm) or args.only_canh_bao
    mode = "CHỈ CA CẢNH BÁO" if args.only_canh_bao else "toàn kho"
    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}  ·  algo v{ALGO_VERSION}  ·  {mode}"
          f"  ·  LLM {'BẬT' if use_llm else 'TẮT'} (≤{args.vlm} đồng thời)")
    await run(args.limit, args.batch_id, args.dry_run, use_llm, args.vlm, args.show,
              args.only_canh_bao)


if __name__ == "__main__":
    asyncio.run(main())
