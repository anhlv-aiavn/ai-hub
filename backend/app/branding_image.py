"""Validate + chuẩn hoá ảnh logo trước khi lưu — không tin ảnh do người dùng
tải lên là an toàn/hợp lệ chỉ vì tên file/Content-Type (xem
PLAN_PHASE3_menu_logo_s3dest.md §Backend 1)."""

import io

from PIL import Image

MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2MB — chặn trước khi decode (tránh decompression-bomb qua Pillow)
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}  # không SVG — có thể chứa script/foreignObject, rủi ro XSS
MIN_SIDE, MAX_SIDE = 64, 2048
MAX_ASPECT = 3.0  # cạnh dài/cạnh ngắn tối đa 3:1 — logo không phải banner
NORMALIZED_MAX_SIDE = 512  # resize lưu trữ, đủ nét cho header/login, giảm dung lượng


def validate_and_normalize_logo(data: bytes) -> bytes:
    """Raise ValueError(message tiếng Việt) nếu không hợp lệ. Trả về PNG bytes
    đã chuẩn hoá (re-encode qua Pillow — loại polyglot/payload lạ trong file gốc,
    resize cạnh dài về NORMALIZED_MAX_SIDE giữ tỉ lệ)."""
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(f"Ảnh quá lớn (tối đa {MAX_LOGO_BYTES // (1024 * 1024)}MB)")

    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()  # bắt file hỏng/giả mạo; SAU verify() ảnh không dùng lại được
    except Exception as e:  # noqa: BLE001 — bất kỳ lỗi decode nào đều là "không phải ảnh hợp lệ"
        raise ValueError("Ảnh hỏng hoặc không phải file ảnh hợp lệ") from e

    img = Image.open(io.BytesIO(data))  # mở lại lần 2 (verify() đã đóng file dùng 1 lần)
    if img.format not in ALLOWED_FORMATS:
        raise ValueError("Định dạng không hỗ trợ (chỉ PNG/JPEG/WEBP)")

    w, h = img.size
    if w < MIN_SIDE or h < MIN_SIDE or w > MAX_SIDE or h > MAX_SIDE:
        raise ValueError(f"Kích thước ảnh ngoài phạm vi cho phép ({MIN_SIDE}–{MAX_SIDE}px)")
    if max(w, h) / min(w, h) > MAX_ASPECT:
        raise ValueError("Tỉ lệ khung hình không phù hợp làm logo")

    img = img.convert("RGBA")
    scale = min(1.0, NORMALIZED_MAX_SIDE / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
