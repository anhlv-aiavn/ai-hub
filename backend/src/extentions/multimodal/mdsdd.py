"""Chuẩn hóa MỤC ĐÍCH SỬ DỤNG ĐẤT — logic thuần, không Mongo, không CLI.

Cùng style `normalize_dang_ky.py`: pure function để pipeline, script audit và
backfill dùng chung MỘT nguồn luật, không chép ra nhiều nơi (bài học
`groups_from_roles` từng bị chép 3 chỗ rồi sửa lệch nhau → công cụ đo báo sai
về chính production).

TRỌNG TÂM: ONT (191) vs ODT (192). Đo trên kho thật, "đất ở" chiếm áp đảo số
bản ghi mục đích, và đây cũng là chỗ DUY NHẤT text trên giấy KHÔNG đủ để quyết —
phải suy từ địa chỉ thửa. Các mã còn lại của danh mục 80 mã bổ sung ở Phase 1,
DỰNG TỪ SỐ LIỆU THẬT của `app.scripts.audit_mdsdd`, không đoán.

Smoke PURE (không cần Mongo/GPU):
    docker compose exec api python -m src.extentions.multimodal.mdsdd
"""

import re

from app.vn_text import strip_diacritics

# Danh mục — hiện chỉ 2 mã đã CHỐT. Phase 1 bổ sung phần còn lại từ audit 0.3.
LOAI_MDSDD: list[dict] = [
    {"id": 191, "ky_hieu_muc_dich": "ONT", "ten_muc_dich": "Đất ở tại nông thôn"},
    {"id": 192, "ky_hieu_muc_dich": "ODT", "ten_muc_dich": "Đất ở tại đô thị"},
]
_THEO_KY_HIEU = {m["ky_hieu_muc_dich"]: m for m in LOAI_MDSDD}

METHODS = ("ky_hieu_ngoac", "ky_hieu_token", "ten_exact", "dia_chi")

# Ký hiệu trong ngoặc: "Đất ở tại nông thôn (ONT)" — dạng chính xác nhất.
_RE_NGOAC = re.compile(r"\(\s*([A-Za-z]{2,4})\s*\)")
# Ký hiệu đứng riêng: bắt buộc HOA hết, tránh nuốt chữ thường ngẫu nhiên.
_RE_TOKEN_HOA = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")

# ĐƠN VỊ HÀNH CHÍNH trong địa chỉ thửa (so trên text ĐÃ BỎ DẤU).
# Địa chỉ VN viết NHỎ → LỚN nên lấy occurrence ĐẦU TIÊN = cấp trực tiếp của thửa.
#
# BẪY: "thị xã" là cấp HUYỆN, không phải "xã" cấp xã. Chuỗi "thi xa" chứa "xa"
# nên phải loại trước bằng lookbehind, nếu không "phường X, thị xã Y" sẽ bị đọc
# thành nông thôn.
_RE_DVHC = re.compile(
    r"\b(?P<thi_tran>thi tran|tt\.)"
    r"|\b(?P<phuong>phuong|p\.(?=\s*[a-z0-9]))"
    r"|(?<!thi )\b(?P<xa>xa)\b"
)
_DVHC_MA = {"phuong": "ODT", "thi_tran": "ODT", "xa": "ONT"}


def chuan_hoa(s) -> str:
    """Text so khớp: bỏ dấu + lower + gộp khoảng trắng (kể cả xuống dòng)."""
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    return re.sub(r"\s+", " ", strip_diacritics(s)).strip()


def don_vi_hanh_chinh(dia_chi) -> str | None:
    """→ "phuong" | "thi_tran" | "xa" | None — cấp hành chính TRỰC TIẾP của thửa.

    Lấy occurrence ĐẦU TIÊN vì địa chỉ VN viết nhỏ → lớn: "Thôn 5, xã Ea Tu,
    thành phố Buôn Ma Thuột" là NÔNG THÔN, dù có chữ "thành phố" phía sau.
    """
    m = _RE_DVHC.search(chuan_hoa(dia_chi))
    return m.lastgroup if m else None


def ky_hieu_trong_text(text) -> tuple[str, str] | None:
    """→ (ký hiệu, method) — ưu tiên trong ngoặc, sau đó token viết HOA."""
    raw = text if isinstance(text, str) else ""
    m = _RE_NGOAC.search(raw)
    if m:
        return m.group(1).upper(), "ky_hieu_ngoac"
    m = _RE_TOKEN_HOA.search(raw)
    if m:
        return m.group(1), "ky_hieu_token"
    return None


def _kq(ky_hieu: str, method: str, score: float) -> dict:
    m = _THEO_KY_HIEU[ky_hieu]
    return {"id": m["id"], "ky_hieu": ky_hieu, "ten": m["ten_muc_dich"],
            "method": method, "score": score, "ambiguous": False}


def la_dat_o(text) -> bool:
    """Chuỗi mục đích này có phải "đất ở" không (mọi biến thể)."""
    t = chuan_hoa(text)
    if not t:
        return False
    if re.search(r"\bdat o\b", t):
        return True
    kh = ky_hieu_trong_text(text)
    return bool(kh and kh[0] in _THEO_KY_HIEU)


