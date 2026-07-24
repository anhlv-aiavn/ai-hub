"""Chuẩn hóa MỤC ĐÍCH SỬ DỤNG ĐẤT — logic thuần, không Mongo, không CLI.

Cùng style `normalize_dang_ky.py`: pure function để pipeline, script audit và
backfill dùng chung MỘT nguồn luật, không chép ra nhiều nơi (bài học
`groups_from_roles` từng bị chép 3 chỗ rồi sửa lệch nhau → công cụ đo báo sai
về chính production).

TRỌNG TÂM: ONT (191) vs ODT (192). Đo trên kho thật, "đất ở" chiếm 87.9% số bản
ghi mục đích, và đây cũng là chỗ DUY NHẤT text trên giấy KHÔNG đủ để quyết —
phải suy từ địa chỉ thửa.

Danh mục 82 mã lấy từ file của bên nghiệp vụ. Bảng alias/nhập nhằng bên dưới
dựng TỪ SỐ LIỆU THẬT của `app.scripts.audit_mdsdd`, không đoán: chuỗi nào danh
mục không có mã đúng (vườn, ao) thì trả ambiguous cho chuyên viên quyết.

Smoke PURE (không cần Mongo/GPU):
    docker compose exec api python -m src.extentions.multimodal.mdsdd
"""

import re

from app.vn_text import strip_diacritics

