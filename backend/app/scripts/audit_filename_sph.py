"""Đo TIỀM NĂNG lấy Số phát hành (SPH) TỪ TÊN TỆP — rẻ, KHÔNG VLM, read-only.

Phát hiện: chuyên viên đặt tên tệp gốc theo chính SPH có chữ sê-ri (vd
"O 276111.pdf", "AK 578667.pdf", "SốK 850214.pdf"). Với sổ bìa đỏ, VLM đọc được
phần SỐ nhưng hay RỚT chữ sê-ri (in vàng trên nền đỏ) — mà tên tệp thì có sẵn.
Nên tên tệp là NGUỒN ĐỘC LẬP để: (a) ghép lại chữ sê-ri khi số khớp, hoặc (b)
điền cả SPH khi VLM ra rỗng/rác.

KHÔNG phải mọi tên tệp đều là SPH: nhiều cái chỉ là số thứ tự scan ("774402.pdf",
"154281.pdf"). Nên CHỈ nhận tên tệp dạng "<1-2 chữ sê-ri> <dãy số>" (có chữ cái
dẫn đầu) — số thuần bị loại, tránh nhận nhầm.

Script này CHỈ ĐO để quyết chiến lược, KHÔNG ghi gì. Nếu tỉ lệ cứu được cao →
làm 1 backfill thuần chuỗi (không GPU) là xong phần lớn; xử lý ảnh nền đỏ chỉ còn
lo phần dư.

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.audit_filename_sph
    docker compose exec api python -m app.scripts.audit_filename_sph --show 40
"""

import argparse
import asyncio
import re
from collections import Counter

# Hợp lệ SPH (đồng bộ extract_gcn._VALID_SPH_*).
_VALID_DIGITS = re.compile(r"^\d{8,15}$")
_VALID_LETTER = re.compile(r"^[A-ZĐ]{1,4} ?\d{5,}$")

# Tên tệp dạng SPH-có-sê-ri: (bỏ nhãn Số/SỐ nếu dính) 1-2 chữ cái + dãy số.
_LABEL = re.compile(r"^S[ỐO]\s*(?=[A-ZĐ])")           # "SốK 850214" → "K 850214"
_FN_SPH = re.compile(r"^([A-ZĐ]{1,2})\s?(\d{4,})$")   # chữ dẫn đầu BẮT BUỘC


def _valid(s) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip().upper()
    return bool(s and (_VALID_DIGITS.fullmatch(s) or _VALID_LETTER.fullmatch(s)))


def _parse_filename(name) -> tuple | None:
    """Tên tệp → (sê-ri, số) nếu là dạng SPH-có-sê-ri, ngược lại None.
    Số thuần ("774402.pdf") → None (không đủ tin, có thể là số thứ tự scan)."""
    if not isinstance(name, str) or not name.strip():
        return None
    s = re.sub(r"\.pdf\s*$", "", name.strip(), flags=re.I).upper()
    s = _LABEL.sub("", s).strip()
    m = _FN_SPH.fullmatch(s)
    if not m:
        return None
    return m.group(1), m.group(2)


def _digits(sph) -> str:
    """Phần số của 1 SPH (bỏ chữ + khoảng trắng)."""
    if not isinstance(sph, str):
        return ""
    return re.sub(r"\D", "", sph)


def _sph_stored(records) -> list:
    out = []
    for r in records or []:
        res = r.get("result") if isinstance(r, dict) else None
        if not isinstance(res, dict):
            continue
        for e in res.get("Đăng ký") or []:
            g = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
            if isinstance(g, dict):
                out.append(g.get("Số phát hành"))
    return out


def _phan_loai(filename, stored_list) -> str:
    """Xếp 1 hồ sơ vào nhóm tiềm năng cứu-bằng-tên-tệp."""
    fn = _parse_filename(filename)
    stored_valid = [s for s in stored_list if _valid(s)]
    if fn is None:
        # Tên tệp không phải SPH-có-sê-ri: đã đúng thì thôi, chưa đúng thì tên tệp
        # KHÔNG giúp (phải trông vào ảnh).
        return "tt_khong_giup_da_dung" if stored_valid else "tt_khong_giup_van_hong"

    fn_series, fn_digits = fn
    fn_full = f"{fn_series} {fn_digits}"
    stored_digits = {_digits(s) for s in stored_list if s}

    if any(_valid(s) and s.strip().upper() == fn_full for s in stored_list):
        return "khop"                      # đã đúng, tên tệp xác nhận
    if fn_digits in stored_digits:
        return "ghep_seri"                 # SỐ khớp, chỉ cần ghép chữ sê-ri từ tên tệp → CHẮC
    if not stored_valid:
        return "dien_ca"                   # VLM ra rỗng/rác, tên tệp cho cả SPH
    return "xung_dot"                      # tên tệp khác hẳn SPH đang lưu → cần soi tay


