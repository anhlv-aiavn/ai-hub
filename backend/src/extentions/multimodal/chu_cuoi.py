"""Suy "chủ cuối" (chủ sử dụng mới nhất) từ một entry Đăng ký đã bóc.

Pure-function, KHÔNG Mongo I/O, KHÔNG CLI vận hành (chỉ block __main__ PURE smoke)
— để pipeline và script backfill dùng chung, tránh circular import (cùng khuôn
`normalize_dang_ky.py`).

Nguồn chân lý xếp theo độ ưu tiên:
  1. Biến động CHUYỂN CHỦ mới nhất (theo Thời gian đã parse — KHÔNG theo thứ tự
     mảng, vì mảng không theo thời gian: xem quan sát dữ liệu thật F1).
  2. Nếu không có chuyển chủ nào → chủ trên giấy gốc (`Chủ sử dụng`).

Bẫy đã bọc (quan sát từ dữ liệu thật):
  - Thế chấp / xóa thế chấp / đính chính CÓ tên người/ngân hàng nhưng KHÔNG đổi
    chủ → phân loại phủ định kiểm TRƯỚC khẳng định.
  - Một text có thể nhiều chủ, ngăn bằng "và" / "và chồng là" / ";".
  - Chủ GỐC cũng hay dồn nhiều người vào một chuỗi "Tên chủ" → phải tách.
  - Text chuyển nhượng cho 1 người nhưng đuôi câu nhắc "vợ chồng" người bán →
    chỉ giữ đoạn có CCCD/sinh năm, bỏ nhiễu.

API chính:
    chu_cuoi_for_entry(entry: dict) -> dict         # 1 entry "Đăng ký"
    chu_cuoi_for_result(result: dict) -> list[dict] # cả result (nhiều entry)

PURE smoke (không cần Mongo/GPU):
    docker compose exec api python -m src.extentions.multimodal.chu_cuoi
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

ALGO_VERSION = 1

# ── Phân loại biến động (khớp trên text ĐÃ BỎ DẤU + lower) ───────────────────
# PHỦ ĐỊNH kiểm TRƯỚC: một biến động vừa "chuyển nhượng" vừa "xóa thế chấp"
# (vd "xóa thế chấp do chuyển nhượng") phải rơi về KHÔNG đổi chủ.
KHONG_DOI_CHU = (
    "the chap", "xoa the chap", "xoa dang ky the chap", "giai chap",
    "dinh chinh", "cap doi", "cap lai", "dang ky lai",
    "thay doi dien tich", "chuyen muc dich su dung", "thay doi muc dich",
    "gop von", "xoa gop von", "ke bien", "thay doi thong tin",
    "hoan thanh nghia vu tai chinh", "chua nop", "nghia vu tai chinh",
)
CHUYEN_CHU = (
    "tang cho", "cho tang", "chuyen nhuong", "nhan chuyen nhuong",
    "thua ke", "nhan thua ke", "chuyen quyen", "chuyen chu",
    "mua ban", "trao doi",
)

_HONORIFIC = r"(?:ông|bà|ong|ba)"
# CCCD/CMND: BẮT BUỘC từ khóa dẫn ngay trước + đúng 9 hoặc 12 chữ số liền (không
# nới — text đầy số hồ sơ dạng "9186/2004/QĐCS 20557/2004" sẽ lọt nếu chỉ bắt số).
RE_CCCD = re.compile(
    r"(?:cccd|cmnd|cmt|căn\s*cước(?:\s*công\s*dân)?|chứng\s*minh(?:\s*nhân\s*dân|\s*thư)?)"
    r"\s*(?:số)?\s*[:.]?\s*(\d{12}|\d{9})(?!\d)",
    re.IGNORECASE,
)
RE_LOAI_GIAY = re.compile(
    r"(căn\s*cước\s*công\s*dân|cccd|chứng\s*minh\s*nhân\s*dân|cmnd|cmt)",
    re.IGNORECASE,
)
RE_NAM_SINH = re.compile(r"sinh\s*(?:năm)?\s*[:.]?\s*((?:19|20)\d{2})", re.IGNORECASE)
RE_DIA_CHI = re.compile(
    r"(?:hktt|hộ\s*khẩu\s*thường\s*trú|nơi\s*thường\s*trú|thường\s*trú|địa\s*chỉ(?:\s*thường\s*trú)?)"
    r"\s*[:.]?\s*(.+?)(?=(?:;|\.\s|\s+và\s+" + _HONORIFIC + r"\b|theo\s|$))",
    re.IGNORECASE,
)
# Neo người: ông/bà/anh/chị + KHOẢNG TRẮNG hoặc DẤU HAI CHẤM ("Ông:" rất phổ biến).
RE_ANCHOR = re.compile(r"\b(ông|bà|ong|ba|anh|chị|chi)[\s:]+", re.IGNORECASE)
# Từ khóa ID/sinh — mốc để bóc tên KHÔNG có honorific (vd "Tặng cho Phạm Thị An, CMND…").
RE_ID_KW = re.compile(
    r"\b(?:sinh(?:\s*năm)?|năm\s*sinh|cccd|cmnd|cmt|căn\s*cước|chứng\s*minh)\b",
    re.IGNORECASE)
# Ngăn cách chủ trong chuỗi Tên chủ giấy gốc: "và (chồng/vợ) (là) (:)" hoặc ";".
RE_SPLIT_NAME = re.compile(
    r"\s+và\s+(?:chồng|vợ)?\s*(?:là)?\s*:?\s*|\s*;\s*", re.IGNORECASE)
RE_STRIP_HONO = re.compile(
    r"^\s*(?:ông|bà|ong|ba|chồng|vợ|là|ông\s*bà)\s*:?\s+", re.IGNORECASE)


def _s(v: Any) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        v = str(v)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", v)).strip()


_DEDOT = str.maketrans("đĐ", "dD")


def _norm(v: Any) -> str:
    """Bỏ dấu + lower + gộp trắng — chỉ để so khớp từ khóa, không dùng để hiển thị."""
    s = _s(v).translate(_DEDOT)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", s).lower()


# ── Phân loại + ngày ────────────────────────────────────────────────────────

def classify_bien_dong(noi_dung: Any) -> str:
    """→ 'chuyen_chu' | 'khong_doi_chu' | 'khong_ro'. Phủ định kiểm trước."""
    t = _norm(noi_dung)
    if not t:
        return "khong_ro"
    for kw in KHONG_DOI_CHU:
        if kw in t:
            return "khong_doi_chu"
    for kw in CHUYEN_CHU:
        if kw in t:
            return "chuyen_chu"
    return "khong_ro"


def _expand_year(y: int) -> int:
    if y >= 100:
        return y
    return 2000 + y if y < 50 else 1900 + y


def parse_date(tg: Any) -> tuple[int, int, int] | None:
    """→ (year, month, day) để SO SÁNH (month/day = 0 nếu thiếu), hoặc None.

    Nuốt d/m/y với phân cách / . - , năm 2 chữ số, thiếu ngày (mm/yyyy), chỉ năm,
    và 'ngày d tháng m năm y'. KHÔNG cần là ngày hợp lệ tuyệt đối — chỉ cần đủ để
    xếp thứ tự.
    """
    t = _s(tg)
    if not t:
        return None
    m = re.search(r"\b(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2,4})\b", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), _expand_year(int(m.group(3)))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return (y, mo, d)
        if 1 <= d <= 12 and 1 <= mo <= 31:  # có khi ghi m/d nhầm — vẫn cứu được năm
            return (y, d, mo)
        return (y, 0, 0)
    n = _norm(t)
    m = re.search(r"ngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+(\d{4})", n)
    if m:
        return (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(\d{1,2})\s*[/.\-]\s*(\d{4})\b", t)     # mm/yyyy
    if m:
        return (int(m.group(2)), int(m.group(1)), 0)
    m = re.search(r"\b(19|20)\d{2}\b", t)                     # chỉ năm
    if m:
        return (int(m.group(0)), 0, 0)
    return None


# ── Bóc chủ từ text ─────────────────────────────────────────────────────────

def _person_from_region(name: str, region: str) -> dict:
    cccd = RE_CCCD.search(region)
    loai = RE_LOAI_GIAY.search(region)
    ns = RE_NAM_SINH.search(region)
    dc = RE_DIA_CHI.search(region)
    lg = ""
    if loai:
        g = _norm(loai.group(1))
        lg = "Căn cước công dân" if ("can cuoc" in g or "cccd" in g) else "Chứng minh nhân dân"
    return {
        "Tên chủ": _s(name),
        "Loại giấy tờ": lg,
        "Số giấy tờ": cccd.group(1) if cccd else "",
        "Năm sinh": ns.group(1) if ns else "",
        "Giới tính": "",
        "Địa chỉ": _s(dc.group(1)) if dc else "",
    }


def _looks_like_name(name: str) -> bool:
    """Tên người VN: 2–5 token, token nào cũng hoa đầu, không chứa số."""
    toks = name.split()
    if not (2 <= len(toks) <= 5):
        return False
    return all(t[:1].isupper() and not any(ch.isdigit() for ch in t) for t in toks)


# Token acronym viết HOA nhưng KHÔNG phải tên — dừng gom tên khi gặp (vd
# "Phương Văn Thao CCCD" không được nuốt "CCCD"). Không đưa "Nam"/"Năm" vào đây
# vì là tên người phổ biến (Nguyễn Văn Nam).
_STOP_TOK = {"cccd", "cmnd", "cmt", "qsdd", "qshn", "gcn", "tp", "tt"}


def _lead_name(chunk: str) -> str:
    """Giữ CÁC token hoa-đầu LIỀN ĐẦU của chunk làm tên; dừng ở token thường/số/acronym.

    'Đặng Thị C theo hợp đồng' → 'Đặng Thị C'.  'Phạm Minh Hùng và Bà' → 'Phạm Minh Hùng'.
    'Phương Văn Thao CCCD' → 'Phương Văn Thao'.
    """
    out: list[str] = []
    for tok in chunk.split():
        w = tok.strip(".,;:()")
        if w and w[:1].isupper() and not any(ch.isdigit() for ch in w) and _norm(w) not in _STOP_TOK:
            out.append(w)
            if len(out) >= 5:
                break
        else:
            break
    while out and _norm(out[0]) in ("ong", "ba", "anh", "chi", "vo", "chong"):
        out.pop(0)
    return " ".join(out)


def _name_before(text: str, pos: int) -> str:
    """Lùi từ vị trí `pos` (mốc sinh/CCCD) gom các token HOA-đầu liền trước làm tên.

    Bóc tên KHÔNG có 'Ông/Bà' — vd 'Tặng cho Phạm Thị An, CMND số…' → 'Phạm Thị An'.
    Gặp token thường (cho, thửa, phường…) thì dừng, nên không nuốt nhầm chữ nền.
    """
    toks = text[:pos].rstrip(" ,;:.-").split()
    out: list[str] = []
    for tok in reversed(toks):
        w = tok.strip(".,;:()")
        if w and w[:1].isupper() and not any(ch.isdigit() for ch in w) and _norm(w) not in _STOP_TOK:
            out.insert(0, w)
            if len(out) >= 5:
                break
        else:
            break
    # bỏ tiền tố honorific nếu lọt vào (vd "Ông Lê Mai R" → "Lê Mai R") để trùng
    # khớp với tên nhánh honorific khi dedup.
    while out and _norm(out[0]) in ("ong", "ba", "anh", "chi", "vo", "chong"):
        out.pop(0)
    return " ".join(out) if 2 <= len(out) <= 5 else ""


def _ends_after_cho(prefix: str) -> bool:
    p = _norm(prefix).rstrip(" :")
    return p.endswith("cho")


def _ends_after_va(prefix: str) -> bool:
    p = _norm(prefix).rstrip(" :")
    return (p.endswith("va") or p.endswith("va vo") or p.endswith("va chong")
            or p.endswith("vo") or p.endswith("chong") or prefix.rstrip().endswith(";"))


def extract_recipients(noi_dung: Any) -> list[dict]:
    """Bóc (các) chủ NHẬN từ một text biến động chuyển chủ. Hai chiến lược, gộp+dedup.

    1. Neo 'Ông/Bà/Anh/Chị <tên>' (chịu cả 'Ông:'). Giữ neo khi: có CCCD/sinh, HOẶC
       chỉ một neo, HOẶC đứng ngay sau 'cho', HOẶC sau 'và/;' mà neo trước đã giữ
       (đồng chủ). Lọc nhiễu kiểu '…tài sản riêng của vợ chồng…'.
    2. Tên KHÔNG honorific đứng ngay trước mốc 'sinh/CCCD' — vd 'Tặng cho Phạm Thị
       An, CMND số…'. Chính xác vì bắt buộc kề mốc ID.

    Chưa bọc: bên bán/bên mua lẫn lộn nhiều người KHÔNG ID (vd 'ông A chuyển cho
    ông B') → trả rỗng, để tầng trên hạ confidence=thap và đẩy LLM/hậu kiểm.
    """
    text = _s(noi_dung)
    if not text:
        return []
    persons: list[dict] = []

    anchors = list(RE_ANCHOR.finditer(text))
    prev_kept = False
    for k, a in enumerate(anchors):
        seg_start = a.end()
        seg_end = anchors[k + 1].start() if k + 1 < len(anchors) else len(text)
        # Tên = token hoa-đầu liền đầu, sau khi cắt ở dấu ngắt mệnh đề (,;.).
        chunk = re.split(r"[,;.]", text[seg_start:seg_end], maxsplit=1)[0]
        name = _lead_name(chunk)
        region = text[a.start():seg_end]
        has_signal = bool(RE_CCCD.search(region) or RE_NAM_SINH.search(region))
        before = text[max(0, a.start() - 10):a.start()]
        keep = (has_signal or len(anchors) == 1 or _ends_after_cho(before)
                or (_ends_after_va(before) and prev_kept))
        if keep and _looks_like_name(name):
            persons.append(_person_from_region(name, region))
            prev_kept = True
        else:
            prev_kept = False

    for m in RE_ID_KW.finditer(text):
        name = _name_before(text, m.start())
        if name:
            # vùng quanh mốc để nhặt CCCD/địa chỉ của chính người này
            region = text[max(0, m.start() - len(name) - 4):m.start() + 200]
            persons.append(_person_from_region(name, region))

    return _dedup_persons(persons)


def split_owner_names(ten_chu: Any) -> list[str]:
    """Tách chuỗi 'Tên chủ' giấy gốc thành từng tên người, bỏ tiền tố Bà/Ông/và chồng là."""
    raw = _s(ten_chu)
    if not raw:
        return []
    out = []
    for part in RE_SPLIT_NAME.split(raw):
        p = RE_STRIP_HONO.sub("", part).strip()
        p = re.sub(r"\s+", " ", p)
        if p:
            out.append(p)
    return out


def _dedup_persons(persons: list[dict]) -> list[dict]:
    seen, out = set(), []
    for p in persons:
        key = (_norm(p.get("Tên chủ")), p.get("Số giấy tờ") or "")
        if key in seen or not key[0]:
            continue
        seen.add(key)
        out.append(p)
    return out


def _chu_from_giay_goc(chu_su_dung: list) -> list[dict]:
    """Chủ cuối = chủ giấy gốc, có tách các chuỗi 'Tên chủ' gộp nhiều người.

    Entry đã có sẵn Số giấy tờ/Địa chỉ (kiểu tách riêng) và 'Tên chủ' đơn → giữ
    nguyên trường. Entry dồn nhiều tên vào 'Tên chủ' → tách, các trường ID/địa chỉ
    thường rỗng ở kiểu cũ nên để trống (không gán bừa cho người sai).
    """
    out: list[dict] = []
    for c in chu_su_dung or []:
        if not isinstance(c, dict):
            continue
        names = split_owner_names(c.get("Tên chủ"))
        if len(names) <= 1:
            out.append({
                "Tên chủ": names[0] if names else _s(c.get("Tên chủ")),
                "Loại giấy tờ": _s(c.get("Loại giấy tờ")),
                "Số giấy tờ": _s(c.get("Số giấy tờ")),
                "Năm sinh": _s(c.get("Năm sinh")),
                "Giới tính": _s(c.get("Giới tính")),
                "Địa chỉ": _s(c.get("Địa chỉ")),
            })
        else:  # chuỗi gộp nhiều người — tách, trường phụ để trống
            for nm in names:
                out.append({"Tên chủ": nm, "Loại giấy tờ": "", "Số giấy tờ": "",
                            "Năm sinh": "", "Giới tính": "", "Địa chỉ": ""})
    return _dedup_persons(out)


# ── API chính ───────────────────────────────────────────────────────────────

def chu_cuoi_for_entry(entry: dict) -> dict:
    """Suy chủ cuối cho MỘT entry 'Đăng ký'. Luôn trả dict đúng schema, không raise."""
    if not isinstance(entry, dict):
        entry = {}
    bds = [b for b in (entry.get("Biến động") or []) if isinstance(b, dict)]

    # Các sự kiện CHUYỂN CHỦ, kèm khóa thời gian để chọn cái mới nhất.
    transfers: list[tuple[tuple, int, dict]] = []
    for i, b in enumerate(bds):
        if classify_bien_dong(b.get("Nội dung biến động")) == "chuyen_chu":
            d = parse_date(b.get("Thời gian"))
            # (có-ngày?, ngày, chỉ-số-mảng): ưu tiên sự kiện CÓ ngày lớn nhất; các
            # sự kiện thiếu ngày xếp theo vị trí mảng (đọc-trên-giấy).
            key = ((1, d, i) if d is not None else (0, (0, 0, 0), i))
            transfers.append((key, i, b))

    if transfers:
        transfers.sort(key=lambda x: x[0])
        _, idx, b = transfers[-1]
        chu = extract_recipients(b.get("Nội dung biến động"))
        co_ngay = parse_date(b.get("Thời gian")) is not None
        conf = "cao" if (chu and co_ngay) else "thap"
        return {
            "algo_version": ALGO_VERSION, "nguon": "bien_dong",
            "bien_dong_index": idx, "thoi_gian": _s(b.get("Thời gian")),
            "loai": _loai_slug(b.get("Nội dung biến động")),
            "chu": chu, "confidence": conf,
        }

    # Không có chuyển chủ → chủ giấy gốc.
    chu = _chu_from_giay_goc(entry.get("Chủ sử dụng"))
    return {
        "algo_version": ALGO_VERSION, "nguon": "giay_goc",
        "bien_dong_index": None, "thoi_gian": "", "loai": "",
        "chu": chu, "confidence": "cao" if chu else "thap",
    }


def _loai_slug(noi_dung: Any) -> str:
    t = _norm(noi_dung)
    for kw in CHUYEN_CHU:
        if kw in t:
            return kw.replace(" ", "_")
    return ""


def chu_cuoi_for_result(result: dict) -> list[dict]:
    """Một chu_cuoi cho mỗi entry 'Đăng ký' của result."""
    if not isinstance(result, dict):
        return []
    return [chu_cuoi_for_entry(e) for e in result.get("Đăng ký") or [] if isinstance(e, dict)]


# ── PURE smoke (dữ liệu BỊA theo pattern thật — không PII) ───────────────────

def _smoke() -> None:
    # F2+F1: mảng KHÔNG theo thời gian; sự kiện cuối là thế chấp; chuyển chủ thật
    # (thừa kế) mới nhất theo NGÀY. Phải lấy thừa kế, không lấy thế chấp.
    e1 = {"Chủ sử dụng": [{"Tên chủ": "Trần Văn A"}, {"Tên chủ": "Lê Thị B"}],
          "Biến động": [
              {"Thời gian": "21/01/2021",
               "Nội dung biến động": "Thừa kế quyền sử dụng đất cho bà Lê Thị B, "
               "sinh năm 1961, CCCD số 001161008960, địa chỉ thường trú: số 1 phố X, "
               "phường Y, quận Z, Hà Nội theo bản thỏa thuận."},
              {"Thời gian": "31/01/07", "Nội dung biến động": "ĐK thế chấp tại ngân hàng NN."},
              {"Thời gian": "22/4/2010", "Nội dung biến động": "Đã xóa ĐK thế chấp theo hồ sơ số 372u"},
          ]}
    r = chu_cuoi_for_entry(e1)
    assert r["nguon"] == "bien_dong", f"KILL [1] phải lấy từ biến động: {r}"
    assert r["loai"] == "thua_ke", f"KILL [2] loại phải là thừa kế: {r['loai']}"
    assert [c["Tên chủ"] for c in r["chu"]] == ["Lê Thị B"], f"KILL [3] chủ cuối sai: {r['chu']}"
    assert r["chu"][0]["Số giấy tờ"] == "001161008960", f"KILL [4] CCCD sai: {r['chu']}"
    assert r["confidence"] == "cao", f"KILL [5]"

    # F2: sự kiện cuối (28/11) là thế chấp; transfer thật là 13/11 → lấy 13/11, 2 chủ
    e2 = {"Chủ sử dụng": [{"Tên chủ": "Cũ C"}],
          "Biến động": [
              {"Thời gian": "11/06/2024", "Nội dung biến động": "Thế chấp tại Ngân hàng BIDV CN Đống Đa."},
              {"Thời gian": "13/11/2024",
               "Nội dung biến động": "Chuyển nhượng cho ông Nguyễn Như D, sinh năm 1984, "
               "CCCD số 020084010921, địa chỉ thường trú: Khối 11, phường Tam Thanh, "
               "thành phố Lạng Sơn và bà Nguyễn Thị E, sinh năm 1986, CCCD số 020186007484, "
               "địa chỉ thường trú: Thôn Hợp Tân, xã Gia Cát, huyện Cao Lộc theo hồ sơ số 2402005305BD"},
              {"Thời gian": "28/11/2024", "Nội dung biến động": "Thế chấp tại Ngân hàng BIDV CN Ngọc Khánh."},
          ]}
    r = chu_cuoi_for_entry(e2)
    assert r["bien_dong_index"] == 1, f"KILL [6] phải lấy sự kiện chuyển nhượng (idx 1): {r['bien_dong_index']}"
    names = [c["Tên chủ"] for c in r["chu"]]
    assert names == ["Nguyễn Như D", "Nguyễn Thị E"], f"KILL [7] tách 2 chủ sai: {names}"
    assert r["chu"][1]["Số giấy tờ"] == "020186007484", f"KILL [8] CCCD người 2 sai: {r['chu']}"

    # F6: đính chính có tên người nhưng KHÔNG đổi chủ → về giấy gốc
    e3 = {"Chủ sử dụng": [{"Tên chủ": "Trần Việt F"}, {"Tên chủ": "Kiều Thị G"}],
          "Biến động": [{"Thời gian": "14.3.05",
                         "Nội dung biến động": "Đính chính: Chủ sở hữu Ông Trần Việt F và vợ là bà Kiều Thị G"}]}
    r = chu_cuoi_for_entry(e3)
    assert r["nguon"] == "giay_goc", f"KILL [9] đính chính KHÔNG được coi là chuyển chủ: {r}"
    assert [c["Tên chủ"] for c in r["chu"]] == ["Trần Việt F", "Kiều Thị G"], f"KILL [10] {r['chu']}"

    # F5: chủ gốc dồn nhiều người trong 1 chuỗi Tên chủ, đủ kiểu ngăn cách
    for raw, want in [
        ("Bà Nguyễn Thị H và chồng: Ông Phạm Mạnh K", ["Nguyễn Thị H", "Phạm Mạnh K"]),
        ("Nguyễn Khắc L và Hoàng Thị M", ["Nguyễn Khắc L", "Hoàng Thị M"]),
        ("Bà Nỗ Thị N và chồng là Ông Nguyễn Duân P", ["Nỗ Thị N", "Nguyễn Duân P"]),
    ]:
        got = split_owner_names(raw)
        assert got == want, f"KILL [11] tách tên gốc sai: {raw!r} → {got}"

    # F6: nhiễu 'vợ chồng' người bán — chỉ 1 chủ nhận (có CCCD), bỏ đuôi
    e4 = {"Chủ sử dụng": [{"Tên chủ": "Cũ Q"}],
          "Biến động": [{"Thời gian": "01/02/2024",
                         "Nội dung biến động": "Đăng ký chuyển nhượng: Ông Lê Mai R, "
                         "CCCD: 001062004166; Nơi thường trú: Số 9, phố X, phường Y, quận Ba Đình. "
                         "Là tài sản riêng của ông Lê Mai R theo văn bản thỏa thuận đổi tài sản "
                         "riêng của vợ chồng ngày 01/02/2024."}]}
    r = chu_cuoi_for_entry(e4)
    got = [c["Tên chủ"] for c in r["chu"]]
    assert got == ["Lê Mai R"], f"KILL [12] phải đúng 1 chủ (bỏ nhiễu vợ chồng): {got}"
    assert r["chu"][0]["Số giấy tờ"] == "001062004166", f"KILL [13] CCCD: {r['chu']}"

    # F7: result rỗng → chu rỗng, thap, KHÔNG crash
    r = chu_cuoi_for_entry({"Chủ sử dụng": [], "Biến động": []})
    assert r["nguon"] == "giay_goc" and r["chu"] == [] and r["confidence"] == "thap", f"KILL [14] {r}"

    # Chỉ nghĩa vụ tài chính → không đổi chủ
    e5 = {"Chủ sử dụng": [{"Tên chủ": "Vũ Hữu S và Đoàn Thị T"}],
          "Biến động": [{"Thời gian": "29/01/2021", "Nội dung biến động": "Đã hoàn thành nghĩa vụ tài chính"}]}
    r = chu_cuoi_for_entry(e5)
    assert r["nguon"] == "giay_goc", f"KILL [15] nghĩa vụ tài chính KHÔNG đổi chủ: {r}"
    assert [c["Tên chủ"] for c in r["chu"]] == ["Vũ Hữu S", "Đoàn Thị T"], f"KILL [16] {r['chu']}"

    # A: honorific có DẤU HAI CHẤM "Ông:" / "Bà:"
    r = extract_recipients("Chuyển nhượng cho Ông: Đỗ Văn Sơn, Sinh năm: 1988, "
                           "CCCD số: 801088018893 và Vợ là bà: Phạm Thị Hà, Sinh năm: 1990, "
                           "CCCD số: 017190063472. Cả hai cùng thường trú: Đội 1, xã X.")
    got = [c["Tên chủ"] for c in r]
    assert got == ["Đỗ Văn Sơn", "Phạm Thị Hà"], f"KILL [26] anchor dấu hai chấm: {got}"
    assert r[0]["Số giấy tờ"] == "801088018893", f"KILL [27] {r}"

    # B: tên KHÔNG honorific, ngay sau 'Tặng cho', có sinh+CMND
    r = extract_recipients("Tặng cho Phạm Thị An, sinh năm 1970, CMND số 010121694, "
                           "thường trú tại phường X, quận Y.")
    assert [c["Tên chủ"] for c in r] == ["Phạm Thị An"], f"KILL [28] tên sau 'cho' không honorific: {r}"
    assert r[0]["Số giấy tờ"] == "010121694", f"KILL [29] {r}"

    # B: 'cho ... cho <Tên>, sinh năm' (nhiều 'cho', tên ở cụm cuối)
    r = extract_recipients("Tặng cho toàn bộ thửa đất cho Đỗ Văn Tình, sinh năm 1966, "
                           "CMND số 112521221, thường trú tại thôn X; theo hồ sơ số 190919-0017BĐ")
    assert [c["Tên chủ"] for c in r] == ["Đỗ Văn Tình"], f"KILL [30]: {r}"

    # C: 'cho Ông X và Bà Y' KHÔNG có ID — giữ nhờ đứng sau 'cho'/'và'
    r = extract_recipients("Đã cho tặng một phần QSDĐ theo hợp đồng số 131 ngày 24/9/2007 "
                           "cho Ông Phạm Minh Hùng và Bà Hoàng Thị Minh Thơm. Đất ở sử dụng riêng 42.5 m2.")
    assert [c["Tên chủ"] for c in r] == ["Phạm Minh Hùng", "Hoàng Thị Minh Thơm"], f"KILL [31]: {r}"

    # C: 'Cho ông X và vợ bà Y' — vợ đứng sau 'và vợ' vẫn được giữ (đồng chủ)
    r = extract_recipients("Ông Trịnh Hòa A chuyển nhượng toàn bộ nhà ở Cho ông "
                           "Nguyễn Bá B và vợ bà Đặng Thị C theo hợp đồng số 798.")
    assert [c["Tên chủ"] for c in r] == ["Nguyễn Bá B", "Đặng Thị C"], f"KILL [33] và-vợ: {r}"

    # KHÔNG bắt nhầm chữ nền thành tên (không có neo hợp lệ → rỗng)
    r = extract_recipients("Đã chuyển quyền sử dụng đất theo hợp đồng số 3115.")
    assert r == [], f"KILL [34] không được chế tên từ chữ nền: {r}"
    # KHÔNG bắt tên tổ chức sau 'cho' (Văn phòng công chứng…) — precision
    r = extract_recipients("Chuyển nhượng lập tại Văn phòng công chứng Thủ Đô số 12.")
    assert r == [], f"KILL [35] không được coi tổ chức là chủ: {r}"

    # 'Ông: Tên CCCD số:…' KHÔNG dấu phẩy — không được nuốt 'CCCD' vào tên
    r = extract_recipients("cho chủ sử dụng: Ông: Phương Văn Thao CCCD số: 001058006269")
    assert [c["Tên chủ"] for c in r] == ["Phương Văn Thao"], f"KILL [36] nuốt acronym: {r}"
    assert r[0]["Số giấy tờ"] == "001058006269", f"KILL [37] {r}"

    # F3: các định dạng ngày phải parse & so sánh đúng
    assert parse_date("31/01/07") == (2007, 1, 31), "KILL [17]"
    assert parse_date("19.4.06") == (2006, 4, 19), "KILL [18]"
    assert parse_date("30-6-04") == (2004, 6, 30), "KILL [19]"
    assert parse_date("04/2010") == (2010, 4, 0), "KILL [20]"
    assert parse_date("1979") == (1979, 0, 0), "KILL [21]"
    assert parse_date("") is None and parse_date("không rõ") is None, "KILL [22]"
    assert parse_date("21/01/2021") > parse_date("22/4/2010"), "KILL [23] so sánh ngày sai"

    # số hồ sơ dài KHÔNG bị bắt thành CCCD
    assert not RE_CCCD.search("theo hồ sơ số 9186/2004/QĐCS 20557/2004"), "KILL [24]"

    # phủ định TRƯỚC: vừa chuyển nhượng vừa xóa thế chấp → không đổi chủ
    assert classify_bien_dong("Xóa thế chấp do chuyển nhượng") == "khong_doi_chu", "KILL [25]"

    print("PASS — chu_cuoi PURE: 37 KILL case xanh")


if __name__ == "__main__":
    _smoke()
