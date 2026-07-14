"""Dry-run: trích xuất GCN cho TÍCH HỢP RIÊNG (đối tác ngoài) — nhận file, trả
thẳng kết quả, KHÔNG lưu vào bucket/collection chính dùng bởi hệ thống thật.
Không có khái niệm `branch`.

File này CỐ TÌNH độc lập với app/routes/batches.py, app/routes/gcn.py và
app/worker/* — không sửa, không import gì từ các module đó — để không ảnh
hưởng luồng xử lý chính đang chạy cho hệ thống thật. Chỉ tái dùng (đọc, không
sửa) pipeline detect+extract thuần túy ở src/extentions/multimodal/pipeline.py
(không đụng Mongo/S3) và MinioClient singleton (không đụng vendor helper).

File tạm: lên BUCKET RIÊNG (config.DRYRUN_BUCKET, khác bucket hệ thống thật),
xoá NGAY sau khi xử lý xong (kể cả khi lỗi) — lifecycle rule của bucket là
lưới an toàn nếu tiến trình chết giữa chừng trước khi kịp xoá tay.

Kết quả: collection riêng `dryrun_job` (metadata job) + `dryrun_item` (1 doc/
file — tách khỏi job để không phình 1 document khi nhiều file, giống cách
`batch`/`gcn` của hệ thống chính tách nhau), TTL tự xoá sau
config.DRYRUN_TTL_SECONDS. Xử lý nền ngay trong process API bằng
asyncio.create_task, có semaphore riêng (DRYRUN_MAX_VLM_CONCURRENT) để không
giành tải VLM với worker chính đang xử lý batch thật."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

import aioboto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import config
from app.db import get_db
from app.deps import current_user, is_admin, require_operator
from src.extentions.minio_helper import minio_client
from src.extentions.multimodal.pipeline import detect_and_extract

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/dryrun", tags=["dryrun"], dependencies=[Depends(current_user)])


def dryrun_jobs():
    return get_db()[config.COLL_DRYRUN_JOB]


def dryrun_items():
    return get_db()[config.COLL_DRYRUN_ITEM]


def _s3_cm():
    session = aioboto3.Session()
    return session.client(
        "s3",
        endpoint_url=minio_client.endpoint_url,
        aws_access_key_id=minio_client.aws_access_key_id,
        aws_secret_access_key=minio_client.aws_secret_access_key,
        verify=minio_client.verify,
        region_name="us-east-1",
    )


_ready = False


async def _ensure_ready() -> None:
    """Tạo bucket tạm + lifecycle an toàn + TTL index — lần đầu dùng. Cố tình
    KHÔNG gộp vào ensure_indexes()/_ensure_bucket() ở app/db.py, app/main.py
    (dùng cho hệ thống chính) để tách biệt hoàn toàn."""
    global _ready
    if _ready:
        return
    try:
        async with _s3_cm() as s3:
            try:
                await s3.head_bucket(Bucket=config.DRYRUN_BUCKET)
            except ClientError:
                await s3.create_bucket(Bucket=config.DRYRUN_BUCKET)
            try:
                await s3.put_bucket_lifecycle_configuration(
                    Bucket=config.DRYRUN_BUCKET,
                    LifecycleConfiguration={"Rules": [{
                        "ID": "dryrun-tmp-expire",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "Expiration": {"Days": config.DRYRUN_S3_LIFECYCLE_DAYS},
                    }]},
                )
            except Exception as e:  # noqa: BLE001 — MinIO không hỗ trợ lifecycle vẫn chạy, chỉ mất lưới an toàn
                log.warning("dryrun: bucket lifecycle lỗi (bỏ qua): %s", e)
        await dryrun_jobs().create_index("created_at", expireAfterSeconds=config.DRYRUN_TTL_SECONDS)
        await dryrun_items().create_index("created_at", expireAfterSeconds=config.DRYRUN_TTL_SECONDS)
        await dryrun_items().create_index("job_id")
        _ready = True
    except Exception as e:  # noqa: BLE001
        log.warning("dryrun ensure_ready lỗi: %s", e)


async def _delete_object(key: str) -> None:
    try:
        async with _s3_cm() as s3:
            await s3.delete_object(Bucket=config.DRYRUN_BUCKET, Key=key)
    except Exception as e:  # noqa: BLE001 — không chặn response vì lifecycle rule đã là lưới an toàn
        log.warning("dryrun: xoá tệp tạm lỗi (key=%s): %s", key, e)


# Giới hạn số FILE dry-run xử lý đồng thời trên toàn bộ API process (mọi job
# cộng lại) — semaphore riêng, tách khỏi worker chính.
_SEM = asyncio.Semaphore(config.DRYRUN_MAX_VLM_CONCURRENT)


@router.post("")
async def create_dryrun(
    files: list[UploadFile] = File(...),
    user: dict = Depends(require_operator),
):
    """Nhận nhiều file PDF, trả job_id ngay; xử lý nền. File lên bucket tạm
    riêng (xoá sau khi xử lý xong), không ghi vào collection `gcn`/`batch`
    chính, không có `branch`."""
    if not files:
        raise HTTPException(status_code=400, detail="Không có tệp nào")
    await _ensure_ready()

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    items_out: list[dict] = []
    item_docs: list[dict] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        dryrun_id = str(uuid.uuid4())
        s3_key = f"{job_id}/{dryrun_id}.pdf"
        await minio_client.async_upload_bytes(config.DRYRUN_BUCKET, s3_key, data)
        filename = f.filename or f"{dryrun_id}.pdf"
        item_docs.append({
            "_id": dryrun_id,
            "job_id": job_id,
            "filename": filename,
            "s3_key": s3_key,
            "status": "queued",
            "page_count": 0,
            "result": None,
            "error": None,
            "created_at": now,
        })
        items_out.append({"dryrun_id": dryrun_id, "filename": filename})

    if not item_docs:
        raise HTTPException(status_code=400, detail="Tệp rỗng/không hợp lệ")

    await dryrun_items().insert_many(item_docs)
    await dryrun_jobs().insert_one({
        "_id": job_id,
        "created_by": user["username"],
        "created_at": now,
        "file_count": len(item_docs),
    })

    asyncio.create_task(_process_job(job_id, [d["_id"] for d in item_docs]))

    return {"job_id": job_id, "items": items_out}


async def _process_job(job_id: str, dryrun_ids: list[str]) -> None:
    async def _run_one(dryrun_id: str) -> None:
        item = await dryrun_items().find_one({"_id": dryrun_id})
        if not item:
            return
        s3_key = item["s3_key"]
        await dryrun_items().update_one({"_id": dryrun_id}, {"$set": {"status": "processing"}})
        try:
            async with _SEM:
                buf = await minio_client.async_get_object(config.DRYRUN_BUCKET, s3_key)
                records = await detect_and_extract(buf)
            page_count = records[0]["page_count"] if records else 0
            await dryrun_items().update_one(
                {"_id": dryrun_id},
                {"$set": {"status": "done", "page_count": page_count, "result": records}},
            )
        except Exception as e:  # noqa: BLE001
            log.exception("dryrun xử lý lỗi (job=%s, item=%s): %s", job_id, dryrun_id, e)
            await dryrun_items().update_one(
                {"_id": dryrun_id}, {"$set": {"status": "error", "error": str(e)}},
            )
        finally:
            await _delete_object(s3_key)

    await asyncio.gather(*(_run_one(did) for did in dryrun_ids))


@router.get("/{job_id}")
async def get_dryrun(job_id: str, user: dict = Depends(current_user)):
    """Poll trạng thái/kết quả. Không có branch → phân quyền theo chủ sở hữu
    (người đã gọi POST) thay vì chi nhánh; admin xem được mọi job."""
    job = await dryrun_jobs().find_one({"_id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy (có thể đã hết hạn)")
    if not is_admin(user) and job.get("created_by") != user["username"]:
        raise HTTPException(status_code=403, detail="Không có quyền xem job này")

    items = await dryrun_items().find({"job_id": job_id}).to_list(length=None)
    out_items = []
    for it in items:
        out_items.append({
            "dryrun_id": it["_id"],
            "filename": it.get("filename"),
            "status": it.get("status"),
            "page_count": it.get("page_count", 0),
            "result": it.get("result"),
            "error": it.get("error"),
        })
    statuses = [it["status"] for it in out_items]
    job_status = "done" if statuses and all(s in ("done", "error") for s in statuses) else "processing"
    return {
        "job_id": job_id,
        "status": job_status,
        "created_at": job.get("created_at"),
        "items": out_items,
    }
