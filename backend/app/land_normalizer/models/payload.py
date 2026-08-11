"""Output contract - khop 1:1 voi payload.json mau (tests/fixtures/00004/expected_payload.json).

Dat ten field trung PascalCase voi payload.json de `model_dump(mode="json")` xuat ra
dung JSON dich ma khong can alias. Day la "hop dong" on dinh: neu backend doi schema,
chi sua file nay + cac resolver lien quan.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _PayloadModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class DonDangKyInfo(_PayloadModel):
    XaId: str | None = None
    MaDonDangKy: str | None = None
    NgayDangKyISO8601: str | None = None
    DongSuDung: bool | None = None  # chua co nguon (khong co field tuong ung trong RawRecord)
    GhiChu: str | None = None


class NguoiSoHuu(_PayloadModel):
    """Shape "nguoi" dung chung cho DataCaNhan, DataVoChong.NguoiThuNhat/NguoiThuHai,
    DataHoGiaDinh.ChuHo/VoChongChuHo - ca 4 cho nay co CUNG 1 shape trong contract cua
    POST /validate-ho-so-dang-ky-kh2959 (truoc day tuong nham DataCaNhan la 1 DTO nho
    hon - sai, xem lich-su-phat-trien.md).

    DanTocId/TenDanToc/QuocTichId1/MaQuocTich/TenQuocTich/LoaiDoiTuongSuDungDatId/XaId
    co trong contract nhung CHUA co nguon du lieu nao trong RawRecord - de None cho
    toi khi co catalog/quy tac tuong ung (dan_toc, quoc_tich...)."""

    Id: str | None = None
    HoTen: str | None = None
    NamSinh: int | None = None
    GioiTinh: str | None = None
    LoaiGiayTo: str | None = None
    SoGiayTo: str | None = None
    NgayCapGiayToISO8601: str | None = None
    NoiCapGiayTo: str | None = None
    XaId: str | None = None
    DiaChi: str | None = None
    DanTocId: int | str | None = None
    TenDanToc: str | None = None
    QuocTichId1: int | str | None = None
    MaQuocTich: str | None = None
    TenQuocTich: str | None = None
    LoaiDoiTuongSuDungDatId: int | str | None = None
    DaChet: bool | None = None


class VoChongData(_PayloadModel):
    Id: str | None = None
    NguoiThuNhat: NguoiSoHuu = NguoiSoHuu()
    NguoiThuHai: NguoiSoHuu = NguoiSoHuu()


class HoGiaDinhData(_PayloadModel):
    """LoaiDoiTuong = "Ho gia dinh" - CHUA co quy tac/nguon du lieu trong resolvers,
    them khung model de dung contract, resolver se dien sau."""

    ChuHo: NguoiSoHuu = NguoiSoHuu()
    VoChongChuHo: NguoiSoHuu = NguoiSoHuu()


class ToChucInfo(_PayloadModel):
    """Dung chung cho DataToChuc va DataCongDongDanCu (2 shape gan giong het nhau
    trong contract, DataCongDongDanCu co them Id - giu Id o ca 2 cho don gian, field
    thua duoc chap nhan). CHUA co quy tac/nguon du lieu trong resolvers."""

    Id: str | None = None
    TenToChuc: str | None = None
    LoaiGiayTo: str | None = None
    SoGiayTo: str | None = None
    NgayCapGiayToISO8601: str | None = None
    NoiCapGiayTo: str | None = None
    XaId: str | None = None
    DiaChi: str | None = None
    LoaiDoiTuongSuDungDatId: int | str | None = None


class ChuSuDungSoHuu(_PayloadModel):
    """LoaiDoiTuong quyet dinh field Data* nao duoc dien (kieu discriminated union).
    Da XAC NHAN: LoaiDoiTuong=1 la "Ca nhan" -> dien DataCaNhan; LoaiDoiTuong=3 la
    "Vo chong" -> dien DataVoChong. DataHoGiaDinh/DataToChuc/DataCongDongDanCu co
    trong contract nhung CHUA co quy tac/nguon du lieu (xem
    resolvers/chu_su_dung.py) - luon None cho toi khi duoc bo sung."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    Id: str | None = None
    LoaiDoiTuong: int | None = None
    DataVoChong: VoChongData | None = None
    DataCaNhan: NguoiSoHuu | None = None
    DataHoGiaDinh: HoGiaDinhData | None = None
    DataToChuc: ToChucInfo | None = None
    DataCongDongDanCu: ToChucInfo | None = None


