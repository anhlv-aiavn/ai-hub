import logging
from io import BytesIO
from typing import List, Optional
from botocore.exceptions import ClientError
import aioboto3
from botocore.config import Config
import os
from dotenv import load_dotenv
# Load variables from .env into the environment
load_dotenv()

logger = logging.getLogger(__name__)

import re
import os
def normalize_s3_key(key: str) -> str:
    """
    Normalize S3/MinIO object key:
    - Remove dangerous path components (., ..)
    - Normalize slashes
    - Remove leading slash
    - Keep original filename (unicode OK)
    - Remove weird control characters
    """

    if not key:
        raise ValueError("Key cannot be empty")

    # 1. Normalize slashes
    key = key.replace("\\", "/")

    # 2. Remove control characters (hidden bugs 😈)
    key = re.sub(r"[\x00-\x1f\x7f]", "", key)

    # 3. Normalize path (resolve ../ and ./)
    key = os.path.normpath(key)

    # 4. Convert back to forward slash (Windows fix)
    key = key.replace("\\", "/")

    # 5. Remove leading "../" or "./"
    while key.startswith("../") or key.startswith("./"):
        key = key[3:]

    # 6. Remove leading slash (S3 không cho)
    key = key.lstrip("/")

    # 7. Prevent empty or current dir
    if key in ("", "."):
        raise ValueError("Invalid key after normalization")

    return key

class MinioClient:
    """Common MinIO client wrapper using boto3."""

    def __init__(
        self, endpoint_url=None, aws_access_key_id=None, aws_secret_access_key=None, verify=True
    ):
        self.endpoint_url = os.getenv("ENDPOINT_URL_MINIO")
        self.aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID_MINIO")
        self.aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY_MINIO")
        if not endpoint_url:
            self.endpoint_url = os.getenv("ENDPOINT_URL_MINIO")
        if not aws_access_key_id:
            self.aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID_MINIO")
        if not aws_secret_access_key:
            self.aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY_MINIO")
        self.verify = False

    async def async_get_object(self, bucket: str, key: str) -> BytesIO:
        """Thật sự bất đồng bộ — không dùng thread pool!"""
        try:
            session = aioboto3.Session()
            async with session.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                verify=self.verify,
                region_name="us-east-1",
                config=Config(
                    connect_timeout=60,
                    read_timeout=300,
                )
            ) as s3:
                response = await s3.get_object(Bucket=bucket, Key=key)
                body = await response["Body"].read()
                return BytesIO(body)
        except ClientError as e:
            logger.error(f"Failed to get {bucket}/{key}: {e}")
            raise

    async def async_object_exists(self, bucket: str, key: str) -> bool:
        """
        Kiểm tra xem object có tồn tại trong bucket MinIO/S3 hay không.
        """
        try:
            session = aioboto3.Session()
            async with session.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                verify=self.verify,
                region_name="us-east-1",
            ) as s3:
                await s3.head_object(Bucket=bucket, Key=key)
                return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                return False
            else:
                # Lỗi khác: permission, network, v.v.
                logger.error(f"Error checking existence of {bucket}/{key}: {e}")
                raise

    async def async_copy_to_bucket(
        self,
        src_bucket: str,
        src_key: str,
        dst_bucket: str,
        dst_key: str,
    ) -> bool:
        """
        Copy an object from src_bucket/src_key to dst_bucket/dst_key.
        Does NOT delete the original — safe for immutable data workflows.
        """
        try:
            session = aioboto3.Session()
            async with session.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                verify=self.verify,
                region_name="us-east-1",
            ) as s3:
                copy_source = {"Bucket": src_bucket, "Key": src_key}
                await s3.copy_object(
                    CopySource=copy_source,
                    Bucket=dst_bucket,
                    Key=dst_key,
                )
                logger.info(f"✅ Copied: {src_bucket}/{src_key} → {dst_bucket}/{dst_key}")
                return True

        except ClientError as e:
            logger.error(
                f"Failed to copy {src_bucket}/{src_key} to {dst_bucket}/{dst_key}: {e}"
            )
            raise

    async def async_list_files(
        self,
        bucket: str,
        prefix: str = "",
        suffix_filter: Optional[str] = None,
    ) -> List[str]:
        """List all files in a bucket (recursively) — asynchronously."""
        keys = []
        continuation_token = None

        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            verify=self.verify,
            region_name="us-east-1",
        ) as s3:
            while True:
                try:
                    list_kwargs = {"Bucket": bucket, "Prefix": prefix}
                    if continuation_token:
                        list_kwargs["ContinuationToken"] = continuation_token

                    resp = await s3.list_objects_v2(**list_kwargs)

                    for obj in resp.get("Contents", []):
                        key = obj["Key"]
                        if not suffix_filter or key.lower().endswith(suffix_filter.lower()):
                            keys.append(key)

                    if resp.get("IsTruncated"):
                        continuation_token = resp.get("NextContinuationToken")
                    else:
                        break

                except ClientError as e:
                    logger.error(f"Error listing files in {bucket}/{prefix}: {e}")
                    break

        return keys

    # Thêm method mới vào MinioClient
    async def async_list_files_paginated(
        self,
        bucket: str,
        prefix: str = "",
        suffix_filter: Optional[str] = None,
        continuation_token: Optional[str] = None,
        start_after: Optional[str] = None,
        max_keys: int = 1000,  # Max allowed by S3/MinIO
    ) -> tuple[List[str], Optional[str]]:
        """List files với pagination — trả về (keys, next_token)."""
        keys = []
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            verify=self.verify,
            region_name="us-east-1",
        ) as s3:
            list_kwargs = {
                "Bucket": bucket, "Prefix": prefix, "MaxKeys": max_keys
            }
            if continuation_token:
                list_kwargs["ContinuationToken"] = continuation_token
            if start_after:
                list_kwargs["StartAfter"] = start_after

            resp = await s3.list_objects_v2(**list_kwargs)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                if not suffix_filter or key.lower().endswith(suffix_filter.lower()):
                    keys.append(key)
            
            next_token = resp.get("NextContinuationToken") if resp.get("IsTruncated") else None
            return keys, next_token
    
    async def async_put_object(self, bucket: str, key: str, data: BytesIO):
        """Upload BytesIO to MinIO asynchronously."""
        key = normalize_s3_key(key)
        data.seek(0)
        body = data.read()  # ← convert to bytes; no stream exhaustion
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            verify=self.verify,
            region_name="us-east-1",
        ) as s3:
            await s3.put_object(Bucket=bucket, Key=key, Body=body)

    async def async_upload_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ):
        """Asynchronously upload bytes to MinIO."""
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            verify=self.verify,
            region_name="us-east-1",
        ) as s3:
            try:
                await s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
                logger.info(f"✅ Uploaded {bucket}/{key}")
            except ClientError as e:
                logger.error(f"Failed to upload {bucket}/{key}: {e}")
                raise


