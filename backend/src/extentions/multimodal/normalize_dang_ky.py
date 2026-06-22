"""
src/extentions/multimodal/normalize_dang_ky.py

Pure-function module để normalize cấu trúc "Đăng ký" output của VLM.

Vấn đề: model thỉnh thoảng trả về big-component (Chủ sử dụng, Thửa đất,
Thông tin nhà ở, Biến động) sai vị trí — lồng trong subobject khác, hoặc
là 1 entry độc lập thiếu key wrapper.

Module này KHÔNG có Mongo I/O và KHÔNG có CLI — để dễ dùng từ pipeline (extract_path)
và từ add-on backfill CLI cùng lúc, tránh circular import.

API chính:
    normalize_dang_ky(data) -> dict
    normalize_doc(mongo_doc) -> dict
    detect_deviations(data) -> list[str]
    detect_deviations_in_doc(mongo_doc) -> list[tuple[int, list[str]]]
"""

from __future__ import annotations

import copy
from typing import Any


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

BIG_COMPONENTS = ("Chủ sử dụng", "Thửa đất", "Thông tin nhà ở", "Biến động")

# Signature keys để classify orphan item (item không có key wrapper big-component).
SIGNATURES: dict[str, set[str]] = {
    "Chủ sử dụng": {
        "Loại đối tượng", "Tên chủ", "Năm sinh", "Giới tính",
        "Loại giấy tờ", "Số giấy tờ", "Ngày cấp định danh",
        "Nơi cấp", "Địa chỉ",
    },
    "Thửa đất": {
        "Số thứ tự thửa", "Số hiệu tờ bản đồ", "Diện tích",
        "Mục đích sử dụng",
    },
    "Thông tin nhà ở": {
        "Loại tài sản gắn liền với đất", "Khu nhà chung cư, nhà hỗn hợp",
        "Nhà chung cư", "Số căn hộ", "Diện tích xây dựng", "Diện tích sàn",
        "Hình thức sở hữu", "Thời hạn sở hữu", "Cấp hạng", "Kết cấu",
        "Số tầng",
    },
    "Biến động": {
        "Thời gian", "Nội dung biến động",
    },
}