async def run(show: int) -> None:
    from app.db import gcns as gcns_col

    gcns = gcns_col()
    proj = {"filename": 1,
            "extractions.result.Đăng ký.Giấy chứng nhận.Số phát hành": 1}
    st = Counter()
    n = 0
    vi_du: dict = {}
    cur = gcns.find({"status": "done"}, proj).batch_size(1000)
    async for d in cur:
        n += 1
        loai = _phan_loai(d.get("filename"), _sph_stored(d.get("extractions")))
        st[loai] += 1
        if show and len(vi_du.setdefault(loai, [])) < show:
            fn = _parse_filename(d.get("filename"))
            vi_du[loai].append(
                (str(d.get("filename"))[:34], _sph_stored(d.get("extractions")),
                 f"{fn[0]} {fn[1]}" if fn else None))

    _tong_ket(n, st, vi_du, show)


def _tong_ket(n, st, vi_du, show) -> None:
    print("\n" + "=" * 70)
    print(f" TIỀM NĂNG LẤY SỐ PHÁT HÀNH TỪ TÊN TỆP  ·  {n:,} hồ sơ done")
    print("=" * 70)
    cuu = st["ghep_seri"] + st["dien_ca"]
    nhan = [
        ("ghep_seri", "GHÉP SÊ-RI (số khớp, chắc chắn)"),
        ("dien_ca", "ĐIỀN CẢ SPH (VLM rỗng/rác)"),
        ("xung_dot", "xung đột (tên tệp ≠ SPH lưu) — soi tay"),
        ("khop", "đã đúng, tên tệp xác nhận"),
        ("tt_khong_giup_van_hong", "tên tệp KHÔNG giúp, vẫn hỏng — cần ẢNH"),
        ("tt_khong_giup_da_dung", "tên tệp không phải SPH, nhưng đã đúng"),
    ]
    for k, label in nhan:
        v = st.get(k, 0)
        print(f"   {label:<46} {v:>8,} ({v/max(n,1)*100:4.1f}%)")
    print("-" * 70)
    print(f"   ⇒ CỨU ĐƯỢC BẰNG TÊN TỆP (không VLM): {cuu:,} ({cuu/max(n,1)*100:.1f}%)")
    print(f"   ⇒ Còn phải trông vào ảnh nền đỏ     : {st['tt_khong_giup_van_hong']:,}"
          f" ({st['tt_khong_giup_van_hong']/max(n,1)*100:.1f}%)")
    if show:
        for k, label in nhan:
            rows = vi_du.get(k) or []
            if not rows:
                continue
            print(f"\n── {label} ──")
            for fn, stored, cand in rows:
                print(f"   {fn:<36} lưu={str(stored)[:32]:<34} tên_tệp={cand}")


# ── PURE smoke ───────────────────────────────────────────────────────────────

def _smoke() -> None:
    assert _parse_filename("O 276111.pdf") == ("O", "276111"), "KILL [1]"
    assert _parse_filename("S974888.pdf") == ("S", "974888"), "KILL [2] dính liền"
    assert _parse_filename("AK 578667.pdf") == ("AK", "578667"), "KILL [3] 2 chữ"
    assert _parse_filename("SốK 850214.pdf") == ("K", "850214"), "KILL [4] bỏ nhãn Số"
    assert _parse_filename("774402.pdf") is None, "KILL [5] số thuần → None"
    assert _parse_filename("154281.pdf") is None, "KILL [6] số thuần → None"
    assert _parse_filename("") is None, "KILL [7]"
    assert _parse_filename(None) is None, "KILL [8]"
    assert _digits("O 733328") == "733328", "KILL [9]"

    # GHÉP SÊ-RI: VLM ra '733328' (rớt O), tên tệp 'O 733328' → số khớp
    assert _phan_loai("O 733328.pdf", ["733328"]) == "ghep_seri", "KILL [10]"
    # ĐIỀN CẢ: VLM rỗng, tên tệp có SPH
    assert _phan_loai("S 974888.pdf", [""]) == "dien_ca", "KILL [11]"
    assert _phan_loai("U 283003.pdf", []) == "dien_ca", "KILL [12] không entry nào"
    # KHỚP: đã đúng
    assert _phan_loai("P 680650.pdf", ["P 680650"]) == "khop", "KILL [13]"
    # XUNG ĐỘT: tên tệp khác hẳn SPH hợp lệ đang lưu
    assert _phan_loai("O 111111.pdf", ["CE 058053"]) == "xung_dot", "KILL [14]"
    # TÊN TỆP KHÔNG GIÚP: số thuần + đang hỏng → cần ảnh
    assert _phan_loai("774402.pdf", [""]) == "tt_khong_giup_van_hong", "KILL [15]"
    assert _phan_loai("774402.pdf", ["CE 058053"]) == "tt_khong_giup_da_dung", "KILL [16]"

    print("audit_filename_sph PURE: 16 KILL ✓")


async def main() -> None:
    p = argparse.ArgumentParser(description="Đo tiềm năng lấy SPH từ tên tệp (read-only).")
    p.add_argument("--show", type=int, default=0, help="In tối đa N ví dụ mỗi nhóm.")
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args()
    if args.smoke:
        _smoke()
        return
    await run(args.show)


if __name__ == "__main__":
    import sys

    if "--smoke" in sys.argv:
        _smoke()
    else:
        asyncio.run(main())
