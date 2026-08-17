"""Logic DÙNG CHUNG giữa 2 script đối chiếu tên tệp `db.gcn` với tên file trong
(các) kho MinIO/S3 nguồn — chuẩn hoá tên, lọc từ khoá GT/GCN, loại lô test, và
đọc/ghi cache liệt kê theo TỪNG NGUỒN (`s3_connections._id`) riêng biệt.

KHÔNG tự chạy được — import từ `app.scripts.quet_minio_ra_cache` (liệt kê 1
nguồn ra cache) và `app.scripts.mapping_du_lieu_cu` (đọc cache, đối chiếu, ghi
Mongo).

── Vì sao tổ chức cache/field theo `source_connection_id` ──────────────────
`source_connection_id` LUÔN là `_id` thật của 1 document trong collection
`s3_connections` (role="source", xem `app/routes/s3_connections.py`) — cơ chế
mở file hiện tại của cột "Tệp gốc" trên UI đã dùng đúng `source_connection_id`
+ key để mở file từ BẤT KỲ connection nguồn nào (`app/storage.py`,
`_get_source_client`). Nên khi mapping với nhiều kho/bucket khác nhau, đơn vị
tổ chức đúng là "1 connection nguồn", không phải "1 bucket" hay "1 kho" chung
chung:

  - Cache: `<cache_dir>/<source_connection_id>/<hash-thu-muc>.json` — mỗi
    nguồn có cây cache riêng, liệt kê/làm mới độc lập, không đụng nhau.
  - Field ghi vào `gcn.s3_key_mapping` (mảng): MỖI phần tử mang theo
    `source_connection_id` của chính nó — chỉ cần field này là đủ để mở đúng
    file sau này, tái dùng thẳng cơ chế mở file đang có cho "Tệp gốc".
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

# ── Field ghi vào document `gcn` khi mapping (mảng — xem docstring trên) ────
S3_KEY_MAPPING_FIELD = "s3_key_mapping"

# _id thật của connection "hsq-hni" trong collection `s3_connections` — mặc
# định cho --source-connection-id ở cả 2 script.
DEFAULT_SOURCE_CONNECTION_ID = "7b539432-9988-41c0-8592-0e0045130ef6"

# ── Chuẩn hoá tên tệp — DÙNG CHUNG ai-hub <-> nguồn ──────────────────────────
# Quy tắc giống hệt `ai-hub/services/dedup_ten_tep/dem_trung_ten_tep.py`:
#   1. Bỏ chữ "GCN" (không phân biệt hoa/thường)
#   2. Bỏ khoảng trắng, ký tự đặc biệt (chỉ giữ chữ và số)
#   3. Chuyển hết về chữ thường
_RE_GCN = re.compile(r"gcn", re.IGNORECASE)
_RE_KEEP = re.compile(r"[^0-9a-zA-ZÀ-ỹ]")


def normalize_filename(name: str) -> str:
    s = _RE_GCN.sub("", name)
    s = _RE_KEEP.sub("", s)
    return s.lower()


# ── Từ khoá "GT"/"GCN" trong tên file nguồn ─────────────────────────────────
# 1 ĐOẠN riêng biệt tách bằng dấu "-" — có thể nằm ở ĐẦU, GIỮA, hay CUỐI tên
# (vd "AB 123456-GT.pdf", "00118-GCN-BO 950309.pdf" đều tính), không phân biệt
# hoa/thường, cho phép khoảng trắng thừa quanh dấu "-". KHÔNG khớp nếu dính
# liền chữ khác không có dấu "-" ngăn cách (vd "AB123456GT.pdf" thì KHÔNG tính).
def suffix_keyword(object_key: str) -> str | None:
    """'GT' / 'GCN' nếu tên file (bỏ đuôi mở rộng) có 1 đoạn tách bằng dấu "-"
    khớp đúng "gt"/"gcn"; ngược lại None. Xét trên TÊN GỐC (chưa chuẩn hoá)."""
    base = object_key.rsplit("/", 1)[-1]
    name, _dot, _ext = base.rpartition(".")
    segments = {seg.strip().lower() for seg in (name or base).split("-")}
    if "gt" in segments:
        return "GT"
    if "gcn" in segments:
        return "GCN"
    return None


MINIO_KEYWORD_FILTER_CHOICES = ("khong-loc", "loai-gt", "chi-gcn")

# 1 ứng viên = (source_connection_id, object_key) — luôn kèm nguồn để biết mở
# file bằng connection nào, kể cả khi đối chiếu với nhiều nguồn cùng lúc.
Candidate = tuple[str, str]


def apply_keyword_filter(index: dict[str, list[Candidate]], mode: str) -> dict[str, list[Candidate]]:
    """Lọc danh sách (source_connection_id, object_key) ứng viên theo từ khoá
    trong tên — áp NGAY TRƯỚC lúc đối chiếu, không đụng tới cache gốc. Chỉ chọn
    1 trong 3 cách:
      'khong-loc' -> giữ nguyên toàn bộ (mặc định, hành vi cũ)
      'loai-gt'   -> bỏ MỌI ứng viên có từ khoá 'GT', giữ lại tất cả cái khác
      'chi-gcn'   -> CHỈ giữ ứng viên có từ khoá 'GCN', bỏ mọi cái khác (kể cả
                     không có từ khoá nào)."""
    if mode == "khong-loc":
        return index
    out: dict[str, list[Candidate]] = {}
    for norm, candidates in index.items():
        if mode == "loai-gt":
            kept = [c for c in candidates if suffix_keyword(c[1]) != "GT"]
        elif mode == "chi-gcn":
            kept = [c for c in candidates if suffix_keyword(c[1]) == "GCN"]
        else:
            raise ValueError(f"mode khong hop le: {mode!r}")
        if kept:
            out[norm] = kept
    return out


# ── Lô test tự sinh "Lô dd/mm HH:MM" (xem app/routes/batches.py:114) ────────
_RE_TEST_BATCH_NAME = re.compile(r"^Lô \d{2}/\d{2} \d{2}:\d{2}$")


async def find_test_batch_ids(batch_coll) -> set[str]:
    """Trả về tập `_id` (chuỗi) của các lô có tên đúng dạng tự sinh "Lô dd/mm
    HH:MM" — không đặt tên riêng khi tạo, coi là lô test, loại khỏi đối chiếu.
    `batch_coll` = collection motor (vd `app.db.batches()`)."""
    ids: set[str] = set()
    async for b in batch_coll.find({}, {"name": 1}):
        if _RE_TEST_BATCH_NAME.fullmatch(b.get("name") or ""):
            ids.add(b["_id"])
    return ids


# ── Cache liệt kê MinIO — 1 CÂY THƯ MỤC RIÊNG cho mỗi source_connection_id ──
# Đọc/ghi file JSON đồng bộ (I/O nhỏ, chạy 1 lần trong script CLI — không có
# lý do dùng aiofiles ở đây).
def cache_dir_for_source(base_cache_dir: str, source_connection_id: str) -> str:
    return os.path.join(base_cache_dir, source_connection_id)


def cache_path(base_cache_dir: str, source_connection_id: str, prefix: str) -> str:
    # Hash tên prefix làm tên file -> tránh ký tự đặc biệt/tiếng Việt/dấu "/"
    # trong prefix gây lỗi tên file trên các hệ điều hành khác nhau.
    h = hashlib.md5(prefix.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir_for_source(base_cache_dir, source_connection_id), f"{h}.json")


def load_cache(base_cache_dir: str, source_connection_id: str, prefix: str,
              max_age_hours: float) -> tuple[dict[str, list[str]], int] | None:
    """Đọc cache của 1 prefix (thuộc 1 nguồn) nếu CÒN MỚI (dưới max_age_hours
    giờ). Trả về None nếu không có cache / cache hỏng / cache cũ quá hạn."""
    if max_age_hours <= 0:
        return None
    path = cache_path(base_cache_dir, source_connection_id, prefix)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("prefix") != prefix:
            return None  # trùng hash (hiếm) -> coi như không có cache, an toàn
        scanned_at = datetime.fromisoformat(data["scanned_at"])
        age_hours = (datetime.now(timezone.utc) - scanned_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
        return data["index"], data["total"]
    except (OSError, ValueError, KeyError, TypeError):
        return None  # cache hỏng/thiếu trường -> coi như không có, liệt kê lại


def save_cache(base_cache_dir: str, source_connection_id: str, prefix: str,
              index: dict[str, list[str]], total: int) -> None:
    d = cache_dir_for_source(base_cache_dir, source_connection_id)
    os.makedirs(d, exist_ok=True)
    path = cache_path(base_cache_dir, source_connection_id, prefix)
    tmp_path = path + f".tmp{os.getpid()}"
    payload = {
        "prefix": prefix,
        "source_connection_id": source_connection_id,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "index": index,
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, path)  # ghi nguyên tử -> không để lại cache dở dang
    except OSError as e:
        print(f"  !! Khong ghi duoc cache cho '{prefix}': {e}", file=sys.stderr)


def load_merged_index(base_cache_dir: str, source_connection_id: str) -> tuple[dict[str, list[str]], int]:
    """Đọc TOÀN BỘ file cache JSON đã có của 1 nguồn, gộp thành 1 index
    {ten_chuan_hoa: [object_key,...]}. Dùng cho script mapping — KHÔNG gọi
    MinIO, chỉ đọc cache có sẵn trên đĩa. Trả về ({}, 0) nếu nguồn chưa có cache
    (chưa chạy `quet_minio_ra_cache.py` cho nguồn này lần nào)."""
    d = cache_dir_for_source(base_cache_dir, source_connection_id)
    merged: dict[str, list[str]] = {}
    total = 0
    if not os.path.isdir(d):
        return merged, total
    for fname in os.listdir(d):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for norm, keys in (data.get("index") or {}).items():
            merged.setdefault(norm, []).extend(keys)
        total += data.get("total", 0)
    return merged, total