# ── DANH MỤC LOẠI ĐẤT — 82 mã, NGUỒN: file danh mục của bên nghiệp vụ ────────
# Nhúng nguyên văn (sinh từ file, không chép tay). Tên giữ đúng bản gốc kể cả
# chỗ có hai dấu cách ("Đất cơ sở  tôn giáo") — chuan_hoa gộp khoảng trắng nên
# so khớp không ảnh hưởng, còn sửa "cho đẹp" là làm lệch với nguồn.
#
# Danh mục này TRẢ LỜI ba câu hỏi treo của Phase 1:
#   · LUA (154) "Đất trồng lúa" CÓ THẬT → "Lúa" trần hết nhập nhằng.
#   · SXN (152) "Đất sản xuất nông nghiệp" CÓ THẬT → "sx nông nghiệp" ra mã.
#   · KHÔNG có mã nào cho "vườn" và "ao" → hai họ này buộc phải để chuyên viên
#     quyết, đúng như đã chốt. Không được tự chọn CLN/NTS thay họ.
LOAI_MDSDD: list[dict] = [
    {"id": 152, "ky_hieu_muc_dich": "SXN", "ten_muc_dich": "Đất sản xuất nông nghiệp"},
    {"id": 154, "ky_hieu_muc_dich": "LUA", "ten_muc_dich": "Đất trồng lúa"},
    {"id": 155, "ky_hieu_muc_dich": "LUC", "ten_muc_dich": "Đất chuyên trồng lúa"},
    {"id": 156, "ky_hieu_muc_dich": "LUK", "ten_muc_dich": "Đất trồng lúa còn lại"},
    {"id": 157, "ky_hieu_muc_dich": "LUN", "ten_muc_dich": "Đất trồng lúa nương"},
    {"id": 161, "ky_hieu_muc_dich": "HNK", "ten_muc_dich": "Đất trồng cây hằng năm khác"},
    {"id": 162, "ky_hieu_muc_dich": "BHK", "ten_muc_dich": "Đất bằng trồng cây hàng năm khác"},
    {"id": 163, "ky_hieu_muc_dich": "NHK", "ten_muc_dich": "Đất nương rẫy trồng cây hàng năm khác"},
    {"id": 164, "ky_hieu_muc_dich": "CLN", "ten_muc_dich": "Đất trồng cây lâu năm"},
    {"id": 165, "ky_hieu_muc_dich": "LNC", "ten_muc_dich": "Đất trồng cây công nghiệp lâu năm"},
    {"id": 166, "ky_hieu_muc_dich": "LNQ", "ten_muc_dich": "Đất trồng cây ăn quả lâu năm"},
    {"id": 167, "ky_hieu_muc_dich": "LNK", "ten_muc_dich": "Đất trồng cây lâu năm khác"},
    {"id": 169, "ky_hieu_muc_dich": "RSX", "ten_muc_dich": "Đất rừng sản xuất"},
    {"id": 170, "ky_hieu_muc_dich": "RSN", "ten_muc_dich": "Trong đó: Đất rừng sản xuất là rừng tự nhiên"},
    {"id": 171, "ky_hieu_muc_dich": "RST", "ten_muc_dich": "Đất có rừng trồng sản xuất"},
    {"id": 172, "ky_hieu_muc_dich": "RSK", "ten_muc_dich": "Đất khoanh nuôi phục hồi rừng sản xuất"},
    {"id": 173, "ky_hieu_muc_dich": "RSM", "ten_muc_dich": "Đất trồng rừng sản xuất"},
    {"id": 174, "ky_hieu_muc_dich": "RPH", "ten_muc_dich": "Đất rừng phòng hộ"},
    {"id": 175, "ky_hieu_muc_dich": "RPN", "ten_muc_dich": "Đất có rừng tự nhiên phòng hộ"},
    {"id": 176, "ky_hieu_muc_dich": "RPT", "ten_muc_dich": "Đất có rừng trồng phòng hộ"},
    {"id": 177, "ky_hieu_muc_dich": "RPK", "ten_muc_dich": "Đất khoanh nuôi phục hồi rừng phòng hộ"},
    {"id": 178, "ky_hieu_muc_dich": "RPM", "ten_muc_dich": "Đất trồng rừng phòng hộ"},
    {"id": 179, "ky_hieu_muc_dich": "RDD", "ten_muc_dich": "Đất rừng đặc dụng"},
    {"id": 180, "ky_hieu_muc_dich": "RDN", "ten_muc_dich": "Đất có rừng tự nhiên đặc dụng"},
    {"id": 181, "ky_hieu_muc_dich": "RDT", "ten_muc_dich": "Đất có rừng trồng đặc dụng"},
    {"id": 182, "ky_hieu_muc_dich": "RDK", "ten_muc_dich": "Đất khoanh nuôi phục hồi rừng đặc dụng"},
    {"id": 183, "ky_hieu_muc_dich": "RDM", "ten_muc_dich": "Đất trồng rừng đặc dụng"},
    {"id": 184, "ky_hieu_muc_dich": "NTS", "ten_muc_dich": "Đất nuôi trồng thuỷ sản"},
    {"id": 185, "ky_hieu_muc_dich": "TSL", "ten_muc_dich": "Đất nuôi trồng thuỷ sản nước lợ, mặn"},
    {"id": 186, "ky_hieu_muc_dich": "TSN", "ten_muc_dich": "Đất nuôi trồng thuỷ sản nước ngọt"},
    {"id": 187, "ky_hieu_muc_dich": "LMU", "ten_muc_dich": "Đất làm muối"},
    {"id": 188, "ky_hieu_muc_dich": "NKH", "ten_muc_dich": "Đất nông nghiệp khác"},
    {"id": 191, "ky_hieu_muc_dich": "ONT", "ten_muc_dich": "Đất ở tại nông thôn"},
    {"id": 192, "ky_hieu_muc_dich": "ODT", "ten_muc_dich": "Đất ở tại đô thị"},
    {"id": 193, "ky_hieu_muc_dich": "CDG", "ten_muc_dich": "Đất chuyên dùng"},
    {"id": 194, "ky_hieu_muc_dich": "CTS", "ten_muc_dich": "Đất trụ sở cơ quan, công trình sự nghiệp"},
    {"id": 204, "ky_hieu_muc_dich": "TSC", "ten_muc_dich": "Đất xây dựng trụ sở cơ quan"},
    {"id": 205, "ky_hieu_muc_dich": "TSK", "ten_muc_dich": "Đất trụ sở khác"},
    {"id": 206, "ky_hieu_muc_dich": "CQP", "ten_muc_dich": "Đất quốc phòng"},
    {"id": 207, "ky_hieu_muc_dich": "CAN", "ten_muc_dich": "Đất an ninh"},
    {"id": 209, "ky_hieu_muc_dich": "SKK", "ten_muc_dich": "Đất khu công nghiệp"},
    {"id": 210, "ky_hieu_muc_dich": "SKC", "ten_muc_dich": "Đất cơ sở sản xuất phi nông nghiệp"},
    {"id": 213, "ky_hieu_muc_dich": "SKS", "ten_muc_dich": "Đất sử dụng cho hoạt động khoáng sản"},
    {"id": 219, "ky_hieu_muc_dich": "SKX", "ten_muc_dich": "Đất sản xuất vật liệu xây dựng, làm đồ gốm"},
    {"id": 225, "ky_hieu_muc_dich": "DGT", "ten_muc_dich": "Đất công trình giao thông"},
    {"id": 228, "ky_hieu_muc_dich": "DTL", "ten_muc_dich": "Đất công trình thủy lợi"},
    {"id": 231, "ky_hieu_muc_dich": "DNL", "ten_muc_dich": "Đất công trình năng lượng"},
    {"id": 236, "ky_hieu_muc_dich": "DBV", "ten_muc_dich": "Đất công trình hạ tầng bưu chính, viễn thông, công nghệ thông tin"},
    {"id": 237, "ky_hieu_muc_dich": "DVH", "ten_muc_dich": "Đất xây dựng cơ sở văn hóa"},
    {"id": 238, "ky_hieu_muc_dich": "DYT", "ten_muc_dich": "Đất xây dựng cơ sở y tế"},
    {"id": 239, "ky_hieu_muc_dich": "DGD", "ten_muc_dich": "Đất xây dựng cơ sở giáo dục và đào tạo"},
    {"id": 240, "ky_hieu_muc_dich": "DTT", "ten_muc_dich": "Đất xây dựng cơ sở thể dục, thể thao"},
    {"id": 241, "ky_hieu_muc_dich": "DKH", "ten_muc_dich": "Đất xây dựng cơ sở khoa học và công nghệ"},
    {"id": 242, "ky_hieu_muc_dich": "DXH", "ten_muc_dich": "Đất xây dựng cơ sở dịch vụ xã hội"},
    {"id": 243, "ky_hieu_muc_dich": "DCH", "ten_muc_dich": "Đất chợ dân sinh, chợ đầu mối"},
    {"id": 247, "ky_hieu_muc_dich": "DDT", "ten_muc_dich": "Đất có di tích"},
    {"id": 248, "ky_hieu_muc_dich": "DRA", "ten_muc_dich": "Đất bãi thải, xử lý chất thải"},
    {"id": 250, "ky_hieu_muc_dich": "TON", "ten_muc_dich": "Đất cơ sở  tôn giáo"},
    {"id": 251, "ky_hieu_muc_dich": "TIN", "ten_muc_dich": "Đất cơ sở  tín ngưỡng"},
    {"id": 252, "ky_hieu_muc_dich": "NTD", "ten_muc_dich": "Đất nghĩa trang, nhà tang lễ, cơ sở hỏa táng; đất cơ sở lưu trữ tro cốt"},
    {"id": 254, "ky_hieu_muc_dich": "SON", "ten_muc_dich": "Đất có mặt nước dạng sông, ngòi, kênh, rạch, suối"},
    {"id": 270, "ky_hieu_muc_dich": "MNC", "ten_muc_dich": "Đất có mặt nước chuyên dùng"},
    {"id": 271, "ky_hieu_muc_dich": "PNK", "ten_muc_dich": "Đất phi nông nghiệp khác"},
    {"id": 273, "ky_hieu_muc_dich": "BCS", "ten_muc_dich": "Đất bằng chưa sử dụng"},
    {"id": 274, "ky_hieu_muc_dich": "DCS", "ten_muc_dich": "Đất đồi núi chưa sử dụng"},
    {"id": 275, "ky_hieu_muc_dich": "NCS", "ten_muc_dich": "Núi đá không có rừng cây"},
    {"id": 276, "ky_hieu_muc_dich": "MVB", "ten_muc_dich": "Đất có mặt nước ven biển"},
    {"id": 277, "ky_hieu_muc_dich": "MVT", "ten_muc_dich": "Đất mặt nước ven biển nuôi trồng thuỷ sản"},
    {"id": 278, "ky_hieu_muc_dich": "MVR", "ten_muc_dich": "Đất mặt nước ven biển có rừng ngập mặn"},
    {"id": 279, "ky_hieu_muc_dich": "MVK", "ten_muc_dich": "Đất mặt nước ven biển có mục đích khác"},
    {"id": 281, "ky_hieu_muc_dich": "DTS", "ten_muc_dich": "Đất xây dựng trụ sở của tổ chức sự nghiệp"},
    {"id": 282, "ky_hieu_muc_dich": "DNG", "ten_muc_dich": "Đất xây dựng cơ sở ngoại giao"},
    {"id": 283, "ky_hieu_muc_dich": "DSK", "ten_muc_dich": "Đất xây dựng công trình sự nghiệp khác"},
    {"id": 284, "ky_hieu_muc_dich": "SKN", "ten_muc_dich": "Đất cụm công nghiệp"},
    {"id": 285, "ky_hieu_muc_dich": "SKT", "ten_muc_dich": "Đất khu chế xuất"},
    {"id": 286, "ky_hieu_muc_dich": "TMD", "ten_muc_dich": "Đất thương mại, dịch vụ"},
    {"id": 287, "ky_hieu_muc_dich": "DDL", "ten_muc_dich": "Đất có danh lam thắng cảnh"},
    {"id": 288, "ky_hieu_muc_dich": "DSH", "ten_muc_dich": "Đất sinh hoạt cộng đồng"},
    {"id": 289, "ky_hieu_muc_dich": "DKV", "ten_muc_dich": "Đất khu vui chơi, giải trí công cộng"},
    {"id": 290, "ky_hieu_muc_dich": "DCK", "ten_muc_dich": "Đất công trình công cộng khác"},
    {"id": 295, "ky_hieu_muc_dich": "KĐD", "ten_muc_dich": "Đất cơ sở bảo tồn đa dạng sinh học"},
    {"id": 296, "ky_hieu_muc_dich": "CNC", "ten_muc_dich": "Đất khu công nghệ cao"},
]
_THEO_KY_HIEU = {m["ky_hieu_muc_dich"]: m for m in LOAI_MDSDD}
_MA_DAT_O = ("ONT", "ODT")

METHODS = ("ky_hieu_ngoac", "ky_hieu_token", "ten_exact", "alias",
           "dia_chi_xa", "dia_chi_huyen", "dia_chi_thon", "dia_chi_ten_huyen")

# Lý do KHÔNG ra mã — phân biệt được ba loại là quan trọng, vì mỗi loại xử khác
# nhau: nhập nhằng → chuyên viên quyết · lẫn cột → sửa prompt extract · chưa map
# → bổ sung alias. Gộp chung một rọ "chưa map" thì không biết đường nào mà lần.
LY_DO = ("nhap_nhang", "khong_phai_muc_dich", "chua_map")

