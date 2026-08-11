"""Helper dung chung cho resolver khi phai quet qua nhieu attempt cua cung 1 so_gcn.

Day la tinh huong lap lai o rat nhieu field (OCR nhieu lan, lan sau co the bo sung
hoac lam mat du lieu lan truoc), nen tach thanh ham tien ich thay vi moi resolver tu
viet lai vong lap.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable, TypeVar

from app.land_normalizer.models.raw_record import RawRecord

T = TypeVar("T")

_EMPTY = (None, "", [])


def first_non_empty(
    attempts: list[RawRecord], getter: Callable[[RawRecord], T | None]
) -> tuple[T | None, int | None]:
    """Duyet attempts THEO DUNG THU TU xuat hien trong nguon, tra ve gia tri
    non-empty dau tien `getter` tim duoc va index cua attempt sinh ra no (de ghi vao
    FieldResult.source, phuc vu truy vet). Tra (None, None) neu khong attempt nao co.

    Luu y: day chi la 1 chien luoc ("lay gia tri khong rong dau tien"). Nhieu field se
    can chien luoc khac (vd "lay gia tri xuat hien o nhieu attempt nhat", "uu tien
    attempt co ai_ocr_status == OK") - khi do viet logic rieng trong resolver, ham nay
    khong ep dung cho moi truong hop.
    """
    for idx, attempt in enumerate(attempts):
        value = getter(attempt)
        if value not in _EMPTY:
            return value, idx
    return None, None


def parse_vn_date_obj(raw: str | int | float | None) -> date | None:
    """Giong parse_vn_date nhung tra ve `date` thay vi chuoi ISO - dung khi resolver
    can SO SANH ngay (vd phan loai giay chung nhan theo moc thoi gian), khong chi
    hien thi. Tra ve None neu khong parse duoc, khong raise."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_vn_date(raw: str | int | float | None) -> str | None:
    """Doi ngay dang 'dd/mm/yyyy' (thuong gap trong du lieu OCR) sang ISO8601
    ('yyyy-mm-dd'). Day la chuyen doi CO HOC (khong can quyet dinh nghiep vu) nen
    dat o day de dung chung, khac voi cac quyet dinh "lay gia tri tu nguon nao"
    (thuoc ve tung resolver). Tra ve None neu khong parse duoc, khong raise -
    du lieu OCR loi dinh dang khong duoc phep lam sap ca pipeline."""
    parsed = parse_vn_date_obj(raw)
    return parsed.isoformat() if parsed else None


def to_int(raw: str | int | float | None) -> int | None:
    """Ep gia tri OCR (co the la '', so nguyen, hoac chuoi so) ve int, tra ve
    None neu khong hop le - khong raise, cung ly do nhu parse_vn_date."""
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def to_float(raw: str | int | float | None) -> float | None:
    """Giong to_int nhung giu phan thap phan - dung cho dien tich."""
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def to_bool(raw: bool | str | int | float | None) -> bool | None:
    """Ep gia tri OCR ve bool. Chap nhan bool that, chuoi 'true'/'false'/'1'/'0'
    (khong phan biet hoa/thuong), so 0/1. Tra ve None neu rong hoac khong nhan
    dang duoc - KHONG doan True/False khi khong ro (khac voi Python `bool("abc")`
    la True), de resolver tu quyet dinh gia tri mac dinh khi can."""
    if isinstance(raw, bool):
        return raw
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return bool(raw)
    normalized = raw.strip().casefold()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    return None
