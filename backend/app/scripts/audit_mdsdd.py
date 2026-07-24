"""Phase 0.3 — ĐO mục đích sử dụng trên toàn kho. KHÔNG GPU / KHÔNG MinIO.

Chỉ đọc `gcn.extractions` (VLM đã bóc sẵn) → trả lời 2 câu hỏi chặn Phase 1:

  1. ONT/ODT — CÂU HỎI CHÍNH. "Đất ở" chiếm phần áp đảo và là chỗ DUY NHẤT chữ
     trên giấy không đủ để quyết mã: phải suy từ địa chỉ thửa. Script đo đúng
     việc đó: bao nhiêu bản ghi đất ở, quyết được bằng đường nào, và còn lại
     bao nhiêu KHÔNG QUYẾT NỔI (= khối lượng rà tay thật sự).
  2. Danh mục còn lại — liệt kê distinct `Loại mục đích` + tần suất + ký hiệu
     bắt được, để Phase 1 dựng bảng 80 mã TỪ SỐ LIỆU THẬT, không đoán.

Luật ONT/ODT dùng chung `src.extentions.multimodal.mdsdd` — KHÔNG chép lại ở
đây, để audit không bao giờ báo khác thứ production sẽ chạy.

KHÔNG ghi gì vào DB. Cursor streaming + projection hẹp — KHÔNG `to_list()`
(pattern cũ `backfill_chu_su_dung_norm.py:23` nạp cả collection sẽ OOM ở 600k).

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.audit_mdsdd
    docker compose exec api python -m app.scripts.audit_mdsdd --limit 20000
    docker compose exec api python -m app.scripts.audit_mdsdd --csv /tmp/mdsdd.csv
"""

import argparse
import asyncio
import csv
import random
import re
import unicodedata
from collections import Counter

from app import config
from app.db import gcns
from src.extentions.multimodal.mdsdd import (
    chuan_hoa,
    don_vi_hanh_chinh,
    ky_hieu_trong_text,
    la_dat_o,
    map_dat_o,
)


class Reservoir:
    """Lấy mẫu ngẫu nhiên K phần tử từ luồng không biết trước độ dài — không giữ
    toàn bộ trong RAM, và không thiên vị về đầu luồng như "lấy K cái đầu tiên"."""

    def __init__(self, k: int, seed: int = 42):
        self.k, self.n, self.items = k, 0, []
        self._rnd = random.Random(seed)

    def offer(self, item) -> None:
        self.n += 1
        if len(self.items) < self.k:
            self.items.append(item)
            return
        j = self._rnd.randrange(self.n)
        if j < self.k:
            self.items[j] = item


def _entries(doc: dict):
    """Duyệt entry `Đăng ký` của 1 doc (bỏ record lỗi/tombstone)."""
    for rec in doc.get("extractions") or []:
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        if not isinstance(res, dict):
            continue
        for e in res.get("Đăng ký") or []:
            if isinstance(e, dict):
                yield e


def _muc_dich(entry: dict):
    """Duyệt (text mục đích, địa chỉ thửa) của 1 entry Đăng ký."""
    for thua in entry.get("Thửa đất") or []:
        if not isinstance(thua, dict):
            continue
        dia_chi = thua.get("Địa chỉ") or ""
        for md in thua.get("Mục đích sử dụng") or []:
            if isinstance(md, dict):
                yield (md.get("Loại mục đích") or ""), dia_chi