# -------------------------------------------------------------
# ✅ Singleton instance
# -------------------------------------------------------------
minio_client = MinioClient()

# minio_client = MinioClient(
#         endpoint_url=settings.ENDPOINT_URL_MINIO_TRAIN,
#         aws_access_key_id=settings.AWS_ACCESS_KEY_ID_MINIO_TRAIN,
#         aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY_MINIO_TRAIN,
#         verify=False,
#     )

# minio_client.delete_bucket("ocr-cv", force=True)


# TEST_BUCKET = "ocr-result"  # 👈 change this
# TEST_KEY = "test/connection_check.txt"
# TEST_DATA = b"Hello from connection test!"


# async def main():
#     print("=== MinIO Connection Test ===\n")

#     # 1. Upload
#     print("1. Uploading test object...")
#     await minio_client.async_upload_bytes(
#         bucket=TEST_BUCKET,
#         key=TEST_KEY,
#         data=TEST_DATA,
#         content_type="text/plain",
#     )

#     # 2. Check existence
#     print("2. Checking object exists...")
#     exists = await minio_client.async_object_exists(TEST_BUCKET, TEST_KEY)
#     assert exists, "❌ Object not found after upload!"
#     print(f"   ✅ Object exists: {exists}")

#     # 3. Download & verify
#     print("3. Downloading and verifying content...")
#     buf = await minio_client.async_get_object(TEST_BUCKET, TEST_KEY)
#     content = buf.read()
#     assert content == TEST_DATA, f"❌ Content mismatch: {content}"
#     print(f"   ✅ Content matches: {content.decode()}")

#     # 4. List files
#     print("4. Listing files with prefix 'test/'...")
#     keys = await minio_client.async_list_files(TEST_BUCKET, prefix="test/")
#     print(f"   ✅ Found {len(keys)} file(s): {keys}")

#     print("\n✅ All checks passed — MinIO connection is healthy!")


# if __name__ == "__main__":
#     import asyncio
#     asyncio.run(main())

if __name__ == "__main__":
    import asyncio

    async def test_connection():
        try:
            # Test: list buckets (cần permission s3:ListAllMyBuckets)
            session = aioboto3.Session()
            async with session.client(
                "s3",
                endpoint_url=minio_client.endpoint_url,
                aws_access_key_id=minio_client.aws_access_key_id,
                aws_secret_access_key=minio_client.aws_secret_access_key,
                verify=minio_client.verify,
                region_name="us-east-1",
            ) as s3:
                buckets = await s3.list_buckets()
                print(f"✅ Connected! Found {len(buckets.get('Buckets', []))} buckets")
                for b in buckets.get("Buckets", [])[:5]:  # Show first 5
                    print(f"  - {b['Name']}")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code == "InvalidAccessKeyId":
                print(f"❌ Auth failed: check credentials")
            elif code == "SignatureDoesNotMatch":
                print(f"❌ Signature error: check secret key / time sync")
            else:
                print(f"❌ Connection error: {e}")
        except Exception as e:
            print(f"❌ Unexpected error: {e}")

    asyncio.run(test_connection())