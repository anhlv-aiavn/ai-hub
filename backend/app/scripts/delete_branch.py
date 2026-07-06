"""Xóa TOÀN BỘ dữ liệu của MỘT chi nhánh: gcn + batch (Mongo) + object MinIO của
các lô thuộc chi nhánh đó. KHÔNG đụng tới tài khoản (users) hay chi nhánh khác.

Chạy trong container API:
    docker compose exec api python -m app.scripts.delete_branch --branch "Chi nhánh Long Biên"
    docker compose exec api python -m app.scripts.delete_branch --branch "Chi nhánh Long Biên" --yes
"""

import argparse
import asyncio

import aioboto3

from app import config
from app.branches import is_valid_branch
from app.db import batches, gcns
from src.extentions.minio_helper import minio_client


async def _delete_minio(batch_ids: set[str]) -> int:
    """Xóa STREAMING theo TỪNG batch_id làm prefix — không liệt kê cả bucket rồi
    lọc trong RAM (bất buộc ở quy mô lớn, xem PLAN_.md §Bất biến 3 / §Quy mô cực
    lớn 1); vừa an toàn RAM vừa rẻ hơn (chỉ liệt kê đúng phần cần xóa). Object đặt
    theo "<batch_id>/..." nên prefix=f"{batch_id}/" trúng đúng object của lô đó."""
    if not batch_ids:
        return 0
    total = 0
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=minio_client.endpoint_url,
        aws_access_key_id=minio_client.aws_access_key_id,
        aws_secret_access_key=minio_client.aws_secret_access_key,
        verify=minio_client.verify,
        region_name="us-east-1",
    ) as s3:
        for batch_id in batch_ids:
            token = None
            while True:
                keys, token = await minio_client.async_list_files_paginated(
                    config.AIHUB_BUCKET, prefix=f"{batch_id}/",
                    continuation_token=token, max_keys=1000,
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


async def main(branch: str, yes: bool) -> None:
    if not await is_valid_branch(branch):
        raise SystemExit(f"Chi nhánh không hợp lệ: {branch!r}")

    batch_ids = {b["_id"] async for b in batches().find({"branch": branch}, {"_id": 1})}
    n_gcn = await gcns().count_documents({"branch": branch})
    print(f"Chi nhánh: {branch}")
    print(f"  Sẽ xóa: lô={len(batch_ids)}, gcn={n_gcn}, + object MinIO của các lô này.")
    if not yes:
        print("Thêm --yes để xác nhận xóa thật.")
        return

    n_obj = await _delete_minio(batch_ids)
    d_gcn = (await gcns().delete_many({"branch": branch})).deleted_count
    d_batch = (await batches().delete_many({"branch": branch})).deleted_count
    print(f"Đã xóa: gcn={d_gcn}, batch={d_batch}, object MinIO={n_obj}. Tài khoản & chi nhánh khác KHÔNG đổi.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Xóa toàn bộ dữ liệu của 1 chi nhánh.")
    p.add_argument("--branch", required=True, help="Tên chi nhánh (đúng như danh sách).")
    p.add_argument("--yes", action="store_true", help="Xác nhận xóa thật.")
    args = p.parse_args()
    asyncio.run(main(args.branch, args.yes))
