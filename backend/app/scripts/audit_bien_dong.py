"""Phase 0.2 — ĐO biến động trên toàn bộ kho, KHÔNG GPU / KHÔNG MinIO.

Chỉ đọc `gcn.extractions` (dữ liệu VLM đã bóc sẵn) → trả lời câu hỏi chặn của
Phase 2 "Chủ cuối": regex thuần có đủ không, hay phải thêm nhánh VLM text-only.

Đo gì:
  - phân bố số biến động / entry Đăng ký; bao nhiêu % giấy có biến động
  - PHÂN LOẠI mỗi biến động: chuyển chủ / không đổi chủ / KHÔNG RÕ
    (con số "KHÔNG RÕ" là quan trọng nhất — nó chính là khối lượng phải xử lý
     bằng VLM text-only hoặc phải bổ sung từ khóa)
  - `Thời gian` parse ra ngày được bao nhiêu %, theo định dạng nào
  - biến động chuyển chủ có bắt được CCCD/CMND không, có bao nhiêu chủ
  - CHỦ CUỐI sẽ lấy từ đâu: biến động hay giấy gốc
  - in mẫu text thật (dài nhất + chưa phân loại được) làm đầu vào thiết kế regex

KHÔNG ghi gì vào DB. Cursor streaming + projection hẹp — KHÔNG `to_list()`
(pattern cũ `backfill_chu_su_dung_norm.py:23` nạp cả collection sẽ OOM ở 600k).

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.audit_bien_dong
    docker compose exec api python -m app.scripts.audit_bien_dong --limit 20000
    docker compose exec api python -m app.scripts.audit_bien_dong --csv /tmp/bd.csv
"""

import argparse
import asyncio
import csv
import random
import re
import statistics
import unicodedata
from collections import Counter

from app import config
from app.db import gcns
from app.vn_text import strip_diacritics

# ── Từ khóa phân loại (so khớp trên text ĐÃ BỎ DẤU + lower) ──────────────────
# Danh sách PHỦ ĐỊNH phải kiểm TRƯỚC (xem PLAN_CHU_CUOI_MDSDD.md §Phase 2 bước 1):
# "thế chấp" có tên ngân hàng + đầy số hiệu — regex ngây thơ bóc nhầm ngân hàng
# thành chủ mới. Đây là bẫy lớn nhất của bài toán.
KHONG_DOI_CHU = [
    "the chap", "xoa the chap", "xoa dang ky the chap", "giai chap",
    "dinh chinh", "cap doi", "cap lai", "dang ky lai",
    "thay doi dien tich", "chuyen muc dich su dung", "thay doi muc dich",
    "gop von", "xoa gop von", "ke bien", "thay doi thong tin",
]
CHUYEN_CHU = [
    "tang cho", "cho tang", "chuyen nhuong", "nhan chuyen nhuong",
    "thua ke", "nhan thua ke", "chuyen quyen", "chuyen chu",
    "mua ban", "trao doi", "gop chung quyen",
]

