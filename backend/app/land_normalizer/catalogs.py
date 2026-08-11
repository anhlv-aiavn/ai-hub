"""Bang tra cuu ma nghiep vu (catalogs) do backend/nguoi dung cung cap (2026-08-05).

Nguon: file `catalogs` o goc repo (export tu he thong dich). Dung de dien cac field
truoc day de `None` vi "can bang tra cuu ma nghiep vu, chua co":
- `LOAI_GCN` -> `GiayChungNhanInfo.LoaiGiayChungNhanId`/`MaLoaiGiayChungNhan`
  (xem resolvers/giay_chung_nhan.py).
- `LOAI_MDSDD` -> `DaMucDich.LoaiMucDichSuDungDatId` (xem resolvers/thua_dat.py).

CHI khop CHINH XAC (sau chuan hoa hoa/thuong + khoang trang) - khong doan/fuzzy-match,
dung tinh than "khong doan ma nghiep vu" da ap dung cho phan loai GCN (xem
giay_chung_nhan.py). Khong khop -> tra ve None, resolver tu quyet dinh fallback.
"""

from __future__ import annotations

LOAI_GCN: list[dict[str, object]] = [
    {
        "id": 1,
        "ma_loai": "GIẤY CHỨNG NHẬN QSD ĐẤT 2003",
        "ten_loai": "Giấy chứng nhận QSDĐ theo Luật Đất Đai 2003",
    },
    {
        "id": 2,
        "ma_loai": "GIẤY CHỨNG NHẬN QSD ĐẤT 1993",
        "ten_loai": "Giấy chứng nhận QSDĐ theo Luật Đất Đai 1993",
    },
    {
        "id": 3,
        "ma_loai": "GCN QSD NHÀ Ở VÀ QSD ĐẤT Ở 60",
        "ten_loai": "Giấy chứng nhận QSHNƠ & QSDĐƠ theo Nghị định 60/NĐ-CP",
    },
    {
        "id": 4,
        "ma_loai": "GCN QSD NHÀ Ở VÀ QSD ĐẤT Ở 90",
        "ten_loai": "Giấy chứng nhận QSHNƠ & QSDĐƠ theo Nghị định 90/NĐ-CP",
    },
    {
        "id": 5,
        "ma_loai": "GCN QSD NHÀ Ở VÀ QSD ĐẤT Ở 95",
        "ten_loai": "Giấy chứng nhận sở hữu công trình theo quy định 95",
    },
    {
        "id": 6,
        "ma_loai": "GCN QSD NHÀ Ở VÀ QSD ĐẤT Ở 2009",
        "ten_loai": "Giấy chứng nhận QSDĐƠ & QSHNƠ và TSKGLVĐ theo NĐ 88/NĐ-CP",
    },
    {
        "id": 7,
        "ma_loai": "GIẤY HỢP THỨC HÓA",
        "ten_loai": "Giấy hợp thức hóa",
    },
    {
        "id": 8,
        "ma_loai": "GIẤY PHÉP XD",
        "ten_loai": "Giấy phép Xây dựng",
    },
    {
        "id": 10,
        "ma_loai": "GIẤY PHÉP MUA BÁN, CHUYỂN DỊCH NHÀ",
        "ten_loai": "Giấy phép mua bán, chuyển dịch nhà",
    },
    {
        "id": 11,
        "ma_loai": "GCN QSD NHÀ Ở VÀ QSD ĐẤT Ở 2014",
        "ten_loai": "Giấy chứng nhận QSDĐƠ & QSHNƠ và TSKGLVĐ theo NĐ 43/NĐ-CP",
    },
    {
        "id": 9,
        "ma_loai": "CÁC LOẠI GCN KHÁC",
        "ten_loai": "Các loại GCN khác",
    },
    {
        "id": 12,
        "ma_loai": "GIẤY CHỨNG NHẬN QSD ĐẤT 2024",
        "ten_loai": "Giấy chứng nhận QSDĐ, QSHTSGLVĐ theo Luật đất đai 2024",
    },
]

