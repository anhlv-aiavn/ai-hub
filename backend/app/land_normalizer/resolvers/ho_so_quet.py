"""Resolver cho HoSoQuets.

Giong ThuaDats/ChuSuDungSoHuus, day la 1 "whole-list resolver": field_path =
"HoSoQuets" (khong co dau `.` hay `[]`) nen pipeline._apply_dotted_path gan thang
danh sach da dung san vao Payload.HoSoQuets.

Quy tac nghiep vu (cung cap 2026-08-06, xac nhan bang cach goi truc tiep
`POST /api/item/search` voi token that trong `data/.hsq_session.json` va doi chieu
voi ground-truth `tests/fixtures/00004/expected_payload.json`):
- `pdf_path` (sau khi `ApiSourceAdapter` da tach object thanh
  `RawRecord.pdf_path` = object_name va `RawRecord.pdf_path_bucket_name` =
  bucket_name - xem ingestion/api_source.py) la nguon DUY NHAT dung cho
  `HoSoQuet` - day la duong dan file PDF DA GHEP/CHINH THUC cua GCN (dang
  "aa_nhom_3/{ma_xa}/{ten_file}.pdf"), KHAC voi `gcn_paths` (danh sach anh/pdf
  SCAN THO nguoi dung tai hien truong upload, KHONG co bucket_name di kem nen
  khong the dien HoSoQuet.BucketName ma khong doan) - resolver nay KHONG dung
  `gcn_paths`.
- `HoSoQuet.SoGcn` lay tu `so_gcn` (giong `ctx.so_gcn`, nguon uu tien nhat cho ma
  GCN trong toan bo pipeline - xem giay_chung_nhan.py).
- `HoSoQuet.BucketName`: uu tien `pdf_path_bucket_name` (chi API moi co). Excel
  (`ExcelSourceAdapter`) KHONG parse cot `pdf_path` thanh object (xem
  ingestion/excel_source.py, `pdf_path` khong nam trong `_JSON_OBJECT_FIELDS`) nen
  luon thieu bucket_name rieng - fallback ve hang so `_DEFAULT_BUCKET_NAME`
  ("hni-kh515-new"): quan sat NHAT QUAN 100% (8/8 mau) tren ca ground-truth Excel
  (`tests/fixtures/00004/expected_payload.json`) LAN 6 ban ghi that goi truc tiep
  tu API (package_id=1467) - chua gap bucket nao khac, nhung day van la GIA DINH
  (khong phai field co nguon truc tiep cho Excel) can xem lai neu xuat hien bucket
  khac trong du lieu tuong lai.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.land_normalizer.models.payload import HoSoQuet
from app.land_normalizer.resolvers.base import FieldResult, ResolverContext
from app.land_normalizer.resolvers.support import first_non_empty

_DEFAULT_BUCKET_NAME = "hni-kh515-new"


@dataclass
class HoSoQuetsResolver:
    field_path: str = "HoSoQuets"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        pdf_path, p_idx = first_non_empty(ctx.attempts, lambda r: r.pdf_path)
        if not pdf_path:
            return FieldResult.missing(
                note="Khong co pdf_path (duong dan file PDF da quet/ghep) o bat ky attempt nao"
            )

        bucket_name, b_idx = first_non_empty(ctx.attempts, lambda r: r.pdf_path_bucket_name)
        if bucket_name:
            source = f"attempts[{p_idx}].pdf_path + attempts[{b_idx}].pdf_path_bucket_name"
            confidence = 0.9
        else:
            bucket_name = _DEFAULT_BUCKET_NAME
            source = f"attempts[{p_idx}].pdf_path + BucketName mac dinh '{_DEFAULT_BUCKET_NAME}' (nguon khong co bucket_name rieng, vd Excel)"
            confidence = 0.6

        entry = HoSoQuet(BucketName=bucket_name, FilePath=pdf_path, SoGcn=ctx.so_gcn)
        return FieldResult(value=[entry], confidence=confidence, source=source)


RESOLVERS = [HoSoQuetsResolver()]