# ── Định dạng Thời gian ─────────────────────────────────────────────────────
DATE_PATTERNS = [
    ("dd/mm/yyyy", re.compile(r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}\b")),
    ("ngay d thang m nam y", re.compile(r"ngay\s+\d{1,2}\s+thang\s+\d{1,2}\s+nam\s+\d{4}")),
    ("mm/yyyy", re.compile(r"\b\d{1,2}[/\-.]\d{4}\b")),
    ("yyyy", re.compile(r"\b(19|20)\d{2}\b")),
]

# CCCD/CMND: BẮT BUỘC có từ khóa dẫn ngay trước + đúng 9 hoặc 12 chữ số liền.
# Không nới lỏng — text biến động đầy số hồ sơ dạng "9186/2004/QĐCS 20557/2004"
# sẽ lọt nếu chỉ bắt "dãy số dài".
RE_CCCD = re.compile(
    r"(?:cccd|cmnd|cmt|can cuoc(?: cong dan)?|chung minh(?: nhan dan| thu)?)"
    r"[^0-9]{0,24}(\d{12}|\d{9})(?!\d)"
)
RE_SO_TRAN = re.compile(r"(?<!\d)(\d{12}|\d{9})(?!\d)")
RE_NGUOI = re.compile(r"\b(?:ong|ba)\b")
RE_VA_NGUOI = re.compile(r"\bva\s+(?:ong|ba)\b")
RE_HKTT = re.compile(r"\b(?:hktt|ho khau thuong tru|thuong tru|dia chi)\b")


def _norm(s) -> str:
    """Text so khớp: bỏ dấu + lower + gộp khoảng trắng (kể cả xuống dòng)."""
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    return re.sub(r"\s+", " ", strip_diacritics(s)).strip()


def classify(noi_dung: str) -> tuple[str, str]:
    """→ ("chuyen_chu"|"khong_doi_chu"|"khong_ro", từ khóa khớp).

    PHỦ ĐỊNH TRƯỚC — một biến động vừa có "chuyển nhượng" vừa có "thế chấp"
    (vd "xóa thế chấp do chuyển nhượng") phải rơi về KHÔNG đổi chủ, không được
    coi là chuyển chủ.
    """
    t = _norm(noi_dung)
    if not t:
        return "khong_ro", ""
    for kw in KHONG_DOI_CHU:
        if kw in t:
            return "khong_doi_chu", kw
    for kw in CHUYEN_CHU:
        if kw in t:
            return "chuyen_chu", kw
    return "khong_ro", ""


def parse_thoi_gian(tg) -> str | None:
    """→ tên định dạng khớp, hoặc None nếu không parse được."""
    t = _norm(tg)
    if not t:
        return None
    for name, rx in DATE_PATTERNS:
        if rx.search(t):
            return name
    return None


class Reservoir:
    """Lấy mẫu ngẫu nhiên K phần tử từ luồng không biết trước độ dài (reservoir
    sampling) — không giữ toàn bộ trong RAM, và không thiên vị về đầu luồng như
    cách "lấy K cái đầu tiên"."""

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


def _pct(xs: list, p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return float(s[i])


def _entries(doc: dict):
    """Duyệt các entry `Đăng ký` của 1 doc (bỏ record lỗi/tombstone)."""
    for rec in doc.get("extractions") or []:
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        if not isinstance(res, dict):
            continue
        for e in res.get("Đăng ký") or []:
            if isinstance(e, dict):
                yield e


async def run(limit: int | None, batch_id: str | None, csv_path: str | None,
              n_sample: int) -> None:
    q: dict = {"extractions": {"$exists": True, "$ne": []}}
    if batch_id:
        q["batch_id"] = batch_id

    docs = n_entries = 0
    entries_co_bd = 0
    bd_per_entry: list[int] = []
    loai = Counter()          # chuyen_chu / khong_doi_chu / khong_ro
    kw_hit = Counter()        # từ khóa nào khớp bao nhiêu lần
    tg_fmt = Counter()        # định dạng Thời gian
    nguon_chu_cuoi = Counter()  # bien_dong / giay_goc

    cc_co_cccd = cc_khong_cccd = 0
    cc_co_hktt = 0
    so_chu_trong_su_kien: list[int] = []
    so_tran_nhung_khong_cccd = 0

    dai_nhat: list[tuple[int, str]] = []   # (len, text) — giữ top N
    mau_khong_ro = Reservoir(n_sample)
    mau_chuyen_chu = Reservoir(n_sample)
    rows_csv: list[dict] = []

    cur = gcns().find(q, {"extractions": 1}).batch_size(500)
    if limit:
        cur = cur.limit(limit)

    async for doc in cur:
        docs += 1
        if docs % 20000 == 0:
            print(f"  ... đã quét {docs:,} hồ sơ")

        for e in _entries(doc):
            n_entries += 1
            bds = [b for b in (e.get("Biến động") or []) if isinstance(b, dict)]
            bd_per_entry.append(len(bds))
            if bds:
                entries_co_bd += 1

            co_chuyen_chu = False
            for b in bds:
                nd = b.get("Nội dung biến động") or ""
                kind, kw = classify(nd)
                loai[kind] += 1
                if kw:
                    kw_hit[kw] += 1

                fmt = parse_thoi_gian(b.get("Thời gian"))
                tg_fmt[fmt or "(không parse được)"] += 1

                t = _norm(nd)
                if len(t) > 0:
                    dai_nhat.append((len(t), nd))
                    if len(dai_nhat) > 400:          # cắt bớt định kỳ, giữ RAM phẳng
                        dai_nhat.sort(key=lambda x: -x[0])
                        del dai_nhat[200:]

                if kind == "chuyen_chu":
                    co_chuyen_chu = True
                    mau_chuyen_chu.offer(nd)
                    cccds = RE_CCCD.findall(t)
                    if cccds:
                        cc_co_cccd += 1
                    else:
                        cc_khong_cccd += 1
                        if RE_SO_TRAN.search(t):
                            so_tran_nhung_khong_cccd += 1
                    if RE_HKTT.search(t):
                        cc_co_hktt += 1
                    # ước lượng số chủ: số lần "ông/bà" (tối thiểu 1 nếu có tên)
                    n_nguoi = len(RE_NGUOI.findall(t))
                    so_chu_trong_su_kien.append(max(n_nguoi, len(cccds), 1))
                    if csv_path:
                        rows_csv.append({
                            "gcn_id": doc.get("_id", ""), "tu_khoa": kw,
                            "thoi_gian": b.get("Thời gian") or "",
                            "so_cccd_bat_duoc": len(cccds),
                            "so_ong_ba": n_nguoi,
                            "co_hktt": int(bool(RE_HKTT.search(t))),
                            "noi_dung": nd,
                        })
                elif kind == "khong_ro" and t:
                    mau_khong_ro.offer(nd)

            nguon_chu_cuoi["bien_dong" if co_chuyen_chu else "giay_goc"] += 1

    _report(docs, n_entries, entries_co_bd, bd_per_entry, loai, kw_hit, tg_fmt,
            nguon_chu_cuoi, cc_co_cccd, cc_khong_cccd, cc_co_hktt,
            so_tran_nhung_khong_cccd, so_chu_trong_su_kien, dai_nhat,
            mau_khong_ro, mau_chuyen_chu)

    if csv_path and rows_csv:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_csv[0].keys()))
            w.writeheader()
            w.writerows(rows_csv)
        print(f"\nĐã ghi {len(rows_csv):,} biến động chuyển chủ ra {csv_path}")