LOAI_MDSDD: list[dict[str, object]] = [
    {"id": 152, "ky_hieu_muc_dich": "SXN", "ten_muc_dich": "Đất sản xuất nông nghiệp"},
    {"id": 154, "ky_hieu_muc_dich": "LUA", "ten_muc_dich": "Đất trồng lúa"},
    {"id": 169, "ky_hieu_muc_dich": "RSX", "ten_muc_dich": "Đất rừng sản xuất"},
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
    {"id": 170, "ky_hieu_muc_dich": "RSN", "ten_muc_dich": "Trong đó: Đất rừng sản xuất là rừng tự nhiên"},
    {"id": 172, "ky_hieu_muc_dich": "RSK", "ten_muc_dich": "Đất khoanh nuôi phục hồi rừng sản xuất"},
    {"id": 174, "ky_hieu_muc_dich": "RPH", "ten_muc_dich": "Đất rừng phòng hộ"},
    {"id": 176, "ky_hieu_muc_dich": "RPT", "ten_muc_dich": "Đất có rừng trồng phòng hộ"},
    {"id": 178, "ky_hieu_muc_dich": "RPM", "ten_muc_dich": "Đất trồng rừng phòng hộ"},
    {"id": 180, "ky_hieu_muc_dich": "RDN", "ten_muc_dich": "Đất có rừng tự nhiên đặc dụng"},
    {"id": 182, "ky_hieu_muc_dich": "RDK", "ten_muc_dich": "Đất khoanh nuôi phục hồi rừng đặc dụng"},
    {"id": 184, "ky_hieu_muc_dich": "NTS", "ten_muc_dich": "Đất nuôi trồng thuỷ sản"},
    {"id": 186, "ky_hieu_muc_dich": "TSN", "ten_muc_dich": "Đất nuôi trồng thuỷ sản nước ngọt"},
    {"id": 188, "ky_hieu_muc_dich": "NKH", "ten_muc_dich": "Đất nông nghiệp khác"},
    {"id": 192, "ky_hieu_muc_dich": "ODT", "ten_muc_dich": "Đất ở tại đô thị"},
    {"id": 194, "ky_hieu_muc_dich": "CTS", "ten_muc_dich": "Đất trụ sở cơ quan, công trình sự nghiệp"},
    {"id": 207, "ky_hieu_muc_dich": "CAN", "ten_muc_dich": "Đất an ninh"},
    {"id": 209, "ky_hieu_muc_dich": "SKK", "ten_muc_dich": "Đất khu công nghiệp"},
    {"id": 213, "ky_hieu_muc_dich": "SKS", "ten_muc_dich": "Đất sử dụng cho hoạt động khoáng sản"},
    {"id": 228, "ky_hieu_muc_dich": "DTL", "ten_muc_dich": "Đất công trình thủy lợi"},
    {"id": 236, "ky_hieu_muc_dich": "DBV", "ten_muc_dich": "Đất công trình hạ tầng bưu chính, viễn thông, công nghệ thông tin"},
    {"id": 238, "ky_hieu_muc_dich": "DYT", "ten_muc_dich": "Đất xây dựng cơ sở y tế"},
    {"id": 240, "ky_hieu_muc_dich": "DTT", "ten_muc_dich": "Đất xây dựng cơ sở thể dục, thể thao"},
    {"id": 242, "ky_hieu_muc_dich": "DXH", "ten_muc_dich": "Đất xây dựng cơ sở dịch vụ xã hội"},
    {"id": 247, "ky_hieu_muc_dich": "DDT", "ten_muc_dich": "Đất có di tích"},
    {"id": 251, "ky_hieu_muc_dich": "TIN", "ten_muc_dich": "Đất cơ sở  tín ngưỡng"},
    {"id": 270, "ky_hieu_muc_dich": "MNC", "ten_muc_dich": "Đất có mặt nước chuyên dùng"},
    {"id": 274, "ky_hieu_muc_dich": "DCS", "ten_muc_dich": "Đất đồi núi chưa sử dụng"},
    {"id": 276, "ky_hieu_muc_dich": "MVB", "ten_muc_dich": "Đất có mặt nước ven biển"},
    {"id": 278, "ky_hieu_muc_dich": "MVR", "ten_muc_dich": "Đất mặt nước ven biển có rừng ngập mặn"},
    {"id": 282, "ky_hieu_muc_dich": "DNG", "ten_muc_dich": "Đất xây dựng cơ sở ngoại giao"},
    {"id": 284, "ky_hieu_muc_dich": "SKN", "ten_muc_dich": "Đất cụm công nghiệp"},
    {"id": 286, "ky_hieu_muc_dich": "TMD", "ten_muc_dich": "Đất thương mại, dịch vụ"},
    {"id": 287, "ky_hieu_muc_dich": "DDL", "ten_muc_dich": "Đất có danh lam thắng cảnh"},
    {"id": 289, "ky_hieu_muc_dich": "DKV", "ten_muc_dich": "Đất khu vui chơi, giải trí công cộng"},
    {"id": 295, "ky_hieu_muc_dich": "KĐD", "ten_muc_dich": "Đất cơ sở bảo tồn đa dạng sinh học"},
    {"id": 205, "ky_hieu_muc_dich": "TSK", "ten_muc_dich": "Đất trụ sở khác"},
    {"id": 171, "ky_hieu_muc_dich": "RST", "ten_muc_dich": "Đất có rừng trồng sản xuất"},
    {"id": 173, "ky_hieu_muc_dich": "RSM", "ten_muc_dich": "Đất trồng rừng sản xuất"},
    {"id": 175, "ky_hieu_muc_dich": "RPN", "ten_muc_dich": "Đất có rừng tự nhiên phòng hộ"},
    {"id": 177, "ky_hieu_muc_dich": "RPK", "ten_muc_dich": "Đất khoanh nuôi phục hồi rừng phòng hộ"},
    {"id": 179, "ky_hieu_muc_dich": "RDD", "ten_muc_dich": "Đất rừng đặc dụng"},
    {"id": 181, "ky_hieu_muc_dich": "RDT", "ten_muc_dich": "Đất có rừng trồng đặc dụng"},
    {"id": 183, "ky_hieu_muc_dich": "RDM", "ten_muc_dich": "Đất trồng rừng đặc dụng"},
    {"id": 185, "ky_hieu_muc_dich": "TSL", "ten_muc_dich": "Đất nuôi trồng thuỷ sản nước lợ, mặn"},
    {"id": 187, "ky_hieu_muc_dich": "LMU", "ten_muc_dich": "Đất làm muối"},
    {"id": 191, "ky_hieu_muc_dich": "ONT", "ten_muc_dich": "Đất ở tại nông thôn"},
    {"id": 193, "ky_hieu_muc_dich": "CDG", "ten_muc_dich": "Đất chuyên dùng"},
    {"id": 204, "ky_hieu_muc_dich": "TSC", "ten_muc_dich": "Đất xây dựng trụ sở cơ quan"},
    {"id": 206, "ky_hieu_muc_dich": "CQP", "ten_muc_dich": "Đất quốc phòng"},
    {"id": 210, "ky_hieu_muc_dich": "SKC", "ten_muc_dich": "Đất cơ sở sản xuất phi nông nghiệp"},
    {"id": 219, "ky_hieu_muc_dich": "SKX", "ten_muc_dich": "Đất sản xuất vật liệu xây dựng, làm đồ gốm"},
    {"id": 225, "ky_hieu_muc_dich": "DGT", "ten_muc_dich": "Đất công trình giao thông"},
    {"id": 231, "ky_hieu_muc_dich": "DNL", "ten_muc_dich": "Đất công trình năng lượng"},
    {"id": 237, "ky_hieu_muc_dich": "DVH", "ten_muc_dich": "Đất xây dựng cơ sở văn hóa"},
    {"id": 239, "ky_hieu_muc_dich": "DGD", "ten_muc_dich": "Đất xây dựng cơ sở giáo dục và đào tạo"},
    {"id": 241, "ky_hieu_muc_dich": "DKH", "ten_muc_dich": "Đất xây dựng cơ sở khoa học và công nghệ"},
    {"id": 243, "ky_hieu_muc_dich": "DCH", "ten_muc_dich": "Đất chợ dân sinh, chợ đầu mối"},
    {"id": 248, "ky_hieu_muc_dich": "DRA", "ten_muc_dich": "Đất bãi thải, xử lý chất thải"},
    {"id": 250, "ky_hieu_muc_dich": "TON", "ten_muc_dich": "Đất cơ sở  tôn giáo"},
    {"id": 252, "ky_hieu_muc_dich": "NTD", "ten_muc_dich": "Đất nghĩa trang, nhà tang lễ, cơ sở hỏa táng; đất cơ sở lưu trữ tro cốt"},
    {"id": 254, "ky_hieu_muc_dich": "SON", "ten_muc_dich": "Đất có mặt nước dạng sông, ngòi, kênh, rạch, suối"},
    {"id": 271, "ky_hieu_muc_dich": "PNK", "ten_muc_dich": "Đất phi nông nghiệp khác"},
    {"id": 273, "ky_hieu_muc_dich": "BCS", "ten_muc_dich": "Đất bằng chưa sử dụng"},
    {"id": 275, "ky_hieu_muc_dich": "NCS", "ten_muc_dich": "Núi đá không có rừng cây"},
    {"id": 277, "ky_hieu_muc_dich": "MVT", "ten_muc_dich": "Đất mặt nước ven biển nuôi trồng thuỷ sản"},
    {"id": 279, "ky_hieu_muc_dich": "MVK", "ten_muc_dich": "Đất mặt nước ven biển có mục đích khác"},
    {"id": 281, "ky_hieu_muc_dich": "DTS", "ten_muc_dich": "Đất xây dựng trụ sở của tổ chức sự nghiệp"},
    {"id": 283, "ky_hieu_muc_dich": "DSK", "ten_muc_dich": "Đất xây dựng công trình sự nghiệp khác"},
    {"id": 285, "ky_hieu_muc_dich": "SKT", "ten_muc_dich": "Đất khu chế xuất"},
    {"id": 288, "ky_hieu_muc_dich": "DSH", "ten_muc_dich": "Đất sinh hoạt cộng đồng"},
    {"id": 290, "ky_hieu_muc_dich": "DCK", "ten_muc_dich": "Đất công trình công cộng khác"},
    {"id": 296, "ky_hieu_muc_dich": "CNC", "ten_muc_dich": "Đất khu công nghệ cao"},
]