# Ký hiệu trong ngoặc: "Đất ở tại nông thôn (ONT)" — dạng chính xác nhất.
_RE_NGOAC = re.compile(r"\(\s*([A-Za-z]{2,4})\s*\)")
# Ký hiệu đứng riêng: bắt buộc HOA hết, tránh nuốt chữ thường ngẫu nhiên.
_RE_TOKEN_HOA = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")

# ── SUY TỪ ĐỊA CHỈ THỬA — 3 BẬC, chắc chắn giảm dần ─────────────────────────
# (mọi regex so trên text ĐÃ BỎ DẤU + lower)
#
# NGUYÊN TẮC CHUNG: tiền tố hành chính phải đứng ĐẦU MỘT ĐOẠN địa chỉ
# ("…, xã Kiêu Kỵ, …"), nên chỉ khớp ở đầu đoạn sau khi tách theo dấu phân
# cách. Khớp lỏng giữa đoạn là sai — đo trên dữ liệu thật:
#   · "Quang Tiến"  chứa "quan"  → ra nhầm QUẬN → ODT
#   · "thôn Phượng" chứa "phuong"→ ra nhầm PHƯỜNG → ODT
#   · "thị xã"      chứa "xa"    → ra nhầm XÃ → ONT (thị xã là cấp HUYỆN)
# Ràng buộc đầu-đoạn loại sạch cả ba lớp lỗi này mà không cần lookbehind chắp vá.
# ":" là dấu phân cách thật trong dữ liệu: "Thôn: Nguyệt, xã: Ứng Hòa, huyện: Ứng Hòa"
_TACH_DOAN = re.compile(r"[,;:/()\-–—]+")

# BẬC 1 — CẤP XÃ, đơn vị TRỰC TIẾP của thửa. Chắc nhất.
#   Viết tắt lấy từ dữ liệu thật: "X. Mai Đình", "TT Trâu Quỳ" (KHÔNG có dấu
#   chấm — đây chính là lý do phần lớn ca "không quyết được" ở audit 0.3).
_RE_CAP_XA = re.compile(
    r"^(?:(?P<thi_tran>thi tran(?=\s|$)|tt\.?(?=\s|$))"
    r"|(?P<phuong>phuong(?=\s|$)|p\.)"
    r"|(?P<xa>xa(?=\s|$)|x\.))"
)
# BẬC 2 — CẤP HUYỆN, dùng khi địa chỉ KHÔNG ghi cấp xã (rất phổ biến: ghi tên
#   thôn + tên xã trần rồi mới tới "huyện X"). Suy được nhờ cấu trúc hành chính:
#     · "quận"  chỉ chứa PHƯỜNG          → chắc chắn ODT
#     · "huyện" chỉ chứa XÃ và THỊ TRẤN  → thị trấn đã bị bậc 1 bắt trước rồi,
#       nên tới đây gần như chắc là xã   → ONT
#   "thị xã"/"thành phố" chứa CẢ phường lẫn xã → KHÔNG suy được, để ambiguous.
_RE_CAP_HUYEN = re.compile(
    r"^(?:(?P<quan>quan(?=\s|$)|q\.)|(?P<huyen>huyen(?=\s|$)|h\.))")
# BẬC 3 — LOẠI ĐIỂM DÂN CƯ. Yếu nhất, nhưng cứu được ca địa chỉ cụt
#   ("Xóm 2, Đốc Thượng" — không có cấp xã lẫn cấp huyện).
#   thôn/xóm/ấp/buôn là đơn vị của NÔNG THÔN; "tổ dân phố" là của đô thị.
#   KHÔNG dùng "phố" trần: "phố Thạch Lối, Thanh Xuân, Sóc Sơn" là TÊN ĐƯỜNG ở
#   xã nông thôn — bắt "phố" sẽ ra ODT sai.
_RE_DIEM_DAN_CU = re.compile(
    r"^(?:(?P<to_dan_pho>to dan pho|khu pho|tdp(?=\s|$))"
    r"|(?P<thon>(?:thon|xom|ap|buon)(?=\s|$)))")

# BẬC 4 — TÊN ĐƠN VỊ CẤP HUYỆN VIẾT TRẦN (không kèm chữ "huyện"/"quận").
#   Chiếm gần hết phần "không quyết được" còn lại của audit 0.3: "Thắng Lợi,
#   Phú Minh, Sóc Sơn, Hà Nội" — Sóc Sơn là HUYỆN, chỉ là không ai ghi chữ
#   "huyện". Danh mục dưới đây là ĐỊA BÀN THẬT của kho này (VPĐK Hà Nội), không
#   phải suy đoán; triển khai tỉnh khác thì thay danh mục, luật giữ nguyên.
#
#   QUÉT TỪ CUỐI ĐỊA CHỈ NGƯỢC LÊN, vì tên cấp huyện đứng sát tên tỉnh. Bắt
#   buộc phải vậy do TRÙNG TÊN có thật giữa các cấp: "Kim Anh, Thanh Xuân, Sóc
#   Sơn, Hà Nội" — Thanh Xuân ở đây là XÃ của huyện Sóc Sơn, không phải quận
#   Thanh Xuân. Quét xuôi sẽ ra ODT sai; quét ngược gặp "Sóc Sơn" trước → ONT.
#
#   CỐ Ý BỎ RA: Từ Liêm (huyện → 2 quận năm 2013), Sơn Tây, Hà Đông (thị xã →
#   quận). Giấy cũ và địa giới nay khác nhau ⇒ để ambiguous cho người quyết,
#   đúng nguyên tắc thà rà tay còn hơn gán sai mã pháp lý.
_HUYEN = {
    "soc son", "dong anh", "gia lam", "thanh tri", "ba vi", "chuong my",
    "dan phuong", "hoai duc", "me linh", "my duc", "phu xuyen", "phuc tho",
    "quoc oai", "thach that", "thanh oai", "thuong tin", "ung hoa",
}
_QUAN = {
    "ba dinh", "hoan kiem", "tay ho", "long bien", "cau giay", "dong da",
    "hai ba trung", "hoang mai", "thanh xuan", "nam tu liem", "bac tu liem",
}

_DVHC_MA = {
    "phuong": "ODT", "thi_tran": "ODT", "xa": "ONT",
    "quan": "ODT", "huyen": "ONT",
    "to_dan_pho": "ODT", "thon": "ONT",
    "ten_quan": "ODT", "ten_huyen": "ONT",
}
_BAC = (
    (_RE_CAP_XA, "dia_chi_xa", 0.9),
    (_RE_CAP_HUYEN, "dia_chi_huyen", 0.8),
    (_RE_DIEM_DAN_CU, "dia_chi_thon", 0.75),
)


def chuan_hoa(s) -> str:
    """Text so khớp: bỏ dấu + lower + gộp khoảng trắng (kể cả xuống dòng)."""
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    return re.sub(r"\s+", " ", strip_diacritics(s)).strip()


def don_vi_hanh_chinh(dia_chi) -> str | None:
    """→ "phuong" | "thi_tran" | "xa" | None — cấp XÃ trực tiếp của thửa (bậc 1).

    Lấy đoạn KHỚP ĐẦU TIÊN vì địa chỉ VN viết nhỏ → lớn: "Thôn 5, xã Ea Tu,
    thành phố Buôn Ma Thuột" là NÔNG THÔN, dù có chữ "thành phố" phía sau.
    """
    for doan in _doan(dia_chi):
        m = _RE_CAP_XA.match(doan)
        if m:
            return m.lastgroup
    return None


