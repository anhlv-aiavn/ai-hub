"""Truy cập MongoDB cho API (motor, long-lived). Worker dùng client riêng/job."""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app import config

_client: AsyncIOMotorClient | None = None


def get_db() -> AsyncIOMotorDatabase:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(config.MONGO_URI)
    return _client[config.MONGO_DB]


def batches():
    return get_db()[config.COLL_BATCH]


def gcns():
    return get_db()[config.COLL_GCN]


def users():
    return get_db()[config.COLL_USER]


def site_config():
    return get_db()[config.COLL_SITE_CONFIG]


def s3_connections():
    return get_db()[config.COLL_S3_CONN]


def audit_log():
    return get_db()[config.COLL_AUDIT]


def import_jobs():
    return get_db()[config.COLL_IMPORT_JOB]


def export_jobs():
    return get_db()[config.COLL_EXPORT_JOB]


def browse_progress_cache():
    return get_db()[config.COLL_BROWSE_PROGRESS]


async def ensure_indexes() -> None:
    await gcns().create_index("batch_id")
    await gcns().create_index("group_key")
    await gcns().create_index("status")
    await gcns().create_index("branch")
    await gcns().create_index("extracted_so_phat_hanhs")
    # Hàng đợi "cần xác minh chủ cuối" — partial index: chỉ ~1% hồ sơ có cảnh báo
    # nên index gọn, lọc nhanh ở quy mô trăm nghìn (xem routes/gcn.py `canh_bao`).
    await gcns().create_index(
        "chu_cuoi.canh_bao", background=True, name="canh_bao_chu_cuoi",
        partialFilterExpression={"chu_cuoi.canh_bao": {"$exists": True}},
    )
    await batches().create_index("created_at")
    await batches().create_index("branch")
    await users().create_index("username", unique=True)
    await users().create_index("assigned_batch_ids")
    await audit_log().create_index([("at", -1)])
    await audit_log().create_index("actor")
    await audit_log().create_index("action")
    await audit_log().create_index("target")
    await gcns().create_index(
        [("source_connection_id", 1), ("s3_key", 1), ("batch_id", 1)],
        unique=True, background=True,
        partialFilterExpression={"source_connection_id": {"$exists": True}},
        name="uniq_source_key_batch",
    )
    await import_jobs().create_index("status")
    await import_jobs().create_index("started_at")
    await export_jobs().create_index("status")
    await gcns().create_index([("branch", 1), ("created_at", -1)])
    # TTL: Mongo tự xóa cache tiến độ MinIO đã hết hạn — không cần dọn tay/kiểm
    # tra tuổi bằng Python (xem app.config.BROWSE_PROGRESS_CACHE_TTL).
    await browse_progress_cache().create_index(
        "computed_at", expireAfterSeconds=config.BROWSE_PROGRESS_CACHE_TTL,
    )