def _normalize_text(raw: str | None) -> str:
    """Strip + casefold + gop khoang trang - dung de so sanh 'khop chinh xac' nhung
    khong nhay hoa/thuong hay khoang trang thua (khac nhau giua cac nguon OCR)."""
    if not raw:
        return ""
    return " ".join(raw.strip().casefold().split())


_LOAI_GCN_BY_ID: dict[int, dict[str, object]] = {entry["id"]: entry for entry in LOAI_GCN}
_LOAI_GCN_BY_TEN: dict[str, dict[str, object]] = {
    _normalize_text(entry["ten_loai"]): entry for entry in LOAI_GCN  # type: ignore[arg-type]
}
_LOAI_MDSDD_BY_ID: dict[int, dict[str, object]] = {entry["id"]: entry for entry in LOAI_MDSDD}
_LOAI_MDSDD_BY_KY_HIEU: dict[str, dict[str, object]] = {
    _normalize_text(entry["ky_hieu_muc_dich"]): entry for entry in LOAI_MDSDD  # type: ignore[arg-type]
}
_LOAI_MDSDD_BY_TEN: dict[str, dict[str, object]] = {
    _normalize_text(entry["ten_muc_dich"]): entry for entry in LOAI_MDSDD  # type: ignore[arg-type]
}


