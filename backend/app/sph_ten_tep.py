"""Vá SỐ PHÁT HÀNH thiếu tiền tố chữ bằng TÊN TỆP — luật thuần, không I/O.

Ca xử lý: VLM đọc đúng cụm số nhưng bỏ mất 1-4 chữ in hoa đứng trước, trong khi
tên tệp (do chuyên viên đặt theo seri trên phôi) đã mang sẵn tiền tố đó:

    AD 597734.pdf  +  SPH đọc được '597734'   ⇒  'AD 597734'

ĐIỀU KIỆN GHÉP (chặt, cố ý): phần SỐ của tên tệp phải TRÙNG cụm số đang có, bỏ
qua số 0 ở đầu. Không trùng ⇒ KHÔNG đụng — `AB 243010.pdf` mà đọc ra '1000005'
là VLM đọc sai hẳn; vá theo tên tệp lúc đó là bịa dữ liệu.

Vì sao lấy cụm số của TÊN TỆP chứ không giữ cụm đang có: seri in trên phôi có số
0 ở đầu ("AN 065845") mà model hay nuốt mất.

Dùng ở HAI nơi, giữ đúng một bản luật:
  - app/worker/run_job.py     — mọi hồ sơ mới, ngay trong pipeline
  - app/scripts/backfill_sph_tu_ten_tep.py — vá kho cũ

Chạy smoke (thuần stdlib, không cần Mongo/VLM):
    python -m app.sph_ten_tep
"""

import re

# Tên tệp mang tiền tố: "AD 597734.pdf", "AĐ381126.pdf", "S 266529.pdf",
# kèm biến thể còn dính chữ "Số"/"So"/"S6" ở đầu ("So AN 065845.pdf").
_TEN_TEP_RE = re.compile(r"^(?:S[ỐỒỔỖỘÔỎÕỌÓÒƠỚO0-9]\s*)?([A-ZĐ]{1,4})\s*(\d{4,7})$")
# Cụm số TRẦN 4-7 chữ số = seri bản cũ bị mất tiền tố. Từ 8 chữ số trở lên là SPH
# bản mới (hợp lệ) → không đụng.
_SO_TRAN_RE = re.compile(r"^\s*(\d{4,7})\s*$")


def tach_ten_tep(filename) -> tuple[str, str] | None:
    """('AD', '597734') từ 'AD 597734.pdf'. None nếu tên tệp không mang tiền tố."""
    if not isinstance(filename, str) or not filename.strip():
        return None
    ten = filename.strip().rsplit("/", 1)[-1]
    ten = re.sub(r"\.pdf$", "", ten, flags=re.IGNORECASE)
    m = _TEN_TEP_RE.fullmatch(re.sub(r"\s+", " ", ten).strip().upper())
    return (m.group(1), m.group(2)) if m else None


def khop_so(so_luu: str, so_ten: str) -> bool:
    """Cùng một con số, bỏ qua số 0 ở đầu ('065845' ≡ '65845')."""
    a, b = so_luu.lstrip("0"), so_ten.lstrip("0")
    return bool(a) and a == b


def va_sph_tu_ten_tep(extractions, filename) -> int:
    """SỬA TẠI CHỖ mọi entry có SPH là cụm số trần trùng số trong tên tệp.

    Trả về số entry đã vá (0 = không đụng gì)."""
    cap = tach_ten_tep(filename)
    if not cap:
        return 0
    prefix, so_ten = cap
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
            if m and khop_so(m.group(1), so_ten):
                g["Số phát hành"] = f"{prefix} {so_ten}"
                n += 1
    return n


def _smoke() -> None:
    assert tach_ten_tep("AD 597734.pdf") == ("AD", "597734"), "KILL [1]"
    assert tach_ten_tep("AĐ 381126.pdf") == ("AĐ", "381126"), "KILL [2] chữ Đ"
    assert tach_ten_tep("AN892317.pdf") == ("AN", "892317"), "KILL [3] không dấu cách"
    assert tach_ten_tep("Ah 867580.pdf") == ("AH", "867580"), "KILL [4] viết thường"
    assert tach_ten_tep("So AN 065845.pdf") == ("AN", "065845"), "KILL [5] còn dính 'So'"
    assert tach_ten_tep("S 266529.pdf") == ("S", "266529"), "KILL [6] seri 1 chữ"
    assert tach_ten_tep("23212.pdf") is None, "KILL [7] tên tệp thuần số → bỏ"
    assert tach_ten_tep("10103010487.pdf") is None, "KILL [8] SPH bản mới không phải tiền tố"
    assert tach_ten_tep(None) is None and tach_ten_tep("") is None, "KILL [9]"

    assert khop_so("065845", "065845") and khop_so("65845", "065845"), "KILL [10]"
    assert not khop_so("1000005", "243010"), "KILL [11] số khác nhau"
    assert not khop_so("0", "000"), "KILL [12] rỗng sau khi bỏ 0 → không khớp"

    def ex(*sph):
        return [{"result": {"Đăng ký": [
            {"Giấy chứng nhận": {"Số phát hành": s}} for s in sph]}}]

    e = ex("065845", "AP 471319")
    assert va_sph_tu_ten_tep(e, "AN 065845.pdf") == 1, "KILL [13]"
    got = [g["Giấy chứng nhận"]["Số phát hành"] for g in e[0]["result"]["Đăng ký"]]
    assert got == ["AN 065845", "AP 471319"], f"KILL [14] {got}"

    # số KHÔNG trùng → tuyệt đối không đụng (VLM đọc sai hẳn, để chạy lại lo)
    e = ex("1000005")
    assert va_sph_tu_ten_tep(e, "AB 243010.pdf") == 0, "KILL [15]"
    assert e[0]["result"]["Đăng ký"][0]["Giấy chứng nhận"]["Số phát hành"] == "1000005", "KILL [16]"

    # nuốt số 0 đầu → lấy cụm số của TÊN TỆP
    e = ex("65845")
    va_sph_tu_ten_tep(e, "AN 065845.pdf")
    assert e[0]["result"]["Đăng ký"][0]["Giấy chứng nhận"]["Số phát hành"] == "AN 065845", "KILL [17]"

    # SPH bản mới (8-15 số) và SPH đã có chữ → không đụng
    e = ex("0103040010", "AP 471319")
    assert va_sph_tu_ten_tep(e, "AP 471319.pdf") == 0, "KILL [18]"

    # rỗng / cấu trúc lạ → không nổ
    assert va_sph_tu_ten_tep([], "AD 597734.pdf") == 0, "KILL [19]"
    assert va_sph_tu_ten_tep(None, "AD 597734.pdf") == 0, "KILL [20]"
    assert va_sph_tu_ten_tep([{"result": None}], "AD 597734.pdf") == 0, "KILL [21]"

    print("sph_ten_tep PURE: 21 KILL ✓")


if __name__ == "__main__":
    _smoke()