class DaMucDich(_PayloadModel):
    Id: str | None = None
    # int | float | str: "So hieu to ban do"/"So thu tu thua" thuong la so nhung
    # du lieu that co the la ma ghep (vd "893-1", tach thua) - xem ThuaDat ben
    # duoi, cung 1 nguon (ParcelThucDia.so_to/so_thua, da la kieu long tu dau).
    SoHieuToBanDo: int | float | str | None = None
    SoThuTuThua: int | float | str | None = None
    LoaiMucDichSuDungDatId: int | str | None = None
    KyHieuMucDich: str | None = None
    DienTich: float | None = None
    ThoiHanSuDungLauDai: bool | None = None
    ThoiHanSuDung: str | None = None
    NgayHetHanSuDungISO8601: str | None = None
    DatTranhChap: bool | None = None
    SuDungChung: bool | None = None
    GhiChu: str | None = None
    NguonGocChiTiet: str | None = None


class ThuaDat(_PayloadModel):
    Id: str | None = None
    XaId: str | None = None
    # int | float | str: xac nhan qua du lieu that (package_id=1467, 2026-08-06) -
    # "So hieu to ban do" co the la ma ghep dang "893-1" (thua dat bi tach), KHONG
    # phai luon so nguyen/thap phan. Truoc day chi int|float lam pydantic raise
    # ValidationError va crash toan bo "Tao don cho TOAN BO du lieu" (mat luon ket
    # qua cua moi GCN khac, khong chi GCN loi) - xem lich-su-phat-trien.md.
    SoHieuToBanDo: int | float | str | None = None
    SoThuTuThua: int | float | str | None = None
    DienTich: float | None = None
    DienTichPhapLy: float | None = None
    SoHieuToBanDoCu: str | None = None  # chua co nguon (khong co field "cu" trong ParcelThucDia)
    SoThuTuThuaCu: str | None = None  # chua co nguon (khong co field "cu" trong ParcelThucDia)
    DiaChi: str | None = None
    DaMucDichs: list[DaMucDich] = []


class Nha(_PayloadModel):
    Id: str | None = None
    SoNha: str | None = None
    SoTang: str | None = None
    LoaiTaiSanNhaId: int | str | None = None
    LoaiCapNhaId: int | str | None = None
    DienTichSan: float | None = None
    DienTichXayDung: float | None = None
    DienTichSuDung: float | None = None
    DienTichSoHuuChung: float | None = None
    DienTichSoHuuRieng: float | None = None
    NamXayDung: str | None = None  # kieu string theo contract (khong phai int)
    NamHoanThanh: int | None = None


class GiayChungNhanInfo(_PayloadModel):
    Id: str | None = None
    LoaiGiayChungNhanId: int | str | None = None
    MaLoaiGiayChungNhan: str | None = None
    TenLoaiGiayChungNhan: str | None = None
    SoHieuGiayChungNhan: str | None = None
    SoHoSoGoc: str | None = None
    SoVaoSo: int | None = None  # kieu int theo contract (khong phai string)
    SoVaoSoCu: str | None = None
    NgayVaoSoISO8601: str | None = None
    TinhTrangGiayChungNhan: int | None = None
    DaCongNhanPhapLy: int | None = None


class GiayChungNhan(_PayloadModel):
    GiayChungNhan: GiayChungNhanInfo = GiayChungNhanInfo()
    PhapNhanId: str | None = None
    ThuaDatIds: list[str] = []
    DaMucDichIds: list[str] = []
    NhaIds: list[str] = []


class HoSoQuet(_PayloadModel):
    BucketName: str | None = None
    FilePath: str | None = None
    SoGcn: str | None = None


class Payload(_PayloadModel):
    DonDangKy: DonDangKyInfo = DonDangKyInfo()
    ChuSuDungSoHuus: list[ChuSuDungSoHuu] = []
    ThuaDats: list[ThuaDat] = []
    Nhas: list[Nha] = []
    GiayChungNhans: list[GiayChungNhan] = []
    HoSoQuets: list[HoSoQuet] = []
