"""Tiện ích S3/MinIO ở lớp app: build client runtime từ doc `s3_connections`,
duyệt thư mục 1 cấp (vendor chỉ có liệt kê đệ quy), test connection với thông
báo lỗi phân loại rõ. KHÔNG sửa vendor `minio_helper.py`."""

import asyncio
import io
from contextlib import AsyncExitStack, asynccontextmanager

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from src.extentions.minio_helper import MinioClient, normalize_s3_key


# ── Client pool SỐNG LÂU (PERF-1) ────────────────────────────────────────────
# 1 client aioboto3 dùng chung/endpoint, giữ mở suốt đời process (bound vào event
# loop hiện tại — API và worker mỗi process 1 loop nên an toàn). aiobotocore an
# toàn khi nhiều coroutine gọi đồng thời (connection pool trong aiohttp). Thay cho
# việc MinioClient.async_* mở Session+client MỚI mỗi call = 1 handshake TCP/TLS.
_pooled: dict[tuple, object] = {}
_pooled_stacks: dict[tuple, AsyncExitStack] = {}
_pooled_lock = asyncio.Lock()


def _pool_key(client: MinioClient) -> tuple:
    # Gồm CẢ secret: xoay credential (đổi secret, giữ access_key) ⇒ key khác ⇒
    # client mới, không dùng nhầm client cũ với secret cũ.
    return (client.endpoint_url, client.aws_access_key_id,
            client.aws_secret_access_key, bool(client.verify))


