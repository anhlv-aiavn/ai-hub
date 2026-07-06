"""Reset DEV: xoá sạch dữ liệu AI-HUB — drop collection Mongo (batch, gcn, user) +
xoá toàn bộ object trong bucket MinIO. CHỈ DÙNG KHI DEV.

Mặc định xoá CẢ tài khoản (user); admin sẽ tự seed lại từ env khi API khởi động
(AIHUB_ADMIN_USER/PASS). Dùng --keep-users để giữ tài khoản.

Chạy trong container API:
    docker compose exec api python -m app.scripts.reset --yes
    docker compose exec api python -m app.scripts.reset --yes --keep-users
"""

import argparse
import asyncio

import aioboto3

from app import config
from app.db import batches, gcns, users
from src.extentions.minio_helper import minio_client


async def _wipe_bucket() -> int:
    """Xóa STREAMING theo trang — KHÔNG nạp toàn bộ key vào RAM (bất buộc ở quy
    mô lớn, xem PLAN_.md §Bất biến 3 / §Quy mô cực lớn 1). Chạy lại sau khi bị
    ngắt giữa chừng vẫn an toàn: liệt kê chỉ trả key còn sót."""
    total = 0
    token = None
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=minio_client.endpoint_url,
        aws_access_key_id=minio_client.aws_access_key_id,
        aws_secret_access_key=minio_client.aws_secret_access_key,
        verify=minio_client.verify,
        region_name="us-east-1",
    ) as s3:
        while True:
            keys, token = await minio_client.async_list_files_paginated(
                config.AIHUB_BUCKET, continuation_token=token, max_keys=1000,
            )
            if keys:
                await s3.delete_objects(
                    Bucket=config.AIHUB_BUCKET,
                    Delete={"Objects": [{"Key": k} for k in keys]},
                )
                total += len(keys)
            if not token:
                break
    return total


async def main(yes: bool, keep_users: bool) -> None:
    scope = "batch, gcn" + ("" if keep_users else ", user")
    if not yes:
        print(f"Sẽ XOÁ SẠCH Mongo ({scope}) + bucket MinIO. Thêm --yes để xác nhận.")
        return
    n_gcn = (await gcns().delete_many({})).deleted_count
    n_batch = (await batches().delete_many({})).deleted_count
    n_user = 0 if keep_users else (await users().delete_many({})).deleted_count
    n_obj = await _wipe_bucket()
    print(f"Đã xoá: gcn={n_gcn}, batch={n_batch}, user={n_user}, object MinIO={n_obj}")
    if not keep_users:
        print("→ Khởi động lại API để seed lại admin từ env (AIHUB_ADMIN_USER/PASS).")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Reset dữ liệu AI-HUB (DEV).")
    p.add_argument("--yes", action="store_true", help="Xác nhận xoá thật.")
    p.add_argument("--keep-users", action="store_true", help="Giữ lại tài khoản (chỉ xoá dữ liệu).")
    args = p.parse_args()
    asyncio.run(main(args.yes, args.keep_users))
