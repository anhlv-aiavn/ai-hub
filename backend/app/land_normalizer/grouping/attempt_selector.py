"""Gom cac RawRecord cung mot so_gcn lai voi nhau - KHONG chon/loai bo attempt nao.

Trong 00004.xlsx, cung mot GCN co the xuat hien nhieu lan (nhieu lan OCR/nhieu lan
nop don, vd so_gcn "10101070103" o ca dong 4 va 5 voi noi dung OCR lech nhau: dong 4
co ai_gcn_so_vao_so con dong 5 thi khong, nguoc lai dong 5 co mo ta bien dong chi
tiet hon dong 4). Neu chon 1 ban ghi "dai dien" ngay tai buoc gom nay se lam mat du
lieu dung tu ban ghi bi loai.

Vi vay o day CHI gom theo so_gcn, giu nguyen toan bo danh sach attempts. Viec "field
X lay tu attempt nao, uu tien the nao khi cac attempt mau thuan nhau" la quyet dinh
nghiep vu CUA TUNG FIELD, nen duoc de cho tung Resolver tu quyet dinh (xem
`resolvers/support.py::first_non_empty` cho pattern pho bien nhat), khong xu ly mot
lan cho tat ca o buoc gom nay.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from app.land_normalizer.models.raw_record import RawRecord


def group_by_so_gcn(records: Iterable[RawRecord]) -> dict[str, list[RawRecord]]:
    groups: dict[str, list[RawRecord]] = defaultdict(list)
    for record in records:
        groups[record.so_gcn].append(record)
    return dict(groups)