def map_dat_o(text, dia_chi: str = "") -> dict | None:
    """Quyết ONT(191) / ODT(192) cho một chuỗi mục đích "đất ở".

    → None nếu chuỗi KHÔNG phải đất ở (mã khác sẽ do Phase 1 xử).
    → dict có `ambiguous: True`, `id: None` nếu là đất ở nhưng không quyết nổi
      (text trần + địa chỉ không nêu phường/thị trấn/xã) → hàng đợi rà tay.
      KHÔNG đoán bừa: đoán sai ONT/ODT là sai mã pháp lý, im lặng.
    """
    if not la_dat_o(text):
        return None

    kh = ky_hieu_trong_text(text)
    if kh and kh[0] in _THEO_KY_HIEU:
        return _kq(kh[0], kh[1], 1.0)

    t = chuan_hoa(text)
    if "nong thon" in t:
        return _kq("ONT", "ten_exact", 1.0)
    if "do thi" in t:
        return _kq("ODT", "ten_exact", 1.0)

    dv = don_vi_hanh_chinh(dia_chi)
    if dv:
        # score thấp hơn 1.0: đây là SUY DẪN từ địa chỉ, không phải chữ trên giấy.
        return {**_kq(_DVHC_MA[dv], "dia_chi", 0.9), "dvhc": dv}

    return {"id": None, "ky_hieu": None, "ten": None,
            "method": None, "score": 0.0, "ambiguous": True}


def _smoke() -> None:
    """Tầng PURE — stdlib, không Mongo, không GPU. KILL đỏ ⇒ dừng, sửa GỐC."""
    # 1. ký hiệu trong ngoặc quyết định, bất kể địa chỉ nói gì
    r = map_dat_o("Đất ở tại nông thôn (ONT)", "phường Nghĩa Tân, quận Cầu Giấy")
    assert r and r["id"] == 191 and r["method"] == "ky_hieu_ngoac", \
        f"KILL [1] ký hiệu trong ngoặc phải thắng địa chỉ: {r}"

    # 2. tên đầy đủ, không ngoặc
    r = map_dat_o("Đất ở tại đô thị", "")
    assert r and r["id"] == 192 and r["method"] == "ten_exact", f"KILL [2] {r}"

    # 3+4. LUẬT ĐÃ CHỐT: "Đất ở" trần → suy từ địa chỉ thửa
    r = map_dat_o("Đất ở", "Số 10, phường Nghĩa Tân, quận Cầu Giấy, Hà Nội")
    assert r and r["id"] == 192 and r["method"] == "dia_chi", \
        f"KILL [3] 'Đất ở' + phường phải ra ODT(192): {r}"
    r = map_dat_o("Đất ở", "Thôn 5, xã Ea Tu, thành phố Buôn Ma Thuột")
    assert r and r["id"] == 191 and r["method"] == "dia_chi", \
        f"KILL [4] 'Đất ở' + xã phải ra ONT(191): {r}"

    # 5. thị trấn = đô thị
    r = map_dat_o("Đất ở", "Khối 3, thị trấn Kỳ Sơn, huyện Kỳ Sơn")
    assert r and r["id"] == 192, f"KILL [5] thị trấn phải ra ODT(192): {r}"

    # 6. BẪY "thị xã" — cấp huyện, KHÔNG được đọc thành "xã"
    r = map_dat_o("Đất ở", "phường Đông Kinh, thị xã Từ Sơn, Bắc Ninh")
    assert r and r["id"] == 192, f"KILL [6] 'thị xã' bị đọc nhầm thành xã: {r}"
    assert don_vi_hanh_chinh("Tổ 4, thị xã Từ Sơn") is None, \
        "KILL [6b] 'thị xã' đứng một mình không phải cấp xã"

    # 7. nhỏ → lớn: lấy đơn vị ĐẦU TIÊN, không phải cái xuất hiện sau
    assert don_vi_hanh_chinh("xã An Bình, thành phố Dĩ An") == "xa", \
        "KILL [7] phải lấy occurrence đầu tiên (cấp trực tiếp của thửa)"

    # 8. không quyết nổi → ambiguous, TUYỆT ĐỐI không đoán
    r = map_dat_o("Đất ở", "Lô 12, khu dân cư Bắc Hà")
    assert r and r["ambiguous"] and r["id"] is None, \
        f"KILL [8] không có dấu hiệu hành chính thì phải ambiguous, không đoán: {r}"

    # 9. text rỗng KHÔNG được ra mã
    for x in ("", None, "   "):
        assert map_dat_o(x, "phường Nghĩa Tân") is None, f"KILL [9] text rỗng ra mã: {x!r}"

    # 10. mục đích KHÁC đất ở → None (Phase 1 xử), không nhận vơ
    for x in ("Đất trồng cây lâu năm (CLN)", "Đất chuyên trồng lúa nước"):
        assert map_dat_o(x, "xã Ea Tu") is None, f"KILL [10] nhận vơ mã không phải đất ở: {x}"

    # 11. mã trả về luôn thuộc danh mục, method luôn hợp lệ
    for txt, dc in [("Đất ở (ODT)", ""), ("Đất ở tại nông thôn", ""), ("Đất ở", "xã X")]:
        r = map_dat_o(txt, dc)
        assert r["id"] in (m["id"] for m in LOAI_MDSDD), f"KILL [11] mã ngoài danh mục: {r}"
        assert r["method"] in METHODS, f"KILL [11b] method lạ: {r}"

    print("mdsdd PURE: 11 nhóm ca ✓")


if __name__ == "__main__":
    _smoke()