def _bar(n: int, total: int, width: int = 34) -> str:
    return "█" * (round(n / total * width) if total else 0)


def _line(label: str, n: int, total: int) -> str:
    pct = (n / total * 100) if total else 0.0
    return f"   {label:<28} {n:>9,} ({pct:5.1f}%) {_bar(n, total)}"


def _report(docs, n_entries, entries_co_bd, bd_per_entry, loai, kw_hit, tg_fmt,
            nguon, cc_cccd, cc_khong, cc_hktt, so_tran, so_chu, dai_nhat,
            mau_khong_ro, mau_chuyen_chu) -> None:
    tong_bd = sum(loai.values())
    print("\n" + "=" * 78)
    print(f" AUDIT BIẾN ĐỘNG — {docs:,} hồ sơ · {n_entries:,} entry Đăng ký · "
          f"{tong_bd:,} biến động")
    print("=" * 78)

    print("\n QUY MÔ")
    print(_line("entry CÓ biến động", entries_co_bd, n_entries))
    if bd_per_entry:
        print(f"   biến động/entry            : avg {statistics.mean(bd_per_entry):.2f}  "
              f"p50 {_pct(bd_per_entry, 50):.0f}  p90 {_pct(bd_per_entry, 90):.0f}  "
              f"max {max(bd_per_entry)}")

    print("\n PHÂN LOẠI BIẾN ĐỘNG  (phủ định kiểm trước — xem docstring)")
    for k in ("chuyen_chu", "khong_doi_chu", "khong_ro"):
        print(_line(k, loai.get(k, 0), tong_bd))
    kr = loai.get("khong_ro", 0)
    if tong_bd:
        print(f"\n   → 'khong_ro' = {kr / tong_bd * 100:.1f}% là KHỐI LƯỢNG PHẢI XỬ LÝ THÊM")
        print("     (bổ sung từ khóa nếu có mẫu lặp lại, hoặc đẩy sang VLM text-only)")

    print("\n TỪ KHÓA KHỚP (top 20)")
    for kw, n in kw_hit.most_common(20):
        tag = "chuyển chủ" if kw in CHUYEN_CHU else "KHÔNG đổi"
        print(f"   {kw:<26} {n:>9,}  [{tag}]")

    print("\n THỜI GIAN — parse ra ngày được không")
    for fmt, n in tg_fmt.most_common():
        print(_line(fmt, n, tong_bd))

    n_cc = cc_cccd + cc_khong
    print("\n BIẾN ĐỘNG CHUYỂN CHỦ — bóc được gì bằng regex")
    if n_cc:
        print(_line("bắt được CCCD/CMND", cc_cccd, n_cc))
        print(_line("KHÔNG bắt được CCCD", cc_khong, n_cc))
        print(_line("  trong đó: có số 9/12 chữ số trần", so_tran, n_cc))
        print(_line("có HKTT/địa chỉ", cc_hktt, n_cc))
        if so_chu:
            nhieu = sum(1 for x in so_chu if x >= 2)
            print(f"   số chủ/sự kiện             : avg {statistics.mean(so_chu):.2f}  "
                  f"max {max(so_chu)}  ·  ≥2 chủ: {nhieu:,} ({nhieu / len(so_chu) * 100:.1f}%)")
        print("\n   → 'có số trần nhưng không bắt được CCCD' = ca regex hụt vì thiếu")
        print("     từ khóa dẫn. Nới regex cho nhóm này TRƯỚC khi nghĩ đến VLM.")

    print("\n CHỦ CUỐI SẼ LẤY TỪ ĐÂU")
    tong_n = sum(nguon.values())
    for k in ("bien_dong", "giay_goc"):
        print(_line(k, nguon.get(k, 0), tong_n))

    print("\n" + "-" * 78)
    print(f" MẪU: {len(mau_khong_ro.items)} biến động CHƯA PHÂN LOẠI ĐƯỢC "
          f"(đầu vào bổ sung từ khóa)")
    print("-" * 78)
    for s in mau_khong_ro.items:
        print(f"   · {_clip(s)}")

    print("\n" + "-" * 78)
    print(f" MẪU: {len(mau_chuyen_chu.items)} biến động CHUYỂN CHỦ (đầu vào thiết kế regex bóc chủ)")
    print("-" * 78)
    for s in mau_chuyen_chu.items:
        print(f"   · {_clip(s)}")

    dai_nhat.sort(key=lambda x: -x[0])
    print("\n" + "-" * 78)
    print(" MẪU: 15 nội dung biến động DÀI NHẤT (ca khó nhất)")
    print("-" * 78)
    for _, s in dai_nhat[:15]:
        print(f"   · {_clip(s, 600)}")


def _clip(s: str, n: int = 300) -> str:
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip()
    return s if len(s) <= n else s[:n] + " …"


async def main() -> None:
    p = argparse.ArgumentParser(
        description="Phase 0.2 — đo biến động để thiết kế 'chủ cuối' (không GPU, không ghi DB).")
    p.add_argument("--limit", type=int, default=None, help="Chỉ quét N hồ sơ đầu (chạy thử).")
    p.add_argument("--batch-id", default=None, help="Giới hạn trong 1 lô.")
    p.add_argument("--csv", default=None,
                   help="Xuất mọi biến động CHUYỂN CHỦ ra CSV (đầu vào soạn regex).")
    p.add_argument("--sample", type=int, default=25, help="Số mẫu in ra mỗi nhóm.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    print("Chế độ CHỈ ĐỌC — không ghi gì vào DB.")
    await run(args.limit, args.batch_id, args.csv, args.sample)


if __name__ == "__main__":
    asyncio.run(main())
