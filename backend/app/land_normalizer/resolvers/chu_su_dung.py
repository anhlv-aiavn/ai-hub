"""Resolver cho ChuSuDungSoHuus.

`ChuSuDungSoHuu` la kieu discriminated union (`LoaiDoiTuong` quyet dinh field
`Data*` nao duoc dien - xem models/payload.py). Moi LoaiDoiTuong duoc xu ly boi 1
class resolver rieng (`VoChongResolver`, `CaNhanResolver`, `HoGiaDinhResolver`),
nhung CHI 1 resolver duy nhat (`ChuSuDungSoHuusResolver`) duoc dang ky vao registry
cho field_path nay - no dong vai tro dispatcher, thu tung loai theo thu tu uu tien
va tra ve ket qua dau tien khong missing. Ly do khong dang ky truc tiep tung class
con: `ResolverRegistry` khong cho 2 resolver cung field_path (xem registry.py) - va
ve mat nghiep vu, "GCN nay co bao nhieu chu so huu, loai gi" la 1 quyet dinh gan
nhau, khong doc lap nhu cac field cua DonDangKy.

Da xac nhan (2026-08-01, mo rong 2026-08-05):

**LoaiDoiTuong = 3 ("Vợ chồng")**
1. Uu tien du lieu OCR, dac biet la OCR bien dong va chu cuoi (`ai_chu_cuoi`) hon
   la chu su dung ghi tren GCN goc (`ai_chu_su_dung`).
2. Thong tin giay to ca nhan uu tien CCCD (moi) hon CMND (cu).
3. Neu OCR va du lieu tu nhap (`chu_su_dung`) trung ho ten, uu tien lay so giay to
   theo CCCD moi - `chu_su_dung` la du lieu quet CCCD tai thoi diem thu thap nen
   duoc coi la nguon CCCD moi nhat.

**LoaiDoiTuong = 1 ("Cá nhân")** - cung quy tac 1 va 3 o tren, nhung KHONG con gioi
han "dung 1 nguoi": neu nguon co N nguoi (N>=1), tao ra N phan tu `ChuSuDungSoHuu`
doc lap, moi phan tu LoaiDoiTuong=1 ung voi 1 nguoi (2026-08-05, xac nhan voi nguoi
dung: N>1 la truong hop "dong so huu" - KHONG co ma LoaiDoiTuong rieng, chi la
nhieu entry Ca nhan trong cung mang `ChuSuDungSoHuus`).

**LoaiDoiTuong = 2 ("Hộ gia đình")** (them 2026-08-05) - xac nhan qua nhan "Loại
đối tượng"="Hộ gia đình" trong `ai_chu_su_dung` (cung tinh than ADR-005 nhu Vo
chong: dung nhan de XAC NHAN, khong doan tu so luong). Dung `HoGiaDinhData{ChuHo,
VoChongChuHo}`: nguoi dung xac nhan **Chu ho = nguoi DAU TIEN, VoChongChuHo = nguoi
THU HAI** (theo thu tu xuat hien trong nguon da chon - uu tien `ai_chu_cuoi` neu so
luong khop, khong thi chinh cac item da gan nhan trong `ai_chu_su_dung`). Chi 1
nguoi gan nhan -> `VoChongChuHo` de trong (khong doan la ai).

CACH AP DUNG (Vo chong): `ai_chu_su_dung` (gan nhan "Loại đối tượng" tren tung
nguoi) duoc dung de XAC NHAN day la truong hop Vo chong (khong doan tu so luong
nguoi). Sau khi xac nhan, CHI TIET 2 nguoi uu tien lay tu `ai_chu_cuoi` neu co dung
2 nguoi (quy tac 1); neu khong, dung lai chinh 2 item da gan nhan "Vợ chồng" trong
`ai_chu_su_dung`.

CACH AP DUNG (Ca nhan/Dong so huu, sua 2026-08-05): KHONG con dung `ai_chu_su_dung`
gan nhan "Cá nhân" de XAC NHAN/DEM SO NGUOI - van giu nguyen ly do ban dau (nhan
"Cá nhân" co the danh cho nhieu nguoi LICH SU khong con la chu hien tai, vd
so_gcn='10101070103' co 4 nguoi gan "Cá nhân" trong `ai_chu_su_dung` nhung chu cuoi
that su chi co 1 nguoi - dung so luong tag nay lam can cu se ra ket qua SAI). Thay
vao do, dem TRUC TIEP so nguoi co trong `ai_chu_cuoi` (uu tien) hoac `chu_su_dung`
(fallback khi khong co OCR) - day CHINH LA nguon dang tin cay nhat (da qua "loc" boi
chinh qua trinh OCR/nhap lieu phan anh trang thai HIEN TAI), khac voi nhan tho tren
`ai_chu_su_dung`. N=1 -> Ca nhan; N>1 -> dong so huu (N entries doc lap).

GIA DINH CHUA XAC NHAN (chung cho Vo chong/Ca nhan/Ho gia dinh, can review khi co
quy tac tiep theo):
- DaChet luon la False - khong co nguon du lieu nao cho biet nguoi so huu da mat.
- "Ngày cấp định danh" trong du lieu OCR duoc coi la ngay cap giay to tuy than
  (NgayCapGiayToISO8601) - thuat ngu "dinh danh" co the am chi dinh danh dien tu,
  can xac nhan lai. DataCaNhan dung CHUNG shape `NguoiSoHuu` voi NguoiThuNhat/
  NguoiThuHai (cap nhat 2026-08-05, xem models/payload.py) nen field nay CO duoc
  dien khi nguon la `ai_chu_cuoi` (AiChuSuDungItem, cung `_build_nguoi` voi Vo
  chong) - chi None khi fallback ve `chu_su_dung` (ChuSuDungThucDia khong co field
  nay).
- Id (ChuSuDungSoHuu, DataVoChong/DataCaNhan, tung NguoiSoHuu) duoc sinh moi (uuid4)
  vi khong co nguon du lieu nao cung cap - mo phong dung cau truc trong payload mau.
- Khop ten giua OCR va `chu_su_dung` dung so sanh chuoi da chuan hoa (strip +
  casefold + gop khoang trang), KHONG fuzzy-match - mot so truong hop OCR sai
  chinh ta se khong khop duoc (vd "PHAN XUÂN DƯƠNG" OCR vs "Phan Xuân Dượng" thu
  thap, thay trong 00004.xlsx so_gcn=010105264600108), khi do resolver fallback ve
  du lieu giay to cua chinh item OCR thay vi bao loi.

Da xac nhan (them 2026-08-06) - `ai_chu_cuoi` (chu cuoi) LUON duoc UU TIEN quyet
dinh khi TON TAI (khong rong), ke ca khi so nguoi cua no MAU THUAN voi nhan "Loại
đối tượng" tren `ai_chu_su_dung` (GCN goc): VoChongResolver/HoGiaDinhResolver
KHONG con duoc fallback ve du lieu GCN goc trong truong hop nay - phai tra ve
missing de nhuong cho CaNhanResolver (nguon dung chu_cuoi la chinh). Phat hien qua
bug thuc te (HSQ item_id=15394213): GCN goc gan nhan "Vợ chồng" cho 2 nguoi (du
lieu OCR loi, thieu SoGiayTo), nhung tai san da duoc sang ten cho 1 ca nhan khac
("Nguyễn Thị Kiều Liên", co du CCCD/dia chi) - phan anh trong `ai_chu_cuoi` chi con
1 nguoi. Truoc ban sua nay, VoChongResolver van tra ve ket qua (2 nguoi cu, sai)
vi chi kiem tra `len(chu_cuoi) == 2` roi fallback thay vi coi su khong khop nay la
tin hieu "da bien dong khoi Vo chong". Ap dung dung cho ca Ho gia dinh.

PHAM VI CHUA XU LY (tra ve missing, khong doan):
- Vo chong: nhieu hon 2 nguoi cung duoc gan "Vo chong" ma `ai_chu_cuoi` cung khong
  co dung 2 nguoi de doi chieu.
- Ho gia dinh: nhieu hon 2 nguoi gan nhan "Hộ gia đình" - khong ro ai la chu ho, ai
  la thanh vien khac (chi ho tro dung 1 hoac 2 nguoi).
- Ca nhan/Dong so huu: ca `ai_chu_cuoi` lan `chu_su_dung` deu rong.
- **"Ưu tiên biến động" (yeu cau 2026-08-05) - CHUA LAM**: nguoi dung yeu cau uu
  tien trich xuat chu so huu moi tu van ban tu do `ai_bien_dong` ("Nội dung biến
  động") truoc ca `ai_chu_cuoi`. Da khao sat toan bo `ai_bien_dong` that trong ca 3
  file Excel (`00004.xlsx`, `00025.xlsx`, `test_cop.xlsx`, ~2250 dong, hang nghin
  muc) va phat hien van ban KHONG theo 1 mau co dinh - it nhat 5 kieu cau khac nhau
  mo ta chuyen nhuong ("Chuyển nhượng cho: ...", "Chuyển nhượng quyền sử dụng đất
  ... cho ...", "Đăng ký sang tên theo Hợp đồng chuyển nhượng...", "Ông X ... đã
  nhận chuyển nhượng...", "Đăng ký chuyển nhượng cho chủ sử dụng: ..."), co ca
  truong hop chuyen nhuong cho 2 nguoi (vo chong/dong so huu moi), khong phai luon
  1 nguoi. 1 regex thu theo dung 2 vi du ban dau CHI khop 4/hon 2000 dong thuc te -
  qua thap de tin cay, va mo rong bao phu dung cach an toan can nhieu quy
  tac/vi du hon la 1 lan sua. Nguoi dung xac nhan (2026-08-05): TAM BO QUA phan nay,
  uu tien lam xong dong so huu/ho gia dinh/OCR chu cuoi truoc - se quay lai khi co
  quy tac day du hon. Dispatcher hien tai VAN CHI dung `ai_chu_cuoi` -> `chu_su_dung`
  nhu truoc, chua doc `ai_bien_dong`.
- 1 GCN co nhieu hon 1 "nhom" chu so huu cung luc (vd vua 1 ca nhan vua 1 cap vo
  chong dong so huu) - khong gap trong 00004.xlsx, dispatcher hien chi tra ve KET
  QUA DAU TIEN khop, chua gop nhieu nhom lai.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.land_normalizer.models.payload import ChuSuDungSoHuu, HoGiaDinhData, NguoiSoHuu, VoChongData
from app.land_normalizer.models.raw_record import AiChuSuDungItem, ChuSuDungThucDia, RawRecord
from app.land_normalizer.resolvers.base import FieldResult, ResolverContext
from app.land_normalizer.resolvers.support import first_non_empty, parse_vn_date, to_int

LOAI_DOI_TUONG_CA_NHAN = 1
LOAI_DOI_TUONG_HO_GIA_DINH = 2
LOAI_DOI_TUONG_VO_CHONG = 3

_LOAI_GIAY_TO_MAP = {
    "chứng minh nhân dân": "CMND",
    "căn cước công dân": "CCCD",
}


def _normalize_loai_giay_to(raw: str | None) -> str | None:
    if not raw:
        return None
    return _LOAI_GIAY_TO_MAP.get(raw.strip().casefold(), raw.strip() or None)


def _normalize_name(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.strip().casefold().split())


def _split_vo_chong_names(ten_chu: str | None) -> list[str]:
    """OCR doi khi tra ve CA 2 nguoi trong 1 item duy nhat, ten noi boi ' va '
    (vd 'Nguyễn Anh Tuấn và Trần Thị Dường') thay vi 2 item rieng biet."""
    if not ten_chu:
        return []
    parts = re.split(r"\s+và\s+", ten_chu.strip(), flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _find_matching_chu_su_dung(
    attempts: list[RawRecord], ten_chu: str | None
) -> ChuSuDungThucDia | None:
    target = _normalize_name(ten_chu)
    if not target:
        return None
    for attempt in attempts:
        for entry in attempt.chu_su_dung:
            if _normalize_name(entry.ten) == target:
                return entry
    return None


def _resolve_so_giay_to(
    ten: str | None,
    own_so_giay_to: str | None,
    own_loai_giay_to_raw: str | None,
    attempts: list[RawRecord],
) -> tuple[str | None, str | None]:
    """Quy tac 3 (dung chung cho Vo chong va Ca nhan): trung ten voi chu_su_dung
    thi uu tien so CCCD tu do; khong thi dung giay to cua chinh nguon OCR."""
    matched = _find_matching_chu_su_dung(attempts, ten)
    if matched and matched.so_giay_to:
        return matched.so_giay_to, "CCCD"
    return own_so_giay_to, _normalize_loai_giay_to(own_loai_giay_to_raw)


# --------------------------------------------------------------------------- #
# LoaiDoiTuong = 3 - "Vo chong"
# --------------------------------------------------------------------------- #


@dataclass
class _PickedSource:
    items: list[AiChuSuDungItem]
    source: str
    confidence: float


def _pick_vo_chong_source_items(attempts: list[RawRecord]) -> _PickedSource | None:
    chu_su_dung, csd_idx = first_non_empty(attempts, lambda r: r.ai_chu_su_dung)
    if not chu_su_dung:
        return None

    vo_chong_items = [
        item for item in chu_su_dung if _normalize_name(item.loai_doi_tuong) == "vợ chồng"
    ]
    if not vo_chong_items:
        return None  # khong co dau hieu day la truong hop Vo chong

    # Da xac nhan la Vo chong (qua nhan "Loại đối tượng") -> uu tien lay CHI TIET
    # tu chu cuoi neu co dung 2 nguoi (quy tac 1: OCR bien dong/chu cuoi dang tin
    # hon GCN goc).
    chu_cuoi, cc_idx = first_non_empty(attempts, lambda r: r.ai_chu_cuoi)
    if chu_cuoi:
        if len(chu_cuoi) == 2:
            return _PickedSource(
                items=chu_cuoi,
                source=(
                    f"attempts[{cc_idx}].ai_chu_cuoi "
                    f"(xac nhan 'Vo chong' qua attempts[{csd_idx}].ai_chu_su_dung)"
                ),
                confidence=0.9,
            )
        # chu_cuoi TON TAI nhung KHONG dung 2 nguoi (vd da sang ten sau bien dong,
        # gio chi con 1 chu) - quy tac "uu tien chu cuoi" (2026-08-06) nghia la
        # chinh su mau thuan nay la tin hieu nhan "Vo chong" tren GCN goc DA CU,
        # KHONG duoc dung nhan cu de ep ve Vo chong nua (bug thuc te: GCN
        # item_id=15394213, chu_cuoi chi con 1 nguoi "Nguyễn Thị Kiều Liên" - HSQ
        # da ghi nhan Ca nhan - nhung dispatcher cu van "vo vet" thanh Vo chong tu
        # 2 nguoi cu tren GCN goc). Tra ve None de nhuong cho CaNhanResolver (dung
        # dung chu_cuoi lam nguon chinh).
        return None

    if len(vo_chong_items) == 2:
        return _PickedSource(
            items=vo_chong_items,
            source=f"attempts[{csd_idx}].ai_chu_su_dung (Loại đối tượng=Vợ chồng)",
            confidence=0.75,
        )

    if len(vo_chong_items) == 1:
        names = _split_vo_chong_names(vo_chong_items[0].ten_chu)
        if len(names) == 2:
            base = vo_chong_items[0].model_dump(by_alias=True)
            synthesized = [
                AiChuSuDungItem(**{**base, "Tên chủ": name}) for name in names
            ]
            return _PickedSource(
                items=synthesized,
                source=f"attempts[{csd_idx}].ai_chu_su_dung[0] (ten ghep boi 'và')",
                confidence=0.5,
            )

    return None  # so luong nguoi gan nhan Vo chong khong ro rang (vd 3, 4 nguoi)


def _build_nguoi(item: AiChuSuDungItem, attempts: list[RawRecord]) -> NguoiSoHuu:
    so_giay_to, loai_giay_to = _resolve_so_giay_to(
        item.ten_chu, item.so_giay_to, item.loai_giay_to, attempts
    )
    matched = _find_matching_chu_su_dung(attempts, item.ten_chu)
    dia_chi = item.dia_chi or (matched.dia_chi_thuong_tru if matched else None)

    return NguoiSoHuu(
        Id=str(uuid.uuid4()),
        HoTen=item.ten_chu,
        NamSinh=to_int(item.nam_sinh),
        GioiTinh=item.gioi_tinh or None,
        LoaiGiayTo=loai_giay_to,
        SoGiayTo=so_giay_to,
        NgayCapGiayToISO8601=parse_vn_date(item.ngay_cap_dinh_danh),
        NoiCapGiayTo=item.noi_cap or None,
        DiaChi=dia_chi,
        DaChet=False,
    )


@dataclass
class VoChongResolver:
    field_path: str = "ChuSuDungSoHuus"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        picked = _pick_vo_chong_source_items(ctx.attempts)
        if picked is None:
            return FieldResult.missing(
                note="Khong xac dinh duoc day la truong hop Vo chong voi dung 2 nguoi"
            )

        wrapper_id = str(uuid.uuid4())
        entry = ChuSuDungSoHuu(
            Id=wrapper_id,
            LoaiDoiTuong=LOAI_DOI_TUONG_VO_CHONG,
            DataVoChong=VoChongData(
                Id=wrapper_id,
                NguoiThuNhat=_build_nguoi(picked.items[0], ctx.attempts),
                NguoiThuHai=_build_nguoi(picked.items[1], ctx.attempts),
            ),
        )
        return FieldResult(value=[entry], confidence=picked.confidence, source=picked.source)


# --------------------------------------------------------------------------- #
# LoaiDoiTuong = 2 - "Ho gia dinh"
# --------------------------------------------------------------------------- #


def _pick_ho_gia_dinh_source_items(attempts: list[RawRecord]) -> _PickedSource | None:
    chu_su_dung, csd_idx = first_non_empty(attempts, lambda r: r.ai_chu_su_dung)
    if not chu_su_dung:
        return None

    ho_items = [
        item for item in chu_su_dung if _normalize_name(item.loai_doi_tuong) == "hộ gia đình"
    ]
    if not ho_items:
        return None  # khong co dau hieu day la truong hop Ho gia dinh
    if len(ho_items) > 2:
        return None  # khong ro ai la chu ho/thanh vien - khong doan (xem PHAM VI CHUA XU LY)

    # Da xac nhan la Ho gia dinh (qua nhan "Loại đối tượng") -> uu tien lay CHI TIET
    # tu chu cuoi neu so luong khop dung voi so nguoi da gan nhan (cung tinh than
    # quy tac 1 cua Vo chong: OCR bien dong/chu cuoi dang tin hon GCN goc).
    chu_cuoi, cc_idx = first_non_empty(attempts, lambda r: r.ai_chu_cuoi)
    if chu_cuoi:
        if len(chu_cuoi) == len(ho_items):
            return _PickedSource(
                items=chu_cuoi,
                source=(
                    f"attempts[{cc_idx}].ai_chu_cuoi "
                    f"(xac nhan 'Hộ gia đình' qua attempts[{csd_idx}].ai_chu_su_dung)"
                ),
                confidence=0.9,
            )
        # chu_cuoi TON TAI nhung so nguoi KHONG khop voi nhan "Hộ gia đình" tren GCN
        # goc - cung mau bug/fix voi Vo chong o tren (2026-08-06): uu tien chu_cuoi,
        # KHONG ep ve Ho gia dinh cu. Tra ve None de nhuong cho CaNhanResolver.
        return None

    return _PickedSource(
        items=ho_items,
        source=f"attempts[{csd_idx}].ai_chu_su_dung (Loại đối tượng=Hộ gia đình)",
        confidence=0.75,
    )


@dataclass
class HoGiaDinhResolver:
    field_path: str = "ChuSuDungSoHuus"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        picked = _pick_ho_gia_dinh_source_items(ctx.attempts)
        if picked is None:
            return FieldResult.missing(
                note="Khong xac dinh duoc day la truong hop Ho gia dinh voi 1 hoac 2 nguoi"
            )

        wrapper_id = str(uuid.uuid4())
        chu_ho = _build_nguoi(picked.items[0], ctx.attempts)
        # Nguoi dung xac nhan 2026-08-05: nguoi DAU TIEN la Chu ho, nguoi THU HAI
        # (neu co) la vo/chong chu ho - theo thu tu xuat hien trong nguon da chon.
        vo_chong_chu_ho = (
            _build_nguoi(picked.items[1], ctx.attempts) if len(picked.items) == 2 else NguoiSoHuu()
        )
        entry = ChuSuDungSoHuu(
            Id=wrapper_id,
            LoaiDoiTuong=LOAI_DOI_TUONG_HO_GIA_DINH,
            DataHoGiaDinh=HoGiaDinhData(ChuHo=chu_ho, VoChongChuHo=vo_chong_chu_ho),
        )
        return FieldResult(value=[entry], confidence=picked.confidence, source=picked.source)


# --------------------------------------------------------------------------- #
# LoaiDoiTuong = 1 - "Ca nhan" / "Dong so huu" (N>1 nguoi, cung LoaiDoiTuong=1)
# --------------------------------------------------------------------------- #


def _ca_nhan_entry_from_ai(item: AiChuSuDungItem, attempts: list[RawRecord]) -> ChuSuDungSoHuu:
    wrapper_id = str(uuid.uuid4())
    # _build_nguoi da tu ap dung quy tac 3 (uu tien CCCD tu chu_su_dung khi trung
    # ten) va dien duoc NamSinh/GioiTinh/NgayCapGiayToISO8601/NoiCapGiayTo tu cung
    # nguon AiChuSuDungItem nhu Vo chong/Ho gia dinh - chi ghi de lai Id de khop voi
    # Id cua ChuSuDungSoHuu bao ngoai (1 nguoi/1 entry nen 2 Id nay phai trung nhau).
    nguoi = _build_nguoi(item, attempts).model_copy(update={"Id": wrapper_id})
    return ChuSuDungSoHuu(Id=wrapper_id, LoaiDoiTuong=LOAI_DOI_TUONG_CA_NHAN, DataCaNhan=nguoi)


def _dedup_chu_cuoi(items: list[AiChuSuDungItem]) -> list[AiChuSuDungItem]:
    """Loai cac ban ghi trung cung 1 nguoi trong `ai_chu_cuoi` (cung `so_giay_to`
    khac rong, hoac cung ten da chuan hoa neu thieu `so_giay_to`), giu ban ghi xuat
    hien dau tien - cung mau voi `thua_dat.py::_dedup_parcels` (loai trung
    `parcels_json` theo `ma_thua_dat`). Can thiet cho "dong so huu" (CaNhanResolver
    gio nhan MOI item trong `ai_chu_cuoi`, khac Vo chong/Ho gia dinh von da duoc bao
    ve tu nhien boi dieu kien khop DUNG so luong): phat hien qua du lieu that
    (so_gcn='010105578604196' trong 00004.xlsx) co item thu 3 trung `so_giay_to`
    voi item 1 (chi khac ten bi dinh rac OCR "Nội Ông ...") - khong phai nguoi thu
    3 that."""
    seen: set[str] = set()
    result: list[AiChuSuDungItem] = []
    for item in items:
        key = (item.so_giay_to or "").strip() or _normalize_name(item.ten_chu)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _ca_nhan_entry_from_thuc_dia(item: ChuSuDungThucDia, attempts: list[RawRecord]) -> ChuSuDungSoHuu:
    wrapper_id = str(uuid.uuid4())
    so_giay_to, loai_giay_to = _resolve_so_giay_to(
        item.ten,
        item.so_giay_to,
        "Căn cước công dân",  # chu_su_dung la du lieu quet CCCD tai cho
        attempts,
    )
    # ChuSuDungThucDia (chu_su_dung) khong co nam_sinh/gioi_tinh/ngay_cap/noi_cap -
    # cac field nay giu None, khac nhanh ai_chu_cuoi/AiChuSuDungItem o tren.
    nguoi = NguoiSoHuu(
        Id=wrapper_id,
        HoTen=item.ten,
        LoaiGiayTo=loai_giay_to,
        SoGiayTo=so_giay_to,
        DiaChi=item.dia_chi_thuong_tru,
        DaChet=False,
    )
    return ChuSuDungSoHuu(Id=wrapper_id, LoaiDoiTuong=LOAI_DOI_TUONG_CA_NHAN, DataCaNhan=nguoi)


@dataclass
class CaNhanResolver:
    """Tra ve N phan tu ChuSuDungSoHuu (N>=1), moi phan tu 1 nguoi, LoaiDoiTuong=1.
    N=1 la "Ca nhan" thong thuong; N>1 la "dong so huu" - KHONG co ma LoaiDoiTuong
    rieng, chi la nhieu entry Ca nhan doc lap trong cung mang (xac nhan 2026-08-05).
    KHONG dung nhan "Loại đối tượng"="Cá nhân" de xac nhan/dem so nguoi (xem ly do
    trong docstring dau file) - dem TRUC TIEP so nguoi trong `ai_chu_cuoi` (uu tien)
    hoac `chu_su_dung` (fallback khi khong co OCR nao)."""

    field_path: str = "ChuSuDungSoHuus"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        chu_cuoi, cc_idx = first_non_empty(ctx.attempts, lambda r: r.ai_chu_cuoi)
        if chu_cuoi:
            chu_cuoi = _dedup_chu_cuoi(chu_cuoi)
            entries = [_ca_nhan_entry_from_ai(item, ctx.attempts) for item in chu_cuoi]
            source = f"attempts[{cc_idx}].ai_chu_cuoi"
            confidence = 0.9
        else:
            chu_su_dung, csd_idx = first_non_empty(ctx.attempts, lambda r: r.chu_su_dung)
            if not chu_su_dung:
                return FieldResult.missing(
                    note="Khong co nguoi nao trong ai_chu_cuoi hoac chu_su_dung"
                )
            entries = [_ca_nhan_entry_from_thuc_dia(item, ctx.attempts) for item in chu_su_dung]
            source = f"attempts[{csd_idx}].chu_su_dung (khong co OCR de uu tien)"
            confidence = 0.6

        note = f"{len(entries)} nguoi -> dong so huu" if len(entries) > 1 else None
        return FieldResult(value=entries, confidence=confidence, source=source, note=note)


# --------------------------------------------------------------------------- #
# Dispatcher - resolver duy nhat duoc dang ky vao registry
# --------------------------------------------------------------------------- #


@dataclass
class ChuSuDungSoHuusResolver:
    field_path: str = "ChuSuDungSoHuus"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        # Thu tu uu tien: Vo chong / Ho gia dinh (xac nhan qua nhan "Loại đối
        # tượng", cu the/dang tin hon) TRUOC, Ca nhan/Dong so huu (khong dung nhan,
        # chi dem nguoi trong ai_chu_cuoi/chu_su_dung) la phuong an du phong CUOI
        # CUNG - neu thu Ca nhan truoc, no se "vo vet" ca truong hop that ra la Vo
        # chong/Ho gia dinh nhung dispatcher chua kip xac nhan qua nhan.
        for resolver in (VoChongResolver(), HoGiaDinhResolver(), CaNhanResolver()):
            result = resolver.resolve(ctx)
            if result.value is not None:
                return result
        return FieldResult.missing(
            note="Khong khop loai doi tuong nao da co quy tac (Vo chong, Ho gia dinh, Ca nhan/Dong so huu)"
        )


RESOLVERS = [ChuSuDungSoHuusResolver()]
