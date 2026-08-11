"""Input contract cho pipeline chuan hoa.

QUAN TRONG: day la "bien gioi" (boundary) duy nhat giua nguon du lieu (Excel hom nay,
API sau nay) va phan logic nghiep vu (resolvers/). RawRecord mo phong dung cac key
`ai_*` / thu thap thuc dia da thay trong file mau 00004.xlsx (hang 2 = ten key) -
day chinh la hinh dang du lieu ma API thu thap + OCR se gui len sau nay, nen khi doi
tu Excel sang API, chi can viet them mot ingestion adapter moi tra ve RawRecord,
khong sua models/resolvers/pipeline.

Cac truong lay tu OCR (ai_*) giu kieu long (str | float | None, hoac
bool | str | int | float | None cho field dang bool) vi OCR tra ve du lieu khong
dong nhat (vd "Dien tich san": 77.2 nhung co dong lai la "23.0" dang string, hoac ""
khi khong doc duoc; tuong tu "Su dung chung": false (bool that) o dong nay nhung ""
o dong khac - da gap loi that khi test voi du lieu ngoai 00004.xlsx, xem
lich-su-phat-trien.md). Viec ep kieu / lam sach la trach nhiem cua resolver, khong
phai cua lop ingestion - KHONG duoc dat field OCR nao la kieu strict (vd `bool`
khong co `| str`), du co ve "dung ve mat logic", vi se lam vo toan bo qua trinh
nap du lieu ngay khi gap 1 dong loi dinh dang.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_LOOSE = str | float | int | None
_LOOSE_BOOL = bool | str | int | float | None  # OCR doi khi tra ve "" thay vi bool that


class _VNModel(BaseModel):
    """Base cho cac object OCR co key tieng Viet co dau (vd 'Tên chủ')."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class NguoiGui(_VNModel):
    id: int | None = None
    ho_ten: str | None = None
    ten_dang_nhap: str | None = None


class ChuSuDungThucDia(_VNModel):
    """Chu su dung do nguoi thu thap nhap tai cho (khong phai OCR)."""

    ten: str | None = None
    so_giay_to: str | None = None
    cccd_front: str | None = None
    cccd_back: str | None = None
    dia_chi_thuong_tru: str | None = None
    dai_dien_thua_ke: bool | None = None


class ParcelThucDia(_VNModel):
    """Thua dat do nguoi thu thap xac dinh tai cho (co toa do GPS)."""

    ma_xa: str | None = None
    so_to: _LOOSE = None
    so_thua: _LOOSE = None
    ma_thua_dat: str | None = None
    dien_tich: _LOOSE = None
    so_thu_tu_gcn: str | None = None
    dia_chi_chi_tiet: str | None = None
    lat: float | None = None
    lng: float | None = None


class AiChuSuDungItem(_VNModel):
    """Mot dong chu so huu doc tu GCN (bang OCR)."""

    ten_chu: str | None = Field(default=None, alias="Tên chủ")
    gioi_tinh: str | None = Field(default=None, alias="Giới tính")
    nam_sinh: _LOOSE = Field(default=None, alias="Năm sinh")
    dia_chi: str | None = Field(default=None, alias="Địa chỉ")
    so_giay_to: str | None = Field(default=None, alias="Số giấy tờ")
    loai_giay_to: str | None = Field(default=None, alias="Loại giấy tờ")
    loai_doi_tuong: str | None = Field(default=None, alias="Loại đối tượng")
    noi_cap: str | None = Field(default=None, alias="Nơi cấp")
    ngay_cap_dinh_danh: str | None = Field(default=None, alias="Ngày cấp định danh")


class AiMucDichSuDungItem(_VNModel):
    ma_mdsd: _LOOSE = Field(default=None, alias="Mã MĐSD")
    mdsd_chuan: str | None = Field(default=None, alias="MĐSD chuẩn")
    loai_muc_dich: str | None = Field(default=None, alias="Loại mục đích")
    dien_tich: _LOOSE = Field(default=None, alias="Diện tích")
    su_dung_chung: _LOOSE_BOOL = Field(default=None, alias="Sử dụng chung")
    nguon_goc_chi_tiet: str | None = Field(default=None, alias="Nguồn gốc chi tiết")
    thoi_han_su_dung: str | None = Field(default=None, alias="Thời hạn sử dụng")
    ngay_het_han_su_dung: str | None = Field(default=None, alias="Ngày hết hạn sử dụng")


