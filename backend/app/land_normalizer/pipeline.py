"""Pipeline chinh: nhan RawRecord (bat ke tu Excel hay API) -> Payload chuan hoa.

Day la lop duy nhat "biet" ca input contract lan output contract; ban than no khong
biet gi ve Excel/HTTP. `run_from_records` la ham nen goi tu ca CLI dev (doc Excel)
lan tu API handler sau nay (nhan 1 RawRecord tu request).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

from app.land_normalizer.grouping.attempt_selector import group_by_so_gcn
from app.land_normalizer.models.payload import Payload
from app.land_normalizer.models.raw_record import RawRecord
from app.land_normalizer.registry import ResolverRegistry, build_default_registry
from app.land_normalizer.resolvers.base import FieldResult, ResolverContext


@dataclass
class ResolutionEntry:
    field_path: str
    result: FieldResult


@dataclass
class BuildResult:
    so_gcn: str
    payload: Payload
    resolutions: list[ResolutionEntry] = field(default_factory=list)

    @property
    def missing_fields(self) -> list[str]:
        return [r.field_path for r in self.resolutions if r.result.value is None]


def _apply_dotted_path(payload: Payload, field_path: str, value: Any) -> None:
    """Ho tro duong dan don gian dang 'Group.Field' (khong co mang [])."""
    if "[]" in field_path:
        raise NotImplementedError(
            f"'{field_path}': duong dan co mang [] can resolver theo tung item, "
            "chua duoc ho tro trong pipeline nay (se bo sung khi lam ThuaDats/Nhas/"
            "GiayChungNhans)."
        )
    *groups, leaf = field_path.split(".")
    target: Any = payload
    for group in groups:
        target = getattr(target, group)
    setattr(target, leaf, value)


def build_payload(
    attempts: list[RawRecord],
    registry: ResolverRegistry,
    processed_at: datetime | None = None,
) -> BuildResult:
    """`attempts`: tat ca ban ghi nguon trung so_gcn (thuong la 1, co the nhieu hon
    neu cung GCN duoc OCR/nop nhieu lan) - xem ResolverContext ve ly do khong gom
    san thanh 1 ban ghi truoc khi resolve."""
    ctx = ResolverContext(attempts=attempts, processed_at=processed_at or datetime.now(timezone.utc))
    payload = Payload()
    resolutions: list[ResolutionEntry] = []

    for field_path in registry.field_paths():
        resolver = registry.get(field_path)
        assert resolver is not None
        result = resolver.resolve(ctx)
        resolutions.append(ResolutionEntry(field_path=field_path, result=result))
        if result.value is not None:
            _apply_dotted_path(payload, field_path, result.value)

    return BuildResult(so_gcn=ctx.so_gcn, payload=payload, resolutions=resolutions)


def run_from_records(
    records: Iterable[RawRecord],
    registry: ResolverRegistry | None = None,
    processed_at: datetime | None = None,
) -> Iterator[BuildResult]:
    """Diem vao chung: 1 luong RawRecord (tu Excel hay API deu duoc) -> BuildResult,
    gom theo so_gcn nhung KHONG loai bo attempt nao (xem group_by_so_gcn)."""
    registry = registry or build_default_registry()
    for attempts in group_by_so_gcn(records).values():
        yield build_payload(attempts, registry=registry, processed_at=processed_at)
