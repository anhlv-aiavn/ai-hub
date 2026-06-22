"""Làm phẳng GCN thành HÀNG (row) để xuất CSV / đẩy FME.

- Áp `review.overrides` (sửa tay khi hậu kiểm) lên bản sao `extractions` trước khi phẳng
  → hàng phản ánh dữ liệu ĐÃ DUYỆT, raw vẫn giữ nguyên trong Mongo.
- Grain: 1 hàng / 1 entry "Đăng ký" (1 GCN). Chủ/Thửa/Mục đích gộp bằng " ; ".
"""

import copy
import re
from typing import Any

_TOKEN = re.compile(r"\[(\d+)\]|([^.\[\]]+)")

# Thứ tự cột cố định (key = nhãn CSV).
COLUMNS = [
    "Tên hiển thị", "Tệp", "Trạng thái", "Hậu kiểm",
    "Số phát hành", "Mã vạch", "Số vào sổ", "Ngày cấp",
    "Chủ sử dụng", "Số chủ",
    "Số thứ tự thửa", "Số hiệu tờ bản đồ", "Diện tích", "Địa chỉ thửa",
    "Mục đích sử dụng", "Số thửa",
    "Thông tin nhà ở", "Biến động gần nhất", "Số biến động",
    "Số trang", "gcn_id",
]


def _tokens(path: str) -> list:
    out: list = []
    for m in _TOKEN.finditer(path):
        idx, key = m.group(1), m.group(2)
        out.append(int(idx) if idx is not None else key)
    return out


def apply_overrides(extractions: list, overrides: dict | None) -> list:
    """Trả bản sao extractions đã áp overrides {path: value}. Bỏ qua path không khớp."""
    data = copy.deepcopy(extractions or [])
    for path, val in (overrides or {}).items():
        toks = _tokens(path)
        if not toks:
            continue
        cur: Any = data
        ok = True
        for t in toks[:-1]:
            try:
                cur = cur[t]
            except (KeyError, IndexError, TypeError):
                ok = False
                break
        if not ok or not toks:
            continue
        last = toks[-1]
        try:
            cur[last] = val
        except (KeyError, IndexError, TypeError):
            continue
    return data


def _join(vals: list) -> str:
    return " ; ".join(str(v).strip() for v in vals if v not in (None, "", []))


def _entries(extractions: list) -> list[dict]:
    out = []
    for rec in extractions or []:
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        if isinstance(res, dict):
            for e in res.get("Đăng ký", []) or []:
                if isinstance(e, dict):
                    out.append(e)
    return out


def flatten_doc(doc: dict) -> list[dict]:
    """1 mongo gcn doc → list hàng (1/entry). Nếu không có entry → 1 hàng rỗng giữ metadata."""
    review = doc.get("review") or {}
    ext = apply_overrides(doc.get("extractions"), review.get("overrides"))
    entries = _entries(ext)

    base = {
        "Tên hiển thị": review.get("display_name") or "",
        "Tệp": doc.get("filename") or "",
        "Trạng thái": doc.get("status") or "",
        "Hậu kiểm": review.get("status") or "unreviewed",
        "Số trang": doc.get("page_count") or 0,
        "gcn_id": doc.get("_id") or "",
    }
    if not entries:
        return [{**{c: "" for c in COLUMNS}, **base}]

    rows = []
    for e in entries:
        gcn = e.get("Giấy chứng nhận") or {}
        chu = [c for c in (e.get("Chủ sử dụng") or []) if isinstance(c, dict)]
        thua = [t for t in (e.get("Thửa đất") or []) if isinstance(t, dict)]
        nha = [n for n in (e.get("Thông tin nhà ở") or []) if isinstance(n, dict)]
        bd = [b for b in (e.get("Biến động") or []) if isinstance(b, dict)]
        first_thua = thua[0] if thua else {}
        muc_dich = []
        for t in thua:
            for m in (t.get("Mục đích sử dụng") or []):
                if isinstance(m, dict) and m.get("Loại mục đích"):
                    muc_dich.append(m["Loại mục đích"])

        rows.append({
            **base,
            "Số phát hành": gcn.get("Số phát hành", ""),
            "Mã vạch": gcn.get("Mã vạch", ""),
            "Số vào sổ": gcn.get("Số vào sổ", ""),
            "Ngày cấp": gcn.get("Ngày cấp", ""),
            "Chủ sử dụng": _join([c.get("Tên chủ") for c in chu]),
            "Số chủ": len(chu),
            "Số thứ tự thửa": first_thua.get("Số thứ tự thửa", ""),
            "Số hiệu tờ bản đồ": first_thua.get("Số hiệu tờ bản đồ", ""),
            "Diện tích": first_thua.get("Diện tích", ""),
            "Địa chỉ thửa": first_thua.get("Địa chỉ", ""),
            "Mục đích sử dụng": _join(muc_dich),
            "Số thửa": len(thua),
            "Thông tin nhà ở": _join([n.get("Loại tài sản gắn liền với đất") for n in nha]),
            "Biến động gần nhất": (bd[-1].get("Nội dung biến động", "") if bd else ""),
            "Số biến động": len(bd),
        })
    return rows