def _doan(dia_chi) -> list[str]:
    """Tách địa chỉ thành các đoạn, giữ nguyên thứ tự nhỏ → lớn."""
    return [d.strip() for d in _TACH_DOAN.split(chuan_hoa(dia_chi)) if d.strip()]


def suy_tu_dia_chi(dia_chi) -> dict | None:
    """Suy ONT/ODT từ địa chỉ thửa theo 3 bậc, dừng ở bậc CHẮC NHẤT bắt được.

    → {"ky_hieu", "dvhc", "method", "score"} hoặc None nếu không bậc nào bắt được.
    Bậc thấp KHÔNG được đè bậc cao: quét hết mọi đoạn ở bậc 1 rồi mới xuống bậc 2.
    "Tổ dân phố An Đào, xã Kiêu Kỵ" phải ra ONT theo "xã" (bậc 1), không ra ODT
    theo "tổ dân phố" (bậc 3) dù cụm đó đứng trước.
    """
    doans = _doan(dia_chi)
    if not doans:
        return None
    for rx, method, score in _BAC:
        for doan in doans:
            m = rx.match(doan)
            if m:
                dv = m.lastgroup
                return {"ky_hieu": _DVHC_MA[dv], "dvhc": dv,
                        "method": method, "score": score}

    # Bậc 4 — tên cấp huyện viết trần. Quét NGƯỢC (xem chú thích _HUYEN).
    for doan in reversed(doans):
        dv = "ten_huyen" if doan in _HUYEN else "ten_quan" if doan in _QUAN else None
        if dv:
            return {"ky_hieu": _DVHC_MA[dv], "dvhc": dv,
                    "method": "dia_chi_ten_huyen", "score": 0.7}
    return None


# ── BẢNG TRA, dựng TỪ TOP-40 CHUỖI THẬT của audit 0.3 (không đoán) ──────────
# Khóa đã chuẩn hóa; so khớp TUYỆT ĐỐI cả chuỗi, không khớp một phần — "Đất
# trồng cây" khác "Đất trồng cây lâu năm" đúng ở chỗ quyết định mã.
ALIAS: dict[str, str] = {
    "dat trong cay lau nam": "CLN", "trong cay lau nam": "CLN", "cln": "CLN",
    "dat nuoi trong thuy san": "NTS", "nuoi trong thuy san": "NTS",
    "dat giao thong": "DGT",
    "dat thuy loi": "DTL",
    # LUA là mã CHA của lúa. "Đất chuyên trồng lúa NƯỚC" là tên hệ cũ của LUC
    # (danh mục hiện hành ghi "Đất chuyên trồng lúa") nên vẫn cần alias.
    "dat chuyen trong lua nuoc": "LUC", "dat trong lua nuoc con lai": "LUK",
    "dat san xuat nong nghiep": "SXN", "san xuat nong nghiep": "SXN",
    "sx nong nghiep": "SXN", "dat trong nong nghiep": "SXN",
    "dat nghia trang": "NTD", "dat nghia trang, nghia dia": "NTD",
    "dat co so ton giao": "TON", "dat co so tin nguong": "TIN",
    "dat cho": "DCH",
    # viết tắt có thật trên giấy
    # TCLN = viết tắt "trồng cây lâu năm". KHÔNG map "…lâu năm khác" về CLN:
    # đó là LNK (167), một mã riêng trong danh mục.
    "dat tcln": "CLN", "tcln": "CLN",
    "dat trong thuy san": "NTS",   # phủ cả "thuỷ" vì khóa đã bỏ dấu
}

# NHẬP NHẰNG THẬT — có mặt nhiều trong kho nhưng KHÔNG suy ra được đúng một mã.
# Trả ambiguous kèm lý do để chuyên viên quyết, TUYỆT ĐỐI không chọn bừa:
#   · "đất vườn"/"vườn"/"ao" — danh mục KHÔNG có mã "vườn"; tùy thửa mà là CLN
#     hay BHK (vườn) / NTS hay MNC (ao). Chọn hộ là gán sai mã pháp lý.
#   · "lúa" trần — không phân biệt được LUC (chuyên trồng) với LUK (còn lại).
#   · "rừng" trần — RSX/RPH/RDD là ba chế độ pháp lý khác hẳn nhau.
#   · "đất nông nghiệp"/"sản xuất nông nghiệp" — tên NHÓM đất, không phải mã.
NHAP_NHANG: set[str] = {
    "dat vuon", "vuon", "dat vuon, ao", "dat ao", "ao", "vuon, ao",
    "dat trong cay", "trong cay",
    "rung", "dat nong nghiep",
    "dat khac", "khac", "dat", "dat khu vuc nong thon", "dat nong thon",
    "dat ven truc giao thong", "kinh te gia dinh", "dat lam kho",
}

# KHÔNG PHẢI MỤC ĐÍCH — giá trị của CỘT KHÁC bị VLM nhét nhầm sang "Loại mục
# đích". Đếm riêng vì cách sửa khác hẳn: sửa prompt extract, không phải bổ sung
# alias. Gộp vào "chưa map" sẽ làm tưởng bảng mã còn thiếu.
KHONG_PHAI_MUC_DICH: set[str] = {
    "su dung chung", "su dung rieng", "rieng", "chung", "dien tich su dung chung",
    "lau dai", "10%", "su dung dat", "duong di chuyen",
    "dat duoc giao hoac thue: duoc giao", "dat duoc giao hoac thue",
}

# ĐUÔI DÀI: audit đo được 404 bản ghi "chưa map" trải trên 281 chuỗi, đầu bảng
# chỉ 9 lần — liệt kê từng chuỗi là vô vọng và sẽ phải nuôi tay mãi. Nhưng đọc
# thì chúng thuộc vài HỌ lặp lại ("Lúa nước", "Lúa lai", "Lúa dài", "Lúa đời"…).
# Bắt theo họ phủ được cả đuôi lẫn các biến thể chưa từng gặp.
#
# Hai regex này chỉ chạy SAU khi mọi phép khớp tuyệt đối đã trượt, nên không
# bao giờ cướp mã của chuỗi đã map được ("Đất trồng cây lâu năm khác" → CLN
# qua ALIAS, dù nó cũng chứa "trồng cây").
_RE_HO_LAN_COT = re.compile(
    r"\b(su dung (chung|rieng|khac|rong)|dien tich|duoc giao|thue dat"
    r"|khong duoc cap|lao dong)\b")
_RE_HO_NHAP_NHANG = re.compile(
    r"\b(vuon|ao|rung|lam nghiep|nong nghiep|trong cay|trong rung)\b")