async def run(limit: int | None, batch_id: str | None, csv_path: str | None,
              n_sample: int, top: int) -> None:
    q: dict = {"extractions": {"$exists": True, "$ne": []}}
    if batch_id:
        q["batch_id"] = batch_id

    docs = n_thua = n_md = 0
    n_rong = 0
    phan_nhom = Counter()        # dat_o / khac
    cach_quyet = Counter()       # đường nào quyết ra ONT/ODT
    ra_ma = Counter()            # ONT / ODT
    dvhc = Counter()             # phuong / thi_tran / xa
    ly_do_ambiguous = Counter()  # địa chỉ rỗng / có địa chỉ nhưng không nêu cấp xã

    distinct = Counter()         # chuỗi đã chuẩn hóa → tần suất
    dai_dien: dict[str, str] = {}   # chuỗi chuẩn hóa → 1 bản gốc để in cho người đọc
    ky_hieu_khac = Counter()     # ký hiệu bắt được ở nhóm KHÔNG phải đất ở
    khac_distinct = Counter()

    mau_ambiguous = Reservoir(n_sample)
    mau_dia_chi_suy = Reservoir(n_sample)
    rows_csv: list[dict] = []

    cur = gcns().find(
        q, {"extractions.result.Đăng ký.Thửa đất": 1},
    ).batch_size(500)
    if limit:
        cur = cur.limit(limit)

    async for doc in cur:
        docs += 1
        if docs % 20000 == 0:
            print(f"  ... đã quét {docs:,} hồ sơ", flush=True)

        for e in _entries(doc):
            for thua in e.get("Thửa đất") or []:
                if isinstance(thua, dict):
                    n_thua += 1
            for text, dia_chi in _muc_dich(e):
                n_md += 1
                t = chuan_hoa(text)
                if not t:
                    n_rong += 1
                    continue
                distinct[t] += 1
                dai_dien.setdefault(t, text)

                if not la_dat_o(text):
                    phan_nhom["khac"] += 1
                    khac_distinct[t] += 1
                    kh = ky_hieu_trong_text(text)
                    if kh:
                        ky_hieu_khac[kh[0]] += 1
                    continue

                phan_nhom["dat_o"] += 1
                r = map_dat_o(text, dia_chi)
                if r["ambiguous"]:
                    cach_quyet["KHÔNG QUYẾT ĐƯỢC"] += 1
                    ly_do_ambiguous["địa chỉ thửa RỖNG" if not chuan_hoa(dia_chi)
                                    else "có địa chỉ, không nêu phường/thị trấn/xã"] += 1
                    mau_ambiguous.offer((text, dia_chi))
                else:
                    cach_quyet[r["method"]] += 1
                    ra_ma[r["ky_hieu"]] += 1
                    if r["method"] == "dia_chi":
                        dvhc[r.get("dvhc") or "?"] += 1
                        mau_dia_chi_suy.offer((text, dia_chi, r["ky_hieu"]))

    if csv_path:
        tong = sum(distinct.values())
        luy_ke = 0
        for t, n in distinct.most_common():
            luy_ke += n
            goc = dai_dien.get(t, t)
            kh = ky_hieu_trong_text(goc)
            rows_csv.append({
                "gia_tri_chuan_hoa": t, "gia_tri_goc": goc, "so_lan": n,
                "ty_le_%": round(n / tong * 100, 4) if tong else 0,
                "luy_ke_%": round(luy_ke / tong * 100, 4) if tong else 0,
                "ky_hieu": (kh[0] if kh else ""),
                "la_dat_o": int(la_dat_o(goc)),
            })

    _report(docs, n_thua, n_md, n_rong, phan_nhom, cach_quyet, ra_ma, dvhc,
            ly_do_ambiguous, distinct, dai_dien, khac_distinct, ky_hieu_khac,
            mau_ambiguous, mau_dia_chi_suy, top)

    if csv_path and rows_csv:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_csv[0].keys()))
            w.writeheader()
            w.writerows(rows_csv)
        print(f"\nĐã ghi {len(rows_csv):,} chuỗi distinct ra {csv_path}")
        print("  → đây là ĐẦU VÀO dựng bảng 80 mã ở Phase 1 (không đoán).")


def _bar(n: int, total: int, width: int = 30) -> str:
    return "█" * (round(n / total * width) if total else 0)


