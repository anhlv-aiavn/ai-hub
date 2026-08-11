"""Adapter chuyển dữ liệu OCR của chính ai-hub (`qc_items.ocr`, pipeline QC Sync)
thành `RawRecord` — input contract của `app.land_normalizer` (port từ dự án
`vpdd-don-ai`, xem docs/algorithm.md §10).

Đây là ĐIỂM DUY NHẤT "biết" hình dạng dữ liệu OCR của ai-hub, thay thế hoàn toàn
`ingestion/api_source.py` (gọi API HSQ thật) của dự án gốc — ai-hub không có quan
hệ gì với hệ thống HSQ, chỉ dùng lại thuật toán chuẩn hoá (`land_normalizer`)
cho dữ liệu OCR của chính mình.

Schema JSON OCR của ai-hub (`Đăng ký` → `Giấy chứng nhận`/`Chủ sử dụng`/
`Thửa đất`/`Thông tin nhà ở`/`Biến động`, xem `src/extentions/multimodal/prompt.py`)
khớp gần như 1:1 với các field alias `ai_chu_su_dung`/`ai_thua_dat`/`ai_tai_san`/
`ai_bien_dong` mà `RawRecord` đã định nghĩa sẵn (models Vietnamese-key alias),
nên hầu như chỉ cần gán thẳng, không cần biến đổi.

KHÔNG merge "attempts" nhiều lần OCR trùng GCN (ADR-002 của vpdd-don-ai) — mỗi
cut tự đứng thành 1 `RawRecord` độc lập ở v1 này (xem docs/features_issues.md,
Issue mở "chưa gộp GCN quét lại nhiều lần")."""

from __future__ import annotations

from typing import Any

from app.land_normalizer.models.raw_record import RawRecord
from src.extentions.multimodal.chu_cuoi import chu_cuoi_for_entry


def first_entry(record: dict) -> dict:
    """`records[ri]["result"]["Đăng ký"][0]` — entry đầu tiên, khớp đúng cách
    `run_job._entry_sph`/`_build_cuts` đã dùng để đặt tên/SPH cho cut[ri] (1 cut
    = 1 record; record có >1 entry "Đăng ký" là trường hợp hiếm, chỉ entry đầu
    được dùng, không mở rộng thêm ở đây)."""
    result = record.get("result") if isinstance(record, dict) else None
    if not isinstance(result, dict):
        return {}
    entries = result.get("Đăng ký") or []
    for e in entries:
        if isinstance(e, dict):
            return e
    return {}


def raw_record_from_cut(entry: dict, cut: dict, ward_code: str, item_id: str) -> RawRecord:
    """`entry` = `first_entry(records[cut["index"]])`. `ward_code` = tên kênh QC
    Sync (`qc_sync_configs.name`, đặt trùng mã Phường/Xã theo quy ước đã có)."""
    gcn: dict[str, Any] = entry.get("Giấy chứng nhận") or {}
    so_gcn = (
        cut.get("so_phat_hanh")
        or gcn.get("Số phát hành")
        or f"{item_id}-cut{cut.get('index')}"
    )
    return RawRecord(
        so_gcn=str(so_gcn),
        ai_gcn_so_phat_hanh=gcn.get("Số phát hành"),
        ai_gcn_ma_vach=gcn.get("Mã vạch"),
        ai_gcn_so_vao_so=gcn.get("Số vào sổ"),
        ai_gcn_ngay_cap=gcn.get("Ngày cấp"),
        ai_chu_su_dung=entry.get("Chủ sử dụng") or [],
        ai_chu_cuoi=chu_cuoi_for_entry(entry).get("chu") or [],
        ai_thua_dat=entry.get("Thửa đất") or [],
        ai_tai_san=entry.get("Thông tin nhà ở") or [],
        ai_bien_dong=entry.get("Biến động") or [],
        parcels_json=[{"ma_xa": ward_code}] if ward_code else [],
        pdf_path=cut.get("s3_key"),
        source_row_ref=f"qc_items/{item_id}/cuts/{cut.get('index')}",
    )
