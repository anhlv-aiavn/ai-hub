"""Rút gọn output engine (`extractions`) thành cột tóm tắt cho bảng trích xuất
và suy ra group_key (Số phát hành chính) để gom tờ bổ sung về GCN gốc."""

from typing import Any

from app.vn_text import strip_diacritics


def format_page_range(indices: list | None) -> str:
    """List index trang 0-based → chuỗi 1-based gọn, gộp đoạn liên tiếp.
    [0,1,2,4] → '1–3, 5'. Rỗng → ''."""
    nums = sorted({int(i) + 1 for i in (indices or []) if isinstance(i, int)})
    if not nums:
        return ""
    parts: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            parts.append(str(start) if start == prev else f"{start}–{prev}")
            start = prev = n
    parts.append(str(start) if start == prev else f"{start}–{prev}")
    return ", ".join(parts)


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


def _entry_summary(entry: dict) -> dict:
    gcn = entry.get("Giấy chứng nhận") if isinstance(entry, dict) else None
    gcn = gcn if isinstance(gcn, dict) else {}
    chu, to_ban_do, so_thua = [], [], []
    for c in entry.get("Chủ sử dụng", []) or []:
        if isinstance(c, dict) and c.get("Tên chủ"):
            chu.append(str(c["Tên chủ"]))
    for t in entry.get("Thửa đất", []) or []:
        if not isinstance(t, dict):
            continue
        if t.get("Số hiệu tờ bản đồ"):
            to_ban_do.append(str(t["Số hiệu tờ bản đồ"]))
        if t.get("Số thứ tự thửa"):
            so_thua.append(str(t["Số thứ tự thửa"]))
    return {
        "so_phat_hanh": str(gcn.get("Số phát hành") or ""),
        "so_vao_so": str(gcn.get("Số vào sổ") or ""),
        "ngay_cap": str(gcn.get("Ngày cấp") or ""),
        "chu_su_dung": chu,
        # Bản bỏ dấu + chữ thường của "chu_su_dung" — cho phép tìm không phân
        # biệt dấu/hoa-thường (xem app/routes/gcn.py, search theo `q`).
        "chu_su_dung_norm": [strip_diacritics(c) for c in chu],
        "to_ban_do": to_ban_do,
        "so_thua": so_thua,
    }


def per_gcn(extractions: list[dict], cuts: list[dict] | None = None) -> list[dict]:
    """1 hàng / 1 GCN (mỗi entry 'Đăng ký') — để bảng tách 1 file → nhiều giấy.
    Gắn cut_index (record) + page_count của file cắt tương ứng."""
    cutmap = {c.get("index"): c for c in (cuts or []) if isinstance(c, dict)}
    out: list[dict] = []
    for ri, rec in enumerate(extractions or []):
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        entries = res.get("Đăng ký", []) if isinstance(res, dict) else []
        cut = cutmap.get(ri)
        page_idx = rec.get("page_indices") or []
        pages = len(page_idx)
        cut_pages = (cut or {}).get("page_count") or pages
        for e in entries:
            if not isinstance(e, dict):
                continue
            row = _entry_summary(e)
            row["cut_index"] = ri if cut else None
            row["page_count"] = cut_pages
            row["page_indices"] = page_idx
            row["page_range"] = format_page_range(page_idx)
            out.append(row)
    return out
