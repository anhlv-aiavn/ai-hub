"""Liệt kê 1 kho MinIO/S3 NGUỒN (1 document trong `s3_connections`, role=source),
ghi kết quả ra cache JSON. KHÔNG đụng tới `db.gcn`. Chạy THỦ CÔNG mỗi khi kho
nguồn có thay đổi (thêm/xoá file); `app.scripts.mapping_du_lieu_cu` sẽ đọc
cache này về sau, không tự liệt kê MinIO nữa.

Chạy TRONG CONTAINER:
    docker compose exec api python -m app.scripts.quet_minio_ra_cache
    docker compose exec api python -m app.scripts.quet_minio_ra_cache --refresh-cache
    docker compose exec api python -m app.scripts.quet_minio_ra_cache --source-connection-id <id-khac>

Thông tin kết nối (endpoint/access key/secret/bucket/verify_tls) đọc TRỰC TIẾP
từ document `s3_connections._id=<source-connection-id>` (role phải là
"source") — không hardcode/duplicate credential trong script. Xem
`app/scripts/matching_core.py` để hiểu vì sao tổ chức theo
`source_connection_id`.

CHIẾN LƯỢC LIỆT KÊ (bucket có nhiều thư mục, tránh quét chậm/treo):
  - CHỈ liệt kê metadata (list_objects_v2) — không tải nội dung file.
  - SONG SONG theo từng thư mục cấp 1 (--max-workers, mặc định 8) — mỗi luồng
    tự đệ quy hết thư mục con bên trong của mình.
  - RETRY/backoff riêng cho từng thư mục khi gặp lỗi mạng/timeout thoáng qua;
    1 thư mục lỗi hẳn chỉ bị BỎ QUA (ghi rõ để chạy lại), không chết tiến trình.
  - CACHE theo thư mục — `--cache-max-age-hours` (mặc định 8760 ~ 1 năm) quyết
    định khi nào coi cache là cũ và liệt kê lại; `--refresh-cache` ép liệt kê
    lại toàn bộ dù cache còn mới.

LƯU Ý VẬN HÀNH: cache ghi vào `--cache-dir` (mặc định thư mục cạnh script,
NẰM TRONG image container) — để cache sống sót qua lần rebuild/restart
container, cần mount 1 volume vào đúng đường dẫn này (hoặc trỏ `--cache-dir`
tới 1 đường dẫn đã mount sẵn). Việc phần liệt kê chạy trong luồng (thread)
đồng bộ (boto3) nên tạm thời chặn event loop trong lúc chạy — chấp nhận được
vì đây là script CLI chạy 1 lần, không phải request đang phục vụ song song.
"""

import argparse
import asyncio
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import (
        ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError,
    )
except ImportError:
    print("Thiếu thư viện boto3 — cài bằng: pip install boto3", file=sys.stderr)
    raise

from app.db import s3_connections
from app.scripts import matching_core as mc

# Console Windows mặc định dùng codepage cp1258/cp850, không encode được hết
# tiếng Việt -> ép stdout/stderr sang UTF-8 để tránh crash khi chạy thử trực
# tiếp ngoài container (trong container Linux/UTF-8 thì đoạn này vô hại).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(SCRIPT_DIR, "cache_minio_index")

MINIO_ALLOWED_EXTENSIONS: tuple[str, ...] | None = (".pdf",)  # chỉ PDF

_TRANSIENT_CLIENT_ERROR_CODES = {
    "RequestTimeout", "RequestTimeoutException", "SlowDown", "InternalError",
    "ServiceUnavailable", "Throttling", "ThrottlingException",
    "500", "502", "503", "504",
}


