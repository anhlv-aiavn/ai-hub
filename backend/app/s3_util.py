"""Tiện ích S3/MinIO ở lớp app: build client runtime từ doc `s3_connections`,
duyệt thư mục 1 cấp (vendor chỉ có liệt kê đệ quy), test connection với thông
báo lỗi phân loại rõ. KHÔNG sửa vendor `minio_helper.py`."""

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from src.extentions.minio_helper import MinioClient


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


async def async_list_folder(client: MinioClient, bucket: str, prefix: str = "",
                            token: str | None = None, max_keys: int = 1000):
    """Duyệt 1 cấp (Delimiter="/"): trả (folders, files, next_token). `files` lọc
    theo đuôi .pdf không phân biệt hoa/thường, kèm size/last_modified/etag."""
    session = aioboto3.Session()
    async with session.client(
        "s3", endpoint_url=client.endpoint_url, aws_access_key_id=client.aws_access_key_id,
        aws_secret_access_key=client.aws_secret_access_key, verify=client.verify,
        region_name="us-east-1",
    ) as s3:
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


async def async_head_object(client: MinioClient, bucket: str, key: str) -> dict:
    """etag/last_modified 1 key — dùng để lưu source_etag/source_mtime lúc import
    (phát hiện nguồn đổi nội dung sau này)."""
    session = aioboto3.Session()
    async with session.client(
        "s3", endpoint_url=client.endpoint_url, aws_access_key_id=client.aws_access_key_id,
        aws_secret_access_key=client.aws_secret_access_key, verify=client.verify,
        region_name="us-east-1",
    ) as s3:
        resp = await s3.head_object(Bucket=bucket, Key=key)
        return {"etag": (resp.get("ETag") or "").strip('"'), "last_modified": resp.get("LastModified")}


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
                                bucket: str, verify: bool, mode: str) -> dict:
    """mode="source": CHỈ head_bucket + list_objects_v2(MaxKeys=1) — cấm ghi/xóa
    trên nguồn (bất biến §Bất biến 1). mode="destination": thêm thử put_object
    rồi xóa ngay file test của chính nó."""
    session = aioboto3.Session()
    try:
        async with session.client(
            "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, verify=verify, region_name="us-east-1",
            config=Config(connect_timeout=10, read_timeout=20),
        ) as s3:
            await s3.head_bucket(Bucket=bucket)
            await s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
            if mode == "destination":
                test_key = "__aihub_connection_test__.txt"
                await s3.put_object(Bucket=bucket, Key=test_key, Body=b"ok")
                await s3.delete_object(Bucket=bucket, Key=test_key)
        return {"result": "ok", "message": "Kết nối thành công"}
    except Exception as e:  # noqa: BLE001
        return {"result": "error", "message": _classify_error(e)}
