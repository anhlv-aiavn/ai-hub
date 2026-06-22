"""Reset DEV: xoá sạch dữ liệu AI-HUB — drop collection Mongo (batch, gcn) +
xoá toàn bộ object trong bucket MinIO. CHỈ DÙNG KHI DEV.

Chạy trong container API:
    docker compose exec api python -m app.scripts.reset --yes
"""

import argparse
import asyncio

import aioboto3

from app import config
from app.db import batches, gcns
from src.extentions.minio_helper import minio_client


async def _wipe_bucket() -> int:
    keys = await minio_client.async_list_files(config.AIHUB_BUCKET)
    if not keys:
        return 0
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=minio_client.endpoint_url,
        aws_access_key_id=minio_client.aws_access_key_id,
        aws_secret_access_key=minio_client.aws_secret_access_key,
        verify=minio_client.verify,
        region_name="us-east-1",
    ) as s3:
        for i in range(0, len(keys), 1000):  # delete_objects giới hạn 1000/lần
            chunk = keys[i:i + 1000]
            await s3.delete_objects(
                Bucket=config.AIHUB_BUCKET,
                Delete={"Objects": [{"Key": k} for k in chunk]},
            )
    return len(keys)


async def main(yes: bool) -> None:
    if not yes:
        print("Sẽ XOÁ SẠCH Mongo (batch, gcn) + bucket MinIO. Thêm --yes để xác nhận.")
        return
    n_gcn = (await gcns().delete_many({})).deleted_count
    n_batch = (await batches().delete_many({})).deleted_count
    n_obj = await _wipe_bucket()
    print(f"Đã xoá: gcn={n_gcn}, batch={n_batch}, object MinIO={n_obj}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Reset dữ liệu AI-HUB (DEV).")
    p.add_argument("--yes", action="store_true", help="Xác nhận xoá thật.")
    asyncio.run(main(p.parse_args().yes))
