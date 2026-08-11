"""Khung resolver - moi field/nhom field trong payload dich co 1 resolver rieng.

Muc dich: khi ban cung cap quy tac nghiep vu cho 1 field, ta chi them/sua 1 resolver
(1 class nho, test duoc doc lap) ma khong dung den cac field khac hay pipeline chinh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.land_normalizer.models.raw_record import RawRecord


@dataclass
class ResolverContext:
    """Toan bo thong tin 1 resolver co the can toi de suy ra gia tri 1 field.

    `attempts` chua TAT CA cac ban ghi nguon trung `so_gcn` (co the la 1 hoac nhieu,
    xem `grouping/attempt_selector.py`). Cố tinh KHONG gom san thanh 1 ban ghi "dai
    dien" - moi resolver tu quyet dinh lay/gop du lieu tu attempt nao, vi do la
    quyet dinh nghiep vu rieng cua tung field (xem `resolvers/support.py`)."""

    attempts: list[RawRecord]
    processed_at: datetime
    lookups: dict[str, Any] = field(default_factory=dict)

    @property
    def so_gcn(self) -> str:
        return self.attempts[0].so_gcn


@dataclass
class FieldResult:
    value: Any
    confidence: float = 1.0  # 0..1, huu ich khi can review thu cong sau nay
    source: str | None = None  # vd "parcels_json[0].ma_xa" - de truy vet
    note: str | None = None  # canh bao/ghi chu (vd fallback, khong chac chan)

    @classmethod
    def missing(cls, note: str | None = None) -> "FieldResult":
        return cls(value=None, confidence=0.0, source=None, note=note)


class Resolver(Protocol):
    """field_path dung dau cham + [] de tro toi vi tri trong Payload, vd:
    'DonDangKy.XaId', 'GiayChungNhans[].GiayChungNhan.NgayVaoSoISO8601'."""

    field_path: str

    def resolve(self, ctx: ResolverContext) -> FieldResult: ...
