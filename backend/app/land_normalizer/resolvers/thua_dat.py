"""Resolver cho ThuaDats.

Giong ChuSuDungSoHuus, day la 1 "whole-list resolver": field_path = "ThuaDats" (khong
co dau `.` hay `[]`) nen pipeline._apply_dotted_path gan thang ca danh sach ThuaDat da
dung san vao Payload.ThuaDats - khong can co che gan-tung-field-trong-mang (xem
pipeline.md).

Quy tac nghiep vu (cung cap 2026-08-01, se bo sung them):
- DiaChi, SoHieuToBanDo (so to), SoThuTuThua (so thua): uu tien du lieu NGUOI DUNG
  NHAP (`parcels_json`, thu thap tai hien truong, co GPS di kem nen dang tin hon).
- DienTich va "cac thong tin khac" (muc dich su dung...): lay tu OCR (`ai_thua_dat`).

CACH AP DUNG:
- `parcels_json` la nguon CHINH quyet dinh CO BAO NHIEU thua dat va danh tinh cua
  tung thua (Id, XaId, SoHieuToBanDo, SoThuTuThua, DiaChi). Neu khong co
  `parcels_json` (khong co du lieu nguoi dung nhap), resolver tra ve missing -
  KHONG tu dung OCR de doan so luong/danh tinh thua dat, dung tinh than voi
  CaNhanResolver (xem chu_su_dung.py): nguon "yeu" hon khong duoc dung khi thieu
  nguon uu tien, tranh doan sai.
- Da phat hien 2 GCN trong 00004.xlsx (`so_gcn` = '010105578604196', 'BG 064902')
  co 2 phan tu `parcels_json` NHUNG trung `ma_thua_dat`/`so_to`/`so_thua` - chi la 2
  lan ghi nhan GPS/anh khac nhau cho CUNG 1 thua dat (`so_thu_tu_gcn` = "1" va "2"),
  khong phai 2 thua dat khac nhau. Resolver loai trung theo `ma_thua_dat` truoc khi
  dung ThuaDat.
- `ai_thua_dat` (OCR) duoc khop voi tung thua dat theo VI TRI (index) - trong toan
  bo 00004.xlsx chua gap GCN nao co nhieu hon 1 thua dat PHAN BIET nen chua co du
  lieu that de kiem chung khop theo cach nao dang tin hon (vd theo dia chi/so
  thua). Neu so luong thua dat phan biet > so luong item OCR, cac thua dat "du ra"
  se khong co DienTich/DaMucDichs (van co day du DiaChi/SoHieuToBanDo/SoThuTuThua
  tu parcels_json).

GIA DINH CHUA XAC NHAN (can review khi co quy tac tiep theo):
- DienTichPhapLy: KHONG co nguon ro rang (khac voi DienTich do dac thuc te) -
  de trong (None).
- SoHieuToBanDoCu/SoThuTuThuaCu: contract co field nay nhung RawRecord/ParcelThucDia
  khong co field "cu" tuong ung (chi co so_to/so_thua hien tai) - de None.
- DaMucDichs (muc dich su dung long trong ThuaDat) duoc coi la mot phan cua "cac
  thong tin khac... lay o OCR" nen cung duoc dien o day, nhung CHI cac field co the
  anh xa co hoc/truc tiep tu OCR (KyHieuMucDich, DienTich, SuDungChung,
  NguonGocChiTiet, ThoiHanSuDungLauDai/ThoiHanSuDung, NgayHetHanSuDungISO8601).
  `DatTranhChap`, `GhiChu` khong co nguon du lieu nao trong 00004.xlsx nen de
  mac dinh False/None.
- `LoaiMucDichSuDungDatId` (cap nhat 2026-08-05, sua lai cung ngay sau khi doi
  chieu voi API HSQ that + toan bo 3 file Excel mau): nguoi dung cung cap catalog
  `loaiMdsdd` (xem `app.land_normalizer.catalogs.LOAI_MDSDD`) - tra cuu CHI khi khop
  CHINH XAC, theo thu tu uu tien:
  1. `item.ma_mdsd` ("Mã MĐSD") - **la ID SO cua catalog TRUC TIEP** (vd 192), KHONG
     phai ky hieu chuoi nhu tung gia dinh sai ban dau - xac nhan qua ca API tim
     kiem HSQ (`/api/item/search`, field nay tra ve int) lan du lieu Excel (100%
     cac dong co gia tri deu la so catalog id). Khop bang `catalogs.loai_mdsdd_by_id`.
  2. `item.mdsd_chuan` ("MĐSD chuẩn") - **luon co dang "KY_HIEU - Ten day du"** (vd
     "ODT - Đất ở tại đô thị", xac nhan tren toan bo du lieu that) - tach lay phan
     KY_HIEU truoc dau " - " dau tien, khop bang `catalogs.loai_mdsdd_by_ky_hieu`.
  3. `item.loai_muc_dich` ("Loại mục đích") - van ban tu do it dang tin nhat, khop
     bang `catalogs.loai_mdsdd_by_ten` (co bo qua tu dem "tại").
  Khop duoc (bat ky buoc nao) thi GHI DE `KyHieuMucDich` bang ky hieu chuan cua
  catalog (thay vi van ban OCR tho). KHONG khop o CA 3 buoc (vd van ban qua chung
  chung nhu "Đất ở" - mo ho giua ODT/ONT) thi giu nguyen hanh vi cu: giu van ban OCR
  tho trong `KyHieuMucDich`, `LoaiMucDichSuDungDatId` = None - KHONG fuzzy-match/doan.
- ThoiHanSuDungLauDai (cap nhat 2026-08-06, quy tac nghiep vu xac nhan): dat o
  (KyHieuMucDich - sau khi da tra catalog - khop "ODT"/"ONT", hoac van ban tho
  "Đất ở"/"Đất ở đô thị"/"Đất ở nông thôn"... khi khong khop catalog) LUON duoc
  KHANG DINH la True, GHI DE bat ky van ban "Thời hạn sử dụng" OCR nao (ke ca khi
  trai nguoc hoac de trong) - theo phap luat dat o duoc su dung on dinh lau dai.
  Cac loai dat khac (khong phai dat o): giu quy tac cu - True khi va chi khi van
  ban "Thời hạn sử dụng" (da chuan hoa) dung bang "lâu dài"; nguoc lai False va
  giu nguyen van ban vao ThoiHanSuDung.
- Id cua ThuaDat lay truc tiep tu `parcels_json[i].ma_thua_dat` (ma he thong thu
  thap da sinh san) - KHONG sinh moi, khac voi DaMucDich.Id (sinh moi vi khong co
  nguon). Dung voi cau truc quan sat duoc trong payload.json mau (ThuaDat.Id =
  "w7er8rr74yc6" = parcels_json[0].ma_thua_dat).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.land_normalizer import catalogs
from app.land_normalizer.models.payload import DaMucDich, ThuaDat
from app.land_normalizer.models.raw_record import AiMucDichSuDungItem, AiThuaDatItem, ParcelThucDia
from app.land_normalizer.resolvers.base import FieldResult, ResolverContext
from app.land_normalizer.resolvers.support import first_non_empty, parse_vn_date, to_bool, to_float, to_int


def _ky_hieu_prefix(mdsd_chuan: str | None) -> str | None:
    """'MĐSD chuẩn' thuc te LUON co dang 'KY_HIEU - Ten day du' (vd 'ODT - Đất ở
    tại đô thị', xac nhan tren toan bo du lieu that - API tim kiem HSQ lan ca 3
    file Excel mau) - tach lay phan KY_HIEU truoc dau ' - ' dau tien."""
    if not mdsd_chuan:
        return None
    ky_hieu, sep, _ = mdsd_chuan.partition(" - ")
    return ky_hieu.strip() if sep else None


_DAT_O_KY_HIEU = {"odt", "ont"}


def _is_dat_o(ky_hieu_muc_dich: str | None) -> bool:
    """Dat o (do thi ODT hoac nong thon ONT, ke ca van ban tho "Đất ở" chua phan
    biet duoc do thi/nong thon khi khong khop catalog) - dung de khang dinh
    ThoiHanSuDungLauDai theo quy tac nghiep vu (xem docstring dau file)."""
    normalized = (ky_hieu_muc_dich or "").strip().casefold()
    return normalized in _DAT_O_KY_HIEU or normalized.startswith("đất ở")


def _lookup_loai_mdsdd(item: AiMucDichSuDungItem) -> dict[str, object] | None:
    """Khop CHINH XAC (theo thu tu uu tien mo ta trong docstring dau file). Tra ve
    None neu khong khop nguon nao - KHONG fuzzy-match."""
    ma_mdsd_id = to_int(item.ma_mdsd)
    if ma_mdsd_id is not None:
        found = catalogs.loai_mdsdd_by_id(ma_mdsd_id)
        if found is not None:
            return found
    found = catalogs.loai_mdsdd_by_ky_hieu(_ky_hieu_prefix(item.mdsd_chuan))
    if found is not None:
        return found
    return catalogs.loai_mdsdd_by_ten(item.loai_muc_dich)


def _dedup_parcels(parcels: list[ParcelThucDia]) -> list[ParcelThucDia]:
    """Loai cac ban ghi trung cung 1 thua dat (cung ma_thua_dat, hoac cung
    so_to+so_thua neu thieu ma_thua_dat), giu ban ghi xuat hien dau tien."""
    seen: set[object] = set()
    result: list[ParcelThucDia] = []
    for parcel in parcels:
        key = parcel.ma_thua_dat or (parcel.so_to, parcel.so_thua)
        if key in seen:
            continue
        seen.add(key)
        result.append(parcel)
    return result


def _build_da_muc_dich(
    item: AiMucDichSuDungItem, so_hieu_to_ban_do: object, so_thu_tu_thua: object
) -> DaMucDich:
    thoi_han_raw = (item.thoi_han_su_dung or "").strip()
    if thoi_han_raw.casefold() == "lâu dài":
        thoi_han_lau_dai, thoi_han_su_dung = True, None
    elif thoi_han_raw:
        thoi_han_lau_dai, thoi_han_su_dung = False, thoi_han_raw
    else:
        thoi_han_lau_dai, thoi_han_su_dung = False, None

    catalog_entry = _lookup_loai_mdsdd(item)
    if catalog_entry is not None:
        loai_mdsdd_id = catalog_entry["id"]
        ky_hieu_muc_dich = catalog_entry["ky_hieu_muc_dich"]
    else:
        loai_mdsdd_id = None
        ky_hieu_muc_dich = item.loai_muc_dich or None

    if _is_dat_o(ky_hieu_muc_dich):
        thoi_han_lau_dai, thoi_han_su_dung = True, None

    return DaMucDich(
        Id=str(uuid.uuid4()),
        SoHieuToBanDo=so_hieu_to_ban_do,
        SoThuTuThua=so_thu_tu_thua,
        LoaiMucDichSuDungDatId=loai_mdsdd_id,
        KyHieuMucDich=ky_hieu_muc_dich,
        DienTich=to_float(item.dien_tich),
        ThoiHanSuDungLauDai=thoi_han_lau_dai,
        ThoiHanSuDung=thoi_han_su_dung,
        NgayHetHanSuDungISO8601=parse_vn_date(item.ngay_het_han_su_dung),
        DatTranhChap=False,  # khong co nguon du lieu
        SuDungChung=to_bool(item.su_dung_chung) or False,
        GhiChu=None,  # khong co nguon du lieu
        NguonGocChiTiet=item.nguon_goc_chi_tiet or None,
    )


def _build_thua_dat(parcel: ParcelThucDia, ai_item: AiThuaDatItem | None) -> ThuaDat:
    return ThuaDat(
        Id=parcel.ma_thua_dat or str(uuid.uuid4()),
        XaId=parcel.ma_xa,
        SoHieuToBanDo=parcel.so_to,
        SoThuTuThua=parcel.so_thua,
        DienTich=to_float(ai_item.dien_tich) if ai_item else None,
        DienTichPhapLy=None,  # khong co nguon ro rang
        SoHieuToBanDoCu=None,  # khong co nguon (khong co field "cu" trong ParcelThucDia)
        SoThuTuThuaCu=None,  # khong co nguon (khong co field "cu" trong ParcelThucDia)
        DiaChi=parcel.dia_chi_chi_tiet,
        DaMucDichs=[
            _build_da_muc_dich(muc_dich, parcel.so_to, parcel.so_thua)
            for muc_dich in (ai_item.muc_dich_su_dung if ai_item else [])
        ],
    )


@dataclass
class ThuaDatsResolver:
    field_path: str = "ThuaDats"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        parcels, p_idx = first_non_empty(ctx.attempts, lambda r: r.parcels_json)
        if not parcels:
            return FieldResult.missing(
                note="Khong co parcels_json (du lieu nguoi dung nhap) de xac dinh thua dat"
            )

        distinct_parcels = _dedup_parcels(parcels)
        ai_thua_dat, a_idx = first_non_empty(ctx.attempts, lambda r: r.ai_thua_dat)
        ai_thua_dat = ai_thua_dat or []

        thua_dats = [
            _build_thua_dat(parcel, ai_thua_dat[i] if i < len(ai_thua_dat) else None)
            for i, parcel in enumerate(distinct_parcels)
        ]

        source = f"attempts[{p_idx}].parcels_json (loai trung theo ma_thua_dat)"
        if ai_thua_dat:
            source += f" + attempts[{a_idx}].ai_thua_dat (khop theo vi tri)"
            confidence = 0.85 if len(ai_thua_dat) >= len(distinct_parcels) else 0.6
        else:
            confidence = 0.5  # co danh tinh thua dat nhung khong co OCR cho DienTich/DaMucDichs

        return FieldResult(value=thua_dats, confidence=confidence, source=source)


RESOLVERS = [ThuaDatsResolver()]
