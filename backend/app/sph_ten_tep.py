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

# Cụm "CHỮ + SỐ" trong tên tệp. Dùng SEARCH (không fullmatch) vì tên tệp thật hay có
# ĐUÔI: "Y 905108-GCN.pdf", "M 273198-GCN.pdf", "10003-GCN-T 009812-p64J8Xxl.pdf".
# Bản fullmatch trước đây bỏ sót TOÀN BỘ nhóm này. An toàn vì còn chốt thứ hai: phần
# SỐ phải trùng cụm số đọc được mới cho vá.
# Nhóm "S?" đầu nuốt nhãn "Số" bị dính ("S6AN 421339" → AN); có backtrack nên
# "S 266529" vẫn ra tiền tố "S".
# Phần chữ: 1-4 chữ liền ("AD"), HOẶC các chữ ĐƠN cách nhau đúng một dấu cách
# ("A Đ339173" = "AĐ 339173" bị gõ lạc dấu cách). Không cho nối kiểu "GCN T".
_CHU = r"(?:[A-ZĐ]{1,4}|[A-ZĐ](?: [A-ZĐ]){1,3})"
_CUM_RE = re.compile(
    rf"(?:S[ỐỒỔỖỘÔỎÕỌÓÒƠỚO0-9]\s*)?({_CHU})[ _-]?(\d{{4,7}})(?![0-9])")
# Nhãn "Số" dính liền tiền tố trong tên tệp ("SoS 216419" → lấy "S").
_NHAN_SO_RE = re.compile(r"^S[ỐỒỔỖỘÔỎÕỌÓÒƠỚO0-9]$")
# Cụm số TRẦN 4-7 chữ số = seri bản cũ bị mất tiền tố. Từ 8 chữ số trở lên là SPH
# bản mới (hợp lệ) → không đụng.
_SO_TRAN_RE = re.compile(r"^\s*(\d{4,7})\s*$")


def _chuan(filename) -> str:
    if not isinstance(filename, str) or not filename.strip():
        return ""
    ten = filename.strip().rsplit("/", 1)[-1]
    return re.sub(r"\s+", " ", re.sub(r"\.pdf$", "", ten, flags=re.IGNORECASE)).strip().upper()


def _go_nhan_so(tien_to: str) -> str:
    return tien_to[2:] if len(tien_to) > 1 and _NHAN_SO_RE.fullmatch(tien_to[:2]) else tien_to


def _duyet_cum(ten: str):
    """Sinh (tien_to, so) cho từng cụm CHỮ+SỐ hợp lệ trong tên tệp đã chuẩn hoá.

    CHẶN CỤM NẰM TRONG CHỮ THƯỜNG: "GIẤY XÁC NHẬN ĐĂNG KÍ ĐẤT ĐAI 6112" từng cho
    ra tiền tố rác "ĐAI" (Đ, A, I đều là chữ cái hợp lệ!). Luật: ký tự khác dấu
    cách ngay TRƯỚC cụm không được là CHỮ CÁI — seri luôn đứng đầu tên, hoặc sau
    dấu phân cách, hoặc sau một cụm số."""
    for m in _CUM_RE.finditer(ten):
        truoc = ten[:m.start()].rstrip(" ")
        if truoc and truoc[-1].isalpha():
            continue
        tt = _go_nhan_so(m.group(1).replace(" ", ""))
        if tt:
            yield tt, m.group(2)


def tach_ten_tep(filename) -> tuple[str, str] | None:
    """Cụm CHỮ+SỐ ĐẦU TIÊN trong tên tệp: ('AD', '597734') từ 'AD 597734.pdf'.
    Chỉ dùng để lọc sơ/đếm — việc vá luôn đi qua `tim_tien_to` (có đối chiếu số)."""
    return next(_duyet_cum(_chuan(filename)), None)


def tim_tien_to(filename, so_luu: str) -> tuple[str, str] | None:
    """Cụm CHỮ+SỐ trong tên tệp mà phần SỐ trùng ĐÚNG `so_luu` (bỏ qua số 0 đầu).

    Trả (tien_to, so_cua_ten_tep) — lấy số của TÊN TỆP vì seri in trên phôi có số 0
    ở đầu ("AN 065845") mà model hay nuốt mất. None = không tìm được ⇒ KHÔNG vá."""
    goc = (so_luu or "").lstrip("0")
    if not goc:
        return None
    for tt, so in _duyet_cum(_chuan(filename)):
        if so.lstrip("0") == goc:
            return (tt, so)
    return None


