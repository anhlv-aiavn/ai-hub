"""Danh sách chi nhánh cố định (đơn vị nghiệp vụ). Người dùng thuộc 1 trong số này;
là chiều gộp để thống kê tiến độ. Nguồn chuẩn duy nhất — frontend fetch qua API."""

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

_VALID = set(BRANCHES)


def is_valid_branch(name: str | None) -> bool:
    return bool(name) and name in _VALID