def _line(label: str, n: int, total: int) -> str:
    pct = (n / total * 100) if total else 0.0
    return f"   {label:<38} {n:>9,} ({pct:5.1f}%) {_bar(n, total)}"


def _clip(s: str, n: int = 120) -> str:
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(s))).strip()
    return s if len(s) <= n else s[:n] + " …"


def _report(docs, n_thua, n_md, n_rong, phan_nhom, cach_quyet, ra_ma, dvhc,
            ly_do, distinct, dai_dien, khac_distinct, ky_hieu_khac,
            mau_ambiguous, mau_dia_chi_suy, top) -> None:
    print("\n" + "=" * 80)
    print(f" AUDIT MỤC ĐÍCH SỬ DỤNG — {docs:,} hồ sơ · {n_thua:,} thửa · "
          f"{n_md:,} bản ghi mục đích")
    print("=" * 80)

    print("\n QUY MÔ")
    print(_line("Loại mục đích RỖNG (không bóc được)", n_rong, n_md))
    co = n_md - n_rong
    print(_line("có giá trị", co, n_md))
    print(f"   chuỗi DISTINCT (đã chuẩn hóa)          : {len(distinct):,}")

    print("\n ĐẤT Ở vs MỤC ĐÍCH KHÁC  ← quyết định phạm vi Phase 1")
    for k, nhan in (("dat_o", "ĐẤT Ở (cần luật ONT/ODT)"), ("khac", "mục đích khác")):
        print(_line(nhan, phan_nhom.get(k, 0), co))

    n_dat_o = phan_nhom.get("dat_o", 0)
    print("\n ĐẤT Ở — QUYẾT ĐƯỢC MÃ BẰNG ĐƯỜNG NÀO")
    print("   (ký hiệu/tên = chữ trên giấy, chắc chắn · địa chỉ = SUY DẪN theo luật đã chốt)")
    nhan_pp = {
        "ky_hieu_ngoac": "ký hiệu trong ngoặc  (ONT)/(ODT)",
        "ky_hieu_token": "ký hiệu đứng riêng   ONT/ODT",
        "ten_exact": "tên đầy đủ           nông thôn/đô thị",
        "dia_chi": "SUY từ địa chỉ thửa",
        "KHÔNG QUYẾT ĐƯỢC": "KHÔNG QUYẾT ĐƯỢC → rà tay",
    }
    for k, nhan in nhan_pp.items():
        print(_line(nhan, cach_quyet.get(k, 0), n_dat_o))

    quyet_duoc = n_dat_o - cach_quyet.get("KHÔNG QUYẾT ĐƯỢC", 0)
    if n_dat_o:
        print(f"\n   → PHỦ ĐƯỢC {quyet_duoc:,}/{n_dat_o:,} bản ghi đất ở "
              f"= {quyet_duoc / n_dat_o * 100:.1f}%")
        if co:
            print(f"     Trên TỔNG bản ghi mục đích: {quyet_duoc / co * 100:.1f}%")

    print("\n ĐẤT Ở — RA MÃ NÀO")
    tong_ma = sum(ra_ma.values())
    print(_line("ONT (191) — nông thôn", ra_ma.get("ONT", 0), tong_ma))
    print(_line("ODT (192) — đô thị", ra_ma.get("ODT", 0), tong_ma))

    if dvhc:
        print("\n ĐƠN VỊ HÀNH CHÍNH BẮT ĐƯỢC TỪ ĐỊA CHỈ (nhánh suy dẫn)")
        tong_dv = sum(dvhc.values())
        for k in ("phuong", "thi_tran", "xa"):
            print(_line(f"{k}  → {'ODT' if k != 'xa' else 'ONT'}", dvhc.get(k, 0), tong_dv))

    if ly_do:
        print("\n VÌ SAO KHÔNG QUYẾT ĐƯỢC (đây là khối lượng rà tay thật)")
        tong_ld = sum(ly_do.values())
        for k, n in ly_do.most_common():
            print(_line(k, n, tong_ld))

    print("\n" + "-" * 80)
    print(f" TOP {top} CHUỖI MỤC ĐÍCH PHỔ BIẾN NHẤT")
    print("-" * 80)
    tong_d = sum(distinct.values())
    luy_ke = 0
    for i, (t, n) in enumerate(distinct.most_common(top), 1):
        luy_ke += n
        print(f"  {i:>3}. {n:>8,} ({n / tong_d * 100:5.2f}%  lũy kế {luy_ke / tong_d * 100:5.1f}%)"
              f"  {_clip(dai_dien.get(t, t), 80)}")

    # Bao nhiêu chuỗi distinct là đủ phủ 90/95/99% — quyết bảng alias to cỡ nào
    print("\n ĐỘ TẬP TRUNG (cần bao nhiêu chuỗi để phủ)")
    luy_ke = 0
    moc = {90: None, 95: None, 99: None}
    for i, (_, n) in enumerate(distinct.most_common(), 1):
        luy_ke += n
        for m in moc:
            if moc[m] is None and tong_d and luy_ke / tong_d * 100 >= m:
                moc[m] = i
    for m, i in moc.items():
        print(f"   phủ {m}% cần {i if i else '—'} chuỗi distinct")

    print("\n" + "-" * 80)
    print(f" TOP {top} MỤC ĐÍCH KHÁC ĐẤT Ở (đầu vào dựng danh mục Phase 1)")
    print("-" * 80)
    tong_k = sum(khac_distinct.values()) or 1
    for i, (t, n) in enumerate(khac_distinct.most_common(top), 1):
        print(f"  {i:>3}. {n:>8,} ({n / tong_k * 100:5.2f}%)  {_clip(dai_dien.get(t, t), 80)}")
    if ky_hieu_khac:
        print("\n   Ký hiệu bắt được ở nhóm này (top 40):")
        print("   " + "  ".join(f"{k}:{n:,}" for k, n in ky_hieu_khac.most_common(40)))

    print("\n" + "-" * 80)
    print(f" MẪU {len(mau_ambiguous.items)} CA KHÔNG QUYẾT ĐƯỢC ONT/ODT")
    print(" (soi để biết có nới được luật địa chỉ không, hay buộc phải rà tay)")
    print("-" * 80)
    for text, dia_chi in mau_ambiguous.items:
        print(f"   · {_clip(text, 40):<42} | địa chỉ: {_clip(dia_chi, 90)}")

    print("\n" + "-" * 80)
    print(f" MẪU {len(mau_dia_chi_suy.items)} CA SUY TỪ ĐỊA CHỈ (kiểm tay xem luật có đúng)")
    print("-" * 80)
    for text, dia_chi, kh in mau_dia_chi_suy.items:
        print(f"   · {kh}  {_clip(text, 30):<32} | {_clip(dia_chi, 90)}")


async def main() -> None:
    p = argparse.ArgumentParser(
        description="Phase 0.3 — đo mục đích sử dụng, trọng tâm ONT/ODT (không GPU, không ghi DB).")
    p.add_argument("--limit", type=int, default=None, help="Chỉ quét N hồ sơ đầu (chạy thử).")
    p.add_argument("--batch-id", default=None, help="Giới hạn trong 1 lô.")
    p.add_argument("--csv", default=None, help="Xuất TOÀN BỘ chuỗi distinct + tần suất ra CSV.")
    p.add_argument("--sample", type=int, default=25, help="Số mẫu in ra mỗi nhóm.")
    p.add_argument("--top", type=int, default=40, help="Số dòng top in ra bảng.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    print("Chế độ CHỈ ĐỌC — không ghi gì vào DB.")
    await run(args.limit, args.batch_id, args.csv, args.sample, args.top)


if __name__ == "__main__":
    asyncio.run(main())
