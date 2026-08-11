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
- `HoSoQuet.BucketName`: `pdf_path_bucket_name` — ở ai-hub, adapter
  (`land_normalizer_adapter.py::raw_record_from_cut`) LUÔN điền giá trị này từ
  bucket MinIO ĐÍCH thật của kênh QC Sync (`s3_connections.bucket` của
  `qc_sync_configs.dest_connection_id`), nên nhánh "thiếu bucket_name riêng" chỉ
  xảy ra khi tra cứu đó lỗi. KHÔNG fallback về 1 tên bucket cố định (bản gốc
  `vpdd-don-ai` dùng `_DEFAULT_BUCKET_NAME = "hni-kh515-new"` — tên bucket HSQ
  của KHÁCH HÀNG KHÁC, hoàn toàn sai với ai-hub) — để trống thay vì hiện 1 tên
  bucket sai nhưng có vẻ hợp lệ.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.land_normalizer.models.payload import HoSoQuet
from app.land_normalizer.resolvers.base import FieldResult, ResolverContext
from app.land_normalizer.resolvers.support import first_non_empty


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
            bucket_name = None
            source = f"attempts[{p_idx}].pdf_path (thieu bucket_name)"
            confidence = 0.4

        entry = HoSoQuet(BucketName=bucket_name, FilePath=pdf_path, SoGcn=ctx.so_gcn)
        return FieldResult(value=[entry], confidence=confidence, source=source)


RESOLVERS = [HoSoQuetsResolver()]