class AiThuaDatItem(_VNModel):
    dien_tich: _LOOSE = Field(default=None, alias="Diện tích")
    dia_chi: str | None = Field(default=None, alias="Địa chỉ")
    so_thu_tu_thua: _LOOSE = Field(default=None, alias="Số thứ tự thửa")
    so_hieu_to_ban_do: _LOOSE = Field(default=None, alias="Số hiệu tờ bản đồ")
    muc_dich_su_dung: list[AiMucDichSuDungItem] = Field(
        default_factory=list, alias="Mục đích sử dụng"
    )


class AiTaiSanItem(_VNModel):
    ket_cau: str | None = Field(default=None, alias="Kết cấu")
    so_tang: _LOOSE = Field(default=None, alias="Số tầng")
    cap_hang: str | None = Field(default=None, alias="Cấp hạng")
    dia_chi: str | None = Field(default=None, alias="Địa chỉ")
    nha_chung_cu: str | None = Field(default=None, alias="Nhà chung cư")
    so_can_ho: str | None = Field(default=None, alias="Số căn hộ")
    dien_tich_san: _LOOSE = Field(default=None, alias="Diện tích sàn")
    dien_tich_xay_dung: _LOOSE = Field(default=None, alias="Diện tích xây dựng")
    hinh_thuc_so_huu: str | None = Field(default=None, alias="Hình thức sở hữu")
    thoi_han_so_huu: str | None = Field(default=None, alias="Thời hạn sở hữu")
    khu_nha_chung_cu: str | None = Field(
        default=None, alias="Khu nhà chung cư, nhà hỗn hợp"
    )
    loai_tai_san: str | None = Field(
        default=None, alias="Loại tài sản gắn liền với đất"
    )


class AiBienDongItem(_VNModel):
    thoi_gian: str | None = Field(default=None, alias="Thời gian")
    noi_dung: str | None = Field(default=None, alias="Nội dung biến động")


class RawRecord(BaseModel):
    """1 ban ghi dau vao day du (tuong duong 1 dong trong 00004.xlsx, va se tuong
    duong 1 request body cua API sau nay)."""

    model_config = ConfigDict(extra="allow")

    so_gcn: str
    ai_ocr_status: str | None = None
    error_message: str | None = None

    nguoi_gui: NguoiGui | None = None
    chu_su_dung: list[ChuSuDungThucDia] = Field(default_factory=list)
    parcels_json: list[ParcelThucDia] = Field(default_factory=list)
    dia_chi_chi_tiet: str | None = None
    loai_yeu_cau: str | None = None

    ai_gcn_so_phat_hanh: str | None = None
    ai_gcn_ma_vach: str | None = None
    ai_gcn_so_vao_so: str | None = None
    ai_gcn_ngay_cap: str | None = None

    ai_chu_su_dung: list[AiChuSuDungItem] = Field(default_factory=list)
    ai_chu_cuoi: list[AiChuSuDungItem] = Field(default_factory=list)
    ai_thua_dat: list[AiThuaDatItem] = Field(default_factory=list)
    ai_tai_san: list[AiTaiSanItem] = Field(default_factory=list)
    ai_bien_dong: list[AiBienDongItem] = Field(default_factory=list)

    gcn_paths: list[str] = Field(default_factory=list)
    pdf_path: str | None = None
    # Chi API moi co (xem ApiSourceAdapter._to_record): pdf_path tra ve dang object
    # {"bucket_name", "object_name"} - `pdf_path` o tren lay `object_name`, field
    # nay giu lai `bucket_name` (truoc 2026-08-06 bi bo qua hoan toan vi "khong
    # resolver nao dung" - nay HoSoQuetsResolver can toi). Excel khong co field
    # tuong duong (cot pdf_path trong 00004.xlsx la chuoi thuong) nen luon None.
    pdf_path_bucket_name: str | None = None
    trang_thai: str | None = None
    ghi_chu_xu_ly: str | None = None
    merge_message: str | None = None

    # Metadata ve nguon goc ban ghi - huu ich khi debug/audit, khong thuoc payload dich.
    source_row_ref: str | None = None
    # "id" cua item tren HSQ (chi co khi nguon la ApiSourceAdapter - xem
    # ingestion/api_source.py) - dung de UI tu dien item_id khi so sanh voi HSQ
    # (app/streamlit_app.py), KHONG lay tu source_row_ref (chuoi debug, khong nen
    # parse nguoc).
    hsq_item_id: int | None = None

    extra: dict[str, Any] = Field(default_factory=dict)
