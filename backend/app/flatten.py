"""Làm phẳng GCN thành HÀNG (row) để xuất CSV / đẩy FME.

- Áp `review.overrides` (sửa tay khi hậu kiểm) lên bản sao `extractions` trước khi phẳng
  → hàng phản ánh dữ liệu ĐÃ DUYỆT, raw vẫn giữ nguyên trong Mongo.
- Grain: 1 hàng / 1 THỬA (theo Số hiệu tờ bản đồ + Số thứ tự thửa). Các trường GCN
  (Số phát hành, Số vào sổ, Ngày cấp, Chủ sử dụng) lặp lại; mục đích gộp theo từng thửa.
  GCN không có thửa → 1 hàng giữ metadata + thông tin GCN.
"""

import copy
import re
from typing import Any

from app.summary import format_page_range

_TOKEN = re.compile(r"\[(\d+)\]|([^.\[\]]+)")

# Thứ tự cột cố định (key = nhãn CSV). File cắt + File gốc + vùng trang lên đầu.
COLUMNS = [
    "Tệp cắt", "Tệp gốc", "Vùng trang gốc",
    "Số phát hành", "Số hiệu tờ bản đồ", "Số thứ tự thửa",
    "Diện tích", "Địa chỉ thửa", "Mục đích sử dụng",
    "Số vào sổ", "Ngày cấp", "Mã vạch",
    "Chủ sử dụng", "Số chủ",
    "Thông tin nhà ở", "Biến động gần nhất", "Số biến động",
    "Tên hiển thị", "Trạng thái", "Hậu kiểm",
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


def effective_extractions(extractions: list, overrides: dict | None,
                          deleted: list | None = None) -> list:
    """extractions sau hậu kiểm: áp overrides + loại các bản ghi bị xoá.
    Bản xoá được thay bằng tombstone (result=None) để GIỮ VỊ TRÍ — cut_index/
    overrides theo index không bị lệch; downstream bỏ qua vì không có entry."""
    data = apply_overrides(extractions, overrides)
    for i in deleted or []:
        if isinstance(i, int) and 0 <= i < len(data) and isinstance(data[i], dict):
            data[i] = {"page_indices": data[i].get("page_indices") or [],
                       "result": None, "_deleted": True}
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
    """1 mongo gcn doc → list hàng (1/thửa). Mỗi hàng kèm File gốc + File cắt (nếu có).
    Helper field `_gcn_id` / `_cut_index` (gạch dưới) chỉ dùng để UI preview — không vào CSV."""
    review = doc.get("review") or {}
    ext = effective_extractions(doc.get("extractions"), review.get("overrides"), review.get("deleted"))
    cuts = {c.get("index"): c for c in (doc.get("cuts") or []) if isinstance(c, dict)}
    gcn_id = doc.get("_id") or ""

    base = {
        "Tệp gốc": doc.get("filename") or "",
        "Tên hiển thị": review.get("display_name") or "",
        "Trạng thái": doc.get("status") or "",
        "Hậu kiểm": review.get("status") or "unreviewed",
        "Số trang": doc.get("page_count") or 0,
        "gcn_id": gcn_id,
        "_gcn_id": gcn_id,
    }

    rows = []
    for ri, rec in enumerate(ext):
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        entries = res.get("Đăng ký", []) if isinstance(res, dict) else []
        cut = cuts.get(ri)
        page_idx = rec.get("page_indices") or (cut or {}).get("page_indices") or []
        recbase = {
            **base,
            "Tệp cắt": (cut or {}).get("name") or "",
            "Vùng trang gốc": format_page_range(page_idx),
            "_cut_index": ri if cut else None,
        }
        for e in entries:
            if not isinstance(e, dict):
                continue
            gcn = e.get("Giấy chứng nhận") or {}
            chu = [c for c in (e.get("Chủ sử dụng") or []) if isinstance(c, dict)]
            thua = [t for t in (e.get("Thửa đất") or []) if isinstance(t, dict)]
            nha = [n for n in (e.get("Thông tin nhà ở") or []) if isinstance(n, dict)]
            bd = [b for b in (e.get("Biến động") or []) if isinstance(b, dict)]

            common = {
                **recbase,
                "Số phát hành": gcn.get("Số phát hành", ""),
                "Mã vạch": gcn.get("Mã vạch", ""),
                "Số vào sổ": gcn.get("Số vào sổ", ""),
                "Ngày cấp": gcn.get("Ngày cấp", ""),
                "Chủ sử dụng": _join([c.get("Tên chủ") for c in chu]),
                "Số chủ": len(chu),
                "Thông tin nhà ở": _join([n.get("Loại tài sản gắn liền với đất") for n in nha]),
                "Biến động gần nhất": (bd[-1].get("Nội dung biến động", "") if bd else ""),
                "Số biến động": len(bd),
            }
            if not thua:
                rows.append({**{c: "" for c in COLUMNS}, **common})
                continue
            for t in thua:
                muc_dich = [m.get("Loại mục đích") for m in (t.get("Mục đích sử dụng") or [])
                            if isinstance(m, dict) and m.get("Loại mục đích")]
                rows.append({
                    **common,
                    "Số thứ tự thửa": t.get("Số thứ tự thửa", ""),
                    "Số hiệu tờ bản đồ": t.get("Số hiệu tờ bản đồ", ""),
                    "Diện tích": t.get("Diện tích", ""),
                    "Địa chỉ thửa": t.get("Địa chỉ", ""),
                    "Mục đích sử dụng": _join(muc_dich),
                })

    if not rows:
        return [{**{c: "" for c in COLUMNS}, **base, "Tệp cắt": ""}]
    return rows