async def pooled_s3(client: MinioClient, max_pool_connections: int = 128):
    """Trả 1 s3 client aioboto3 SỐNG LÂU cho endpoint/creds của `client` (tạo
    lười, cache theo creds). Dùng cho hot path get/put — KHÔNG mở/đóng mỗi call."""
    key = _pool_key(client)
    s3 = _pooled.get(key)
    if s3 is not None:
        return s3
    async with _pooled_lock:
        s3 = _pooled.get(key)  # double-check sau khi giành lock
        if s3 is not None:
            return s3
        stack = AsyncExitStack()
        session = aioboto3.Session()
        s3 = await stack.enter_async_context(session.client(
            "s3", endpoint_url=client.endpoint_url,
            aws_access_key_id=client.aws_access_key_id,
            aws_secret_access_key=client.aws_secret_access_key,
            verify=client.verify, region_name="us-east-1",
            config=Config(
                max_pool_connections=max_pool_connections,
                connect_timeout=60, read_timeout=300,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        ))
        _pooled[key] = s3
        _pooled_stacks[key] = stack
        return s3


async def pooled_get(client: MinioClient, bucket: str, key: str, max_pool: int = 128) -> io.BytesIO:
    s3 = await pooled_s3(client, max_pool)
    resp = await s3.get_object(Bucket=bucket, Key=key)
    body = await resp["Body"].read()
    return io.BytesIO(body)


async def pooled_put(client: MinioClient, bucket: str, key: str, data: bytes, max_pool: int = 128) -> None:
    # Giữ ĐÚNG hành vi vendor async_put_object: normalize key trước khi ghi.
    s3 = await pooled_s3(client, max_pool)
    await s3.put_object(Bucket=bucket, Key=normalize_s3_key(key), Body=data)


async def aclose_pooled() -> None:
    """Đóng mọi client pool (gọi lúc shutdown API) — tránh cảnh báo unclosed session."""
    async with _pooled_lock:
        for stack in _pooled_stacks.values():
            try:
                await stack.aclose()
            except Exception:  # noqa: BLE001
                pass
        _pooled.clear()
        _pooled_stacks.clear()


def build_client(conn: dict) -> MinioClient:
    """Client runtime từ 1 doc s3_connections. Constructor vendor không nhận
    tham số (đọc env) — né bằng gán thuộc tính sau khởi tạo, không sửa vendor
    ([minio_helper.py:57-69])."""
    c = MinioClient()
    c.endpoint_url = conn["endpoint_url"]
    c.aws_access_key_id = conn["access_key_id"]
    c.aws_secret_access_key = conn["secret_access_key"]
    c.verify = bool(conn.get("verify_tls"))
    return c


@asynccontextmanager
async def open_s3_client(client: MinioClient):
    """Mo 1 S3 client DUNG CHUNG cho ca 1 chuoi goi lien tiep (nhieu trang list)
    -- tranh tao Session/client rieng cho TUNG LAN GOI (moi lan la 1 ket noi
    TCP/TLS moi). Voi vong lap vai chuc/tram lan (thu muc lon), chi phi tao
    lai session cong don se lam request cham dan roi timeout -- xem
    _list_recursive_capped/_list_level_all trong routes/browse.py (truoc day
    goi async_list_folder() moi vong lap)."""
    session = aioboto3.Session()
    async with session.client(
        "s3", endpoint_url=client.endpoint_url, aws_access_key_id=client.aws_access_key_id,
        aws_secret_access_key=client.aws_secret_access_key, verify=client.verify,
        region_name="us-east-1",
    ) as s3:
        yield s3


async def list_folder_page(s3, bucket: str, prefix: str = "",
                           token: str | None = None, max_keys: int = 1000):
    """Duyet 1 trang (Delimiter="/") tren 1 s3 client DA MO SAN (xem
    open_s3_client) -- trả (folders, files, next_token). `files` loc theo duoi
    .pdf khong phan biet hoa/thuong, kem size/last_modified/etag."""
    kwargs = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/", "MaxKeys": max_keys}
    if token:
        kwargs["ContinuationToken"] = token
    resp = await s3.list_objects_v2(**kwargs)
    folders = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    files = [
        {"key": o["Key"], "name": o["Key"].rsplit("/", 1)[-1],
         "size": o.get("Size", 0), "last_modified": o.get("LastModified"),
         "etag": (o.get("ETag") or "").strip('"')}
        for o in resp.get("Contents", [])
        if o["Key"].lower().endswith(".pdf")
    ]
    next_token = resp.get("NextContinuationToken") if resp.get("IsTruncated") else None
    return folders, files, next_token


async def async_list_folder(client: MinioClient, bucket: str, prefix: str = "",
                            token: str | None = None, max_keys: int = 1000):
    """Duyet 1 trang, tu mo/dong session rieng -- dung cho goi DON LE (vd
    browse_folder, moi request UI chi can dung 1 trang). Voi vong lap NHIEU
    trang, dung open_s3_client() + list_folder_page() de tai su dung 1 client
    thay vi mo session moi moi trang."""
    async with open_s3_client(client) as s3:
        return await list_folder_page(s3, bucket, prefix, token, max_keys)


def _classify_error(e: Exception) -> str:
    if isinstance(e, (EndpointConnectionError, ConnectTimeoutError)):
        return "Không kết nối được endpoint (kiểm tra URL/mạng)"
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")
        if code == "InvalidAccessKeyId":
            return "Access key không tồn tại"
        if code == "SignatureDoesNotMatch":
            return "Secret key sai, hoặc lệch giờ hệ thống"
        if code in ("NoSuchBucket", "404"):
            return "Bucket không tồn tại"
        if code in ("AccessDenied", "403"):
            return "Không đủ quyền truy cập (Access Denied)"
        return f"Lỗi kết nối: {code or str(e)}"
    return f"Lỗi kết nối: {e}"


async def async_test_connection(endpoint_url: str, access_key: str, secret_key: str,
                                bucket: str | None, verify: bool, mode: str) -> dict:
    """mode="source": CHỈ head_bucket + list_objects_v2(MaxKeys=1) — cấm ghi/xóa
    trên nguồn (bất biến §Bất biến 1). mode="destination": thêm thử put_object
    rồi xóa ngay file test của chính nó.

    `bucket` rỗng/None (chưa chọn bucket) → CHƯA biết bucket nào để head/list, chỉ
    xác nhận endpoint/key/secret đúng qua `list_buckets()` rồi trả kèm danh sách
    bucket cho frontend hiện dropdown chọn."""
    session = aioboto3.Session()
    try:
        async with session.client(
            "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, verify=verify, region_name="us-east-1",
            config=Config(connect_timeout=10, read_timeout=20),
        ) as s3:
            if not bucket:
                resp = await s3.list_buckets()
                buckets = [b["Name"] for b in resp.get("Buckets", [])]
                return {"result": "ok", "message": "Kết nối thành công — chọn bucket bên dưới",
                        "buckets": buckets}
            await s3.head_bucket(Bucket=bucket)
            await s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
            if mode == "destination":
                test_key = "__aihub_connection_test__.txt"
                await s3.put_object(Bucket=bucket, Key=test_key, Body=b"ok")
                await s3.delete_object(Bucket=bucket, Key=test_key)
        return {"result": "ok", "message": "Kết nối thành công"}
    except Exception as e:  # noqa: BLE001
        return {"result": "error", "message": _classify_error(e)}
