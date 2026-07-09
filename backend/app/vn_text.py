"""Tiện ích xử lý chuỗi tiếng Việt dùng chung cho tìm kiếm không phân biệt dấu."""

import unicodedata

_DEDOT_D = str.maketrans("đĐ", "dD")


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
