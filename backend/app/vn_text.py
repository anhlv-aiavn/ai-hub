"""Tiện ích xử lý chuỗi tiếng Việt dùng chung cho tìm kiếm không phân biệt dấu."""

import re
import unicodedata

_DEDOT_D = str.maketrans("đĐ", "dD")


def ci_pattern(s: str) -> str:
    """Dựng regex khớp `s` không phân biệt hoa/thường theo đúng nghĩa Unicode
    (kể cả chữ Việt có dấu), vì Mongo $options:"i" chỉ casefold ASCII."""
    parts = []
    for ch in s:
        lo, up = ch.lower(), ch.upper()
        # len==1 guard: vài ký tự upper()/lower() ra chuỗi nhiều ký tự (vd "ß"→"SS"),
        # lúc đó không thể gộp vào 1 character-class — giữ nguyên literal cho an toàn.
        if lo != up and len(lo) == 1 and len(up) == 1:
            parts.append(f"[{re.escape(lo)}{re.escape(up)}]")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def strip_diacritics(s: str) -> str:
    """Bỏ dấu tiếng Việt + hạ chữ thường — dùng để so khớp tìm không phân biệt
    dấu/hoa-thường (vd "nguyen van a" khớp "NGUYỄN VĂN A").

    NFD tách ký tự có dấu thành chữ cái gốc + dấu (combining mark, category
    "Mn") rồi xóa các dấu đó. Riêng "đ/Đ" không tách được qua NFD (là chữ cái
    riêng, không phải chữ + dấu) nên phải translate thủ công."""
    if not s:
        return ""
    s = s.translate(_DEDOT_D)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", s).lower()
