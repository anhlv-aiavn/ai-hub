"""Resolver cho Nhas.

Quy tac nghiep vu (cung cap 2026-08-01): lay thang tu du lieu OCR (`ai_tai_san`) -
khac voi ThuaDats, khong co nguon "nguoi dung nhap" rieng cho nha (khong co truong
tuong duong `parcels_json` cho tai san gan lien voi dat).

CACH AP DUNG: moi item trong `ai_tai_san` sinh ra 1 `Nha`. Chi anh xa cac field co
nguon ro rang/truc tiep; con lai de `None` thay vi doan - da doi chieu voi
`payload.json` mau (GCN `10101070103`) va khop CHINH XAC (tat ca field khong anh xa
duoc trong Nha mau cung deu la `null`).

Cac field KHONG co nguon trong `ai_tai_san`, de `None`:
- `SoNha`: `ai_tai_san` chi co "Địa chỉ" gop chung, khong tach rieng so nha.
- `DienTichSuDung`, `DienTichSoHuuChung`, `DienTichSoHuuRieng`: khong co field
  tuong ung trong `ai_tai_san` (chi co "Diện tích sàn", "Diện tích xây dựng").
- `NamXayDung`, `NamHoanThanh`: khong co field tuong ung.
- `LoaiTaiSanNhaId`: can bang tra cuu ma nghiep vu cho van ban "Loại tài sản gắn
  liền với đất" (vd "Nhà ở", "Căn hộ"...) - chua duoc cung cap.
- `LoaiCapNhaId`: can bang tra cuu ma nghiep vu cho van ban "Cấp hạng" (vd "Cấp
  III", "Cấp II"...) - chua duoc cung cap.

GIA DINH CHUA XAC NHAN:
- `Id` sinh moi (uuid4) vi khong co nguon du lieu nao cung cap - giong `DaMucDich`,
  khac `ThuaDat.Id` (lay tu `ma_thua_dat`).
- `SoTang` giu nguyen dinh dang chuoi tu OCR (vd "02" KHONG bi rut gon thanh "2").
- Chua xu ly truong hop 1 GCN co nhieu `ai_tai_san` item ung voi nhieu thua dat
  khac nhau (chua gap trong 00004.xlsx) - hien moi item OCR sinh 1 Nha doc lap,
  khong doi chieu voi ThuaDats tuong ung.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.land_normalizer.models.payload import Nha
from app.land_normalizer.models.raw_record import AiTaiSanItem
from app.land_normalizer.resolvers.base import FieldResult, ResolverContext
from app.land_normalizer.resolvers.support import first_non_empty, to_float


def _build_nha(item: AiTaiSanItem) -> Nha:
    return Nha(
        Id=str(uuid.uuid4()),
        SoNha=None,
        SoTang=str(item.so_tang) if item.so_tang not in (None, "") else None,
        LoaiTaiSanNhaId=None,
        LoaiCapNhaId=None,
        DienTichSan=to_float(item.dien_tich_san),
        DienTichXayDung=to_float(item.dien_tich_xay_dung),
        DienTichSuDung=None,
        DienTichSoHuuChung=None,
        DienTichSoHuuRieng=None,
        NamXayDung=None,
        NamHoanThanh=None,
    )


@dataclass
class NhasResolver:
    field_path: str = "Nhas"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        ai_tai_san, idx = first_non_empty(ctx.attempts, lambda r: r.ai_tai_san)
        if not ai_tai_san:
            return FieldResult.missing(note="Khong co ai_tai_san (OCR) o bat ky attempt nao")

        nhas = [_build_nha(item) for item in ai_tai_san]
        return FieldResult(value=nhas, confidence=0.85, source=f"attempts[{idx}].ai_tai_san")


RESOLVERS = [NhasResolver()]