def va_sph_tu_ten_tep(extractions, filename) -> int:
    """SỬA TẠI CHỖ mọi entry có SPH là cụm số trần trùng số trong tên tệp.

    Trả về số entry đã vá (0 = không đụng gì)."""
    if not _chuan(filename):
        return 0
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
            if not m:
                continue
            cap = tim_tien_to(filename, m.group(1))
            if cap:
                g["Số phát hành"] = f"{cap[0]} {cap[1]}"
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
    # ĐUÔI -GCN: bản fullmatch cũ bỏ sót sạch nhóm này
    assert tach_ten_tep("Y 905108-GCN.pdf") == ("Y", "905108"), "KILL [10] đuôi -GCN"
    assert tach_ten_tep("CE 403356-GCN.pdf") == ("CE", "403356"), "KILL [11]"
    assert tach_ten_tep("S 216419-GCN.pdf") == ("S", "216419"), "KILL [12]"

    assert tim_tien_to("AN 065845.pdf", "65845") == ("AN", "065845"), "KILL [13] số 0 đầu"
    assert tim_tien_to("S6AN 421339.pdf", "421339") == ("AN", "421339"), "KILL [14] nhãn Số dính"
    assert tim_tien_to("10003-GCN-T 009812-p64.pdf", "009812") == ("T", "009812"), "KILL [15]"
    assert tim_tien_to("AB 243010.pdf", "1000005") is None, "KILL [16] số không trùng"
    assert tim_tien_to("AD 597734.pdf", "000") is None, "KILL [17] toàn số 0"
    assert tim_tien_to(None, "597734") is None, "KILL [18]"

    def ex(*sph):
        return [{"result": {"Đăng ký": [
            {"Giấy chứng nhận": {"Số phát hành": s}} for s in sph]}}]

    e = ex("065845", "AP 471319")
    assert va_sph_tu_ten_tep(e, "AN 065845.pdf") == 1, "KILL [19]"
    got = [g["Giấy chứng nhận"]["Số phát hành"] for g in e[0]["result"]["Đăng ký"]]
    assert got == ["AN 065845", "AP 471319"], f"KILL [20] {got}"

    e = ex("905108")
    assert va_sph_tu_ten_tep(e, "Y 905108-GCN.pdf") == 1, "KILL [21] đuôi -GCN vá được"
    assert e[0]["result"]["Đăng ký"][0]["Giấy chứng nhận"]["Số phát hành"] == "Y 905108", "KILL [22]"

    # số KHÔNG trùng → tuyệt đối không đụng (VLM đọc sai hẳn, để chạy lại lo)
    e = ex("1000005")
    assert va_sph_tu_ten_tep(e, "AB 243010.pdf") == 0, "KILL [23]"
    assert e[0]["result"]["Đăng ký"][0]["Giấy chứng nhận"]["Số phát hành"] == "1000005", "KILL [24]"

    # nuốt số 0 đầu → lấy cụm số của TÊN TỆP
    e = ex("65845")
    va_sph_tu_ten_tep(e, "AN 065845.pdf")
    assert e[0]["result"]["Đăng ký"][0]["Giấy chứng nhận"]["Số phát hành"] == "AN 065845", "KILL [25]"

    # SPH bản mới (8-15 số) và SPH đã có chữ → không đụng
    e = ex("0103040010", "AP 471319")
    assert va_sph_tu_ten_tep(e, "AP 471319.pdf") == 0, "KILL [26]"

    # rỗng / cấu trúc lạ → không nổ
    assert va_sph_tu_ten_tep([], "AD 597734.pdf") == 0, "KILL [27]"
    assert va_sph_tu_ten_tep(None, "AD 597734.pdf") == 0, "KILL [28]"
    assert va_sph_tu_ten_tep([{"result": None}], "AD 597734.pdf") == 0, "KILL [29]"
    assert va_sph_tu_ten_tep(ex("597734"), None) == 0, "KILL [30] không có tên tệp"

    # ── Bẫy thật gặp trong dry-run ──────────────────────────────────────────
    # Tên tệp là CÂU CHỮ: "ĐAI" trong "đất đai" từng bị nhận nhầm làm tiền tố
    assert tim_tien_to("Giấy xác nhận đăng kí đất đai 6112.pdf", "6112") is None, "KILL [31]"
    assert tach_ten_tep("Giấy xác nhận đăng kí đất đai 6112.pdf") is None, "KILL [32]"
    assert tim_tien_to("Đơn đăng ký biến động 1234.pdf", "1234") is None, "KILL [33]"
    # Dấu cách lạc giữa tiền tố: "A Đ339173" = "AĐ 339173", KHÔNG phải "Đ 339173"
    assert tim_tien_to("A Đ339173.pdf", "339173") == ("AĐ", "339173"), "KILL [34]"
    assert tim_tien_to("A Đ 515935.pdf", "515935") == ("AĐ", "515935"), "KILL [35]"
    assert tim_tien_to("A A226525.pdf", "226525") == ("AA", "226525"), "KILL [36]"
    # nhưng KHÔNG nối bừa cụm nhiều chữ cách nhau: "GCN T 009812" → không phải "GCNT"
    assert tim_tien_to("GCN T 009812.pdf", "009812") is None, "KILL [37]"
    # hai seri trong một tên: cụm thứ hai vẫn dùng được (sau cụm SỐ, không phải chữ)
    assert tim_tien_to("A854780 A854779.pdf", "854779") == ("A", "854779"), "KILL [38]"
    assert tim_tien_to("A854780 A854779.pdf", "854780") == ("A", "854780"), "KILL [39]"
    # sau dấu phân cách vẫn nhận
    assert tim_tien_to("10003-GCN-T 009812-p64.pdf", "009812") == ("T", "009812"), "KILL [40]"

    print("sph_ten_tep PURE: 40 KILL ✓")


if __name__ == "__main__":
    _smoke()