# HỌ RA MÃ — khác hai regex trên: đây là họ mà danh mục CÓ mã cha, nên gom được
# mà không phải đoán. Cả đuôi dài "Lúa nước / Lúa lai / Lúa dài / Lúa đời /
# Lúa nước 2 năm" đều là lúa, và LUA (154) chính là mã cha của LUC/LUK/LUN.
# Chỉ chạy sau mọi khớp tuyệt đối, nên "Đất chuyên trồng lúa" vẫn ra LUC.
_HO_RA_MA: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\blua\b"), "LUA"),
)


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
    """Chuỗi mục đích này có phải "đất ở" không (mọi biến thể).

    Danh sách đồng nghĩa lấy TỪ SỐ LIỆU THẬT của audit 0.3, không đoán:
    "thổ cư" là cách gọi cũ của đất ở (giấy trước 2003, còn 219 bản ghi/20k hồ
    sơ mẫu); "nhà ở"/"chỗ ở"/"làm nhà ở" cũng là mục đích để ở.

    Riêng "ở" trần chỉ nhận khi là TOÀN BỘ chuỗi — đây là ca VLM bóc cụt "đất
    ở"; nếu khớp lỏng thì "ở" sẽ dính vào vô số chuỗi khác.
    """
    t = chuan_hoa(text)
    if not t:
        return False
    if t == "o" or re.search(r"\bdat o\b|\btho cu\b|\bnha o\b|\bcho o\b", t):
        return True
    kh = ky_hieu_trong_text(text)
    # CHỈ ONT/ODT — không phải cả danh mục. Dùng _THEO_KY_HIEU ở đây là bẫy:
    # danh mục mở rộng thì "Đất trồng cây lâu năm (CLN)" hóa thành "đất ở".
    return bool(kh and kh[0] in _MA_DAT_O)


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
    # CHỈ ONT/ODT — cùng bẫy như la_dat_o: chuỗi đa mục đích "đất ở, CLN" vào
    # được nhánh này, nếu nhận cả danh mục thì nó trả về CLN cho một bản ghi
    # đất ở. Mã ngoài đất ở là việc của map_muc_dich.
    if kh and kh[0] in _MA_DAT_O:
        return _kq(kh[0], kh[1], 1.0)

    t = chuan_hoa(text)
    if "nong thon" in t:
        return _kq("ONT", "ten_exact", 1.0)
    if "do thi" in t:
        return _kq("ODT", "ten_exact", 1.0)

    dc = suy_tu_dia_chi(dia_chi)
    if dc:
        # score < 1.0: đây là SUY DẪN từ địa chỉ, không phải chữ trên giấy.
        return {**_kq(dc["ky_hieu"], dc["method"], dc["score"]), "dvhc": dc["dvhc"]}

    return _amb("nhap_nhang")


# tên chuẩn (đã bỏ dấu) → ký hiệu, dựng 1 lần lúc import
_THEO_TEN = {chuan_hoa(m["ten_muc_dich"]): m["ky_hieu_muc_dich"] for m in LOAI_MDSDD}
# tiền tố mã số của hệ cũ: "005-Đất khu vực nông thôn"
_RE_TIEN_TO_SO = re.compile(r"^\d+\s*[-.]\s*")
# chú thích trong ngoặc + đuôi diện tích — nhiễu quanh một mục đích ĐÚNG:
# "Đất trồng cây lâu năm (sử dụng chung)" vẫn là CLN. Không bỏ thì chuỗi trượt
# khớp tuyệt đối rồi rơi trúng regex họ, bị gán nhầm là "lẫn cột".
_RE_NGOAC_CHU_THICH = re.compile(r"\s*\([^)]*\)\s*")
_RE_DUOI_DIEN_TICH = re.compile(r"\s*:?\s*[\d.,]+\s*m2\s*$")


def _khoa(text) -> str:
    """Khóa tra bảng: chuẩn hóa + bỏ tiền tố mã số, chú thích ngoặc, đuôi diện tích.

    An toàn với ký hiệu trong ngoặc "(ONT)" vì ky_hieu_trong_text đọc text GỐC
    và chạy TRƯỚC mọi phép tra bảng.
    """
    t = _RE_TIEN_TO_SO.sub("", chuan_hoa(text))
    t = _RE_NGOAC_CHU_THICH.sub(" ", t)
    return _RE_DUOI_DIEN_TICH.sub("", t).strip()


def _amb(ly_do: str) -> dict:
    return {"id": None, "ky_hieu": None, "ten": None, "method": None,
            "score": 0.0, "ambiguous": True, "ly_do": ly_do}


def map_muc_dich(text, dia_chi: str = "") -> dict | None:
    """Chuẩn hóa MỘT chuỗi "Loại mục đích" về mã trong LOAI_MDSDD.

    → None nếu chuỗi rỗng.
    → dict có `ambiguous: True` + `ly_do` khi không ra được đúng một mã. Ba lý
      do tách bạch (xem LY_DO): nhập nhằng thật · giá trị lẫn từ cột khác · chưa
      có trong bảng alias. Mỗi loại có cách xử khác nhau nên không được gộp.

    Thứ tự khớp theo độ chắc giảm dần; đất ở tách riêng vì còn phải suy ONT/ODT
    từ địa chỉ thửa (xem map_dat_o).
    """
    t = _khoa(text)
    if not t:
        return None

    if la_dat_o(text):
        return map_dat_o(text, dia_chi)

    kh = ky_hieu_trong_text(text)
    if kh and kh[0] in _THEO_KY_HIEU:
        return _kq(kh[0], kh[1], 1.0)

    if t in _THEO_TEN:
        return _kq(_THEO_TEN[t], "ten_exact", 1.0)
    if t in ALIAS:
        return _kq(ALIAS[t], "alias", 0.95)
    if t in KHONG_PHAI_MUC_DICH:
        return _amb("khong_phai_muc_dich")
    if t in NHAP_NHANG:
        return _amb("nhap_nhang")
    # bắt theo HỌ — chỉ tới đây khi mọi khớp tuyệt đối đã trượt
    if _RE_HO_LAN_COT.search(t):
        return _amb("khong_phai_muc_dich")
    for rx, ky in _HO_RA_MA:
        if rx.search(t):
            return _kq(ky, "alias", 0.85)
    if _RE_HO_NHAP_NHANG.search(t):
        return _amb("nhap_nhang")
    return _amb("chua_map")


# ── GẮN MÃ VÀO extractions ──────────────────────────────────────────────────
# Đặt THẲNG vào từng mục đích trong `extractions` (không phải mảng song song),
# vì grain khớp đúng chỗ và vì như vậy hai cột xuất ra tự động đi qua
# `apply_overrides`: chuyên viên sửa mã trong form hậu kiểm là CSV đúng ngay,
# không phải chờ backfill chạy lại. Thêm KEY anh em không dịch index nào nên
# mọi path trong `review.overrides` vẫn trỏ đúng chỗ cũ.
# v2: nhúng danh mục 82 mã thật → cột Mã đổi từ ký hiệu sang SỐ ID, và nhiều
# chuỗi trước đây nhập nhằng nay ra mã (LUA/SXN). Doc đã backfill ở v1 phải
# tính lại — bump version là backfill tự nhặt đúng nhóm đó.
ALGO_VERSION = 2
KEY_MA = "Mã MĐSD"
KEY_TEN = "MĐSD chuẩn"


def _hien_thi(r: dict) -> tuple:
    """(giá trị cột Mã, giá trị cột MĐSD chuẩn) cho 1 kết quả map.

    Mã = SỐ ID của danh mục (một kiểu dữ liệu, hợp cho FME). Ký hiệu không mất:
    nó nằm ngay đầu cột "MĐSD chuẩn" ("ONT - Đất ở tại nông thôn"), là dạng
    người đọc nhận ra ngay trên giấy.
    """
    if r.get("ambiguous") or not r.get("ky_hieu"):
        return "", ""
    return r["id"], f"{r['ky_hieu']} - {r['ten']}"