def _is_transient_error(e: Exception) -> bool:
    if isinstance(e, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        return True
    if isinstance(e, ClientError):
        err = e.response.get("Error", {}) or {}
        status = str(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
        return str(err.get("Code", "")) in _TRANSIENT_CLIENT_ERROR_CODES or status in _TRANSIENT_CLIENT_ERROR_CODES
    return False


def _build_client(conn: dict, max_workers: int):
    return boto3.client(
        "s3",
        endpoint_url=conn["endpoint_url"],
        aws_access_key_id=conn["access_key_id"],
        aws_secret_access_key=conn["secret_access_key"],
        verify=bool(conn.get("verify_tls")),
        region_name="us-east-1",
        config=Config(connect_timeout=30, read_timeout=120,
                     max_pool_connections=max(20, max_workers * 4),
                     retries={"max_attempts": 3, "mode": "standard"}),
    )


def _list_prefix_index(s3, bucket: str, prefix: str, progress_every: int) -> tuple[dict[str, list[str]], int]:
    print(f"  [bat dau] '{prefix}'")
    index: dict[str, list[str]] = defaultdict(list)
    total = 0
    next_report = progress_every
    paginator = s3.get_paginator("list_objects_v2")
    kwargs = {"Bucket": bucket, "Prefix": prefix, "PaginationConfig": {"PageSize": 1000}}
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents") or []:
            key = obj["Key"]
            if key.endswith("/"):
                continue  # "thư mục ảo"
            if MINIO_ALLOWED_EXTENSIONS and not key.lower().endswith(MINIO_ALLOWED_EXTENSIONS):
                continue
            base = key.rsplit("/", 1)[-1]
            norm = mc.normalize_filename(base)
            if norm:
                index[norm].append(key)
            total += 1
        if total >= next_report:
            print(f"  ... dang quet '{prefix}': {total:,} object")
            next_report += progress_every
    return dict(index), total


def _list_prefix_index_with_retry(s3, bucket: str, prefix: str, retries: int, retry_base_delay: float,
                                  progress_every: int) -> tuple[dict[str, list[str]], int]:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return _list_prefix_index(s3, bucket, prefix, progress_every)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if not _is_transient_error(e) or attempt == retries:
                raise
            delay = retry_base_delay * (2 ** (attempt - 1))
            print(f"  !! Lỗi tạm thời khi liệt kê '{prefix}' (lần {attempt}/{retries}): {e}"
                  f" -> thử lại sau {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)
    raise last_err  # không tới đây


def _get_prefix_index(s3, bucket: str, cache_dir: str, source_id: str, prefix: str,
                      cache_max_age_hours: float, refresh_cache: bool, retries: int,
                      retry_base_delay: float, progress_every: int) -> tuple[dict[str, list[str]], int, bool]:
    if not refresh_cache:
        cached = mc.load_cache(cache_dir, source_id, prefix, cache_max_age_hours)
        if cached is not None:
            index, total = cached
            return index, total, True
    index, total = _list_prefix_index_with_retry(s3, bucket, prefix, retries, retry_base_delay, progress_every)
    mc.save_cache(cache_dir, source_id, prefix, index, total)
    return index, total, False


def _list_top_level_prefixes(s3, bucket: str, base_prefix: str) -> list[str]:
    prefixes: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    kwargs = {"Bucket": bucket, "Prefix": base_prefix, "Delimiter": "/",
             "PaginationConfig": {"PageSize": 1000}}
    for page in paginator.paginate(**kwargs):
        for p in page.get("CommonPrefixes") or []:
            prefixes.append(p["Prefix"])
    return prefixes


def scan_source(s3, bucket: str, base_prefix: str, cache_dir: str, source_id: str,
                cache_max_age_hours: float, refresh_cache: bool, max_workers: int, retries: int,
                retry_base_delay: float, progress_every: int) -> tuple[int, int, list[str], int]:
    """Liệt kê toàn bộ object của 1 nguồn (đồng bộ — dùng ThreadPoolExecutor).
    Trả về (tổng object, tổng tên chuẩn hoá riêng biệt, prefix lỗi/bỏ qua, số
    thư mục lấy được từ cache)."""
    top_prefixes = _list_top_level_prefixes(s3, bucket, base_prefix)
    if not top_prefixes:
        index, total, from_cache = _get_prefix_index(
            s3, bucket, cache_dir, source_id, base_prefix, cache_max_age_hours, refresh_cache,
            retries, retry_base_delay, progress_every)
        return total, len(index), [], (1 if from_cache else 0)

    print(f"Tìm thấy {len(top_prefixes)} thư mục cấp 1 -> liệt kê song song ({max_workers} luồng)")
    names: set[str] = set()
    total = 0
    failed: list[str] = []
    from_cache_count = 0
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_get_prefix_index, s3, bucket, cache_dir, source_id, pfx,
                       cache_max_age_hours, refresh_cache, retries, retry_base_delay, progress_every): pfx
            for pfx in top_prefixes
        }
        for fut in as_completed(futures):
            pfx = futures[fut]
            done += 1
            try:
                idx, cnt, from_cache = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"!! Bỏ qua thư mục '{pfx}' sau {retries} lần thử — lỗi: {e}", file=sys.stderr)
                failed.append(pfx)
                continue
            names.update(idx.keys())
            total += cnt
            from_cache_count += 1 if from_cache else 0
            tag = "cache" if from_cache else "quet moi"
            print(f"  [{done}/{len(top_prefixes)}] xong '{pfx}' ({tag}): {cnt:,} object (luỹ kế {total:,})")
    return total, len(names), failed, from_cache_count


