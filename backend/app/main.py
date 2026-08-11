"""AI-HUB API — FastAPI. Gắn router batches/gcn/events, đảm bảo index + bucket."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI

from app import auth, config
from app.branches import BRANCHES
from app.db import ensure_indexes, s3_connections as s3_connections_col, site_config, users
from app.routes import audit as audit_routes
from app.routes import auth as auth_routes
from app.routes import batches, browse, dryrun, events, export, gcn, qc_sync, s3_connections, settings
from app.routes import users as users_routes
from src.extentions.minio_helper import minio_client

log = logging.getLogger(__name__)

app = FastAPI(title="AI-HUB", version="0.1.0")
app.include_router(auth_routes.router)
app.include_router(users_routes.router)
app.include_router(batches.router)
app.include_router(gcn.router)
app.include_router(events.router)
app.include_router(settings.router)
app.include_router(s3_connections.router)
app.include_router(browse.router)
app.include_router(audit_routes.router)
app.include_router(export.router)
app.include_router(dryrun.router)
app.include_router(qc_sync.router)


@app.on_event("startup")
async def _startup() -> None:
    try:
        await ensure_indexes()
    except Exception as e:  # noqa: BLE001
        log.warning("ensure_indexes lỗi: %s", e)
    await _seed_admin()
    await _seed_site_config()
    await _seed_dest_from_env()
    await _ensure_bucket()


@app.on_event("shutdown")
async def _shutdown() -> None:
    # Đóng client MinIO pool sống lâu (PERF-1) — tránh cảnh báo unclosed session.
    from app.s3_util import aclose_pooled

    await aclose_pooled()


async def _seed_site_config() -> None:
    """Migrate cấu hình hard-code hôm nay vào DB — Hà Nội chạy y hệt sau migrate.
    upsert `$setOnInsert`: nhiều API worker khởi động song song vẫn chỉ tạo 1
    lần, không đè cấu hình admin đã sửa (cùng pattern `_seed_admin`)."""
    try:
        await site_config().update_one(
            {"_id": "site"},
            {"$setOnInsert": {
                "name": "AI-HUB",
                "branches": list(BRANCHES),
                "branding": {
                    "org_name": "VP Đăng ký đất đai TP Hà Nội",
                    "logo_url": "/logo-sotnmt.png",
                    "copyright_text": "VP Đăng ký đất đai TP Hà Nội",
                },
            }},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("seed_site_config lỗi: %s", e)


async def _seed_dest_from_env() -> None:
    """Deployment ĐANG CHẠY hôm nay sống nhờ fallback ENV vừa bị bỏ ở
    `storage._get_dest_client` (xem PLAN_PHASE3_menu_logo_s3dest.md §Quyết định
    5,7) — seed 1 lần thành cấu hình `s3_connections role=destination` tường
    minh để không đứt tay khi lên bản này. Deployment MỚI (ENV rỗng/mặc định)
    sẽ KHÔNG được seed → thấy đúng trạng thái "chưa cấu hình", đúng ý đồ bắt
    cấu hình tường minh từ đầu."""
    if not minio_client.endpoint_url:  # ENV chưa từng set → đừng seed rác
        return
    try:
        if await s3_connections_col().count_documents({"role": "destination"}):
            return
        now = datetime.now(timezone.utc)
        await s3_connections_col().insert_one({
            "_id": str(uuid.uuid4()), "role": "destination",
            "name": "(migrated từ ENV)",
            "endpoint_url": minio_client.endpoint_url,
            "access_key_id": minio_client.aws_access_key_id,
            "secret_access_key": minio_client.aws_secret_access_key,
            "bucket": config.AIHUB_BUCKET, "verify_tls": bool(minio_client.verify),
            "created_at": now, "updated_at": now,
            "last_checked_at": None, "last_check_status": None, "last_check_message": None,
        })
        log.info("Đã migrate S3 đích từ ENV vào s3_connections")
    except Exception as e:  # noqa: BLE001 — race hiếm giữa nhiều worker khởi động cùng lúc
        log.warning("seed_dest_from_env lỗi: %s", e)


async def _seed_admin() -> None:
    """Tạo admin đầu tiên từ env (AIHUB_ADMIN_USER/PASS) nếu chưa tồn tại."""
    if config.JWT_SECRET == "dev-insecure-change-me":
        log.warning("AIHUB_JWT_SECRET chưa đặt — token KHÔNG an toàn cho môi trường thật")
    if not config.ADMIN_USER or not config.ADMIN_PASS:
        return
    username = config.ADMIN_USER.strip().lower()
    try:
        if await users().find_one({"username": username}, {"_id": 1}):
            return
        await users().insert_one({
            "username": username,
            "password": auth.hash_password(config.ADMIN_PASS),
            "role": "admin", "branch": None, "active": True,
            "created_at": datetime.now(timezone.utc),
        })
        log.info("Đã tạo admin '%s' từ env", username)
    except Exception as e:  # noqa: BLE001
        log.warning("seed_admin lỗi: %s", e)


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