def duyet_muc_dich(records):
    """Duyệt mọi mục đích trong `extractions`, trả (ri, ei, ti, mi, md, địa chỉ thửa).

    Một giấy có nhiều thửa, mỗi thửa nhiều mục đích → phải đủ 4 chỉ số mới định
    vị được một ô. Địa chỉ lấy ở tầng THỬA vì luật ONT/ODT suy từ đó.
    """
    for ri, rec in enumerate(records or []):
        if not isinstance(rec, dict) or not isinstance(rec.get("result"), dict):
            continue
        for ei, entry in enumerate(rec["result"].get("Đăng ký") or []):
            if not isinstance(entry, dict):
                continue
            for ti, thua in enumerate(entry.get("Thửa đất") or []):
                if not isinstance(thua, dict):
                    continue
                dia_chi = thua.get("Địa chỉ") or ""
                for mi, md in enumerate(thua.get("Mục đích sử dụng") or []):
                    if isinstance(md, dict):
                        yield ri, ei, ti, mi, md, dia_chi


def gan_mdsdd(records) -> dict:
    """Gắn KEY_MA/KEY_TEN vào từng mục đích (SỬA TẠI CHỖ). → tóm tắt để lưu top-level.

    Tóm tắt phẳng (`can_ra_tay`, `ly_do`) là để TRUY VẤN: lọc hàng đợi rà tay
    trên 600k bằng path multikey sâu 6 tầng thì phải đánh index, bằng cờ phẳng
    thì không.

    Luôn ghi cả hai key kể cả khi rỗng — có key với giá trị "" thì form hậu kiểm
    hiện ra ô trống cho chuyên viên điền; thiếu key thì không có ô nào để điền.
    """
    n = n_ra_ma = 0
    ly_do: set[str] = set()
    for _, _, _, _, md, dia_chi in duyet_muc_dich(records):
        r = map_muc_dich(md.get("Loại mục đích"), dia_chi)
        if r is None:      # chuỗi rỗng — không bịa ô mã cho trường VLM bỏ trống
            md.pop(KEY_MA, None)
            md.pop(KEY_TEN, None)
            continue
        n += 1
        md[KEY_MA], md[KEY_TEN] = _hien_thi(r)
        if r.get("ambiguous"):
            ly_do.add(r.get("ly_do") or "chua_map")
        else:
            n_ra_ma += 1
    return {"n": n, "n_ra_ma": n_ra_ma,
            "can_ra_tay": bool(ly_do), "ly_do": sorted(ly_do)}


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
    assert r and r["id"] == 192 and r["method"] == "dia_chi_xa", \
        f"KILL [3] 'Đất ở' + phường phải ra ODT(192): {r}"
    r = map_dat_o("Đất ở", "Thôn 5, xã Ea Tu, thành phố Buôn Ma Thuột")
    assert r and r["id"] == 191 and r["method"] == "dia_chi_xa", \
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

    # 10. mục đích KHÁC đất ở → None (map_muc_dich xử), không nhận vơ
    for x in ("Đất trồng cây lâu năm (CLN)", "Đất chuyên trồng lúa nước"):
        assert map_dat_o(x, "xã Ea Tu") is None, f"KILL [10] nhận vơ mã không phải đất ở: {x}"
    # ca THẬT từ stage mdsdd_real: chuỗi đa mục đích chứa ký hiệu của mã KHÁC.
    # Nhánh đất ở không được trả về mã đó — nó phải quyết ONT/ODT như thường.
    r = map_dat_o("đất ở, CLN", "xã Cổ Bi, huyện Gia Lâm")
    assert r and r["ky_hieu"] == "ONT", f"KILL [10b] 'đất ở, CLN' phải ra ONT: {r}"

    # 11. mã trả về luôn thuộc danh mục, method luôn hợp lệ
    for txt, dc in [("Đất ở (ODT)", ""), ("Đất ở tại nông thôn", ""), ("Đất ở", "xã X")]:
        r = map_dat_o(txt, dc)
        assert r["id"] in (m["id"] for m in LOAI_MDSDD), f"KILL [11] mã ngoài danh mục: {r}"
        assert r["method"] in METHODS, f"KILL [11b] method lạ: {r}"

    # ── 12. VIẾT TẮT — lấy nguyên văn từ mẫu "không quyết được" của audit 0.3 ──
    viet_tat = [
        ("Tổ dân phố Cầu Việt, TT Trâu Quỳ, Huyện Gia Lâm", 192, "TT không dấu chấm"),
        ("S015 - Nhà ở Khu TTTQ Z125, X. Mai Đình - H. Sóc Sơn", 191, "X. viết tắt"),
        ("Khu Đ, Thị trấn Sóc Sơn - Huyện Sóc Sơn", 192, "thị trấn đầy đủ"),
        ("Số 5, P. 12, quận Gò Vấp", 192, "P. viết tắt"),
    ]
    for dc, ma, ten in viet_tat:
        r = map_dat_o("Đất ở", dc)
        assert r and r["id"] == ma, f"KILL [12] {ten}: {dc!r} → {r}"

    # ── 13. BẬC 2: địa chỉ KHÔNG ghi cấp xã, chỉ có cấp huyện ──────────────
    # huyện chỉ chứa xã + thị trấn; thị trấn đã bị bậc 1 bắt trước ⇒ còn lại là xã.
    r = map_dat_o("Đất ở", "Đồng Lợi, Quang Tiến, Huyện Sóc Sơn")
    assert r and r["id"] == 191 and r["method"] == "dia_chi_huyen", \
        f"KILL [13] huyện (không thị trấn) phải ra ONT: {r}"
    r = map_dat_o("Đất ở", "Số 8 ngõ 4, Trung Hòa, quận Cầu Giấy, Hà Nội")
    assert r and r["id"] == 192 and r["method"] == "dia_chi_huyen", \
        f"KILL [13b] quận chỉ chứa phường ⇒ ODT: {r}"
    # thị xã / thành phố chứa CẢ phường lẫn xã → KHÔNG được suy bậc 2
    r = map_dat_o("Đất ở", "Khu 3, thị xã Từ Sơn, Bắc Ninh")
    assert r and r["ambiguous"], f"KILL [13c] thị xã không được suy: {r}"

    # ── 14. BẬC 3: địa chỉ cụt, chỉ còn loại điểm dân cư ───────────────────
    r = map_dat_o("Đất ở", "Xóm 2, Đốc Thượng")
    assert r and r["id"] == 191 and r["method"] == "dia_chi_thon", f"KILL [14] xóm ⇒ ONT: {r}"
    r = map_dat_o("Đất ở", "Số 3, Ngách 3, Ngõ Nông, thôn Trung Na")
    assert r and r["id"] == 191, f"KILL [14b] thôn ⇒ ONT: {r}"
    # "phố" TRẦN không được coi là đô thị — đây là TÊN ĐƯỜNG ở xã nông thôn.
    # Địa chỉ thật: bậc 4 gặp "Sóc Sơn" ⇒ ONT. Nếu bắt "phố" thì ra ODT sai.
    r = map_dat_o("Đất ở", "phố Thạch Lối, Thanh Xuân, Sóc Sơn, Hà Nội")
    assert r and r["id"] == 191, f"KILL [14c] 'phố' trần bị đọc thành đô thị: {r}"
    r = map_dat_o("Đất ở", "phố Thạch Lối, Đốc Hậu")
    assert r and r["ambiguous"], f"KILL [14d] 'phố' trần không được suy ra ODT: {r}"

    # ── 15. BẬC KHÔNG ĐƯỢC ĐÈ NHAU: có cấp xã thì bậc 1 thắng ─────────────
    r = map_dat_o("Đất ở", "Tổ dân phố An Đào, xã Kiêu Kỵ, huyện Gia Lâm")
    assert r and r["id"] == 191 and r["method"] == "dia_chi_xa", \
        f"KILL [15] 'tổ dân phố' (bậc 3) không được đè 'xã' (bậc 1): {r}"

    # ── 16. ĐỒNG NGHĨA ĐẤT Ở lấy từ audit 0.3 ─────────────────────────────
    for txt in ("Thổ cư", "Đất thổ cư", "Thổ cư lâu dài", "Nhà ở", "Chỗ ở lâu dài", "ở"):
        r = map_dat_o(txt, "xã Phù Lỗ, huyện Sóc Sơn")
        assert r and r["id"] == 191, f"KILL [16] {txt!r} phải được nhận là đất ở: {r}"
    # nhưng KHÔNG nhận vơ các chuỗi rác cùng nhóm
    for txt in ("Sử dụng chung", "Sử dụng riêng", "Riêng", "Chung", "10%", "Vườn"):
        assert map_dat_o(txt, "xã Phù Lỗ") is None, f"KILL [16b] nhận vơ chuỗi rác: {txt!r}"

    # ── 17. BẬC 4: tên cấp huyện viết TRẦN (mẫu thật từ audit 0.3) ────────
    r = map_dat_o("Đất ở", "Thắng Lợi, Phú Minh, Sóc Sơn, Hà Nội")
    assert r and r["id"] == 191 and r["method"] == "dia_chi_ten_huyen", \
        f"KILL [17] tên huyện trần phải ra ONT: {r}"
    r = map_dat_o("Đất ở", "Khu Tập Thể 230- Cổ Bi- Gia Lâm- Hà Nội")
    assert r and r["id"] == 191, f"KILL [17b] {r}"

    # BẪY TRÙNG TÊN: "Thanh Xuân" vừa là QUẬN Hà Nội vừa là XÃ của huyện Sóc
    # Sơn. Quét ngược từ cuối phải gặp "Sóc Sơn" trước ⇒ ONT, không phải ODT.
    r = map_dat_o("Đất ở", "Kim Anh, Thanh Xuân, Sóc Sơn, Hà Nội")
    assert r and r["id"] == 191, f"KILL [17c] trùng tên xã/quận — phải quét ngược: {r}"
    # còn khi Thanh Xuân đứng đúng vị trí cấp huyện thì vẫn ra ODT
    r = map_dat_o("Đất ở", "Số 5 Nguyễn Trãi, Thanh Xuân, Hà Nội")
    assert r and r["id"] == 192, f"KILL [17d] {r}"

    # Địa bàn CỐ Ý bỏ ra khỏi danh mục (đổi cấp theo thời gian) → không được đoán
    for dc in ("Tây Tựu, Từ Liêm, Hà Nội", "Khu 5, Sơn Tây, Hà Nội"):
        r = map_dat_o("Đất ở", dc)
        assert r and r["ambiguous"], f"KILL [17e] địa bàn đã đổi cấp không được đoán: {dc} → {r}"

    # ── 18. Dấu hai chấm cũng là dấu phân cách ────────────────────────────
    r = map_dat_o("Đất ở", "Thôn: Nguyệt, xã: Ứng Hòa, huyện: Ứng Hòa, tỉnh: Hà Tây")
    assert r and r["id"] == 191 and r["method"] == "dia_chi_xa", \
        f"KILL [18] 'xã:' có dấu hai chấm vẫn phải bắt được: {r}"

    # ── 19. DANH MỤC: bất biến cấu trúc ───────────────────────────────────
    kys = [m["ky_hieu_muc_dich"] for m in LOAI_MDSDD]
    assert len(kys) == len(set(kys)), "KILL [19] ký hiệu trùng trong LOAI_MDSDD"
    for m in LOAI_MDSDD:
        assert m["ky_hieu_muc_dich"].isupper() and m["ten_muc_dich"], f"KILL [19b] {m}"
    tens = [chuan_hoa(m["ten_muc_dich"]) for m in LOAI_MDSDD]
    assert len(tens) == len(set(tens)), "KILL [19c] tên chuẩn trùng nhau sau bỏ dấu"
    # alias phải trỏ vào mã CÓ THẬT, và không được đè lên tên chuẩn
    for k, v in ALIAS.items():
        assert v in _THEO_KY_HIEU, f"KILL [19d] alias {k!r} trỏ mã không có: {v}"
        assert k == chuan_hoa(k), f"KILL [19e] khóa alias chưa chuẩn hóa: {k!r}"
    # ba tập lý do phải RỜI NHAU — một chuỗi rơi vào 2 tập thì kết quả tùy thứ tự code
    assert not (NHAP_NHANG & KHONG_PHAI_MUC_DICH), "KILL [19f] hai tập giao nhau"
    assert not (set(ALIAS) & NHAP_NHANG), "KILL [19g] alias vừa map vừa nhập nhằng"

    # ── 20. map_muc_dich trên chuỗi THẬT của audit 0.3 ────────────────────
    ra_ma = [
        ("Đất trồng cây lâu năm", "CLN"), ("đất trồng cây lâu năm", "CLN"),
        ("CLN", "CLN"), ("Đất giao thông", "DGT"),
        ("Đất nuôi trồng thủy sản", "NTS"), ("Đất rừng sản xuất", "RSX"),
        # HNK "Đất trồng cây hằng năm khác" — "hằng"/"hàng" bỏ dấu là một, còn
        # BHK là "Đất BẰNG trồng cây hàng năm khác", khác mã.
        ("Đất trồng cây hàng năm khác", "HNK"),
        ("Đất bằng trồng cây hàng năm khác", "BHK"), ("Đất chợ", "DCH"),
        ("005-Đất trồng cây lâu năm", "CLN"),   # tiền tố mã số hệ cũ
        # danh mục thật có mã CHA cho lúa và cho sản xuất nông nghiệp
        ("Lúa", "LUA"), ("Lúa nước", "LUA"), ("Lúa lai", "LUA"),
        ("Lúa nước 2 năm", "LUA"), ("Đất trồng lúa", "LUA"),
        ("Đất chuyên trồng lúa", "LUC"), ("Đất trồng lúa nương", "LUN"),
        ("sx nông nghiệp", "SXN"), ("Sản xuất nông nghiệp", "SXN"),
    ]
    for txt, ky in ra_ma:
        r = map_muc_dich(txt, "")
        assert r and r["ky_hieu"] == ky, f"KILL [20] {txt!r} phải ra {ky}: {r}"

    # NHẬP NHẰNG THẬT — không được chọn bừa một mã
    # "vườn"/"ao" KHÔNG có mã trong danh mục 82 mã → không được tự chọn CLN/NTS.
    for txt in ("đất vườn", "Vườn", "Đất Ao", "Ao", "Rừng",
                "Đất nông nghiệp", "Đất trồng cây", "Đất khác"):
        r = map_muc_dich(txt, "xã Phù Lỗ, huyện Sóc Sơn")
        assert r["ambiguous"] and r["ly_do"] == "nhap_nhang", \
            f"KILL [20b] {txt!r} phải là nhập nhằng, không được đoán mã: {r}"

    # LẪN CỘT — phải tách khỏi "chưa map" để biết đường sửa prompt extract
    for txt in ("Sử dụng chung", "Sử dụng riêng", "Riêng", "Chung", "10%", "Lâu dài"):
        r = map_muc_dich(txt, "")
        assert r["ambiguous"] and r["ly_do"] == "khong_phai_muc_dich", \
            f"KILL [20c] {txt!r} phải bị đánh dấu là LẪN CỘT: {r}"

    # chuỗi lạ hoàn toàn → chua_map, không phải nhap_nhang
    r = map_muc_dich("Đất abc xyz không có thật", "")
    assert r["ambiguous"] and r["ly_do"] == "chua_map", f"KILL [20d] {r}"

    # ── 20e. ĐUÔI DÀI bắt theo HỌ (chuỗi thật từ danh sách "chưa map") ────
    for txt in ("vườn liền kề", "Vườn ao", "Đất vườn + Ao", "Lâm nghiệp",
                "Nông nghiệp", "Đất trồng cây hàng năm"):
        r = map_muc_dich(txt, "")
        assert r["ly_do"] == "nhap_nhang", f"KILL [20e] {txt!r} phải vào họ nhập nhằng: {r}"
    for txt in ("Đất sử dụng chung", "Diện tích sử dụng riêng", "Sử dụng rộng",
                "Sử dụng khác", "Thuế đất", "Đất không được cấp Giấy chứng nhận"):
        r = map_muc_dich(txt, "")
        assert r["ly_do"] == "khong_phai_muc_dich", f"KILL [20f] {txt!r}: {r}"
    # nhiễu quanh một mục đích ĐÚNG không được làm mất mã (ca thật từ audit)
    for txt, ky in (("Đất trồng cây lâu năm (sử dụng chung)", "CLN"),
                    ("Đất giao thông (ngõ đi chung)", "DGT"),
                    ("Đất trồng cây lâu năm: 300 m2", "CLN")):
        r = map_muc_dich(txt, "")
        assert r["ky_hieu"] == ky, f"KILL [20h] chú thích làm mất mã {txt!r}: {r}"
    # nhưng ký hiệu trong ngoặc thì VẪN phải đọc được (đọc text gốc, trước khi tra bảng)
    r = map_muc_dich("Đất ở tại nông thôn (ONT)", "")
    assert r["ky_hieu"] == "ONT", f"KILL [20i] bỏ ngoặc làm mất ký hiệu: {r}"

    # HỌ KHÔNG ĐƯỢC CƯỚP MÃ của chuỗi đã khớp tuyệt đối
    for txt, ky in (("Đất trồng cây lâu năm khác", "LNK"), ("Đất TCLN", "CLN"),
                    ("Đất trồng cây lâu năm", "CLN"), ("Đất nuôi trồng thủy sản", "NTS")):
        r = map_muc_dich(txt, "")
        assert r["ky_hieu"] == ky, f"KILL [20g] họ cướp mã của {txt!r}: {r}"

    # ── 21. map_muc_dich KHÔNG được phá nhánh đất ở ───────────────────────
    r = map_muc_dich("Đất ở", "Thắng Lợi, Phú Minh, Sóc Sơn, Hà Nội")
    assert r and r["id"] == 191, f"KILL [21] đất ở phải đi nhánh map_dat_o: {r}"
    r = map_muc_dich("Thổ cư", "phường Nghĩa Tân, quận Cầu Giấy")
    assert r and r["id"] == 192, f"KILL [21b] {r}"
    assert map_muc_dich("", "xã X") is None, "KILL [21c] chuỗi rỗng phải trả None"

    # ── 22. mọi kết quả phải ĐÚNG MỘT DẠNG: có mã, hoặc ambiguous có lý do ─
    moi_chuoi = (list(ALIAS) + list(NHAP_NHANG) + list(KHONG_PHAI_MUC_DICH)
                 + [m["ten_muc_dich"] for m in LOAI_MDSDD] + ["Đất ở", "xyz"])
    for txt in moi_chuoi:
        r = map_muc_dich(txt, "xã Phù Lỗ")
        assert r is not None, f"KILL [22] {txt!r} → None ngoài dự kiến"
        if r["ambiguous"]:
            assert r["ky_hieu"] is None and r["ly_do"] in LY_DO, f"KILL [22b] {txt!r}: {r}"
        else:
            assert r["ky_hieu"] in _THEO_KY_HIEU and r["method"] in METHODS, \
                f"KILL [22c] {txt!r}: {r}"

    # ── 23. gan_mdsdd trên cây extractions nhiều thửa / nhiều mục đích ────
    def _cay():
        return [{"result": {"Đăng ký": [{"Thửa đất": [
            {"Địa chỉ": "Thôn Cam, xã Cổ Bi, huyện Gia Lâm",
             "Mục đích sử dụng": [{"Loại mục đích": "Đất ở"},
                                  {"Loại mục đích": "Đất trồng cây lâu năm"}]},
            {"Địa chỉ": "Tổ 10, phường Phúc Đồng, quận Long Biên",
             "Mục đích sử dụng": [{"Loại mục đích": "Đất ở"},
                                  {"Loại mục đích": "đất vườn"},
                                  {"Loại mục đích": ""}]},
        ]}]}}]

    recs = _cay()
    tt = gan_mdsdd(recs)
    thuas = recs[0]["result"]["Đăng ký"][0]["Thửa đất"]
    assert tt["n"] == 4 and tt["n_ra_ma"] == 3, f"KILL [23] đếm sai: {tt}"
    assert tt["can_ra_tay"] and tt["ly_do"] == ["nhap_nhang"], f"KILL [23b] {tt}"
    # mỗi thửa dùng ĐỊA CHỈ CỦA CHÍNH NÓ — hai thửa cùng giấy ra hai mã khác nhau
    assert thuas[0]["Mục đích sử dụng"][0][KEY_MA] == 191, "KILL [23c] thửa 1 phải ONT"
    assert thuas[1]["Mục đích sử dụng"][0][KEY_MA] == 192, "KILL [23d] thửa 2 phải ODT"
    assert thuas[0]["Mục đích sử dụng"][1][KEY_MA] == 164, "KILL [23e] CLN"
    # ký hiệu KHÔNG mất — nó nằm đầu cột "MĐSD chuẩn"
    assert thuas[0]["Mục đích sử dụng"][1][KEY_TEN].startswith("CLN - "), "KILL [23e2]"
    # cột Mã chỉ MỘT kiểu dữ liệu: số id, hoặc rỗng. Trộn số với chữ là đẩy
    # sang FME thì vỡ.
    for _, _, _, _, md, _ in duyet_muc_dich(recs):
        v = md.get(KEY_MA, "")
        assert v == "" or isinstance(v, int), f"KILL [23i] cột Mã lẫn kiểu: {md}"
    # nhập nhằng: CÓ key nhưng rỗng — form hậu kiểm cần ô trống để chuyên viên điền
    assert thuas[1]["Mục đích sử dụng"][1][KEY_MA] == "", "KILL [23f] nhập nhằng phải để trống"
    # mục đích rỗng: KHÔNG bịa ra ô mã
    assert KEY_MA not in thuas[1]["Mục đích sử dụng"][2], "KILL [23g] chuỗi rỗng không được có ô mã"
    # KHÔNG đụng dữ liệu raw
    assert thuas[0]["Mục đích sử dụng"][0]["Loại mục đích"] == "Đất ở", "KILL [23h] sửa raw"

    # ── 24. IDEMPOTENT: chạy 2 lần cho kết quả y hệt (điều kiện của backfill) ─
    import copy
    a = _cay()
    gan_mdsdd(a)
    b = copy.deepcopy(a)
    tt2 = gan_mdsdd(b)
    assert a == b and tt2 == tt, "KILL [24] gan_mdsdd KHÔNG idempotent"

    # cây rỗng/rác không được nổ
    for xau in (None, [], [{}], [{"result": None}], [{"result": {"Đăng ký": [None]}}]):
        assert gan_mdsdd(xau)["n"] == 0, f"KILL [24b] vỡ trên đầu vào rác: {xau!r}"

    print("mdsdd PURE: 24 nhóm ca ✓")


if __name__ == "__main__":
    _smoke()