CHU_SCHEMA = {
    "Loại đối tượng": "",
    "Tên chủ": "",
    "Năm sinh": "",
    "Giới tính": "",
    "Loại giấy tờ": "",
    "Số giấy tờ": "",
    "Ngày cấp định danh": "",
    "Nơi cấp": "",
    "Địa chỉ": "",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_str(v: Any) -> str:
    """Coerce value về str, an toàn với None/int/float/dict."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v).strip()


def _classify_orphan(item: Any, min_overlap: int = 1, min_ratio: float = 0.4) -> str | None:
    """Phân loại 1 orphan item theo signature.

    - min_overlap: phải khớp ít nhất N key trong signature.
    - min_ratio: tỉ lệ key của item nằm trong signature ≥ ratio (tránh false-positive).
    """
    if not isinstance(item, dict) or not item:
        return None
    keys = set(item.keys())
    best, best_score = None, 0
    for comp, sig in SIGNATURES.items():
        overlap = len(keys & sig)
        if overlap > best_score:
            best_score, best = overlap, comp
    if best is None or best_score < min_overlap:
        return None
    if best_score / max(len(keys), 1) < min_ratio:
        return None
    return best


def _strip_big_components(node: Any) -> Any:
    """Deep-copy, loại các key thuộc BIG_COMPONENTS — dùng trước khi bucket."""
    if isinstance(node, dict):
        return {k: copy.deepcopy(v) for k, v in node.items() if k not in BIG_COMPONENTS}
    return copy.deepcopy(node)


def _collect(node: Any, bucket: dict[str, list]) -> None:
    """Duyệt đệ quy, gặp key big-component thì:
      - append _strip_big_components(item) vào bucket
      - recurse vào item gốc để bắt nested deeper.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in BIG_COMPONENTS:
                items = value if isinstance(value, list) else [value]
                for item in items:
                    if isinstance(item, dict):
                        bucket[key].append(_strip_big_components(item))
                        _collect(item, bucket)
            else:
                _collect(value, bucket)
    elif isinstance(node, list):
        for item in node:
            _collect(item, bucket)


def _normalize_chu(c: dict) -> dict:
    out = {}
    for k, default in CHU_SCHEMA.items():
        v = c.get(k)
        out[k] = v if v is not None else default
    return out


def _has_signal(item: dict) -> bool:
    """Item có 'signal' của 1 big-component (≥1 signature key)."""
    if not isinstance(item, dict):
        return False
    keys = set(item.keys())
    return any(keys & sig for sig in SIGNATURES.values())


def _is_real_entry(item: Any) -> bool:
    """Real entry = có Giấy chứng nhận HOẶC có ít nhất 1 key big-component."""
    if not isinstance(item, dict):
        return False
    if "Giấy chứng nhận" in item:
        return True
    return any(k in BIG_COMPONENTS for k in item.keys())


def _dedup_chu(items: list[dict]) -> list[dict]:
    """Dedup Chủ sử dụng theo (Tên chủ, Số giấy tờ). Safe với None/int."""
    seen = set()
    out = []
    for c in items:
        if not isinstance(c, dict):
            continue
        key = (_safe_str(c.get("Tên chủ")), _safe_str(c.get("Số giấy tờ")))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _dedup_bien_dong(items: list[dict]) -> list[dict]:
    """Dedup Biến động theo (Thời gian, Nội dung biến động). Safe với None."""
    seen = set()
    out = []
    for b in items:
        if not isinstance(b, dict):
            continue
        tg = _safe_str(b.get("Thời gian"))
        nd = _safe_str(b.get("Nội dung biến động"))
        if not tg and not nd:
            continue
        key = (tg, nd)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "Thời gian": b.get("Thời gian") or "",
            "Nội dung biến động": b.get("Nội dung biến động") or "",
        })
    return out


