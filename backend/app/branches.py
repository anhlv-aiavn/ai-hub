"""Danh sách chi nhánh — đọc từ `site_config` (Mongo), cache trong-tiến-trình TTL
~30s bắt buộc (nhiều API/worker process, `invalidate_site_cache()` chỉ xóa cache
cục bộ; TTL đảm bảo mọi process hội tụ sau ≤30s). `BRANCHES` giữ lại làm giá trị
seed ban đầu (dùng bởi `main.py::_seed_site_config`), KHÔNG còn là nguồn chuẩn."""

import time

from app.db import site_config

BRANCHES: list[str] = [
    "Chi nhánh Khu vực Ba Đình – Hoàn Kiếm – Đống Đa",
    "Chi nhánh Hai Bà Trưng",
    "Chi nhánh Hoàng Mai",
    "Chi nhánh Thanh Xuân",
    "Chi nhánh Cầu Giấy",
    "Chi nhánh Nam Từ Liêm",
    "Chi nhánh Bắc Từ Liêm",
    "Chi nhánh Hà Đông",
    "Chi nhánh Tây Hồ",
    "Chi nhánh Long Biên",
    "Chi nhánh Thanh Trì",
    "Chi nhánh Thường Tín",
    "Chi nhánh Phú Xuyên",
    "Chi nhánh Hoài Đức",
    "Chi nhánh Đan Phượng",
    "Chi nhánh Phúc Thọ",
    "Chi nhánh Sơn Tây",
    "Chi nhánh Ba Vì",
    "Chi nhánh Đông Anh",
    "Chi nhánh Mê Linh",
    "Chi nhánh Sóc Sơn",
    "Chi nhánh Thạch Thất",
    "Chi nhánh Quốc Oai",
    "Chi nhánh Gia Lâm",
    "Chi nhánh Thanh Oai",
    "Chi nhánh Chương Mỹ",
    "Chi nhánh Ứng Hòa",
    "Chi nhánh Mỹ Đức",
]

_TTL = 30.0
_cache: dict | None = None
_cache_at: float = 0.0


async def _load() -> dict:
    """Đọc site_config qua cache TTL. Miss/hết hạn → query Mongo."""
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < _TTL:
        return _cache
    doc = await site_config().find_one({"_id": "site"}) or {}
    _cache = doc
    _cache_at = now
    return doc


def invalidate_site_cache() -> None:
    """Fast-path cục bộ sau khi PATCH /settings/site — process khác tự hội tụ
    theo TTL, không cần biết sự kiện này (xem module docstring)."""
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


async def get_branches() -> list[str]:
    doc = await _load()
    return doc.get("branches") or list(BRANCHES)


async def is_valid_branch(name: str | None) -> bool:
    if not name:
        return False
    return name in await get_branches()
