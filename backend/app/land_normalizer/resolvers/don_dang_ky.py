"""Resolver cho nhom DonDangKy.

Moi vi du minh hoa 2 kieu resolver khac nhau; cac field con lai cua DonDangKy
(MaDonDangKy, GhiChu) CHUA duoc dang ky - pipeline se bao "missing resolver" cho
chung, day la hanh vi mong muon cho toi khi co quy tac nghiep vu that.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.land_normalizer.resolvers.base import FieldResult, ResolverContext
from app.land_normalizer.resolvers.support import first_non_empty


@dataclass
class XaIdResolver:
    """XaId lay tu thua dat dau tien nguoi thu thap ghi nhan tai cho (parcels_json).
    Day la du lieu "cung" (co GPS di kem) nen tin cay hon OCR.

    Quet qua TAT CA attempt cung so_gcn (khong chi attempt cuoi) vi lan OCR/nop don
    truoc co the co parcels_json con lan sau lai thieu, hoac nguoc lai."""

    field_path: str = "DonDangKy.XaId"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        value, idx = first_non_empty(
            ctx.attempts,
            lambda r: r.parcels_json[0].ma_xa if r.parcels_json else None,
        )
        if value is None:
            return FieldResult.missing(
                note="Khong co parcels_json[0].ma_xa o bat ky attempt nao"
            )
        return FieldResult(
            value=value,
            confidence=1.0,
            source=f"attempts[{idx}].parcels_json[0].ma_xa",
        )


@dataclass
class NgayDangKyResolver:
    """Ngay dang ky = ngay he thong xu ly ban ghi nay (khong doc tu nguon)."""

    field_path: str = "DonDangKy.NgayDangKyISO8601"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        return FieldResult(
            value=ctx.processed_at.date().isoformat(),
            confidence=1.0,
            source="processed_at (system clock)",
        )


RESOLVERS = [XaIdResolver(), NgayDangKyResolver()]
