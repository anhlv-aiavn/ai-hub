"""Rút gọn output engine (`extractions`) thành cột tóm tắt cho bảng trích xuất
và suy ra group_key (Số phát hành chính) để gom tờ bổ sung về GCN gốc."""

from typing import Any


def _entries(extractions: list[dict]) -> list[dict]:
    """Duyệt mọi record → mọi entry 'Đăng ký'. Trả phẳng list entry kèm page_indices."""
    out: list[dict] = []
    for rec in extractions or []:
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        if not isinstance(res, dict):
            continue
        for entry in res.get("Đăng ký", []) or []:
            if isinstance(entry, dict):
                out.append(entry)
    return out


def collect_so_phat_hanhs(extractions: list[dict]) -> list[str]:
    out: list[str] = []
    for entry in _entries(extractions):
        gcn = entry.get("Giấy chứng nhận")
        if isinstance(gcn, dict):
            sph = gcn.get("Số phát hành")
            if sph:
                out.append(str(sph))
    return out


def group_key_of(extractions: list[dict]) -> str | None:
    """Khóa gom = Số phát hành đầu tiên tìm được. None nếu chưa có."""
    sphs = collect_so_phat_hanhs(extractions)
    return sphs[0] if sphs else None


def _first(entry: dict, *keys: str) -> str:
    gcn = entry.get("Giấy chứng nhận") if isinstance(entry, dict) else None
    if isinstance(gcn, dict):
        for k in keys:
            v = gcn.get(k)
            if v:
                return str(v)
    return ""


def summarize(extractions: list[dict]) -> dict[str, Any]:
    """Cột tóm tắt cho 1 GCN (1 PDF). Lấy entry đầu làm đại diện + đếm tổng."""
    entries = _entries(extractions)
    sphs = collect_so_phat_hanhs(extractions)
    head = entries[0] if entries else {}

    chu_names: list[str] = []
    to_ban_do: list[str] = []
    so_thua: list[str] = []
    if isinstance(head, dict):
        for c in head.get("Chủ sử dụng", []) or []:
            if isinstance(c, dict) and c.get("Tên chủ"):
                chu_names.append(str(c["Tên chủ"]))
        for t in head.get("Thửa đất", []) or []:
            if not isinstance(t, dict):
                continue
            if t.get("Số hiệu tờ bản đồ"):
                to_ban_do.append(str(t["Số hiệu tờ bản đồ"]))
            if t.get("Số thứ tự thửa"):
                so_thua.append(str(t["Số thứ tự thửa"]))

    return {
        "so_phat_hanh": _first(head, "Số phát hành"),
        "so_vao_so": _first(head, "Số vào sổ"),
        "ngay_cap": _first(head, "Ngày cấp"),
        "chu_su_dung": chu_names,
        "to_ban_do": to_ban_do,
        "so_thua": so_thua,
        "gcn_count": len(entries),
        "so_phat_hanhs": sphs,
    }
