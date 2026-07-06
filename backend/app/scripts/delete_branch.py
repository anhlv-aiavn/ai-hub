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
    if not batch_ids:
        return 0
    keys = await minio_client.async_list_files(config.AIHUB_BUCKET)
    # Object đặt theo "<batch_id>/..." → lọc theo tiền tố batch của chi nhánh.
    targets = [k for k in (keys or []) if k.split("/", 1)[0] in batch_ids]
    if not targets:
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
        for i in range(0, len(targets), 1000):  # delete_objects giới hạn 1000/lần
            chunk = targets[i:i + 1000]
            await s3.delete_objects(
                Bucket=config.AIHUB_BUCKET,
                Delete={"Objects": [{"Key": k} for k in chunk]},
            )
    return len(targets)


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