def _build_entry(main: dict, orphans: list[tuple[str, dict]]) -> dict:
    bucket: dict[str, list] = {k: [] for k in BIG_COMPONENTS}
    _collect(main, bucket)
    for kind, item in orphans:
        if isinstance(item, dict):
            bucket[kind].append(_strip_big_components(item))
            _collect(item, bucket)

    gcn = main.get("Giấy chứng nhận", {}) if isinstance(main, dict) else {}
    chu_list = [_normalize_chu(c) for c in bucket["Chủ sử dụng"] if _has_signal(c)]
    chu_list = _dedup_chu(chu_list)
    bien_dong = _dedup_bien_dong(bucket["Biến động"])

    return {
        "Giấy chứng nhận": gcn if isinstance(gcn, dict) else {},
        "Chủ sử dụng":     chu_list,
        "Thửa đất":        bucket["Thửa đất"],
        "Thông tin nhà ở": bucket["Thông tin nhà ở"],
        "Biến động":       bien_dong,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_dang_ky(data: Any) -> dict:
    """Normalize {"Đăng ký": [...]} hoặc list entries."""
    if isinstance(data, dict):
        entries = data.get("Đăng ký", [data])
    elif isinstance(data, list):
        entries = data
    else:
        return {"Đăng ký": []}

    groups: list[dict] = []
    current: dict | None = None

    for e in entries:
        if not isinstance(e, dict):
            continue
        if _is_real_entry(e):
            if current is not None:
                groups.append(current)
            current = {"main": e, "orphans": []}
        else:
            kind = _classify_orphan(e)
            if current is not None and kind:
                current["orphans"].append((kind, e))

    if current is not None:
        groups.append(current)

    out_entries = [_build_entry(g["main"], g["orphans"]) for g in groups]
    return {"Đăng ký": out_entries}


def normalize_doc(doc: dict) -> dict:
    """Normalize 1 mongo document có 'extractions'."""
    if not isinstance(doc, dict) or not doc.get("extractions"):
        return doc
    new_doc = copy.deepcopy(doc)
    for ex in new_doc["extractions"]:
        result = ex.get("result")
        if isinstance(result, dict):
            ex["result"] = normalize_dang_ky(result)
    return new_doc


def normalize_extractions(extractions: list[dict]) -> list[dict]:
    """Normalize in-place danh sách extractions (như output của detect_and_extract)."""
    for rec in extractions or []:
        if not isinstance(rec, dict):
            continue
        result = rec.get("result")
        if isinstance(result, dict):
            rec["result"] = normalize_dang_ky(result)
    return extractions


def detect_deviations(data: Any) -> list[str]:
    """Phát hiện lệch cấu trúc trong Đăng ký. Empty list = không có vấn đề."""
    deviations: list[str] = []

    if isinstance(data, dict):
        entries = data.get("Đăng ký", [data])
    elif isinstance(data, list):
        entries = data
    else:
        return [f"Input không phải dict/list (type={type(data).__name__})"]

    if not isinstance(entries, list):
        return [f"'Đăng ký' không phải list (type={type(entries).__name__})"]

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            deviations.append(f"Đăng ký[{i}]: không phải dict (type={type(entry).__name__})")
            continue

        if not _is_real_entry(entry):
            kind = _classify_orphan(entry)
            if kind:
                deviations.append(
                    f"Đăng ký[{i}]: orphan — thiếu wrapper, looks like '{kind}' "
                    f"(keys={list(entry.keys())})"
                )
            else:
                deviations.append(
                    f"Đăng ký[{i}]: entry không classify được, không có Giấy chứng nhận "
                    f"(keys={list(entry.keys())})"
                )
            continue

        gcn = entry.get("Giấy chứng nhận")
        if isinstance(gcn, dict):
            for nested in BIG_COMPONENTS:
                if nested in gcn:
                    deviations.append(
                        f"Đăng ký[{i}].Giấy chứng nhận có '{nested}' nested (nên ở entry level)"
                    )

        for comp in BIG_COMPONENTS:
            val = entry.get(comp)
            if not isinstance(val, list):
                continue
            for j, item in enumerate(val):
                if not isinstance(item, dict):
                    continue
                for nested in BIG_COMPONENTS:
                    if nested in item:
                        deviations.append(
                            f"Đăng ký[{i}].{comp}[{j}] có nested '{nested}' (nên ở entry level)"
                        )

        chu_list = entry.get("Chủ sử dụng") or []
        seen_chu = set()
        for k, c in enumerate(chu_list):
            if not isinstance(c, dict):
                continue
            key = (_safe_str(c.get("Tên chủ")), _safe_str(c.get("Số giấy tờ")))
            if key in seen_chu and any(key):
                deviations.append(
                    f"Đăng ký[{i}].Chủ sử dụng[{k}]: trùng (Tên={key[0]!r}, "
                    f"Số giấy tờ={key[1]!r})"
                )
            seen_chu.add(key)

        bd_list = entry.get("Biến động") or []
        for k, b in enumerate(bd_list):
            if not isinstance(b, dict):
                continue
            if not (b.get("Thời gian") or b.get("Nội dung biến động")):
                deviations.append(
                    f"Đăng ký[{i}].Biến động[{k}]: rỗng (không có Thời gian/Nội dung)"
                )

    return deviations


def detect_deviations_in_doc(doc: dict) -> list[tuple[int, list[str]]]:
    """Wrap detect_deviations cho 1 mongo doc. Trả về [(ex_idx, deviations)]."""
    out = []
    for i, ex in enumerate(doc.get("extractions") or []):
        result = ex.get("result")
        if not isinstance(result, dict):
            continue
        devs = detect_deviations(result)
        if devs:
            out.append((i, devs))
    return out