async def run(args: argparse.Namespace) -> None:
    conn = await s3_connections().find_one({"_id": args.source_connection_id})
    if not conn:
        print(f"!! Không tìm thấy s3_connections._id={args.source_connection_id!r}", file=sys.stderr)
        raise SystemExit(1)
    if conn.get("role") != "source":
        print(f"!! Connection {args.source_connection_id!r} có role={conn.get('role')!r}, "
              f"cần role='source' để liệt kê làm kho đối chiếu.", file=sys.stderr)
        raise SystemExit(1)

    bucket = conn["bucket"]
    print(f"Nguồn: source_connection_id={args.source_connection_id!r}  name={conn.get('name')!r}")
    print(f"Kết nối: {conn['endpoint_url']}  bucket={bucket}  prefix='{args.prefix}'")

    s3 = _build_client(conn, args.max_workers)
    try:
        s3.head_bucket(Bucket=bucket)
    except (ClientError, EndpointConnectionError) as e:
        print(f"!! Không kết nối được kho MinIO nguồn: {e}", file=sys.stderr)
        raise SystemExit(1)

    total, n_names, failed, from_cache_count = scan_source(
        s3, bucket, args.prefix, args.cache_dir, args.source_connection_id,
        args.cache_max_age_hours, args.refresh_cache, args.max_workers, args.retries,
        args.retry_base_delay, args.progress_every)

    print(f"\nTổng số object đã quét: {total:,}")
    print(f"Số tên chuẩn hoá riêng biệt: {n_names:,}")
    if from_cache_count:
        print(f"Số thư mục lấy từ cache (không liệt kê lại): {from_cache_count}")
    if failed:
        print(f"!! CẢNH BÁO: {len(failed)} thư mục quét LỖI (đã thử lại {args.retries} lần) "
              f"— cache CHƯA ĐẦY ĐỦ cho các thư mục này, nên chạy lại: {failed}", file=sys.stderr)
    print(f"\nCache ghi tại: {mc.cache_dir_for_source(args.cache_dir, args.source_connection_id)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source-connection-id", default=mc.DEFAULT_SOURCE_CONNECTION_ID,
                   help=f"_id của document trong s3_connections (role=source) cần liệt kê "
                        f"(mặc định: connection 'hsq-hni', {mc.DEFAULT_SOURCE_CONNECTION_ID}).")
    p.add_argument("--prefix", default="", help="Prefix bắt đầu quét trong bucket (mặc định: cả bucket).")
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                   help="Thư mục gốc chứa cache (mặc định app/scripts/cache_minio_index/ — cần mount "
                        "volume vào đây để cache sống sót qua rebuild/restart container).")
    p.add_argument("--cache-max-age-hours", type=float, default=float(os.getenv("MINIO_CACHE_MAX_AGE_HOURS", "8760")),
                   help="Coi cache của 1 thư mục là MỚI nếu dưới N giờ (mặc định 8760 ~ 1 năm). "
                        "0 = luôn coi cache là cũ, luôn liệt kê lại.")
    p.add_argument("--refresh-cache", action="store_true",
                   help="Bỏ qua cache, liệt kê lại TOÀN BỘ nguồn rồi ghi đè cache (dùng khi biết kho đã đổi).")
    p.add_argument("--max-workers", type=int, default=int(os.getenv("MINIO_LIST_MAX_WORKERS", "8")))
    p.add_argument("--retries", type=int, default=int(os.getenv("MINIO_LIST_RETRIES", "4")))
    p.add_argument("--retry-base-delay", type=float, default=float(os.getenv("MINIO_LIST_RETRY_BASE_DELAY", "2")))
    p.add_argument("--progress-every", type=int, default=int(os.getenv("MINIO_LIST_PROGRESS_EVERY", "5000")))
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