def _normalize_text_no_tai(raw: str | None) -> str:
    """Giong _normalize_text nhung bo them tu dem "tại" - xac nhan voi nguoi dung
    2026-08-05: du lieu thuc te thuong ghi 'Đất ở đô thị'/'Đất ở nông thôn' (bo "tại")
    thay vi dung nguyen van catalog 'Đất ở TẠI đô thị'/'Đất ở TẠI nông thôn". Chi 2
    muc nay trong LOAI_MDSDD co chua "tại" nen khong co rui ro va cham giua cac muc
    khac nhau sau khi bo tu nay."""
    words = [w for w in _normalize_text(raw).split() if w != "tại"]
    return " ".join(words)


_LOAI_MDSDD_BY_TEN_NO_TAI: dict[str, dict[str, object]] = {
    _normalize_text_no_tai(entry["ten_muc_dich"]): entry for entry in LOAI_MDSDD  # type: ignore[arg-type]
}


def loai_gcn_by_id(loai_gcn_id: int) -> dict[str, object] | None:
    return _LOAI_GCN_BY_ID.get(loai_gcn_id)


def loai_gcn_by_ten(ten_loai: str | None) -> dict[str, object] | None:
    """Khop CHINH XAC (sau chuan hoa) theo `ten_loai` - dung khi da biet van ban mo ta
    day du (vd tu quy tac phan loai co san trong giay_chung_nhan.py) va muon tra ra
    id/ma catalog tuong ung, khong doan."""
    return _LOAI_GCN_BY_TEN.get(_normalize_text(ten_loai))


def loai_mdsdd_by_id(loai_mdsdd_id: int) -> dict[str, object] | None:
    return _LOAI_MDSDD_BY_ID.get(loai_mdsdd_id)


def loai_mdsdd_by_ky_hieu(ky_hieu: str | None) -> dict[str, object] | None:
    """Khop CHINH XAC theo ky hieu muc dich (vd 'ODT', 'LUA')."""
    return _LOAI_MDSDD_BY_KY_HIEU.get(_normalize_text(ky_hieu))


def loai_mdsdd_by_ten(ten_muc_dich: str | None) -> dict[str, object] | None:
    """Khop theo ten day du (vd 'Đất ở tại đô thị'), CHINH XAC sau chuan hoa - khong
    fuzzy-match tu do. Rieng tu dem "tại" duoc bo qua khi so sanh (xem
    _normalize_text_no_tai) vi day la sai lech dinh dang da xac nhan thuc te, khong
    phai doan nghia. Van ban qua chung chung nhu 'Đất ở' (khong ro do thi/nong thon)
    van se KHONG khop - mo ho giua ODT/ONT, resolver phai giu nguyen van ban goc."""
    found = _LOAI_MDSDD_BY_TEN.get(_normalize_text(ten_muc_dich))
    if found is not None:
        return found
    return _LOAI_MDSDD_BY_TEN_NO_TAI.get(_normalize_text_no_tai(ten_muc_dich))
