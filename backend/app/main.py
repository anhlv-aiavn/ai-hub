"""AI-HUB API — FastAPI. Gắn router batches/gcn/events, đảm bảo index + bucket."""

import logging

from fastapi import FastAPI

from app import config
from app.db import ensure_indexes
from app.routes import batches, events, gcn
from src.extentions.minio_helper import minio_client

log = logging.getLogger(__name__)

app = FastAPI(title="AI-HUB", version="0.1.0")
app.include_router(batches.router)
app.include_router(gcn.router)
app.include_router(events.router)


@app.on_event("startup")
async def _startup() -> None:
    try:
        await ensure_indexes()
    except Exception as e:  # noqa: BLE001
        log.warning("ensure_indexes lỗi: %s", e)
    await _ensure_bucket()


async def _ensure_bucket() -> None:
    import aioboto3
    from botocore.exceptions import ClientError

    session = aioboto3.Session()
    try:
        async with session.client(
            "s3",
            endpoint_url=minio_client.endpoint_url,
            aws_access_key_id=minio_client.aws_access_key_id,
            aws_secret_access_key=minio_client.aws_secret_access_key,
            verify=minio_client.verify,
            region_name="us-east-1",
        ) as s3:
            try:
                await s3.head_bucket(Bucket=config.AIHUB_BUCKET)
            except ClientError:
                await s3.create_bucket(Bucket=config.AIHUB_BUCKET)
                log.info("Đã tạo bucket %s", config.AIHUB_BUCKET)
    except Exception as e:  # noqa: BLE001
        log.warning("ensure_bucket lỗi: %s", e)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
