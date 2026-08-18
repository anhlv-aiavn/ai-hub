"""Loại TRANG TRÙNG trong một file trước khi đưa vào VLM.

Vì sao cần: kho mẫu thật có file mà MỖI TỜ được scan hai lần — một bản ảnh màu,
một bản xám (và một trong hai bị xoay 90°, đã được lớp render xoay thẳng lại).
File `Kết quả ĐK/583572.pdf` 58 trang thực chất chỉ ~29 tờ. Không loại thì tốn
GẤP ĐÔI GPU đúng trên nhóm file nặng nhất, và VLM dễ đếm một nội dung thành hai.

Cách làm: difference-hash (dHash) 16×16 = 256 bit trên ảnh xám. So byte thì vô
dụng (hai bản chụp cùng tờ khác nhau hoàn toàn ở mức byte: màu/độ sáng/nén), còn
aHash 8×8 — lựa chọn đầu tiên — thì NGƯỢC LẠI, quá thô: đo thử trên trang văn bản
tổng hợp, hai tờ KHÁC LOẠI (đơn đăng ký vs giấy xác nhận) chỉ lệch 4/64 bit vì
trang nào cũng "trắng là chính, chữ đen ở giữa" → sẽ loại nhầm trang thật. dHash
so sáng-tối giữa hai ô LIỀN KỀ nên bám bố cục chữ, ở 256 bit cho tách bạch rõ:

    cùng tờ (bản màu vs bản xám)   lệch  4–11 bit
    khác tờ                        lệch 29–37 bit

Thuần CPU, không thêm dependency (PIL đã có sẵn cho khâu render).
"""

import base64
import io
import logging

from PIL import Image

log = logging.getLogger(__name__)

N = 16                     # lưới dHash → N*N = 256 bit
NGUONG_MAC_DINH = 18       # nằm giữa "cùng tờ ≤11" và "khác tờ ≥29" đo được


def dhash(img_b64: str) -> int | None:
    """dHash 256 bit của một ảnh base64. Ảnh hỏng → None (bỏ qua, không loại)."""
    try:
        im = Image.open(io.BytesIO(base64.b64decode(img_b64)))
        im = im.convert("L").resize((N + 1, N), Image.Resampling.LANCZOS)
    except Exception as e:  # noqa: BLE001
        log.warning("dhash lỗi: %s", e)
        return None
    px = im.tobytes()      # mode "L" → đúng (N+1)*N byte, hàng nối tiếp hàng
    h = 0
    for r in range(N):
        hang = px[r * (N + 1):(r + 1) * (N + 1)]
        for c in range(N):
            h = (h << 1) | (1 if hang[c] > hang[c + 1] else 0)
    return h


def _lech(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def trang_trung(images: list[str], nguong: int = NGUONG_MAC_DINH) -> set[int]:
    """Trả tập INDEX các trang là bản lặp của một trang ĐỨNG TRƯỚC nó.

    Giữ bản xuất hiện ĐẦU TIÊN (không cố đoán bản nào "nét hơn" — thêm một tiêu chí
    đo độ nét là thêm một chỗ sai, mà hai bản scan cùng tờ thì đọc được như nhau).
    Trả INDEX chứ không trả danh sách ảnh đã lọc: `images` phải giữ nguyên vị trí vì
    `page_indices` và khâu cắt trang đều đánh chỉ số theo nó.
    """
    hashes: list[tuple[int, int]] = []   # (index, hash) của các trang được GIỮ
    bo: set[int] = set()
    for i, b64 in enumerate(images):
        h = dhash(b64)
        if h is None:
            continue
        if any(_lech(h, hg) <= nguong for _, hg in hashes):
            bo.add(i)
            continue
        hashes.append((i, h))
    if bo:
        log.info("trang_trung: loại %d/%d trang lặp", len(bo), len(images))
    return bo


# ── PURE smoke (`python -m app.anh_trung`) ──────────────────────────────────

def _smoke() -> None:
    from PIL import ImageDraw

    def to_b64(im: Image.Image) -> str:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def giay(dong: list[str], mau: bool = True, lech_px: int = 0) -> str:
        """Giả lập một trang A4 scan: nền trắng, khối chữ đen."""
        im = Image.new("RGB", (600, 800), "white")
        d = ImageDraw.Draw(im)
        for k, t in enumerate(dong):
            d.text((30 + lech_px, 40 + k * 26 + lech_px), t, fill="black")
        if not mau:   # bản scan xám, tối hơn — đúng ca gặp trong kho thật
            im = im.convert("L").point(lambda v: max(0, v - 20)).convert("RGB")
        return to_b64(im)

    DON = ["DON DANG KY DAT DAI, TAI SAN GAN LIEN VOI DAT"] + [
        f"muc {i}: Nguyen Van A, thua 12, to ban do 7, dien tich 120.5 m2" for i in range(20)]
    CCCD = ["CAN CUOC CONG DAN"] + [f"so 001099001234 - dong {i}" for i in range(6)]
    GXN = ["GIAY XAC NHAN DANG KY DAT DAI"] + [
        f"3.{i} noi dung xac nhan cua UBND xa Vinh Quynh nam 1994" for i in range(18)]

    a, a_xam, a_lech = giay(DON), giay(DON, mau=False), giay(DON, lech_px=3)
    b, c = giay(CCCD), giay(GXN)

    assert dhash(a) is not None, "KILL [1] hash ảnh hợp lệ"
    assert dhash("không phải base64!!!") is None, "KILL [2] ảnh hỏng → None, không nổ"

    d_xam = _lech(dhash(a), dhash(a_xam))
    d_lech = _lech(dhash(a), dhash(a_lech))
    assert d_xam <= NGUONG_MAC_DINH, f"KILL [3] bản màu vs bản xám cùng tờ: lệch {d_xam}"
    assert d_lech <= NGUONG_MAC_DINH, f"KILL [4] cùng tờ lệch vài px khi scan: lệch {d_lech}"

    # Chốt chặn quan trọng nhất: KHÔNG được loại nhầm hai tờ khác nội dung. Đây
    # đúng là chỗ aHash 8×8 chết (lệch 4/64) nên phải có KILL riêng.
    for ten, x in (("CCCD", b), ("giấy xác nhận", c)):
        d = _lech(dhash(a), dhash(x))
        assert d > NGUONG_MAC_DINH, f"KILL [5] đơn vs {ten} KHÔNG được coi là trùng: lệch {d}"
    assert _lech(dhash(b), dhash(c)) > NGUONG_MAC_DINH, "KILL [6] CCCD vs giấy xác nhận"

    assert trang_trung([a, a_xam, b]) == {1}, "KILL [7] loại bản lặp, giữ bản đầu"
    assert trang_trung([a, b, a_xam]) == {2}, "KILL [8] lặp không liền kề vẫn bắt được"
    assert trang_trung([a, b, c]) == set(), "KILL [9] ba tờ khác nhau → không loại gì"
    assert trang_trung([]) == set(), "KILL [10] rỗng"
    assert trang_trung([a, "hỏng", a_xam]) == {2}, "KILL [11] ảnh hỏng không chặn việc dò lặp"
    assert trang_trung([a, a_xam], nguong=0) == set(), "KILL [12] ngưỡng 0 → chỉ loại ảnh y hệt"

    print("anh_trung PURE: 12 KILL ✓")


if __name__ == "__main__":
    _smoke()
